from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Optional
import uuid

from core.enums import RiskLevel
from core.events.types import EventSource, EventType
from core.errors import ValidationError
from core.autonomy.evaluator import AutonomyEvaluator
from core.autonomy.model import (
    ApprovalRequest,
    AutonomyPolicy,
    DecisionRequest,
    PolicyDecision,
    UserInputRequest,
    utc_now,
)
from core.autonomy.types import (
    ActionCategory,
    ApprovalRequestStatus,
    AutonomyLevel,
    DecisionRequestStatus,
    PolicyDecisionResult,
    UserInputStatus,
)

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime


class AutonomyService:
    """
    Central control layer for Human-in-the-Loop, Autonomy Policies,
    Scoped Approvals, User Questions/Decisions, and Emergency Stops.
    """

    def __init__(self, runtime: WorkforceRuntime):
        self.runtime = runtime
        self._lock = threading.RLock()
        self._emergency_stopped = False
        self._emergency_stop_reason: Optional[str] = None
        self._approved_action_tokens: set[str] = set()  # token -> authorized action

    @property
    def is_emergency_stopped(self) -> bool:
        with self._lock:
            return self._emergency_stopped

    def set_emergency_stop(self, reason: str = "Immediate safety halt", actor: str = "User") -> None:
        with self._lock:
            self._emergency_stopped = True
            self._emergency_stop_reason = reason

            self.runtime.log_event(
                event_type=EventType.EMERGENCY_STOP_ACTIVATED,
                payload={"reason": reason, "actor": actor},
                source=EventSource.USER,
            )

    def clear_emergency_stop(self, actor: str = "User") -> None:
        with self._lock:
            self._emergency_stopped = False
            self._emergency_stop_reason = None

            self.runtime.log_event(
                event_type=EventType.EMERGENCY_STOP_CLEARED,
                payload={"actor": actor},
                source=EventSource.USER,
            )

    def create_policy(
        self,
        project_id: str,
        autonomy_level: AutonomyLevel = AutonomyLevel.BALANCED,
        allowed_tools: Optional[list[str]] = None,
        denied_tools: Optional[list[str]] = None,
        approval_required_actions: Optional[list[ActionCategory]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> AutonomyPolicy:
        with self._lock:
            policy_id = f"pol-{uuid.uuid4().hex[:8]}"
            policy = AutonomyPolicy(
                id=policy_id,
                project_id=project_id,
                autonomy_level=autonomy_level,
                allowed_tools=list(allowed_tools or []),
                denied_tools=list(denied_tools or []),
                approval_required_actions=list(approval_required_actions or []),
                metadata=dict(metadata or {}),
            )

            if hasattr(self.runtime.store, "save_autonomy_policy"):
                self.runtime.store.save_autonomy_policy(policy)

            self.runtime.log_event(
                event_type=EventType.AUTONOMY_POLICY_CREATED,
                payload={
                    "policy_id": policy.id,
                    "project_id": project_id,
                    "autonomy_level": policy.autonomy_level.value,
                },
                project_id=project_id,
                source=EventSource.RUNTIME,
            )
            return policy

    def get_policy_for_project(self, project_id: str) -> AutonomyPolicy:
        with self._lock:
            if hasattr(self.runtime.store, "get_autonomy_policy_by_project"):
                pol = self.runtime.store.get_autonomy_policy_by_project(project_id)
                if pol:
                    return pol
            # Default safe policy
            return AutonomyPolicy(id=f"pol-def-{project_id[:6]}", project_id=project_id, autonomy_level=AutonomyLevel.BALANCED)

    def evaluate_tool_action(
        self,
        project_id: str,
        tool_id: str,
        arguments: dict[str, Any],
    ) -> PolicyDecision:
        policy = self.get_policy_for_project(project_id)
        decision = AutonomyEvaluator.evaluate_action(
            policy=policy,
            tool_id=tool_id,
            arguments=arguments,
            is_emergency_stopped=self.is_emergency_stopped,
        )

        if decision.result == PolicyDecisionResult.ALLOW:
            self.runtime.log_event(
                event_type=EventType.ACTION_ALLOWED_BY_POLICY,
                payload={"action": tool_id, "risk_level": decision.risk_level.value},
                project_id=project_id,
                source=EventSource.RUNTIME,
            )
        elif decision.result == PolicyDecisionResult.DENY:
            self.runtime.log_event(
                event_type=EventType.ACTION_BLOCKED_BY_POLICY,
                payload={
                    "action": tool_id,
                    "risk_level": decision.risk_level.value,
                    "matched_rule": decision.explanation.matched_rules[0] if decision.explanation.matched_rules else "DENY",
                },
                project_id=project_id,
                source=EventSource.RUNTIME,
            )

        return decision

    def request_approval(
        self,
        project_id: str,
        task_id: str,
        worker_id: str,
        action: str,
        category: ActionCategory,
        risk_level: RiskLevel,
        reason: str,
        requested_scope: str,
        workflow_id: Optional[str] = None,
        affected_resources: Optional[list[str]] = None,
        evidence: Optional[list[str]] = None,
    ) -> ApprovalRequest:
        with self._lock:
            app_id = f"app-{uuid.uuid4().hex[:8]}"
            req = ApprovalRequest(
                id=app_id,
                project_id=project_id,
                workflow_id=workflow_id,
                task_id=task_id,
                worker_id=worker_id,
                action=action,
                category=category,
                risk_level=risk_level,
                reason=reason,
                requested_scope=requested_scope,
                affected_resources=list(affected_resources or []),
                evidence=list(evidence or []),
                status=ApprovalRequestStatus.PENDING,
            )

            if hasattr(self.runtime.store, "save_approval_request"):
                self.runtime.store.save_approval_request(req)

            self.runtime.log_event(
                event_type=EventType.APPROVAL_REQUESTED,
                payload={
                    "approval_id": req.id,
                    "workflow_id": workflow_id,
                    "task_id": task_id,
                    "worker_id": worker_id,
                    "action": action,
                    "risk_level": risk_level.value,
                    "scope": requested_scope,
                    "reason": reason,
                },
                project_id=project_id,
                task_id=task_id,
                source=EventSource.RUNTIME,
            )
            return req

    def approve_request(self, approval_id: str, approver: str = "User") -> ApprovalRequest:
        with self._lock:
            req = None
            if hasattr(self.runtime.store, "get_approval_request"):
                req = self.runtime.store.get_approval_request(approval_id)
            if not req:
                raise ValidationError(f"Approval request '{approval_id}' not found.")

            if req.status != ApprovalRequestStatus.PENDING:
                raise ValidationError(f"Cannot approve request with status '{req.status.value}'.")

            req.status = ApprovalRequestStatus.APPROVED
            req.decided_at = utc_now()
            req.decided_by = approver

            if hasattr(self.runtime.store, "save_approval_request"):
                self.runtime.store.save_approval_request(req)

            # Register approved action token
            token = f"{req.project_id}:{req.task_id}:{req.action}"
            self._approved_action_tokens.add(token)

            self.runtime.log_event(
                event_type=EventType.APPROVAL_GRANTED,
                payload={"approval_id": req.id, "action": req.action, "approved_by": approver},
                project_id=req.project_id,
                task_id=req.task_id,
                source=EventSource.USER,
            )
            return req

    def reject_request(self, approval_id: str, reason: str = "User rejected", decider: str = "User") -> ApprovalRequest:
        with self._lock:
            req = None
            if hasattr(self.runtime.store, "get_approval_request"):
                req = self.runtime.store.get_approval_request(approval_id)
            if not req:
                raise ValidationError(f"Approval request '{approval_id}' not found.")

            if req.status != ApprovalRequestStatus.PENDING:
                raise ValidationError(f"Cannot reject request with status '{req.status.value}'.")

            req.status = ApprovalRequestStatus.REJECTED
            req.decided_at = utc_now()
            req.decided_by = decider
            req.rejection_reason = reason

            if hasattr(self.runtime.store, "save_approval_request"):
                self.runtime.store.save_approval_request(req)

            self.runtime.log_event(
                event_type=EventType.APPROVAL_REJECTED,
                payload={"approval_id": req.id, "action": req.action, "reason": reason},
                project_id=req.project_id,
                task_id=req.task_id,
                source=EventSource.USER,
            )
            return req

    def request_user_input(
        self,
        project_id: str,
        task_id: str,
        question: str,
        context: str = "",
        workflow_id: Optional[str] = None,
    ) -> UserInputRequest:
        with self._lock:
            req_id = f"input-{uuid.uuid4().hex[:8]}"
            req = UserInputRequest(
                id=req_id,
                project_id=project_id,
                workflow_id=workflow_id,
                task_id=task_id,
                question=question,
                context=context,
                status=UserInputStatus.PENDING,
            )
            if hasattr(self.runtime.store, "save_user_input_request"):
                self.runtime.store.save_user_input_request(req)

            self.runtime.log_event(
                event_type=EventType.USER_INPUT_REQUESTED,
                payload={"request_id": req.id, "question": question, "context": context},
                project_id=project_id,
                task_id=task_id,
                source=EventSource.RUNTIME,
            )
            return req

    def answer_user_input(self, request_id: str, answer: str) -> UserInputRequest:
        with self._lock:
            req = None
            if hasattr(self.runtime.store, "get_user_input_request"):
                req = self.runtime.store.get_user_input_request(request_id)
            if not req:
                raise ValidationError(f"User input request '{request_id}' not found.")

            req.answer = answer
            req.status = UserInputStatus.ANSWERED
            req.answered_at = utc_now()

            if hasattr(self.runtime.store, "save_user_input_request"):
                self.runtime.store.save_user_input_request(req)

            self.runtime.log_event(
                event_type=EventType.USER_INPUT_RECEIVED,
                payload={"request_id": req.id, "answer": answer},
                project_id=req.project_id,
                task_id=req.task_id,
                source=EventSource.USER,
            )
            return req

    def request_decision(
        self,
        project_id: str,
        task_id: str,
        title: str,
        options: list[str],
        rationale: str = "",
        workflow_id: Optional[str] = None,
    ) -> DecisionRequest:
        with self._lock:
            dec_id = f"dec-{uuid.uuid4().hex[:8]}"
            req = DecisionRequest(
                id=dec_id,
                project_id=project_id,
                workflow_id=workflow_id,
                task_id=task_id,
                title=title,
                options=list(options),
                rationale=rationale,
                status=DecisionRequestStatus.PENDING,
            )
            if hasattr(self.runtime.store, "save_decision_request"):
                self.runtime.store.save_decision_request(req)

            self.runtime.log_event(
                event_type=EventType.DECISION_REQUESTED,
                payload={"decision_id": req.id, "title": title, "options": options},
                project_id=project_id,
                task_id=task_id,
                source=EventSource.RUNTIME,
            )
            return req

    def choose_decision(self, decision_id: str, chosen_option: str) -> DecisionRequest:
        with self._lock:
            req = None
            if hasattr(self.runtime.store, "get_decision_request"):
                req = self.runtime.store.get_decision_request(decision_id)
            if not req:
                raise ValidationError(f"Decision request '{decision_id}' not found.")

            if chosen_option not in req.options:
                raise ValidationError(f"Option '{chosen_option}' is not one of valid options: {req.options}")

            req.chosen_option = chosen_option
            req.status = DecisionRequestStatus.DECIDED
            req.decided_at = utc_now()

            if hasattr(self.runtime.store, "save_decision_request"):
                self.runtime.store.save_decision_request(req)

            self.runtime.log_event(
                event_type=EventType.DECISION_RECEIVED,
                payload={"decision_id": req.id, "chosen_option": chosen_option},
                project_id=req.project_id,
                task_id=req.task_id,
                source=EventSource.USER,
            )
            return req

    # --- Query & UI Helper Methods ---
    def list_pending_approvals(self, project_id: str) -> list[ApprovalRequest]:
        with self._lock:
            if hasattr(self.runtime.store, "list_approval_requests"):
                return self.runtime.store.list_approval_requests(project_id=project_id, status=ApprovalRequestStatus.PENDING.value)
            return []

    def grant_approval(self, approval_id: str, decided_by: str = "User") -> ApprovalRequest:
        return self.approve_request(approval_id=approval_id, approver=decided_by)

    def reject_approval(self, approval_id: str, reason: str = "User rejected", decided_by: str = "User") -> ApprovalRequest:
        return self.reject_request(approval_id=approval_id, reason=reason, decider=decided_by)

    def list_pending_user_inputs(self, project_id: str) -> list[UserInputRequest]:
        with self._lock:
            if hasattr(self.runtime.store, "list_user_input_requests"):
                return self.runtime.store.list_user_input_requests(project_id=project_id, status=UserInputStatus.PENDING.value)
            return []

    def respond_to_user_input(self, input_id: str, answer: str) -> UserInputRequest:
        return self.answer_user_input(request_id=input_id, answer=answer)

    def list_pending_decisions(self, project_id: str) -> list[DecisionRequest]:
        with self._lock:
            if hasattr(self.runtime.store, "list_decision_requests"):
                return self.runtime.store.list_decision_requests(project_id=project_id, status=DecisionRequestStatus.PENDING.value)
            return []

    def respond_to_decision(self, decision_id: str, chosen_option: str, rationale: str = "") -> DecisionRequest:
        return self.choose_decision(decision_id=decision_id, chosen_option=chosen_option)


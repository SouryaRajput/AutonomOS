from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.programmer.contracts.implementation_plan import EscalationCandidate
import uuid

from core.programmer.contracts.blocker import ProgrammerBlocker
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.identifiers import (
    new_escalation_id,
    validate_escalation_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    ProgrammerBlockerCategory,
    ProgrammerBlockerSeverity,
    ProgrammerExecutionStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class ProgrammerEscalationCategory(str, Enum):
    """Categorization of issues requiring Manager escalation and intervention."""
    SCOPE = "SCOPE"
    PERMISSION = "PERMISSION"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    DEPENDENCY = "DEPENDENCY"
    ARCHITECTURAL = "ARCHITECTURAL"
    RESOURCE = "RESOURCE"
    VERIFICATION = "VERIFICATION"
    OTHER = "OTHER"


class ManagerEscalationResponseAction(str, Enum):
    """Permitted responses from the Manager for an escalated decision or blocker."""
    APPROVE = "APPROVE"
    DENY = "DENY"
    MODIFY_WORK_ORDER = "MODIFY_WORK_ORDER"
    PROVIDE_CONTEXT = "PROVIDE_CONTEXT"
    CANCEL = "CANCEL"
    REQUEST_ALTERNATIVE = "REQUEST_ALTERNATIVE"


class EscalationStatus(str, Enum):
    """Lifecycle status of a Programmer escalation."""
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


# Unauthorized responders that cannot approve or resolve escalations
UNAUTHORIZED_RESPONDER_NAMES = frozenset({
    "programmer",
    "cline",
    "cline_backend",
    "worker",
    "worker.programmer",
    "agent",
    "subagent",
})


@dataclass
class ManagerEscalationResponse:
    """
    Formal response and instruction from the Manager regarding an escalated blocker or decision.
    Invariants:
    - Programmer CANNOT self-approve an escalation.
    - Cline backend CANNOT approve an escalation.
    - Responses must come from Manager or User authority.
    """
    response_id: str
    escalation_id: str
    action: ManagerEscalationResponseAction
    responder: str
    reason: str
    additional_context: Optional[dict[str, Any]] = None
    modified_work_order: Optional[ProgrammerWorkOrder] = None
    timestamp: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.action, str):
            try:
                self.action = ManagerEscalationResponseAction(self.action.upper())
            except ValueError:
                raise ProgrammerValidationError(
                    f"Invalid manager escalation response action: '{self.action}'.",
                    field_name="action",
                )
        self.validate()

    def validate(self) -> None:
        """
        Validate response integrity and enforce the authority separation invariant:
        Programmer/Cline cannot self-approve or answer escalations.
        """
        validate_escalation_id(self.escalation_id)
        if not self.response_id or not self.response_id.strip():
            raise ProgrammerValidationError("Response ID cannot be empty.", field_name="response_id")
        if not self.responder or not self.responder.strip():
            raise ProgrammerValidationError("Responder cannot be empty.", field_name="responder")
        if not self.reason or not self.reason.strip():
            raise ProgrammerValidationError("Response reason cannot be empty.", field_name="reason")

        normalized = self.responder.strip().lower()
        if normalized in UNAUTHORIZED_RESPONDER_NAMES or any(
            sub in normalized for sub in ("programmer", "cline", "subagent")
        ):
            raise ProgrammerValidationError(
                f"Unauthorized escalation responder '{self.responder}'. "
                f"Programmer and Cline cannot self-approve or answer escalations.",
                field_name="responder",
            )

        if not isinstance(self.action, ManagerEscalationResponseAction):
            raise ProgrammerValidationError(
                f"Invalid response action type: {type(self.action).__name__}",
                field_name="action",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "response_id": self.response_id,
            "escalation_id": self.escalation_id,
            "action": self.action.value,
            "responder": self.responder,
            "reason": self.reason,
            "additional_context": dict(self.additional_context) if self.additional_context else None,
            "modified_work_order": (
                self.modified_work_order.to_dict()
                if isinstance(self.modified_work_order, ProgrammerWorkOrder)
                else self.modified_work_order
            ),
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ManagerEscalationResponse:
        mwo_data = data.get("modified_work_order")
        mwo = None
        if isinstance(mwo_data, ProgrammerWorkOrder):
            mwo = mwo_data
        elif isinstance(mwo_data, dict):
            mwo = ProgrammerWorkOrder.from_dict(mwo_data)

        raw_action = data.get("action", ManagerEscalationResponseAction.APPROVE.value)
        action = (
            raw_action
            if isinstance(raw_action, ManagerEscalationResponseAction)
            else ManagerEscalationResponseAction(str(raw_action).upper())
        )

        return cls(
            response_id=data.get("response_id", str(uuid.uuid4())),
            escalation_id=data["escalation_id"],
            action=action,
            responder=data.get("responder", "manager"),
            reason=data.get("reason", ""),
            additional_context=data.get("additional_context"),
            modified_work_order=mwo,
            timestamp=data.get("timestamp", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ProgrammerEscalation:
    """
    Structured document representing an escalated blocker or required decision
    forwarded to the Manager for authority/direction.
    """
    escalation_id: str
    execution_id: str
    work_order_id: str
    category: ProgrammerEscalationCategory
    requested_decision: str
    severity: ProgrammerBlockerSeverity = ProgrammerBlockerSeverity.HIGH
    blocker: Optional[ProgrammerBlocker] = None
    observed_facts: list[str] = field(default_factory=list)
    current_scope: dict[str, Any] = field(default_factory=dict)
    attempted_actions: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    suggested_options: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now)
    status: EscalationStatus = EscalationStatus.PENDING
    response: Optional[ManagerEscalationResponse] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.category, str):
            try:
                self.category = ProgrammerEscalationCategory(self.category.upper())
            except ValueError:
                raise ProgrammerValidationError(
                    f"Invalid escalation category: '{self.category}'.",
                    field_name="category",
                )
        if isinstance(self.severity, str):
            try:
                self.severity = ProgrammerBlockerSeverity(self.severity.upper())
            except ValueError:
                raise ProgrammerValidationError(
                    f"Invalid escalation severity: '{self.severity}'.",
                    field_name="severity",
                )
        if isinstance(self.status, str):
            try:
                self.status = EscalationStatus(self.status.upper())
            except ValueError:
                raise ProgrammerValidationError(
                    f"Invalid escalation status: '{self.status}'.",
                    field_name="status",
                )
        self.validate()

    def validate(self) -> None:
        """Validate escalation identifiers, category, and completeness."""
        validate_escalation_id(self.escalation_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)

        if not self.requested_decision or not self.requested_decision.strip():
            raise ProgrammerValidationError(
                "Escalation requested_decision cannot be empty.",
                field_name="requested_decision",
            )
        if not isinstance(self.category, ProgrammerEscalationCategory):
            raise ProgrammerValidationError(
                f"Invalid escalation category type: {type(self.category).__name__}",
                field_name="category",
            )
        if self.response is not None:
            self.response.validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "escalation_id": self.escalation_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "category": self.category.value,
            "severity": self.severity.value,
            "requested_decision": self.requested_decision,
            "blocker": self.blocker.to_dict() if isinstance(self.blocker, ProgrammerBlocker) else self.blocker,
            "observed_facts": list(self.observed_facts),
            "current_scope": dict(self.current_scope),
            "attempted_actions": list(self.attempted_actions),
            "evidence": list(self.evidence),
            "suggested_options": list(self.suggested_options),
            "trace": dict(self.trace),
            "timestamp": self.timestamp,
            "status": self.status.value,
            "response": self.response.to_dict() if self.response else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammerEscalation:
        raw_category = data.get("category", ProgrammerEscalationCategory.OTHER.value)
        category = (
            raw_category
            if isinstance(raw_category, ProgrammerEscalationCategory)
            else ProgrammerEscalationCategory(str(raw_category).upper())
        )

        raw_severity = data.get("severity", ProgrammerBlockerSeverity.HIGH.value)
        severity = (
            raw_severity
            if isinstance(raw_severity, ProgrammerBlockerSeverity)
            else ProgrammerBlockerSeverity(str(raw_severity).upper())
        )

        raw_status = data.get("status", EscalationStatus.PENDING.value)
        status = (
            raw_status
            if isinstance(raw_status, EscalationStatus)
            else EscalationStatus(str(raw_status).upper())
        )

        blk_data = data.get("blocker")
        blocker = None
        if isinstance(blk_data, ProgrammerBlocker):
            blocker = blk_data
        elif isinstance(blk_data, dict):
            blocker = ProgrammerBlocker.from_dict(blk_data)

        resp_data = data.get("response")
        response = None
        if isinstance(resp_data, ManagerEscalationResponse):
            response = resp_data
        elif isinstance(resp_data, dict):
            response = ManagerEscalationResponse.from_dict(resp_data)

        return cls(
            escalation_id=data["escalation_id"],
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            category=category,
            requested_decision=data.get("requested_decision", ""),
            severity=severity,
            blocker=blocker,
            observed_facts=list(data.get("observed_facts", [])),
            current_scope=dict(data.get("current_scope", {})),
            attempted_actions=list(data.get("attempted_actions", [])),
            evidence=list(data.get("evidence", [])),
            suggested_options=list(data.get("suggested_options", [])),
            trace=dict(data.get("trace", {})),
            timestamp=data.get("timestamp", utc_now()),
            status=status,
            response=response,
            metadata=dict(data.get("metadata", {})),
        )


class EscalationCoordinator:
    """
    Coordinates creation, lifecycle management, and resolution of Programmer escalations.
    Guarantees:
    - Programmer cannot self-approve escalations.
    - Cline backend cannot approve escalations.
    - Stale responses on already resolved/cancelled escalations are rejected.
    - Execution is blocked on escalation and unblocked/modified upon resolution.
    - Historical WorkOrder is untouched on modification; a validated revision is created.
    - Strict lineage verification across WorkOrder, Execution, and Escalation.
    """

    def __init__(self) -> None:
        self._escalations_by_execution: dict[str, list[ProgrammerEscalation]] = {}
        self._escalations_by_id: dict[str, ProgrammerEscalation] = {}

    def create_escalation(
        self,
        execution: ProgrammerExecution,
        work_order: ProgrammerWorkOrder,
        category: ProgrammerEscalationCategory | str,
        requested_decision: str,
        observed_facts: Optional[list[str]] = None,
        attempted_actions: Optional[list[str]] = None,
        evidence: Optional[list[str]] = None,
        suggested_options: Optional[list[str]] = None,
        severity: ProgrammerBlockerSeverity | str = ProgrammerBlockerSeverity.HIGH,
        blocker: Optional[ProgrammerBlocker] = None,
        escalation_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> ProgrammerEscalation:
        """
        Create and record an escalation, capturing a snapshot of the current scope,
        registering the blocker on the execution, and marking execution BLOCKED.
        """
        # Lineage verification
        if execution.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"Execution work_order_id '{execution.work_order_id}' does not match "
                f"WorkOrder work_order_id '{work_order.work_order_id}'."
            )

        if isinstance(category, str):
            category = ProgrammerEscalationCategory(category.upper())
        if isinstance(severity, str):
            severity = ProgrammerBlockerSeverity(severity.upper())

        # Scope snapshot
        current_scope = {
            "allowed_paths": list(work_order.allowed_paths),
            "writable_paths": list(work_order.writable_paths),
            "read_only_paths": list(work_order.read_only_paths),
            "forbidden_paths": list(work_order.forbidden_paths),
            "allowed_commands": [
                cmd.to_dict() if hasattr(cmd, "to_dict") else cmd
                for cmd in work_order.allowed_commands
            ],
        }

        # Blocker handling and execution transition to BLOCKED
        if blocker is None:
            blk_category_val = category.value
            try:
                blk_category = ProgrammerBlockerCategory(blk_category_val)
            except ValueError:
                blk_category = ProgrammerBlockerCategory.OTHER

            blocker = execution.block(
                reason=requested_decision,
                category=blk_category,
                severity=severity,
                required_decision=requested_decision,
            )
        else:
            if execution.status != ProgrammerExecutionStatus.BLOCKED:
                execution.transition_to(
                    ProgrammerExecutionStatus.BLOCKED,
                    reason=requested_decision,
                    blocker=blocker,
                )
            elif blocker not in execution.blockers:
                execution.blockers.append(blocker)

        esc_id = escalation_id or new_escalation_id()
        esc_trace = trace or {
            "execution_id": execution.execution_id,
            "work_order_id": work_order.work_order_id,
            "task_id": work_order.manager_task_id,
            "project_id": work_order.project_id,
            "correlation_id": work_order.correlation_id,
            "created_at": utc_now(),
        }

        escalation = ProgrammerEscalation(
            escalation_id=esc_id,
            execution_id=execution.execution_id,
            work_order_id=work_order.work_order_id,
            category=category,
            severity=severity,
            blocker=blocker,
            observed_facts=list(observed_facts or []),
            requested_decision=requested_decision,
            current_scope=current_scope,
            attempted_actions=list(attempted_actions or []),
            evidence=list(evidence or []),
            suggested_options=list(suggested_options or []),
            trace=esc_trace,
            timestamp=utc_now(),
            status=EscalationStatus.PENDING,
            metadata=dict(metadata or {}),
        )
        escalation.validate()

        self._escalations_by_execution.setdefault(execution.execution_id, []).append(escalation)
        self._escalations_by_id[escalation.escalation_id] = escalation

        return escalation

    def escalate_candidate(
        self,
        execution: ProgrammerExecution,
        work_order: ProgrammerWorkOrder,
        candidate: EscalationCandidate,
    ) -> ProgrammerEscalation:
        """Create and record an escalation from an EscalationCandidate."""
        observed = [candidate.reason]
        if candidate.target:
            observed.append(f"Target: {candidate.target}")
        return self.create_escalation(
            execution=execution,
            work_order=work_order,
            category=candidate.category,
            requested_decision=candidate.requested_decision,
            observed_facts=observed,
            suggested_options=list(candidate.suggested_options),
            severity=candidate.severity,
            metadata=dict(candidate.metadata),
        )

    def apply_response(
        self,
        escalation: ProgrammerEscalation,
        response: ManagerEscalationResponse,
        execution: ProgrammerExecution,
        work_order: ProgrammerWorkOrder,
    ) -> tuple[EscalationStatus, Optional[ProgrammerWorkOrder]]:
        """
        Apply a Manager response to a pending escalation with strict lineage,
        authority, and immutability checks.
        Returns (final_escalation_status, revised_work_order_or_None).
        """
        # 1. Lineage verification
        if escalation.execution_id != execution.execution_id:
            raise ProgrammerLineageError(
                f"Lineage mismatch: escalation execution_id '{escalation.execution_id}' "
                f"does not match execution '{execution.execution_id}'."
            )
        if escalation.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"Lineage mismatch: escalation work_order_id '{escalation.work_order_id}' "
                f"does not match work order '{work_order.work_order_id}'."
            )
        if response.escalation_id != escalation.escalation_id:
            raise ProgrammerLineageError(
                f"Lineage mismatch: response escalation_id '{response.escalation_id}' "
                f"does not match escalation '{escalation.escalation_id}'."
            )

        # 2. Stale escalation check
        if escalation.status != EscalationStatus.PENDING:
            raise ProgrammerValidationError(
                f"Stale escalation response: escalation '{escalation.escalation_id}' is already "
                f"'{escalation.status.value}' and cannot receive further responses."
            )

        # 3. Validate response and enforce authority separation (no self-approval)
        response.validate()

        # 4. Attach response
        escalation.response = response

        # 5. Dispatch action handling
        if response.action == ManagerEscalationResponseAction.APPROVE:
            escalation.status = EscalationStatus.RESOLVED
            if escalation.blocker and not escalation.blocker.resolved_at:
                escalation.blocker.resolve(
                    resolution_notes=f"Approved by {response.responder}: {response.reason}",
                    resolved_by=response.responder,
                )
            if execution.status == ProgrammerExecutionStatus.BLOCKED:
                execution.unblock(
                    resolution_notes=f"Escalation {escalation.escalation_id} approved by {response.responder}: {response.reason}",
                    resolved_by=response.responder,
                )
            return EscalationStatus.RESOLVED, None

        elif response.action == ManagerEscalationResponseAction.DENY:
            escalation.status = EscalationStatus.REJECTED
            # Execution remains BLOCKED; blocker remains active
            return EscalationStatus.REJECTED, None

        elif response.action == ManagerEscalationResponseAction.MODIFY_WORK_ORDER:
            escalation.status = EscalationStatus.RESOLVED
            if escalation.blocker and not escalation.blocker.resolved_at:
                escalation.blocker.resolve(
                    resolution_notes=f"WorkOrder modified by {response.responder}: {response.reason}",
                    resolved_by=response.responder,
                )

            revised_wo: Optional[ProgrammerWorkOrder] = None
            if response.modified_work_order is not None:
                if isinstance(response.modified_work_order, ProgrammerWorkOrder):
                    revised_wo = response.modified_work_order
                elif isinstance(response.modified_work_order, dict):
                    revised_wo = ProgrammerWorkOrder.from_dict(response.modified_work_order)
                revised_wo.validate()
            else:
                modifications = (response.additional_context or {}).get("modifications", {})
                revised_wo = work_order.create_revision(
                    modifications=modifications,
                    reason=response.reason,
                )

            if execution.status == ProgrammerExecutionStatus.BLOCKED:
                execution.unblock(
                    resolution_notes=f"WorkOrder revised to {revised_wo.work_order_id} by {response.responder}",
                    resolved_by=response.responder,
                )
            return EscalationStatus.RESOLVED, revised_wo

        elif response.action == ManagerEscalationResponseAction.PROVIDE_CONTEXT:
            escalation.status = EscalationStatus.RESOLVED
            if response.additional_context:
                work_order.context.update(response.additional_context)
            if escalation.blocker and not escalation.blocker.resolved_at:
                escalation.blocker.resolve(
                    resolution_notes=f"Context provided by {response.responder}: {response.reason}",
                    resolved_by=response.responder,
                )
            if execution.status == ProgrammerExecutionStatus.BLOCKED:
                execution.unblock(
                    resolution_notes=f"Context provided for escalation {escalation.escalation_id} by {response.responder}",
                    resolved_by=response.responder,
                )
            return EscalationStatus.RESOLVED, None

        elif response.action == ManagerEscalationResponseAction.CANCEL:
            escalation.status = EscalationStatus.CANCELLED
            if execution.status != ProgrammerExecutionStatus.CANCELLED:
                execution.cancel(
                    requested_by=response.responder,
                    reason=f"Escalation {escalation.escalation_id} cancelled by {response.responder}: {response.reason}",
                    confirmed=True,
                    agent_terminated=True,
                    cleanup_completed=True,
                )
            return EscalationStatus.CANCELLED, None

        elif response.action == ManagerEscalationResponseAction.REQUEST_ALTERNATIVE:
            escalation.status = EscalationStatus.REJECTED
            # Execution remains BLOCKED; Programmer must formulate alternative options
            return EscalationStatus.REJECTED, None

        raise ProgrammerValidationError(
            f"Unhandled escalation response action: '{response.action}'.",
            field_name="action",
        )

    def get_escalations(self, execution_id: str) -> list[ProgrammerEscalation]:
        """Retrieve all escalations created for the specified execution."""
        return list(self._escalations_by_execution.get(execution_id, []))

    def get_pending_escalation(self, execution_id: str) -> Optional[ProgrammerEscalation]:
        """Retrieve the currently active pending escalation for the execution, if any."""
        for esc in self._escalations_by_execution.get(execution_id, []):
            if esc.status == EscalationStatus.PENDING:
                return esc
        return None

    def get_escalation(self, escalation_id: str) -> Optional[ProgrammerEscalation]:
        """Retrieve an escalation by its unique ID."""
        return self._escalations_by_id.get(escalation_id)

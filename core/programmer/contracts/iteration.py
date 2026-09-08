from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Optional, Sequence

from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.delivery import DeliveryPackage
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.feedback import EngineeringFeedback
from core.programmer.contracts.feedback_adapter import FeedbackToWorkOrderAdapter
from core.programmer.contracts.identifiers import (
    new_work_order_id,
    validate_work_order_id,
)
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    AcceptanceStatus,
    ManagerIterationDecision,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PriorEngineeringContext:
    """
    Structured comprehension of prior engineering work, observed failures,
    requirement evolutions, and open defects for Programmer in a revised iteration.
    
    Guarantees:
    - Answers:
      1. What was previously implemented
      2. What failed
      3. What changed in requirements
      4. What remains to be fixed
    - Non-self-grading notice: failures do not automatically imply code defects (e.g. test bug).
    """
    previous_work_order_id: str
    previous_execution_id: Optional[str] = None
    previous_delivery_id: Optional[str] = None
    previously_implemented: list[str] = field(default_factory=list)
    failures_observed: list[dict[str, Any]] = field(default_factory=list)
    requirements_diff: dict[str, Any] = field(default_factory=dict)
    remaining_to_fix: list[str] = field(default_factory=list)
    accepted_feedback: list[dict[str, Any]] = field(default_factory=list)
    advisory_suggestions: list[dict[str, Any]] = field(default_factory=list)
    manager_decision: ManagerIterationDecision = ManagerIterationDecision.REQUEST_FIX
    manager_notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.manager_decision, str):
            try:
                self.manager_decision = ManagerIterationDecision(self.manager_decision.upper())
            except (ValueError, KeyError):
                self.manager_decision = ManagerIterationDecision.REQUEST_FIX

        self.previously_implemented = list(self.previously_implemented or [])
        self.failures_observed = [dict(f) if isinstance(f, dict) else {"failure": str(f)} for f in (self.failures_observed or [])]
        self.requirements_diff = dict(self.requirements_diff or {})
        self.remaining_to_fix = list(self.remaining_to_fix or [])
        self.accepted_feedback = [dict(f) if isinstance(f, dict) else {"feedback": str(f)} for f in (self.accepted_feedback or [])]
        self.advisory_suggestions = [dict(s) if isinstance(s, dict) else {"suggestion": str(s)} for s in (self.advisory_suggestions or [])]
        self.metadata = dict(self.metadata or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_work_order_id": self.previous_work_order_id,
            "previous_execution_id": self.previous_execution_id,
            "previous_delivery_id": self.previous_delivery_id,
            "previously_implemented": list(self.previously_implemented),
            "failures_observed": [dict(f) for f in self.failures_observed],
            "requirements_diff": dict(self.requirements_diff),
            "remaining_to_fix": list(self.remaining_to_fix),
            "accepted_feedback": [dict(f) for f in self.accepted_feedback],
            "advisory_suggestions": [dict(s) for s in self.advisory_suggestions],
            "manager_decision": self.manager_decision.value if hasattr(self.manager_decision, "value") else str(self.manager_decision),
            "manager_notes": self.manager_notes,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PriorEngineeringContext:
        dec_raw = data.get("manager_decision", ManagerIterationDecision.REQUEST_FIX.value)
        try:
            decision = ManagerIterationDecision(dec_raw)
        except (ValueError, KeyError):
            decision = ManagerIterationDecision.REQUEST_FIX

        return cls(
            previous_work_order_id=str(data.get("previous_work_order_id", "")),
            previous_execution_id=data.get("previous_execution_id"),
            previous_delivery_id=data.get("previous_delivery_id"),
            previously_implemented=list(data.get("previously_implemented", [])),
            failures_observed=list(data.get("failures_observed", [])),
            requirements_diff=dict(data.get("requirements_diff", {})),
            remaining_to_fix=list(data.get("remaining_to_fix", [])),
            accepted_feedback=list(data.get("accepted_feedback", [])),
            advisory_suggestions=list(data.get("advisory_suggestions", [])),
            manager_decision=decision,
            manager_notes=str(data.get("manager_notes", "")),
            metadata=dict(data.get("metadata", {})),
            created_at=str(data.get("created_at", utc_now())),
        )

    @classmethod
    def from_work_order(cls, work_order: ProgrammerWorkOrder) -> Optional[PriorEngineeringContext]:
        """Extract prior engineering context from a revised work order if present."""
        if not work_order or not work_order.context:
            return None
        ctx_data = work_order.context.get("prior_engineering_context")
        if isinstance(ctx_data, dict):
            return cls.from_dict(ctx_data)
        return None

    def to_prompt_markdown(self) -> str:
        """Formats prior engineering context as structured Markdown for prompt injection."""
        lines = []
        lines.append(f"Previous Work Order: {self.previous_work_order_id}")
        if self.previous_execution_id:
            lines.append(f"Previous Execution: {self.previous_execution_id}")
        if self.previous_delivery_id:
            lines.append(f"Previous Delivery: {self.previous_delivery_id}")
        lines.append(f"Manager Decision: {self.manager_decision.value if hasattr(self.manager_decision, 'value') else str(self.manager_decision)}")
        if self.manager_notes:
            lines.append(f"Manager Notes: {self.manager_notes}")

        lines.append("\n### 1. What Was Previously Implemented:")
        if self.previously_implemented:
            for item in self.previously_implemented:
                lines.append(f"- {item}")
        else:
            lines.append("- (Initial implementation from previous iteration)")

        lines.append("\n### 2. What Failed (Observed Failures & Defects):")
        if self.failures_observed:
            for fail in self.failures_observed:
                desc = fail.get("issue") or fail.get("description") or fail.get("summary") or str(fail)
                obs = fail.get("observed_behavior")
                exp = fail.get("expected_behavior")
                obs_str = f" [Observed: {obs}]" if obs else ""
                exp_str = f" [Expected: {exp}]" if exp else ""
                lines.append(f"- {desc}{obs_str}{exp_str}")
        else:
            lines.append("- (No explicit failures recorded)")

        lines.append("\n### 3. What Changed in Requirements:")
        if self.requirements_diff:
            added_reqs = self.requirements_diff.get("added_requirements", [])
            if added_reqs:
                lines.append("Added Requirements:")
                for ar in added_reqs:
                    lines.append(f"  + {ar}")
            added_ac = self.requirements_diff.get("added_acceptance_criteria", [])
            if added_ac:
                lines.append("Added Acceptance Criteria:")
                for ac in added_ac:
                    desc = ac.get("description") if isinstance(ac, dict) else (ac.description if hasattr(ac, "description") else str(ac))
                    lines.append(f"  + {desc}")
            modified_ac = self.requirements_diff.get("modified_acceptance_criteria", [])
            if modified_ac:
                lines.append("Modified Acceptance Criteria:")
                for mac in modified_ac:
                    desc = mac.get("description") if isinstance(mac, dict) else (mac.description if hasattr(mac, "description") else str(mac))
                    lines.append(f"  * {desc}")
            removed_ac = self.requirements_diff.get("removed_acceptance_criteria_ids", [])
            if removed_ac:
                lines.append(f"Removed Criteria IDs: {', '.join(removed_ac)}")
        else:
            lines.append("- (Requirements and criteria remain identical to previous iteration)")

        lines.append("\n### 4. What Remains to be Fixed:")
        if self.remaining_to_fix:
            for item in self.remaining_to_fix:
                lines.append(f"- {item}")
        else:
            lines.append("- (Verify existing implementation and resolve any pending failures)")

        if self.advisory_suggestions:
            lines.append("\n### Advisory Suggestions (Non-binding recommendations from peer workers):")
            for sug in self.advisory_suggestions:
                lines.append(f"- {sug.get('suggested_direction', sug)}")

        lines.append("\nCRITICAL NOTICE: Do not assume every test failure requires code changes. Investigate whether the implementation, the test assertion, or the configuration is at fault, and modify only what is strictly necessary.")
        return "\n".join(lines)


@dataclass
class IterationOutcome:
    """Outcome of a Manager engineering iteration evaluation."""
    decision: ManagerIterationDecision
    revised_work_order: Optional[ProgrammerWorkOrder] = None
    routing_target: Optional[str] = None
    reason: str = ""
    iteration_context: Optional[PriorEngineeringContext] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value if hasattr(self.decision, "value") else str(self.decision),
            "revised_work_order_id": self.revised_work_order.work_order_id if self.revised_work_order else None,
            "routing_target": self.routing_target,
            "reason": self.reason,
            "iteration_context": self.iteration_context.to_dict() if self.iteration_context else None,
            "metadata": dict(self.metadata),
        }


class EngineeringIterationCoordinator:
    """
    Coordinates multi-turn engineering iteration under strict Manager authority.
    
    Core Invariants:
    1. Manager remains the sole organizational orchestrator.
    2. Peer workers (Tester, Designer, Researcher) never command Programmer directly.
    3. Programmer executes ONLY when an authorized ProgrammerWorkOrder is issued.
    4. Prior work orders, executions, and deliveries are immutable.
    5. Revisions increment monotonically and preserve causal lineage.
    6. Suggested directions from external workers remain advisory.
    """

    @classmethod
    def orchestrate_iteration(
        cls,
        base_work_order: ProgrammerWorkOrder,
        decision: ManagerIterationDecision,
        feedback_items: Optional[Sequence[EngineeringFeedback]] = None,
        accepted_feedback_ids: Optional[Sequence[str]] = None,
        previous_execution: Optional[ProgrammerExecution] = None,
        previous_delivery: Optional[DeliveryPackage] = None,
        previous_result: Optional[ProgrammerResult] = None,
        new_technical_requirements: Optional[Sequence[str]] = None,
        new_acceptance_criteria: Optional[Sequence[Any]] = None,
        modified_acceptance_criteria: Optional[Sequence[Any]] = None,
        removed_acceptance_criteria_ids: Optional[Sequence[str]] = None,
        adopt_suggestions_as_requirements: bool = False,
        manager_notes: str = "",
        objective: Optional[str] = None,
        stale_work_order_ids: Optional[Sequence[str]] = None,
        allowed_paths: Optional[list[str]] = None,
        writable_paths: Optional[list[str]] = None,
        read_only_paths: Optional[list[str]] = None,
        forbidden_paths: Optional[list[str]] = None,
        allowed_commands: Optional[list[Any]] = None,
        iteration_budget: int = 10,
        time_budget: int = 600,
        metadata: Optional[dict[str, Any]] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> IterationOutcome:
        """
        Evaluate prior engineering state and external worker feedback under Manager authority.
        If decision is REQUEST_FIX, generates an authorized revised ProgrammerWorkOrder.
        Otherwise, returns the non-engineering routing or termination outcome.
        """
        if not base_work_order:
            raise ProgrammerValidationError("base_work_order is required for iteration.", field_name="base_work_order")

        # Normalize decision
        if isinstance(decision, str):
            try:
                norm_decision = ManagerIterationDecision(decision.upper())
            except (ValueError, KeyError):
                norm_decision = ManagerIterationDecision.ESCALATE
        else:
            norm_decision = decision

        # Non-fix decisions: Manager does NOT issue a revised ProgrammerWorkOrder
        if norm_decision == ManagerIterationDecision.ACCEPT:
            return IterationOutcome(
                decision=norm_decision,
                reason=manager_notes or "Deliverables accepted by Manager.",
                metadata={"base_work_order_id": base_work_order.work_order_id},
            )

        if norm_decision == ManagerIterationDecision.REJECT:
            return IterationOutcome(
                decision=norm_decision,
                reason=manager_notes or "Deliverables rejected by Manager.",
                metadata={"base_work_order_id": base_work_order.work_order_id},
            )

        if norm_decision == ManagerIterationDecision.CANCEL:
            return IterationOutcome(
                decision=norm_decision,
                reason=manager_notes or "Task/iteration cancelled by Manager.",
                metadata={"base_work_order_id": base_work_order.work_order_id},
            )

        if norm_decision == ManagerIterationDecision.ESCALATE:
            return IterationOutcome(
                decision=norm_decision,
                routing_target="ManagerEscalation",
                reason=manager_notes or "Escalated for administrative or architectural review.",
                metadata={"base_work_order_id": base_work_order.work_order_id},
            )

        if norm_decision == ManagerIterationDecision.REQUEST_DESIGN_CHANGE:
            return IterationOutcome(
                decision=norm_decision,
                routing_target="Designer",
                reason=manager_notes or "Routed to Designer for UI/UX specification update.",
                metadata={"base_work_order_id": base_work_order.work_order_id},
            )

        if norm_decision == ManagerIterationDecision.REQUEST_RESEARCH:
            return IterationOutcome(
                decision=norm_decision,
                routing_target="Researcher",
                reason=manager_notes or "Routed to Researcher for technical investigation.",
                metadata={"base_work_order_id": base_work_order.work_order_id},
            )

        # ---------------------------------------------------------------------
        # Manager Decision: REQUEST_FIX (or revised engineering work order)
        # ---------------------------------------------------------------------
        # 1. Synthesize what was previously implemented
        previously_implemented: list[str] = []
        if previous_delivery and previous_delivery.change_set:
            cs = previous_delivery.change_set
            for fc in (cs.files_changed or []):
                previously_implemented.append(f"Modified file: {fc}")
            for cf in (cs.created_files or []):
                previously_implemented.append(f"Created file: {cf}")
            if cs.commit_message:
                previously_implemented.append(f"Commit: {cs.commit_message}")
        elif previous_result:
            for fc in (previous_result.files_changed or []):
                previously_implemented.append(f"Modified file: {fc}")
            if previous_result.summary:
                previously_implemented.append(f"Result summary: {previous_result.summary}")

        if not previously_implemented:
            for req in (base_work_order.technical_requirements or []):
                previously_implemented.append(f"Requirement: {req}")

        # 2. Synthesize what failed (observed failures & feedback)
        failures_observed: list[dict[str, Any]] = []
        fbs = list(feedback_items or [])
        accepted_set = set(accepted_feedback_ids) if accepted_feedback_ids is not None else {fb.feedback_id for fb in fbs}

        accepted_fb_dicts: list[dict[str, Any]] = []
        advisory_sugs: list[dict[str, Any]] = []

        for fb in fbs:
            if fb.feedback_id in accepted_set:
                accepted_fb_dicts.append(fb.to_dict())
                failures_observed.append({
                    "feedback_id": fb.feedback_id,
                    "source_worker": fb.source_worker,
                    "issue": fb.issue,
                    "issue_type": fb.issue_type.value if hasattr(fb.issue_type, "value") else str(fb.issue_type),
                    "severity": fb.severity.value if hasattr(fb.severity, "value") else str(fb.severity),
                    "observed_behavior": fb.observed_behavior,
                    "expected_behavior": fb.expected_behavior,
                    "reproduction": fb.reproduction_information,
                })
                if fb.suggested_direction:
                    advisory_sugs.append({
                        "feedback_id": fb.feedback_id,
                        "suggested_direction": fb.suggested_direction,
                    })

        if previous_delivery:
            for ar in (previous_delivery.acceptance_results or []):
                ar_stat = getattr(ar, "status", None)
                if ar_stat != AcceptanceStatus.PASS and str(ar_stat).upper() != "PASS":
                    failures_observed.append({
                        "criterion_id": getattr(ar, "criterion_id", ""),
                        "description": getattr(ar, "description", ""),
                        "status": ar_stat.value if hasattr(ar_stat, "value") else str(ar_stat),
                        "message": getattr(ar, "message", ""),
                    })
            for tr in (previous_delivery.test_results or []):
                if not tr.passed:
                    failures_observed.append({
                        "test_command": tr.command,
                        "exit_code": tr.exit_code,
                        "failures": tr.tests_failed,
                    })
        elif previous_result:
            for ar in (previous_result.acceptance_results or []):
                ar_stat = getattr(ar, "status", None)
                if ar_stat != AcceptanceStatus.PASS and str(ar_stat).upper() != "PASS":
                    failures_observed.append({
                        "criterion_id": getattr(ar, "criterion_id", ""),
                        "description": getattr(ar, "description", ""),
                        "status": ar_stat.value if hasattr(ar_stat, "value") else str(ar_stat),
                        "message": getattr(ar, "message", ""),
                    })

        # 3. Compute Requirements Diff & Updated Acceptance Criteria
        base_ac = list(base_work_order.acceptance_criteria or [])
        removed_ids = set(removed_acceptance_criteria_ids or [])
        mod_dict: dict[str, Any] = {}
        for mac in (modified_acceptance_criteria or []):
            cid = getattr(mac, "criterion_id", None) or (mac.get("criterion_id") if isinstance(mac, dict) else None)
            if cid:
                mod_dict[cid] = mac

        updated_ac: list[Any] = []
        for ac in base_ac:
            cid = getattr(ac, "criterion_id", None) or (ac.get("criterion_id") if isinstance(ac, dict) else None)
            if cid in removed_ids:
                continue
            if cid in mod_dict:
                updated_ac.append(mod_dict[cid])
            else:
                updated_ac.append(ac)

        # Add new acceptance criteria
        for nac in (new_acceptance_criteria or []):
            updated_ac.append(nac)

        requirements_diff: dict[str, Any] = {
            "added_requirements": list(new_technical_requirements or []),
            "added_acceptance_criteria": [
                nac.to_dict() if hasattr(nac, "to_dict") else (dict(nac) if isinstance(nac, dict) else str(nac))
                for nac in (new_acceptance_criteria or [])
            ],
            "modified_acceptance_criteria": [
                mac.to_dict() if hasattr(mac, "to_dict") else (dict(mac) if isinstance(mac, dict) else str(mac))
                for mac in (modified_acceptance_criteria or [])
            ],
            "removed_acceptance_criteria_ids": list(removed_ids),
        }

        # 4. Synthesize remaining items to fix
        remaining_to_fix: list[str] = []
        for fo in failures_observed:
            issue_text = fo.get("issue") or fo.get("description") or fo.get("message") or str(fo)
            remaining_to_fix.append(f"Fix: {issue_text}")
        for nr in (new_technical_requirements or []):
            remaining_to_fix.append(f"Implement: {nr}")

        # 5. Build PriorEngineeringContext
        prev_exec_id = (
            previous_execution.execution_id if previous_execution
            else (previous_delivery.execution_id if previous_delivery
                  else (previous_result.execution_id if previous_result else None))
        )
        prev_del_id = previous_delivery.delivery_id if previous_delivery else None

        prior_context = PriorEngineeringContext(
            previous_work_order_id=base_work_order.work_order_id,
            previous_execution_id=prev_exec_id,
            previous_delivery_id=prev_del_id,
            previously_implemented=previously_implemented,
            failures_observed=failures_observed,
            requirements_diff=requirements_diff,
            remaining_to_fix=remaining_to_fix,
            accepted_feedback=accepted_fb_dicts,
            advisory_suggestions=advisory_sugs,
            manager_decision=norm_decision,
            manager_notes=manager_notes,
            metadata=dict(metadata or {}),
        )

        # 6. Combined requirements for transform
        combined_requirements = list(base_work_order.technical_requirements or [])
        if new_technical_requirements:
            combined_requirements.extend(new_technical_requirements)

        # Package extra context and trace
        extra_ctx = {
            "prior_engineering_context": prior_context.to_dict(),
            "previous_execution_id": prev_exec_id,
            "previous_delivery_id": prev_del_id,
            "manager_iteration_decision": norm_decision.value,
        }

        iter_trace = dict(trace or {})
        iter_trace.update({
            "iteration_cycle": base_work_order.revision_number + 1,
            "manager_iteration_decision": norm_decision.value,
            "manager_notes": manager_notes,
            "previous_delivery_id": prev_del_id,
            "previous_execution_id": prev_exec_id,
        })

        iter_meta = dict(metadata or {})
        iter_meta.update({
            "is_iteration_revision": True,
            "previous_work_order_id": base_work_order.work_order_id,
        })

        # 7. Construct new ProgrammerWorkOrder using FeedbackToWorkOrderAdapter
        revised_wo = FeedbackToWorkOrderAdapter.transform(
            base_work_order=base_work_order,
            feedback_items=fbs,
            accepted_feedback_ids=accepted_feedback_ids,
            adopt_suggestions_as_requirements=adopt_suggestions_as_requirements,
            manager_requirements=combined_requirements,
            stale_work_order_ids=stale_work_order_ids,
            objective=objective,
            allowed_paths=allowed_paths,
            writable_paths=writable_paths,
            read_only_paths=read_only_paths,
            forbidden_paths=forbidden_paths,
            allowed_commands=allowed_commands,
            acceptance_criteria=updated_ac,
            iteration_budget=iteration_budget,
            time_budget=time_budget,
            extra_context=extra_ctx,
            trace=iter_trace,
            metadata=iter_meta,
        )

        return IterationOutcome(
            decision=norm_decision,
            revised_work_order=revised_wo,
            iteration_context=prior_context,
            reason=manager_notes,
            metadata={"revised_work_order_id": revised_wo.work_order_id},
        )

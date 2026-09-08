from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence
import uuid

from core.programmer.contracts.feedback import EngineeringFeedback
from core.programmer.contracts.identifiers import (
    new_work_order_id,
)
from core.programmer.contracts.research_reference import ResearchEvidenceReference
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    DuplicateFeedbackError,
    ProgrammerValidationError,
    StaleFeedbackError,
    UnrelatedFeedbackError,
)
from core.programmer.types import (
    FeedbackSeverity,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class FeedbackToWorkOrderAdapter:
    """
    Deterministic adapter allowing Manager to evaluate, validate, and convert
    cross-worker engineering feedback (from Tester or Designer) into a new or
    revised ProgrammerWorkOrder.
    
    Invariants:
    1. Manager decides whether external feedback becomes Programmer work.
    2. Suggested direction from external workers is NOT automatically a requirement.
    3. External workers cannot directly mutate Programmer execution state or expand permissions.
    4. Unrelated, stale, or duplicate feedback submissions are strictly rejected.
    5. Complete causal lineage is preserved across all revisions.
    """

    @classmethod
    def transform(
        cls,
        base_work_order: ProgrammerWorkOrder,
        feedback_items: Sequence[EngineeringFeedback],
        accepted_feedback_ids: Optional[Sequence[str]] = None,
        adopt_suggestions_as_requirements: bool = False,
        manager_requirements: Optional[Sequence[str]] = None,
        stale_work_order_ids: Optional[Sequence[str]] = None,
        objective: Optional[str] = None,
        allowed_paths: Optional[list[str]] = None,
        writable_paths: Optional[list[str]] = None,
        read_only_paths: Optional[list[str]] = None,
        forbidden_paths: Optional[list[str]] = None,
        allowed_commands: Optional[list[Any]] = None,
        acceptance_criteria: Optional[list[Any]] = None,
        required_checks: Optional[list[str]] = None,
        iteration_budget: int = 10,
        time_budget: int = 600,
        work_order_id: Optional[str] = None,
        extra_context: Optional[dict[str, Any]] = None,
        trace: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ProgrammerWorkOrder:
        """
        Construct a revised ProgrammerWorkOrder incorporating validated feedback.
        """
        if not base_work_order:
            raise ProgrammerValidationError("base_work_order is required", field_name="base_work_order")

        stale_ids = set(stale_work_order_ids or [])
        seen_feedback_ids: set[str] = set()
        seen_issue_signatures: set[tuple[str, str]] = set()

        # 1. Validate feedback integrity: Unrelated, Stale, Duplicate checks
        for fb in feedback_items:
            # Check for duplicate feedback ID
            if fb.feedback_id in seen_feedback_ids:
                raise DuplicateFeedbackError(
                    f"Duplicate feedback ID detected: '{fb.feedback_id}'.",
                    details={"feedback_id": fb.feedback_id},
                )
            seen_feedback_ids.add(fb.feedback_id)

            # Check for duplicate issue signature
            sig = (fb.issue.strip().lower(), fb.observed_behavior.strip().lower())
            if sig in seen_issue_signatures:
                raise DuplicateFeedbackError(
                    f"Duplicate feedback issue detected for: '{fb.issue}'.",
                    details={"issue": fb.issue, "feedback_id": fb.feedback_id},
                )
            seen_issue_signatures.add(sig)

            # Check project match
            if fb.target_project != base_work_order.project_id:
                raise UnrelatedFeedbackError(
                    f"Feedback '{fb.feedback_id}' target_project '{fb.target_project}' does not match work order project '{base_work_order.project_id}'.",
                    details={"target_project": fb.target_project, "work_order_project": base_work_order.project_id},
                )

            # Check work order lineage
            if fb.related_work_order != base_work_order.work_order_id:
                raise UnrelatedFeedbackError(
                    f"Feedback '{fb.feedback_id}' related_work_order '{fb.related_work_order}' does not match base work order '{base_work_order.work_order_id}'.",
                    details={"related_work_order": fb.related_work_order, "base_work_order_id": base_work_order.work_order_id},
                )

            # Check staleness against explicit stale IDs or superseded revision numbers
            if fb.related_work_order in stale_ids:
                raise StaleFeedbackError(
                    f"Feedback '{fb.feedback_id}' references superseded work order '{fb.related_work_order}'.",
                    details={"related_work_order": fb.related_work_order},
                )
            fb_target_rev = fb.trace.get("target_revision_number")
            if fb_target_rev is not None and int(fb_target_rev) < base_work_order.revision_number:
                raise StaleFeedbackError(
                    f"Feedback '{fb.feedback_id}' targets revision {fb_target_rev}, but current revision is {base_work_order.revision_number}.",
                    details={"feedback_revision": fb_target_rev, "current_revision": base_work_order.revision_number},
                )

        # 2. Manager Acceptance / Rejection Filter
        accepted_set = set(accepted_feedback_ids) if accepted_feedback_ids is not None else seen_feedback_ids
        accepted_items: list[EngineeringFeedback] = []
        rejected_items: list[EngineeringFeedback] = []

        for fb in feedback_items:
            if fb.feedback_id in accepted_set:
                accepted_items.append(fb)
            else:
                rejected_items.append(fb)

        # 3. Construct Technical Requirements from Accepted Feedback
        # Invariant: Suggested direction is NOT automatically a requirement unless requested by Manager
        new_tech_requirements: list[str] = list(manager_requirements or [])
        advisory_suggestions: list[dict[str, Any]] = []
        feedback_evidence_refs: list[ResearchEvidenceReference] = list(base_work_order.research_evidence or [])

        # Priority calculation based on highest feedback severity
        max_severity = FeedbackSeverity.LOW
        severity_order = [FeedbackSeverity.LOW, FeedbackSeverity.MEDIUM, FeedbackSeverity.HIGH, FeedbackSeverity.CRITICAL]

        for fb in accepted_items:
            if severity_order.index(fb.severity) > severity_order.index(max_severity):
                max_severity = fb.severity

            # Base issue requirement
            req_text = f"Fix {fb.issue_type.value} ({fb.feedback_id}): {fb.issue}. Expected: {fb.expected_behavior}"
            new_tech_requirements.append(req_text)

            # Suggested direction handling
            if fb.suggested_direction:
                if adopt_suggestions_as_requirements:
                    new_tech_requirements.append(
                        f"Adopted suggestion ({fb.feedback_id}): {fb.suggested_direction}"
                    )
                else:
                    advisory_suggestions.append({
                        "feedback_id": fb.feedback_id,
                        "issue": fb.issue,
                        "suggested_direction": fb.suggested_direction,
                        "status": "ADVISORY_SUGGESTION",
                    })

            # Link evidence
            for ev in fb.evidence:
                ref = ResearchEvidenceReference.from_evidence(
                    ev,
                    relevance_notes=f"Feedback evidence from {fb.source_worker} for {fb.issue}",
                )
                feedback_evidence_refs.append(ref)

        # 4. Lineage Preservation and Permission Scope
        # Ensure external workers cannot expand permissions beyond Manager scope
        eff_allowed = allowed_paths if allowed_paths is not None else list(base_work_order.allowed_paths or [])
        eff_writable = writable_paths if writable_paths is not None else list(base_work_order.writable_paths or [])
        eff_readonly = read_only_paths if read_only_paths is not None else list(base_work_order.read_only_paths or [])
        eff_forbidden = forbidden_paths if forbidden_paths is not None else list(base_work_order.forbidden_paths or [])
        eff_commands = allowed_commands if allowed_commands is not None else list(base_work_order.allowed_commands or [])

        new_wo_id = work_order_id or new_work_order_id()
        next_revision = base_work_order.revision_number + 1

        resolved_obj = objective or f"Resolve feedback for: {base_work_order.objective}"

        # Context packaging
        feedback_context: dict[str, Any] = dict(base_work_order.context or {})
        feedback_context.update({
            "accepted_feedback": [fb.to_dict() for fb in accepted_items],
            "rejected_feedback": [fb.to_dict() for fb in rejected_items],
            "feedback_advisory_suggestions": advisory_suggestions,
            "max_feedback_severity": max_severity.value,
        })
        if extra_context:
            feedback_context.update(extra_context)

        # Trace packaging
        wo_trace = dict(trace or {})
        wo_trace.update({
            "parent_work_order_id": base_work_order.work_order_id,
            "previous_revision": base_work_order.revision_number,
            "feedback_count": len(feedback_items),
            "accepted_feedback_ids": [fb.feedback_id for fb in accepted_items],
            "rejected_feedback_ids": [fb.feedback_id for fb in rejected_items],
            "adapter": "FeedbackToWorkOrderAdapter",
            "revised_at": utc_now(),
        })

        wo_meta = dict(metadata or {})
        wo_meta.update({
            "source_feedback_ids": [fb.feedback_id for fb in accepted_items],
            "source_workers": list({fb.source_worker for fb in accepted_items}),
            "highest_severity": max_severity.value,
        })

        return ProgrammerWorkOrder(
            work_order_id=new_wo_id,
            manager_task_id=base_work_order.manager_task_id,
            project_id=base_work_order.project_id,
            correlation_id=base_work_order.correlation_id,
            objective=resolved_obj,
            parent_work_order_id=base_work_order.work_order_id,
            revision_number=next_revision,
            technical_requirements=new_tech_requirements,
            constraints=list(base_work_order.constraints or []),
            allowed_paths=eff_allowed,
            writable_paths=eff_writable,
            read_only_paths=eff_readonly,
            forbidden_paths=eff_forbidden,
            allowed_commands=eff_commands,
            acceptance_criteria=acceptance_criteria if acceptance_criteria is not None else list(base_work_order.acceptance_criteria or []),
            required_checks=required_checks if required_checks is not None else list(base_work_order.required_checks or []),
            research_evidence=feedback_evidence_refs,
            iteration_budget=iteration_budget,
            time_budget=time_budget,
            context=feedback_context,
            trace=wo_trace,
            metadata=wo_meta,
        )

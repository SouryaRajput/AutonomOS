from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Optional, Sequence
import uuid

from core.programmer.contracts.identifiers import (
    new_handoff_id,
    new_work_order_id,
)
from core.programmer.contracts.handoff import EngineeringHandoff
from core.programmer.contracts.research_reference import ResearchEvidenceReference
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ConflictingResearchError,
    MissingResearchEvidenceError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    EngineeringHandoffType,
    EpistemicContextType,
    HandoffPriority,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EpistemicContextItem:
    """
    Explicitly categorized context item distinguishing empirical observations,
    inferences, managerial mandates, and engineering assumptions.
    """
    content: str
    epistemic_type: EpistemicContextType
    source_id: Optional[str] = None
    evidence_id: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "epistemic_type": self.epistemic_type.value if hasattr(self.epistemic_type, "value") else str(self.epistemic_type),
            "source_id": self.source_id,
            "evidence_id": self.evidence_id,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpistemicContextItem:
        t_raw = data.get("epistemic_type", EpistemicContextType.OBSERVED_RESEARCH_EVIDENCE.value)
        try:
            ep_type = EpistemicContextType(t_raw)
        except (ValueError, KeyError):
            ep_type = EpistemicContextType.OBSERVED_RESEARCH_EVIDENCE
        return cls(
            content=str(data.get("content", "")),
            epistemic_type=ep_type,
            source_id=data.get("source_id"),
            evidence_id=data.get("evidence_id"),
            notes=str(data.get("notes", "")),
        )


class ResearchToWorkOrderAdapter:
    """
    Deterministic adapter allowing Manager to transform structured Researcher output
    into an authorized, bounded ProgrammerWorkOrder.
    
    Invariants:
    1. Manager remains the sole source of organizational authority.
    2. Researcher recommendations are NEVER silently converted into mandatory requirements.
    3. Unselected research evidence and raw crawler traces are strictly excluded.
    4. Epistemic boundaries are maintained (OBSERVED, INFERRED, MANAGER-DEFINED, ASSUMPTIONS).
    5. Contradictions must be surfaced and require explicit Manager resolution.
    6. Complete causal lineage is preserved.
    """

    @classmethod
    def transform(
        cls,
        research_result: Any,
        manager_task_id: str,
        objective: str,
        project_id: Optional[str] = None,
        selected_evidence_ids: Optional[Sequence[str]] = None,
        accepted_recommendation_ids: Optional[Sequence[str]] = None,
        manager_requirements: Optional[Sequence[str]] = None,
        inferred_requirements: Optional[Sequence[str]] = None,
        technical_constraints: Optional[Sequence[str]] = None,
        engineering_assumptions: Optional[Sequence[str]] = None,
        conflict_resolutions: Optional[dict[str, str]] = None,
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
        correlation_id: Optional[str] = None,
        trace: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ProgrammerWorkOrder:
        """
        Construct a validated, research-backed ProgrammerWorkOrder.
        """
        if not manager_task_id or not str(manager_task_id).strip():
            raise ProgrammerValidationError("manager_task_id cannot be empty", field_name="manager_task_id")
        if not objective or not str(objective).strip():
            raise ProgrammerValidationError("objective cannot be empty", field_name="objective")

        # Extract research properties
        res_req_id = getattr(research_result, "request_id", "") or (research_result.get("request_id", "") if isinstance(research_result, dict) else "")
        res_task_id = getattr(research_result, "task_id", "") or (research_result.get("task_id", "") if isinstance(research_result, dict) else "")
        res_project_id = getattr(research_result, "project_id", "") or (research_result.get("project_id", "") if isinstance(research_result, dict) else "")
        resolved_project_id = str(project_id or res_project_id or "default-project")

        resolved_corr_id = str(
            correlation_id
            or getattr(research_result, "correlation_id", None)
            or (research_result.get("correlation_id") if isinstance(research_result, dict) else None)
            or (trace or {}).get("correlation_id")
            or f"corr-{uuid.uuid4().hex[:8]}"
        )

        # Index available evidence and findings from research result
        evidence_items = getattr(research_result, "evidence", []) or (research_result.get("evidence", []) if isinstance(research_result, dict) else [])
        findings_items = getattr(research_result, "findings", []) or (research_result.get("findings", []) if isinstance(research_result, dict) else [])
        recommendations_items = getattr(research_result, "recommendations", []) or (research_result.get("recommendations", []) if isinstance(research_result, dict) else [])
        contradictions_items = getattr(research_result, "contradictions", []) or (research_result.get("contradictions", []) if isinstance(research_result, dict) else [])

        evidence_by_id: dict[str, Any] = {}
        for ev in evidence_items:
            ev_id = getattr(ev, "evidence_id", None) or (ev.get("evidence_id") if isinstance(ev, dict) else None)
            if ev_id:
                evidence_by_id[str(ev_id)] = ev
        for fn in findings_items:
            fn_id = getattr(fn, "finding_id", None) or (fn.get("finding_id") if isinstance(fn, dict) else None)
            if fn_id:
                evidence_by_id[str(fn_id)] = fn

        # 1. Process and check contradictions / conflicts
        resolutions = dict(conflict_resolutions or {})
        unresolved_contradictions = []
        for c in contradictions_items:
            c_id = getattr(c, "contradiction_id", None) or (c.get("contradiction_id") if isinstance(c, dict) else None)
            topic = getattr(c, "topic", "") or (c.get("topic", "") if isinstance(c, dict) else "")
            if c_id not in resolutions and topic not in resolutions:
                unresolved_contradictions.append(c)

        if unresolved_contradictions:
            c_first = unresolved_contradictions[0]
            c_id = getattr(c_first, "contradiction_id", None) or (c_first.get("contradiction_id") if isinstance(c_first, dict) else None)
            c_topic = getattr(c_first, "topic", "") or (c_first.get("topic", "") if isinstance(c_first, dict) else "")
            raise ConflictingResearchError(
                f"Unresolved conflicting research findings regarding topic '{c_topic}'. Manager resolution required.",
                contradiction_id=c_id,
                details={"topic": c_topic, "unresolved_count": len(unresolved_contradictions)},
            )

        # 2. Process selected evidence
        attached_evidence: list[ResearchEvidenceReference] = []
        epistemic_items: list[EpistemicContextItem] = []

        if selected_evidence_ids:
            for ev_id in selected_evidence_ids:
                ev_str = str(ev_id)
                if ev_str not in evidence_by_id:
                    raise MissingResearchEvidenceError(
                        ev_str,
                        f"Selected research evidence '{ev_str}' does not exist in ResearchResult.",
                    )
                raw_ev = evidence_by_id[ev_str]
                ref = ResearchEvidenceReference.from_evidence(raw_ev)
                attached_evidence.append(ref)
                epistemic_items.append(
                    EpistemicContextItem(
                        content=ref.claim_or_fact,
                        epistemic_type=EpistemicContextType.OBSERVED_RESEARCH_EVIDENCE,
                        evidence_id=ref.evidence_id,
                        source_id=ref.source_ref,
                    )
                )

        # 3. Process requirements distinguishing Inferred vs Manager-defined vs Recommendations
        tech_requirements: list[str] = []
        
        # Explicit Manager-defined requirements
        for mr in (manager_requirements or []):
            tech_requirements.append(str(mr))
            epistemic_items.append(
                EpistemicContextItem(
                    content=str(mr),
                    epistemic_type=EpistemicContextType.MANAGER_DEFINED_REQUIREMENTS,
                )
            )

        # Recommendations: only explicitly accepted ones become Manager-defined requirements
        accepted_rec_set = set(accepted_recommendation_ids or [])
        advisory_recommendations: list[dict[str, Any]] = []

        for rec in recommendations_items:
            rec_id = getattr(rec, "recommendation_id", None) or (rec.get("recommendation_id") if isinstance(rec, dict) else None)
            rec_action = getattr(rec, "action", "") or (rec.get("action", "") if isinstance(rec, dict) else "")
            rec_rationale = getattr(rec, "rationale", "") or (rec.get("rationale", "") if isinstance(rec, dict) else "")

            if rec_id in accepted_rec_set:
                req_text = f"Adopted recommendation ({rec_id}): {rec_action}"
                tech_requirements.append(req_text)
                epistemic_items.append(
                    EpistemicContextItem(
                        content=req_text,
                        epistemic_type=EpistemicContextType.MANAGER_DEFINED_REQUIREMENTS,
                        source_id=str(rec_id),
                        notes=f"Accepted Researcher recommendation: {rec_rationale}",
                    )
                )
            else:
                advisory_recommendations.append({
                    "recommendation_id": rec_id,
                    "action": rec_action,
                    "rationale": rec_rationale,
                    "status": "UNACCEPTED_ADVISORY",
                })

        # Inferred requirements: preserved in context and epistemic items, distinct from Manager mandates
        for inf in (inferred_requirements or []):
            epistemic_items.append(
                EpistemicContextItem(
                    content=str(inf),
                    epistemic_type=EpistemicContextType.INFERRED_REQUIREMENTS,
                )
            )

        # Engineering assumptions: recorded epistemically
        for asm in (engineering_assumptions or []):
            epistemic_items.append(
                EpistemicContextItem(
                    content=str(asm),
                    epistemic_type=EpistemicContextType.PROGRAMMER_ENGINEERING_ASSUMPTIONS,
                )
            )

        # 4. Prepare context (keeping it concise, non-bloated, and excluding raw crawler logs)
        wo_context: dict[str, Any] = {
            "source_research": {
                "request_id": res_req_id,
                "task_id": res_task_id,
                "project_id": res_project_id,
                "selected_evidence_count": len(attached_evidence),
            },
            "epistemic_context": [item.to_dict() for item in epistemic_items],
            "inferred_requirements": list(inferred_requirements or []),
            "engineering_assumptions": list(engineering_assumptions or []),
            "advisory_recommendations": advisory_recommendations,
            "conflict_resolutions": resolutions,
        }

        # Constraints
        all_constraints = list(technical_constraints or [])

        # Trace and lineage
        wo_id = work_order_id or new_work_order_id()
        wo_trace = dict(trace or {})
        wo_trace.update({
            "source_research_request_id": res_req_id,
            "source_research_task_id": res_task_id,
            "manager_task_id": manager_task_id,
            "adapter": "ResearchToWorkOrderAdapter",
            "transformed_at": utc_now(),
        })

        wo_meta = dict(metadata or {})
        wo_meta.update({
            "source_research_request_id": res_req_id,
            "source_research_task_id": res_task_id,
            "attached_evidence_ids": [e.evidence_id for e in attached_evidence],
            "unselected_evidence_count": len(evidence_by_id) - len(attached_evidence),
        })

        return ProgrammerWorkOrder(
            work_order_id=wo_id,
            manager_task_id=manager_task_id,
            project_id=resolved_project_id,
            correlation_id=resolved_corr_id,
            objective=objective,
            technical_requirements=tech_requirements,
            constraints=all_constraints,
            allowed_paths=allowed_paths,
            writable_paths=writable_paths,
            read_only_paths=read_only_paths,
            forbidden_paths=forbidden_paths,
            allowed_commands=allowed_commands,
            acceptance_criteria=acceptance_criteria,
            required_checks=required_checks,
            research_evidence=attached_evidence,
            iteration_budget=iteration_budget,
            time_budget=time_budget,
            context=wo_context,
            trace=wo_trace,
            metadata=wo_meta,
        )

    @classmethod
    def create_research_handoff(
        cls,
        research_result: Any,
        manager_task_id: str,
        objective: str,
        target_worker_id: str = "worker-programmer",
        source_worker_id: str = "worker-researcher",
        selected_evidence_ids: Optional[Sequence[str]] = None,
        accepted_recommendation_ids: Optional[Sequence[str]] = None,
        priority: HandoffPriority = HandoffPriority.NORMAL,
        trace: Optional[dict[str, Any]] = None,
    ) -> EngineeringHandoff:
        """
        Create a validated EngineeringHandoff contract of type RESEARCH_TO_PROGRAMMER.
        """
        res_req_id = getattr(research_result, "request_id", "") or (research_result.get("request_id", "") if isinstance(research_result, dict) else "")
        res_task_id = getattr(research_result, "task_id", "") or (research_result.get("task_id", "") if isinstance(research_result, dict) else "")
        res_project_id = getattr(research_result, "project_id", "") or (research_result.get("project_id", "") if isinstance(research_result, dict) else "default-project")

        evidence_items = getattr(research_result, "evidence", []) or (research_result.get("evidence", []) if isinstance(research_result, dict) else [])
        evidence_dicts: list[dict[str, Any]] = []
        selected_set = set(selected_evidence_ids or [])

        for ev in evidence_items:
            ev_id = getattr(ev, "evidence_id", None) or (ev.get("evidence_id") if isinstance(ev, dict) else None)
            if not selected_set or ev_id in selected_set:
                if hasattr(ev, "to_dict"):
                    evidence_dicts.append(ev.to_dict())
                elif isinstance(ev, dict):
                    evidence_dicts.append(dict(ev))

        h_trace = dict(trace or {})
        h_trace.update({
            "source_research_request_id": res_req_id,
            "source_research_task_id": res_task_id,
            "manager_task_id": manager_task_id,
        })

        return EngineeringHandoff(
            handoff_id=new_handoff_id(),
            project_id=res_project_id,
            source_worker_id=source_worker_id,
            target_worker_id=target_worker_id,
            source_task_id=res_task_id or manager_task_id,
            objective=objective,
            requested_action="Implement engineering solution grounded in verified research evidence.",
            handoff_type=EngineeringHandoffType.RESEARCH_TO_PROGRAMMER,
            evidence=evidence_dicts,
            priority=priority,
            trace=h_trace,
        )

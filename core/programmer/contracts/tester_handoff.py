from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
from typing import Any, Optional, Sequence
import uuid

from core.programmer.contracts.handoff import EngineeringHandoff
from core.programmer.contracts.identifiers import (
    new_handoff_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    MissingVerificationEvidenceError,
    ProgrammerValidationError,
    UnauthorizedQAClaimError,
)
from core.programmer.types import (
    ApiChangeType,
    EngineeringHandoffType,
    HandoffPriority,
    VerificationDomain,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ChangedApiContract:
    """
    Documents an added, modified, deprecated, or removed API endpoint and its impact.
    """
    endpoint_ref: str
    change_type: ApiChangeType = ApiChangeType.MODIFIED
    description: str = ""
    breaking_change: bool = False
    supporting_evidence_ids: list[str] = field(default_factory=list)

    def __post_init__(self):
        if isinstance(self.change_type, str):
            try:
                self.change_type = ApiChangeType(self.change_type.upper())
            except (ValueError, KeyError):
                self.change_type = ApiChangeType.MODIFIED
        self.supporting_evidence_ids = [str(eid) for eid in (self.supporting_evidence_ids or [])]

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint_ref": self.endpoint_ref,
            "change_type": self.change_type.value if hasattr(self.change_type, "value") else str(self.change_type),
            "description": self.description,
            "breaking_change": self.breaking_change,
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChangedApiContract:
        ct_raw = data.get("change_type", ApiChangeType.MODIFIED.value)
        try:
            ct = ApiChangeType(ct_raw)
        except (ValueError, KeyError):
            ct = ApiChangeType.MODIFIED
        return cls(
            endpoint_ref=str(data.get("endpoint_ref", "")),
            change_type=ct,
            description=str(data.get("description", "")),
            breaking_change=bool(data.get("breaking_change", False)),
            supporting_evidence_ids=list(data.get("supporting_evidence_ids", [])),
        )


@dataclass
class ProgrammerVerificationSummary:
    """
    Developer-side verification results.
    Explicitly marked with PROGRAMMER_VERIFICATION domain to prevent bypassing independent QA.
    """
    verification_domain: VerificationDomain = VerificationDomain.PROGRAMMER_VERIFICATION
    checks_run: int = 0
    checks_passed: int = 0
    checks_failed: int = 0
    evidence_ids: list[str] = field(default_factory=list)
    summary_notes: str = ""
    independent_testing_recommended: bool = True
    recommended_focus_areas: list[str] = field(default_factory=list)

    def __post_init__(self):
        # Enforce invariant: Programmer verification does NOT preclude independent QA
        self.verification_domain = VerificationDomain.PROGRAMMER_VERIFICATION
        self.independent_testing_recommended = True
        self.evidence_ids = [str(eid) for eid in (self.evidence_ids or [])]
        self.recommended_focus_areas = [str(a) for a in (self.recommended_focus_areas or [])]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verification_domain": self.verification_domain.value,
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "evidence_ids": list(self.evidence_ids),
            "summary_notes": self.summary_notes,
            "independent_testing_recommended": self.independent_testing_recommended,
            "recommended_focus_areas": list(self.recommended_focus_areas),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammerVerificationSummary:
        return cls(
            verification_domain=VerificationDomain.PROGRAMMER_VERIFICATION,
            checks_run=int(data.get("checks_run", 0)),
            checks_passed=int(data.get("checks_passed", 0)),
            checks_failed=int(data.get("checks_failed", 0)),
            evidence_ids=list(data.get("evidence_ids", [])),
            summary_notes=str(data.get("summary_notes", "")),
            independent_testing_recommended=True,
            recommended_focus_areas=list(data.get("recommended_focus_areas", [])),
        )


_UNAUTHORIZED_QA_PHRASES = [
    "testing is complete",
    "tester tasks complete",
    "no tester required",
    "testing unnecessary",
    "bypass tester",
    "skip independent testing",
    "qa unnecessary",
    "mark tester complete",
]


class ProgrammerToTesterHandoffBuilder:
    """
    Constructs a structured, validated EngineeringHandoff of type PROGRAMMER_TO_TESTER.
    
    Invariants:
    1. Distinguishes PROGRAMMER_VERIFICATION from TESTER_VERIFICATION.
    2. Programmer's internal test success is NOT treated as proof that QA is unnecessary.
    3. Programmer cannot mark Tester tasks complete or mutate Tester state.
    4. All referenced evidence IDs must exist in provided evidence records.
    5. Preserves lineage back to ManagerTask and ProgrammerWorkOrder.
    """

    @classmethod
    def build(
        cls,
        work_order: ProgrammerWorkOrder,
        implementation_summary: str,
        changed_files: Optional[Sequence[str]] = None,
        created_files: Optional[Sequence[str]] = None,
        deleted_files: Optional[Sequence[str]] = None,
        diff_verification: Optional[Any] = None,
        affected_modules: Optional[Sequence[str]] = None,
        changed_apis: Optional[Sequence[ChangedApiContract]] = None,
        changed_data_models: Optional[dict[str, Any]] = None,
        configuration_changes: Optional[Sequence[str]] = None,
        required_test_areas: Optional[Sequence[str]] = None,
        high_value_test_areas: Optional[Sequence[str]] = None,
        known_risks: Optional[Sequence[Any]] = None,
        known_limitations: Optional[Sequence[str]] = None,
        unresolved_issues: Optional[Sequence[str]] = None,
        previous_verification_results: Optional[Sequence[Any]] = None,
        verification_evidence: Optional[Sequence[Any]] = None,
        artifacts: Optional[Sequence[str]] = None,
        target_worker_id: str = "worker-tester",
        source_worker_id: str = "worker-programmer",
        objective: Optional[str] = None,
        requested_action: str = "Execute independent QA test plan covering modified modules and changed APIs.",
        priority: HandoffPriority = HandoffPriority.NORMAL,
        trace: Optional[dict[str, Any]] = None,
    ) -> EngineeringHandoff:
        """
        Assemble and validate a Programmer-to-Tester EngineeringHandoff contract.
        """
        if not work_order:
            raise ProgrammerValidationError("work_order is required", field_name="work_order")
        if not implementation_summary or not implementation_summary.strip():
            raise ProgrammerValidationError("implementation_summary cannot be empty", field_name="implementation_summary")

        # 1. Authority Boundary Guard: Ensure Programmer does not claim Tester tasks are complete
        all_text = f"{implementation_summary} {requested_action}".lower()
        for phrase in _UNAUTHORIZED_QA_PHRASES:
            if phrase in all_text:
                raise UnauthorizedQAClaimError(
                    f"Unauthorized QA authority claim: '{phrase}'. Programmer cannot mark Tester tasks complete or assert that independent QA is unnecessary.",
                    claim=phrase,
                )

        # 2. Extract and reconcile file changes
        c_files = list(changed_files or [])
        cr_files = list(created_files or [])
        d_files = list(deleted_files or [])

        if diff_verification is not None:
            if hasattr(diff_verification, "files_changed") and not c_files:
                c_files = list(diff_verification.files_changed)
            if hasattr(diff_verification, "files_created") and not cr_files:
                cr_files = list(diff_verification.files_created)
            if hasattr(diff_verification, "files_deleted") and not d_files:
                d_files = list(diff_verification.files_deleted)

        # 3. Index available evidence IDs
        known_evidence_ids: set[str] = set()
        evidence_dicts: list[dict[str, Any]] = []

        for ev in (verification_evidence or []):
            ev_id = getattr(ev, "evidence_id", None) or getattr(ev, "id", None) or (ev.get("evidence_id") if isinstance(ev, dict) else None)
            if ev_id:
                known_evidence_ids.add(str(ev_id))
            if hasattr(ev, "to_dict"):
                evidence_dicts.append(ev.to_dict())
            elif isinstance(ev, dict):
                evidence_dicts.append(dict(ev))
            else:
                evidence_dicts.append({"evidence": str(ev)})

        for rev in (work_order.research_evidence or []):
            known_evidence_ids.add(str(rev.evidence_id))

        # 4. Validate evidence references
        # Check changed APIs
        for api in (changed_apis or []):
            for eid in api.supporting_evidence_ids:
                if eid not in known_evidence_ids:
                    raise MissingVerificationEvidenceError(
                        eid,
                        f"Changed API '{api.endpoint_ref}' references evidence '{eid}' which is missing from handoff evidence.",
                    )

        # Check previous verification results
        checks_run = 0
        checks_passed = 0
        checks_failed = 0
        verified_results_list: list[dict[str, Any]] = []

        for chk in (previous_verification_results or []):
            checks_run += 1
            status_val = getattr(chk, "status", None) or (chk.get("status") if isinstance(chk, dict) else None)
            if str(status_val).upper() in ("PASS", "PASSED", "SUCCESS"):
                checks_passed += 1
            else:
                checks_failed += 1

            ev_refs = getattr(chk, "evidence", []) or (chk.get("evidence", []) if isinstance(chk, dict) else [])
            for eid in ev_refs:
                if str(eid) not in known_evidence_ids:
                    raise MissingVerificationEvidenceError(
                        str(eid),
                        f"Previous verification check '{getattr(chk, 'check_id', 'unknown')}' references evidence '{eid}' which is missing from handoff evidence.",
                    )
            if hasattr(chk, "to_dict"):
                verified_results_list.append(chk.to_dict())
            elif isinstance(chk, dict):
                verified_results_list.append(dict(chk))
            else:
                verified_results_list.append({"check": str(chk)})

        # 5. Build Programmer Verification Summary
        focus_areas = list(high_value_test_areas or [])
        # Auto-recommend focus areas based on breaking changes and deleted files
        for api in (changed_apis or []):
            if api.breaking_change and api.endpoint_ref not in focus_areas:
                focus_areas.append(f"Breaking API Change: {api.endpoint_ref}")
        if d_files and "Deleted Files & Regressions" not in focus_areas:
            focus_areas.append("Deleted Files & Regressions")

        prev_summary = ProgrammerVerificationSummary(
            verification_domain=VerificationDomain.PROGRAMMER_VERIFICATION,
            checks_run=checks_run,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            evidence_ids=list(known_evidence_ids),
            summary_notes=f"Programmer completed {checks_passed}/{checks_run} checks. Independent QA required.",
            independent_testing_recommended=True,
            recommended_focus_areas=focus_areas,
        )

        # 6. Build context payload
        all_limitations = list(known_limitations or [])
        all_unresolved = list(unresolved_issues or [])

        tester_context: dict[str, Any] = {
            "implementation_summary": implementation_summary,
            "changed_files": c_files,
            "created_files": cr_files,
            "deleted_files": d_files,
            "affected_modules": list(affected_modules or []),
            "changed_apis": [api.to_dict() for api in (changed_apis or [])],
            "changed_data_models": dict(changed_data_models or {}),
            "configuration_changes": list(configuration_changes or []),
            "required_test_areas": list(required_test_areas or []),
            "high_value_test_areas": focus_areas,
            "known_limitations": all_limitations,
            "unresolved_issues": all_unresolved,
            "previous_verification": {
                "summary": prev_summary.to_dict(),
                "results": verified_results_list,
            },
        }

        # Format acceptance criteria strings
        criteria_strings: list[str] = []
        for ac in (work_order.acceptance_criteria or []):
            if hasattr(ac, "description"):
                criteria_strings.append(f"{getattr(ac, 'criterion_id', '')}: {ac.description}".strip(": "))
            elif isinstance(ac, dict):
                desc = ac.get("description", str(ac))
                cid = ac.get("criterion_id", "")
                criteria_strings.append(f"{cid}: {desc}".strip(": "))
            else:
                criteria_strings.append(str(ac))

        # Format risks
        risk_records: list[dict[str, Any]] = []
        for r in (known_risks or []):
            if hasattr(r, "to_dict"):
                risk_records.append(r.to_dict())
            elif isinstance(r, dict):
                risk_records.append(dict(r))
            else:
                risk_records.append({"description": str(r)})

        # Trace
        h_trace = dict(trace or {})
        h_trace.update({
            "work_order_id": work_order.work_order_id,
            "manager_task_id": work_order.manager_task_id,
            "builder": "ProgrammerToTesterHandoffBuilder",
            "built_at": utc_now(),
        })

        return EngineeringHandoff(
            handoff_id=new_handoff_id(),
            project_id=work_order.project_id,
            source_worker_id=source_worker_id,
            target_worker_id=target_worker_id,
            source_task_id=work_order.manager_task_id or work_order.task_id or "task-default",
            work_order_id=work_order.work_order_id,
            handoff_type=EngineeringHandoffType.PROGRAMMER_TO_TESTER,
            objective=objective or f"QA verification for: {work_order.objective}",
            requested_action=requested_action,
            context=tester_context,
            artifacts=list(artifacts or (c_files + cr_files)),
            requirements=[f"Verify: {c}" for c in criteria_strings],
            constraints=list(work_order.constraints or []),
            acceptance_criteria=criteria_strings,
            evidence=evidence_dicts,
            risks=risk_records,
            known_unknowns=all_unresolved + all_limitations,
            priority=priority,
            trace=h_trace,
        )

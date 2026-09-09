from __future__ import annotations

from typing import Any, Optional

from core.tester.contracts.identifiers import (
    validate_execution_id,
    validate_result_id,
    validate_work_order_id,
)
from core.tester.errors import (
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    AcceptanceCriterionStatus,
    DefectSeverity,
    TesterResultStatus,
)


class TesterResultValidator:
    """
    Deterministic validator for TesterResult packages.
    
    Invariants:
    - Strict causal lineage (Task -> WorkOrder -> Execution -> Result).
    - Complete referential integrity: every evidence_id referenced anywhere must exist in result.evidence.
    - Zero duplicate identifiers within any collection.
    - Acceptance criteria PASS strictly requires supporting evidence.
    - Defects must contain concrete observations and reproduction steps (anti-opinion boundary).
    - QualitySummary counts must be mathematically consistent with actual items.
    """
    __test__ = False

    @classmethod
    def validate_all(cls, result: Any) -> None:
        """Run complete deterministic validation on the given TesterResult."""
        cls.validate_identity_and_lineage(result)
        cls.validate_status(result)
        cls.validate_unique_ids(result)
        cls.validate_evidence_integrity(result)
        cls.validate_defects(result)
        cls.validate_findings(result)
        cls.validate_acceptance_results(result)
        cls.validate_quality_summary(result)

    @classmethod
    def validate_identity_and_lineage(cls, result: Any) -> None:
        """Validate result identifiers and required lineage fields."""
        validate_result_id(result.result_id)
        validate_execution_id(result.execution_id)
        validate_work_order_id(result.work_order_id)

        task_id = getattr(result, "task_id", None) or getattr(result, "manager_task_id", None)
        if not task_id or not str(task_id).strip():
            raise TesterLineageError("TesterResult must specify a valid non-empty task_id.")

        if not getattr(result, "project_id", None) or not str(result.project_id).strip():
            raise TesterLineageError("TesterResult must specify a valid non-empty project_id.")

        if not getattr(result, "correlation_id", None) or not str(result.correlation_id).strip():
            raise TesterLineageError("TesterResult must specify a valid non-empty correlation_id.")

    @classmethod
    def validate_status(cls, result: Any) -> None:
        """Validate that status is a valid TesterResultStatus."""
        if not isinstance(result.status, TesterResultStatus):
            raise TesterValidationError(
                f"Invalid TesterResult status: {result.status}",
                field_name="status",
            )

    @classmethod
    def validate_unique_ids(cls, result: Any) -> None:
        """Ensure no duplicate IDs exist within any discrete collection."""
        # 1. Test cases
        tc_ids = set()
        for tc in getattr(result, "test_cases", []):
            tid = getattr(tc, "test_id", None)
            if tid in tc_ids:
                raise TesterValidationError(f"Duplicate test_id '{tid}' in test_cases.", field_name="test_cases")
            if tid:
                tc_ids.add(tid)

        # 2. Defects
        def_ids = set()
        for d in getattr(result, "defects", []):
            did = getattr(d, "defect_id", None)
            if did in def_ids:
                raise TesterValidationError(f"Duplicate defect_id '{did}' in defects.", field_name="defects")
            if did:
                def_ids.add(did)

        # 3. Findings
        find_ids = set()
        for f in getattr(result, "findings", []):
            fid = getattr(f, "finding_id", None)
            if fid in find_ids:
                raise TesterValidationError(f"Duplicate finding_id '{fid}' in findings.", field_name="findings")
            if fid:
                find_ids.add(fid)

        # 4. Acceptance results
        ac_ids = set()
        for ac in getattr(result, "acceptance_results", []):
            cid = getattr(ac, "criterion_id", None)
            if cid in ac_ids:
                raise TesterValidationError(f"Duplicate criterion_id '{cid}' in acceptance_results.", field_name="acceptance_results")
            if cid:
                ac_ids.add(cid)

        # 5. Evidence
        ev_ids = set()
        for ev in getattr(result, "evidence", []):
            eid = getattr(ev, "evidence_id", None)
            if eid in ev_ids:
                raise TesterValidationError(f"Duplicate evidence_id '{eid}' in evidence package.", field_name="evidence")
            if eid:
                ev_ids.add(eid)

        # 6. Recommendations
        rec_ids = set()
        for rec in getattr(result, "recommendations", []):
            rid = getattr(rec, "recommendation_id", None)
            if rid and rid in rec_ids:
                raise TesterValidationError(f"Duplicate recommendation_id '{rid}' in recommendations.", field_name="recommendations")
            if rid:
                rec_ids.add(rid)

    @classmethod
    def validate_evidence_integrity(cls, result: Any) -> None:
        """
        Verify referential integrity of all evidence IDs referenced in result.
        Every evidence ID referenced anywhere in the result must exist in result.evidence.
        """
        known_evidence_ids = {
            getattr(e, "evidence_id")
            for e in getattr(result, "evidence", [])
            if hasattr(e, "evidence_id")
        }

        # Check references from test cases
        for tc in getattr(result, "test_cases", []):
            for eid in getattr(tc, "evidence_ids", []):
                if eid not in known_evidence_ids:
                    raise TesterValidationError(
                        f"TestCase '{getattr(tc, 'name', 'unnamed')}' references unknown evidence_id '{eid}' "
                        f"not found in result evidence package.",
                        field_name="evidence_ids",
                    )

        # Check references from defects
        for d in getattr(result, "defects", []):
            for eid in getattr(d, "evidence_ids", []):
                if eid not in known_evidence_ids:
                    raise TesterValidationError(
                        f"Defect '{getattr(d, 'defect_id')}' references unknown evidence_id '{eid}' "
                        f"not found in result evidence package.",
                        field_name="evidence_ids",
                    )

        # Check references from findings
        for f in getattr(result, "findings", []):
            for eid in getattr(f, "evidence_ids", []):
                if eid not in known_evidence_ids:
                    raise TesterValidationError(
                        f"Finding '{getattr(f, 'finding_id')}' references unknown evidence_id '{eid}' "
                        f"not found in result evidence package.",
                        field_name="evidence_ids",
                    )

        # Check references from acceptance results
        for ac in getattr(result, "acceptance_results", []):
            for eid in getattr(ac, "evidence_ids", []):
                if eid not in known_evidence_ids:
                    raise TesterValidationError(
                        f"AcceptanceCriterion '{getattr(ac, 'criterion_id')}' references unknown evidence_id '{eid}' "
                        f"not found in result evidence package.",
                        field_name="evidence_ids",
                    )

        # Check references from recommendations
        for rec in getattr(result, "recommendations", []):
            for eid in getattr(rec, "evidence_ids", []):
                if eid not in known_evidence_ids:
                    raise TesterValidationError(
                        f"Recommendation '{getattr(rec, 'title', '')}' references unknown evidence_id '{eid}' "
                        f"not found in result evidence package.",
                        field_name="evidence_ids",
                    )

    @classmethod
    def validate_defects(cls, result: Any) -> None:
        """Validate each defect in the result."""
        for d in getattr(result, "defects", []):
            if hasattr(d, "validate"):
                d.validate()

    @classmethod
    def validate_findings(cls, result: Any) -> None:
        """Validate each finding in the result."""
        for f in getattr(result, "findings", []):
            if hasattr(f, "validate"):
                f.validate()

    @classmethod
    def validate_acceptance_results(cls, result: Any) -> None:
        """Validate each acceptance criterion result."""
        for ac in getattr(result, "acceptance_results", []):
            if hasattr(ac, "validate"):
                ac.validate()
            # Double check PASS without evidence
            if getattr(ac, "status", None) == AcceptanceCriterionStatus.PASS and not getattr(ac, "evidence_ids", []):
                raise TesterValidationError(
                    f"AcceptanceCriterion '{getattr(ac, 'criterion_id')}' marked PASS without evidence. "
                    "Evidence-backed evaluation strictly requires supporting evidence for PASS.",
                    field_name="evidence_ids",
                )

    @classmethod
    def validate_quality_summary(cls, result: Any) -> None:
        """Validate that quality_summary counts strictly match actual result collections."""
        qs = getattr(result, "quality_summary", None)
        if qs is None:
            return

        if hasattr(qs, "validate"):
            qs.validate()

        # Cross-verify test counts
        tcs = getattr(result, "test_cases", [])
        if tcs and qs.total_tests != len(tcs):
            raise TesterValidationError(
                f"QualitySummary total_tests ({qs.total_tests}) does not match test_cases count ({len(tcs)}).",
                field_name="quality_summary.total_tests",
            )

        # Cross-verify critical defects
        crit_actual = sum(
            1 for d in getattr(result, "defects", [])
            if getattr(d, "severity", None) == DefectSeverity.CRITICAL
        )
        if qs.critical_defects != crit_actual:
            raise TesterValidationError(
                f"QualitySummary critical_defects ({qs.critical_defects}) does not match actual count ({crit_actual}).",
                field_name="quality_summary.critical_defects",
            )

        # Cross-verify high defects
        high_actual = sum(
            1 for d in getattr(result, "defects", [])
            if getattr(d, "severity", None) == DefectSeverity.HIGH
        )
        if qs.high_defects != high_actual:
            raise TesterValidationError(
                f"QualitySummary high_defects ({qs.high_defects}) does not match actual count ({high_actual}).",
                field_name="quality_summary.high_defects",
            )

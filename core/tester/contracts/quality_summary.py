from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.thresholds import QualityThresholds
from core.tester.errors import TesterValidationError
from core.tester.types import (
    AcceptanceCriterionStatus,
    AcceptanceSummaryStatus,
    DefectSeverity,
    FindingCategory,
    TestCaseStatus,
)


@dataclass
class QualitySummary:
    """
    Deterministic aggregation of test executions, defects, findings, and criteria evaluations.
    
    Invariants:
    - Purely deterministic counting and threshold comparison.
    - Zero fuzzy AI scores or universal synthetic numbers.
    - Bounded and validated integer counters.
    """
    __test__ = False
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    not_verified: int = 0
    critical_defects: int = 0
    high_defects: int = 0
    medium_low_defects: int = 0
    major_ux_findings: int = 0
    performance_findings: int = 0
    acceptance_status: AcceptanceSummaryStatus = AcceptanceSummaryStatus.NOT_VERIFIED
    thresholds_met: Optional[bool] = None
    evaluated_against_thresholds: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.acceptance_status, str):
            try:
                self.acceptance_status = AcceptanceSummaryStatus(self.acceptance_status.upper())
            except (ValueError, KeyError):
                self.acceptance_status = AcceptanceSummaryStatus.NOT_VERIFIED

    def validate(self) -> None:
        """Validate mathematical and count consistency."""
        non_neg_fields = [
            ("total_tests", self.total_tests),
            ("passed", self.passed),
            ("failed", self.failed),
            ("blocked", self.blocked),
            ("not_verified", self.not_verified),
            ("critical_defects", self.critical_defects),
            ("high_defects", self.high_defects),
            ("medium_low_defects", self.medium_low_defects),
            ("major_ux_findings", self.major_ux_findings),
            ("performance_findings", self.performance_findings),
        ]
        for name, val in non_neg_fields:
            if not isinstance(val, int) or val < 0:
                raise TesterValidationError(
                    f"QualitySummary count '{name}' must be a non-negative integer, got {val}.",
                    field_name=f"quality_summary.{name}",
                )

        if self.total_tests > 0:
            sum_tests = self.passed + self.failed + self.blocked + self.not_verified
            if sum_tests != self.total_tests:
                raise TesterValidationError(
                    f"QualitySummary inconsistent: passed({self.passed}) + failed({self.failed}) + "
                    f"blocked({self.blocked}) + not_verified({self.not_verified}) = {sum_tests}, "
                    f"does not match total_tests({self.total_tests}).",
                    field_name="quality_summary.total_tests",
                )

    @classmethod
    def compute(
        cls,
        test_cases: Sequence[Any] = (),
        defects: Sequence[Any] = (),
        findings: Sequence[Any] = (),
        acceptance_results: Sequence[Any] = (),
        quality_thresholds: Optional[QualityThresholds] = None,
    ) -> QualitySummary:
        """
        Deterministically aggregate test, defect, finding, and acceptance collections.
        Evaluates against quality_thresholds if provided.
        """
        # 1. Aggregate test cases
        total_tests = len(test_cases)
        passed = sum(1 for tc in test_cases if getattr(tc, "status", None) == TestCaseStatus.PASS)
        failed = sum(1 for tc in test_cases if getattr(tc, "status", None) == TestCaseStatus.FAIL)
        blocked = sum(1 for tc in test_cases if getattr(tc, "status", None) == TestCaseStatus.BLOCKED)
        not_verified = total_tests - (passed + failed + blocked)

        # 2. Aggregate defects
        critical_defects = sum(
            1 for d in defects
            if getattr(d, "severity", None) == DefectSeverity.CRITICAL
        )
        high_defects = sum(
            1 for d in defects
            if getattr(d, "severity", None) == DefectSeverity.HIGH
        )
        medium_low_defects = sum(
            1 for d in defects
            if getattr(d, "severity", None) in (DefectSeverity.MEDIUM, DefectSeverity.LOW)
        )

        # 3. Aggregate findings
        major_ux_findings = sum(
            1 for f in findings
            if getattr(f, "category", None) == FindingCategory.UX
            and getattr(f, "severity", None) in (DefectSeverity.CRITICAL, DefectSeverity.HIGH)
        )
        performance_findings = sum(
            1 for f in findings
            if getattr(f, "category", None) == FindingCategory.PERFORMANCE
        )

        # 4. Aggregate acceptance status
        if not acceptance_results:
            acceptance_status = AcceptanceSummaryStatus.NOT_VERIFIED
        else:
            ac_passed = sum(
                1 for ac in acceptance_results
                if getattr(ac, "status", None) == AcceptanceCriterionStatus.PASS
            )
            ac_failed = sum(
                1 for ac in acceptance_results
                if getattr(ac, "status", None) == AcceptanceCriterionStatus.FAIL
            )
            if ac_passed == len(acceptance_results):
                acceptance_status = AcceptanceSummaryStatus.ALL_PASSED
            elif ac_failed == len(acceptance_results):
                acceptance_status = AcceptanceSummaryStatus.FAILED
            elif ac_passed > 0 or ac_failed > 0:
                acceptance_status = AcceptanceSummaryStatus.PARTIAL
            else:
                acceptance_status = AcceptanceSummaryStatus.NOT_VERIFIED

        # 5. Evaluate against thresholds deterministically
        thresholds_met: Optional[bool] = None
        evaluated_against_thresholds = False
        if quality_thresholds is not None:
            evaluated_against_thresholds = True
            # Acceptance pass rate
            ac_total = len(acceptance_results)
            ac_pass_rate = (
                (sum(1 for ac in acceptance_results if getattr(ac, "status", None) == AcceptanceCriterionStatus.PASS) / ac_total)
                if ac_total > 0 else 0.0
            )

            is_crit_ok = critical_defects <= quality_thresholds.max_critical_defects
            is_high_ok = high_defects <= quality_thresholds.max_high_defects
            is_pass_rate_ok = ac_pass_rate >= quality_thresholds.min_acceptance_pass_rate if ac_total > 0 else True

            thresholds_met = is_crit_ok and is_high_ok and is_pass_rate_ok

        summary = cls(
            total_tests=total_tests,
            passed=passed,
            failed=failed,
            blocked=blocked,
            not_verified=not_verified,
            critical_defects=critical_defects,
            high_defects=high_defects,
            medium_low_defects=medium_low_defects,
            major_ux_findings=major_ux_findings,
            performance_findings=performance_findings,
            acceptance_status=acceptance_status,
            thresholds_met=thresholds_met,
            evaluated_against_thresholds=evaluated_against_thresholds,
        )
        summary.validate()
        return summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tests": self.total_tests,
            "passed": self.passed,
            "failed": self.failed,
            "blocked": self.blocked,
            "not_verified": self.not_verified,
            "critical_defects": self.critical_defects,
            "high_defects": self.high_defects,
            "medium_low_defects": self.medium_low_defects,
            "major_ux_findings": self.major_ux_findings,
            "performance_findings": self.performance_findings,
            "acceptance_status": (
                self.acceptance_status.value
                if hasattr(self.acceptance_status, "value")
                else str(self.acceptance_status)
            ),
            "thresholds_met": self.thresholds_met,
            "evaluated_against_thresholds": self.evaluated_against_thresholds,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QualitySummary:
        st_raw = data.get("acceptance_status", AcceptanceSummaryStatus.NOT_VERIFIED.value)
        try:
            acceptance_status = AcceptanceSummaryStatus(str(st_raw).upper())
        except (ValueError, KeyError):
            acceptance_status = AcceptanceSummaryStatus.NOT_VERIFIED

        return cls(
            total_tests=int(data.get("total_tests", 0)),
            passed=int(data.get("passed", 0)),
            failed=int(data.get("failed", 0)),
            blocked=int(data.get("blocked", 0)),
            not_verified=int(data.get("not_verified", 0)),
            critical_defects=int(data.get("critical_defects", 0)),
            high_defects=int(data.get("high_defects", 0)),
            medium_low_defects=int(data.get("medium_low_defects", 0)),
            major_ux_findings=int(data.get("major_ux_findings", 0)),
            performance_findings=int(data.get("performance_findings", 0)),
            acceptance_status=acceptance_status,
            thresholds_met=data.get("thresholds_met"),
            evaluated_against_thresholds=bool(data.get("evaluated_against_thresholds", False)),
            metadata=dict(data.get("metadata", {})),
        )

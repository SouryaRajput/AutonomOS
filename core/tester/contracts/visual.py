from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.finding import TesterDefect, TesterFinding
from core.tester.contracts.identifiers import (
    new_visual_assertion_id,
    validate_execution_id,
    validate_visual_assertion_id,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    DefectSeverity,
    DefectType,
    VisualAssertionStatus,
    VisualCheckType,
)

logger = logging.getLogger("AutonomOS.Tester.VisualContracts")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class VisualAssertion:
    """
    Specification of an explicit visual or geometric check evaluated in Tester V1.
    Defines measurable criteria without subjective aesthetic judgments.
    """
    __test__ = False
    assertion_id: str = field(default_factory=new_visual_assertion_id)
    check_type: VisualCheckType = VisualCheckType.OVERLAP
    target_element_id: str = ""
    reference_element_id: Optional[str] = None
    expected_condition: str = ""
    tolerance_px: float = 0.0
    allow_intentional_overlap: bool = False
    rules: dict[str, Any] = field(default_factory=dict)
    test_case_id: Optional[str] = None
    test_step_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_visual_assertion_id(self.assertion_id)
        if isinstance(self.check_type, str):
            try:
                self.check_type = VisualCheckType(self.check_type.upper())
            except (ValueError, KeyError):
                self.check_type = VisualCheckType.OVERLAP

        if not self.target_element_id or not str(self.target_element_id).strip():
            raise TesterValidationError("VisualAssertion must specify a target_element_id.", field_name="target_element_id")

        if not isinstance(self.tolerance_px, (int, float)) or self.tolerance_px < 0:
            raise TesterValidationError(
                f"VisualAssertion tolerance_px must be non-negative numeric, got {self.tolerance_px}.",
                field_name="tolerance_px",
            )
        self.tolerance_px = float(self.tolerance_px)
        self.rules = dict(self.rules)
        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "check_type": self.check_type.value,
            "target_element_id": self.target_element_id,
            "reference_element_id": self.reference_element_id,
            "expected_condition": self.expected_condition,
            "tolerance_px": self.tolerance_px,
            "allow_intentional_overlap": self.allow_intentional_overlap,
            "rules": dict(self.rules),
            "test_case_id": self.test_case_id,
            "test_step_id": self.test_step_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisualAssertion:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for VisualAssertion, got {type(data).__name__}.")
        raw_type = data.get("check_type", VisualCheckType.OVERLAP.value)
        try:
            check_type = VisualCheckType(str(raw_type).upper())
        except (ValueError, KeyError):
            check_type = VisualCheckType.OVERLAP

        return cls(
            assertion_id=str(data.get("assertion_id") or new_visual_assertion_id()),
            check_type=check_type,
            target_element_id=str(data.get("target_element_id", "")),
            reference_element_id=data.get("reference_element_id"),
            expected_condition=str(data.get("expected_condition", "")),
            tolerance_px=float(data.get("tolerance_px", 0.0)),
            allow_intentional_overlap=bool(data.get("allow_intentional_overlap", False)),
            rules=dict(data.get("rules", {})),
            test_case_id=data.get("test_case_id"),
            test_step_id=data.get("test_step_id"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class VisualAssertionResult:
    """
    Authoritative result of an individual visual assertion evaluation.
    Links measured numeric values, evidence IDs, and any resulting TesterDefect.
    """
    __test__ = False
    assertion_id: str
    check_type: VisualCheckType = VisualCheckType.OVERLAP
    target_element_id: str = ""
    reference_element_id: Optional[str] = None
    status: VisualAssertionStatus = VisualAssertionStatus.PASS
    measured_values: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    defect: Optional[TesterDefect] = None
    test_case_id: Optional[str] = None
    test_step_id: Optional[str] = None
    trace: dict[str, Any] = field(default_factory=dict)
    evaluated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_visual_assertion_id(self.assertion_id)
        if isinstance(self.check_type, str):
            try:
                self.check_type = VisualCheckType(self.check_type.upper())
            except (ValueError, KeyError):
                self.check_type = VisualCheckType.OVERLAP

        if isinstance(self.status, str):
            try:
                self.status = VisualAssertionStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = VisualAssertionStatus.UNVERIFIED

        self.evidence_ids = list(self.evidence_ids)
        self.measured_values = dict(self.measured_values)
        self.trace = dict(self.trace)

    @property
    def is_pass(self) -> bool:
        return self.status == VisualAssertionStatus.PASS

    @property
    def is_fail(self) -> bool:
        return self.status == VisualAssertionStatus.FAIL

    @property
    def is_skipped(self) -> bool:
        return self.status == VisualAssertionStatus.SKIPPED

    @property
    def is_unverified(self) -> bool:
        return self.status == VisualAssertionStatus.UNVERIFIED

    def apply_fix(self, *args, **kwargs) -> Any:
        """Zero fixing guard: Visual assertions do NOT modify source code."""
        raise TesterBoundaryViolationError(
            action="VISUAL_AUTO_FIX",
            reason=(
                f"VisualAssertionResult '{self.assertion_id}' is an evaluation object. "
                "Tester does not perform automatic fixes on product or source code."
            ),
        )

    def auto_fix(self, *args, **kwargs) -> Any:
        return self.apply_fix(*args, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "check_type": self.check_type.value,
            "target_element_id": self.target_element_id,
            "reference_element_id": self.reference_element_id,
            "status": self.status.value,
            "measured_values": dict(self.measured_values),
            "description": self.description,
            "evidence_ids": list(self.evidence_ids),
            "defect": self.defect.to_dict() if self.defect and hasattr(self.defect, "to_dict") else None,
            "test_case_id": self.test_case_id,
            "test_step_id": self.test_step_id,
            "trace": dict(self.trace),
            "evaluated_at": self.evaluated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisualAssertionResult:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for VisualAssertionResult, got {type(data).__name__}.")
        raw_type = data.get("check_type", VisualCheckType.OVERLAP.value)
        try:
            check_type = VisualCheckType(str(raw_type).upper())
        except (ValueError, KeyError):
            check_type = VisualCheckType.OVERLAP

        raw_status = data.get("status", VisualAssertionStatus.PASS.value)
        try:
            status = VisualAssertionStatus(str(raw_status).upper())
        except (ValueError, KeyError):
            status = VisualAssertionStatus.UNVERIFIED

        defect_data = data.get("defect")
        defect_obj = TesterDefect.from_dict(defect_data) if isinstance(defect_data, dict) else None

        return cls(
            assertion_id=str(data.get("assertion_id", "")),
            check_type=check_type,
            target_element_id=str(data.get("target_element_id", "")),
            reference_element_id=data.get("reference_element_id"),
            status=status,
            measured_values=dict(data.get("measured_values", {})),
            description=str(data.get("description", "")),
            evidence_ids=list(data.get("evidence_ids", [])),
            defect=defect_obj,
            test_case_id=data.get("test_case_id"),
            test_step_id=data.get("test_step_id"),
            trace=dict(data.get("trace", {})),
            evaluated_at=str(data.get("evaluated_at", utc_now())),
        )


@dataclass
class VisualEvaluationResult:
    """
    Consolidated outcome of deterministic visual and geometry evaluation.
    Holds all evaluated assertions, generated defects, findings, and evidence bindings.
    """
    __test__ = False
    execution_id: str
    project_id: str
    work_order_id: Optional[str] = None
    status: VisualAssertionStatus = VisualAssertionStatus.PASS
    defects: list[TesterDefect] = field(default_factory=list)
    findings: list[TesterFinding] = field(default_factory=list)
    assertion_results: list[VisualAssertionResult] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_execution_id(self.execution_id)
        if not self.project_id or not str(self.project_id).strip():
            raise TesterLineageError("VisualEvaluationResult requires a non-empty project_id.")

        if isinstance(self.status, str):
            try:
                self.status = VisualAssertionStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = VisualAssertionStatus.PASS

        self.defects = list(self.defects)
        self.findings = list(self.findings)
        self.assertion_results = list(self.assertion_results)
        self.evidence_ids = list(self.evidence_ids)
        self.metadata = dict(self.metadata)

        # Aggregate evidence IDs from assertion results and defects
        ev_set = set(self.evidence_ids)
        for ar in self.assertion_results:
            ev_set.update(ar.evidence_ids)
            if ar.defect:
                ev_set.update(ar.defect.evidence_ids)
        for d in self.defects:
            ev_set.update(d.evidence_ids)
        self.evidence_ids = sorted(list(ev_set))

        # Recompute overall status if defects exist
        if self.defects or any(ar.is_fail for ar in self.assertion_results):
            self.status = VisualAssertionStatus.FAIL

    @property
    def total_checks(self) -> int:
        return len(self.assertion_results)

    @property
    def passed_checks(self) -> int:
        return sum(1 for ar in self.assertion_results if ar.is_pass)

    @property
    def failed_checks(self) -> int:
        return sum(1 for ar in self.assertion_results if ar.is_fail)

    @property
    def skipped_checks(self) -> int:
        return sum(1 for ar in self.assertion_results if ar.is_skipped)

    @property
    def unverified_checks(self) -> int:
        return sum(1 for ar in self.assertion_results if ar.is_unverified)

    @property
    def total_defects(self) -> int:
        return len(self.defects)

    @property
    def critical_defects(self) -> int:
        return sum(1 for d in self.defects if d.severity == DefectSeverity.CRITICAL)

    @property
    def high_defects(self) -> int:
        return sum(1 for d in self.defects if d.severity == DefectSeverity.HIGH)

    @property
    def medium_defects(self) -> int:
        return sum(1 for d in self.defects if d.severity == DefectSeverity.MEDIUM)

    @property
    def low_defects(self) -> int:
        return sum(1 for d in self.defects if d.severity == DefectSeverity.LOW)

    @property
    def is_pass(self) -> bool:
        return self.status == VisualAssertionStatus.PASS and len(self.defects) == 0

    def apply_fix(self, *args, **kwargs) -> Any:
        """Zero fixing guard: Visual evaluation does not perform automatic fixes."""
        raise TesterBoundaryViolationError(
            action="VISUAL_AUTO_FIX",
            reason=(
                f"VisualEvaluationResult for execution '{self.execution_id}' is an evaluation object. "
                "Tester does not perform automatic fixes on product or source code."
            ),
        )

    def auto_fix(self, *args, **kwargs) -> Any:
        return self.apply_fix(*args, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "project_id": self.project_id,
            "work_order_id": self.work_order_id,
            "status": self.status.value,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "skipped_checks": self.skipped_checks,
            "unverified_checks": self.unverified_checks,
            "total_defects": self.total_defects,
            "defects": [d.to_dict() if hasattr(d, "to_dict") else d for d in self.defects],
            "findings": [f.to_dict() if hasattr(f, "to_dict") else f for f in self.findings],
            "assertion_results": [ar.to_dict() if hasattr(ar, "to_dict") else ar for ar in self.assertion_results],
            "evidence_ids": list(self.evidence_ids),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisualEvaluationResult:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for VisualEvaluationResult, got {type(data).__name__}.")

        raw_status = data.get("status", VisualAssertionStatus.PASS.value)
        try:
            status = VisualAssertionStatus(str(raw_status).upper())
        except (ValueError, KeyError):
            status = VisualAssertionStatus.PASS

        raw_defects = data.get("defects", [])
        defects = [TesterDefect.from_dict(d) if isinstance(d, dict) else d for d in raw_defects]

        raw_findings = data.get("findings", [])
        findings = [TesterFinding.from_dict(f) if isinstance(f, dict) else f for f in raw_findings]

        raw_ars = data.get("assertion_results", [])
        assertion_results = [VisualAssertionResult.from_dict(ar) if isinstance(ar, dict) else ar for ar in raw_ars]

        return cls(
            execution_id=str(data.get("execution_id", "")),
            project_id=str(data.get("project_id", "")),
            work_order_id=data.get("work_order_id"),
            status=status,
            defects=defects,
            findings=findings,
            assertion_results=assertion_results,
            evidence_ids=list(data.get("evidence_ids", [])),
            metadata=dict(data.get("metadata", {})),
            created_at=str(data.get("created_at", utc_now())),
        )

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.finding import TesterDefect, TesterFinding
from core.tester.contracts.identifiers import (
    new_ux_evaluation_id,
    validate_ux_evaluation_id,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterValidationError,
)
from core.tester.types import (
    UXCheckType,
    UXEvaluationStatus,
)

logger = logging.getLogger("AutonomOS.Tester.UXContracts")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# UX Flow Expectation Model
# ---------------------------------------------------------------------------

@dataclass
class UXFlowExpectation:
    """
    Explicit usability expectation for a specific task or workflow.
    """
    flow_name: str = ""
    objective: str = ""
    step_name: str = ""
    expected_outcome: str = ""
    actual_outcome: str = ""
    is_completed: bool = True
    blocker_reason: Optional[str] = None
    required_actions: list[str] = field(default_factory=list)
    required_controls: list[str] = field(default_factory=list)
    expected_feedback: Optional[str] = None
    can_recover_from_error: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.required_actions = list(self.required_actions)
        self.required_controls = list(self.required_controls)
        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "flow_name": self.flow_name,
            "objective": self.objective,
            "step_name": self.step_name,
            "expected_outcome": self.expected_outcome,
            "actual_outcome": self.actual_outcome,
            "is_completed": self.is_completed,
            "blocker_reason": self.blocker_reason,
            "required_actions": list(self.required_actions),
            "required_controls": list(self.required_controls),
            "expected_feedback": self.expected_feedback,
            "can_recover_from_error": self.can_recover_from_error,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UXFlowExpectation:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for UXFlowExpectation, got {type(data).__name__}.")
        return cls(
            flow_name=str(data.get("flow_name", "")),
            objective=str(data.get("objective", "")),
            step_name=str(data.get("step_name", "")),
            expected_outcome=str(data.get("expected_outcome", "")),
            actual_outcome=str(data.get("actual_outcome", "")),
            is_completed=bool(data.get("is_completed", True)),
            blocker_reason=data.get("blocker_reason"),
            required_actions=list(data.get("required_actions", [])),
            required_controls=list(data.get("required_controls", [])),
            expected_feedback=data.get("expected_feedback"),
            can_recover_from_error=bool(data.get("can_recover_from_error", True)),
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# UX Assertion Model
# ---------------------------------------------------------------------------

@dataclass
class UXAssertion:
    """
    Specification of a discrete UX flow or usability check.
    """
    __test__ = False
    assertion_id: str = field(default_factory=new_ux_evaluation_id)
    check_type: UXCheckType = UXCheckType.REQUIRED_ACTION_BLOCKED
    target_flow: str = ""
    target_element_id: Optional[str] = None
    expectation: UXFlowExpectation = field(default_factory=UXFlowExpectation)
    test_case_id: Optional[str] = None
    execution_id: Optional[str] = None
    step_id: Optional[str] = None
    flow_name: str = ""
    description: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    actionable_suggestion: Optional[str] = None
    is_subjective_preference: bool = False
    is_vague_critique: bool = False
    expectations: list[UXFlowExpectation] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_ux_evaluation_id(self.assertion_id)
        if isinstance(self.check_type, str):
            try:
                self.check_type = UXCheckType(self.check_type.upper())
            except (ValueError, KeyError):
                self.check_type = UXCheckType.REQUIRED_ACTION_BLOCKED

        if not self.target_flow and self.flow_name:
            self.target_flow = self.flow_name
        elif not self.flow_name and self.target_flow:
            self.flow_name = self.target_flow

        if isinstance(self.expectation, dict):
            self.expectation = UXFlowExpectation.from_dict(self.expectation)

        cleaned_exps = []
        for exp in self.expectations:
            if isinstance(exp, dict):
                cleaned_exps.append(UXFlowExpectation.from_dict(exp))
            elif isinstance(exp, UXFlowExpectation):
                cleaned_exps.append(exp)
        self.expectations = cleaned_exps

        self.evidence_ids = list(self.evidence_ids)
        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "check_type": self.check_type.value,
            "target_flow": self.target_flow,
            "target_element_id": self.target_element_id,
            "expectation": self.expectation.to_dict(),
            "test_case_id": self.test_case_id,
            "execution_id": self.execution_id,
            "step_id": self.step_id,
            "flow_name": self.flow_name,
            "description": self.description,
            "evidence_ids": list(self.evidence_ids),
            "actionable_suggestion": self.actionable_suggestion,
            "is_subjective_preference": self.is_subjective_preference,
            "is_vague_critique": self.is_vague_critique,
            "expectations": [e.to_dict() for e in self.expectations],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UXAssertion:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for UXAssertion, got {type(data).__name__}.")

        raw_type = data.get("check_type", UXCheckType.REQUIRED_ACTION_BLOCKED.value)
        try:
            check_type = UXCheckType(str(raw_type).upper())
        except (ValueError, KeyError):
            check_type = UXCheckType.REQUIRED_ACTION_BLOCKED

        raw_exp = data.get("expectation", {})
        exp = UXFlowExpectation.from_dict(raw_exp) if isinstance(raw_exp, dict) else UXFlowExpectation()

        return cls(
            assertion_id=str(data.get("assertion_id") or new_ux_evaluation_id()),
            check_type=check_type,
            target_flow=str(data.get("target_flow", "")),
            target_element_id=data.get("target_element_id"),
            expectation=exp,
            test_case_id=data.get("test_case_id"),
            execution_id=data.get("execution_id"),
            step_id=data.get("step_id"),
            flow_name=str(data.get("flow_name", "")),
            description=str(data.get("description", "")),
            evidence_ids=list(data.get("evidence_ids", [])),
            actionable_suggestion=data.get("actionable_suggestion"),
            is_subjective_preference=bool(data.get("is_subjective_preference", False)),
            is_vague_critique=bool(data.get("is_vague_critique", False)),
            expectations=[UXFlowExpectation.from_dict(e) if isinstance(e, dict) else e for e in data.get("expectations", [])],
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# UX Assertion Result
# ---------------------------------------------------------------------------

@dataclass
class UXAssertionResult:
    """
    Outcome of an evaluated UX check.
    """
    __test__ = False
    assertion_id: str
    check_type: UXCheckType
    status: UXEvaluationStatus = UXEvaluationStatus.PASS
    target_flow: str = ""
    target_element_id: Optional[str] = None
    description: str = ""
    defect: Optional[TesterDefect] = None
    recommendation: Optional[TesterFinding] = None
    evidence_ids: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    test_case_id: Optional[str] = None
    evaluated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = UXEvaluationStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = UXEvaluationStatus.NOT_VERIFIED

        if isinstance(self.check_type, str):
            try:
                self.check_type = UXCheckType(self.check_type.upper())
            except (ValueError, KeyError):
                self.check_type = UXCheckType.REQUIRED_ACTION_BLOCKED

        self.evidence_ids = list(self.evidence_ids)
        self.trace = dict(self.trace)

    @property
    def is_pass(self) -> bool:
        return self.status == UXEvaluationStatus.PASS

    @property
    def is_fail(self) -> bool:
        return self.status == UXEvaluationStatus.FAIL

    @property
    def is_blocked(self) -> bool:
        return self.status == UXEvaluationStatus.BLOCKED

    @property
    def is_not_verified(self) -> bool:
        return self.status == UXEvaluationStatus.NOT_VERIFIED

    @property
    def is_skipped(self) -> bool:
        return self.status == UXEvaluationStatus.SKIPPED

    @property
    def is_discarded(self) -> bool:
        return self.status == UXEvaluationStatus.DISCARDED

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "check_type": self.check_type.value,
            "status": self.status.value,
            "target_flow": self.target_flow,
            "target_element_id": self.target_element_id,
            "description": self.description,
            "defect": self.defect.to_dict() if self.defect and hasattr(self.defect, "to_dict") else None,
            "recommendation": self.recommendation.to_dict() if self.recommendation and hasattr(self.recommendation, "to_dict") else None,
            "evidence_ids": list(self.evidence_ids),
            "trace": dict(self.trace),
            "test_case_id": self.test_case_id,
            "evaluated_at": self.evaluated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UXAssertionResult:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for UXAssertionResult, got {type(data).__name__}.")

        raw_status = data.get("status", UXEvaluationStatus.PASS.value)
        try:
            status = UXEvaluationStatus(str(raw_status).upper())
        except (ValueError, KeyError):
            status = UXEvaluationStatus.NOT_VERIFIED

        raw_type = data.get("check_type", UXCheckType.REQUIRED_ACTION_BLOCKED.value)
        try:
            check_type = UXCheckType(str(raw_type).upper())
        except (ValueError, KeyError):
            check_type = UXCheckType.REQUIRED_ACTION_BLOCKED

        defect_data = data.get("defect")
        defect_obj = TesterDefect.from_dict(defect_data) if isinstance(defect_data, dict) else None

        rec_data = data.get("recommendation")
        rec_obj = TesterFinding.from_dict(rec_data) if isinstance(rec_data, dict) else None

        return cls(
            assertion_id=str(data.get("assertion_id", "")),
            check_type=check_type,
            status=status,
            target_flow=str(data.get("target_flow", "")),
            target_element_id=data.get("target_element_id"),
            description=str(data.get("description", "")),
            defect=defect_obj,
            recommendation=rec_obj,
            evidence_ids=list(data.get("evidence_ids", [])),
            trace=dict(data.get("trace", {})),
            test_case_id=data.get("test_case_id"),
            evaluated_at=str(data.get("evaluated_at", utc_now())),
        )


# ---------------------------------------------------------------------------
# UX Evaluation Aggregate Result
# ---------------------------------------------------------------------------

@dataclass
class UXEvaluationResult:
    """
    Authoritative aggregate outcome of UX flow and usability evaluations.
    """
    __test__ = False
    evaluation_id: str
    execution_id: Optional[str] = None
    status: UXEvaluationStatus = UXEvaluationStatus.PASS
    assertion_results: list[UXAssertionResult] = field(default_factory=list)
    defects: list[TesterDefect] = field(default_factory=list)
    findings: list[TesterFinding] = field(default_factory=list)
    recommendations: list[TesterFinding] = field(default_factory=list)
    discarded_count: int = 0
    evidence_ids: list[str] = field(default_factory=list)
    test_case_id: Optional[str] = None
    evaluated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_ux_evaluation_id(self.evaluation_id)
        if isinstance(self.status, str):
            try:
                self.status = UXEvaluationStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = UXEvaluationStatus.NOT_VERIFIED

        self.assertion_results = list(self.assertion_results)
        self.defects = list(self.defects)
        self.findings = list(self.findings)
        self.recommendations = list(self.recommendations)
        self.evidence_ids = list(self.evidence_ids)
        self.metadata = dict(self.metadata)

    @property
    def overall_status(self) -> UXEvaluationStatus:
        return self.status

    @property
    def is_pass(self) -> bool:
        return self.status == UXEvaluationStatus.PASS

    @property
    def is_fail(self) -> bool:
        return self.status == UXEvaluationStatus.FAIL

    @property
    def is_blocked(self) -> bool:
        return self.status == UXEvaluationStatus.BLOCKED

    @property
    def is_not_verified(self) -> bool:
        return self.status == UXEvaluationStatus.NOT_VERIFIED

    def apply_fix(self, *args, **kwargs) -> Any:
        """Zero fixing guard: UX evaluation does NOT modify source code or attempt auto repairs."""
        raise TesterBoundaryViolationError(
            action="UX_AUTO_FIX",
            reason=(
                f"UXEvaluationResult '{self.evaluation_id}' is an evaluation object. "
                "Tester does not perform automatic fixes on product or source code."
            ),
        )

    def auto_fix(self, *args, **kwargs) -> Any:
        return self.apply_fix(*args, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "execution_id": self.execution_id,
            "status": self.status.value,
            "assertion_results": [ar.to_dict() if hasattr(ar, "to_dict") else ar for ar in self.assertion_results],
            "defects": [d.to_dict() if hasattr(d, "to_dict") else d for d in self.defects],
            "findings": [f.to_dict() if hasattr(f, "to_dict") else f for f in self.findings],
            "recommendations": [r.to_dict() if hasattr(r, "to_dict") else r for r in self.recommendations],
            "discarded_count": int(self.discarded_count),
            "evidence_ids": list(self.evidence_ids),
            "test_case_id": self.test_case_id,
            "evaluated_at": self.evaluated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UXEvaluationResult:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for UXEvaluationResult, got {type(data).__name__}.")

        raw_status = data.get("status", UXEvaluationStatus.PASS.value)
        try:
            status = UXEvaluationStatus(str(raw_status).upper())
        except (ValueError, KeyError):
            status = UXEvaluationStatus.NOT_VERIFIED

        assertions = []
        for ar in data.get("assertion_results", []):
            if isinstance(ar, dict):
                assertions.append(UXAssertionResult.from_dict(ar))
            elif isinstance(ar, UXAssertionResult):
                assertions.append(ar)

        defects = []
        for d in data.get("defects", []):
            if isinstance(d, dict):
                defects.append(TesterDefect.from_dict(d))
            elif isinstance(d, TesterDefect):
                defects.append(d)

        findings = []
        for f in data.get("findings", []):
            if isinstance(f, dict):
                findings.append(TesterFinding.from_dict(f))
            elif isinstance(f, TesterFinding):
                findings.append(f)

        recs = []
        for r in data.get("recommendations", []):
            if isinstance(r, dict):
                recs.append(TesterFinding.from_dict(r))
            elif isinstance(r, TesterFinding):
                recs.append(r)

        return cls(
            evaluation_id=str(data.get("evaluation_id", "")),
            execution_id=data.get("execution_id"),
            status=status,
            assertion_results=assertions,
            defects=defects,
            findings=findings,
            recommendations=recs,
            discarded_count=int(data.get("discarded_count", 0)),
            evidence_ids=list(data.get("evidence_ids", [])),
            test_case_id=data.get("test_case_id"),
            evaluated_at=str(data.get("evaluated_at", utc_now())),
            metadata=dict(data.get("metadata", {})),
        )

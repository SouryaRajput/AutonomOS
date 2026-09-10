from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.finding import TesterDefect, TesterFinding
from core.tester.contracts.identifiers import (
    new_animation_evaluation_id,
    validate_animation_evaluation_id,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterValidationError,
)
from core.tester.types import (
    AnimationCheckType,
    AnimationEvaluationStatus,
)

logger = logging.getLogger("AutonomOS.Tester.AnimationContracts")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Animation State Expectation
# ---------------------------------------------------------------------------

@dataclass
class AnimationStateExpectation:
    """
    Explicit state expectation for an animation or transition.
    """
    initial_state: Optional[str] = None
    expected_final_state: str = ""
    target_element_id: str = ""
    expected_duration_ms: Optional[float] = None
    max_duration_ms: Optional[float] = None
    require_intermediate_progress: bool = True
    expected_properties: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.expected_properties = dict(self.expected_properties)
        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_state": self.initial_state,
            "expected_final_state": self.expected_final_state,
            "target_element_id": self.target_element_id,
            "expected_duration_ms": self.expected_duration_ms,
            "max_duration_ms": self.max_duration_ms,
            "require_intermediate_progress": self.require_intermediate_progress,
            "expected_properties": dict(self.expected_properties),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnimationStateExpectation:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for AnimationStateExpectation, got {type(data).__name__}.")

        return cls(
            initial_state=data.get("initial_state"),
            expected_final_state=str(data.get("expected_final_state", "")),
            target_element_id=str(data.get("target_element_id", "")),
            expected_duration_ms=float(data["expected_duration_ms"]) if data.get("expected_duration_ms") is not None else None,
            max_duration_ms=float(data["max_duration_ms"]) if data.get("max_duration_ms") is not None else None,
            require_intermediate_progress=bool(data.get("require_intermediate_progress", True)),
            expected_properties=dict(data.get("expected_properties", {})),
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# Animation Assertion
# ---------------------------------------------------------------------------

@dataclass
class AnimationAssertion:
    """
    Specification of a single animation or transition check evaluated in Tester V1.
    """
    __test__ = False
    assertion_id: str = field(default_factory=new_animation_evaluation_id)
    check_type: AnimationCheckType = AnimationCheckType.TRANSITION_COMPLETION
    target_element_id: str = ""
    expectation: AnimationStateExpectation = field(default_factory=AnimationStateExpectation)
    test_case_id: Optional[str] = None
    test_step_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_animation_evaluation_id(self.assertion_id)
        if isinstance(self.check_type, str):
            try:
                self.check_type = AnimationCheckType(self.check_type.upper())
            except (ValueError, KeyError):
                self.check_type = AnimationCheckType.TRANSITION_COMPLETION

        if isinstance(self.expectation, dict):
            self.expectation = AnimationStateExpectation.from_dict(self.expectation)

        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "check_type": self.check_type.value,
            "target_element_id": self.target_element_id,
            "expectation": self.expectation.to_dict(),
            "test_case_id": self.test_case_id,
            "test_step_id": self.test_step_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnimationAssertion:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for AnimationAssertion, got {type(data).__name__}.")

        raw_type = data.get("check_type", AnimationCheckType.TRANSITION_COMPLETION.value)
        try:
            check_type = AnimationCheckType(str(raw_type).upper())
        except (ValueError, KeyError):
            check_type = AnimationCheckType.TRANSITION_COMPLETION

        raw_exp = data.get("expectation", {})
        exp = AnimationStateExpectation.from_dict(raw_exp) if isinstance(raw_exp, dict) else AnimationStateExpectation()

        return cls(
            assertion_id=str(data.get("assertion_id") or new_animation_evaluation_id()),
            check_type=check_type,
            target_element_id=str(data.get("target_element_id", "")),
            expectation=exp,
            test_case_id=data.get("test_case_id"),
            test_step_id=data.get("test_step_id"),
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# Animation Assertion Result
# ---------------------------------------------------------------------------

@dataclass
class AnimationAssertionResult:
    """
    Authoritative outcome of an animation or transition check.
    """
    __test__ = False
    assertion_id: str
    check_type: AnimationCheckType
    status: AnimationEvaluationStatus = AnimationEvaluationStatus.PASS
    target_element_id: str = ""
    initial_state_observed: Optional[str] = None
    final_state_observed: Optional[str] = None
    transition_completed: bool = False
    frames_evaluated_count: int = 0
    stuck_detected: bool = False
    description: str = ""
    defect: Optional[TesterDefect] = None
    recommendation: Optional[TesterFinding] = None
    evidence_ids: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    test_case_id: Optional[str] = None
    test_step_id: Optional[str] = None
    evaluated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = AnimationEvaluationStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = AnimationEvaluationStatus.NOT_VERIFIED

        if isinstance(self.check_type, str):
            try:
                self.check_type = AnimationCheckType(self.check_type.upper())
            except (ValueError, KeyError):
                self.check_type = AnimationCheckType.TRANSITION_COMPLETION

        self.evidence_ids = list(self.evidence_ids)
        self.trace = dict(self.trace)

    @property
    def is_pass(self) -> bool:
        return self.status == AnimationEvaluationStatus.PASS

    @property
    def is_fail(self) -> bool:
        return self.status == AnimationEvaluationStatus.FAIL

    @property
    def is_blocked(self) -> bool:
        return self.status == AnimationEvaluationStatus.BLOCKED

    @property
    def is_not_verified(self) -> bool:
        return self.status == AnimationEvaluationStatus.NOT_VERIFIED

    @property
    def is_skipped(self) -> bool:
        return self.status == AnimationEvaluationStatus.SKIPPED

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "check_type": self.check_type.value,
            "status": self.status.value,
            "target_element_id": self.target_element_id,
            "initial_state_observed": self.initial_state_observed,
            "final_state_observed": self.final_state_observed,
            "transition_completed": self.transition_completed,
            "frames_evaluated_count": int(self.frames_evaluated_count),
            "stuck_detected": self.stuck_detected,
            "description": self.description,
            "defect": self.defect.to_dict() if self.defect and hasattr(self.defect, "to_dict") else None,
            "recommendation": self.recommendation.to_dict() if self.recommendation and hasattr(self.recommendation, "to_dict") else None,
            "evidence_ids": list(self.evidence_ids),
            "trace": dict(self.trace),
            "test_case_id": self.test_case_id,
            "test_step_id": self.test_step_id,
            "evaluated_at": self.evaluated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnimationAssertionResult:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for AnimationAssertionResult, got {type(data).__name__}.")

        raw_status = data.get("status", AnimationEvaluationStatus.PASS.value)
        try:
            status = AnimationEvaluationStatus(str(raw_status).upper())
        except (ValueError, KeyError):
            status = AnimationEvaluationStatus.NOT_VERIFIED

        raw_type = data.get("check_type", AnimationCheckType.TRANSITION_COMPLETION.value)
        try:
            check_type = AnimationCheckType(str(raw_type).upper())
        except (ValueError, KeyError):
            check_type = AnimationCheckType.TRANSITION_COMPLETION

        defect_data = data.get("defect")
        defect_obj = TesterDefect.from_dict(defect_data) if isinstance(defect_data, dict) else None

        rec_data = data.get("recommendation")
        rec_obj = TesterFinding.from_dict(rec_data) if isinstance(rec_data, dict) else None

        return cls(
            assertion_id=str(data.get("assertion_id", "")),
            check_type=check_type,
            status=status,
            target_element_id=str(data.get("target_element_id", "")),
            initial_state_observed=data.get("initial_state_observed"),
            final_state_observed=data.get("final_state_observed"),
            transition_completed=bool(data.get("transition_completed", False)),
            frames_evaluated_count=int(data.get("frames_evaluated_count", 0)),
            stuck_detected=bool(data.get("stuck_detected", False)),
            description=str(data.get("description", "")),
            defect=defect_obj,
            recommendation=rec_obj,
            evidence_ids=list(data.get("evidence_ids", [])),
            trace=dict(data.get("trace", {})),
            test_case_id=data.get("test_case_id"),
            test_step_id=data.get("test_step_id"),
            evaluated_at=str(data.get("evaluated_at", utc_now())),
        )


# ---------------------------------------------------------------------------
# Animation Evaluation Aggregate Result
# ---------------------------------------------------------------------------

@dataclass
class AnimationEvaluationResult:
    """
    Authoritative aggregate outcome of animation and transition evaluations.
    """
    __test__ = False
    evaluation_id: str
    status: AnimationEvaluationStatus = AnimationEvaluationStatus.PASS
    assertion_results: list[AnimationAssertionResult] = field(default_factory=list)
    defects: list[TesterDefect] = field(default_factory=list)
    findings: list[TesterFinding] = field(default_factory=list)
    total_frames_evaluated: int = 0
    evidence_ids: list[str] = field(default_factory=list)
    test_case_id: Optional[str] = None
    test_step_id: Optional[str] = None
    evaluated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_animation_evaluation_id(self.evaluation_id)
        if isinstance(self.status, str):
            try:
                self.status = AnimationEvaluationStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = AnimationEvaluationStatus.NOT_VERIFIED

        self.assertion_results = list(self.assertion_results)
        self.defects = list(self.defects)
        self.findings = list(self.findings)
        self.evidence_ids = list(self.evidence_ids)
        self.metadata = dict(self.metadata)

    @property
    def is_pass(self) -> bool:
        return self.status == AnimationEvaluationStatus.PASS

    @property
    def is_fail(self) -> bool:
        return self.status == AnimationEvaluationStatus.FAIL

    @property
    def is_blocked(self) -> bool:
        return self.status == AnimationEvaluationStatus.BLOCKED

    @property
    def is_not_verified(self) -> bool:
        return self.status == AnimationEvaluationStatus.NOT_VERIFIED

    @property
    def is_skipped(self) -> bool:
        return self.status == AnimationEvaluationStatus.SKIPPED

    def apply_fix(self, *args, **kwargs) -> Any:
        """Zero fixing guard: Animation evaluation does NOT modify source code or attempt auto repairs."""
        raise TesterBoundaryViolationError(
            action="ANIMATION_AUTO_FIX",
            reason=(
                f"AnimationEvaluationResult '{self.evaluation_id}' is an evaluation object. "
                "Tester does not perform automatic fixes on product or source code."
            ),
        )

    def auto_fix(self, *args, **kwargs) -> Any:
        return self.apply_fix(*args, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "status": self.status.value,
            "assertion_results": [ar.to_dict() if hasattr(ar, "to_dict") else ar for ar in self.assertion_results],
            "defects": [d.to_dict() if hasattr(d, "to_dict") else d for d in self.defects],
            "findings": [f.to_dict() if hasattr(f, "to_dict") else f for f in self.findings],
            "total_frames_evaluated": int(self.total_frames_evaluated),
            "evidence_ids": list(self.evidence_ids),
            "test_case_id": self.test_case_id,
            "test_step_id": self.test_step_id,
            "evaluated_at": self.evaluated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnimationEvaluationResult:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for AnimationEvaluationResult, got {type(data).__name__}.")

        raw_status = data.get("status", AnimationEvaluationStatus.PASS.value)
        try:
            status = AnimationEvaluationStatus(str(raw_status).upper())
        except (ValueError, KeyError):
            status = AnimationEvaluationStatus.NOT_VERIFIED

        assertions = []
        for ar in data.get("assertion_results", []):
            if isinstance(ar, dict):
                assertions.append(AnimationAssertionResult.from_dict(ar))
            elif isinstance(ar, AnimationAssertionResult):
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

        return cls(
            evaluation_id=str(data.get("evaluation_id", "")),
            status=status,
            assertion_results=assertions,
            defects=defects,
            findings=findings,
            total_frames_evaluated=int(data.get("total_frames_evaluated", 0)),
            evidence_ids=list(data.get("evidence_ids", [])),
            test_case_id=data.get("test_case_id"),
            test_step_id=data.get("test_step_id"),
            evaluated_at=str(data.get("evaluated_at", utc_now())),
            metadata=dict(data.get("metadata", {})),
        )

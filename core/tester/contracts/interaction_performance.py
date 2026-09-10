from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.identifiers import (
    new_interaction_performance_id,
    validate_evidence_id,
    validate_execution_id,
    validate_interaction_performance_id,
    validate_test_case_id,
    validate_trace_id,
)
from core.tester.contracts.interaction import InteractionTarget, normalize_target
from core.tester.contracts.performance import PerformanceMeasurement
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    InteractionPerformanceStatus,
    PerformanceMetricType,
    TesterActionType,
)

logger = logging.getLogger("AutonomOS.Tester.InteractionPerformanceContracts")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Interaction Performance Specification
# ---------------------------------------------------------------------------

@dataclass
class InteractionPerformanceSpec:
    """
    Specification for measuring user interaction responsiveness already defined in the frozen TestPlan.
    Measures from USER ACTION initiation to resulting OBSERVABLE RESPONSE without treating raw click
    duration as the complete UX response time.
    """
    __test__ = False
    target: Any
    action_type: TesterActionType = TesterActionType.CLICK
    expected_state: Optional[str] = None
    threshold_ms: Optional[float] = None
    timeout_seconds: float = 10.0
    repeat_count: int = 1
    test_case_id: Optional[str] = None
    execution_id: Optional[str] = None
    evidence_id: Optional[str] = None
    trace_id: Optional[str] = None
    action_payload: Optional[dict[str, Any]] = None
    spec_id: str = field(default_factory=new_interaction_performance_id)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.action_type, str):
            try:
                self.action_type = TesterActionType(self.action_type.upper())
            except ValueError:
                self.action_type = TesterActionType.CLICK

        if self.threshold_ms is not None:
            self.threshold_ms = float(self.threshold_ms)
            if self.threshold_ms <= 0:
                raise TesterValidationError(
                    f"threshold_ms must be strictly positive if specified, got {self.threshold_ms}.",
                    field_name="threshold_ms",
                )

        if self.timeout_seconds <= 0:
            self.timeout_seconds = 10.0

        if self.repeat_count < 1:
            self.repeat_count = 1

        if self.test_case_id:
            validate_test_case_id(self.test_case_id)

        if self.execution_id:
            validate_execution_id(self.execution_id)

        if self.evidence_id:
            validate_evidence_id(self.evidence_id)

        if self.trace_id:
            validate_trace_id(self.trace_id)

        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        target_repr: Any
        if isinstance(self.target, InteractionTarget):
            target_repr = self.target.to_dict()
        elif isinstance(self.target, (str, dict, list, tuple)):
            target_repr = self.target
        else:
            target_repr = str(self.target)

        return {
            "spec_id": self.spec_id,
            "target": target_repr,
            "action_type": self.action_type.value,
            "expected_state": self.expected_state,
            "threshold_ms": self.threshold_ms,
            "timeout_seconds": self.timeout_seconds,
            "repeat_count": self.repeat_count,
            "test_case_id": self.test_case_id,
            "execution_id": self.execution_id,
            "evidence_id": self.evidence_id,
            "trace_id": self.trace_id,
            "action_payload": dict(self.action_payload or {}),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InteractionPerformanceSpec:
        raw_action = data.get("action_type", TesterActionType.CLICK.value)
        try:
            action_type = TesterActionType(str(raw_action).upper())
        except ValueError:
            action_type = TesterActionType.CLICK

        raw_thresh = data.get("threshold_ms")
        thresh = float(raw_thresh) if raw_thresh is not None else None

        return cls(
            target=data.get("target"),
            action_type=action_type,
            expected_state=data.get("expected_state"),
            threshold_ms=thresh,
            timeout_seconds=float(data.get("timeout_seconds", 10.0)),
            repeat_count=int(data.get("repeat_count", 1)),
            test_case_id=data.get("test_case_id"),
            execution_id=data.get("execution_id"),
            evidence_id=data.get("evidence_id"),
            trace_id=data.get("trace_id"),
            action_payload=dict(data.get("action_payload", {})),
            spec_id=str(data.get("spec_id", new_interaction_performance_id())),
            metadata=dict(data.get("metadata", {})),
        )

    # -----------------------------------------------------------------------
    # Invariant Guards: Zero Code Modifications & Zero Optimization
    # -----------------------------------------------------------------------

    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Interaction performance spec cannot modify application code or apply fixes."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Interaction performance spec cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Interaction performance spec cannot modify application code or apply fixes."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Interaction performance spec cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Interaction performance spec does not classify defects in Phase 7.3."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_DEFECT_CLASSIFICATION",
            reason="Interaction performance spec does not classify defects in Phase 7.3.",
        )


# ---------------------------------------------------------------------------
# Interaction Performance Result
# ---------------------------------------------------------------------------

@dataclass
class InteractionPerformanceResult:
    """
    Authoritative outcome of an interaction responsiveness performance measurement.
    Accurately captures action start, action completion, resulting state readiness,
    total USER ACTION -> OBSERVABLE RESPONSE duration, and explicit threshold evaluation.
    """
    __test__ = False
    interaction_performance_id: str
    execution_id: str
    spec: InteractionPerformanceSpec
    status: InteractionPerformanceStatus = InteractionPerformanceStatus.SUCCESS
    measurements: list[PerformanceMeasurement] = field(default_factory=list)
    action_started_at: Optional[str] = None
    action_completed_at: Optional[str] = None
    state_ready_at: Optional[str] = None
    action_execution_duration_ms: Optional[float] = None
    state_readiness_duration_ms: Optional[float] = None
    interaction_duration_ms: Optional[float] = None
    threshold_ms: Optional[float] = None
    threshold_met: Optional[bool] = None
    evidence_id: Optional[str] = None
    trace_id: Optional[str] = None
    error_message: Optional[str] = None
    sample_count: int = 1
    provenance: dict[str, Any] = field(default_factory=dict)
    timestamps: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        validate_interaction_performance_id(self.interaction_performance_id)
        validate_execution_id(self.execution_id)

        if self.spec.execution_id and self.spec.execution_id != self.execution_id:
            raise TesterLineageError(
                f"InteractionPerformanceResult execution_id '{self.execution_id}' does not match spec execution_id '{self.spec.execution_id}'."
            )

        if isinstance(self.status, str):
            try:
                self.status = InteractionPerformanceStatus(self.status.upper())
            except ValueError:
                self.status = InteractionPerformanceStatus.SUCCESS

        if not self.timestamps:
            self.timestamps = {"created_at": utc_now()}

        self.measurements = list(self.measurements)
        self.provenance = dict(self.provenance)
        self.metadata = dict(self.metadata)

        # Synchronize threshold from spec if not explicitly set
        if self.threshold_ms is None and self.spec.threshold_ms is not None:
            self.threshold_ms = self.spec.threshold_ms

        # Compute threshold_met strictly if threshold_ms is provided
        if self.threshold_ms is not None and self.threshold_met is None:
            primary_duration = (
                self.interaction_duration_ms
                or self.state_readiness_duration_ms
                or self.action_execution_duration_ms
            )
            if primary_duration is not None:
                self.threshold_met = primary_duration <= self.threshold_ms

        if self.evidence_id:
            validate_evidence_id(self.evidence_id)

        if self.trace_id:
            validate_trace_id(self.trace_id)

    @property
    def is_success(self) -> bool:
        return self.status == InteractionPerformanceStatus.SUCCESS

    @property
    def is_timeout(self) -> bool:
        return self.status == InteractionPerformanceStatus.TIMEOUT

    @property
    def is_missing_transition(self) -> bool:
        return self.status == InteractionPerformanceStatus.MISSING_STATE_TRANSITION

    @property
    def is_failed(self) -> bool:
        return self.status in (
            InteractionPerformanceStatus.INTERACTION_FAILED,
            InteractionPerformanceStatus.MISSING_STATE_TRANSITION,
            InteractionPerformanceStatus.TIMEOUT,
        )

    @property
    def is_unavailable(self) -> bool:
        return self.status == InteractionPerformanceStatus.MEASUREMENT_UNAVAILABLE

    @property
    def target(self) -> Any:
        return self.spec.target

    @property
    def action_type(self) -> TesterActionType:
        return self.spec.action_type

    @property
    def test_case_id(self) -> Optional[str]:
        return self.spec.test_case_id

    # -----------------------------------------------------------------------
    # Invariant Guards: Zero Code Modifications & Zero Optimization
    # -----------------------------------------------------------------------

    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Interaction performance evaluation cannot modify source code or apply fixes."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Interaction performance evaluation cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Interaction performance evaluation cannot modify source code or apply fixes."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Interaction performance evaluation cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Interaction performance evaluation does not classify defects in Phase 7.3."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_DEFECT_CLASSIFICATION",
            reason="Interaction performance evaluation does not classify defects in Phase 7.3.",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "interaction_performance_id": self.interaction_performance_id,
            "execution_id": self.execution_id,
            "spec": self.spec.to_dict(),
            "status": self.status.value,
            "action_started_at": self.action_started_at,
            "action_completed_at": self.action_completed_at,
            "state_ready_at": self.state_ready_at,
            "action_execution_duration_ms": self.action_execution_duration_ms,
            "state_readiness_duration_ms": self.state_readiness_duration_ms,
            "interaction_duration_ms": self.interaction_duration_ms,
            "threshold_ms": self.threshold_ms,
            "threshold_met": self.threshold_met,
            "measurements": [m.to_dict() for m in self.measurements],
            "evidence_id": self.evidence_id,
            "trace_id": self.trace_id,
            "error_message": self.error_message,
            "sample_count": self.sample_count,
            "provenance": dict(self.provenance),
            "timestamps": dict(self.timestamps),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InteractionPerformanceResult:
        spec = InteractionPerformanceSpec.from_dict(data["spec"])
        raw_status = data.get("status", InteractionPerformanceStatus.SUCCESS.value)
        try:
            status = InteractionPerformanceStatus(raw_status.upper())
        except ValueError:
            status = InteractionPerformanceStatus.SUCCESS

        measurements = [
            PerformanceMeasurement.from_dict(m)
            for m in data.get("measurements", [])
        ]

        raw_thresh = data.get("threshold_ms")
        threshold_ms = float(raw_thresh) if raw_thresh is not None else None

        threshold_met = data.get("threshold_met")
        if threshold_met is not None:
            threshold_met = bool(threshold_met)

        return cls(
            interaction_performance_id=str(data["interaction_performance_id"]),
            execution_id=str(data["execution_id"]),
            spec=spec,
            status=status,
            measurements=measurements,
            action_started_at=data.get("action_started_at"),
            action_completed_at=data.get("action_completed_at"),
            state_ready_at=data.get("state_ready_at"),
            action_execution_duration_ms=float(data["action_execution_duration_ms"])
            if data.get("action_execution_duration_ms") is not None
            else None,
            state_readiness_duration_ms=float(data["state_readiness_duration_ms"])
            if data.get("state_readiness_duration_ms") is not None
            else None,
            interaction_duration_ms=float(data["interaction_duration_ms"])
            if data.get("interaction_duration_ms") is not None
            else None,
            threshold_ms=threshold_ms,
            threshold_met=threshold_met,
            evidence_id=data.get("evidence_id"),
            trace_id=data.get("trace_id"),
            error_message=data.get("error_message"),
            sample_count=int(data.get("sample_count", 1)),
            provenance=dict(data.get("provenance", {})),
            timestamps=dict(data.get("timestamps", {})),
            metadata=dict(data.get("metadata", {})),
        )

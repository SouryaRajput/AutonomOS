from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.identifiers import (
    new_load_performance_id,
    validate_execution_id,
    validate_load_performance_id,
    validate_test_case_id,
)
from core.tester.contracts.performance import (
    PerformanceMeasurement,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    NavigationPerformanceStatus,
    PerformanceInitialState,
    PerformanceMetricType,
)

logger = logging.getLogger("AutonomOS.Tester.LoadPerformanceContracts")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Load & Navigation Performance Test Specification
# ---------------------------------------------------------------------------

@dataclass
class LoadPerformanceTestSpec:
    """
    Specification for evaluating application startup, navigation, and page load performance.
    Captures target route, clean state requirements, optional explicit threshold, and repetition bounds.
    """
    __test__ = False
    target_url: str
    route_name: Optional[str] = None
    initial_state: PerformanceInitialState = PerformanceInitialState.FRESH_PAGE
    threshold_ms: Optional[float] = None
    timeout_seconds: float = 30.0
    readiness_selector: Optional[str] = None
    repeat_count: int = 1
    test_case_id: Optional[str] = None
    execution_id: Optional[str] = None
    spec_id: str = field(default_factory=new_load_performance_id)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.initial_state, str):
            try:
                self.initial_state = PerformanceInitialState(self.initial_state.upper())
            except ValueError:
                self.initial_state = PerformanceInitialState.FRESH_PAGE

        if self.threshold_ms is not None:
            self.threshold_ms = float(self.threshold_ms)
            if self.threshold_ms <= 0:
                raise TesterValidationError(
                    f"threshold_ms must be strictly positive if specified, got {self.threshold_ms}.",
                    field_name="threshold_ms",
                )

        if self.repeat_count < 1:
            self.repeat_count = 1

        if self.test_case_id:
            validate_test_case_id(self.test_case_id)

        if self.execution_id:
            validate_execution_id(self.execution_id)

        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "target_url": self.target_url,
            "route_name": self.route_name,
            "initial_state": self.initial_state.value,
            "threshold_ms": self.threshold_ms,
            "timeout_seconds": self.timeout_seconds,
            "readiness_selector": self.readiness_selector,
            "repeat_count": self.repeat_count,
            "test_case_id": self.test_case_id,
            "execution_id": self.execution_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoadPerformanceTestSpec:
        raw_state = data.get("initial_state", PerformanceInitialState.FRESH_PAGE.value)
        try:
            initial_state = PerformanceInitialState(raw_state.upper())
        except ValueError:
            initial_state = PerformanceInitialState.FRESH_PAGE

        raw_thresh = data.get("threshold_ms")
        thresh = float(raw_thresh) if raw_thresh is not None else None

        return cls(
            target_url=str(data["target_url"]),
            route_name=data.get("route_name"),
            initial_state=initial_state,
            threshold_ms=thresh,
            timeout_seconds=float(data.get("timeout_seconds", 30.0)),
            readiness_selector=data.get("readiness_selector"),
            repeat_count=int(data.get("repeat_count", 1)),
            test_case_id=data.get("test_case_id"),
            execution_id=data.get("execution_id"),
            spec_id=str(data.get("spec_id", new_load_performance_id())),
            metadata=dict(data.get("metadata", {})),
        )

    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Load performance spec cannot modify application code or apply fixes."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Load performance spec cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Load performance spec cannot modify application code or apply fixes."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Load performance spec cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Load performance spec does not classify defects in Phase 7.2."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_DEFECT_CLASSIFICATION",
            reason="Load performance spec does not classify defects in Phase 7.2.",
        )


# ---------------------------------------------------------------------------
# Load & Navigation Performance Result
# ---------------------------------------------------------------------------

@dataclass
class LoadPerformanceResult:
    """
    Authoritative result of an application startup, navigation, or page load measurement.
    Accurately captures quantitative durations and explicit threshold fulfillment without
    classifying defects or inventing thresholds.
    """
    __test__ = False
    load_performance_id: str
    execution_id: str
    spec: LoadPerformanceTestSpec
    status: NavigationPerformanceStatus = NavigationPerformanceStatus.SUCCESS
    measurements: list[PerformanceMeasurement] = field(default_factory=list)
    navigation_duration_ms: Optional[float] = None
    page_load_duration_ms: Optional[float] = None
    application_startup_duration_ms: Optional[float] = None
    dom_content_loaded_ms: Optional[float] = None
    load_event_ms: Optional[float] = None
    first_contentful_paint_ms: Optional[float] = None
    largest_contentful_paint_ms: Optional[float] = None
    route_transition_duration_ms: Optional[float] = None
    page_readiness_duration_ms: Optional[float] = None
    threshold_ms: Optional[float] = None
    threshold_met: Optional[bool] = None
    error_message: Optional[str] = None
    sample_count: int = 1
    provenance: dict[str, Any] = field(default_factory=dict)
    timestamps: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        validate_load_performance_id(self.load_performance_id)
        validate_execution_id(self.execution_id)

        if self.spec.execution_id and self.spec.execution_id != self.execution_id:
            raise TesterLineageError(
                f"LoadPerformanceResult execution_id '{self.execution_id}' does not match spec execution_id '{self.spec.execution_id}'."
            )

        if isinstance(self.status, str):
            try:
                self.status = NavigationPerformanceStatus(self.status.upper())
            except ValueError:
                self.status = NavigationPerformanceStatus.SUCCESS

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
                self.page_load_duration_ms
                or self.navigation_duration_ms
                or self.route_transition_duration_ms
                or self.application_startup_duration_ms
            )
            if primary_duration is not None:
                self.threshold_met = primary_duration <= self.threshold_ms

    @property
    def is_success(self) -> bool:
        return self.status == NavigationPerformanceStatus.SUCCESS

    @property
    def is_timeout(self) -> bool:
        return self.status == NavigationPerformanceStatus.TIMEOUT

    @property
    def is_failed(self) -> bool:
        return self.status in (
            NavigationPerformanceStatus.APPLICATION_LOAD_FAILED,
            NavigationPerformanceStatus.NAVIGATION_FAILED,
        )

    @property
    def is_unavailable(self) -> bool:
        return self.status == NavigationPerformanceStatus.MEASUREMENT_UNAVAILABLE

    @property
    def target_url(self) -> str:
        return self.spec.target_url

    @property
    def initial_state(self) -> PerformanceInitialState:
        return self.spec.initial_state

    @property
    def test_case_id(self) -> Optional[str]:
        return self.spec.test_case_id

    # -----------------------------------------------------------------------
    # Invariant Guards: Zero Fixing & Zero Defect Creation in Phase 7.2
    # -----------------------------------------------------------------------

    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Load performance evaluation cannot modify source code or apply fixes."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Load performance evaluation cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Load performance evaluation cannot modify source code or apply fixes."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Load performance evaluation cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Load performance evaluation does not classify defects in Phase 7.2."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_DEFECT_CLASSIFICATION",
            reason="Load performance evaluation does not classify defects in Phase 7.2.",
        )

    # -----------------------------------------------------------------------
    # Serialization
    # -----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "load_performance_id": self.load_performance_id,
            "execution_id": self.execution_id,
            "spec": self.spec.to_dict(),
            "status": self.status.value,
            "measurements": [m.to_dict() for m in self.measurements],
            "navigation_duration_ms": self.navigation_duration_ms,
            "page_load_duration_ms": self.page_load_duration_ms,
            "application_startup_duration_ms": self.application_startup_duration_ms,
            "dom_content_loaded_ms": self.dom_content_loaded_ms,
            "load_event_ms": self.load_event_ms,
            "first_contentful_paint_ms": self.first_contentful_paint_ms,
            "largest_contentful_paint_ms": self.largest_contentful_paint_ms,
            "route_transition_duration_ms": self.route_transition_duration_ms,
            "page_readiness_duration_ms": self.page_readiness_duration_ms,
            "threshold_ms": self.threshold_ms,
            "threshold_met": self.threshold_met,
            "error_message": self.error_message,
            "sample_count": self.sample_count,
            "provenance": dict(self.provenance),
            "timestamps": dict(self.timestamps),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoadPerformanceResult:
        spec = LoadPerformanceTestSpec.from_dict(data["spec"])

        raw_status = data.get("status", NavigationPerformanceStatus.SUCCESS.value)
        try:
            status = NavigationPerformanceStatus(raw_status.upper())
        except ValueError:
            status = NavigationPerformanceStatus.SUCCESS

        measurements = [
            PerformanceMeasurement.from_dict(m) if isinstance(m, dict) else m
            for m in data.get("measurements", [])
        ]

        return cls(
            load_performance_id=str(data["load_performance_id"]),
            execution_id=str(data["execution_id"]),
            spec=spec,
            status=status,
            measurements=measurements,
            navigation_duration_ms=data.get("navigation_duration_ms"),
            page_load_duration_ms=data.get("page_load_duration_ms"),
            application_startup_duration_ms=data.get("application_startup_duration_ms"),
            dom_content_loaded_ms=data.get("dom_content_loaded_ms"),
            load_event_ms=data.get("load_event_ms"),
            first_contentful_paint_ms=data.get("first_contentful_paint_ms"),
            largest_contentful_paint_ms=data.get("largest_contentful_paint_ms"),
            route_transition_duration_ms=data.get("route_transition_duration_ms"),
            page_readiness_duration_ms=data.get("page_readiness_duration_ms"),
            threshold_ms=data.get("threshold_ms"),
            threshold_met=data.get("threshold_met"),
            error_message=data.get("error_message"),
            sample_count=int(data.get("sample_count", 1)),
            provenance=dict(data.get("provenance", {})),
            timestamps=dict(data.get("timestamps", {})),
            metadata=dict(data.get("metadata", {})),
        )

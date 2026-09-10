from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.geometry import ViewportDimensions
from core.tester.contracts.identifiers import (
    new_performance_measurement_id,
    validate_execution_id,
    validate_performance_measurement_id,
    validate_test_case_id,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    PerformanceMeasurementStatus,
    PerformanceMetricType,
    PerformanceMetricUnit,
)

logger = logging.getLogger("AutonomOS.Tester.PerformanceContracts")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Performance Environment Context
# ---------------------------------------------------------------------------

@dataclass
class PerformanceEnvironment:
    """
    Environmental context under which a performance measurement was captured.
    Ensures measurements are not compared across incompatible runtimes or viewports
    without explicitly recording the discrepancy.
    """
    browser: str = "headless"
    viewport: Optional[ViewportDimensions] = None
    network_configuration: str = "unthrottled"
    application_revision: str = "unknown"
    test_execution: str = ""
    timestamp: str = field(default_factory=utc_now)
    os_platform: str = "unknown"
    hardware_concurrency: Optional[int] = None
    device_memory_gb: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.viewport, dict):
            self.viewport = ViewportDimensions.from_dict(self.viewport)
        self.metadata = dict(self.metadata)

    def is_compatible_with(self, other: PerformanceEnvironment) -> tuple[bool, list[str]]:
        """
        Check if another environment is comparable for performance benchmarking.
        Returns (is_compatible, list_of_discrepancies).
        """
        differences: list[str] = []

        if self.browser.lower() != other.browser.lower():
            differences.append(f"Browser mismatch: '{self.browser}' vs '{other.browser}'")

        if self.network_configuration.lower() != other.network_configuration.lower():
            differences.append(
                f"Network mismatch: '{self.network_configuration}' vs '{other.network_configuration}'"
            )

        if self.viewport is not None and other.viewport is not None:
            if abs(self.viewport.width - other.viewport.width) > 50 or abs(self.viewport.height - other.viewport.height) > 50:
                differences.append(
                    f"Viewport mismatch: {self.viewport.width}x{self.viewport.height} vs {other.viewport.width}x{other.viewport.height}"
                )
        elif (self.viewport is None) != (other.viewport is None):
            differences.append("Viewport presence mismatch (one specified, other unspecified)")

        return len(differences) == 0, differences

    def to_dict(self) -> dict[str, Any]:
        return {
            "browser": self.browser,
            "viewport": self.viewport.to_dict() if self.viewport else None,
            "network_configuration": self.network_configuration,
            "application_revision": self.application_revision,
            "test_execution": self.test_execution,
            "timestamp": self.timestamp,
            "os_platform": self.os_platform,
            "hardware_concurrency": self.hardware_concurrency,
            "device_memory_gb": self.device_memory_gb,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerformanceEnvironment:
        vp_raw = data.get("viewport")
        vp = ViewportDimensions.from_dict(vp_raw) if vp_raw else None
        return cls(
            browser=str(data.get("browser", "headless")),
            viewport=vp,
            network_configuration=str(data.get("network_configuration", "unthrottled")),
            application_revision=str(data.get("application_revision", "unknown")),
            test_execution=str(data.get("test_execution", "")),
            timestamp=str(data.get("timestamp", utc_now())),
            os_platform=str(data.get("os_platform", "unknown")),
            hardware_concurrency=data.get("hardware_concurrency"),
            device_memory_gb=data.get("device_memory_gb"),
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# Performance Measurement Budget
# ---------------------------------------------------------------------------

@dataclass
class PerformanceMeasurementBudget:
    """
    Finite sampling limits for runtime performance data collection.
    Prevents infinite sampling and uncontrolled memory/time overhead.
    """
    max_measurements: int = 100
    max_samples_per_metric: int = 20
    time_budget_ms: Optional[float] = None
    enforce_strict_budget: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_measurements": self.max_measurements,
            "max_samples_per_metric": self.max_samples_per_metric,
            "time_budget_ms": self.time_budget_ms,
            "enforce_strict_budget": self.enforce_strict_budget,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerformanceMeasurementBudget:
        return cls(
            max_measurements=int(data.get("max_measurements", 100)),
            max_samples_per_metric=int(data.get("max_samples_per_metric", 20)),
            time_budget_ms=float(data["time_budget_ms"]) if data.get("time_budget_ms") is not None else None,
            enforce_strict_budget=bool(data.get("enforce_strict_budget", True)),
        )


# ---------------------------------------------------------------------------
# Performance Measurement Model
# ---------------------------------------------------------------------------

@dataclass
class PerformanceMeasurement:
    """
    Authoritative quantitative runtime performance measurement.
    Strictly records measurable performance attributes without evaluating whether
    they meet or fail expectations.
    """
    __test__ = False
    measurement_id: str
    execution_id: str
    metric: PerformanceMetricType | str
    test_case_id: Optional[str] = None
    value: Optional[float] = None
    unit: PerformanceMetricUnit | str = PerformanceMetricUnit.MILLISECONDS
    status: PerformanceMeasurementStatus = PerformanceMeasurementStatus.AVAILABLE
    timestamp: str = field(default_factory=utc_now)
    source: str = "performance_recorder"
    environment: PerformanceEnvironment = field(default_factory=PerformanceEnvironment)
    viewport: Optional[ViewportDimensions] = None
    sample_info: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    trace_id: Optional[str] = None
    threshold_context: Optional[dict[str, Any]] = None
    unavailable_reason: Optional[str] = None
    validation_errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate identifier formatting, lineage, and quantitative value invariants."""
        validate_performance_measurement_id(self.measurement_id)
        validate_execution_id(self.execution_id)

        if self.test_case_id:
            validate_test_case_id(self.test_case_id)

        # Normalize metric type
        if isinstance(self.metric, str):
            try:
                self.metric = PerformanceMetricType.from_str(self.metric)
            except ValueError:
                pass

        # Normalize unit
        if isinstance(self.unit, str):
            try:
                self.unit = PerformanceMetricUnit(self.unit.lower())
            except ValueError:
                pass

        # Normalize status
        if isinstance(self.status, str):
            try:
                self.status = PerformanceMeasurementStatus(self.status.upper())
            except ValueError:
                self.status = PerformanceMeasurementStatus.AVAILABLE

        # Normalize environment
        if isinstance(self.environment, dict):
            self.environment = PerformanceEnvironment.from_dict(self.environment)

        # Normalize viewport
        if isinstance(self.viewport, dict):
            self.viewport = ViewportDimensions.from_dict(self.viewport)
        elif self.viewport is None and self.environment and self.environment.viewport is not None:
            self.viewport = self.environment.viewport

        self.sample_info = dict(self.sample_info)
        self.provenance = dict(self.provenance)
        self.metadata = dict(self.metadata)
        self.validation_errors = list(self.validation_errors)

        # Invariant: Value handling & numeric validity
        if self.value is None:
            if self.status != PerformanceMeasurementStatus.INVALID:
                self.status = PerformanceMeasurementStatus.UNAVAILABLE
        else:
            try:
                self.value = float(self.value)
            except (ValueError, TypeError):
                self.status = PerformanceMeasurementStatus.INVALID
                self.validation_errors.append(f"Value '{self.value}' cannot be converted to float.")

            # Duration and count metrics cannot be negative
            if self.value is not None and self.value < 0:
                metric_name = getattr(self.metric, "value", str(self.metric))
                self.status = PerformanceMeasurementStatus.INVALID
                self.validation_errors.append(
                    f"Negative value {self.value} is invalid for metric '{metric_name}'."
                )

    @property
    def is_available(self) -> bool:
        """Check if numeric measurement is available."""
        return self.status == PerformanceMeasurementStatus.AVAILABLE and self.value is not None

    @property
    def is_unavailable(self) -> bool:
        """Check if metric is unavailable on this platform/runtime."""
        return self.status == PerformanceMeasurementStatus.UNAVAILABLE

    @property
    def is_valid(self) -> bool:
        """Check if measurement has valid structure and non-negative value."""
        return self.status != PerformanceMeasurementStatus.INVALID and len(self.validation_errors) == 0

    # -----------------------------------------------------------------------
    # Invariant Guards: Zero Fixing & Zero Defect Creation in Phase 7.1
    # -----------------------------------------------------------------------

    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Performance measurement cannot modify source code or apply fixes."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Performance measurement foundation cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Performance measurement cannot modify source code or apply fixes."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Performance measurement foundation cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Performance measurement does not classify defects in Phase 7.1."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_DEFECT_CLASSIFICATION",
            reason="Performance measurement foundation does not classify defects in Phase 7.1.",
        )

    # -----------------------------------------------------------------------
    # Serialization
    # -----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Convert PerformanceMeasurement to JSON-serializable dictionary."""
        return {
            "measurement_id": self.measurement_id,
            "execution_id": self.execution_id,
            "test_case_id": self.test_case_id,
            "metric": getattr(self.metric, "value", str(self.metric)),
            "value": self.value,
            "unit": getattr(self.unit, "value", str(self.unit)),
            "status": self.status.value,
            "timestamp": self.timestamp,
            "source": self.source,
            "environment": self.environment.to_dict(),
            "viewport": self.viewport.to_dict() if self.viewport else None,
            "sample_info": dict(self.sample_info),
            "provenance": dict(self.provenance),
            "trace_id": self.trace_id,
            "threshold_context": dict(self.threshold_context) if self.threshold_context else None,
            "unavailable_reason": self.unavailable_reason,
            "validation_errors": list(self.validation_errors),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerformanceMeasurement:
        """Construct PerformanceMeasurement from dictionary."""
        env_raw = data.get("environment", {})
        env = PerformanceEnvironment.from_dict(env_raw) if isinstance(env_raw, dict) else PerformanceEnvironment()

        vp_raw = data.get("viewport")
        vp = ViewportDimensions.from_dict(vp_raw) if isinstance(vp_raw, dict) else None

        raw_metric = data.get("metric", "")
        try:
            metric = PerformanceMetricType.from_str(raw_metric)
        except ValueError:
            metric = raw_metric

        raw_unit = data.get("unit", PerformanceMetricUnit.MILLISECONDS.value)
        try:
            unit = PerformanceMetricUnit(raw_unit)
        except ValueError:
            unit = raw_unit

        raw_status = data.get("status", PerformanceMeasurementStatus.AVAILABLE.value)
        try:
            status = PerformanceMeasurementStatus(raw_status)
        except ValueError:
            status = PerformanceMeasurementStatus.AVAILABLE

        return cls(
            measurement_id=str(data["measurement_id"]),
            execution_id=str(data["execution_id"]),
            test_case_id=data.get("test_case_id"),
            metric=metric,
            value=data.get("value"),
            unit=unit,
            status=status,
            timestamp=str(data.get("timestamp", utc_now())),
            source=str(data.get("source", "performance_recorder")),
            environment=env,
            viewport=vp,
            sample_info=dict(data.get("sample_info", {})),
            provenance=dict(data.get("provenance", {})),
            trace_id=data.get("trace_id"),
            threshold_context=data.get("threshold_context"),
            unavailable_reason=data.get("unavailable_reason"),
            validation_errors=list(data.get("validation_errors", [])),
            metadata=dict(data.get("metadata", {})),
        )

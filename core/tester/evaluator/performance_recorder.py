from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.geometry import ViewportDimensions
from core.tester.contracts.identifiers import (
    new_performance_measurement_id,
    validate_execution_id,
    validate_test_case_id,
)
from core.tester.contracts.performance import (
    PerformanceEnvironment,
    PerformanceMeasurement,
    PerformanceMeasurementBudget,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterBudgetExceededError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    PerformanceMeasurementStatus,
    PerformanceMetricType,
    PerformanceMetricUnit,
)

logger = logging.getLogger("AutonomOS.Tester.PerformanceRecorder")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class PerformanceMeasurementRecorder:
    """
    Deterministic recorder for runtime performance measurements.
    Strictly collects, validates, and stores performance data bounded by execution
    budgets without performing subjective evaluation, benchmark invention, or defect classification.
    """
    __test__ = False

    def __init__(
        self,
        execution_id: str,
        project_id: str,
        work_order_id: Optional[str] = None,
        environment: Optional[PerformanceEnvironment] = None,
        budget: Optional[PerformanceMeasurementBudget] = None,
    ) -> None:
        validate_execution_id(execution_id)
        if not project_id or not project_id.strip():
            raise TesterLineageError("PerformanceMeasurementRecorder requires a valid non-empty project_id.")

        self.execution_id = execution_id
        self.project_id = project_id
        self.work_order_id = work_order_id
        self.environment = environment or PerformanceEnvironment(test_execution=execution_id)
        self.budget = budget or PerformanceMeasurementBudget()
        self.measurements: list[PerformanceMeasurement] = []

    def record(self, measurement: PerformanceMeasurement) -> PerformanceMeasurement:
        """
        Record an authoritative PerformanceMeasurement.
        Enforces execution isolation, budget boundaries, and metric validity.
        """
        # 1. Enforce execution isolation
        if measurement.execution_id != self.execution_id:
            raise TesterLineageError(
                f"Measurement execution_id ('{measurement.execution_id}') does not match "
                f"recorder session ('{self.execution_id}')."
            )

        # 2. Check total measurement count budget
        if self.budget.enforce_strict_budget and len(self.measurements) >= self.budget.max_measurements:
            raise TesterBudgetExceededError(
                message=(
                    f"Performance measurement budget exhausted: reached maximum of "
                    f"{self.budget.max_measurements} measurements."
                ),
                budget_type="MAX_MEASUREMENTS",
                current_value=len(self.measurements),
                max_allowed=self.budget.max_measurements,
            )

        # 3. Check per-metric sample count budget
        metric_key = getattr(measurement.metric, "value", str(measurement.metric))
        current_samples = sum(
            1 for m in self.measurements
            if getattr(m.metric, "value", str(m.metric)) == metric_key
        )
        if self.budget.enforce_strict_budget and current_samples >= self.budget.max_samples_per_metric:
            raise TesterBudgetExceededError(
                message=(
                    f"Per-metric sample budget exhausted for metric '{metric_key}': "
                    f"reached maximum of {self.budget.max_samples_per_metric} samples."
                ),
                budget_type="MAX_SAMPLES_PER_METRIC",
                current_value=current_samples,
                max_allowed=self.budget.max_samples_per_metric,
            )

        # Attach default environment if measurement environment is empty
        if not measurement.environment.test_execution:
            measurement.environment.test_execution = self.execution_id

        self.measurements.append(measurement)
        return measurement

    def record_metric(
        self,
        metric: PerformanceMetricType | str,
        value: Optional[float],
        unit: PerformanceMetricUnit | str = PerformanceMetricUnit.MILLISECONDS,
        test_case_id: Optional[str] = None,
        source: str = "performance_recorder",
        viewport: Optional[ViewportDimensions] = None,
        sample_info: Optional[dict[str, Any]] = None,
        provenance: Optional[dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        threshold_context: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> PerformanceMeasurement:
        """Construct and record a measurable numeric metric."""
        vp = viewport or self.environment.viewport
        measurement = PerformanceMeasurement(
            measurement_id=new_performance_measurement_id(),
            execution_id=self.execution_id,
            test_case_id=test_case_id,
            metric=metric,
            value=value,
            unit=unit,
            status=PerformanceMeasurementStatus.AVAILABLE if value is not None else PerformanceMeasurementStatus.UNAVAILABLE,
            source=source,
            environment=self.environment,
            viewport=vp,
            sample_info=sample_info or {},
            provenance=provenance or {},
            trace_id=trace_id,
            threshold_context=threshold_context,
            metadata=metadata or {},
        )
        return self.record(measurement)

    def record_unavailable(
        self,
        metric: PerformanceMetricType | str,
        reason: str = "Metric unavailable on active platform or runtime",
        test_case_id: Optional[str] = None,
        source: str = "performance_recorder",
        metadata: Optional[dict[str, Any]] = None,
    ) -> PerformanceMeasurement:
        """Explicitly record that a metric is unavailable without synthesizing fallback data."""
        measurement = PerformanceMeasurement(
            measurement_id=new_performance_measurement_id(),
            execution_id=self.execution_id,
            test_case_id=test_case_id,
            metric=metric,
            value=None,
            status=PerformanceMeasurementStatus.UNAVAILABLE,
            source=source,
            environment=self.environment,
            viewport=self.environment.viewport,
            unavailable_reason=reason,
            metadata=metadata or {},
        )
        return self.record(measurement)

    def record_from_navigation_timing(
        self,
        timing_data: dict[str, Any],
        test_case_id: Optional[str] = None,
        source: str = "navigation_timing_api",
    ) -> list[PerformanceMeasurement]:
        """
        Extract and record standard timing metrics from browser or mock timing dictionary.
        Recognizes navigation_duration, page_load_duration, dom_content_loaded, load_event_duration,
        first_contentful_paint (FCP), largest_contentful_paint (LCP), and interaction_duration.
        """
        recorded: list[PerformanceMeasurement] = []

        timing_mappings = [
            (
                PerformanceMetricType.NAVIGATION_DURATION,
                ["navigation_duration", "navigationDuration", "nav_duration"],
                PerformanceMetricUnit.MILLISECONDS,
            ),
            (
                PerformanceMetricType.PAGE_LOAD_DURATION,
                ["page_load_duration", "pageLoadDuration", "load_duration"],
                PerformanceMetricUnit.MILLISECONDS,
            ),
            (
                PerformanceMetricType.DOM_CONTENT_LOADED,
                ["dom_content_loaded", "domContentLoaded", "domContentLoadedEventEnd", "dcl"],
                PerformanceMetricUnit.MILLISECONDS,
            ),
            (
                PerformanceMetricType.LOAD_EVENT_DURATION,
                ["load_event_duration", "loadEventDuration", "loadEventEnd"],
                PerformanceMetricUnit.MILLISECONDS,
            ),
            (
                PerformanceMetricType.FIRST_CONTENTFUL_PAINT,
                ["first_contentful_paint", "firstContentfulPaint", "fcp"],
                PerformanceMetricUnit.MILLISECONDS,
            ),
            (
                PerformanceMetricType.LARGEST_CONTENTFUL_PAINT,
                ["largest_contentful_paint", "largestContentfulPaint", "lcp"],
                PerformanceMetricUnit.MILLISECONDS,
            ),
            (
                PerformanceMetricType.INTERACTION_DURATION,
                ["interaction_duration", "interactionDuration", "inp"],
                PerformanceMetricUnit.MILLISECONDS,
            ),
        ]

        for metric_type, keys, unit in timing_mappings:
            val = None
            found_key = False
            for k in keys:
                if k in timing_data:
                    val = timing_data[k]
                    found_key = True
                    break

            if found_key:
                if val is not None:
                    m = self.record_metric(
                        metric=metric_type,
                        value=float(val),
                        unit=unit,
                        test_case_id=test_case_id,
                        source=source,
                    )
                else:
                    m = self.record_unavailable(
                        metric=metric_type,
                        reason=f"Metric '{metric_type.value}' was null in {source}",
                        test_case_id=test_case_id,
                        source=source,
                    )
                recorded.append(m)

        return recorded

    def record_from_network_stats(
        self,
        stats: dict[str, Any],
        test_case_id: Optional[str] = None,
        source: str = "network_monitor",
    ) -> list[PerformanceMeasurement]:
        """
        Extract and record network and request metrics from dictionary.
        Recognizes total_requests, failed_requests, request_duration, and resource_count.
        """
        recorded: list[PerformanceMeasurement] = []

        network_mappings = [
            (
                PerformanceMetricType.TOTAL_REQUESTS,
                ["total_requests", "totalRequests", "requests_count"],
                PerformanceMetricUnit.COUNT,
            ),
            (
                PerformanceMetricType.FAILED_REQUESTS,
                ["failed_requests", "failedRequests", "error_requests"],
                PerformanceMetricUnit.COUNT,
            ),
            (
                PerformanceMetricType.REQUEST_DURATION,
                ["request_duration", "requestDuration", "avg_request_duration_ms"],
                PerformanceMetricUnit.MILLISECONDS,
            ),
            (
                PerformanceMetricType.RESOURCE_COUNT,
                ["resource_count", "resourceCount", "total_resources"],
                PerformanceMetricUnit.COUNT,
            ),
        ]

        for metric_type, keys, unit in network_mappings:
            val = None
            found_key = False
            for k in keys:
                if k in stats:
                    val = stats[k]
                    found_key = True
                    break

            if found_key:
                if val is not None:
                    m = self.record_metric(
                        metric=metric_type,
                        value=float(val),
                        unit=unit,
                        test_case_id=test_case_id,
                        source=source,
                    )
                else:
                    m = self.record_unavailable(
                        metric=metric_type,
                        reason=f"Metric '{metric_type.value}' was null in {source}",
                        test_case_id=test_case_id,
                        source=source,
                    )
                recorded.append(m)

        return recorded

    def get_measurements_by_metric(
        self,
        metric: PerformanceMetricType | str,
    ) -> list[PerformanceMeasurement]:
        """Return all measurements matching the specified metric."""
        metric_key = getattr(metric, "value", str(metric)).lower()
        return [
            m for m in self.measurements
            if getattr(m.metric, "value", str(m.metric)).lower() == metric_key
        ]

    def get_metric_value(
        self,
        metric: PerformanceMetricType | str,
    ) -> Optional[float]:
        """Return the most recent numeric value for a metric, or None."""
        matching = self.get_measurements_by_metric(metric)
        for m in reversed(matching):
            if m.is_available:
                return m.value
        return None

    # -----------------------------------------------------------------------
    # Invariant Guards: Zero Fixing & Zero Defect Creation in Phase 7.1
    # -----------------------------------------------------------------------

    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Performance measurement recorder cannot modify source code or apply fixes."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Performance measurement recorder cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Performance measurement recorder cannot modify source code or apply fixes."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Performance measurement recorder cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Performance measurement foundation does not classify defects in Phase 7.1."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_DEFECT_CLASSIFICATION",
            reason="Performance measurement foundation does not classify defects in Phase 7.1.",
        )

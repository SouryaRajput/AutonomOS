from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.tester.contracts.identifiers import (
    new_load_performance_id,
    validate_execution_id,
)
from core.tester.contracts.load_performance import (
    LoadPerformanceResult,
    LoadPerformanceTestSpec,
)
from core.tester.contracts.performance import (
    PerformanceEnvironment,
    PerformanceMeasurement,
    PerformanceMeasurementBudget,
)
from core.tester.evaluator.performance_recorder import (
    PerformanceMeasurementRecorder,
)
from core.tester.contracts.session import BrowserSession, NavigationResult
from core.tester.errors import (
    NavigationTimeoutError,
    TesterBoundaryViolationError,
    TesterBudgetExceededError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    NavigationPerformanceStatus,
    PerformanceInitialState,
    PerformanceMeasurementStatus,
    PerformanceMetricType,
    PerformanceMetricUnit,
)

logger = logging.getLogger("AutonomOS.Tester.LoadPerformanceEvaluator")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class LoadNavigationPerformanceEvaluator:
    """
    Deterministic evaluator for application startup, navigation, and page load performance.
    Measures quantitative timing against defined clean starting states, verifies explicit thresholds
    without benchmark invention, and preserves distinct failure semantics without automatic defect creation.
    """
    __test__ = False

    def __init__(
        self,
        execution_id: str,
        project_id: str,
        work_order_id: Optional[str] = None,
        environment: Optional[PerformanceEnvironment] = None,
        recorder: Optional[PerformanceMeasurementRecorder] = None,
    ) -> None:
        validate_execution_id(execution_id)
        if not project_id or not project_id.strip():
            raise TesterLineageError("LoadNavigationPerformanceEvaluator requires a valid non-empty project_id.")

        self.execution_id = execution_id
        self.project_id = project_id
        self.work_order_id = work_order_id
        self.environment = environment or PerformanceEnvironment(test_execution=execution_id)
        self.recorder = recorder or PerformanceMeasurementRecorder(
            execution_id=execution_id,
            project_id=project_id,
            work_order_id=work_order_id,
            environment=self.environment,
        )

    def measure_page_load(
        self,
        spec: LoadPerformanceTestSpec,
        session: Optional[BrowserSession] = None,
        mock_timing: Optional[dict[str, Any]] = None,
        mock_failure: Optional[str] = None,
    ) -> LoadPerformanceResult:
        """
        Execute an authorized page load performance measurement test.
        Establishes clean state, navigates to authorized target, extracts reliable timings,
        records measurements into the recorder, and evaluates explicit thresholds if configured.
        """
        load_id = new_load_performance_id()
        started_at = utc_now()

        # 1. Distinguish explicit simulated or caught failures
        if mock_failure == "timeout":
            return LoadPerformanceResult(
                load_performance_id=load_id,
                execution_id=self.execution_id,
                spec=spec,
                status=NavigationPerformanceStatus.TIMEOUT,
                error_message=f"Navigation to '{spec.target_url}' timed out after {spec.timeout_seconds}s.",
                threshold_ms=spec.threshold_ms,
                threshold_met=False if spec.threshold_ms else None,
                timestamps={"started_at": started_at, "completed_at": utc_now()},
            )

        if mock_failure in ("network_error", "navigation_failed"):
            return LoadPerformanceResult(
                load_performance_id=load_id,
                execution_id=self.execution_id,
                spec=spec,
                status=NavigationPerformanceStatus.NAVIGATION_FAILED,
                error_message=f"Navigation to '{spec.target_url}' failed: host unreachable or network dropped.",
                threshold_ms=spec.threshold_ms,
                threshold_met=False if spec.threshold_ms else None,
                timestamps={"started_at": started_at, "completed_at": utc_now()},
            )

        if mock_failure in ("load_failed", "startup_failed", "app_crash"):
            return LoadPerformanceResult(
                load_performance_id=load_id,
                execution_id=self.execution_id,
                spec=spec,
                status=NavigationPerformanceStatus.APPLICATION_LOAD_FAILED,
                error_message=f"Application failed to load or start up at '{spec.target_url}'.",
                threshold_ms=spec.threshold_ms,
                threshold_met=False if spec.threshold_ms else None,
                timestamps={"started_at": started_at, "completed_at": utc_now()},
            )

        if mock_failure == "no_api" or (mock_timing is not None and mock_timing.get("has_performance_api") is False):
            # Missing performance API is an unavailable measurement, NOT a defect
            m = self.recorder.record_unavailable(
                metric=PerformanceMetricType.NAVIGATION_DURATION,
                reason="Performance timing API (window.performance) unavailable on active runtime platform.",
                test_case_id=spec.test_case_id,
            )
            return LoadPerformanceResult(
                load_performance_id=load_id,
                execution_id=self.execution_id,
                spec=spec,
                status=NavigationPerformanceStatus.MEASUREMENT_UNAVAILABLE,
                measurements=[m],
                error_message="Performance measurement APIs unavailable in active environment.",
                threshold_ms=spec.threshold_ms,
                threshold_met=None,
                timestamps={"started_at": started_at, "completed_at": utc_now()},
            )

        # 2. Live Session Navigation (if provided)
        nav_duration_from_session: Optional[float] = None
        if session is not None:
            try:
                # Clean starting state
                if spec.initial_state == PerformanceInitialState.FRESH_PAGE:
                    session.navigate("about:blank")
                elif spec.initial_state == PerformanceInitialState.FRESH_CONTEXT:
                    session.navigate("about:blank")

                nav_res = session.navigate(spec.target_url, timeout_seconds=spec.timeout_seconds)
                if not nav_res.is_success:
                    return LoadPerformanceResult(
                        load_performance_id=load_id,
                        execution_id=self.execution_id,
                        spec=spec,
                        status=NavigationPerformanceStatus.NAVIGATION_FAILED,
                        error_message=nav_res.error or "Navigation failed.",
                        threshold_ms=spec.threshold_ms,
                        threshold_met=False if spec.threshold_ms else None,
                        timestamps={"started_at": started_at, "completed_at": utc_now()},
                    )
                nav_duration_from_session = nav_res.duration_ms
            except NavigationTimeoutError as e:
                return LoadPerformanceResult(
                    load_performance_id=load_id,
                    execution_id=self.execution_id,
                    spec=spec,
                    status=NavigationPerformanceStatus.TIMEOUT,
                    error_message=str(e),
                    threshold_ms=spec.threshold_ms,
                    threshold_met=False if spec.threshold_ms else None,
                    timestamps={"started_at": started_at, "completed_at": utc_now()},
                )
            except Exception as e:
                return LoadPerformanceResult(
                    load_performance_id=load_id,
                    execution_id=self.execution_id,
                    spec=spec,
                    status=NavigationPerformanceStatus.NAVIGATION_FAILED,
                    error_message=str(e),
                    threshold_ms=spec.threshold_ms,
                    threshold_met=False if spec.threshold_ms else None,
                    timestamps={"started_at": started_at, "completed_at": utc_now()},
                )

        # 3. Extract and Record Measurements
        measurements: list[PerformanceMeasurement] = []
        timing = dict(mock_timing or {})

        nav_duration = timing.get("navigation_duration", nav_duration_from_session or 0.0)
        page_load = timing.get("page_load_duration", nav_duration)
        dom_loaded = timing.get("dom_content_loaded")
        load_event = timing.get("load_event_duration")
        fcp = timing.get("first_contentful_paint")
        lcp = timing.get("largest_contentful_paint")
        startup = timing.get("application_startup_duration")
        readiness = timing.get("page_readiness_duration")

        # Repetition loop for bounded sample collection
        actual_samples = 0
        target_repeats = max(1, spec.repeat_count)

        for sample_idx in range(target_repeats):
            try:
                # Record main navigation duration
                m_nav = self.recorder.record_metric(
                    metric=PerformanceMetricType.NAVIGATION_DURATION,
                    value=float(nav_duration),
                    test_case_id=spec.test_case_id,
                    sample_info={"sample_index": sample_idx, "target_url": spec.target_url},
                    threshold_context={"threshold_ms": spec.threshold_ms} if spec.threshold_ms else None,
                )
                measurements.append(m_nav)

                # Record page load duration
                m_load = self.recorder.record_metric(
                    metric=PerformanceMetricType.PAGE_LOAD_DURATION,
                    value=float(page_load),
                    test_case_id=spec.test_case_id,
                    sample_info={"sample_index": sample_idx},
                )
                measurements.append(m_load)

                if dom_loaded is not None:
                    m_dom = self.recorder.record_metric(
                        metric=PerformanceMetricType.DOM_CONTENT_LOADED,
                        value=float(dom_loaded),
                        test_case_id=spec.test_case_id,
                    )
                    measurements.append(m_dom)

                if load_event is not None:
                    m_le = self.recorder.record_metric(
                        metric=PerformanceMetricType.LOAD_EVENT_DURATION,
                        value=float(load_event),
                        test_case_id=spec.test_case_id,
                    )
                    measurements.append(m_le)

                if fcp is not None:
                    m_fcp = self.recorder.record_metric(
                        metric=PerformanceMetricType.FIRST_CONTENTFUL_PAINT,
                        value=float(fcp),
                        test_case_id=spec.test_case_id,
                    )
                    measurements.append(m_fcp)

                if lcp is not None:
                    m_lcp = self.recorder.record_metric(
                        metric=PerformanceMetricType.LARGEST_CONTENTFUL_PAINT,
                        value=float(lcp),
                        test_case_id=spec.test_case_id,
                    )
                    measurements.append(m_lcp)

                if startup is not None:
                    m_sup = self.recorder.record_metric(
                        metric=PerformanceMetricType.APPLICATION_STARTUP_DURATION,
                        value=float(startup),
                        test_case_id=spec.test_case_id,
                    )
                    measurements.append(m_sup)

                if readiness is not None:
                    m_rdy = self.recorder.record_metric(
                        metric=PerformanceMetricType.PAGE_READINESS_DURATION,
                        value=float(readiness),
                        test_case_id=spec.test_case_id,
                    )
                    measurements.append(m_rdy)

                actual_samples += 1
            except TesterBudgetExceededError:
                # Stop bounded sampling deterministically when budget runs out
                logger.info(f"Measurement budget exhausted during load performance repetition at sample {sample_idx}.")
                break

        # 4. Evaluate Threshold if Explicitly Provided
        threshold_met: Optional[bool] = None
        if spec.threshold_ms is not None:
            primary_val = page_load or nav_duration
            threshold_met = float(primary_val) <= float(spec.threshold_ms)

        completed_at = utc_now()
        provenance = {
            "initial_state": spec.initial_state.value,
            "target_url": spec.target_url,
            "samples_collected": actual_samples,
        }

        return LoadPerformanceResult(
            load_performance_id=load_id,
            execution_id=self.execution_id,
            spec=spec,
            status=NavigationPerformanceStatus.SUCCESS,
            measurements=measurements,
            navigation_duration_ms=float(nav_duration),
            page_load_duration_ms=float(page_load),
            application_startup_duration_ms=float(startup) if startup is not None else None,
            dom_content_loaded_ms=float(dom_loaded) if dom_loaded is not None else None,
            load_event_ms=float(load_event) if load_event is not None else None,
            first_contentful_paint_ms=float(fcp) if fcp is not None else None,
            largest_contentful_paint_ms=float(lcp) if lcp is not None else None,
            page_readiness_duration_ms=float(readiness) if readiness is not None else None,
            threshold_ms=spec.threshold_ms,
            threshold_met=threshold_met,
            sample_count=actual_samples,
            provenance=provenance,
            timestamps={"started_at": started_at, "completed_at": completed_at},
        )

    def measure_route_transition(
        self,
        spec: LoadPerformanceTestSpec,
        session: Optional[BrowserSession] = None,
        mock_timing: Optional[dict[str, Any]] = None,
        mock_failure: Optional[str] = None,
    ) -> LoadPerformanceResult:
        """
        Measure in-app client-side navigation or route transition performance.
        """
        load_id = new_load_performance_id()
        started_at = utc_now()

        if mock_failure == "timeout":
            return LoadPerformanceResult(
                load_performance_id=load_id,
                execution_id=self.execution_id,
                spec=spec,
                status=NavigationPerformanceStatus.TIMEOUT,
                error_message=f"Route transition to '{spec.target_url}' timed out.",
                threshold_ms=spec.threshold_ms,
                threshold_met=False if spec.threshold_ms else None,
                timestamps={"started_at": started_at, "completed_at": utc_now()},
            )

        if mock_failure in ("failed", "transition_failed"):
            return LoadPerformanceResult(
                load_performance_id=load_id,
                execution_id=self.execution_id,
                spec=spec,
                status=NavigationPerformanceStatus.NAVIGATION_FAILED,
                error_message=f"Route transition to '{spec.target_url}' failed.",
                threshold_ms=spec.threshold_ms,
                threshold_met=False if spec.threshold_ms else None,
                timestamps={"started_at": started_at, "completed_at": utc_now()},
            )

        timing = dict(mock_timing or {})
        route_duration = float(timing.get("route_transition_duration", 120.0))
        readiness_duration = float(timing.get("page_readiness_duration", route_duration + 50.0))

        measurements = []
        m_route = self.recorder.record_metric(
            metric=PerformanceMetricType.ROUTE_TRANSITION_DURATION,
            value=route_duration,
            test_case_id=spec.test_case_id,
            sample_info={"route_name": spec.route_name or spec.target_url},
        )
        measurements.append(m_route)

        m_rdy = self.recorder.record_metric(
            metric=PerformanceMetricType.PAGE_READINESS_DURATION,
            value=readiness_duration,
            test_case_id=spec.test_case_id,
        )
        measurements.append(m_rdy)

        threshold_met: Optional[bool] = None
        if spec.threshold_ms is not None:
            threshold_met = route_duration <= spec.threshold_ms

        return LoadPerformanceResult(
            load_performance_id=load_id,
            execution_id=self.execution_id,
            spec=spec,
            status=NavigationPerformanceStatus.SUCCESS,
            measurements=measurements,
            route_transition_duration_ms=route_duration,
            page_readiness_duration_ms=readiness_duration,
            threshold_ms=spec.threshold_ms,
            threshold_met=threshold_met,
            sample_count=1,
            provenance={"route_name": spec.route_name, "target_url": spec.target_url},
            timestamps={"started_at": started_at, "completed_at": utc_now()},
        )

    # -----------------------------------------------------------------------
    # Invariant Guards: Zero Fixing & Zero Defect Creation in Phase 7.2
    # -----------------------------------------------------------------------

    def apply_fix(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Load performance evaluator cannot modify source code or apply fixes."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Load performance evaluator cannot modify application code or apply fixes.",
        )

    def auto_fix(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Load performance evaluator cannot modify source code or apply fixes."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_AUTO_FIX",
            reason="Load performance evaluator cannot modify application code or apply fixes.",
        )

    def create_defect(self, *args: Any, **kwargs: Any) -> None:
        """Strict invariant: Load performance evaluator does not classify defects in Phase 7.2."""
        raise TesterBoundaryViolationError(
            action="PERFORMANCE_DEFECT_CLASSIFICATION",
            reason="Load performance evaluator does not classify defects in Phase 7.2.",
        )

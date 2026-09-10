from __future__ import annotations

import unittest
from typing import Any

from core.tester.contracts.geometry import ViewportDimensions
from core.tester.contracts.identifiers import (
    new_evidence_id,
    new_execution_id,
    new_performance_measurement_id,
    new_test_case_id,
    new_trace_id,
    new_work_order_id,
    validate_performance_measurement_id,
)
from core.tester.contracts.performance import (
    PerformanceEnvironment,
    PerformanceMeasurement,
    PerformanceMeasurementBudget,
)
from core.tester.evaluator.performance_recorder import (
    PerformanceMeasurementRecorder,
)
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.result import TesterResult
from core.tester.errors import (
    InvalidTesterIdError,
    TesterBoundaryViolationError,
    TesterBudgetExceededError,
    TesterLineageError,
)
from core.tester.types import (
    PerformanceMeasurementStatus,
    PerformanceMetricType,
    PerformanceMetricUnit,
    TesterExecutionStatus,
    TesterResultStatus,
)


class TestTesterPerformanceMeasurement(unittest.TestCase):
    """
    Unit test suite for Tester V1 — Phase 7.1: Performance Context & Measurement Foundation.
    Tests deterministic measurement recording, environmental context, budget enforcement,
    invariants, and strict zero-defect / zero-fixing boundaries.
    """

    def setUp(self) -> None:
        self.execution_id = new_execution_id()
        self.project_id = "proj-ecommerce-store"
        self.work_order_id = new_work_order_id()
        self.test_case_id = new_test_case_id()
        self.trace_id = new_trace_id()
        self.evidence_id = new_evidence_id()

        self.desktop_viewport = ViewportDimensions(width=1920, height=1080)
        self.mobile_viewport = ViewportDimensions(width=375, height=667)

        self.environment = PerformanceEnvironment(
            browser="chromium-headless",
            viewport=self.desktop_viewport,
            network_configuration="unthrottled",
            application_revision="git-commit-abcdef12",
            test_execution=self.execution_id,
            os_platform="darwin-arm64",
            hardware_concurrency=8,
            device_memory_gb=16.0,
        )

        self.recorder = PerformanceMeasurementRecorder(
            execution_id=self.execution_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            environment=self.environment,
            budget=PerformanceMeasurementBudget(max_measurements=25, max_samples_per_metric=5),
        )

    # -----------------------------------------------------------------------
    # Scenario 1: Valid Measurement
    # -----------------------------------------------------------------------
    def test_01_valid_measurement(self) -> None:
        """Scenario 1: Records valid numeric measurement with metric, value, unit, and environment."""
        measurement = self.recorder.record_metric(
            metric=PerformanceMetricType.PAGE_LOAD_DURATION,
            value=1240.5,
            unit=PerformanceMetricUnit.MILLISECONDS,
            test_case_id=self.test_case_id,
            source="performance_observer",
            trace_id=self.trace_id,
            provenance={"evidence_id": self.evidence_id},
        )

        self.assertIsNotNone(measurement.measurement_id)
        validate_performance_measurement_id(measurement.measurement_id)
        self.assertEqual(measurement.execution_id, self.execution_id)
        self.assertEqual(measurement.test_case_id, self.test_case_id)
        self.assertEqual(measurement.metric, PerformanceMetricType.PAGE_LOAD_DURATION)
        self.assertEqual(measurement.value, 1240.5)
        self.assertEqual(measurement.unit, PerformanceMetricUnit.MILLISECONDS)
        self.assertEqual(measurement.status, PerformanceMeasurementStatus.AVAILABLE)
        self.assertTrue(measurement.is_available)
        self.assertTrue(measurement.is_valid)
        self.assertFalse(measurement.is_unavailable)
        self.assertEqual(len(self.recorder.measurements), 1)

    # -----------------------------------------------------------------------
    # Scenario 2: Unavailable Metric
    # -----------------------------------------------------------------------
    def test_02_unavailable_metric(self) -> None:
        """Scenario 2: Handles unavailable metrics cleanly without synthesizing invented numbers."""
        measurement = self.recorder.record_unavailable(
            metric=PerformanceMetricType.LARGEST_CONTENTFUL_PAINT,
            reason="LCP API is not supported in this runtime environment",
            test_case_id=self.test_case_id,
        )

        self.assertEqual(measurement.metric, PerformanceMetricType.LARGEST_CONTENTFUL_PAINT)
        self.assertIsNone(measurement.value)
        self.assertEqual(measurement.status, PerformanceMeasurementStatus.UNAVAILABLE)
        self.assertTrue(measurement.is_unavailable)
        self.assertFalse(measurement.is_available)
        self.assertTrue(measurement.is_valid)  # Structurally valid, just unavailable
        self.assertIn("not supported", measurement.unavailable_reason or "")
        # Confirm no invented numbers were created
        self.assertIsNone(self.recorder.get_metric_value(PerformanceMetricType.LARGEST_CONTENTFUL_PAINT))

    # -----------------------------------------------------------------------
    # Scenario 3: Invalid Measurement
    # -----------------------------------------------------------------------
    def test_03_invalid_measurement(self) -> None:
        """Scenario 3: Detects invalid negative values and marks status as INVALID with errors."""
        measurement = PerformanceMeasurement(
            measurement_id=new_performance_measurement_id(),
            execution_id=self.execution_id,
            metric=PerformanceMetricType.NAVIGATION_DURATION,
            value=-150.0,  # Negative duration is physically impossible
            unit=PerformanceMetricUnit.MILLISECONDS,
        )

        self.assertEqual(measurement.status, PerformanceMeasurementStatus.INVALID)
        self.assertFalse(measurement.is_valid)
        self.assertFalse(measurement.is_available)
        self.assertGreater(len(measurement.validation_errors), 0)
        self.assertIn("Negative value", measurement.validation_errors[0])

        # Test invalid identifier format
        with self.assertRaises(InvalidTesterIdError):
            PerformanceMeasurement(
                measurement_id="invalid-prefix-1234",
                execution_id=self.execution_id,
                metric=PerformanceMetricType.NAVIGATION_DURATION,
                value=100.0,
            )

    # -----------------------------------------------------------------------
    # Scenario 4: Duration Measurements
    # -----------------------------------------------------------------------
    def test_04_duration_measurement(self) -> None:
        """Scenario 4: Measures duration metrics across standard lifecycle events in ms."""
        m_nav = self.recorder.record_metric(PerformanceMetricType.NAVIGATION_DURATION, 420.0)
        m_dom = self.recorder.record_metric(PerformanceMetricType.DOM_CONTENT_LOADED, 650.0)
        m_fcp = self.recorder.record_metric(PerformanceMetricType.FIRST_CONTENTFUL_PAINT, 510.0)
        m_load = self.recorder.record_metric(PerformanceMetricType.LOAD_EVENT_DURATION, 1100.0)
        m_act = self.recorder.record_metric(PerformanceMetricType.INTERACTION_DURATION, 45.0)

        for m in (m_nav, m_dom, m_fcp, m_load, m_act):
            self.assertEqual(m.unit, PerformanceMetricUnit.MILLISECONDS)
            self.assertTrue(m.is_available)
            self.assertGreater(m.value, 0)

        self.assertEqual(self.recorder.get_metric_value(PerformanceMetricType.NAVIGATION_DURATION), 420.0)
        self.assertEqual(self.recorder.get_metric_value(PerformanceMetricType.DOM_CONTENT_LOADED), 650.0)
        self.assertEqual(self.recorder.get_metric_value(PerformanceMetricType.FIRST_CONTENTFUL_PAINT), 510.0)
        self.assertEqual(self.recorder.get_metric_value(PerformanceMetricType.LOAD_EVENT_DURATION), 1100.0)
        self.assertEqual(self.recorder.get_metric_value(PerformanceMetricType.INTERACTION_DURATION), 45.0)

    # -----------------------------------------------------------------------
    # Scenario 5: Request Measurements
    # -----------------------------------------------------------------------
    def test_05_request_measurement(self) -> None:
        """Scenario 5: Measures request count and duration metrics with proper units."""
        m_reqs = self.recorder.record_metric(
            metric=PerformanceMetricType.TOTAL_REQUESTS,
            value=34.0,
            unit=PerformanceMetricUnit.COUNT,
        )
        m_failed = self.recorder.record_metric(
            metric=PerformanceMetricType.FAILED_REQUESTS,
            value=0.0,
            unit=PerformanceMetricUnit.COUNT,
        )
        m_dur = self.recorder.record_metric(
            metric=PerformanceMetricType.REQUEST_DURATION,
            value=85.2,
            unit=PerformanceMetricUnit.MILLISECONDS,
        )
        m_res = self.recorder.record_metric(
            metric=PerformanceMetricType.RESOURCE_COUNT,
            value=28.0,
            unit=PerformanceMetricUnit.COUNT,
        )

        self.assertEqual(m_reqs.unit, PerformanceMetricUnit.COUNT)
        self.assertEqual(m_reqs.value, 34.0)
        self.assertEqual(m_failed.value, 0.0)
        self.assertEqual(m_dur.value, 85.2)
        self.assertEqual(m_res.value, 28.0)

    # -----------------------------------------------------------------------
    # Scenario 6: Environment Metadata
    # -----------------------------------------------------------------------
    def test_06_environment_metadata(self) -> None:
        """Scenario 6: Verifies full environmental context recording."""
        measurement = self.recorder.record_metric(
            metric=PerformanceMetricType.PAGE_LOAD_DURATION,
            value=950.0,
        )

        env = measurement.environment
        self.assertEqual(env.browser, "chromium-headless")
        self.assertEqual(env.network_configuration, "unthrottled")
        self.assertEqual(env.application_revision, "git-commit-abcdef12")
        self.assertEqual(env.test_execution, self.execution_id)
        self.assertEqual(env.os_platform, "darwin-arm64")
        self.assertEqual(env.hardware_concurrency, 8)
        self.assertEqual(env.device_memory_gb, 16.0)
        self.assertIsNotNone(env.timestamp)

    # -----------------------------------------------------------------------
    # Scenario 7: Different Viewports & Compatibility Checking
    # -----------------------------------------------------------------------
    def test_07_different_viewports(self) -> None:
        """Scenario 7: Records viewport context and detects environment discrepancies."""
        m_desktop = self.recorder.record_metric(
            metric=PerformanceMetricType.PAGE_LOAD_DURATION,
            value=800.0,
            viewport=self.desktop_viewport,
        )

        m_mobile = self.recorder.record_metric(
            metric=PerformanceMetricType.PAGE_LOAD_DURATION,
            value=1450.0,
            viewport=self.mobile_viewport,
        )

        self.assertEqual(m_desktop.viewport.width, 1920)
        self.assertEqual(m_desktop.viewport.height, 1080)
        self.assertEqual(m_mobile.viewport.width, 375)
        self.assertEqual(m_mobile.viewport.height, 667)

        # Environmental compatibility checking
        desktop_env = PerformanceEnvironment(browser="chromium", viewport=self.desktop_viewport)
        mobile_env = PerformanceEnvironment(browser="chromium", viewport=self.mobile_viewport)
        firefox_env = PerformanceEnvironment(browser="firefox", viewport=self.desktop_viewport)

        is_compat, diffs = desktop_env.is_compatible_with(mobile_env)
        self.assertFalse(is_compat)
        self.assertIn("Viewport mismatch", diffs[0])

        is_compat_ff, diffs_ff = desktop_env.is_compatible_with(firefox_env)
        self.assertFalse(is_compat_ff)
        self.assertIn("Browser mismatch", diffs_ff[0])

        is_compat_same, diffs_same = desktop_env.is_compatible_with(desktop_env)
        self.assertTrue(is_compat_same)
        self.assertEqual(len(diffs_same), 0)

    # -----------------------------------------------------------------------
    # Scenario 8: Deterministic Mock Metrics
    # -----------------------------------------------------------------------
    def test_08_deterministic_mock_metrics(self) -> None:
        """Scenario 8: Extracts metrics from deterministic mock timing and network dictionaries."""
        mock_timing = {
            "navigation_duration": 350.0,
            "page_load_duration": 820.0,
            "dom_content_loaded": 480.0,
            "load_event_duration": 800.0,
            "first_contentful_paint": 390.0,
            "largest_contentful_paint": 710.0,
            "interaction_duration": 32.0,
        }
        mock_network = {
            "total_requests": 18,
            "failed_requests": 0,
            "request_duration": 94.5,
            "resource_count": 15,
        }

        timing_results = self.recorder.record_from_navigation_timing(mock_timing, self.test_case_id)
        network_results = self.recorder.record_from_network_stats(mock_network, self.test_case_id)

        self.assertEqual(len(timing_results), 7)
        self.assertEqual(len(network_results), 4)

        self.assertEqual(self.recorder.get_metric_value(PerformanceMetricType.NAVIGATION_DURATION), 350.0)
        self.assertEqual(self.recorder.get_metric_value(PerformanceMetricType.PAGE_LOAD_DURATION), 820.0)
        self.assertEqual(self.recorder.get_metric_value(PerformanceMetricType.TOTAL_REQUESTS), 18.0)
        self.assertEqual(self.recorder.get_metric_value(PerformanceMetricType.FAILED_REQUESTS), 0.0)

    # -----------------------------------------------------------------------
    # Scenario 9: Budget Enforcement
    # -----------------------------------------------------------------------
    def test_09_budget_enforcement(self) -> None:
        """Scenario 9: Bounded sampling stops recording when measurement budget is exhausted."""
        tight_budget = PerformanceMeasurementBudget(max_measurements=3, max_samples_per_metric=2)
        tight_recorder = PerformanceMeasurementRecorder(
            execution_id=self.execution_id,
            project_id=self.project_id,
            budget=tight_budget,
        )

        # First 2 samples of navigation_duration succeed
        tight_recorder.record_metric(PerformanceMetricType.NAVIGATION_DURATION, 100.0)
        tight_recorder.record_metric(PerformanceMetricType.NAVIGATION_DURATION, 110.0)

        # 3rd sample of same metric hits max_samples_per_metric (2)
        with self.assertRaises(TesterBudgetExceededError) as ctx_metric:
            tight_recorder.record_metric(PerformanceMetricType.NAVIGATION_DURATION, 120.0)
        self.assertEqual(ctx_metric.exception.budget_type, "MAX_SAMPLES_PER_METRIC")

        # 3rd total measurement of a different metric succeeds
        tight_recorder.record_metric(PerformanceMetricType.PAGE_LOAD_DURATION, 200.0)

        # 4th total measurement hits max_measurements (3)
        with self.assertRaises(TesterBudgetExceededError) as ctx_total:
            tight_recorder.record_metric(PerformanceMetricType.PAGE_LOAD_DURATION, 210.0)
        self.assertEqual(ctx_total.exception.budget_type, "MAX_MEASUREMENTS")

    # -----------------------------------------------------------------------
    # Scenario 10: Provenance & Lineage Tracking
    # -----------------------------------------------------------------------
    def test_10_provenance_and_lineage(self) -> None:
        """Scenario 10: Verifies provenance tracking and trace correlation."""
        measurement = self.recorder.record_metric(
            metric=PerformanceMetricType.REQUEST_DURATION,
            value=65.0,
            test_case_id=self.test_case_id,
            trace_id=self.trace_id,
            provenance={
                "source_evidence_id": self.evidence_id,
                "instrumentation": "network_cdp_listener",
            },
        )

        self.assertEqual(measurement.trace_id, self.trace_id)
        self.assertEqual(measurement.provenance["source_evidence_id"], self.evidence_id)
        self.assertEqual(measurement.provenance["instrumentation"], "network_cdp_listener")
        self.assertIsNotNone(measurement.timestamp)

    # -----------------------------------------------------------------------
    # Scenario 11: Execution Isolation
    # -----------------------------------------------------------------------
    def test_11_execution_isolation(self) -> None:
        """Scenario 11: Rejects cross-execution pollution."""
        foreign_execution_id = new_execution_id()
        foreign_measurement = PerformanceMeasurement(
            measurement_id=new_performance_measurement_id(),
            execution_id=foreign_execution_id,  # Mismatched execution ID
            metric=PerformanceMetricType.NAVIGATION_DURATION,
            value=300.0,
        )

        with self.assertRaises(TesterLineageError):
            self.recorder.record(foreign_measurement)

    # -----------------------------------------------------------------------
    # Scenario 12: No Automatic Defect Creation & Zero Fixing
    # -----------------------------------------------------------------------
    def test_12_no_automatic_defect_creation(self) -> None:
        """Scenario 12: Strict verification that high values do NOT create defects and fixing is blocked."""
        # Even with high latency, measurement foundation ONLY records values, never creates defects
        m = self.recorder.record_metric(
            metric=PerformanceMetricType.NAVIGATION_DURATION,
            value=15000.0,  # 15 seconds
            threshold_context={"max_authorized_ms": 3000.0},  # Preserved context
        )

        self.assertEqual(m.value, 15000.0)
        self.assertEqual(m.threshold_context["max_authorized_ms"], 3000.0)

        # Strict boundary: create_defect raises TesterBoundaryViolationError
        with self.assertRaises(TesterBoundaryViolationError):
            m.create_defect()

        with self.assertRaises(TesterBoundaryViolationError):
            self.recorder.create_defect()

        # Strict boundary: apply_fix / auto_fix raises TesterBoundaryViolationError
        with self.assertRaises(TesterBoundaryViolationError):
            m.apply_fix()

        with self.assertRaises(TesterBoundaryViolationError):
            self.recorder.auto_fix()

    # -----------------------------------------------------------------------
    # Serialization Roundtrip & Execution Result Forwarding
    # -----------------------------------------------------------------------
    def test_serialization_and_execution_integration(self) -> None:
        """Test serialization roundtrip and TesterExecution result forwarding."""
        measurement = self.recorder.record_metric(
            metric=PerformanceMetricType.PAGE_LOAD_DURATION,
            value=842.0,
            unit=PerformanceMetricUnit.MILLISECONDS,
            test_case_id=self.test_case_id,
            trace_id=self.trace_id,
        )

        # 1. PerformanceMeasurement roundtrip
        m_dict = measurement.to_dict()
        m_restored = PerformanceMeasurement.from_dict(m_dict)
        self.assertEqual(m_restored.measurement_id, measurement.measurement_id)
        self.assertEqual(m_restored.execution_id, measurement.execution_id)
        self.assertEqual(m_restored.metric, PerformanceMetricType.PAGE_LOAD_DURATION)
        self.assertEqual(m_restored.value, 842.0)
        self.assertEqual(m_restored.unit, PerformanceMetricUnit.MILLISECONDS)
        self.assertEqual(m_restored.status, PerformanceMeasurementStatus.AVAILABLE)

        # 2. PerformanceEnvironment roundtrip
        env_dict = self.environment.to_dict()
        env_restored = PerformanceEnvironment.from_dict(env_dict)
        self.assertEqual(env_restored.browser, self.environment.browser)
        self.assertEqual(env_restored.application_revision, self.environment.application_revision)

        # 3. TesterExecution integration
        execution = TesterExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id="mtask-test-1",
            project_id=self.project_id,
            correlation_id="corr-test-1",
        )
        execution.record_performance_measurement(measurement)
        self.assertEqual(len(execution.performance_measurements), 1)

        result = execution.create_result(
            status=TesterResultStatus.COMPLETED,
            summary_for_manager="Performance measurements recorded.",
        )
        self.assertEqual(len(result.performance_measurements), 1)
        self.assertEqual(result.performance_measurements[0].value, 842.0)

        res_dict = result.to_dict()
        self.assertIn("performance_measurements", res_dict)
        self.assertEqual(len(res_dict["performance_measurements"]), 1)


if __name__ == "__main__":
    unittest.main()

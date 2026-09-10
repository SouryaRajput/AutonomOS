from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock

from core.tester.contracts.geometry import ViewportDimensions
from core.tester.contracts.identifiers import (
    LOAD_PERFORMANCE_ID_PREFIX,
    new_execution_id,
    new_load_performance_id,
    new_test_case_id,
    new_work_order_id,
    validate_load_performance_id,
)
from core.tester.contracts.load_performance import (
    LoadPerformanceResult,
    LoadPerformanceTestSpec,
)
from core.tester.contracts.performance import (
    PerformanceEnvironment,
    PerformanceMeasurementBudget,
)
from core.tester.contracts.session import NavigationResult
from core.tester.evaluator.load_performance_evaluator import (
    LoadNavigationPerformanceEvaluator,
)
from core.tester.evaluator.performance_recorder import (
    PerformanceMeasurementRecorder,
)
from core.tester.errors import (
    InvalidTesterIdError,
    TesterBoundaryViolationError,
    TesterLineageError,
)
from core.tester.types import (
    NavigationPerformanceStatus,
    PerformanceInitialState,
    PerformanceMeasurementStatus,
    PerformanceMetricType,
    PerformanceMetricUnit,
)


class TestTesterLoadPerformance(unittest.TestCase):
    """
    Unit test suite for Tester V1 — Phase 7.2: Load & Navigation Performance.
    Tests page load measurement, navigation lifecycle timings, route transitions,
    clean starting states, unavailable metrics, failure modes, bounded repetitions,
    explicit vs absent thresholds, serialization, and strict zero-defect / zero-fixing boundaries.
    """

    def setUp(self) -> None:
        self.execution_id = new_execution_id()
        self.project_id = "proj-checkout-app"
        self.work_order_id = new_work_order_id()
        self.test_case_id = new_test_case_id()

        self.viewport = ViewportDimensions(width=1280, height=800)
        self.environment = PerformanceEnvironment(
            browser="chromium-headless",
            viewport=self.viewport,
            network_configuration="cable_broadband",
            application_revision="git-rev-1234abcd",
            test_execution=self.execution_id,
            os_platform="darwin-arm64",
            hardware_concurrency=8,
            device_memory_gb=16.0,
        )

        self.budget = PerformanceMeasurementBudget(
            max_measurements=50,
            max_samples_per_metric=10,
        )

        self.recorder = PerformanceMeasurementRecorder(
            execution_id=self.execution_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            environment=self.environment,
            budget=self.budget,
        )

        self.evaluator = LoadNavigationPerformanceEvaluator(
            execution_id=self.execution_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            recorder=self.recorder,
        )

    # -----------------------------------------------------------------------
    # Scenario 1: Fast Page Load
    # -----------------------------------------------------------------------
    def test_01_fast_page_load(self) -> None:
        """Scenario 1: Normal fast page load records accurately with SUCCESS status."""
        spec = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/dashboard",
            initial_state=PerformanceInitialState.FRESH_PAGE,
            timeout_seconds=10.0,
        )

        mock_timing = {
            "navigation_duration": 220.0,
            "page_load_duration": 245.0,
            "dom_content_loaded": 110.0,
            "load_event_duration": 135.0,
        }

        result = self.evaluator.measure_page_load(spec, mock_timing=mock_timing)

        self.assertTrue(result.is_success)
        self.assertFalse(result.is_timeout)
        self.assertFalse(result.is_failed)
        self.assertEqual(result.status, NavigationPerformanceStatus.SUCCESS)
        self.assertEqual(result.navigation_duration_ms, 220.0)
        self.assertEqual(result.page_load_duration_ms, 245.0)
        self.assertEqual(result.dom_content_loaded_ms, 110.0)
        self.assertEqual(result.load_event_ms, 135.0)
        self.assertGreater(len(result.measurements), 0)

        # Invariant: Absent threshold remains None
        self.assertIsNone(result.threshold_met)

    # -----------------------------------------------------------------------
    # Scenario 2: Slow Page Load (No Defect Generation)
    # -----------------------------------------------------------------------
    def test_02_slow_page_load(self) -> None:
        """Scenario 2: High latency records accurately without creating defects or errors."""
        spec = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/heavy-report",
            initial_state=PerformanceInitialState.FRESH_PAGE,
            timeout_seconds=30.0,
        )

        mock_timing = {
            "navigation_duration": 8500.0,
            "page_load_duration": 9200.0,
            "dom_content_loaded": 6100.0,
            "load_event_duration": 3100.0,
        }

        result = self.evaluator.measure_page_load(spec, mock_timing=mock_timing)

        # It is still an authorized measurement success
        self.assertTrue(result.is_success)
        self.assertEqual(result.navigation_duration_ms, 8500.0)
        self.assertEqual(result.page_load_duration_ms, 9200.0)

        # Crucial invariant: Phase 7.2 NEVER generates defects or modifies code
        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.create_defect()

        with self.assertRaises(TesterBoundaryViolationError):
            result.create_defect()

    # -----------------------------------------------------------------------
    # Scenario 3: Navigation Timing Lifecycle Metrics
    # -----------------------------------------------------------------------
    def test_03_navigation_timing(self) -> None:
        """Scenario 3: Extracts all supported navigation lifecycle timings reliably."""
        spec = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/products",
        )

        mock_timing = {
            "navigation_duration": 450.0,
            "page_load_duration": 480.0,
            "dom_content_loaded": 210.0,
            "load_event_duration": 270.0,
            "first_contentful_paint": 180.0,
            "largest_contentful_paint": 350.0,
            "application_startup_duration": 600.0,
            "page_readiness_duration": 520.0,
        }

        result = self.evaluator.measure_page_load(spec, mock_timing=mock_timing)

        self.assertEqual(result.status, NavigationPerformanceStatus.SUCCESS)
        self.assertEqual(result.navigation_duration_ms, 450.0)
        self.assertEqual(result.page_load_duration_ms, 480.0)
        self.assertEqual(result.dom_content_loaded_ms, 210.0)
        self.assertEqual(result.load_event_ms, 270.0)
        self.assertEqual(result.first_contentful_paint_ms, 180.0)
        self.assertEqual(result.largest_contentful_paint_ms, 350.0)
        self.assertEqual(result.application_startup_duration_ms, 600.0)
        self.assertEqual(result.page_readiness_duration_ms, 520.0)

        # Check recorded metrics in recorder
        recorded_types = [m.metric for m in result.measurements]
        self.assertIn(PerformanceMetricType.NAVIGATION_DURATION, recorded_types)
        self.assertIn(PerformanceMetricType.PAGE_LOAD_DURATION, recorded_types)
        self.assertIn(PerformanceMetricType.DOM_CONTENT_LOADED, recorded_types)
        self.assertIn(PerformanceMetricType.LOAD_EVENT_DURATION, recorded_types)
        self.assertIn(PerformanceMetricType.FIRST_CONTENTFUL_PAINT, recorded_types)
        self.assertIn(PerformanceMetricType.LARGEST_CONTENTFUL_PAINT, recorded_types)
        self.assertIn(PerformanceMetricType.APPLICATION_STARTUP_DURATION, recorded_types)
        self.assertIn(PerformanceMetricType.PAGE_READINESS_DURATION, recorded_types)

    # -----------------------------------------------------------------------
    # Scenario 4: Route Transition Performance
    # -----------------------------------------------------------------------
    def test_04_route_transition(self) -> None:
        """Scenario 4: Measures in-app client-side navigation or route transition."""
        spec = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/cart",
            route_name="/cart",
            initial_state=PerformanceInitialState.EXISTING_SESSION,
        )

        mock_timing = {
            "route_transition_duration": 115.0,
            "page_readiness_duration": 180.0,
        }

        result = self.evaluator.measure_route_transition(spec, mock_timing=mock_timing)

        self.assertEqual(result.status, NavigationPerformanceStatus.SUCCESS)
        self.assertEqual(result.route_transition_duration_ms, 115.0)
        self.assertEqual(result.page_readiness_duration_ms, 180.0)
        self.assertEqual(len(result.measurements), 2)
        self.assertEqual(
            result.measurements[0].metric, PerformanceMetricType.ROUTE_TRANSITION_DURATION
        )
        self.assertEqual(
            result.measurements[1].metric, PerformanceMetricType.PAGE_READINESS_DURATION
        )

    # -----------------------------------------------------------------------
    # Scenario 5: Missing Performance Timing API
    # -----------------------------------------------------------------------
    def test_05_missing_performance_api(self) -> None:
        """Scenario 5: Runtime without timing API records MEASUREMENT_UNAVAILABLE without synthetic numbers."""
        spec = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/simple",
        )

        mock_timing = {"has_performance_api": False}

        result = self.evaluator.measure_page_load(spec, mock_timing=mock_timing)

        self.assertEqual(
            result.status, NavigationPerformanceStatus.MEASUREMENT_UNAVAILABLE
        )
        self.assertTrue(result.is_unavailable)
        self.assertFalse(result.is_success)
        self.assertEqual(len(result.measurements), 1)
        self.assertEqual(
            result.measurements[0].status, PerformanceMeasurementStatus.UNAVAILABLE
        )
        self.assertIsNone(result.measurements[0].value)

    # -----------------------------------------------------------------------
    # Scenario 6: Timeout Handling
    # -----------------------------------------------------------------------
    def test_06_timeout(self) -> None:
        """Scenario 6: Navigation timeout produces TIMEOUT status."""
        spec = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/slow-api",
            timeout_seconds=5.0,
            threshold_ms=2000.0,
        )

        # Test simulated timeout on page load
        result = self.evaluator.measure_page_load(spec, mock_failure="timeout")
        self.assertEqual(result.status, NavigationPerformanceStatus.TIMEOUT)
        self.assertTrue(result.is_timeout)
        self.assertFalse(result.is_success)
        self.assertIn("timed out", result.error_message)
        self.assertFalse(result.threshold_met)

        # Test simulated timeout on route transition
        res_route = self.evaluator.measure_route_transition(spec, mock_failure="timeout")
        self.assertEqual(res_route.status, NavigationPerformanceStatus.TIMEOUT)
        self.assertTrue(res_route.is_timeout)

    # -----------------------------------------------------------------------
    # Scenario 7: Failed Navigation / Application Load Failed
    # -----------------------------------------------------------------------
    def test_07_failed_navigation(self) -> None:
        """Scenario 7: Network errors produce NAVIGATION_FAILED or APPLICATION_LOAD_FAILED."""
        spec = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://non-existent-host.local",
            threshold_ms=1000.0,
        )

        # Navigation failed (host unreachable, DNS failure)
        res_nav_fail = self.evaluator.measure_page_load(
            spec, mock_failure="navigation_failed"
        )
        self.assertEqual(
            res_nav_fail.status, NavigationPerformanceStatus.NAVIGATION_FAILED
        )
        self.assertTrue(res_nav_fail.is_failed)
        self.assertFalse(res_nav_fail.is_success)
        self.assertFalse(res_nav_fail.threshold_met)

        # Application load failed (crash, 500 error)
        res_app_fail = self.evaluator.measure_page_load(
            spec, mock_failure="app_crash"
        )
        self.assertEqual(
            res_app_fail.status, NavigationPerformanceStatus.APPLICATION_LOAD_FAILED
        )
        self.assertTrue(res_app_fail.is_failed)

    # -----------------------------------------------------------------------
    # Scenario 8: Explicit Threshold Evaluation
    # -----------------------------------------------------------------------
    def test_08_explicit_threshold(self) -> None:
        """Scenario 8: Explicit threshold evaluates threshold_met=True (pass) and False (fail)."""
        # Case A: Fast page load under threshold (pass)
        spec_pass = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/fast",
            threshold_ms=1000.0,
        )
        result_pass = self.evaluator.measure_page_load(
            spec_pass,
            mock_timing={"navigation_duration": 400.0, "page_load_duration": 450.0},
        )
        self.assertEqual(result_pass.threshold_ms, 1000.0)
        self.assertIs(result_pass.threshold_met, True)

        # Case B: Slow page load exceeding threshold (fail)
        spec_fail = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/slow",
            threshold_ms=1000.0,
        )
        result_fail = self.evaluator.measure_page_load(
            spec_fail,
            mock_timing={"navigation_duration": 1800.0, "page_load_duration": 2100.0},
        )
        self.assertEqual(result_fail.threshold_ms, 1000.0)
        self.assertIs(result_fail.threshold_met, False)

        # Case C: Route transition threshold evaluation
        spec_route_pass = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/settings",
            threshold_ms=200.0,
        )
        res_r_pass = self.evaluator.measure_route_transition(
            spec_route_pass, mock_timing={"route_transition_duration": 120.0}
        )
        self.assertIs(res_r_pass.threshold_met, True)

        spec_route_fail = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/settings",
            threshold_ms=100.0,
        )
        res_r_fail = self.evaluator.measure_route_transition(
            spec_route_fail, mock_timing={"route_transition_duration": 150.0}
        )
        self.assertIs(res_r_fail.threshold_met, False)

    # -----------------------------------------------------------------------
    # Scenario 9: No Threshold (Never Invent Thresholds)
    # -----------------------------------------------------------------------
    def test_09_no_threshold(self) -> None:
        """Scenario 9: When threshold is omitted, threshold_met remains None (never invent thresholds)."""
        spec = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/overview",
            threshold_ms=None,
        )

        result = self.evaluator.measure_page_load(
            spec,
            mock_timing={"navigation_duration": 3500.0, "page_load_duration": 4000.0},
        )

        self.assertIsNone(result.threshold_ms)
        self.assertIsNone(result.threshold_met)

    # -----------------------------------------------------------------------
    # Scenario 10: Repeated Bounded Measurements
    # -----------------------------------------------------------------------
    def test_10_repeated_bounded_measurements(self) -> None:
        """Scenario 10: Bounded repetitions collect multiple samples within budget."""
        spec = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/feed",
            repeat_count=3,
        )

        result = self.evaluator.measure_page_load(
            spec,
            mock_timing={"navigation_duration": 300.0, "page_load_duration": 320.0},
        )

        self.assertEqual(result.sample_count, 3)
        # Each repeat records 2 metrics (nav and page_load)
        self.assertEqual(len(result.measurements), 6)

    # -----------------------------------------------------------------------
    # Scenario 11: Budget Exhaustion Halts Repetitions
    # -----------------------------------------------------------------------
    def test_11_budget_exhaustion(self) -> None:
        """Scenario 11: Bounded repetition halts cleanly when measurement budget is exhausted."""
        tight_recorder = PerformanceMeasurementRecorder(
            execution_id=self.execution_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            environment=self.environment,
            budget=PerformanceMeasurementBudget(max_measurements=4, max_samples_per_metric=4),
        )
        tight_evaluator = LoadNavigationPerformanceEvaluator(
            execution_id=self.execution_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            recorder=tight_recorder,
        )

        spec = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/feed",
            repeat_count=10,  # 10 repeats would attempt 20 measurements
        )

        result = tight_evaluator.measure_page_load(
            spec,
            mock_timing={"navigation_duration": 300.0, "page_load_duration": 320.0},
        )

        # Budget allows max 4 measurements, halts at 2 samples (4 measurements)
        self.assertLessEqual(len(tight_recorder.measurements), 4)
        self.assertEqual(result.sample_count, 2)
        self.assertEqual(len(result.measurements), 4)

    # -----------------------------------------------------------------------
    # Scenario 12: Provenance & Identifiers
    # -----------------------------------------------------------------------
    def test_12_provenance(self) -> None:
        """Scenario 12: Validates lineage, prefixes, timestamps, and target url provenance."""
        spec = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/analytics",
            initial_state=PerformanceInitialState.FRESH_PAGE,
        )

        result = self.evaluator.measure_page_load(
            spec,
            mock_timing={"navigation_duration": 500.0, "page_load_duration": 520.0},
        )

        self.assertTrue(result.load_performance_id.startswith(LOAD_PERFORMANCE_ID_PREFIX))
        validate_load_performance_id(result.load_performance_id)
        self.assertEqual(result.execution_id, self.execution_id)
        self.assertEqual(result.test_case_id, self.test_case_id)
        self.assertEqual(result.target_url, "http://localhost:3000/analytics")
        self.assertEqual(result.initial_state, PerformanceInitialState.FRESH_PAGE)
        self.assertIn("started_at", result.timestamps)
        self.assertIn("completed_at", result.timestamps)
        self.assertEqual(result.provenance["target_url"], "http://localhost:3000/analytics")
        self.assertEqual(result.provenance["initial_state"], PerformanceInitialState.FRESH_PAGE.value)

    # -----------------------------------------------------------------------
    # Scenario 13: Serialization Roundtrip
    # -----------------------------------------------------------------------
    def test_13_serialization_roundtrip(self) -> None:
        """Scenario 13: LoadPerformanceTestSpec and LoadPerformanceResult roundtrip through to_dict/from_dict."""
        spec = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/account",
            route_name="/account",
            initial_state=PerformanceInitialState.FRESH_CONTEXT,
            repeat_count=2,
            timeout_seconds=15.0,
            threshold_ms=800.0,
        )

        spec_dict = spec.to_dict()
        spec_loaded = LoadPerformanceTestSpec.from_dict(spec_dict)
        self.assertEqual(spec_loaded.execution_id, spec.execution_id)
        self.assertEqual(spec_loaded.target_url, spec.target_url)
        self.assertEqual(spec_loaded.route_name, spec.route_name)
        self.assertEqual(spec_loaded.initial_state, PerformanceInitialState.FRESH_CONTEXT)
        self.assertEqual(spec_loaded.repeat_count, 2)
        self.assertEqual(spec_loaded.timeout_seconds, 15.0)
        self.assertEqual(spec_loaded.threshold_ms, 800.0)

        result = self.evaluator.measure_page_load(
            spec,
            mock_timing={"navigation_duration": 400.0, "page_load_duration": 420.0},
        )
        res_dict = result.to_dict()
        res_loaded = LoadPerformanceResult.from_dict(res_dict)

        self.assertEqual(res_loaded.load_performance_id, result.load_performance_id)
        self.assertEqual(res_loaded.execution_id, result.execution_id)
        self.assertEqual(res_loaded.status, NavigationPerformanceStatus.SUCCESS)
        self.assertEqual(res_loaded.navigation_duration_ms, 400.0)
        self.assertEqual(res_loaded.page_load_duration_ms, 420.0)
        self.assertIs(res_loaded.threshold_met, True)
        self.assertEqual(res_loaded.threshold_ms, 800.0)

    # -----------------------------------------------------------------------
    # Scenario 14: Clean Starting State Handling
    # -----------------------------------------------------------------------
    def test_14_clean_state_handling(self) -> None:
        """Scenario 14: BrowserSession navigation ensures clean starting states."""
        mock_session = MagicMock()
        mock_nav_result = NavigationResult(
            url="http://localhost:3000/orders",
            status_code=200,
            duration_ms=310.0,
        )
        mock_session.navigate.return_value = mock_nav_result

        # FRESH_PAGE navigates to about:blank first
        spec_fresh = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/orders",
            initial_state=PerformanceInitialState.FRESH_PAGE,
            timeout_seconds=15.0,
        )
        self.evaluator.measure_page_load(spec_fresh, session=mock_session)

        self.assertEqual(mock_session.navigate.call_count, 2)
        mock_session.navigate.assert_any_call("about:blank")
        mock_session.navigate.assert_any_call("http://localhost:3000/orders", timeout_seconds=15.0)

        # EXISTING_SESSION does not reset to blank
        mock_session.reset_mock()
        spec_existing = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/orders",
            initial_state=PerformanceInitialState.EXISTING_SESSION,
            timeout_seconds=15.0,
        )
        self.evaluator.measure_page_load(spec_existing, session=mock_session)
        self.assertEqual(mock_session.navigate.call_count, 1)
        mock_session.navigate.assert_called_once_with(
            "http://localhost:3000/orders", timeout_seconds=15.0
        )

    # -----------------------------------------------------------------------
    # Scenario 15: Zero-Fixing and Zero-Defect Guards
    # -----------------------------------------------------------------------
    def test_15_zero_fixing_and_defect_guard(self) -> None:
        """Scenario 15: Evaluator, Spec, and Result strictly prohibit code modifications and defect generation."""
        spec = LoadPerformanceTestSpec(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/test",
        )
        result = self.evaluator.measure_page_load(
            spec, mock_timing={"navigation_duration": 100.0}
        )

        # Evaluator invariant guards
        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.apply_fix()
        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.auto_fix()
        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.create_defect()

        # Spec invariant guards
        with self.assertRaises(TesterBoundaryViolationError):
            spec.apply_fix()
        with self.assertRaises(TesterBoundaryViolationError):
            spec.create_defect()

        # Result invariant guards
        with self.assertRaises(TesterBoundaryViolationError):
            result.apply_fix()
        with self.assertRaises(TesterBoundaryViolationError):
            result.auto_fix()
        with self.assertRaises(TesterBoundaryViolationError):
            result.create_defect()

    # -----------------------------------------------------------------------
    # Scenario 16: Lineage and Identifier Validation
    # -----------------------------------------------------------------------
    def test_16_lineage_validation(self) -> None:
        """Scenario 16: Mismatched execution IDs raise TesterLineageError, invalid IDs raise InvalidTesterIdError."""
        foreign_execution_id = new_execution_id()
        spec = LoadPerformanceTestSpec(
            execution_id=foreign_execution_id,
            test_case_id=self.test_case_id,
            target_url="http://localhost:3000/lineage-test",
        )

        with self.assertRaises(TesterLineageError):
            LoadPerformanceResult(
                load_performance_id=new_load_performance_id(),
                execution_id=self.execution_id,  # Mismatched!
                spec=spec,
                status=NavigationPerformanceStatus.SUCCESS,
            )

        with self.assertRaises(InvalidTesterIdError):
            LoadPerformanceResult(
                load_performance_id="invalid-prefix-12345",
                execution_id=self.execution_id,
                spec=LoadPerformanceTestSpec(
                    execution_id=self.execution_id,
                    test_case_id=self.test_case_id,
                    target_url="http://localhost:3000/test",
                ),
                status=NavigationPerformanceStatus.SUCCESS,
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock

from core.tester.contracts.geometry import ViewportDimensions
from core.tester.contracts.identifiers import (
    INTERACTION_PERFORMANCE_ID_PREFIX,
    new_evidence_id,
    new_execution_id,
    new_interaction_performance_id,
    new_test_case_id,
    new_trace_id,
    new_work_order_id,
    validate_interaction_performance_id,
)
from core.tester.contracts.interaction import InteractionResult, InteractionTarget
from core.tester.contracts.interaction_performance import (
    InteractionPerformanceResult,
    InteractionPerformanceSpec,
)
from core.tester.contracts.performance import (
    PerformanceEnvironment,
    PerformanceMeasurementBudget,
)
from core.tester.evaluator.interaction_performance_evaluator import (
    InteractionPerformanceEvaluator,
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
    InteractionPerformanceStatus,
    InteractionStatus,
    PerformanceMeasurementStatus,
    PerformanceMetricType,
    TesterActionType,
)


class TestTesterInteractionPerformance(unittest.TestCase):
    """
    Unit test suite for Tester V1 — Phase 7.3: Interaction Performance.
    Tests user action to observable response latency, explicit vs absent thresholds,
    state transition success and missing states, timeouts, failure classification coordination,
    bounded repeated measurements, budget exhaustion, evidence linkage, provenance,
    and strict zero-defect / zero-fixing boundaries.
    """

    def setUp(self) -> None:
        self.execution_id = new_execution_id()
        self.project_id = "proj-e-commerce"
        self.work_order_id = new_work_order_id()
        self.test_case_id = new_test_case_id()
        self.evidence_id = new_evidence_id()
        self.trace_id = new_trace_id()

        self.viewport = ViewportDimensions(width=1440, height=900)
        self.environment = PerformanceEnvironment(
            browser="chromium-headless",
            viewport=self.viewport,
            network_configuration="unthrottled",
            application_revision="rev-7.3-alpha",
            test_execution=self.execution_id,
        )

        self.budget = PerformanceMeasurementBudget(
            max_measurements=60,
            max_samples_per_metric=20,
        )

        self.recorder = PerformanceMeasurementRecorder(
            execution_id=self.execution_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            environment=self.environment,
            budget=self.budget,
        )

        self.evaluator = InteractionPerformanceEvaluator(
            execution_id=self.execution_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            recorder=self.recorder,
        )

    # -----------------------------------------------------------------------
    # Scenario 1: Fast Interaction
    # -----------------------------------------------------------------------
    def test_01_fast_interaction(self) -> None:
        """Scenario 1: Fast button click records accurately with SUCCESS status."""
        spec = InteractionPerformanceSpec(
            target="#btn-add-to-cart",
            action_type=TesterActionType.CLICK,
            expected_state=".cart-badge-updated",
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        mock_timing = {
            "action_execution_duration": 25.0,
            "state_readiness_duration": 35.0,
            "interaction_duration": 60.0,
        }

        result = self.evaluator.measure_interaction(spec, mock_timing=mock_timing)

        self.assertTrue(result.is_success)
        self.assertFalse(result.is_timeout)
        self.assertFalse(result.is_failed)
        self.assertEqual(result.status, InteractionPerformanceStatus.SUCCESS)
        self.assertEqual(result.action_execution_duration_ms, 25.0)
        self.assertEqual(result.state_readiness_duration_ms, 35.0)
        self.assertEqual(result.interaction_duration_ms, 60.0)
        self.assertIsNone(result.threshold_met)  # No threshold configured

        # Measurements recorded in recorder
        recorded_types = [m.metric for m in result.measurements]
        self.assertIn(PerformanceMetricType.INTERACTION_DURATION, recorded_types)
        self.assertIn(PerformanceMetricType.STATE_READINESS_DURATION, recorded_types)
        self.assertIn(PerformanceMetricType.ACTION_EXECUTION_DURATION, recorded_types)

    # -----------------------------------------------------------------------
    # Scenario 2: Slow Interaction (No Defect Invention)
    # -----------------------------------------------------------------------
    def test_02_slow_interaction(self) -> None:
        """Scenario 2: Slow interaction records latency accurately without creating defects."""
        spec = InteractionPerformanceSpec(
            target="#btn-submit-order",
            action_type=TesterActionType.CLICK,
            expected_state="#order-confirmation",
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        mock_timing = {
            "action_execution_duration": 120.0,
            "state_readiness_duration": 4080.0,
            "interaction_duration": 4200.0,
        }

        result = self.evaluator.measure_interaction(spec, mock_timing=mock_timing)

        self.assertTrue(result.is_success)
        self.assertEqual(result.interaction_duration_ms, 4200.0)
        self.assertEqual(result.state_readiness_duration_ms, 4080.0)

        # Invariant: Evaluator and Result never classify defects or apply fixes
        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.create_defect()
        with self.assertRaises(TesterBoundaryViolationError):
            result.create_defect()

    # -----------------------------------------------------------------------
    # Scenario 3: Explicit Latency Threshold
    # -----------------------------------------------------------------------
    def test_03_explicit_latency_threshold(self) -> None:
        """Scenario 3: Explicit threshold evaluates threshold_met=True (pass) and False (fail)."""
        # Case A: Fast interaction meeting threshold (<= 100ms)
        spec_pass = InteractionPerformanceSpec(
            target="#btn-toggle",
            action_type=TesterActionType.CLICK,
            expected_state="#panel-open",
            threshold_ms=100.0,
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )
        res_pass = self.evaluator.measure_interaction(
            spec_pass, mock_timing={"interaction_duration": 65.0}
        )
        self.assertEqual(res_pass.threshold_ms, 100.0)
        self.assertIs(res_pass.threshold_met, True)

        # Case B: Slow interaction exceeding threshold (> 100ms)
        spec_fail = InteractionPerformanceSpec(
            target="#btn-toggle",
            action_type=TesterActionType.CLICK,
            expected_state="#panel-open",
            threshold_ms=100.0,
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )
        res_fail = self.evaluator.measure_interaction(
            spec_fail, mock_timing={"interaction_duration": 280.0}
        )
        self.assertEqual(res_fail.threshold_ms, 100.0)
        self.assertIs(res_fail.threshold_met, False)

    # -----------------------------------------------------------------------
    # Scenario 4: No Threshold (Never Invent "Acceptable" Latency)
    # -----------------------------------------------------------------------
    def test_04_no_threshold(self) -> None:
        """Scenario 4: When threshold is omitted, threshold_met remains strictly None."""
        spec = InteractionPerformanceSpec(
            target="#input-search",
            action_type=TesterActionType.TYPE,
            expected_state="#search-results",
            threshold_ms=None,
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = self.evaluator.measure_interaction(
            spec, mock_timing={"interaction_duration": 850.0}
        )

        self.assertIsNone(result.threshold_ms)
        self.assertIsNone(result.threshold_met)

    # -----------------------------------------------------------------------
    # Scenario 5: Successful State Transition (USER ACTION -> OBSERVABLE RESPONSE)
    # -----------------------------------------------------------------------
    def test_05_successful_state_transition(self) -> None:
        """Scenario 5: Complete interaction measures both action completion and state readiness."""
        spec = InteractionPerformanceSpec(
            target="#btn-filter",
            action_type=TesterActionType.CLICK,
            expected_state="#grid-filtered",
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        mock_timing = {
            "action_execution_duration": 30.0,
            "state_readiness_duration": 110.0,
            "interaction_duration": 140.0,
        }

        result = self.evaluator.measure_interaction(spec, mock_timing=mock_timing)

        self.assertEqual(result.status, InteractionPerformanceStatus.SUCCESS)
        self.assertIsNotNone(result.action_started_at)
        self.assertIsNotNone(result.action_completed_at)
        self.assertIsNotNone(result.state_ready_at)
        self.assertEqual(result.action_execution_duration_ms, 30.0)
        self.assertEqual(result.state_readiness_duration_ms, 110.0)
        self.assertEqual(result.interaction_duration_ms, 140.0)

    # -----------------------------------------------------------------------
    # Scenario 6: Missing State Transition
    # -----------------------------------------------------------------------
    def test_06_missing_state_transition(self) -> None:
        """Scenario 6: Action succeeds but expected state change never emerges."""
        spec = InteractionPerformanceSpec(
            target="#btn-save-settings",
            action_type=TesterActionType.CLICK,
            expected_state="#toast-saved",
            threshold_ms=500.0,
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = self.evaluator.measure_interaction(
            spec, mock_failure="missing_state"
        )

        self.assertEqual(
            result.status, InteractionPerformanceStatus.MISSING_STATE_TRANSITION
        )
        self.assertTrue(result.is_missing_transition)
        self.assertTrue(result.is_failed)
        self.assertFalse(result.is_success)
        self.assertFalse(result.threshold_met)
        self.assertIn("never emerged", result.error_message)

        # Check defect classification coordination
        guidance = self.evaluator.evaluate_failure_classification(result)
        self.assertEqual(guidance["classification"], "FUNCTIONAL_STATE_TRANSITION_FAILURE")

    # -----------------------------------------------------------------------
    # Scenario 7: Timeout
    # -----------------------------------------------------------------------
    def test_07_timeout(self) -> None:
        """Scenario 7: Interaction or state readiness wait exceeds timeout."""
        spec = InteractionPerformanceSpec(
            target="#btn-generate-report",
            action_type=TesterActionType.CLICK,
            expected_state="#report-ready",
            timeout_seconds=5.0,
            threshold_ms=1000.0,
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = self.evaluator.measure_interaction(spec, mock_failure="timeout")

        self.assertEqual(result.status, InteractionPerformanceStatus.TIMEOUT)
        self.assertTrue(result.is_timeout)
        self.assertTrue(result.is_failed)
        self.assertFalse(result.is_success)
        self.assertFalse(result.threshold_met)
        self.assertIn("timed out", result.error_message)

        # Check defect classification coordination
        guidance = self.evaluator.evaluate_failure_classification(result)
        self.assertEqual(guidance["classification"], "TIMEOUT")

    # -----------------------------------------------------------------------
    # Scenario 8: Failed Interaction (Click / Dispatch Error)
    # -----------------------------------------------------------------------
    def test_08_failed_interaction(self) -> None:
        """Scenario 8: Target disabled or unclickable produces INTERACTION_FAILED."""
        spec = InteractionPerformanceSpec(
            target="#disabled-btn",
            action_type=TesterActionType.CLICK,
            expected_state="#modal-opened",
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = self.evaluator.measure_interaction(spec, mock_failure="failed")

        self.assertEqual(
            result.status, InteractionPerformanceStatus.INTERACTION_FAILED
        )
        self.assertTrue(result.is_failed)
        self.assertFalse(result.is_success)
        self.assertIn("failed to execute", result.error_message)

        # Check defect classification coordination
        guidance = self.evaluator.evaluate_failure_classification(result)
        self.assertEqual(guidance["classification"], "RUNTIME_INTERACTION_FAILURE")

    # -----------------------------------------------------------------------
    # Scenario 9: Bounded Repeated Measurements
    # -----------------------------------------------------------------------
    def test_09_bounded_repeated_measurement(self) -> None:
        """Scenario 9: Bounded repetitions collect multiple samples within budget."""
        spec = InteractionPerformanceSpec(
            target="#btn-increment",
            action_type=TesterActionType.CLICK,
            expected_state="#counter-value",
            repeat_count=3,
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        mock_timing = {
            "action_execution_duration": 15.0,
            "state_readiness_duration": 25.0,
            "interaction_duration": 40.0,
        }

        result = self.evaluator.measure_interaction(spec, mock_timing=mock_timing)

        self.assertEqual(result.sample_count, 3)
        # Each repeat records 3 metrics (interaction, state_readiness, action_execution)
        self.assertEqual(len(result.measurements), 9)

    # -----------------------------------------------------------------------
    # Scenario 10: Budget Exhaustion Halts Repetitions
    # -----------------------------------------------------------------------
    def test_10_budget_exhaustion(self) -> None:
        """Scenario 10: Bounded repetition halts cleanly when measurement budget is exhausted."""
        tight_recorder = PerformanceMeasurementRecorder(
            execution_id=self.execution_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            environment=self.environment,
            budget=PerformanceMeasurementBudget(max_measurements=4, max_samples_per_metric=4),
        )
        tight_evaluator = InteractionPerformanceEvaluator(
            execution_id=self.execution_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            recorder=tight_recorder,
        )

        spec = InteractionPerformanceSpec(
            target="#btn-repeat",
            action_type=TesterActionType.CLICK,
            repeat_count=10,  # 10 repeats would attempt 30 measurements
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = tight_evaluator.measure_interaction(
            spec,
            mock_timing={"interaction_duration": 50.0},
        )

        # Budget allows max 4 measurements, halts gracefully
        self.assertLessEqual(len(tight_recorder.measurements), 4)
        self.assertLessEqual(result.sample_count, 2)

    # -----------------------------------------------------------------------
    # Scenario 11: Evidence and Trace Linkage
    # -----------------------------------------------------------------------
    def test_11_evidence_linkage(self) -> None:
        """Scenario 11: Trace and evidence IDs linked to measurements and result."""
        spec = InteractionPerformanceSpec(
            target="#btn-checkout",
            action_type=TesterActionType.CLICK,
            expected_state="#checkout-page",
            evidence_id=self.evidence_id,
            trace_id=self.trace_id,
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = self.evaluator.measure_interaction(
            spec, mock_timing={"interaction_duration": 120.0}
        )

        self.assertEqual(result.evidence_id, self.evidence_id)
        self.assertEqual(result.trace_id, self.trace_id)

        # First measurement has linked trace_id and evidence_id in provenance
        m = result.measurements[0]
        self.assertEqual(m.trace_id, self.trace_id)
        self.assertEqual(m.provenance.get("evidence_id"), self.evidence_id)

    # -----------------------------------------------------------------------
    # Scenario 12: Provenance & Identifiers
    # -----------------------------------------------------------------------
    def test_12_provenance(self) -> None:
        """Scenario 12: Validates lineage, prefixes, timestamps, and target provenance."""
        spec = InteractionPerformanceSpec(
            target="#btn-apply-promo",
            action_type=TesterActionType.CLICK,
            expected_state="#discount-applied",
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = self.evaluator.measure_interaction(
            spec, mock_timing={"interaction_duration": 75.0}
        )

        self.assertTrue(
            result.interaction_performance_id.startswith(INTERACTION_PERFORMANCE_ID_PREFIX)
        )
        validate_interaction_performance_id(result.interaction_performance_id)
        self.assertEqual(result.execution_id, self.execution_id)
        self.assertEqual(result.test_case_id, self.test_case_id)
        self.assertEqual(result.action_type, TesterActionType.CLICK)
        self.assertEqual(result.target, "#btn-apply-promo")
        self.assertIn("started_at", result.timestamps)
        self.assertIn("completed_at", result.timestamps)
        self.assertEqual(result.provenance["target"], "#btn-apply-promo")
        self.assertEqual(result.provenance["action_type"], TesterActionType.CLICK.value)

    # -----------------------------------------------------------------------
    # Scenario 13: Serialization Roundtrip
    # -----------------------------------------------------------------------
    def test_13_serialization_roundtrip(self) -> None:
        """Scenario 13: Spec and Result serialize cleanly to and from dict."""
        spec = InteractionPerformanceSpec(
            target=InteractionTarget(selector="#btn-login"),
            action_type=TesterActionType.CLICK,
            expected_state="#user-profile",
            threshold_ms=250.0,
            timeout_seconds=8.0,
            repeat_count=2,
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            evidence_id=self.evidence_id,
            trace_id=self.trace_id,
        )

        spec_dict = spec.to_dict()
        spec_loaded = InteractionPerformanceSpec.from_dict(spec_dict)
        self.assertEqual(spec_loaded.action_type, TesterActionType.CLICK)
        self.assertEqual(spec_loaded.threshold_ms, 250.0)
        self.assertEqual(spec_loaded.repeat_count, 2)
        self.assertEqual(spec_loaded.execution_id, self.execution_id)

        result = self.evaluator.measure_interaction(
            spec,
            mock_timing={
                "action_execution_duration": 20.0,
                "state_readiness_duration": 80.0,
                "interaction_duration": 100.0,
            },
        )
        res_dict = result.to_dict()
        res_loaded = InteractionPerformanceResult.from_dict(res_dict)

        self.assertEqual(res_loaded.interaction_performance_id, result.interaction_performance_id)
        self.assertEqual(res_loaded.status, InteractionPerformanceStatus.SUCCESS)
        self.assertEqual(res_loaded.interaction_duration_ms, 100.0)
        self.assertIs(res_loaded.threshold_met, True)
        self.assertEqual(res_loaded.threshold_ms, 250.0)

    # -----------------------------------------------------------------------
    # Scenario 14: Live BrowserSession Interaction
    # -----------------------------------------------------------------------
    def test_14_live_session_interaction(self) -> None:
        """Scenario 14: Dispatches through live BrowserSession abstraction."""
        mock_session = MagicMock()
        mock_action_result = InteractionResult(
            action_id="tact-click-1",
            execution_id=self.execution_id,
            runtime_id="trun-test-1",
            action_type=TesterActionType.CLICK,
            status=InteractionStatus.SUCCESS,
            target="#btn-menu",
            duration_ms=42.0,
        )
        mock_session.click.return_value = mock_action_result

        spec = InteractionPerformanceSpec(
            target="#btn-menu",
            action_type=TesterActionType.CLICK,
            expected_state="#nav-drawer",
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )

        result = self.evaluator.measure_interaction(spec, session=mock_session)

        self.assertTrue(result.is_success)
        mock_session.click.assert_called_once_with("#btn-menu")
        self.assertEqual(result.action_execution_duration_ms, 42.0)

    # -----------------------------------------------------------------------
    # Scenario 15: Zero-Fixing and Defect Invariant Guards
    # -----------------------------------------------------------------------
    def test_15_zero_fixing_and_defect_guard(self) -> None:
        """Scenario 15: Evaluator, Spec, and Result strictly prohibit code modifications and defect generation."""
        spec = InteractionPerformanceSpec(
            target="#btn-test",
            action_type=TesterActionType.CLICK,
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
        )
        result = self.evaluator.measure_interaction(
            spec, mock_timing={"interaction_duration": 50.0}
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
            spec.auto_fix()
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
        """Scenario 16: Mismatched IDs raise TesterLineageError; invalid formats raise InvalidTesterIdError."""
        foreign_id = new_execution_id()
        spec = InteractionPerformanceSpec(
            target="#btn-test",
            action_type=TesterActionType.CLICK,
            execution_id=foreign_id,  # Mismatch!
            test_case_id=self.test_case_id,
        )

        with self.assertRaises(TesterLineageError):
            InteractionPerformanceResult(
                interaction_performance_id=new_interaction_performance_id(),
                execution_id=self.execution_id,
                spec=spec,
                status=InteractionPerformanceStatus.SUCCESS,
            )

        with self.assertRaises(InvalidTesterIdError):
            InteractionPerformanceResult(
                interaction_performance_id="invalid-id-format",
                execution_id=self.execution_id,
                spec=InteractionPerformanceSpec(
                    target="#btn-test",
                    execution_id=self.execution_id,
                ),
                status=InteractionPerformanceStatus.SUCCESS,
            )


if __name__ == "__main__":
    unittest.main()

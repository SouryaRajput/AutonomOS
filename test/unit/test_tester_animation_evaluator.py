from __future__ import annotations

import unittest

from core.tester.contracts.animation import (
    AnimationAssertion,
    AnimationAssertionResult,
    AnimationEvaluationResult,
    AnimationStateExpectation,
)
from core.tester.contracts.identifiers import (
    new_evidence_id,
    new_execution_id,
    new_frame_observation_id,
    new_step_id,
    new_test_case_id,
)
from core.tester.contracts.video_frame import (
    FrameExtractionStatus,
    FramePosition,
    VideoFrameObservation,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.evaluator.animation_evaluator import (
    MAX_EVALUATION_FRAMES,
    AnimationEvaluator,
)
from core.tester.types import (
    AnimationCheckType,
    AnimationEvaluationStatus,
    DefectSeverity,
    DefectType,
    FindingCategory,
)


class TestTesterAnimationEvaluator(unittest.TestCase):
    """
    Unit test suite for Phase 6.5: Animation & Transition Evaluation.
    Covers all 10 required test scenarios + boundary invariants & serialization:
    1. successful transition
    2. missing transition
    3. stuck animation
    4. incomplete final state
    5. intentional no-animation behavior
    6. insufficient frame evidence
    7. bounded frame analysis
    8. subjective motion preference
    9. evidence provenance
    10. runtime failure
    11. zero fixing guard
    12. contract serialization roundtrip
    """

    def setUp(self) -> None:
        self.execution_id = new_execution_id()
        self.project_id = "test-anim-proj"
        self.work_order_id = "two-anim-001"
        self.video_evidence_id = new_evidence_id()
        self.test_case_id = new_test_case_id()
        self.test_step_id = new_step_id()
        self.evaluator = AnimationEvaluator(
            execution_id=self.execution_id,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            max_evaluation_frames=MAX_EVALUATION_FRAMES,
        )

    def _make_frame(
        self,
        index: int,
        timestamp_ms: float,
        state: str,
        element_id: str = "modal_dialog",
        status: FrameExtractionStatus = FrameExtractionStatus.SUCCESS,
        error_message: str | None = None,
        execution_id: str | None = None,
        project_id: str | None = None,
    ) -> VideoFrameObservation:
        return VideoFrameObservation(
            frame_observation_id=new_frame_observation_id(),
            execution_id=execution_id or self.execution_id,
            project_id=project_id or self.project_id,
            source_video_evidence_id=self.video_evidence_id,
            frame_position=FramePosition(timestamp_ms=timestamp_ms, frame_index=index),
            status=status,
            frame_evidence_id=new_evidence_id(),
            error_message=error_message,
            observation_metadata={
                "element_id": element_id,
                "state": state,
                element_id: {"state": state},
            },
            description=f"Element '{element_id}' is in '{state}' state at {timestamp_ms}ms.",
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
        )

    def test_01_successful_transition(self) -> None:
        """Scenario 1: Element cleanly transitions from initial state to expected final state."""
        frames = [
            self._make_frame(0, 0.0, "closed"),
            self._make_frame(1, 150.0, "animating"),
            self._make_frame(2, 300.0, "open"),
        ]
        exp = AnimationStateExpectation(
            initial_state="closed",
            expected_final_state="open",
            target_element_id="modal_dialog",
            require_intermediate_progress=True,
        )

        res = self.evaluator.evaluate_transition(
            expectation=exp,
            frame_observations=frames,
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
        )

        self.assertTrue(res.is_pass)
        self.assertEqual(res.status, AnimationEvaluationStatus.PASS)
        self.assertEqual(res.initial_state_observed, "closed")
        self.assertEqual(res.final_state_observed, "open")
        self.assertTrue(res.transition_completed)
        self.assertIsNone(res.defect)
        self.assertGreater(len(res.evidence_ids), 0)

    def test_02_missing_transition(self) -> None:
        """Scenario 2: Element remains in initial state across all frames without moving."""
        frames = [
            self._make_frame(0, 0.0, "closed"),
            self._make_frame(1, 150.0, "closed"),
            self._make_frame(2, 300.0, "closed"),
        ]
        exp = AnimationStateExpectation(
            initial_state="closed",
            expected_final_state="open",
            target_element_id="modal_dialog",
        )

        res = self.evaluator.evaluate_transition(
            expectation=exp,
            frame_observations=frames,
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
        )

        self.assertTrue(res.is_fail)
        self.assertEqual(res.status, AnimationEvaluationStatus.FAIL)
        self.assertFalse(res.transition_completed)
        self.assertIsNotNone(res.defect)
        self.assertEqual(res.defect.defect_type, DefectType.ANIMATION)
        self.assertEqual(res.defect.severity, DefectSeverity.HIGH)
        self.assertIn("Missing Transition", res.defect.title)
        self.assertIn("modal_dialog", res.defect.affected_components)

    def test_03_stuck_animation(self) -> None:
        """Scenario 3: Animation starts, but stalls in an intermediate state permanently."""
        frames = [
            self._make_frame(0, 0.0, "closed"),
            self._make_frame(1, 100.0, "half_open"),
            self._make_frame(2, 200.0, "half_open"),
            self._make_frame(3, 300.0, "half_open"),
        ]
        exp = AnimationStateExpectation(
            initial_state="closed",
            expected_final_state="open",
            target_element_id="modal_dialog",
        )

        res = self.evaluator.evaluate_transition(
            expectation=exp,
            frame_observations=frames,
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
        )

        self.assertTrue(res.is_fail)
        self.assertTrue(res.stuck_detected)
        self.assertEqual(res.check_type, AnimationCheckType.STUCK_ANIMATION)
        self.assertIsNotNone(res.defect)
        self.assertEqual(res.defect.defect_type, DefectType.ANIMATION)
        self.assertIn("Stuck Animation", res.defect.title)

    def test_04_incomplete_final_state(self) -> None:
        """Scenario 4: Motion completes, but final state does not equal expected final state."""
        frames = [
            self._make_frame(0, 0.0, "closed"),
            self._make_frame(1, 100.0, "animating"),
            self._make_frame(2, 200.0, "collapsed"),
        ]
        exp = AnimationStateExpectation(
            initial_state="closed",
            expected_final_state="expanded",
            target_element_id="sidebar",
        )

        res = self.evaluator.evaluate_transition(
            expectation=exp,
            frame_observations=frames,
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
        )

        self.assertTrue(res.is_fail)
        self.assertEqual(res.check_type, AnimationCheckType.INCOMPLETE_FINAL_STATE)
        self.assertIsNotNone(res.defect)
        self.assertIn("Incomplete Final State", res.defect.title)
        self.assertEqual(res.final_state_observed, "collapsed")

    def test_05_intentional_no_animation_behavior(self) -> None:
        """Scenario 5: Instantaneous transition without gradual frames is recognized as intentional."""
        frames = [
            self._make_frame(0, 0.0, "off"),
            self._make_frame(1, 50.0, "on"),
        ]
        exp = AnimationStateExpectation(
            initial_state="off",
            expected_final_state="on",
            target_element_id="toggle_switch",
            require_intermediate_progress=False,
        )

        res = self.evaluator.evaluate_transition(
            expectation=exp,
            frame_observations=frames,
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
        )

        self.assertTrue(res.is_pass)
        self.assertEqual(res.status, AnimationEvaluationStatus.PASS)
        self.assertTrue(res.transition_completed)
        self.assertIsNone(res.defect)
        self.assertIn("intentional instantaneous transition", res.description)

    def test_06_insufficient_frame_evidence(self) -> None:
        """Scenario 6: Empty frames or single frame when progress is required returns NOT_VERIFIED."""
        exp = AnimationStateExpectation(
            initial_state="closed",
            expected_final_state="open",
            target_element_id="dropdown",
            require_intermediate_progress=True,
        )

        # A: Empty frames
        res_empty = self.evaluator.evaluate_transition(
            expectation=exp,
            frame_observations=[],
            test_case_id=self.test_case_id,
        )
        self.assertTrue(res_empty.is_not_verified)
        self.assertEqual(res_empty.status, AnimationEvaluationStatus.NOT_VERIFIED)

        # B: Single frame
        single_frame = [self._make_frame(0, 0.0, "closed")]
        res_single = self.evaluator.evaluate_transition(
            expectation=exp,
            frame_observations=single_frame,
            test_case_id=self.test_case_id,
        )
        self.assertTrue(res_single.is_not_verified)
        self.assertEqual(res_single.status, AnimationEvaluationStatus.NOT_VERIFIED)

    def test_07_bounded_frame_analysis(self) -> None:
        """Scenario 7: Enforces strict frame budget cap; 50 frames sampled down to <= 10."""
        # Generate 50 frames
        frames = [
            self._make_frame(i, float(i * 20), "animating" if 0 < i < 49 else ("closed" if i == 0 else "open"))
            for i in range(50)
        ]
        exp = AnimationStateExpectation(
            initial_state="closed",
            expected_final_state="open",
            target_element_id="large_anim",
        )

        res = self.evaluator.evaluate_transition(
            expectation=exp,
            frame_observations=frames,
            test_case_id=self.test_case_id,
        )

        self.assertTrue(res.is_pass)
        self.assertLessEqual(res.frames_evaluated_count, MAX_EVALUATION_FRAMES)
        self.assertEqual(res.frames_evaluated_count, 10)

    def test_08_subjective_motion_preference(self) -> None:
        """Scenario 8: Subjective opinions are filtered from defect classification and converted to recommendations."""
        is_subj, finding = self.evaluator.filter_subjective_motion_critique(
            critique_text="The animation feels slow and isn't premium enough."
        )
        self.assertTrue(is_subj)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.category, FindingCategory.RECOMMENDATION)

        # Also evaluate through evaluate_animation
        assertion = AnimationAssertion(
            check_type=AnimationCheckType.TRANSITION_COMPLETION,
            target_element_id="header",
            expectation=AnimationStateExpectation(
                expected_final_state="animation feels slow",
            ),
        )
        agg = self.evaluator.evaluate_animation(
            assertions=[assertion],
            frame_observations=[],
            test_case_id=self.test_case_id,
        )
        self.assertEqual(len(agg.defects), 0)
        self.assertEqual(len(agg.findings), 1)
        self.assertEqual(agg.findings[0].category, FindingCategory.RECOMMENDATION)
        self.assertEqual(agg.assertion_results[0].status, AnimationEvaluationStatus.SKIPPED)

    def test_09_evidence_provenance(self) -> None:
        """Scenario 9: Evidence IDs are bound and validated; mismatched lineage raises error."""
        frames = [
            self._make_frame(0, 0.0, "closed"),
            self._make_frame(1, 100.0, "open"),
        ]
        res = self.evaluator.evaluate_transition(
            expectation=AnimationStateExpectation(
                initial_state="closed",
                expected_final_state="open",
                require_intermediate_progress=False,
            ),
            frame_observations=frames,
            source_evidence_ids=["tevid-src-001"],
            test_case_id=self.test_case_id,
        )
        self.assertIn("tevid-src-001", res.evidence_ids)
        self.assertIn(self.video_evidence_id, res.evidence_ids)

        # Lineage mismatch: execution_id
        foreign_frame = self._make_frame(0, 0.0, "closed", execution_id="texec-foreign-999")
        with self.assertRaises(TesterLineageError):
            self.evaluator.evaluate_transition(
                expectation=AnimationStateExpectation(expected_final_state="open"),
                frame_observations=[foreign_frame],
            )

        # Lineage mismatch: project_id
        foreign_proj_frame = self._make_frame(0, 0.0, "closed", project_id="foreign-project")
        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.evaluate_transition(
                expectation=AnimationStateExpectation(expected_final_state="open"),
                frame_observations=[foreign_proj_frame],
            )

    def test_10_runtime_failure(self) -> None:
        """Scenario 10: Frame extraction failure/timeout produces BLOCKED rather than a product defect."""
        failed_frame = self._make_frame(
            0,
            0.0,
            "unknown",
            status=FrameExtractionStatus.FAILED,
            error_message="Video codec decoding error",
        )
        exp = AnimationStateExpectation(
            initial_state="closed",
            expected_final_state="open",
        )

        res = self.evaluator.evaluate_transition(
            expectation=exp,
            frame_observations=[failed_frame],
            test_case_id=self.test_case_id,
        )

        self.assertTrue(res.is_blocked)
        self.assertEqual(res.status, AnimationEvaluationStatus.BLOCKED)
        self.assertIsNone(res.defect)
        self.assertIn("Video codec decoding error", res.description)

    def test_11_zero_fixing_guard(self) -> None:
        """Zero fixing guard: Calling apply_fix or auto_fix raises TesterBoundaryViolationError."""
        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.apply_fix()

        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.auto_fix()

        result = AnimationEvaluationResult(
            evaluation_id="tanim-res-001",
            status=AnimationEvaluationStatus.FAIL,
        )
        with self.assertRaises(TesterBoundaryViolationError):
            result.apply_fix()

        with self.assertRaises(TesterBoundaryViolationError):
            result.auto_fix()

    def test_12_contract_serialization_roundtrip(self) -> None:
        """Contract serialization: to_dict and from_dict produce identical instances."""
        exp = AnimationStateExpectation(
            initial_state="closed",
            expected_final_state="open",
            target_element_id="dialog",
            expected_duration_ms=300.0,
            max_duration_ms=500.0,
            require_intermediate_progress=True,
            expected_properties={"opacity": 1.0},
        )
        exp_dict = exp.to_dict()
        exp_restored = AnimationStateExpectation.from_dict(exp_dict)
        self.assertEqual(exp_restored.initial_state, exp.initial_state)
        self.assertEqual(exp_restored.expected_final_state, exp.expected_final_state)
        self.assertEqual(exp_restored.expected_properties, exp.expected_properties)

        assertion = AnimationAssertion(
            check_type=AnimationCheckType.TRANSITION_COMPLETION,
            target_element_id="dialog",
            expectation=exp,
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
        )
        assertion_dict = assertion.to_dict()
        assertion_restored = AnimationAssertion.from_dict(assertion_dict)
        self.assertEqual(assertion_restored.assertion_id, assertion.assertion_id)
        self.assertEqual(assertion_restored.check_type, assertion.check_type)
        self.assertEqual(assertion_restored.target_element_id, assertion.target_element_id)

        res = AnimationAssertionResult(
            assertion_id="tanim-ast-001",
            check_type=AnimationCheckType.TRANSITION_COMPLETION,
            status=AnimationEvaluationStatus.PASS,
            target_element_id="dialog",
            initial_state_observed="closed",
            final_state_observed="open",
            transition_completed=True,
            frames_evaluated_count=3,
        )
        res_dict = res.to_dict()
        res_restored = AnimationAssertionResult.from_dict(res_dict)
        self.assertEqual(res_restored.assertion_id, res.assertion_id)
        self.assertEqual(res_restored.status, res.status)
        self.assertEqual(res_restored.transition_completed, res.transition_completed)

        eval_res = AnimationEvaluationResult(
            evaluation_id="tanim-agg-001",
            status=AnimationEvaluationStatus.PASS,
            assertion_results=[res],
            total_frames_evaluated=3,
        )
        eval_dict = eval_res.to_dict()
        eval_restored = AnimationEvaluationResult.from_dict(eval_dict)
        self.assertEqual(eval_restored.evaluation_id, eval_res.evaluation_id)
        self.assertEqual(eval_restored.status, eval_res.status)
        self.assertEqual(len(eval_restored.assertion_results), 1)


if __name__ == "__main__":
    unittest.main()

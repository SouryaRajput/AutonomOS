from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from core.tester import (
    EvidenceType,
    FrameExtractionOptions,
    FrameExtractionStatus,
    FramePosition,
    FrameSelectionStrategy,
    ObservationBinder,
    ObservationType,
    TesterBoundaryViolationError,
    TesterEvidence,
    TesterExecution,
    TesterExecutionStatus,
    TesterLineageError,
    TesterObservation,
    TesterValidationError,
    TesterWorkOrder,
    VideoFrameExtractor,
    VideoFrameObservation,
    FRAME_OBSERVATION_ID_PREFIX,
    new_evidence_id,
    new_execution_id,
    new_frame_observation_id,
    new_step_id,
    new_test_case_id,
    new_work_order_id,
    validate_frame_observation_id,
)
from core.tester.contracts.video_frame import MAX_FRAME_BUDGET_LIMIT


class TestTesterVideoFrame(unittest.TestCase):
    """
    Validation test suite for Tester V1 Phase 4.4: Video & Frame Observation.
    
    Verifies all 14 mandatory scenarios:
    1. Valid frame extraction with default and explicit options
    2. Timestamp selection strategy
    3. Frame index selection strategy
    4. Bounded sampling & interval strategy
    5. Invalid timestamp handling
    6. Missing video & unavailable decoder handling
    7. Unsupported evidence type handling
    8. Timeout handling
    9. Execution isolation & path traversal boundary defense
    10. Evidence lineage & observation binding
    11. Hard budget enforcement (clamping to max 50 frames & work order limits)
    12. Deterministic behavior & idempotency
    13. Pure observation invariants (rejection of evaluative methods & keys)
    14. Serialization & deserialization round-trip
    """

    def setUp(self) -> None:
        self.project_id = "proj-video-test"
        self.execution_id = new_execution_id()
        self.work_order_id = new_work_order_id()
        self.evidence_id = new_evidence_id()
        self.test_case_id = new_test_case_id()
        self.test_step_id = new_step_id()
        self.extractor = VideoFrameExtractor()

    def _make_execution(self) -> TesterExecution:
        return TesterExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id="task-video-01",
            project_id=self.project_id,
            correlation_id="corr-video-01",
            status=TesterExecutionStatus.RUNNING,
            current_phase="EXECUTION",
        )

    def _make_video_evidence(
        self,
        evidence_id: str | None = None,
        duration_ms: float = 3000.0,
        fps: int = 30,
        artifact_ref: str = "artifacts/recordings/test_run.mp4",
        execution_id: str | None = None,
        metadata_extra: dict | None = None,
        description: str = "Recorded test video interaction",
    ) -> TesterEvidence:
        meta = {
            "duration_ms": duration_ms,
            "fps": fps,
            "project_id": self.project_id,
            "viewport": {"width": 1280, "height": 720},
        }
        if metadata_extra:
            meta.update(metadata_extra)

        return TesterEvidence(
            evidence_id=evidence_id or self.evidence_id,
            execution_id=execution_id or self.execution_id,
            evidence_type=EvidenceType.VIDEO,
            artifact_reference=artifact_ref,
            description=description,
            metadata=meta,
        )

    # -----------------------------------------------------------------------
    # Scenario 1: Valid Frame Extraction
    # -----------------------------------------------------------------------
    def test_valid_frame_extraction(self) -> None:
        execution = self._make_execution()
        evidence = self._make_video_evidence()

        options = FrameExtractionOptions(
            strategy=FrameSelectionStrategy.TIMESTAMP,
            timestamps_ms=[0.0, 500.0, 1000.0],
            max_frames=5,
        )
        frames = self.extractor.extract_frames(
            evidence=evidence,
            options=options,
            execution=execution,
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
        )

        self.assertEqual(len(frames), 3)
        for i, frame in enumerate(frames):
            self.assertIsInstance(frame, VideoFrameObservation)
            validate_frame_observation_id(frame.frame_observation_id)
            self.assertTrue(frame.frame_observation_id.startswith(FRAME_OBSERVATION_ID_PREFIX))
            self.assertEqual(frame.execution_id, self.execution_id)
            self.assertEqual(frame.project_id, self.project_id)
            self.assertEqual(frame.source_video_evidence_id, evidence.evidence_id)
            self.assertEqual(frame.test_case_id, self.test_case_id)
            self.assertEqual(frame.test_step_id, self.test_step_id)
            self.assertEqual(frame.status, FrameExtractionStatus.SUCCESS)
            self.assertEqual(frame.confidence, 1.0)
            self.assertFalse(frame.is_uncertain)
            self.assertIsNotNone(frame.frame_evidence_reference)
            self.assertIn("artifacts/frames/", frame.frame_evidence_reference)

    # -----------------------------------------------------------------------
    # Scenario 2: Timestamp Selection Strategy
    # -----------------------------------------------------------------------
    def test_timestamp_selection_strategy(self) -> None:
        evidence = self._make_video_evidence(duration_ms=5000.0, fps=25)
        options = FrameExtractionOptions(
            strategy=FrameSelectionStrategy.TIMESTAMP,
            timestamps_ms=[200.0, 1000.0, 2400.0],
        )

        frames = self.extractor.extract_frames(evidence=evidence, options=options)
        self.assertEqual(len(frames), 3)

        self.assertEqual(frames[0].frame_position.timestamp_ms, 200.0)
        self.assertEqual(frames[0].frame_position.seconds, 0.2)
        self.assertEqual(frames[0].frame_position.frame_index, 5)  # 0.2 * 25

        self.assertEqual(frames[1].frame_position.timestamp_ms, 1000.0)
        self.assertEqual(frames[1].frame_position.seconds, 1.0)
        self.assertEqual(frames[1].frame_position.frame_index, 25)

        self.assertEqual(frames[2].frame_position.timestamp_ms, 2400.0)
        self.assertEqual(frames[2].frame_position.seconds, 2.4)
        self.assertEqual(frames[2].frame_position.frame_index, 60)

    # -----------------------------------------------------------------------
    # Scenario 3: Frame Index Selection Strategy
    # -----------------------------------------------------------------------
    def test_frame_index_selection_strategy(self) -> None:
        evidence = self._make_video_evidence(duration_ms=4000.0, fps=30)
        options = FrameExtractionOptions(
            strategy=FrameSelectionStrategy.FRAME_INDEX,
            frame_indices=[0, 30, 90],
        )

        frames = self.extractor.extract_frames(evidence=evidence, options=options)
        self.assertEqual(len(frames), 3)

        self.assertEqual(frames[0].frame_position.frame_index, 0)
        self.assertEqual(frames[0].frame_position.timestamp_ms, 0.0)

        self.assertEqual(frames[1].frame_position.frame_index, 30)
        self.assertEqual(frames[1].frame_position.timestamp_ms, 1000.0)

        self.assertEqual(frames[2].frame_position.frame_index, 90)
        self.assertEqual(frames[2].frame_position.timestamp_ms, 3000.0)

    # -----------------------------------------------------------------------
    # Scenario 4: Bounded Sampling & Interval Strategy
    # -----------------------------------------------------------------------
    def test_sample_and_interval_strategies(self) -> None:
        evidence = self._make_video_evidence(duration_ms=2000.0, fps=20)

        # 4a. Sample Strategy
        sample_opts = FrameExtractionOptions(
            strategy=FrameSelectionStrategy.SAMPLE,
            sample_count=5,
        )
        sample_frames = self.extractor.extract_frames(evidence=evidence, options=sample_opts)
        self.assertEqual(len(sample_frames), 5)
        # Expected sample timestamps: 0, 500, 1000, 1500, 2000 ms
        expected_ts = [0.0, 500.0, 1000.0, 1500.0, 2000.0]
        for f, exp in zip(sample_frames, expected_ts):
            self.assertAlmostEqual(f.frame_position.timestamp_ms, exp, places=1)

        # 4b. Interval Strategy
        interval_opts = FrameExtractionOptions(
            strategy=FrameSelectionStrategy.INTERVAL,
            interval_ms=500.0,
            start_time_ms=0.0,
            end_time_ms=1500.0,
        )
        interval_frames = self.extractor.extract_frames(evidence=evidence, options=interval_opts)
        self.assertEqual(len(interval_frames), 4)  # 0, 500, 1000, 1500
        for f, exp in zip(interval_frames, [0.0, 500.0, 1000.0, 1500.0]):
            self.assertEqual(f.frame_position.timestamp_ms, exp)

    # -----------------------------------------------------------------------
    # Scenario 5: Invalid Timestamp Handling
    # -----------------------------------------------------------------------
    def test_invalid_timestamp_handling(self) -> None:
        # 5a. Negative timestamp raises TesterValidationError
        with self.assertRaises(TesterValidationError):
            FramePosition(timestamp_ms=-50.0)

        # 5b. Negative frame index raises TesterValidationError
        with self.assertRaises(TesterValidationError):
            FramePosition(frame_index=-1)

        # 5c. Neither timestamp nor frame_index raises TesterValidationError
        with self.assertRaises(TesterValidationError):
            FramePosition()

        # 5d. Requested timestamp significantly exceeding video duration returns FAILED status gracefully
        evidence = self._make_video_evidence(duration_ms=2000.0)
        options = FrameExtractionOptions(
            strategy=FrameSelectionStrategy.TIMESTAMP,
            timestamps_ms=[10000.0],  # 10s on a 2s video
        )
        frames = self.extractor.extract_frames(evidence=evidence, options=options)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].status, FrameExtractionStatus.FAILED)
        self.assertEqual(frames[0].confidence, 0.0)
        self.assertTrue(frames[0].is_uncertain)
        self.assertIn("exceeds video duration", frames[0].error_message or "")

    # -----------------------------------------------------------------------
    # Scenario 6: Missing Video Handling
    # -----------------------------------------------------------------------
    def test_missing_video_handling(self) -> None:
        # 6a. Missing metadata flag
        evidence_missing = self._make_video_evidence(metadata_extra={"missing": True})
        frames = self.extractor.extract_frames(evidence=evidence_missing)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].status, FrameExtractionStatus.UNAVAILABLE)
        self.assertEqual(frames[0].confidence, 0.0)
        self.assertTrue(frames[0].is_uncertain)
        self.assertIn("missing or not found", frames[0].error_message or "")

        # 6b. Empty artifact reference
        evidence_empty = self._make_video_evidence(artifact_ref="")
        frames_empty = self.extractor.extract_frames(evidence=evidence_empty)
        self.assertEqual(len(frames_empty), 1)
        self.assertEqual(frames_empty[0].status, FrameExtractionStatus.UNAVAILABLE)

        # 6c. Simulated unavailable decoder
        extractor_unavail = VideoFrameExtractor(simulate_unavailable=True)
        evidence = self._make_video_evidence()
        frames_sim = extractor_unavail.extract_frames(evidence=evidence)
        self.assertEqual(len(frames_sim), 1)
        self.assertEqual(frames_sim[0].status, FrameExtractionStatus.UNAVAILABLE)

    # -----------------------------------------------------------------------
    # Scenario 7: Unsupported Video Handling
    # -----------------------------------------------------------------------
    def test_unsupported_evidence_handling(self) -> None:
        # Screenshot evidence instead of video
        screenshot_evidence = TesterEvidence(
            evidence_id=new_evidence_id(),
            execution_id=self.execution_id,
            evidence_type=EvidenceType.SCREENSHOT,
            artifact_reference="artifacts/screenshots/shot.png",
            metadata={"project_id": self.project_id},
        )
        frames = self.extractor.extract_frames(evidence=screenshot_evidence)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].status, FrameExtractionStatus.UNSUPPORTED)
        self.assertEqual(frames[0].confidence, 0.0)
        self.assertTrue(frames[0].is_uncertain)
        self.assertIn("Must be VIDEO", frames[0].error_message or "")

    # -----------------------------------------------------------------------
    # Scenario 8: Timeout Handling
    # -----------------------------------------------------------------------
    def test_timeout_handling(self) -> None:
        extractor_timeout = VideoFrameExtractor(simulate_timeout=True)
        evidence = self._make_video_evidence()
        options = FrameExtractionOptions(timeout_seconds=5.0)

        frames = extractor_timeout.extract_frames(evidence=evidence, options=options)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].status, FrameExtractionStatus.TIMEOUT)
        self.assertEqual(frames[0].confidence, 0.0)
        self.assertTrue(frames[0].is_uncertain)
        self.assertIn("timed out", frames[0].error_message or "")

    # -----------------------------------------------------------------------
    # Scenario 9: Execution Isolation & Path Traversal Boundary Defense
    # -----------------------------------------------------------------------
    def test_execution_isolation_and_path_traversal(self) -> None:
        execution = self._make_execution()

        # 9a. Cross-execution lineage error
        foreign_evidence = self._make_video_evidence(execution_id=new_execution_id())
        with self.assertRaises(TesterLineageError):
            self.extractor.extract_frames(evidence=foreign_evidence, execution=execution)

        # 9b. Cross-project isolation error
        foreign_project_evidence = self._make_video_evidence(
            metadata_extra={"project_id": "other-tenant"}
        )
        with self.assertRaises(TesterBoundaryViolationError) as cm:
            self.extractor.extract_frames(evidence=foreign_project_evidence, execution=execution)
        self.assertEqual(cm.exception.action, "VIDEO_PROJECT_ISOLATION")

        # 9c. Unauthorized evidence flag
        unauth_evidence = self._make_video_evidence(metadata_extra={"unauthorized": True})
        with self.assertRaises(TesterBoundaryViolationError) as cm:
            self.extractor.extract_frames(evidence=unauth_evidence, execution=execution)
        self.assertEqual(cm.exception.action, "UNAUTHORIZED_EVIDENCE")

        # 9d. Directory traversal attack prevention
        traversal_evidence = self._make_video_evidence(
            artifact_ref="../../etc/shadow"
        )
        with self.assertRaises(TesterBoundaryViolationError) as cm:
            self.extractor.extract_frames(evidence=traversal_evidence, execution=execution)
        self.assertEqual(cm.exception.action, "UNAUTHORIZED_VIDEO_ACCESS")

        # 9e. Root system path access prevention
        system_path_evidence = self._make_video_evidence(
            artifact_ref="/etc/secret_recording.mp4"
        )
        with self.assertRaises(TesterBoundaryViolationError) as cm:
            self.extractor.extract_frames(evidence=system_path_evidence, execution=execution)
        self.assertEqual(cm.exception.action, "UNAUTHORIZED_VIDEO_ACCESS")

    # -----------------------------------------------------------------------
    # Scenario 10: Evidence Lineage & Observation Binding
    # -----------------------------------------------------------------------
    def test_evidence_lineage_and_observation_binding(self) -> None:
        evidence = self._make_video_evidence()
        options = FrameExtractionOptions(timestamps_ms=[750.0])

        frames = self.extractor.extract_frames(
            evidence=evidence,
            options=options,
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
        )
        self.assertEqual(len(frames), 1)
        frame_obs = frames[0]

        # 10a. Bind to TesterObservation via to_observation()
        obs = frame_obs.to_observation()
        self.assertIsInstance(obs, TesterObservation)
        self.assertEqual(obs.observation_type, ObservationType.VIDEO_FRAME)
        self.assertEqual(obs.execution_id, self.execution_id)
        self.assertEqual(obs.project_id, self.project_id)
        self.assertEqual(obs.test_case_id, self.test_case_id)
        self.assertEqual(obs.test_step_id, self.test_step_id)
        self.assertIn(evidence.evidence_id, obs.evidence_ids)
        self.assertEqual(obs.observed_state["frame_observation_id"], frame_obs.frame_observation_id)
        self.assertEqual(obs.observed_state["status"], "SUCCESS")

        # 10b. Bind via ObservationBinder
        bound_obs = ObservationBinder.bind_video_frame_observation(
            video_frame_observation=frame_obs,
            description="Custom frame description",
        )
        self.assertIsInstance(bound_obs, TesterObservation)
        self.assertEqual(bound_obs.observation_type, ObservationType.VIDEO_FRAME)
        self.assertEqual(bound_obs.description, "Custom frame description")

    # -----------------------------------------------------------------------
    # Scenario 11: Hard Budget Enforcement
    # -----------------------------------------------------------------------
    def test_hard_budget_enforcement(self) -> None:
        evidence = self._make_video_evidence(duration_ms=100000.0)

        # 11a. Requesting 10,000 frames is clamped to MAX_FRAME_BUDGET_LIMIT (50)
        options_huge = FrameExtractionOptions(
            strategy=FrameSelectionStrategy.SAMPLE,
            sample_count=10000,
            max_frames=10000,
        )
        self.assertEqual(options_huge.max_frames, MAX_FRAME_BUDGET_LIMIT)

        frames = self.extractor.extract_frames(evidence=evidence, options=options_huge)
        self.assertLessEqual(len(frames), MAX_FRAME_BUDGET_LIMIT)
        self.assertEqual(len(frames), 50)

        # 11b. Work order iteration_budget clamps extraction count
        work_order = TesterWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id="mtask-video-01",
            correlation_id="corr-video-01",
            project_id=self.project_id,
            iteration_budget=3,
        )
        options_small = FrameExtractionOptions(
            strategy=FrameSelectionStrategy.INTERVAL,
            interval_ms=100.0,
            max_frames=20,
        )
        frames_budget = self.extractor.extract_frames(
            evidence=evidence,
            options=options_small,
            work_order=work_order,
        )
        self.assertEqual(len(frames_budget), 3)

    # -----------------------------------------------------------------------
    # Scenario 12: Deterministic Behavior & Idempotency
    # -----------------------------------------------------------------------
    def test_deterministic_behavior(self) -> None:
        evidence = self._make_video_evidence(duration_ms=4000.0, fps=25)
        options = FrameExtractionOptions(
            strategy=FrameSelectionStrategy.INTERVAL,
            interval_ms=1000.0,
        )

        run1 = self.extractor.extract_frames(evidence=evidence, options=options)
        run2 = self.extractor.extract_frames(evidence=evidence, options=options)

        self.assertEqual(len(run1), len(run2))
        for f1, f2 in zip(run1, run2):
            self.assertEqual(f1.frame_position.timestamp_ms, f2.frame_position.timestamp_ms)
            self.assertEqual(f1.frame_position.frame_index, f2.frame_position.frame_index)
            self.assertEqual(f1.status, f2.status)
            self.assertEqual(f1.confidence, f2.confidence)

    # -----------------------------------------------------------------------
    # Scenario 13: Pure Observation Invariants & Evaluative Rejection
    # -----------------------------------------------------------------------
    def test_pure_observation_invariants(self) -> None:
        evidence = self._make_video_evidence()
        frames = self.extractor.extract_frames(evidence=evidence)
        frame = frames[0]

        # 13a. Reject to_defect()
        with self.assertRaises(TesterBoundaryViolationError) as cm:
            frame.to_defect()
        self.assertEqual(cm.exception.action, "FRAME_TO_DEFECT")

        # 13b. Reject to_finding()
        with self.assertRaises(TesterBoundaryViolationError) as cm:
            frame.to_finding()
        self.assertEqual(cm.exception.action, "FRAME_TO_FINDING")

        # 13c. Reject assert_verdict()
        with self.assertRaises(TesterBoundaryViolationError) as cm:
            frame.assert_verdict()
        self.assertEqual(cm.exception.action, "FRAME_ASSERT_VERDICT")

        # 13d. Reject forbidden evaluative metadata keys
        for forbidden_key in ["is_defect", "animation_broken", "quality_score", "ux_score", "verdict"]:
            with self.assertRaises(TesterBoundaryViolationError) as cm:
                VideoFrameObservation(
                    frame_observation_id=new_frame_observation_id(),
                    execution_id=self.execution_id,
                    project_id=self.project_id,
                    source_video_evidence_id=self.evidence_id,
                    frame_position=FramePosition(timestamp_ms=0.0),
                    observation_metadata={forbidden_key: True},
                )
            self.assertEqual(cm.exception.action, "FRAME_EVALUATION")

    # -----------------------------------------------------------------------
    # Scenario 14: Serialization & Deserialization Round-Trip
    # -----------------------------------------------------------------------
    def test_serialization_round_trip(self) -> None:
        pos = FramePosition(timestamp_ms=1250.0, frame_index=37)
        pos_dict = pos.to_dict()
        restored_pos = FramePosition.from_dict(pos_dict)
        self.assertEqual(pos, restored_pos)

        opts = FrameExtractionOptions(
            strategy=FrameSelectionStrategy.SAMPLE,
            sample_count=8,
            max_frames=15,
            timeout_seconds=20.0,
        )
        opts_dict = opts.to_dict()
        restored_opts = FrameExtractionOptions.from_dict(opts_dict)
        self.assertEqual(opts.strategy, restored_opts.strategy)
        self.assertEqual(opts.sample_count, restored_opts.sample_count)
        self.assertEqual(opts.max_frames, restored_opts.max_frames)

        obs = VideoFrameObservation(
            frame_observation_id=new_frame_observation_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            source_video_evidence_id=self.evidence_id,
            frame_position=pos,
            status=FrameExtractionStatus.SUCCESS,
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
            confidence=1.0,
            observation_metadata={"note": "clean frame"},
        )
        obs_dict = obs.to_dict()
        restored_obs = VideoFrameObservation.from_dict(obs_dict)
        self.assertEqual(obs.frame_observation_id, restored_obs.frame_observation_id)
        self.assertEqual(obs.execution_id, restored_obs.execution_id)
        self.assertEqual(obs.project_id, restored_obs.project_id)
        self.assertEqual(obs.frame_position.timestamp_ms, restored_obs.frame_position.timestamp_ms)
        self.assertEqual(obs.status, restored_obs.status)
        self.assertEqual(obs.observation_metadata, restored_obs.observation_metadata)


if __name__ == "__main__":
    unittest.main()

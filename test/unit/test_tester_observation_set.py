from __future__ import annotations

import unittest

from core.tester import (
    EvidenceType,
    ObservationCompleteness,
    ObservationType,
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterObservation,
    TesterValidationError,
    OBSERVATION_SET_ID_PREFIX,
    ObservationAggregator,
    ObservationSet,
    new_evidence_id,
    new_execution_id,
    new_observation_id,
    new_observation_set_id,
    new_step_id,
    new_test_case_id,
    validate_observation_set_id,
)


class TestTesterObservationSet(unittest.TestCase):
    """
    Validation test suite for Tester V1 Phase 4.5: Observation Aggregation.
    
    Verifies all 12 mandatory scenarios:
    1. One observation aggregation
    2. Multiple observations aggregation
    3. Multiple steps grouping and organization
    4. Mixed observation types (Screenshot, OCR, Geometry, Video Frame)
    5. Duplicate evidence deduplication
    6. Partial observation set (e.g. OCR unavailable while screenshot captured -> PARTIAL, not product failure)
    7. Empty observation set (0 observations -> EMPTY)
    8. Failed observation set (all observations failed -> FAILED)
    9. Chronological ordering preservation
    10. Provenance & traceability preservation
    11. Execution isolation & cross-project rejection
    12. Serialization round-trip & evaluation API rejection
    """

    def setUp(self) -> None:
        self.project_id = "proj-obs-set-test"
        self.execution_id = new_execution_id()
        self.test_case_id = new_test_case_id()
        self.step_1_id = new_step_id()
        self.step_2_id = new_step_id()
        self.step_3_id = new_step_id()
        self.evidence_1_id = new_evidence_id()
        self.evidence_2_id = new_evidence_id()
        self.aggregator = ObservationAggregator()

    def _make_observation(
        self,
        observation_id: str | None = None,
        test_step_id: str | None = None,
        observation_type: ObservationType = ObservationType.SCREEN,
        description: str = "Test observation",
        evidence_ids: list[str] | None = None,
        timestamp: str | None = None,
        execution_id: str | None = None,
        project_id: str | None = None,
        test_case_id: str | None = None,
        status: str = "SUCCESS",
        confidence: float = 1.0,
        is_uncertain: bool = False,
        observed_state_extra: dict | None = None,
        metadata_extra: dict | None = None,
    ) -> TesterObservation:
        state = {"status": status}
        if observed_state_extra:
            state.update(observed_state_extra)

        meta = {}
        if metadata_extra:
            meta.update(metadata_extra)

        obs_kwargs: dict = {
            "observation_id": observation_id or new_observation_id(),
            "execution_id": execution_id or self.execution_id,
            "project_id": project_id or self.project_id,
            "test_case_id": test_case_id or self.test_case_id,
            "test_step_id": test_step_id or self.step_1_id,
            "observation_type": observation_type,
            "description": description,
            "observed_state": state,
            "evidence_ids": evidence_ids or [self.evidence_1_id],
            "source": "test_runner",
            "confidence": confidence,
            "is_uncertain": is_uncertain,
            "metadata": meta,
        }
        if timestamp:
            obs_kwargs["timestamp"] = timestamp

        return TesterObservation(**obs_kwargs)

    # -----------------------------------------------------------------------
    # Scenario 1: One Observation
    # -----------------------------------------------------------------------
    def test_one_observation(self) -> None:
        obs = self._make_observation(description="Initial button observation")
        obs_set = self.aggregator.aggregate(
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            observations=[obs],
        )

        self.assertTrue(obs_set.observation_set_id.startswith(OBSERVATION_SET_ID_PREFIX))
        validate_observation_set_id(obs_set.observation_set_id)
        self.assertEqual(obs_set.execution_id, self.execution_id)
        self.assertEqual(obs_set.project_id, self.project_id)
        self.assertEqual(obs_set.test_case_id, self.test_case_id)
        self.assertEqual(len(obs_set.observations), 1)
        self.assertEqual(obs_set.completeness, ObservationCompleteness.COMPLETE)
        self.assertIn(self.evidence_1_id, obs_set.evidence_references)

        by_step = obs_set.get_observations_by_step(self.step_1_id)
        self.assertEqual(len(by_step), 1)
        self.assertEqual(by_step[0].observation_id, obs.observation_id)

    # -----------------------------------------------------------------------
    # Scenario 2: Multiple Observations
    # -----------------------------------------------------------------------
    def test_multiple_observations(self) -> None:
        obs1 = self._make_observation(evidence_ids=[self.evidence_1_id], description="Obs 1")
        obs2 = self._make_observation(evidence_ids=[self.evidence_2_id], description="Obs 2")
        obs3 = self._make_observation(evidence_ids=[self.evidence_1_id, self.evidence_2_id], description="Obs 3")

        obs_set = self.aggregator.aggregate(
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            observations=[obs1, obs2, obs3],
        )

        self.assertEqual(len(obs_set.observations), 3)
        self.assertEqual(obs_set.completeness, ObservationCompleteness.COMPLETE)
        # Should collect unique evidence references
        self.assertEqual(set(obs_set.evidence_references), {self.evidence_1_id, self.evidence_2_id})

    # -----------------------------------------------------------------------
    # Scenario 3: Multiple Steps
    # -----------------------------------------------------------------------
    def test_multiple_steps(self) -> None:
        obs_step1 = self._make_observation(test_step_id=self.step_1_id, description="Step 1 Obs")
        obs_step2 = self._make_observation(test_step_id=self.step_2_id, description="Step 2 Obs")
        obs_step3 = self._make_observation(test_step_id=self.step_3_id, description="Step 3 Obs")

        obs_set = self.aggregator.aggregate(
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            observations=[obs_step1, obs_step2, obs_step3],
        )

        grouped = obs_set.group_by_step()
        self.assertIn(self.step_1_id, grouped)
        self.assertIn(self.step_2_id, grouped)
        self.assertIn(self.step_3_id, grouped)
        self.assertEqual(len(grouped[self.step_1_id]), 1)
        self.assertEqual(len(grouped[self.step_2_id]), 1)
        self.assertEqual(len(grouped[self.step_3_id]), 1)

        self.assertEqual(obs_set.get_observations_by_step(self.step_2_id)[0].observation_id, obs_step2.observation_id)

    # -----------------------------------------------------------------------
    # Scenario 4: Mixed Observation Types
    # -----------------------------------------------------------------------
    def test_mixed_observation_types(self) -> None:
        obs_shot = self._make_observation(
            observation_type=ObservationType.SCREEN,
            description="Screenshot taken",
        )
        obs_ocr = self._make_observation(
            observation_type=ObservationType.TEXT,
            description="OCR extracted text",
        )
        obs_geom = self._make_observation(
            observation_type=ObservationType.GEOMETRY,
            description="Geometry observed",
        )
        obs_frame = self._make_observation(
            observation_type=ObservationType.VIDEO_FRAME,
            description="Video frame captured",
        )

        obs_set = self.aggregator.aggregate(
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            observations=[obs_shot, obs_ocr, obs_geom, obs_frame],
        )

        grouped = obs_set.group_by_type()
        self.assertIn("SCREEN", grouped)
        self.assertIn("TEXT", grouped)
        self.assertIn("GEOMETRY", grouped)
        self.assertIn("VIDEO_FRAME", grouped)

        self.assertEqual(len(obs_set.get_observations_by_type(ObservationType.SCREEN)), 1)
        self.assertEqual(len(obs_set.get_observations_by_type(ObservationType.TEXT)), 1)
        self.assertEqual(len(obs_set.get_observations_by_type(ObservationType.GEOMETRY)), 1)
        self.assertEqual(len(obs_set.get_observations_by_type(ObservationType.VIDEO_FRAME)), 1)

    # -----------------------------------------------------------------------
    # Scenario 5: Duplicate Evidence Deduplication
    # -----------------------------------------------------------------------
    def test_duplicate_evidence_deduplication(self) -> None:
        obs1 = self._make_observation(
            observation_id="tobs-dup1",
            test_step_id=self.step_1_id,
            observation_type=ObservationType.SCREEN,
            evidence_ids=[self.evidence_1_id],
            description="Identical screenshot observation",
            observed_state_extra={"dim": 100},
        )
        # Duplicate 1: exact same observation_id
        obs1_dup = self._make_observation(
            observation_id="tobs-dup1",
            test_step_id=self.step_1_id,
            observation_type=ObservationType.SCREEN,
            evidence_ids=[self.evidence_1_id],
            description="Identical screenshot observation",
            observed_state_extra={"dim": 100},
        )
        # Duplicate 2: different ID but identical evidence processing for the same step and state
        obs1_content_dup = self._make_observation(
            observation_id="tobs-dup2",
            test_step_id=self.step_1_id,
            observation_type=ObservationType.SCREEN,
            evidence_ids=[self.evidence_1_id],
            description="Identical screenshot observation",
            observed_state_extra={"dim": 100},
        )
        # Distinct observation: different step
        obs2 = self._make_observation(
            observation_id="tobs-unique",
            test_step_id=self.step_2_id,
            observation_type=ObservationType.SCREEN,
            evidence_ids=[self.evidence_1_id],
            description="Distinct step observation",
        )

        obs_set = self.aggregator.aggregate(
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            observations=[obs1, obs1_dup, obs1_content_dup, obs2],
            deduplicate=True,
        )

        # Deduplication must reduce the 3 identical step-1 observations to exactly 1
        self.assertEqual(len(obs_set.observations), 2)
        self.assertEqual(obs_set.observations[0].observation_id, "tobs-dup1")
        self.assertEqual(obs_set.observations[1].observation_id, "tobs-unique")

        # When deduplicate=False, all observations are retained
        obs_set_no_dedup = self.aggregator.aggregate(
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            observations=[obs1, obs1_dup, obs1_content_dup, obs2],
            deduplicate=False,
        )
        self.assertEqual(len(obs_set_no_dedup.observations), 4)

    # -----------------------------------------------------------------------
    # Scenario 6: Partial Observation Set
    # -----------------------------------------------------------------------
    def test_partial_observation_set(self) -> None:
        # Screenshot succeeded
        obs_shot = self._make_observation(
            test_step_id=self.step_1_id,
            observation_type=ObservationType.SCREEN,
            status="SUCCESS",
            confidence=1.0,
            description="Screenshot captured",
        )
        # OCR extraction was unavailable
        obs_ocr = self._make_observation(
            test_step_id=self.step_1_id,
            observation_type=ObservationType.TEXT,
            status="UNAVAILABLE",
            confidence=0.0,
            is_uncertain=True,
            description="OCR engine unavailable",
            observed_state_extra={"error_message": "OCR provider unavailable"},
        )

        obs_set = self.aggregator.aggregate(
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            observations=[obs_shot, obs_ocr],
        )

        # Completeness is PARTIAL because one observation was unavailable
        self.assertEqual(obs_set.completeness, ObservationCompleteness.PARTIAL)

        # Invariant check: PARTIAL does NOT equal test failure or verdict
        with self.assertRaises(TesterBoundaryViolationError) as cm:
            obs_set.assert_verdict()
        self.assertEqual(cm.exception.action, "OBSERVATION_SET_ASSERT_VERDICT")

        # Also: partial when an expected step is missing
        obs_set_missing_step = self.aggregator.aggregate(
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            observations=[obs_shot],
            expected_step_ids=[self.step_1_id, self.step_2_id],
        )
        self.assertEqual(obs_set_missing_step.completeness, ObservationCompleteness.PARTIAL)

    # -----------------------------------------------------------------------
    # Scenario 7: Empty Observation Set
    # -----------------------------------------------------------------------
    def test_empty_observation_set(self) -> None:
        obs_set = self.aggregator.aggregate(
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            observations=[],
        )

        self.assertEqual(obs_set.completeness, ObservationCompleteness.EMPTY)
        self.assertEqual(len(obs_set.observations), 0)
        self.assertEqual(len(obs_set.evidence_references), 0)

    # -----------------------------------------------------------------------
    # Scenario 8: Failed Observation Set
    # -----------------------------------------------------------------------
    def test_failed_observation_set(self) -> None:
        obs_fail1 = self._make_observation(
            status="FAILED",
            confidence=0.0,
            description="Capture failed",
            observed_state_extra={"error_message": "Timeout capturing screenshot"},
        )
        obs_fail2 = self._make_observation(
            status="UNAVAILABLE",
            confidence=0.0,
            description="Runtime unavailable",
            observed_state_extra={"error_message": "Runtime crashed"},
        )

        obs_set = self.aggregator.aggregate(
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            observations=[obs_fail1, obs_fail2],
        )

        # All observations failed -> set completeness is FAILED
        self.assertEqual(obs_set.completeness, ObservationCompleteness.FAILED)

    # -----------------------------------------------------------------------
    # Scenario 9: Chronological Ordering
    # -----------------------------------------------------------------------
    def test_chronological_ordering(self) -> None:
        obs_early = self._make_observation(
            timestamp="2026-09-09T10:00:00.000000+00:00",
            description="Early moment",
        )
        obs_middle = self._make_observation(
            timestamp="2026-09-09T10:00:05.000000+00:00",
            description="Middle moment",
        )
        obs_late = self._make_observation(
            timestamp="2026-09-09T10:00:10.000000+00:00",
            description="Late moment",
        )

        # Pass in scrambled order: late, early, middle
        obs_set = self.aggregator.aggregate(
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            observations=[obs_late, obs_early, obs_middle],
            sort_chronological=True,
        )

        self.assertEqual(len(obs_set.observations), 3)
        self.assertEqual(obs_set.observations[0].description, "Early moment")
        self.assertEqual(obs_set.observations[1].description, "Middle moment")
        self.assertEqual(obs_set.observations[2].description, "Late moment")

    # -----------------------------------------------------------------------
    # Scenario 10: Provenance & Traceability Preservation
    # -----------------------------------------------------------------------
    def test_provenance_and_traceability(self) -> None:
        obs = self._make_observation()
        obs_set = self.aggregator.aggregate(
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            observations=[obs],
            trace_id="ttrace-custom-999",
            provenance={"custom_pipeline": "e2e_runner"},
        )

        self.assertEqual(obs_set.trace_id, "ttrace-custom-999")
        self.assertEqual(obs_set.provenance["source"], "observation_aggregator")
        self.assertEqual(obs_set.provenance["custom_pipeline"], "e2e_runner")
        self.assertEqual(obs_set.provenance["total_raw"], 1)
        self.assertEqual(obs_set.provenance["total_aggregated"], 1)

    # -----------------------------------------------------------------------
    # Scenario 11: Execution Isolation & Cross-Project Rejection
    # -----------------------------------------------------------------------
    def test_execution_isolation(self) -> None:
        # 11a. Cross-execution rejection
        foreign_exec_obs = self._make_observation(execution_id=new_execution_id())
        with self.assertRaises(TesterLineageError):
            self.aggregator.aggregate(
                execution_id=self.execution_id,
                project_id=self.project_id,
                test_case_id=self.test_case_id,
                observations=[foreign_exec_obs],
            )

        # 11b. Cross-project rejection
        foreign_project_obs = self._make_observation(project_id="other-project")
        with self.assertRaises(TesterBoundaryViolationError) as cm:
            self.aggregator.aggregate(
                execution_id=self.execution_id,
                project_id=self.project_id,
                test_case_id=self.test_case_id,
                observations=[foreign_project_obs],
            )
        self.assertEqual(cm.exception.action, "OBSERVATION_SET_PROJECT_ISOLATION")

        # 11c. Cross-test-case rejection
        foreign_case_obs = self._make_observation(test_case_id=new_test_case_id())
        with self.assertRaises(TesterLineageError):
            self.aggregator.aggregate(
                execution_id=self.execution_id,
                project_id=self.project_id,
                test_case_id=self.test_case_id,
                observations=[foreign_case_obs],
            )

    # -----------------------------------------------------------------------
    # Scenario 12: Serialization & Evaluation API Rejection
    # -----------------------------------------------------------------------
    def test_serialization_and_boundary_rejection(self) -> None:
        obs1 = self._make_observation(description="Shot 1")
        obs2 = self._make_observation(description="Shot 2")
        obs_set = self.aggregator.aggregate(
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            observations=[obs1, obs2],
            trace_id="ttrace-serial-1",
            metadata={"run_env": "chromium"},
        )

        # 12a. Round-trip serialization
        data = obs_set.to_dict()
        self.assertIsInstance(data, dict)
        self.assertEqual(data["observation_set_id"], obs_set.observation_set_id)
        self.assertEqual(data["completeness"], "COMPLETE")
        self.assertEqual(len(data["observations"]), 2)

        restored = ObservationSet.from_dict(data)
        self.assertEqual(restored.observation_set_id, obs_set.observation_set_id)
        self.assertEqual(restored.execution_id, obs_set.execution_id)
        self.assertEqual(restored.project_id, obs_set.project_id)
        self.assertEqual(restored.completeness, obs_set.completeness)
        self.assertEqual(len(restored.observations), 2)
        self.assertEqual(restored.metadata, {"run_env": "chromium"})

        # 12b. Evaluation methods rejection
        with self.assertRaises(TesterBoundaryViolationError) as cm:
            obs_set.to_defect()
        self.assertEqual(cm.exception.action, "OBSERVATION_SET_TO_DEFECT")

        with self.assertRaises(TesterBoundaryViolationError) as cm:
            obs_set.to_finding()
        self.assertEqual(cm.exception.action, "OBSERVATION_SET_TO_FINDING")

        with self.assertRaises(TesterBoundaryViolationError) as cm:
            obs_set.to_recommendation()
        self.assertEqual(cm.exception.action, "OBSERVATION_SET_TO_RECOMMENDATION")

        with self.assertRaises(TesterBoundaryViolationError) as cm:
            obs_set.assert_verdict()
        self.assertEqual(cm.exception.action, "OBSERVATION_SET_ASSERT_VERDICT")

        # 12c. Forbidden evaluative keys in metadata
        for forbidden in ["is_defect", "verdict", "ux_score", "visual_score", "quality_score"]:
            with self.assertRaises(TesterBoundaryViolationError) as cm:
                ObservationSet(
                    observation_set_id=new_observation_set_id(),
                    execution_id=self.execution_id,
                    project_id=self.project_id,
                    metadata={forbidden: True},
                )
            self.assertEqual(cm.exception.action, "OBSERVATION_SET_EVALUATION")


if __name__ == "__main__":
    unittest.main()

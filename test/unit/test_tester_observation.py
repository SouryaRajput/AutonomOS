from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from core.events.types import EventType
from core.tester import (
    ObservationBinder,
    ObservationConfidence,
    ObservationType,
    TesterBoundaryViolationError,
    TesterEvidence,
    TesterExecution,
    TesterExecutionStatus,
    TesterLineageError,
    TesterObservation,
    TesterValidationError,
    new_evidence_id,
    new_execution_id,
    new_observation_id,
    new_step_id,
    new_test_case_id,
    new_work_order_id,
    validate_observation_id,
)


class TestTesterObservation(unittest.TestCase):
    """
    Validation test suite for Tester V1 Phase 4.1: Observation Model & Evidence Binding.
    Verifies:
    1. Descriptive nature & evaluative key rejection
    2. Evaluation API rejection (to_defect, to_finding)
    3. Strict immutability after creation
    4. Causal lineage preservation
    5. ObservationBinder binding helpers
    6. Explicit uncertainty handling
    7. TesterExecution.record_observation enforcement
    8. TesterExecution.get_observations filtering
    9. Serialization round-trip (to_dict, from_dict)
    10. Event emission (TEST_OBSERVATION_RECORDED)
    11. Multi-observation execution sequence
    12. Invalid identifier handling
    """

    def setUp(self) -> None:
        self.project_id = "proj-obs-test"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.test_case_id = new_test_case_id()
        self.test_step_id = new_step_id()
        self.evidence_id = new_evidence_id()

    def _make_execution(self) -> TesterExecution:
        return TesterExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id="task-obs-01",
            project_id=self.project_id,
            correlation_id="corr-obs-01",
            status=TesterExecutionStatus.RUNNING,
        )

    def test_scenario_01_descriptive_nature_rejects_evaluative_keys(self) -> None:
        """Observation must be descriptive and reject evaluative keys like verdict, defect, is_failure."""
        # Clean observed state succeeds
        obs = TesterObservation(
            observation_id=new_observation_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            observation_type=ObservationType.SCREEN,
            description="Button 'Submit' visible in viewport",
            observed_state={"element": "button", "text": "Submit", "visible": True},
        )
        self.assertEqual(obs.observation_type, ObservationType.SCREEN)
        self.assertEqual(obs.observed_state["text"], "Submit")

        # Reject evaluative keys in observed_state
        for bad_key in ("is_defect", "verdict", "is_failure", "passed", "defect_severity"):
            with self.subTest(bad_key=bad_key):
                with self.assertRaises(TesterBoundaryViolationError) as ctx:
                    TesterObservation(
                        observation_id=new_observation_id(),
                        execution_id=self.execution_id,
                        project_id=self.project_id,
                        observed_state={bad_key: True, "color": "red"},
                    )
                self.assertIn("Observations are descriptive only", str(ctx.exception))

    def test_scenario_02_evaluation_api_rejection(self) -> None:
        """Observations cannot directly produce defects or findings."""
        obs = TesterObservation(
            observation_id=new_observation_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            observation_type=ObservationType.TEXT,
            description="Modal dialog text observed",
            observed_state={"text": "Internal Server Error"},
        )
        with self.assertRaises(TesterBoundaryViolationError) as ctx_def:
            obs.to_defect()
        self.assertIn("cannot produce TesterDefect directly", str(ctx_def.exception))

        with self.assertRaises(TesterBoundaryViolationError) as ctx_find:
            obs.to_finding()
        self.assertIn("cannot produce TesterFinding directly", str(ctx_find.exception))

    def test_scenario_03_strict_immutability(self) -> None:
        """TesterObservation must be strictly immutable once initialized."""
        obs = TesterObservation(
            observation_id=new_observation_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            observation_type=ObservationType.GEOMETRY,
            description="Coordinates of search bar",
            observed_state={"x": 100, "y": 200},
        )
        with self.assertRaises(TesterBoundaryViolationError) as ctx:
            obs.description = "Mutated description"
        self.assertIn("immutable", str(ctx.exception))

        with self.assertRaises(TesterBoundaryViolationError):
            obs.confidence = 0.5

    def test_scenario_04_causal_lineage_preservation(self) -> None:
        """TesterObservation preserves full causal lineage (execution, test case, step, evidence)."""
        ev_id_1 = new_evidence_id()
        ev_id_2 = new_evidence_id()
        obs = TesterObservation(
            observation_id=new_observation_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
            evidence_ids=[ev_id_1, ev_id_2],
            observation_type=ObservationType.SCREEN,
            description="Visual state captured across two evidence snapshots",
            observed_state={"viewport": "desktop"},
        )
        self.assertEqual(obs.execution_id, self.execution_id)
        self.assertEqual(obs.project_id, self.project_id)
        self.assertEqual(obs.test_case_id, self.test_case_id)
        self.assertEqual(obs.test_step_id, self.test_step_id)
        self.assertEqual(obs.evidence_ids, [ev_id_1, ev_id_2])

    def test_scenario_05_observation_binder_helpers(self) -> None:
        """ObservationBinder supports binding various observation types cleanly."""
        ev = TesterEvidence(
            evidence_id=self.evidence_id,
            data="screenshot data",
            execution_id=self.execution_id,
            metadata={"project_id": self.project_id},
        )

        # 1. Screenshot
        obs_screen = ObservationBinder.bind_screenshot(
            execution_id=self.execution_id,
            project_id=self.project_id,
            evidence=ev,
            test_case_id=self.test_case_id,
            viewport={"width": 1280, "height": 720},
        )
        self.assertEqual(obs_screen.observation_type, ObservationType.SCREEN)
        self.assertEqual(obs_screen.evidence_ids, [self.evidence_id])

        # 2. Video frame
        obs_video = ObservationBinder.bind_video_frame(
            execution_id=self.execution_id,
            project_id=self.project_id,
            frame_index=15,
            timestamp_ms=500.0,
            evidence=ev,
        )
        self.assertEqual(obs_video.observation_type, ObservationType.VIDEO_FRAME)
        self.assertEqual(obs_video.observed_state["frame_index"], 15)

        # 3. Geometry
        obs_geom = ObservationBinder.bind_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            bounding_box={"x": 50, "y": 100, "width": 200, "height": 40},
            target_element="#submit-btn",
        )
        self.assertEqual(obs_geom.observation_type, ObservationType.GEOMETRY)
        self.assertEqual(obs_geom.observed_state["target_element"], "#submit-btn")

        # 4. Text
        obs_text = ObservationBinder.bind_text(
            execution_id=self.execution_id,
            project_id=self.project_id,
            text="Welcome back, user",
            target="h1.greeting",
        )
        self.assertEqual(obs_text.observation_type, ObservationType.TEXT)
        self.assertEqual(obs_text.observed_state["text"], "Welcome back, user")

        # 5. UI State
        obs_ui = ObservationBinder.bind_ui_state(
            execution_id=self.execution_id,
            project_id=self.project_id,
            ui_state={"dropdown_open": True, "active_tab": 2},
        )
        self.assertEqual(obs_ui.observation_type, ObservationType.UI_STATE)

        # 6. Runtime State
        obs_rt = ObservationBinder.bind_runtime_state(
            execution_id=self.execution_id,
            project_id=self.project_id,
            runtime_state={"network_idle": True, "cookies_count": 3},
        )
        self.assertEqual(obs_rt.observation_type, ObservationType.RUNTIME_STATE)

        # 7. Metric
        obs_metric = ObservationBinder.bind_metric(
            execution_id=self.execution_id,
            project_id=self.project_id,
            metric_name="dom_interactive",
            metric_value=235.4,
            unit="ms",
        )
        self.assertEqual(obs_metric.observation_type, ObservationType.METRIC)
        self.assertEqual(obs_metric.observed_state["metric_value"], 235.4)

    def test_scenario_06_explicit_uncertainty_handling(self) -> None:
        """Low confidence observations preserve explicit uncertainty and do not inflate to facts."""
        obs = TesterObservation(
            observation_id=new_observation_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            observation_type=ObservationType.TEXT,
            description="Partially occluded text",
            observed_state={"text": "Log..."},
            confidence=0.45,
            is_uncertain=True,
        )
        self.assertTrue(obs.is_uncertain)
        self.assertAlmostEqual(obs.confidence, 0.45)

        # Automatically flags uncertainty if confidence < 0.8
        obs2 = TesterObservation(
            observation_id=new_observation_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            confidence=0.6,
        )
        self.assertTrue(obs2.is_uncertain)

    def test_scenario_07_record_observation_enforcement(self) -> None:
        """TesterExecution.record_observation validates lineage, project isolation, and duplicates."""
        execution = self._make_execution()
        obs = TesterObservation(
            observation_id=new_observation_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            observation_type=ObservationType.UI_STATE,
            description="Initial UI state",
        )
        execution.record_observation(obs)
        self.assertEqual(len(execution.observations), 1)

        # Duplicate rejection
        with self.assertRaises(TesterValidationError):
            execution.record_observation(obs)

        # Foreign execution_id rejection
        foreign_exec_obs = TesterObservation(
            observation_id=new_observation_id(),
            execution_id=new_execution_id(),
            project_id=self.project_id,
            description="Foreign execution",
        )
        with self.assertRaises(TesterLineageError):
            execution.record_observation(foreign_exec_obs)

        # Cross-tenant project_id rejection
        cross_tenant_obs = TesterObservation(
            observation_id=new_observation_id(),
            execution_id=self.execution_id,
            project_id="other-project",
            description="Cross tenant",
        )
        with self.assertRaises(TesterLineageError):
            execution.record_observation(cross_tenant_obs)

    def test_scenario_08_get_observations_filtering(self) -> None:
        """get_observations allows filtering by test_case_id, test_step_id, and observation_type."""
        execution = self._make_execution()
        tc1 = new_test_case_id()
        tc2 = new_test_case_id()
        step1 = new_step_id()
        step2 = new_step_id()

        obs1 = TesterObservation(
            observation_id=new_observation_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=tc1,
            test_step_id=step1,
            observation_type=ObservationType.SCREEN,
        )
        obs2 = TesterObservation(
            observation_id=new_observation_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=tc1,
            test_step_id=step2,
            observation_type=ObservationType.TEXT,
        )
        obs3 = TesterObservation(
            observation_id=new_observation_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=tc2,
            test_step_id=step1,
            observation_type=ObservationType.SCREEN,
        )

        execution.record_observation(obs1)
        execution.record_observation(obs2)
        execution.record_observation(obs3)

        self.assertEqual(len(execution.get_observations(test_case_id=tc1)), 2)
        self.assertEqual(len(execution.get_observations(test_case_id=tc2)), 1)
        self.assertEqual(len(execution.get_observations(test_step_id=step1)), 2)
        self.assertEqual(len(execution.get_observations(observation_type=ObservationType.SCREEN)), 2)
        self.assertEqual(len(execution.get_observations(observation_type=ObservationType.TEXT)), 1)

    def test_scenario_09_serialization_round_trip(self) -> None:
        """to_dict and from_dict preserve all properties faithfully."""
        obs = TesterObservation(
            observation_id=new_observation_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
            observation_type=ObservationType.METRIC,
            description="Load time metric",
            observed_state={"time_ms": 120},
            evidence_ids=[self.evidence_id],
            confidence=0.95,
            is_uncertain=False,
            provenance={"tool": "performance_api"},
            metadata={"tag": "benchmark"},
        )
        d = obs.to_dict()
        restored = TesterObservation.from_dict(d)

        self.assertEqual(restored.observation_id, obs.observation_id)
        self.assertEqual(restored.execution_id, obs.execution_id)
        self.assertEqual(restored.project_id, obs.project_id)
        self.assertEqual(restored.test_case_id, obs.test_case_id)
        self.assertEqual(restored.test_step_id, obs.test_step_id)
        self.assertEqual(restored.observation_type, obs.observation_type)
        self.assertEqual(restored.description, obs.description)
        self.assertEqual(restored.observed_state, obs.observed_state)
        self.assertEqual(restored.evidence_ids, obs.evidence_ids)
        self.assertEqual(restored.confidence, obs.confidence)
        self.assertEqual(restored.is_uncertain, obs.is_uncertain)
        self.assertEqual(restored.provenance, obs.provenance)

        # Immutability holds on restored object
        with self.assertRaises(TesterBoundaryViolationError):
            restored.description = "Altered after restore"

    def test_scenario_10_event_bus_emission(self) -> None:
        """Event bus receives TEST_OBSERVATION_RECORDED when observation is recorded."""
        mock_event_bus = MagicMock()
        obs = TesterObservation(
            observation_id=new_observation_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            observation_type=ObservationType.TEXT,
            description="Title observed",
            observed_state={"text": "Dashboard"},
        )
        ObservationBinder.emit_observation_event(mock_event_bus, obs)

        mock_event_bus.emit.assert_called_once()
        call_kwargs = mock_event_bus.emit.call_args[1]
        self.assertEqual(call_kwargs["event_type"], EventType.TEST_OBSERVATION_RECORDED)
        self.assertEqual(call_kwargs["payload"]["observation_id"], obs.observation_id)

    def test_scenario_11_multi_observation_sequence(self) -> None:
        """Multiple sequential observations form a coherent factual record."""
        execution = self._make_execution()
        steps = [
            (ObservationType.SCREEN, "Page loaded"),
            (ObservationType.TEXT, "Header confirmed"),
            (ObservationType.GEOMETRY, "Form element located"),
            (ObservationType.UI_STATE, "Form submitted"),
            (ObservationType.METRIC, "Response latency 85ms"),
        ]

        for obs_type, desc in steps:
            obs = TesterObservation(
                observation_id=new_observation_id(),
                execution_id=self.execution_id,
                project_id=self.project_id,
                observation_type=obs_type,
                description=desc,
                observed_state={"step_desc": desc},
            )
            execution.record_observation(obs)

        self.assertEqual(len(execution.observations), 5)
        recorded_types = [o.observation_type for o in execution.observations]
        self.assertEqual(
            recorded_types,
            [
                ObservationType.SCREEN,
                ObservationType.TEXT,
                ObservationType.GEOMETRY,
                ObservationType.UI_STATE,
                ObservationType.METRIC,
            ],
        )

    def test_scenario_12_invalid_identifiers_rejected(self) -> None:
        """Invalid observation_id, execution_id, or missing project_id are rejected."""
        from core.tester.errors import InvalidTesterIdError

        with self.assertRaises(InvalidTesterIdError):
            TesterObservation(
                observation_id="invalid-prefix-123",
                execution_id=self.execution_id,
                project_id=self.project_id,
            )

        with self.assertRaises(InvalidTesterIdError):
            TesterObservation(
                observation_id=new_observation_id(),
                execution_id="invalid-exec-prefix",
                project_id=self.project_id,
            )

        with self.assertRaises(TesterLineageError):
            TesterObservation(
                observation_id=new_observation_id(),
                execution_id=self.execution_id,
                project_id="",
            )


if __name__ == "__main__":
    unittest.main()

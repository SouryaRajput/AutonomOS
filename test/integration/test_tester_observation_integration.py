from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from core.models import Task
from core.tester import (
    ApplicableTestCategory,
    EvidenceType,
    FrameExtractionStatus,
    FrameSelectionStrategy,
    MockOCRProvider,
    ObservationAggregator,
    ObservationCompleteness,
    ObservationSet,
    ObservationType,
    Point,
    Rectangle,
    TestCase,
    TestContext,
    TestEnvironment,
    TesterActionType,
    TesterBoundaryViolationError,
    TesterEvidence,
    TesterExecution,
    TesterExecutionStatus,
    TesterLineageError,
    TesterObservation,
    TestObservationPipeline,
    TestPlan,
    TestPlanningPipeline,
    TestScope,
    TestStep,
    TesterValidationError,
    TesterWorkOrder,
    TestingCapability,
    VideoFrameExtractor,
    VisualGeometryObserver,
    new_evidence_id,
    new_execution_id,
    new_plan_id,
    new_step_id,
    new_test_case_id,
    new_work_order_id,
)
from core.tester.contracts.runtime import MockTestRuntime


class TestTesterObservationIntegration(unittest.TestCase):
    """
    Integration verification test suite for Tester V1 Phase 4.6:
    Observation Integration & Boundary Verification.
    
    Verifies all mandatory Phase 4.6 requirements:
    1. Complete end-to-end observation pipeline:
       ManagerTask -> TesterWorkOrder -> TesterExecution -> frozen TestPlan -> runtime startup
       -> authorized interaction -> screenshot -> ScreenObservation -> OCR -> TextObservation
       -> geometry observation -> video recording -> frame observation -> ObservationSet aggregation
       -> cleanup -> final trace.
    2. Capability gating: unauthorized observation actions rejected when capability not granted.
    3. Unavailable OCR handling: records unavailable OCR observation without product failure.
    4. Failed screenshot handling: graceful capture failure, no image fabricated.
    5. Missing video handling: records unavailable video observation gracefully.
    6. Frame timeout handling: records frame extraction timeout observation gracefully.
    7. Budget exhaustion: iteration and time limits enforced.
    8. Cancellation handling: halts cleanly when execution cancelled.
    9. Runtime shutdown & cleanup in finally block.
    10. Execution & tenant isolation (cross-execution / cross-project checks).
    11. False observation prevention: zero fabrication of coordinates, text, frames.
    12. No evaluation leakage: strictly rejects verdicts, findings, and defects.
    13. Unfrozen TestPlan rejection: execution requires frozen plan.
    """

    def setUp(self) -> None:
        self.project_id = "proj-obs-integ-46"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.task_id = "mtask-integ-46"
        self.correlation_id = "corr-obs-integ-46"

    def _make_manager_task(self) -> Task:
        return Task(
            id=self.task_id,
            project_id=self.project_id,
            title="Execute observation verification pipeline",
            objective="Verify multi-modal observations over frozen test plan",
        )

    def _make_work_order(
        self,
        authorized_capabilities: list[TestingCapability] | None = None,
        time_budget: int = 300,
        iteration_budget: int = 20,
        metadata_extra: dict | None = None,
    ) -> TesterWorkOrder:
        all_caps = [
            TestingCapability.NAVIGATE,
            TestingCapability.CLICK,
            TestingCapability.SCREENSHOT,
            TestingCapability.SCREEN_RECORDING,
        ]
        meta = {"target_app": "local_web_portal"}
        if metadata_extra:
            meta.update(metadata_extra)

        return TesterWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Perform factual runtime observations without evaluation",
            test_scope=TestScope(routes=["/dashboard"], components=["#submit-btn"]),
            acceptance_criteria=[{"criterion_id": "crit-01", "description": "Dashboard loads successfully"}],
            authorized_capabilities=authorized_capabilities if authorized_capabilities is not None else all_caps,
            time_budget=time_budget,
            iteration_budget=iteration_budget,
            metadata=meta,
        )

    def _make_execution(self, work_order: TesterWorkOrder) -> TesterExecution:
        return TesterExecution(
            execution_id=self.execution_id,
            work_order_id=work_order.work_order_id,
            task_id=work_order.manager_task_id,
            project_id=work_order.project_id,
            correlation_id=work_order.correlation_id,
            status=TesterExecutionStatus.STARTING,
        )

    def _make_frozen_test_plan(
        self,
        work_order: TesterWorkOrder,
        steps: list[TestStep] | None = None,
    ) -> TestPlan:
        default_steps = [
            TestStep(
                step_number=1,
                step_id="tstep-1",
                action=TesterActionType.NAVIGATE.value,
                target="/dashboard",
                description="Navigate to dashboard and observe screen and text",
                metadata={"requires_screenshot": True, "requires_ocr": True},
            ),
            TestStep(
                step_number=2,
                step_id="tstep-2",
                action=TesterActionType.CLICK.value,
                target="#submit-btn",
                description="Click submit button and observe layout geometry",
                metadata={
                    "requires_geometry": True,
                    "element_id": "submit_button",
                    "element_rect": {"x": 100.0, "y": 200.0, "width": 150.0, "height": 40.0},
                },
            ),
            TestStep(
                step_number=3,
                step_id="tstep-3",
                action=TesterActionType.START_RECORDING.value,
                target="dashboard_flow",
                description="Record animation and observe frames",
                metadata={"requires_recording": True, "requires_video_frame": True},
            ),
        ]

        test_case = TestCase(
            test_case_id="ttest-case-01",
            category=ApplicableTestCategory.UI_INTERACTION,
            objective="Comprehensive Dashboard Observation",
            steps=steps or default_steps,
        )

        plan = TestPlan(
            plan_id=new_plan_id(),
            work_order_id=work_order.work_order_id,
            execution_id=self.execution_id,
            project_id=work_order.project_id,
            test_cases=[test_case],
            time_budget=work_order.time_budget,
            iteration_budget=work_order.iteration_budget,
        )
        plan.mark_validated()
        plan.freeze()
        return plan

    # -----------------------------------------------------------------------
    # Scenario 1: Complete End-to-End Deterministic Pipeline
    # -----------------------------------------------------------------------
    def test_end_to_end_observation_pipeline(self) -> None:
        # 1. ManagerTask
        task = self._make_manager_task()
        self.assertEqual(task.id, self.task_id)

        # 2. TesterWorkOrder
        work_order = self._make_work_order()
        self.assertEqual(work_order.manager_task_id, task.id)

        # 3. TesterExecution
        execution = self._make_execution(work_order)
        self.assertEqual(execution.status, TesterExecutionStatus.STARTING)

        # 4. Frozen TestPlan
        plan = self._make_frozen_test_plan(work_order)
        self.assertTrue(plan.is_frozen)

        # 5. Local Test Application / Runtime
        runtime = MockTestRuntime(execution=execution, work_order=work_order)
        self.assertFalse(runtime.is_running)

        # 6. OCR Provider with deterministic canned text
        ocr_provider = MockOCRProvider()
        ocr_provider.set_canned_result(
            "artifacts/screenshots/",
            extracted_text="Dashboard Ready Welcome User",
        )

        # 7. Initialize Pipeline
        pipeline = TestObservationPipeline(
            ocr_provider=ocr_provider,
            geometry_observer=VisualGeometryObserver(),
            frame_extractor=VideoFrameExtractor(),
            aggregator=ObservationAggregator(),
        )

        # 8. Execute Observations
        observation_sets = pipeline.execute_observations(
            execution=execution,
            work_order=work_order,
            test_plan=plan,
            runtime=runtime,
        )

        # 9. Verify Runtime Lifecycle Transition & Teardown
        self.assertEqual(execution.status, TesterExecutionStatus.RUNNING)
        # Runtime started and was cleanly stopped during cleanup
        self.assertFalse(runtime.is_running)

        # 10. Verify Observation Sets
        self.assertEqual(len(observation_sets), 1)
        obs_set = observation_sets[0]
        self.assertIsInstance(obs_set, ObservationSet)
        self.assertEqual(obs_set.test_case_id, "ttest-case-01")
        self.assertEqual(obs_set.completeness, ObservationCompleteness.COMPLETE)

        # Verify grouped observations by type
        by_type = obs_set.group_by_type()
        self.assertIn("SCREEN", by_type)
        self.assertIn("TEXT", by_type)
        self.assertIn("GEOMETRY", by_type)
        self.assertIn("VIDEO_FRAME", by_type)

        # Verify Screen Observation
        screen_obs = obs_set.get_observations_by_type(ObservationType.SCREEN)
        self.assertGreaterEqual(len(screen_obs), 1)
        self.assertEqual(screen_obs[0].observed_state["status"], "SUCCESS")

        # Verify Text Observation
        text_obs = obs_set.get_observations_by_type(ObservationType.TEXT)
        self.assertGreaterEqual(len(text_obs), 1)
        self.assertEqual(text_obs[0].observed_state["status"], "SUCCESS")
        self.assertIn("Dashboard Ready", text_obs[0].description)

        # Verify Geometry Observation
        geom_obs = obs_set.get_observations_by_type(ObservationType.GEOMETRY)
        self.assertGreaterEqual(len(geom_obs), 1)
        self.assertEqual(geom_obs[0].observed_state["status"], "AVAILABLE")
        self.assertEqual(geom_obs[0].observed_state["bounds"]["width"], 150.0)

        # Verify Video Frame Observation
        frame_obs = obs_set.get_observations_by_type(ObservationType.VIDEO_FRAME)
        self.assertEqual(len(frame_obs), 3)  # Sampled 3 frames
        for f in frame_obs:
            self.assertEqual(f.observed_state["status"], "SUCCESS")

        # 11. Verify Execution-level Registration
        self.assertEqual(len(execution.observation_sets), 1)
        self.assertGreaterEqual(len(execution.observations), 6)
        self.assertGreaterEqual(len(execution.evidence), 2)  # 1 screenshot, 1 recording

        # 12. Verify Auditable Traces
        obs_traces = [t for t in execution.traces if t.action_type == TesterActionType.RECORD_OBSERVATION]
        self.assertGreaterEqual(len(obs_traces), 1)
        self.assertEqual(obs_traces[0].action_details["completeness"], "COMPLETE")

    # -----------------------------------------------------------------------
    # Scenario 2: Capability Gating
    # -----------------------------------------------------------------------
    def test_unauthorized_observation_capability_gating(self) -> None:
        # Work order authorizes NAVIGATE and CLICK, but NOT SCREENSHOT or SCREEN_RECORDING
        work_order = self._make_work_order(
            authorized_capabilities=[TestingCapability.NAVIGATE, TestingCapability.CLICK]
        )
        execution = self._make_execution(work_order)

        # Test step requires screenshot
        screenshot_step = TestStep(
            step_number=1,
            step_id="tstep-unauth-shot",
            action=TesterActionType.CAPTURE_SCREENSHOT.value,
            description="Capture unauthorized screenshot",
            metadata={"requires_screenshot": True},
        )
        plan_shot = self._make_frozen_test_plan(work_order, steps=[screenshot_step])

        pipeline = TestObservationPipeline()
        with self.assertRaises(TesterBoundaryViolationError) as cm:
            pipeline.execute_observations(
                execution=execution,
                work_order=work_order,
                test_plan=plan_shot,
            )
        self.assertEqual(cm.exception.action, "UNAUTHORIZED_CAPABILITY")
        self.assertIn("SCREENSHOT capability authorization", cm.exception.reason)

        # Test step requires video recording
        video_step = TestStep(
            step_number=1,
            step_id="tstep-unauth-vid",
            action=TesterActionType.START_RECORDING.value,
            description="Capture unauthorized video",
            metadata={"requires_recording": True},
        )
        plan_vid = self._make_frozen_test_plan(work_order, steps=[video_step])
        with self.assertRaises(TesterBoundaryViolationError) as cm:
            pipeline.execute_observations(
                execution=execution,
                work_order=work_order,
                test_plan=plan_vid,
            )
        self.assertEqual(cm.exception.action, "UNAUTHORIZED_CAPABILITY")
        self.assertIn("SCREEN_RECORDING capability authorization", cm.exception.reason)

    # -----------------------------------------------------------------------
    # Scenario 3: Unavailable OCR Handling (Failure Semantics)
    # -----------------------------------------------------------------------
    def test_unavailable_ocr_failure_semantics(self) -> None:
        work_order = self._make_work_order()
        execution = self._make_execution(work_order)

        step = TestStep(
            step_number=1,
            step_id="tstep-ocr-unavail",
            description="Step with OCR text inspection",
            metadata={"requires_screenshot": True, "requires_ocr": True},
        )
        plan = self._make_frozen_test_plan(work_order, steps=[step])

        # OCR provider configured with simulate_unavailable
        unavail_ocr = MockOCRProvider(simulate_unavailable=True)
        pipeline = TestObservationPipeline(ocr_provider=unavail_ocr)

        observation_sets = pipeline.execute_observations(
            execution=execution,
            work_order=work_order,
            test_plan=plan,
        )

        self.assertEqual(len(observation_sets), 1)
        obs_set = observation_sets[0]

        # Completeness is PARTIAL (screenshot succeeded, but OCR was unavailable)
        self.assertEqual(obs_set.completeness, ObservationCompleteness.PARTIAL)

        # Verify OCR observation status is UNAVAILABLE
        text_obs = obs_set.get_observations_by_type(ObservationType.TEXT)
        self.assertEqual(len(text_obs), 1)
        self.assertEqual(text_obs[0].observed_state["status"], "UNAVAILABLE")
        self.assertEqual(text_obs[0].confidence, 0.0)
        self.assertTrue(text_obs[0].is_uncertain)

        # Invariant: NO product failure or defect created
        self.assertEqual(len(execution.defects), 0)
        self.assertEqual(len(execution.findings), 0)

    # -----------------------------------------------------------------------
    # Scenario 4: Failed Screenshot Handling (Zero Image Fabrication)
    # -----------------------------------------------------------------------
    def test_failed_screenshot_handling(self) -> None:
        work_order = self._make_work_order()
        execution = self._make_execution(work_order)

        step = TestStep(
            step_number=1,
            step_id="tstep-shot-fail",
            description="Capture failing screenshot",
            metadata={"requires_screenshot": True, "simulate_screenshot_failure": True},
        )
        plan = self._make_frozen_test_plan(work_order, steps=[step])

        pipeline = TestObservationPipeline()
        observation_sets = pipeline.execute_observations(
            execution=execution,
            work_order=work_order,
            test_plan=plan,
        )

        self.assertEqual(len(observation_sets), 1)
        obs_set = observation_sets[0]
        self.assertEqual(obs_set.completeness, ObservationCompleteness.FAILED)

        screen_obs = obs_set.get_observations_by_type(ObservationType.SCREEN)
        self.assertEqual(len(screen_obs), 1)
        self.assertEqual(screen_obs[0].observed_state["status"], "FAILED")
        self.assertEqual(screen_obs[0].confidence, 0.0)

        # Zero fabrication: no fake evidence was registered in execution.evidence
        self.assertEqual(len(execution.evidence), 0)

    # -----------------------------------------------------------------------
    # Scenario 5: Missing Video Handling (Zero Frame Fabrication)
    # -----------------------------------------------------------------------
    def test_missing_video_handling(self) -> None:
        work_order = self._make_work_order()
        execution = self._make_execution(work_order)

        step = TestStep(
            step_number=1,
            step_id="tstep-vid-missing",
            description="Video animation step with missing file",
            metadata={"requires_recording": True, "requires_video_frame": True, "simulate_missing_video": True},
        )
        plan = self._make_frozen_test_plan(work_order, steps=[step])

        pipeline = TestObservationPipeline()
        observation_sets = pipeline.execute_observations(
            execution=execution,
            work_order=work_order,
            test_plan=plan,
        )

        self.assertEqual(len(observation_sets), 1)
        obs_set = observation_sets[0]

        frame_obs = obs_set.get_observations_by_type(ObservationType.VIDEO_FRAME)
        self.assertEqual(len(frame_obs), 1)
        self.assertEqual(frame_obs[0].observed_state["status"], "UNAVAILABLE")
        self.assertIn("missing or not found", frame_obs[0].observed_state["error_message"])

    # -----------------------------------------------------------------------
    # Scenario 6: Frame Extraction Timeout Handling
    # -----------------------------------------------------------------------
    def test_frame_extraction_timeout_handling(self) -> None:
        work_order = self._make_work_order()
        execution = self._make_execution(work_order)

        step = TestStep(
            step_number=1,
            step_id="tstep-frame-timeout",
            description="Animation step timing out",
            metadata={"requires_recording": True, "requires_video_frame": True},
        )
        plan = self._make_frozen_test_plan(work_order, steps=[step])

        timeout_extractor = VideoFrameExtractor(simulate_timeout=True)
        pipeline = TestObservationPipeline(frame_extractor=timeout_extractor)

        observation_sets = pipeline.execute_observations(
            execution=execution,
            work_order=work_order,
            test_plan=plan,
        )

        self.assertEqual(len(observation_sets), 1)
        obs_set = observation_sets[0]
        frame_obs = obs_set.get_observations_by_type(ObservationType.VIDEO_FRAME)
        self.assertEqual(len(frame_obs), 1)
        self.assertEqual(frame_obs[0].observed_state["status"], "TIMEOUT")

    # -----------------------------------------------------------------------
    # Scenario 7: Budget Enforcement (Iteration & Time Limits)
    # -----------------------------------------------------------------------
    def test_budget_enforcement(self) -> None:
        # WorkOrder with iteration_budget = 1
        work_order = self._make_work_order(iteration_budget=1)
        execution = self._make_execution(work_order)

        step1 = TestStep(step_number=1, step_id="tstep-b1", description="Step 1", metadata={"requires_screenshot": True})
        step2 = TestStep(step_number=2, step_id="tstep-b2", description="Step 2", metadata={"requires_screenshot": True})

        case1 = TestCase(test_case_id="ttest-c1", category=ApplicableTestCategory.UI_INTERACTION, objective="Case 1", steps=[step1])
        case2 = TestCase(test_case_id="ttest-c2", category=ApplicableTestCategory.UI_INTERACTION, objective="Case 2", steps=[step2])

        plan = TestPlan(
            plan_id=new_plan_id(),
            work_order_id=work_order.work_order_id,
            execution_id=execution.execution_id,
            project_id=work_order.project_id,
            test_cases=[case1, case2],
            iteration_budget=1,
        )
        plan.mark_validated()
        plan.freeze()

        pipeline = TestObservationPipeline()
        observation_sets = pipeline.execute_observations(
            execution=execution,
            work_order=work_order,
            test_plan=plan,
        )

        # Halts after 1 iteration; second case is skipped due to budget exhaustion
        self.assertEqual(len(observation_sets), 1)
        self.assertEqual(observation_sets[0].test_case_id, "ttest-c1")

        budget_traces = [
            t for t in execution.traces
            if t.action_details.get("budget_exhausted") == "ITERATION_BUDGET"
        ]
        self.assertEqual(len(budget_traces), 1)

    # -----------------------------------------------------------------------
    # Scenario 8: Cancellation Handling
    # -----------------------------------------------------------------------
    def test_cancellation_handling(self) -> None:
        work_order = self._make_work_order()
        execution = self._make_execution(work_order)
        execution.status = TesterExecutionStatus.CANCELLED

        plan = self._make_frozen_test_plan(work_order)
        pipeline = TestObservationPipeline()

        observation_sets = pipeline.execute_observations(
            execution=execution,
            work_order=work_order,
            test_plan=plan,
        )
        self.assertEqual(len(observation_sets), 0)

    # -----------------------------------------------------------------------
    # Scenario 9: Runtime Shutdown & Cleanup
    # -----------------------------------------------------------------------
    def test_runtime_shutdown_cleanup(self) -> None:
        work_order = self._make_work_order()
        execution = self._make_execution(work_order)
        plan = self._make_frozen_test_plan(work_order)

        runtime = MockTestRuntime(execution=execution, work_order=work_order)
        pipeline = TestObservationPipeline()

        pipeline.execute_observations(
            execution=execution,
            work_order=work_order,
            test_plan=plan,
            runtime=runtime,
        )

        # Runtime must be stopped in the cleanup block
        self.assertFalse(runtime.is_running)

    # -----------------------------------------------------------------------
    # Scenario 10: Execution & Tenant Isolation
    # -----------------------------------------------------------------------
    def test_execution_and_tenant_isolation(self) -> None:
        work_order = self._make_work_order()
        execution = self._make_execution(work_order)

        # 10a. Cross-project TestPlan rejected
        foreign_project_plan = self._make_frozen_test_plan(work_order)
        foreign_project_plan.project_id = "other-tenant"
        pipeline = TestObservationPipeline()

        with self.assertRaises(TesterBoundaryViolationError) as cm:
            pipeline.execute_observations(
                execution=execution,
                work_order=work_order,
                test_plan=foreign_project_plan,
            )
        self.assertEqual(cm.exception.action, "TEST_PLAN_PROJECT_ISOLATION")

        # 10b. Lineage mismatch between WorkOrder and TestPlan
        mismatched_wo_plan = self._make_frozen_test_plan(work_order)
        mismatched_wo_plan.work_order_id = "two-other-order"
        with self.assertRaises(TesterLineageError):
            pipeline.execute_observations(
                execution=execution,
                work_order=work_order,
                test_plan=mismatched_wo_plan,
            )

    # -----------------------------------------------------------------------
    # Scenario 11: False Observation Prevention (Zero Fabrication)
    # -----------------------------------------------------------------------
    def test_false_observation_prevention(self) -> None:
        work_order = self._make_work_order()
        execution = self._make_execution(work_order)

        # Geometry bounds missing: must not fabricate coordinates
        step = TestStep(
            step_number=1,
            step_id="tstep-zero-fab",
            description="Geometry step with missing element bounds",
            metadata={"requires_geometry": True, "simulate_missing_geometry": True},
        )
        plan = self._make_frozen_test_plan(work_order, steps=[step])

        pipeline = TestObservationPipeline()
        observation_sets = pipeline.execute_observations(
            execution=execution,
            work_order=work_order,
            test_plan=plan,
        )

        self.assertEqual(len(observation_sets), 1)
        geom_obs = observation_sets[0].get_observations_by_type(ObservationType.GEOMETRY)
        self.assertEqual(len(geom_obs), 1)
        self.assertEqual(geom_obs[0].observed_state["status"], "UNAVAILABLE")
        # Bounds must NOT exist as fake zeros
        self.assertIsNone(geom_obs[0].observed_state["bounds"])

    # -----------------------------------------------------------------------
    # Scenario 12: No Evaluation Leakage
    # -----------------------------------------------------------------------
    def test_no_evaluation_leakage(self) -> None:
        work_order = self._make_work_order()
        execution = self._make_execution(work_order)
        plan = self._make_frozen_test_plan(work_order)

        pipeline = TestObservationPipeline()
        pipeline.execute_observations(
            execution=execution,
            work_order=work_order,
            test_plan=plan,
        )

        # Pipeline must NEVER create defects, findings, or pass/fail verdicts
        self.assertEqual(len(execution.defects), 0)
        self.assertEqual(len(execution.findings), 0)

        # Direct evaluation methods on pipeline are strictly rejected
        with self.assertRaises(TesterBoundaryViolationError) as cm:
            pipeline.to_defect()
        self.assertEqual(cm.exception.action, "PIPELINE_TO_DEFECT")

        with self.assertRaises(TesterBoundaryViolationError) as cm:
            pipeline.to_finding()
        self.assertEqual(cm.exception.action, "PIPELINE_TO_FINDING")

        with self.assertRaises(TesterBoundaryViolationError) as cm:
            pipeline.assert_verdict()
        self.assertEqual(cm.exception.action, "PIPELINE_ASSERT_VERDICT")

    # -----------------------------------------------------------------------
    # Scenario 13: Unfrozen TestPlan Rejection
    # -----------------------------------------------------------------------
    def test_unfrozen_test_plan_rejected(self) -> None:
        work_order = self._make_work_order()
        execution = self._make_execution(work_order)

        unfrozen_plan = TestPlan(
            plan_id=new_plan_id(),
            work_order_id=work_order.work_order_id,
            execution_id=execution.execution_id,
            project_id=work_order.project_id,
            test_cases=[],
        )
        self.assertFalse(unfrozen_plan.is_frozen)

        pipeline = TestObservationPipeline()
        with self.assertRaises(TesterValidationError):
            pipeline.execute_observations(
                execution=execution,
                work_order=work_order,
                test_plan=unfrozen_plan,
            )


if __name__ == "__main__":
    unittest.main()

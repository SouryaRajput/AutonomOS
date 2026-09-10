from __future__ import annotations

import unittest
from typing import Any, Optional

from core.events.model import Event
from core.events.types import EventSource, EventType
from core.models import Task, WorkerOutput
from core.tester.contracts.animation import (
    AnimationAssertion,
    AnimationStateExpectation,
)
from core.tester.contracts.applicability import (
    CategoryApplicability,
    TestApplicabilityReport,
)
from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.finding import (
    AcceptanceCriterionResult,
    TesterDefect,
    TesterEvidence,
    TesterFinding,
)
from core.tester.contracts.functional_pipeline import FunctionalEvaluationPipeline
from core.tester.contracts.geometry import (
    GeometryObservation,
    Rectangle,
    ViewportDimensions,
    VisualGeometryObserver,
)
from core.tester.contracts.identifiers import (
    new_evidence_id,
    new_execution_id,
    new_frame_observation_id,
    new_geometry_id,
    new_observation_id,
    new_ocr_id,
    new_test_case_id,
    new_work_order_id,
)
from core.tester.contracts.manager_bridge import (
    FakeTesterWorker,
    TesterManagerBridge,
)
from core.tester.contracts.ocr import BoundingBox, OCRResult, TextRegion
from core.tester.contracts.plan import TestCase, TestPlan, TestStep
from core.tester.contracts.responsive import (
    DEFAULT_DESKTOP_PROFILE,
    DEFAULT_MOBILE_PROFILE,
    DEFAULT_TABLET_PROFILE,
    ViewportProfile,
)
from core.tester.contracts.result import TesterResult
from core.tester.contracts.scroll import ScrollAssertion, ScrollBudget, ScrollPosition
from core.tester.contracts.typography import TextRequirement, TypographyAssertion
from core.tester.contracts.ux import (
    UXAssertion,
    UXFlowExpectation,
)
from core.tester.contracts.video_frame import (
    FramePosition,
    VideoFrameObservation,
)
from core.tester.contracts.visual import VisualAssertion
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import TesterBoundaryViolationError
from core.tester.types import (
    AcceptanceCriterionStatus,
    AnimationCheckType,
    ApplicabilityLevel,
    ApplicableTestCategory,
    DefectSeverity,
    DefectType,
    EvidenceType,
    FindingCategory,
    ObservationType,
    PreflightStatus,
    ResponsiveCheckType,
    ScrollDirection,
    ShipRecommendation,
    TestCaseStatus,
    TestPlanStatus,
    TestSurface,
    TesterExecutionStatus,
    TesterResultStatus,
    TypographyCheckType,
    UXCheckType,
    VisibilityState,
    VisualCheckType,
)


class TestTesterVisualUXIntegration(unittest.TestCase):
    """
    Phase 6.6 Integration Test Suite: Visual & UX Evaluation Pipeline Integration.

    Verifies the end-to-end integration of visual, scrolling, responsive,
    typography, animation, and UX flow evaluators into the Tester pipeline:
    1. healthy frontend all pass (SHIP recommendation, zero defects)
    2. deliberately broken frontend (distinct defects for visual, responsive, typography, UX)
    3. backend-only project (skips visual/UX evaluations cleanly)
    4. responsive cross-viewport evaluation
    5. animation frame evaluation
    6. false success prevention (broken UI never receives SHIP)
    7. Manager orchestration roundtrip (Task -> WorkOrder -> Pipeline -> Result -> WorkerOutput)
    """

    def setUp(self) -> None:
        self.recorded_events: list[Event] = []

        def _sink(ev: Event) -> None:
            self.recorded_events.append(ev)

        self.event_sink = _sink
        self.bridge = TesterManagerBridge(event_sink=self.event_sink)
        self.pipeline = FunctionalEvaluationPipeline(
            bridge=self.bridge,
            event_sink=self.event_sink,
        )
        self.project_id = "proj-vis-ux"
        self.execution_id = "texec-ui-integ"
        self.viewport = ViewportDimensions(width=1200.0, height=800.0)
        self.evidence = TesterEvidence(
            evidence_id=new_evidence_id(),
            evidence_type=EvidenceType.SCREENSHOT,
            data="screenshot-payload",
            execution_id=self.execution_id,
            metadata={"project_id": self.project_id},
        )

    def _make_geom(
        self,
        element_id: str,
        bounds: Rectangle,
        viewport: Optional[ViewportDimensions] = None,
        visibility_state: VisibilityState = VisibilityState.VISIBLE,
        source_evidence: Optional[TesterEvidence] = None,
    ) -> GeometryObservation:
        return VisualGeometryObserver.observe_element_geometry(
            execution_id=self.execution_id,
            project_id=self.project_id,
            element_id=element_id,
            bounds=bounds,
            source_evidence=source_evidence or self.evidence,
            viewport=viewport or self.viewport,
            visibility_state=visibility_state,
        )

    def _make_frame(
        self,
        index: int,
        timestamp_ms: float,
        state: str,
        element_id: str = "modal",
    ) -> VideoFrameObservation:
        return VideoFrameObservation(
            frame_observation_id=new_frame_observation_id(),
            execution_id=self.execution_id,
            project_id=self.project_id,
            source_video_evidence_id=self.evidence.evidence_id,
            frame_position=FramePosition(timestamp_ms=timestamp_ms, frame_index=index),
            frame_evidence_id=new_evidence_id(),
            observation_metadata={
                "element_id": element_id,
                "state": state,
                element_id: {"state": state},
            },
            description=f"Element '{element_id}' is in '{state}' state at {timestamp_ms}ms.",
            test_case_id="ttest-frontend-main",
        )

    def _create_base_task(self, title: str = "UI Integration Test") -> Task:
        return Task(
            id="task-ui-integ",
            project_id=self.project_id,
            title=title,
            objective="Verify visual and UX frontend quality",
            success_criteria=["Layout renders cleanly", "Controls are interactive"],
            metadata={
                "correlation_id": "corr-ui-integ-1",
                "acceptance_criteria": [
                    AcceptanceCriterion(
                        criterion_id="ac-ui-1",
                        description="Main container is visible and cleanly positioned",
                    )
                ],
                "test_scope": ["frontend.ui", "frontend.ux"],
            },
        )

    def _create_base_plan_and_execution(
        self,
        task: Task,
        work_order: TesterWorkOrder,
    ) -> tuple[TestPlan, TesterExecution]:
        tc = TestCase(
            test_case_id="ttest-frontend-main",
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Verify frontend page loads and components are accessible",
            steps=[
                TestStep(
                    step_number=1,
                    description="Navigate to dashboard",
                    action="GET /dashboard",
                    expected="Dashboard displayed",
                )
            ],
            expected_outcome="Dashboard rendered without errors",
            acceptance_linkage=["ac-ui-1"],
            covered_surfaces=[TestSurface.UI],
        )
        frozen_plan = TestPlan(
            plan_id="tplan-ui-integ",
            execution_id=self.execution_id,
            work_order_id=work_order.work_order_id,
            project_id=work_order.project_id,
            test_cases=[tc],
            status=TestPlanStatus.FROZEN,
        )
        execution = TesterExecution(
            execution_id=self.execution_id,
            work_order_id=work_order.work_order_id,
            task_id=task.id,
            project_id=task.project_id,
            correlation_id=task.metadata["correlation_id"],
            status=TesterExecutionStatus.STARTING,
        )
        return frozen_plan, execution

    # -----------------------------------------------------------------------
    # Scenario 1: Healthy Frontend All Pass
    # -----------------------------------------------------------------------
    def test_01_healthy_frontend_all_pass(self) -> None:
        """Scenario 1: Clean frontend where visual, responsive, typography, animation, and UX pass."""
        task = self._create_base_task(title="Healthy UI Checkout")
        work_order = self.bridge.issue_work_order(task)
        plan, execution = self._create_base_plan_and_execution(task, work_order)

        runtime_events = [
            {
                "event_type": "HTTP_RESPONSE",
                "url": "http://localhost:8080/dashboard",
                "status_code": 200,
                "is_required_resource": True,
                "is_api": False,
                "test_case_id": "ttest-frontend-main",
            }
        ]

        # 1. Healthy Visual Assertion & Observation
        geom_obs = [
            self._make_geom("hero_banner", Rectangle(x=0, y=0, width=1200, height=300)),
            self._make_geom("submit_btn", Rectangle(x=100, y=350, width=200, height=50)),
        ]
        vis_assertions = [
            VisualAssertion(
                check_type=VisualCheckType.VISIBILITY,
                target_element_id="submit_btn",
                test_case_id="ttest-frontend-main",
            )
        ]

        # 2. Healthy Scrolling
        scroll_assertions = [
            ScrollAssertion(
                target_element_id="content_container",
                direction=ScrollDirection.VERTICAL,
                budget=ScrollBudget(max_distance_px=1000.0),
                metadata={
                    "initial_position": ScrollPosition(scroll_x=0, scroll_y=0, max_scroll_x=0, max_scroll_y=500),
                    "post_position": ScrollPosition(scroll_x=0, scroll_y=250, max_scroll_x=0, max_scroll_y=500),
                },
                test_case_id="ttest-frontend-main",
            )
        ]

        # 3. Healthy Responsive Cross-Viewport
        responsive_checks = [
            {
                "viewport_observations": {
                    DEFAULT_DESKTOP_PROFILE.name: [
                        self._make_geom(
                            "content_container",
                            Rectangle(x=0, y=0, width=1200, height=800),
                            viewport=ViewportDimensions(width=1280, height=800),
                        )
                    ],
                    DEFAULT_MOBILE_PROFILE.name: [
                        self._make_geom(
                            "content_container",
                            Rectangle(x=0, y=0, width=360, height=600),
                            viewport=ViewportDimensions(width=375, height=667),
                        )
                    ],
                },
                "profiles": [DEFAULT_DESKTOP_PROFILE, DEFAULT_MOBILE_PROFILE],
                "test_case_id": "ttest-frontend-main",
            }
        ]

        # 4. Healthy Typography (Required Text present)
        typo_assertions = [
            TypographyAssertion(
                check_type=TypographyCheckType.REQUIRED_TEXT_MISSING,
                target_element_id="submit_btn",
                required_text="Submit Order",
                test_case_id="ttest-frontend-main",
            )
        ]
        ocr_results = [
            OCRResult(
                ocr_id=new_ocr_id(),
                execution_id=self.execution_id,
                project_id=self.project_id,
                source_evidence_id=self.evidence.evidence_id,
                extracted_text="Submit Order",
                confidence=0.98,
                text_regions=[
                    TextRegion(
                        text="Submit Order",
                        confidence=0.98,
                        bounding_box=BoundingBox(x=100.0, y=350.0, width=200.0, height=50.0),
                    )
                ],
            )
        ]

        # 5. Healthy Animation
        anim_assertions = [
            AnimationAssertion(
                check_type=AnimationCheckType.TRANSITION_COMPLETION,
                expectation=AnimationStateExpectation(
                    target_element_id="modal",
                    expected_final_state="visible",
                ),
                test_case_id="ttest-frontend-main",
            )
        ]
        frame_obs = [
            self._make_frame(0, 0.0, "hidden"),
            self._make_frame(1, 300.0, "visible"),
        ]

        # 6. Healthy UX
        ux_assertions = [
            UXAssertion(
                check_type=UXCheckType.REQUIRED_ACTION_BLOCKED,
                target_flow="checkout_flow",
                expectations=[
                    UXFlowExpectation(
                        step_name="confirm",
                        expected_outcome="Order confirmed",
                        actual_outcome="Order confirmed",
                        is_completed=True,
                    )
                ],
                test_case_id="ttest-frontend-main",
            )
        ]

        # Execute pipeline
        exec_out, result, worker_out = self.pipeline.execute(
            work_order=work_order,
            execution=execution,
            test_plan=plan,
            runtime_events=runtime_events,
            visual_assertions=vis_assertions,
            geometry_observations=geom_obs,
            scroll_assertions=scroll_assertions,
            responsive_checks=responsive_checks,
            typography_assertions=typo_assertions,
            ocr_results=ocr_results,
            animation_assertions=anim_assertions,
            frame_observations=frame_obs,
            ux_assertions=ux_assertions,
        )

        # Assertions
        self.assertEqual(result.status, TesterResultStatus.COMPLETED)
        self.assertIn(result.ship_recommendation, (ShipRecommendation.SHIP, ShipRecommendation.SHIP_WITH_WARNINGS))
        self.assertEqual(len(exec_out.defects), 0)
        self.assertEqual(result.visual_ux_summary["visual_defects"], 0)
        self.assertEqual(result.visual_ux_summary["ux_defects"], 0)
        self.assertGreater(len(result.visual_results), 0)
        self.assertGreater(len(result.ux_results), 0)

    # -----------------------------------------------------------------------
    # Scenario 2: Deliberately Broken Frontend Distinct Defects
    # -----------------------------------------------------------------------
    def test_02_deliberately_broken_frontend_distinct_defects(self) -> None:
        """Scenario 2: Broken frontend produces distinct defects across visual, responsive, typography, and UX."""
        task = self._create_base_task(title="Broken UI Checkout")
        work_order = self.bridge.issue_work_order(task)
        plan, execution = self._create_base_plan_and_execution(task, work_order)

        # 1. Visual Defect: Element completely occluded (invisible when required visible)
        vis_assertions = [
            VisualAssertion(
                check_type=VisualCheckType.VISIBILITY,
                target_element_id="hidden_cta",
                test_case_id="ttest-frontend-main",
            )
        ]
        geom_obs = [
            self._make_geom(
                "hidden_cta",
                Rectangle(x=50, y=50, width=100, height=40),
                visibility_state=VisibilityState.HIDDEN,
            )
        ]

        # 2. Responsive Defect: Viewport overflow (element wider than mobile viewport)
        responsive_checks = [
            {
                "viewport_observations": {
                    DEFAULT_MOBILE_PROFILE.name: [
                        self._make_geom(
                            "overflowing_panel",
                            Rectangle(x=0, y=0, width=600, height=400),
                            viewport=ViewportDimensions(width=375, height=667),
                        )
                    ],
                },
                "profiles": [DEFAULT_MOBILE_PROFILE],
                "test_case_id": "ttest-frontend-main",
            }
        ]

        # 3. Typography Defect: Missing required terms of service text
        typo_assertions = [
            TypographyAssertion(
                check_type=TypographyCheckType.REQUIRED_TEXT_MISSING,
                target_element_id="legal_disclaimer",
                required_text="I agree to Terms of Service",
                test_case_id="ttest-frontend-main",
            )
        ]
        ocr_results = [
            OCRResult(
                ocr_id=new_ocr_id(),
                execution_id=self.execution_id,
                project_id=self.project_id,
                source_evidence_id=self.evidence.evidence_id,
                extracted_text="Random garbage text",
                confidence=0.90,
                text_regions=[],
            )
        ]

        # 4. UX Defect: Inaccessible action button blocking completion
        ux_assertions = [
            UXAssertion(
                check_type=UXCheckType.INACCESSIBLE_CONTROL,
                target_flow="checkout_submission",
                expectations=[
                    UXFlowExpectation(
                        step_name="submit_order",
                        expected_outcome="Submit button accessible and clickable",
                        actual_outcome="Submit button is pointer-events: none and disabled",
                        is_completed=False,
                        blocker_reason="Button disabled permanently",
                    )
                ],
                test_case_id="ttest-frontend-main",
            )
        ]

        # Execute pipeline
        exec_out, result, worker_out = self.pipeline.execute(
            work_order=work_order,
            execution=execution,
            test_plan=plan,
            visual_assertions=vis_assertions,
            geometry_observations=geom_obs,
            responsive_checks=responsive_checks,
            typography_assertions=typo_assertions,
            ocr_results=ocr_results,
            ux_assertions=ux_assertions,
        )

        # Assertions
        self.assertEqual(result.status, TesterResultStatus.COMPLETED)
        self.assertEqual(result.ship_recommendation, ShipRecommendation.DO_NOT_SHIP)
        self.assertGreater(len(exec_out.defects), 0)

        # Check distinct defect categories exist
        defect_types = {d.defect_type for d in exec_out.defects}
        self.assertIn(DefectType.VISUAL, defect_types)
        self.assertIn(DefectType.RESPONSIVE, defect_types)
        self.assertIn(DefectType.TYPOGRAPHY, defect_types)
        self.assertIn(DefectType.UX, defect_types)

        # Check visual_ux_summary reflects both visual defects and UX defects
        self.assertGreater(result.visual_ux_summary["visual_defects"], 0)
        self.assertGreater(result.visual_ux_summary["ux_defects"], 0)

    # -----------------------------------------------------------------------
    # Scenario 3: Backend-Only Project Skips Visual & UX
    # -----------------------------------------------------------------------
    def test_03_backend_only_project_skips_visual_ux(self) -> None:
        """Scenario 3: Backend-only task where VISUAL and UX are NOT_APPLICABLE skips visual checks."""
        task = self._create_base_task(title="Backend Microservice")
        work_order = self.bridge.issue_work_order(task)
        plan, execution = self._create_base_plan_and_execution(task, work_order)

        # Explicitly configure applicability: UI categories NOT_APPLICABLE
        app_report = TestApplicabilityReport(
            project_id=task.project_id,
            work_order_id=work_order.work_order_id,
            execution_id=execution.execution_id,
            classifications={
                ApplicableTestCategory.FUNCTIONAL: CategoryApplicability(category=ApplicableTestCategory.FUNCTIONAL, level=ApplicabilityLevel.REQUIRED),
                ApplicableTestCategory.VISUAL: CategoryApplicability(category=ApplicableTestCategory.VISUAL, level=ApplicabilityLevel.NOT_APPLICABLE, rationale="API only"),
                ApplicableTestCategory.RESPONSIVE: CategoryApplicability(category=ApplicableTestCategory.RESPONSIVE, level=ApplicabilityLevel.NOT_APPLICABLE, rationale="API only"),
                ApplicableTestCategory.TYPOGRAPHY: CategoryApplicability(category=ApplicableTestCategory.TYPOGRAPHY, level=ApplicabilityLevel.NOT_APPLICABLE, rationale="API only"),
                ApplicableTestCategory.ANIMATION: CategoryApplicability(category=ApplicableTestCategory.ANIMATION, level=ApplicabilityLevel.NOT_APPLICABLE, rationale="API only"),
                ApplicableTestCategory.UX: CategoryApplicability(category=ApplicableTestCategory.UX, level=ApplicabilityLevel.NOT_APPLICABLE, rationale="API only"),
            },
        )
        execution.attach_test_applicability(app_report)

        # Pass visual assertions that would fail if evaluated
        vis_assertions = [
            VisualAssertion(
                check_type=VisualCheckType.VISIBILITY,
                target_element_id="nonexistent_element",
                test_case_id="ttest-frontend-main",
            )
        ]
        ux_assertions = [
            UXAssertion(
                check_type=UXCheckType.REQUIRED_ACTION_BLOCKED,
                target_flow="nonexistent_flow",
                expectations=[
                    UXFlowExpectation(
                        step_name="step1",
                        is_completed=False,
                    )
                ],
                test_case_id="ttest-frontend-main",
            )
        ]

        exec_out, result, worker_out = self.pipeline.execute(
            work_order=work_order,
            execution=execution,
            test_plan=plan,
            visual_assertions=vis_assertions,
            ux_assertions=ux_assertions,
        )

        # Since VISUAL and UX were NOT_APPLICABLE, they were not evaluated
        self.assertEqual(len(exec_out.visual_results), 0)
        self.assertEqual(len(exec_out.ux_results), 0)
        self.assertEqual(len(exec_out.defects), 0)
        self.assertEqual(result.visual_ux_summary["visual_defects"], 0)
        self.assertEqual(result.visual_ux_summary["ux_defects"], 0)

    # -----------------------------------------------------------------------
    # Scenario 4: Responsive Cross-Viewport Integration
    # -----------------------------------------------------------------------
    def test_04_responsive_cross_viewport_integration(self) -> None:
        """Scenario 4: Responsive evaluations run across desktop, tablet, and mobile."""
        task = self._create_base_task(title="Responsive Portal")
        work_order = self.bridge.issue_work_order(task)
        plan, execution = self._create_base_plan_and_execution(task, work_order)

        responsive_checks = [
            {
                "viewport_observations": {
                    DEFAULT_DESKTOP_PROFILE.name: [
                        self._make_geom(
                            "main_nav",
                            Rectangle(x=0, y=0, width=1280, height=60),
                            viewport=ViewportDimensions(width=1280, height=800),
                        )
                    ],
                    DEFAULT_TABLET_PROFILE.name: [
                        self._make_geom(
                            "main_nav",
                            Rectangle(x=0, y=0, width=768, height=60),
                            viewport=ViewportDimensions(width=768, height=1024),
                        )
                    ],
                    DEFAULT_MOBILE_PROFILE.name: [
                        self._make_geom(
                            "main_nav",
                            Rectangle(x=0, y=0, width=375, height=50),
                            viewport=ViewportDimensions(width=375, height=667),
                        )
                    ],
                },
                "profiles": [DEFAULT_DESKTOP_PROFILE, DEFAULT_TABLET_PROFILE, DEFAULT_MOBILE_PROFILE],
                "test_case_id": "ttest-frontend-main",
            }
        ]

        exec_out, result, worker_out = self.pipeline.execute(
            work_order=work_order,
            execution=execution,
            test_plan=plan,
            responsive_checks=responsive_checks,
        )

        self.assertEqual(len(exec_out.responsive_results), 1)
        self.assertEqual(len(result.responsive_results), 1)
        resp_res = result.responsive_results[0]
        self.assertEqual(resp_res.total_viewports_evaluated, 3)

    # -----------------------------------------------------------------------
    # Scenario 5: Animation Frame Evaluation Integration
    # -----------------------------------------------------------------------
    def test_05_animation_frame_evaluation_integration(self) -> None:
        """Scenario 5: Animation evaluation runs frame sequence observations through the pipeline."""
        task = self._create_base_task(title="Animated Toast")
        work_order = self.bridge.issue_work_order(task)
        plan, execution = self._create_base_plan_and_execution(task, work_order)

        anim_assertions = [
            AnimationAssertion(
                check_type=AnimationCheckType.TRANSITION_COMPLETION,
                expectation=AnimationStateExpectation(
                    target_element_id="toast_notification",
                    expected_final_state="dismissed",
                ),
                test_case_id="ttest-frontend-main",
            )
        ]
        frame_obs = [
            self._make_frame(0, 0.0, "active", element_id="toast_notification"),
            self._make_frame(1, 250.0, "fading", element_id="toast_notification"),
            self._make_frame(2, 500.0, "dismissed", element_id="toast_notification"),
        ]

        exec_out, result, worker_out = self.pipeline.execute(
            work_order=work_order,
            execution=execution,
            test_plan=plan,
            animation_assertions=anim_assertions,
            frame_observations=frame_obs,
        )

        self.assertEqual(len(exec_out.animation_results), 1)
        self.assertEqual(len(result.animation_results), 1)
        self.assertTrue(result.animation_results[0].is_pass)

    # -----------------------------------------------------------------------
    # Scenario 6: False Success Prevention
    # -----------------------------------------------------------------------
    def test_06_false_success_prevention(self) -> None:
        """Scenario 6: Broken UI / blocked UX flow cannot receive SHIP recommendation."""
        task = self._create_base_task(title="Broken Onboarding")
        work_order = self.bridge.issue_work_order(task)
        plan, execution = self._create_base_plan_and_execution(task, work_order)

        ux_assertions = [
            UXAssertion(
                check_type=UXCheckType.ONBOARDING_FAILURE,
                target_flow="user_onboarding",
                expectations=[
                    UXFlowExpectation(
                        step_name="complete_profile",
                        expected_outcome="Profile submitted and dashboard loaded",
                        actual_outcome="Next button unresponsive; trapped in step 1",
                        is_completed=False,
                    )
                ],
                test_case_id="ttest-frontend-main",
            )
        ]

        exec_out, result, worker_out = self.pipeline.execute(
            work_order=work_order,
            execution=execution,
            test_plan=plan,
            ux_assertions=ux_assertions,
        )

        self.assertNotEqual(result.ship_recommendation, ShipRecommendation.SHIP)
        self.assertEqual(result.ship_recommendation, ShipRecommendation.DO_NOT_SHIP)
        self.assertGreater(len(result.defects), 0)
        self.assertTrue(any(d.defect_type == DefectType.UX for d in result.defects))

    # -----------------------------------------------------------------------
    # Scenario 7: Manager Orchestration Roundtrip
    # -----------------------------------------------------------------------
    def test_07_manager_orchestration_roundtrip(self) -> None:
        """Scenario 7: Full orchestration roundtrip from Manager Task to final WorkerOutput."""
        task = self._create_base_task(title="Manager Orchestration Verification")
        work_order = self.bridge.issue_work_order(task)
        plan, execution = self._create_base_plan_and_execution(task, work_order)

        ux_assertions = [
            UXAssertion(
                check_type=UXCheckType.REQUIRED_ACTION_BLOCKED,
                target_flow="standard_flow",
                expectations=[
                    UXFlowExpectation(
                        step_name="load_page",
                        expected_outcome="Page loaded",
                        actual_outcome="Page loaded",
                        is_completed=True,
                    )
                ],
                test_case_id="ttest-frontend-main",
            )
        ]

        exec_out, result, worker_out = self.pipeline.execute(
            work_order=work_order,
            execution=execution,
            test_plan=plan,
            ux_assertions=ux_assertions,
        )

        # Validate WorkerOutput returned to Manager
        self.assertIsInstance(worker_out, WorkerOutput)
        self.assertEqual(worker_out.metadata["task_id"], task.id)
        self.assertEqual(worker_out.metadata["correlation_id"], task.metadata["correlation_id"])
        self.assertIn("visual_ux_summary", worker_out.metadata)
        self.assertIn("visual_defects", worker_out.metadata["visual_ux_summary"])
        self.assertIn("ux_defects", worker_out.metadata["visual_ux_summary"])

        # Check terminal event emitted
        comp_events = [e for e in self.recorded_events if e.event_type == EventType.TESTER_COMPLETED]
        self.assertEqual(len(comp_events), 1)


if __name__ == "__main__":
    unittest.main()

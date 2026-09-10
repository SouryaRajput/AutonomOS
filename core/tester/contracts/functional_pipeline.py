from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import time
from typing import Any, Callable, Optional, Sequence, Union

from core.enums import TaskStatus, WorkerStatus
from core.events.model import Event, new_event_id
from core.events.types import EventSource, EventType
from core.models import Task, WorkerOutput
from core.tester.contracts.blocker import TesterBlocker
from core.tester.contracts.boundary import TesterBoundaryGuard
from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.finding import (
    AcceptanceCriterionResult,
    TesterDefect,
    TesterEvidence,
    TesterFinding,
)
from core.tester.contracts.identifiers import (
    new_blocker_id,
    new_trace_id,
)
from core.tester.contracts.interaction import InteractionEngine
from core.tester.contracts.manager_bridge import TesterManagerBridge
from core.tester.contracts.observation import TesterObservation
from core.tester.contracts.plan import TestPlan
from core.tester.contracts.planning_pipeline import TestPlanningPipeline
from core.tester.contracts.preflight import (
    ResourceCheckResult,
    RouteCheckResult,
    TestPreflightResult,
)
from core.tester.contracts.quality_summary import QualitySummary
from core.tester.contracts.recommendation import TesterRecommendation
from core.tester.contracts.result import TesterResult
from core.tester.contracts.runtime import TestRuntime
from core.tester.contracts.runtime_observation import RuntimeObservation
from core.tester.contracts.session import BrowserSession
from core.tester.contracts.test_case import TestCaseResult
from core.tester.contracts.trace import TesterTrace
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    TesterBlockerError,
    TesterBoundaryViolationError,
    TesterError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.evaluator.defect_classifier import ClassificationResult, DefectClassifier
from core.tester.evaluator.functional_evaluator import FunctionalAcceptanceEvaluator
from core.tester.evaluator.preflight_evaluator import TestPreflightEvaluator
from core.tester.evaluator.runtime_evaluator import RuntimeEvaluator
from core.tester.evaluator.visual_evaluator import VisualEvaluator
from core.tester.evaluator.scroll_evaluator import ScrollEvaluator
from core.tester.evaluator.responsive_evaluator import ResponsiveEvaluator
from core.tester.evaluator.typography_evaluator import TypographyEvaluator
from core.tester.evaluator.animation_evaluator import AnimationEvaluator
from core.tester.evaluator.ux_evaluator import UXFlowEvaluator
from core.tester.contracts.visual import VisualAssertion
from core.tester.contracts.scroll import ScrollAssertion
from core.tester.contracts.responsive import ResponsiveCheck
from core.tester.contracts.typography import TypographyAssertion
from core.tester.contracts.animation import AnimationAssertion
from core.tester.contracts.ux import UXAssertion, UXFlowExpectation
from core.tester.contracts.geometry import GeometryObservation
from core.tester.contracts.ocr import OCRResult
from core.tester.contracts.video_frame import VideoFrameObservation
from core.tester.executor.test_case_executor import TestCaseExecutor
from core.tester.types import (
    AcceptanceCriterionStatus,
    ApplicabilityLevel,
    ApplicableTestCategory,
    DefectSeverity,
    DefectType,
    EvidenceType,
    FindingCategory,
    PreflightDecision,
    PreflightStatus,
    RuntimeEventSeverity,
    RuntimeEventType,
    ShipRecommendation,
    TestCaseStatus,
    TesterActionType,
    TesterBlockerCategory,
    TesterBlockerSeverity,
    TesterExecutionStatus,
    TesterResultStatus,
    TesterWorkOrderStatus,
    TestingCapability,
)

logger = logging.getLogger("AutonomOS.Tester.FunctionalEvaluationPipeline")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class FunctionalEvaluationPipeline:
    """
    Phase 5.6: Functional Evaluation Integration Pipeline.
    
    Orchestrates the authoritative end-to-end evaluation lifecycle:
    TesterWorkOrder
          ↓
    Frozen TestPlan
          ↓
    Preflight Evaluation (Phase 5.1)
          ↓
    Test Case Execution (Phase 5.2)
          ↓
    Runtime Observations & Request Evaluation (Phase 5.3)
          ↓
    Evidence Collection
          ↓
    Functional Acceptance Evaluation (Phase 5.4)
          ↓
    Defect & Finding Classification (Phase 5.5)
          ↓
    Structured TesterResult (Phase 5.6)
          ↓
    Manager Handoff via TesterManagerBridge
    
    Invariants & Architectural Principles:
    1. Tester Must Actually Run:
       Activating this pipeline triggers real evaluation rather than mere worker registration.
    2. Product Failure vs. Tester Failure:
       - Product Failure (failing tests, defects, runtime 500s/404s, failed acceptance criteria)
         results in TesterResultStatus.COMPLETED with failing evaluation and DO_NOT_SHIP.
       - Tester Failure (runtime unavailable, browser unavailable, invalid environment)
         results in TesterResultStatus.BLOCKED or FAILED with NOT_VERIFIED.
    3. Prevent False Success:
       - Never reports PASS without concrete evidence.
       - Execution COMPLETED is separate from product PASS.
       - Skipped tests never masquerade as passing.
       - Preflight failures and runtime 500s/404s never disappear into successful results.
    4. Strict Causal Lineage:
       Preserves Task -> WorkOrder -> Execution -> Result lineage.
    5. Zero Automatic Fixes & Zero Retesting:
       Never alters product source or retests without Manager authorization.
    """
    __test__ = False

    def __init__(
        self,
        bridge: Optional[TesterManagerBridge] = None,
        planning_pipeline: Optional[TestPlanningPipeline] = None,
        preflight_evaluator: Optional[TestPreflightEvaluator] = None,
        test_case_executor: Optional[TestCaseExecutor] = None,
        runtime_evaluator: Optional[RuntimeEvaluator] = None,
        functional_evaluator: Optional[FunctionalAcceptanceEvaluator] = None,
        defect_classifier: Optional[DefectClassifier] = None,
        visual_evaluator: Optional[VisualEvaluator] = None,
        scroll_evaluator: Optional[ScrollEvaluator] = None,
        responsive_evaluator: Optional[ResponsiveEvaluator] = None,
        typography_evaluator: Optional[TypographyEvaluator] = None,
        animation_evaluator: Optional[AnimationEvaluator] = None,
        ux_evaluator: Optional[UXFlowEvaluator] = None,
        event_sink: Optional[Callable[[Event], Any]] = None,
        worker_id: str = "worker.tester",
    ) -> None:
        self.bridge = bridge or TesterManagerBridge(event_sink=event_sink)
        self.planning_pipeline = planning_pipeline or TestPlanningPipeline(bridge=self.bridge)
        self.preflight_evaluator = preflight_evaluator or TestPreflightEvaluator()
        self.test_case_executor = test_case_executor or TestCaseExecutor(event_sink=event_sink)
        self.runtime_evaluator = runtime_evaluator
        self.functional_evaluator = functional_evaluator or FunctionalAcceptanceEvaluator(event_sink=event_sink)
        self.defect_classifier = defect_classifier or DefectClassifier(event_sink=event_sink)
        self.visual_evaluator = visual_evaluator
        self.scroll_evaluator = scroll_evaluator
        self.responsive_evaluator = responsive_evaluator
        self.typography_evaluator = typography_evaluator
        self.animation_evaluator = animation_evaluator
        self.ux_evaluator = ux_evaluator
        self.event_sink = event_sink
        self.worker_id = worker_id

    def execute(
        self,
        work_order: TesterWorkOrder,
        execution: Optional[TesterExecution] = None,
        test_plan: Optional[TestPlan] = None,
        runtime: Optional[TestRuntime] = None,
        interaction_engine: Optional[InteractionEngine] = None,
        session: Optional[BrowserSession] = None,
        environment: Optional[TestEnvironment] = None,
        runtime_capabilities: Optional[Sequence[TestingCapability | str]] = None,
        files_manifest: Optional[Sequence[str]] = None,
        target_routes: Optional[Sequence[str]] = None,
        target_components: Optional[Sequence[str]] = None,
        preflight_routes: Optional[Sequence[Union[str, dict[str, Any], RouteCheckResult]]] = None,
        preflight_resources: Optional[Sequence[Union[str, dict[str, Any], ResourceCheckResult]]] = None,
        preflight_terminal_output: Optional[Union[str, Sequence[str]]] = None,
        build_command: Optional[str] = None,
        build_runner: Optional[Callable[[str], tuple[int, str, str]]] = None,
        mock_app_checker: Optional[Callable[[str], dict[str, Any]]] = None,
        runtime_events: Optional[Sequence[Union[RuntimeObservation, dict[str, Any]]]] = None,
        step_handler: Optional[Callable[..., Any]] = None,
        timeout_seconds: Optional[float] = None,
        require_browser: bool = False,
        browser_available: bool = True,
        runtime_available: bool = True,
        visual_assertions: Optional[Sequence[VisualAssertion]] = None,
        scroll_assertions: Optional[Sequence[ScrollAssertion]] = None,
        responsive_checks: Optional[Sequence[Union[ResponsiveCheck, dict[str, Any]]]] = None,
        typography_assertions: Optional[Sequence[TypographyAssertion]] = None,
        animation_assertions: Optional[Sequence[AnimationAssertion]] = None,
        ux_assertions: Optional[Sequence[UXAssertion]] = None,
        geometry_observations: Optional[Sequence[GeometryObservation]] = None,
        ocr_results: Optional[Sequence[OCRResult]] = None,
        frame_observations: Optional[Sequence[VideoFrameObservation]] = None,
    ) -> tuple[TesterExecution, TesterResult, WorkerOutput]:
        """
        Execute the complete functional evaluation cycle against the authorized WorkOrder.
        Returns the finalized TesterExecution, structured TesterResult, and standard WorkerOutput.
        """
        # -------------------------------------------------------------------
        # 1. Lineage, Execution Activation & Boundary Verification
        # -------------------------------------------------------------------
        work_order.validate()

        if execution is None:
            execution = self.bridge.dispatch_work_order(work_order, worker_id=self.worker_id)
        else:
            execution.validate_lineage(work_order=work_order)
            if execution.status == TesterExecutionStatus.REQUESTED:
                execution.transition_to(
                    TesterExecutionStatus.STARTING,
                    reason="Execution activated by functional pipeline",
                )
            if execution.execution_id not in self.bridge.executions:
                self.bridge.executions[execution.execution_id] = execution

        # Enforce authority boundaries: Tester cannot expand scope or budget
        self.bridge.assert_authority_boundary(work_order, execution)

        # Check early terminal or blocker state
        if execution.is_terminal:
            result = execution.create_result(
                summary_for_manager=f"Execution already terminal: {execution.status.value}",
            )
            worker_output = self.bridge.receive_result(result, execution, work_order)
            return execution, result, worker_output

        if execution.status == TesterExecutionStatus.BLOCKED:
            result = execution.create_result(
                status=TesterResultStatus.BLOCKED,
                summary_for_manager=f"Execution blocked: {'; '.join(b.description for b in execution.active_blockers)}",
                ship_recommendation=ShipRecommendation.NOT_VERIFIED,
            )
            worker_output = self.bridge.receive_result(result, execution, work_order)
            return execution, result, worker_output

        # -------------------------------------------------------------------
        # 2. Tester Failure Detection (Runtime or Browser Unavailable)
        # -------------------------------------------------------------------
        if not runtime_available:
            blocker = TesterBlocker(
                blocker_id=new_blocker_id(),
                work_order_id=work_order.work_order_id,
                execution_id=execution.execution_id,
                category=TesterBlockerCategory.ENVIRONMENT,
                severity=TesterBlockerSeverity.CRITICAL,
                description="Target test runtime is unavailable or failed to initialize.",
                required_decision="Manager intervention required to supply a functioning runtime environment.",
            )
            self.bridge.escalate_blocker(execution, blocker, work_order=work_order)
            result = execution.create_result(
                status=TesterResultStatus.BLOCKED,
                summary_for_manager=f"Tester Failure: {blocker.description}",
                ship_recommendation=ShipRecommendation.NOT_VERIFIED,
            )
            worker_output = self.bridge.receive_result(result, execution, work_order)
            return execution, result, worker_output

        if require_browser and not browser_available:
            blocker = TesterBlocker(
                blocker_id=new_blocker_id(),
                work_order_id=work_order.work_order_id,
                execution_id=execution.execution_id,
                category=TesterBlockerCategory.ENVIRONMENT,
                severity=TesterBlockerSeverity.CRITICAL,
                description="Required browser environment is unavailable or headless display failed.",
                required_decision="Manager intervention required to provision browser capabilities.",
            )
            self.bridge.escalate_blocker(execution, blocker, work_order=work_order)
            result = execution.create_result(
                status=TesterResultStatus.BLOCKED,
                summary_for_manager=f"Tester Failure: {blocker.description}",
                ship_recommendation=ShipRecommendation.NOT_VERIFIED,
            )
            worker_output = self.bridge.receive_result(result, execution, work_order)
            return execution, result, worker_output

        # -------------------------------------------------------------------
        # 3. Frozen Test Plan Resolution (Phase 3 Integration)
        # -------------------------------------------------------------------
        plan: TestPlan
        if test_plan is not None:
            test_plan.validate_lineage(execution=execution, work_order=work_order)
            if not test_plan.is_frozen:
                if not test_plan.is_validated:
                    test_plan.mark_validated()
                test_plan.freeze()
            plan = test_plan
            if execution.test_plan is None:
                execution.attach_test_plan(plan)
        elif execution.test_plan is not None:
            plan = execution.test_plan
        else:
            # Execute planning pipeline
            execution, terminal_result, plan = self.planning_pipeline.execute_planning_pipeline(
                execution=execution,
                work_order=work_order,
                environment=environment or getattr(work_order, "test_environment", None),
                runtime_capabilities=runtime_capabilities or work_order.authorized_capabilities,
                files_manifest=files_manifest,
                target_routes=target_routes,
                target_components=target_components,
            )
            if terminal_result is not None:
                # Terminal planning outcome: NO_APPLICABLE_TESTS
                worker_output = self.bridge.receive_result(terminal_result, execution, work_order)
                return execution, terminal_result, worker_output

        # -------------------------------------------------------------------
        # 4. Preflight Evaluation (Phase 5.1 Integration)
        # -------------------------------------------------------------------
        preflight_result: Optional[TestPreflightResult] = None
        if len(plan.test_cases) > 0:
            preflight_result = self.preflight_evaluator.evaluate_preflight(
                execution=execution,
                work_order=work_order,
                test_plan=plan,
                runtime=runtime,
                session=session,
                routes=preflight_routes,
                resources=preflight_resources,
                terminal_output=preflight_terminal_output,
                build_command=build_command,
                build_runner=build_runner,
                mock_app_checker=mock_app_checker,
                event_sink=self.event_sink,
                timeout_seconds=timeout_seconds or 30.0,
            )
            execution.record_preflight_result(preflight_result)

            # Check preflight decision:
            # - BLOCKED: operational blocker (e.g. port blocked, network blocked)
            if preflight_result.decision == PreflightDecision.BLOCKED:
                blocker_msg = "; ".join(preflight_result.blockers) if preflight_result.blockers else "Preflight environment blocked"
                blocker = TesterBlocker(
                    blocker_id=new_blocker_id(),
                    work_order_id=work_order.work_order_id,
                    execution_id=execution.execution_id,
                    category=TesterBlockerCategory.ENVIRONMENT,
                    severity=TesterBlockerSeverity.CRITICAL,
                    description=blocker_msg,
                    required_decision="Manager intervention required to unblock preflight environment.",
                )
                self.bridge.escalate_blocker(execution, blocker, work_order=work_order)
                result = execution.create_result(
                    status=TesterResultStatus.BLOCKED,
                    summary_for_manager=f"Preflight blocked: {blocker_msg}",
                    ship_recommendation=ShipRecommendation.NOT_VERIFIED,
                )
                worker_output = self.bridge.receive_result(result, execution, work_order)
                return execution, result, worker_output

            # - FAILED: product failure (build failed, compilation error, repeated startup crash)
            elif preflight_result.decision == PreflightDecision.FAILED:
                # Halt execution and classify failure without pretending product passed!
                if execution.status in (TesterExecutionStatus.STARTING, TesterExecutionStatus.PLAN_FROZEN):
                    execution.transition_to(
                        TesterExecutionStatus.RUNNING,
                        reason="Beginning test execution lifecycle",
                    )
                execution.transition_to(
                    TesterExecutionStatus.EVALUATING,
                    reason="Preflight failed: classifying application startup / build failure",
                )

                # Classify preflight failure into defects
                self.defect_classifier.classify(
                    execution=execution,
                    work_order=work_order,
                    test_plan=plan,
                    test_case_results=execution.test_cases,
                    runtime_observations=execution.runtime_observations,
                    acceptance_results=execution.acceptance_results,
                    observations=execution.observations,
                    evidence=execution.evidence,
                )

                # Evaluate acceptance criteria (will fail / not verified due to preflight failure)
                self.functional_evaluator.evaluate(
                    execution=execution,
                    work_order=work_order,
                    test_plan=plan,
                    test_case_results=execution.test_cases,
                    runtime_observations=execution.runtime_observations,
                    observations=execution.observations,
                    evidence=execution.evidence,
                )

                execution.transition_to(
                    TesterExecutionStatus.REPORTING,
                    reason="Preflight failed: compiling final evaluation result",
                )

                fail_reasons = preflight_result.warnings or ["Application failed preflight readiness checks"]
                result = execution.create_result(
                    status=TesterResultStatus.COMPLETED,
                    summary_for_manager=f"Product evaluation failed preflight: {'; '.join(fail_reasons)}",
                    ship_recommendation=ShipRecommendation.DO_NOT_SHIP,
                )
                worker_output = self.bridge.receive_result(result, execution, work_order)
                return execution, result, worker_output

        # -------------------------------------------------------------------
        # 5. Test Case Execution (Phase 5.2 Integration)
        # -------------------------------------------------------------------
        if execution.status != TesterExecutionStatus.RUNNING:
            execution.transition_to(
                TesterExecutionStatus.RUNNING,
                reason=f"Beginning execution of {len(plan.test_cases)} tests from frozen plan '{plan.plan_id}'",
            )

        executor = TestCaseExecutor(
            runtime=runtime,
            interaction_engine=interaction_engine,
            step_handler=step_handler,
            event_sink=self.event_sink,
        )
        executor.execute_plan(
            execution=execution,
            work_order=work_order,
            test_plan=plan,
            runtime=runtime,
            timeout_seconds=timeout_seconds,
        )

        # -------------------------------------------------------------------
        # 6. Runtime & Request Evaluation (Phase 5.3 Integration)
        # -------------------------------------------------------------------
        rt_evaluator = self.runtime_evaluator or RuntimeEvaluator(
            execution=execution,
            work_order=work_order,
            event_sink=self.event_sink,
        )

        # Ingest injected or recorded runtime events if supplied
        if runtime_events:
            for ev_item in runtime_events:
                if isinstance(ev_item, RuntimeObservation):
                    execution.record_runtime_observation(ev_item)
                elif isinstance(ev_item, dict):
                    ev_type_str = str(ev_item.get("event_type", "HTTP_RESPONSE")).upper()
                    if "HTTP" in ev_type_str or "API" in ev_type_str:
                        rt_evaluator.evaluate_http(
                            url=ev_item.get("url", ""),
                            method=ev_item.get("method", "GET"),
                            status_code=ev_item.get("status_code", 200),
                            timing_ms=ev_item.get("duration_ms") or ev_item.get("timing_ms"),
                            is_required=ev_item.get("is_required_resource", ev_item.get("is_required", True)),
                            error_message=ev_item.get("error_message"),
                            test_case_id=ev_item.get("test_case_id"),
                            is_api=ev_item.get("is_api"),
                            evidence_ids=ev_item.get("evidence_ids"),
                        )
                    elif "CONSOLE" in ev_type_str:
                        rt_evaluator.evaluate_console(
                            message=ev_item.get("message", ""),
                            level=ev_item.get("level", "error"),
                            source=ev_item.get("source", "console"),
                            test_case_id=ev_item.get("test_case_id"),
                            evidence_ids=ev_item.get("evidence_ids"),
                        )
                    elif "PROCESS" in ev_type_str or "CRASH" in ev_type_str:
                        rt_evaluator.evaluate_process(
                            message=ev_item.get("message", ""),
                            exit_code=ev_item.get("exit_code"),
                            signal=ev_item.get("signal"),
                            is_crash=ev_item.get("is_crash", False),
                            test_case_id=ev_item.get("test_case_id"),
                            evidence_ids=ev_item.get("evidence_ids"),
                        )

        # -------------------------------------------------------------------
        # 7. Transition to EVALUATING
        # -------------------------------------------------------------------
        execution.transition_to(
            TesterExecutionStatus.EVALUATING,
            reason="Evaluating functional acceptance criteria and classifying defects",
        )

        # -------------------------------------------------------------------
        # 8. Functional Acceptance Evaluation (Phase 5.4 Integration)
        # -------------------------------------------------------------------
        self.functional_evaluator.evaluate(
            execution=execution,
            work_order=work_order,
            test_plan=plan,
            test_case_results=execution.test_cases,
            runtime_observations=execution.runtime_observations,
            observations=execution.observations,
            evidence=execution.evidence,
        )

        # -------------------------------------------------------------------
        # 8.5 Visual & UX Evaluation (Phase 6 Integration)
        # -------------------------------------------------------------------
        def is_category_applicable(cat: ApplicableTestCategory) -> bool:
            if execution.test_applicability is not None:
                item = execution.test_applicability.get_category(cat)
                if item is not None and item.level == ApplicabilityLevel.NOT_APPLICABLE:
                    return False
            return True

        # 1. Visual Geometry & Spatial Evaluation
        if is_category_applicable(ApplicableTestCategory.VISUAL) and (visual_assertions or geometry_observations):
            vis_eval = self.visual_evaluator or VisualEvaluator(
                execution_id=execution.execution_id,
                project_id=execution.project_id,
                work_order_id=work_order.work_order_id,
            )
            v_res = vis_eval.evaluate(
                test_plan=test_plan,
                geometry_observations=geometry_observations or [],
                ocr_results=ocr_results or [],
                evidence=execution.evidence or [],
                visual_assertions=visual_assertions or [],
            )
            execution.record_visual_result(v_res)
            for d in v_res.defects:
                execution.defects.append(d)
            for f in v_res.findings:
                execution.findings.append(f)

        # 2. Scrolling & Overflow Evaluation
        if is_category_applicable(ApplicableTestCategory.VISUAL) and scroll_assertions:
            scrl_eval = self.scroll_evaluator or ScrollEvaluator(
                execution_id=execution.execution_id,
                project_id=execution.project_id,
                work_order_id=work_order.work_order_id,
            )
            for s_assert in scroll_assertions:
                init_pos = s_assert.metadata.get("initial_position")
                post_pos = s_assert.metadata.get("post_position")
                if getattr(s_assert.direction, "value", str(s_assert.direction)) == "HORIZONTAL":
                    s_res = scrl_eval.evaluate_horizontal_scroll(
                        initial_position=init_pos,
                        post_position=post_pos,
                        is_intentional=s_assert.is_intentional_overflow,
                        test_case_id=s_assert.test_case_id,
                    )
                else:
                    s_res = scrl_eval.evaluate_vertical_scroll(
                        initial_position=init_pos,
                        post_position=post_pos,
                        test_case_id=s_assert.test_case_id,
                    )
                execution.record_visual_result(s_res)
                if s_res.defect:
                    execution.defects.append(s_res.defect)

        # 3. Responsive Layout Evaluation
        if is_category_applicable(ApplicableTestCategory.RESPONSIVE) and responsive_checks:
            resp_eval = self.responsive_evaluator or ResponsiveEvaluator(
                execution_id=execution.execution_id,
                project_id=execution.project_id,
                work_order_id=work_order.work_order_id,
            )
            for r_chk in responsive_checks:
                if isinstance(r_chk, dict) and "viewport_observations" in r_chk:
                    r_res = resp_eval.evaluate_cross_viewport(
                        viewport_observations=r_chk["viewport_observations"],
                        profiles=r_chk.get("profiles"),
                        test_case_id=r_chk.get("test_case_id"),
                    )
                elif hasattr(r_chk, "viewport_profile") and hasattr(r_chk, "geometry_observations"):
                    r_res = resp_eval.evaluate_viewport_layout(
                        viewport_profile=r_chk.viewport_profile,
                        geometry_observations=r_chk.geometry_observations,
                        test_case_id=getattr(r_chk, "test_case_id", None),
                    )
                else:
                    continue
                execution.record_responsive_result(r_res)
                for d in r_res.defects:
                    execution.defects.append(d)

        # 4. Typography & Text Presentation Evaluation
        if (is_category_applicable(ApplicableTestCategory.TYPOGRAPHY) or is_category_applicable(ApplicableTestCategory.OCR)) and typography_assertions:
            typo_eval = self.typography_evaluator or TypographyEvaluator(
                execution_id=execution.execution_id,
                project_id=execution.project_id,
                work_order_id=work_order.work_order_id,
            )
            ocr_res = ocr_results[0] if ocr_results else None
            typo_res = typo_eval.evaluate_typography(
                assertions=typography_assertions,
                ocr_result=ocr_res,
                geometry_observations=geometry_observations or [],
            )
            execution.record_typography_result(typo_res)
            for d in typo_res.defects:
                execution.defects.append(d)
            for f in typo_res.findings:
                execution.findings.append(f)

        # 5. Animation & Transition Evaluation
        if (is_category_applicable(ApplicableTestCategory.ANIMATION) or is_category_applicable(ApplicableTestCategory.VIDEO)) and animation_assertions:
            anim_eval = self.animation_evaluator or AnimationEvaluator(
                execution_id=execution.execution_id,
                project_id=execution.project_id,
                work_order_id=work_order.work_order_id,
            )
            anim_res = anim_eval.evaluate_animation(
                assertions=animation_assertions,
                frame_observations=frame_observations or [],
            )
            execution.record_animation_result(anim_res)
            for d in anim_res.defects:
                execution.defects.append(d)
            for f in anim_res.findings:
                execution.findings.append(f)

        # 6. UX Flow & Usability Evaluation
        if is_category_applicable(ApplicableTestCategory.UX) and ux_assertions:
            ux_eval = self.ux_evaluator or UXFlowEvaluator(
                execution_id=execution.execution_id,
                project_id=execution.project_id,
                work_order_id=work_order.work_order_id,
            )
            ux_res = ux_eval.evaluate_ux(
                assertions=ux_assertions,
                test_case_results=execution.test_cases,
            )
            execution.record_ux_result(ux_res)
            for d in ux_res.defects:
                execution.defects.append(d)
            for f in ux_res.findings:
                execution.findings.append(f)
            for r in ux_res.recommendations:
                execution.findings.append(r)

        # -------------------------------------------------------------------
        # 9. Defect & Finding Classification (Phase 5.5 Integration)
        # -------------------------------------------------------------------
        self.defect_classifier.classify(
            execution=execution,
            work_order=work_order,
            test_plan=plan,
            test_case_results=execution.test_cases,
            runtime_observations=execution.runtime_observations,
            acceptance_results=execution.acceptance_results,
            observations=execution.observations,
            evidence=execution.evidence,
        )

        # -------------------------------------------------------------------
        # 10. Transition to REPORTING
        # -------------------------------------------------------------------
        execution.transition_to(
            TesterExecutionStatus.REPORTING,
            reason="Synthesizing authoritative TesterResult package",
        )

        # -------------------------------------------------------------------
        # 11. Determine Advisory Ship Recommendation
        # -------------------------------------------------------------------
        has_critical_defects = any(d.severity == DefectSeverity.CRITICAL for d in execution.defects)
        has_high_defects = any(d.severity == DefectSeverity.HIGH for d in execution.defects)
        has_failed_tests = any(tc.is_fail for tc in execution.test_cases)
        has_failed_criteria = any(ac.status == AcceptanceCriterionStatus.FAIL for ac in execution.acceptance_results)
        has_unverified_criteria = any(ac.status == AcceptanceCriterionStatus.NOT_VERIFIED for ac in execution.acceptance_results)
        has_runtime_failures = any(ro.is_failure and ro.is_application_owned for ro in execution.runtime_observations)

        if has_critical_defects or has_high_defects or has_failed_tests or has_failed_criteria or has_runtime_failures:
            final_ship_rec = ShipRecommendation.DO_NOT_SHIP
        elif execution.defects:
            final_ship_rec = ShipRecommendation.SHIP_WITH_WARNINGS
        elif has_unverified_criteria or not execution.acceptance_results:
            final_ship_rec = ShipRecommendation.NOT_VERIFIED
        else:
            final_ship_rec = ShipRecommendation.SHIP

        # Summary for manager
        passed_count = sum(1 for tc in execution.test_cases if tc.is_pass)
        failed_count = sum(1 for tc in execution.test_cases if tc.is_fail)
        total_tests = len(execution.test_cases)
        defects_count = len(execution.defects)

        if final_ship_rec == ShipRecommendation.SHIP:
            summary = (
                f"Testing passed cleanly: {passed_count}/{total_tests} test cases passed. "
                f"All {len(execution.acceptance_results)} acceptance criteria verified. Zero defects detected. "
                f"Advisory: {final_ship_rec.value}."
            )
        elif final_ship_rec == ShipRecommendation.SHIP_WITH_WARNINGS:
            summary = (
                f"Testing completed with warnings: {passed_count}/{total_tests} test cases passed. "
                f"{defects_count} non-critical defect(s) detected. Advisory: {final_ship_rec.value}."
            )
        elif final_ship_rec == ShipRecommendation.DO_NOT_SHIP:
            summary = (
                f"Testing failed: {failed_count}/{total_tests} test case(s) failed. "
                f"{defects_count} defect(s) detected. Advisory: {final_ship_rec.value}."
            )
        else:
            summary = (
                f"Testing completed with unverified criteria: {passed_count}/{total_tests} tests passed. "
                f"Advisory: {final_ship_rec.value}."
            )

        # -------------------------------------------------------------------
        # 12. Construct Authoritative TesterResult & Return to Manager
        # -------------------------------------------------------------------
        result = execution.create_result(
            status=TesterResultStatus.COMPLETED,
            summary_for_manager=summary,
            ship_recommendation=final_ship_rec,
        )

        # Handoff to Manager via bridge (validates lineage, transitions terminal, emits TESTER_COMPLETED)
        worker_output = self.bridge.receive_result(result, execution, work_order)

        return execution, result, worker_output


# Canonical alias
TesterExecutionPipeline = FunctionalEvaluationPipeline

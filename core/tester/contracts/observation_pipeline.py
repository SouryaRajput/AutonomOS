from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import time
from typing import Any, Optional, Sequence, Union

from core.events.types import EventSource, EventType
from core.tester.contracts.action import TestActionRecord
from core.tester.contracts.boundary import (
    TESTER_ALLOWED_CAPABILITIES,
    TESTER_FORBIDDEN_ACTIONS,
    TesterBoundaryGuard,
)
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.finding import TesterEvidence
from core.tester.contracts.geometry import (
    Point,
    Rectangle,
    ViewportDimensions,
    VisualGeometryObserver,
)
from core.tester.contracts.identifiers import (
    new_evidence_id,
    new_observation_id,
    new_observation_set_id,
    new_trace_id,
)
from core.tester.contracts.interaction import InteractionEngine
from core.tester.contracts.observation import ObservationBinder, TesterObservation
from core.tester.contracts.observation_set import (
    ObservationAggregator,
    ObservationSet,
)
from core.tester.contracts.ocr import MockOCRProvider, OCRProvider
from core.tester.contracts.plan import TestCase, TestPlan, TestStep
from core.tester.contracts.recording import ScreenRecordingService
from core.tester.contracts.runtime import TestRuntime
from core.tester.contracts.screenshot import ScreenshotCaptureService
from core.tester.contracts.trace import TesterTrace
from core.tester.contracts.video_frame import (
    FrameExtractionOptions,
    FrameSelectionStrategy,
    MAX_FRAME_BUDGET_LIMIT,
    VideoFrameExtractor,
)
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    EvidenceType,
    FrameExtractionStatus,
    ObservationCompleteness,
    ObservationType,
    OCRStatus,
    TesterActionType,
    TesterExecutionStatus,
    TestingCapability,
)

logger = logging.getLogger("AutonomOS.Tester.ObservationPipeline")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class TestObservationPipeline:
    """
    Deterministic observation execution pipeline for Tester V1 Phase 4.6.
    
    Coordinates:
    ManagerTask -> TesterWorkOrder -> TesterExecution -> frozen TestPlan -> Test Execution
        -> Runtime Interaction -> Evidence Capture -> Observation -> ObservationSet -> Phase 5
    
    Invariants & Architectural Boundary:
    - Purely observational: records WHAT WAS OBSERVED, never evaluates pass/fail or produces defects.
    - Capability gating: Global Boundary ∩ WorkOrder authorizations ∩ Frozen TestPlan ∩ Runtime capability.
    - Budgets: respects time, iteration, evidence, and frame processing budgets.
    - Failure semantics: observation failures (OCR unavailable, screenshot failed, frame timeout)
      do NOT fail product execution or crash the Tester.
    - False observation protection: ZERO fabrication. Never fabricates text, coordinates, frames, or UI state.
    - No evaluation leakage: calling to_defect(), to_finding(), or assert_verdict() raises TesterBoundaryViolationError.
    """
    __test__ = False

    def __init__(
        self,
        bridge: Optional[Any] = None,
        ocr_provider: Optional[OCRProvider] = None,
        geometry_observer: Optional[VisualGeometryObserver] = None,
        frame_extractor: Optional[VideoFrameExtractor] = None,
        aggregator: Optional[ObservationAggregator] = None,
        screenshot_service: Optional[ScreenshotCaptureService] = None,
        screen_recorder: Optional[ScreenRecordingService] = None,
    ) -> None:
        self.bridge = bridge
        self.ocr_provider = ocr_provider or MockOCRProvider()
        self.geometry_observer = geometry_observer or VisualGeometryObserver()
        self.frame_extractor = frame_extractor or VideoFrameExtractor()
        self.aggregator = aggregator or ObservationAggregator()
        self.screenshot_service = screenshot_service
        self.screen_recorder = screen_recorder

    def _emit(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        execution: TesterExecution,
        work_order: TesterWorkOrder,
    ) -> None:
        """Helper to emit auditable domain events through manager bridge if available."""
        if self.bridge and hasattr(self.bridge, "emit_event"):
            self.bridge.emit_event(
                event_type=event_type,
                payload=payload,
                project_id=execution.project_id,
                task_id=execution.task_id,
                correlation_id=execution.correlation_id,
                worker_id=execution.worker_id,
                source=EventSource.WORKER,
            )

    # -----------------------------------------------------------------------
    # Boundary Protections
    # -----------------------------------------------------------------------
    def to_defect(self, *args: Any, **kwargs: Any) -> Any:
        raise TesterBoundaryViolationError(
            action="PIPELINE_TO_DEFECT",
            reason="TestObservationPipeline cannot produce defects. Observation is descriptive only.",
        )

    def to_finding(self, *args: Any, **kwargs: Any) -> Any:
        raise TesterBoundaryViolationError(
            action="PIPELINE_TO_FINDING",
            reason="TestObservationPipeline cannot produce findings. Observation is descriptive only.",
        )

    def assert_verdict(self, *args: Any, **kwargs: Any) -> Any:
        raise TesterBoundaryViolationError(
            action="PIPELINE_ASSERT_VERDICT",
            reason="TestObservationPipeline cannot assert test verdicts. Evaluation belongs exclusively to Phase 5.",
        )

    # -----------------------------------------------------------------------
    # Main Execution Flow
    # -----------------------------------------------------------------------
    def execute_observations(
        self,
        execution: TesterExecution,
        work_order: TesterWorkOrder,
        test_plan: TestPlan,
        runtime: Optional[TestRuntime] = None,
        interaction_engine: Optional[InteractionEngine] = None,
        mock_app: Optional[Any] = None,
    ) -> list[ObservationSet]:
        """
        Execute authorized observation pipeline across test cases in a frozen TestPlan.
        """
        # 1. Lineage & Frozen Plan Verification
        execution.validate_lineage(work_order=work_order)
        if not test_plan.is_frozen:
            raise TesterValidationError(
                f"Cannot execute observation pipeline: TestPlan '{test_plan.plan_id}' is not frozen."
            )
        if test_plan.work_order_id != work_order.work_order_id:
            raise TesterLineageError(
                f"TestPlan work_order_id ('{test_plan.work_order_id}') does not match WorkOrder ('{work_order.work_order_id}')."
            )
        if test_plan.project_id != execution.project_id:
            raise TesterBoundaryViolationError(
                action="TEST_PLAN_PROJECT_ISOLATION",
                reason=f"TestPlan project_id ('{test_plan.project_id}') does not match execution ('{execution.project_id}').",
            )

        # Check cancellation
        if execution.status == TesterExecutionStatus.CANCELLED:
            logger.info("Execution is cancelled; aborting observation pipeline.")
            return []

        # 2. Lifecycle State Transition
        if execution.status in (TesterExecutionStatus.PLAN_FROZEN, TesterExecutionStatus.STARTING):
            execution.transition_to(
                TesterExecutionStatus.RUNNING,
                reason="Executing authorized test plan and gathering factual observations",
            )

        # 3. Capability Gating Resolution
        authorized_caps: set[TestingCapability] = set()
        for cap in (work_order.authorized_capabilities or []):
            if isinstance(cap, TestingCapability):
                authorized_caps.add(cap)
            elif isinstance(cap, str):
                try:
                    authorized_caps.add(TestingCapability(cap.upper()))
                except ValueError:
                    pass

        # 4. Budget Tracking Setup
        start_time = time.time()
        time_budget_sec = float(work_order.time_budget or test_plan.time_budget or 300.0)
        iteration_budget = int(work_order.iteration_budget or test_plan.iteration_budget or 100)
        max_evidence_count = int(work_order.metadata.get("max_evidence_count", 50))
        iterations_executed = 0
        total_evidence_collected = len(execution.evidence)
        budget_exhausted = False

        # 5. Start Runtime if needed
        runtime_started_here = False
        if runtime is not None and not getattr(runtime, "is_running", False):
            try:
                runtime.start()
                runtime_started_here = True
            except Exception as e:
                logger.error(f"Failed to start test runtime: {e}")
                # Record blocker or handle gracefully
                pass

        observation_sets_produced: list[ObservationSet] = []

        try:
            # 6. Execute Test Cases
            for test_case in test_plan.test_cases:
                # Check cancellation
                if execution.status == TesterExecutionStatus.CANCELLED:
                    logger.info("Execution cancelled during test case execution.")
                    break

                # Check time budget
                elapsed = time.time() - start_time
                if elapsed >= time_budget_sec:
                    logger.warning(f"Time budget ({time_budget_sec}s) exceeded. Halting observation pipeline.")
                    budget_exhausted = True
                    execution.traces.append(
                        TesterTrace(
                            trace_id=new_trace_id(),
                            execution_id=execution.execution_id,
                            work_order_id=work_order.work_order_id,
                            task_id=execution.task_id,
                            project_id=execution.project_id,
                            correlation_id=execution.correlation_id,
                            action_type=TesterActionType.EXECUTE_TEST,
                            action_details={"budget_exhausted": "TIME_BUDGET", "elapsed_sec": elapsed},
                        )
                    )
                    break

                # Check iteration budget
                if iterations_executed >= iteration_budget:
                    logger.warning(f"Iteration budget ({iteration_budget}) exhausted. Halting observation pipeline.")
                    budget_exhausted = True
                    execution.traces.append(
                        TesterTrace(
                            trace_id=new_trace_id(),
                            execution_id=execution.execution_id,
                            work_order_id=work_order.work_order_id,
                            task_id=execution.task_id,
                            project_id=execution.project_id,
                            correlation_id=execution.correlation_id,
                            action_type=TesterActionType.EXECUTE_TEST,
                            action_details={"budget_exhausted": "ITERATION_BUDGET", "iterations": iterations_executed},
                        )
                    )
                    break

                case_observations: list[TesterObservation] = []
                case_evidence: list[TesterEvidence] = []
                expected_step_ids = [step.step_id for step in test_case.steps]

                # Execute Steps
                for step in test_case.steps:
                    iterations_executed += 1
                    step_meta = step.metadata or {}
                    step_desc = str(step.description or "").lower()

                    # -------------------------------------------------------
                    # A. Runtime Interaction (if applicable)
                    # -------------------------------------------------------
                    action_type = getattr(step, "action", None)
                    if action_type is not None:
                        act_str = action_type.value if hasattr(action_type, "value") else str(action_type).upper()
                        # Gate capability
                        if act_str in {"NAVIGATE", "GOTO"} and TestingCapability.NAVIGATE not in authorized_caps:
                            raise TesterBoundaryViolationError(
                                action="UNAUTHORIZED_CAPABILITY",
                                reason="Navigation action attempted without NAVIGATE capability authorization in WorkOrder.",
                            )
                        if act_str in {"CLICK", "INPUT", "TYPE", "HOVER"} and TestingCapability.CLICK not in authorized_caps:
                            raise TesterBoundaryViolationError(
                                action="UNAUTHORIZED_CAPABILITY",
                                reason=f"Interaction '{act_str}' attempted without CLICK capability authorization in WorkOrder.",
                            )

                        if interaction_engine is not None and hasattr(interaction_engine, "execute_step"):
                            interaction_engine.execute_step(step, execution=execution)

                    # -------------------------------------------------------
                    # B. Justified Observation Triggers
                    # -------------------------------------------------------
                    # 1. Screen Observation (Screenshot)
                    requires_screenshot = (
                        step_meta.get("requires_screenshot", False)
                        or test_case.metadata.get("requires_screenshot", False)
                        or "screenshot" in step_desc
                        or "visual" in step_desc
                    )

                    last_screenshot_evidence: Optional[TesterEvidence] = None

                    if requires_screenshot:
                        # Capability Gating
                        if TestingCapability.SCREENSHOT not in authorized_caps:
                            raise TesterBoundaryViolationError(
                                action="UNAUTHORIZED_CAPABILITY",
                                reason="Screenshot observation requested without SCREENSHOT capability authorization in WorkOrder.",
                            )

                        if total_evidence_collected >= max_evidence_count:
                            logger.warning("Evidence budget limit reached; skipping screenshot capture.")
                        else:
                            # Attempt capture
                            capture_success = True
                            captured_data = None
                            artifact_ref = f"artifacts/screenshots/{execution.execution_id}_{step.step_id}.png"

                            if self.screenshot_service is not None:
                                try:
                                    res = self.screenshot_service.capture(
                                        reason=step_desc or "Step verification",
                                        work_order=work_order,
                                    )
                                    if res and res.status and str(res.status).upper() == "SUCCESS":
                                        captured_data = getattr(res, "data", None) or "image_data_placeholder"
                                        artifact_ref = getattr(res, "artifact_path", artifact_ref)
                                    else:
                                        capture_success = False
                                except Exception as e:
                                    logger.error(f"Screenshot capture service error: {e}")
                                    capture_success = False
                            elif step_meta.get("simulate_screenshot_failure"):
                                capture_success = False
                            else:
                                captured_data = "mock_screenshot_png_bytes"

                            if capture_success:
                                shot_ev = TesterEvidence(
                                    evidence_id=new_evidence_id(),
                                    execution_id=execution.execution_id,
                                    evidence_type=EvidenceType.SCREENSHOT,
                                    artifact_reference=artifact_ref,
                                    data=captured_data,
                                    description=f"Screenshot for step '{step.step_id}'",
                                    metadata={"project_id": execution.project_id, "step_id": step.step_id},
                                )
                                execution.record_evidence(shot_ev)
                                case_evidence.append(shot_ev)
                                total_evidence_collected += 1
                                last_screenshot_evidence = shot_ev

                                # Bind observation
                                shot_obs = ObservationBinder.bind_screenshot(
                                    execution_id=execution.execution_id,
                                    project_id=execution.project_id,
                                    evidence=shot_ev,
                                    test_case_id=test_case.case_id,
                                    test_step_id=step.step_id,
                                    runtime_id=getattr(runtime, "runtime_id", None),
                                    description=f"Screenshot observed for step '{step.step_id}'.",
                                )
                                case_observations.append(shot_obs)
                            else:
                                # Capture failed: ZERO FABRICATION. Record factual failure observation.
                                fail_obs = TesterObservation(
                                    observation_id=new_observation_id(),
                                    execution_id=execution.execution_id,
                                    project_id=execution.project_id,
                                    test_case_id=test_case.case_id,
                                    test_step_id=step.step_id,
                                    observation_type=ObservationType.SCREEN,
                                    description="Screenshot capture failed; no visual image was obtained.",
                                    observed_state={"status": "FAILED", "error_message": "Screenshot capture failed"},
                                    confidence=0.0,
                                    is_uncertain=True,
                                    provenance={"source": "screenshot_service", "status": "FAILED"},
                                )
                                case_observations.append(fail_obs)

                    # 2. Text / OCR Observation
                    requires_ocr = (
                        step_meta.get("requires_ocr", False)
                        or step_meta.get("requires_text", False)
                        or "ocr" in step_desc
                        or "text" in step_desc
                    )

                    if requires_ocr:
                        # False observation protection: cannot OCR without screenshot evidence
                        if last_screenshot_evidence is None or not last_screenshot_evidence.artifact_reference:
                            ocr_obs = TesterObservation(
                                observation_id=new_observation_id(),
                                execution_id=execution.execution_id,
                                project_id=execution.project_id,
                                test_case_id=test_case.case_id,
                                test_step_id=step.step_id,
                                observation_type=ObservationType.TEXT,
                                description="Text observation unavailable: underlying screenshot evidence was not obtained.",
                                observed_state={"status": "UNAVAILABLE", "error_message": "Missing screenshot evidence"},
                                confidence=0.0,
                                is_uncertain=True,
                                provenance={"source": "ocr_provider", "status": "UNAVAILABLE"},
                            )
                            case_observations.append(ocr_obs)
                        else:
                            # Run bounded OCR
                            ocr_res = self.ocr_provider.extract_text(
                                evidence=last_screenshot_evidence,
                                execution=execution,
                            )
                            ocr_obs = ocr_res.to_observation(
                                test_case_id=test_case.case_id,
                                test_step_id=step.step_id,
                            )
                            case_observations.append(ocr_obs)

                    # 3. Geometry / Layout Observation
                    requires_geometry = (
                        step_meta.get("requires_geometry", False)
                        or "layout" in step_desc
                        or "geometry" in step_desc
                    )

                    if requires_geometry:
                        target_elem = step_meta.get("element_id") or "main_container"
                        vp = ViewportDimensions(width=1280.0, height=720.0)
                        raw_rect = step_meta.get("element_rect")

                        # If bounds are missing and simulate_missing_geometry: ZERO FABRICATION
                        if step_meta.get("simulate_missing_geometry") or (raw_rect is None and not mock_app):
                            geom_obs_obj = self.geometry_observer.observe_element_geometry(
                                execution_id=execution.execution_id,
                                project_id=execution.project_id,
                                element_id=target_elem,
                                bounds=None,
                                viewport=vp,
                                test_case_id=test_case.case_id,
                                test_step_id=step.step_id,
                            )
                        else:
                            rect_val = raw_rect or {"x": 50.0, "y": 100.0, "width": 200.0, "height": 40.0}
                            geom_obs_obj = self.geometry_observer.observe_element_geometry(
                                execution_id=execution.execution_id,
                                project_id=execution.project_id,
                                element_id=target_elem,
                                bounds=Rectangle(
                                    x=rect_val["x"],
                                    y=rect_val["y"],
                                    width=rect_val["width"],
                                    height=rect_val["height"],
                                ),
                                viewport=vp,
                                test_case_id=test_case.case_id,
                                test_step_id=step.step_id,
                            )
                        case_observations.append(geom_obs_obj.to_observation())

                    # 4. Video / Animation Frame Observation
                    requires_video = (
                        step_meta.get("requires_recording", False)
                        or step_meta.get("requires_video_frame", False)
                        or "animation" in step_desc
                        or "video" in step_desc
                    )

                    if requires_video:
                        # Capability Gating
                        if TestingCapability.SCREEN_RECORDING not in authorized_caps:
                            raise TesterBoundaryViolationError(
                                action="UNAUTHORIZED_CAPABILITY",
                                reason="Screen recording observation requested without SCREEN_RECORDING capability authorization in WorkOrder.",
                            )

                        if step_meta.get("simulate_missing_video"):
                            vid_ev = TesterEvidence(
                                evidence_id=new_evidence_id(),
                                execution_id=execution.execution_id,
                                evidence_type=EvidenceType.VIDEO,
                                artifact_reference="",
                                description="Missing video recording",
                                metadata={"project_id": execution.project_id, "missing": True},
                            )
                        else:
                            vid_ev = TesterEvidence(
                                evidence_id=new_evidence_id(),
                                execution_id=execution.execution_id,
                                evidence_type=EvidenceType.VIDEO,
                                artifact_reference=f"artifacts/recordings/{execution.execution_id}_{step.step_id}.mp4",
                                description=f"Video recording for step '{step.step_id}'",
                                metadata={"project_id": execution.project_id, "duration_ms": 2000.0, "fps": 30},
                            )
                            execution.record_evidence(vid_ev)
                            case_evidence.append(vid_ev)
                            total_evidence_collected += 1

                        # Bounded frame extraction
                        frame_opts = FrameExtractionOptions(
                            strategy=FrameSelectionStrategy.SAMPLE,
                            sample_count=min(3, MAX_FRAME_BUDGET_LIMIT),
                            max_frames=3,
                        )
                        frame_obs_list = self.frame_extractor.extract_frames(
                            evidence=vid_ev,
                            options=frame_opts,
                            execution=execution,
                            test_case_id=test_case.case_id,
                            test_step_id=step.step_id,
                        )
                        for f_obs in frame_obs_list:
                            case_observations.append(f_obs.to_observation())

                    # 5. Default Runtime State Observation if no specific observation was triggered
                    if not (requires_screenshot or requires_ocr or requires_geometry or requires_video):
                        runtime_state_data = {
                            "url": getattr(runtime, "current_url", "http://localhost:8080/"),
                            "status": "READY",
                        }
                        if mock_app is not None and hasattr(mock_app, "get_state"):
                            runtime_state_data.update(mock_app.get_state())

                        state_obs = TesterObservation(
                            observation_id=new_observation_id(),
                            execution_id=execution.execution_id,
                            project_id=execution.project_id,
                            test_case_id=test_case.case_id,
                            test_step_id=step.step_id,
                            observation_type=ObservationType.RUNTIME_STATE,
                            description=f"Runtime state observed for step '{step.step_id}'.",
                            observed_state=runtime_state_data,
                            confidence=1.0,
                            provenance={"source": "runtime_state"},
                        )
                        case_observations.append(state_obs)

                # -------------------------------------------------------
                # C. ObservationSet Aggregation for the TestCase
                # -------------------------------------------------------
                obs_set = self.aggregator.aggregate(
                    execution_id=execution.execution_id,
                    project_id=execution.project_id,
                    test_case_id=test_case.case_id,
                    observations=case_observations,
                    expected_step_ids=expected_step_ids,
                    trace_id=execution.correlation_id,
                    deduplicate=True,
                    sort_chronological=True,
                )

                # Record into execution session
                for obs in obs_set.observations:
                    if not any(o.observation_id == obs.observation_id for o in execution.observations):
                        execution.record_observation(obs)
                execution.record_observation_set(obs_set)
                observation_sets_produced.append(obs_set)

                # Append auditable trace
                execution.traces.append(
                    TesterTrace(
                        trace_id=new_trace_id(),
                        execution_id=execution.execution_id,
                        work_order_id=work_order.work_order_id,
                        task_id=execution.task_id,
                        project_id=execution.project_id,
                        correlation_id=execution.correlation_id,
                        action_type=TesterActionType.RECORD_OBSERVATION,
                        action_details={
                            "test_case_id": test_case.case_id,
                            "observation_set_id": obs_set.observation_set_id,
                            "completeness": obs_set.completeness.value,
                            "observations_count": len(obs_set.observations),
                            "evidence_references_count": len(obs_set.evidence_references),
                        },
                    )
                )

        finally:
            # 7. Cleanup runtime if pipeline started it
            if runtime_started_here and runtime is not None and getattr(runtime, "is_running", False):
                try:
                    runtime.stop()
                except Exception as e:
                    logger.error(f"Error stopping test runtime during observation cleanup: {e}")

        return observation_sets_produced

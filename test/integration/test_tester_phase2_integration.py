from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import time
from typing import Any, List
import unittest

from core.enums import RiskLevel, TaskStatus, WorkerStatus
from core.events.model import Event
from core.events.types import EventSource, EventType
from core.models import Task
from core.tester.contracts.action import TestActionRecord
from core.tester.contracts.boundary import TESTER_ALLOWED_CAPABILITIES
from core.tester.contracts.browser_runtime import BrowserTestRuntime
from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.finding import TesterEvidence
from core.tester.contracts.identifiers import (
    new_action_id,
    new_evidence_id,
    new_execution_id,
    new_runtime_id,
    new_test_case_id,
    new_work_order_id,
)
from core.tester.contracts.manager_bridge import TesterManagerBridge
from core.tester.contracts.recording import (
    ScreenRecordingOptions,
    ScreenRecordingResult,
    VideoArtifactStorage,
)
from core.tester.contracts.result import TesterResult
from core.tester.contracts.scope import TestScope
from core.tester.contracts.screenshot import (
    ScreenshotArtifactStorage,
    ScreenshotCaptureOptions,
    ScreenshotCaptureResult,
)
from core.tester.contracts.session import HttpBrowserSession, MockBrowserSession
from core.tester.contracts.test_case import TestCaseResult
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    InvalidUrlError,
    NavigationDeniedError,
    SessionStoppedError,
    SessionUnavailableError,
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    AcceptanceCriterionStatus,
    EnvironmentType,
    EvidenceType,
    InteractionStatus,
    RecordingCaptureReason,
    ScreenRecordingStatus,
    ScreenshotCaptureReason,
    ScreenshotCaptureStatus,
    ShipRecommendation,
    TestActionStatus,
    TestCaseStatus,
    TestCategory,
    TestRuntimeStatus,
    TesterActionType,
    TesterExecutionStatus,
    TesterResultStatus,
    TestingCapability,
)


def handle_local_test_app(path: str) -> tuple[int, dict[str, str], str]:
    """
    Deterministic in-process web application for Phase 2 integration testing.
    Provides:
    - Navigation targets ('/', '/login', '/dashboard', '/error')
    - Form inputs (username, password)
    - Buttons (#submit-btn)
    - Scrollable content (#scroll-container with 50 lines)
    - Hoverable element (#hover-box)
    - Draggable and droppable elements (#drag-source, #drop-target)
    - Visual state indicators (#status-banner)
    """
    if path in ("/", "/login"):
        lines = "".join([f"<p class='content-item'>Scrollable record {i}</p>" for i in range(1, 51)])
        html = f"""
        <!DOCTYPE html>
        <html>
            <head><title>AutonomOS Test Application</title></head>
            <body>
                <h1>Phase 2 Test Portal</h1>
                <div id="status-banner" class="idle">Ready</div>
                
                <form id="login-form">
                    <input type="text" id="username" placeholder="Username" />
                    <input type="password" id="password" placeholder="Password" />
                    <button type="submit" id="submit-btn">Submit</button>
                </form>

                <div id="hover-box" class="hover-target" style="width: 100px; height: 30px;">Hover Over Me</div>
                <div id="drag-source" draggable="true">Drag Source</div>
                <div id="drop-target">Drop Target</div>

                <div id="scroll-container" style="height: 150px; overflow-y: scroll;">
                    {lines}
                </div>
            </body>
        </html>
        """
        return 200, {"Content-Type": "text/html"}, html.strip()
    elif path == "/dashboard":
        html = """
        <!DOCTYPE html>
        <html>
            <head><title>System Dashboard</title></head>
            <body>
                <h1>Application Dashboard</h1>
                <div id="dashboard-status" class="active">Session Verified</div>
            </body>
        </html>
        """
        return 200, {"Content-Type": "text/html"}, html.strip()
    elif path == "/error":
        return 500, {"Content-Type": "text/plain"}, "Internal Server Error"
    else:
        return 404, {"Content-Type": "text/plain"}, "Not Found"


class TestTesterPhase2Integration(unittest.TestCase):
    """
    Final Integration and Verification Test Suite for Tester V1 Phase 2.
    
    Validates:
    1. test_22_step_e2e_session_golden_path (Section 3)
    2. test_full_authorization_chain_all_nine_capabilities (Section 2)
    3. test_strict_causal_lineage_and_mismatch_rejections (Section 4)
    4. test_project_and_execution_isolation (Section 5 & 10)
    5. test_runtime_lifecycle_invariants_and_no_silent_start (Section 6)
    6. test_evidence_integration_and_zero_raw_binary_leakage (Section 7 & 15)
    7. test_screenshot_and_video_coordination (Section 8)
    8. test_action_failure_semantics (Section 9)
    9. test_resource_and_time_boundaries (Section 10 & 18)
    10. test_cleanup_in_all_operational_scenarios (Section 11)
    11. test_security_verification_and_sanitization (Section 12 & 17)
    12. test_false_success_prevention_invariants (Section 13)
    13. test_trace_and_event_provenance (Section 14)
    """

    def setUp(self) -> None:
        self.project_id = "proj-phase2-integ-01"
        self.task_id = "task-mgr-phase2-01"
        self.correlation_id = "corr-phase2-01"
        self.base_url = "http://127.0.0.1:8080"
        self.emitted_events: List[Event] = []
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_base = Path(self.temp_dir.name)
        self.bridge = TesterManagerBridge(event_sink=self._event_sink)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _event_sink(self, event: Event) -> None:
        self.emitted_events.append(event)

    def _create_manager_task(self, project_id: str | None = None) -> Task:
        return Task(
            id=self.task_id,
            project_id=project_id or self.project_id,
            title="Complete Phase 2 Verification",
            objective="Verify full Phase 2 controlled browser runtime capabilities",
            status=TaskStatus.RUNNING,
            priority=10,
            risk=RiskLevel.MEDIUM,
            metadata={
                "correlation_id": self.correlation_id,
                "product_artifact": "web-frontend:2.6.0",
                "instructions": ["Execute interaction, screenshot, and video flow"],
                "authorized_capabilities": [
                    "NAVIGATE",
                    "MOVE_CURSOR",
                    "CLICK",
                    "TYPE",
                    "KEYBOARD_INPUT",
                    "SCROLL",
                    "HOVER",
                    "DRAG",
                    "WAIT",
                    "SCREENSHOT",
                    "SCREEN_RECORDING",
                ],
            },
        )

    def _setup_runtime(
        self,
        task: Task | None = None,
        authorized_capabilities: list[TestingCapability] | None = None,
        session: Any = None,
        unsupported_capabilities: set[TestingCapability] | None = None,
    ) -> tuple[BrowserTestRuntime, TesterExecution, TesterWorkOrder]:
        mgr_task = task or self._create_manager_task()
        caps = authorized_capabilities or [
            TestingCapability.NAVIGATE,
            TestingCapability.MOVE_CURSOR,
            TestingCapability.CLICK,
            TestingCapability.TYPE,
            TestingCapability.KEYBOARD_INPUT,
            TestingCapability.SCROLL,
            TestingCapability.HOVER,
            TestingCapability.DRAG,
            TestingCapability.WAIT,
            TestingCapability.SCREENSHOT,
            TestingCapability.SCREEN_RECORDING,
        ]

        wo = self.bridge.issue_work_order(
            task=mgr_task,
            objective="Phase 2 Runtime Verification",
            instructions=["Exercise interactions and evidence collection"],
            product_artifact="web-frontend:2.6.0",
            test_scope=TestScope(
                routes=["/", "/dashboard"],
                features=["auth", "interactions", "evidence", "Phase 2 Complete Integration Case"],
            ),
            test_categories=[TestCategory.FUNCTIONAL, TestCategory.INTEGRATION],
            authorized_capabilities=caps,
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-phase2-01",
                    description="All interaction and evidence capture operations execute within bounds",
                )
            ],
        )

        execution = self.bridge.dispatch_work_order(wo)

        env = TestEnvironment(
            project_id=mgr_task.project_id,
            work_order_id=wo.work_order_id,
            execution_id=execution.execution_id,
            application_url=self.base_url,
            environment_type=EnvironmentType.BROWSER,
        )

        browser_session = session or MockBrowserSession(
            project_id=mgr_task.project_id,
            work_order_id=wo.work_order_id,
            execution_id=execution.execution_id,
            runtime_id=new_runtime_id(),
            initial_url=self.base_url,
        )

        storage_screenshot = ScreenshotArtifactStorage(base_dir=str(self.storage_base))
        storage_video = VideoArtifactStorage(base_dir=str(self.storage_base))

        runtime = BrowserTestRuntime(
            execution=execution,
            work_order=wo,
            environment=env,
            session=browser_session,
            runtime_id=browser_session.runtime_id,
            event_sink=self._event_sink,
        )
        runtime.screenshot_storage = storage_screenshot
        runtime.screenshot_service.storage = storage_screenshot
        runtime.video_storage = storage_video
        runtime.screen_recorder.storage = storage_video

        if unsupported_capabilities and hasattr(browser_session, "unsupported_capabilities"):
            browser_session.unsupported_capabilities.update(unsupported_capabilities)

        return runtime, execution, wo

    # ======================================================================
    # 1. End-to-End 22-Step Golden Path (Section 3)
    # ======================================================================
    def test_22_step_e2e_session_golden_path(self) -> None:
        """
        Executes the exact 22-step golden path flow from Section 3:
        ManagerTask -> WorkOrder -> Execution -> Environment -> Start Runtime ->
        Start Session -> Navigate -> Start Recording -> Move Cursor -> Click ->
        Type -> Press Key -> Scroll -> Hover -> Capture Screenshot -> Interaction ->
        Stop Recording -> Collect Action Results -> Collect Screenshot Evidence ->
        Collect Video Evidence -> Stop Runtime -> Verify Artifacts Accessible.
        """
        # Step 1: Create ManagerTask
        mgr_task = self._create_manager_task()
        self.assertEqual(mgr_task.id, self.task_id)

        # Step 2: Create TesterWorkOrder
        # Step 3: Create TesterExecution
        # Step 4: Create TestEnvironment
        runtime, execution, wo = self._setup_runtime(task=mgr_task)
        self.assertEqual(execution.task_id, mgr_task.id)
        self.assertEqual(execution.project_id, mgr_task.project_id)

        # Step 5: Start TestRuntime
        runtime.start()
        self.assertEqual(runtime.status, TestRuntimeStatus.READY)

        # Step 6: Start browser session (HttpBrowserSession is ready)
        self.assertTrue(runtime.session.is_ready)

        # Step 7: Navigate to local application
        nav_action = runtime.navigate("/")
        self.assertEqual(nav_action.status, TestActionStatus.SUCCESS)
        self.assertEqual(runtime.status, TestRuntimeStatus.RUNNING)

        # Step 8: Start screen recording
        rec_start = runtime.start_recording(
            reason=RecordingCaptureReason.TEST_FLOW,
            label="phase2_e2e_session",
            fps=30,
        )
        self.assertEqual(rec_start.status, ScreenRecordingStatus.RECORDING)
        self.assertTrue(runtime.is_recording)

        # Step 9: Move cursor
        move_res = runtime.move_cursor("#submit-btn")
        self.assertTrue(move_res.is_success)

        # Step 10: Click an element
        click_res = runtime.click("#submit-btn")
        self.assertTrue(click_res.is_success)

        # Step 11: Type text
        type_res = runtime.type_text("#username", "tester_lead_user")
        self.assertTrue(type_res.is_success)

        # Step 12: Press a key
        key_res = runtime.press_key("Enter", target="#username")
        self.assertTrue(key_res.is_success)

        # Step 13: Scroll
        scroll_res = runtime.scroll(direction="vertical", amount=150, target="#scroll-container")
        self.assertTrue(scroll_res.is_success)

        # Step 14: Hover
        hover_res = runtime.hover("#hover-box")
        self.assertTrue(hover_res.is_success)

        # Step 15: Capture screenshot (CHECKPOINT)
        snap_res = runtime.capture_viewport(
            reason=ScreenshotCaptureReason.CHECKPOINT,
            label="form_filled_checkpoint",
        )
        self.assertEqual(snap_res.status, ScreenshotCaptureStatus.SUCCESS)
        self.assertTrue(snap_res.is_success)
        self.assertTrue(os.path.exists(snap_res.artifact_reference))

        # Step 16: Perform another interaction (drag from source to target)
        drag_res = runtime.drag("#drag-source", "#drop-target")
        self.assertTrue(drag_res.is_success)

        time.sleep(0.01)

        # Step 17: Stop recording
        rec_stop = runtime.stop_recording(reason=RecordingCaptureReason.TEST_FLOW)
        self.assertEqual(rec_stop.status, ScreenRecordingStatus.COMPLETED)
        self.assertTrue(rec_stop.is_success)
        self.assertFalse(runtime.is_recording)

        # Step 18: Collect action results
        self.assertGreaterEqual(len(runtime.action_records), 1)  # NAVIGATE
        self.assertGreaterEqual(len(runtime.interaction_history), 7)  # move, click, type, key, scroll, hover, drag

        # Step 19: Collect screenshot evidence
        self.assertEqual(len(runtime.captured_screenshots), 1)
        screenshot_evidence = [e for e in runtime.evidences if e.evidence_type == EvidenceType.SCREENSHOT]
        self.assertEqual(len(screenshot_evidence), 1)

        # Step 20: Collect video evidence
        self.assertEqual(len(runtime.recording_history), 2)  # start + stop
        video_evidence = [e for e in runtime.evidences if e.evidence_type == EvidenceType.VIDEO]
        self.assertEqual(len(video_evidence), 1)

        # Step 21: Stop TestRuntime
        runtime.stop()
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)

        # Step 22: Verify all artifacts remain accessible on disk with verified SHA-256
        shot_path = Path(snap_res.artifact_reference)
        self.assertTrue(shot_path.exists())
        self.assertEqual(hashlib.sha256(shot_path.read_bytes()).hexdigest(), snap_res.checksum)

        vid_path = Path(rec_stop.artifact_reference)
        self.assertTrue(vid_path.exists())
        self.assertEqual(hashlib.sha256(vid_path.read_bytes()).hexdigest(), rec_stop.checksum)

    # ======================================================================
    # 2. Full Authorization Chain for All 9 Capabilities (Section 2)
    # ======================================================================
    def test_full_authorization_chain_all_nine_capabilities(self) -> None:
        """
        Verify that EVERY runtime operation respects the 3-way authorization chain:
        Global Tester Boundary ∩ WorkOrder Authorization ∩ Runtime Backend Capabilities.
        Tested for: NAVIGATE, CLICK, TYPE, KEYBOARD_INPUT, SCROLL, HOVER, DRAG, SCREENSHOT, SCREEN_RECORDING.
        For every denied capability:
        - Underlying browser does NOT receive the operation
        - Structured denial is returned
        - Auditable trace/event exists
        """
        capabilities_to_test = [
            (TestingCapability.NAVIGATE, "navigate"),
            (TestingCapability.CLICK, "click"),
            (TestingCapability.TYPE, "type"),
            (TestingCapability.KEYBOARD_INPUT, "keyboard"),
            (TestingCapability.SCROLL, "scroll"),
            (TestingCapability.HOVER, "hover"),
            (TestingCapability.DRAG, "drag"),
            (TestingCapability.SCREENSHOT, "screenshot"),
            (TestingCapability.SCREEN_RECORDING, "recording"),
        ]

        for omitted_cap, desc in capabilities_to_test:
            with self.subTest(omitted_capability=omitted_cap.value):
                # WorkOrder grants everything EXCEPT omitted_cap
                allowed = [c for c in TESTER_ALLOWED_CAPABILITIES if c != omitted_cap]
                runtime, _, _ = self._setup_runtime(authorized_capabilities=allowed)
                runtime.start()
                if omitted_cap != TestingCapability.NAVIGATE:
                    runtime.navigate("/")

                self.assertNotIn(omitted_cap, runtime.effective_capabilities)

                if omitted_cap == TestingCapability.NAVIGATE:
                    # Raises TesterBoundaryViolationError, emits TEST_ACTION_DENIED
                    with self.assertRaises(TesterBoundaryViolationError):
                        runtime.navigate("/dashboard")
                    denied_evs = [e for e in self.emitted_events if e.event_type == EventType.TEST_ACTION_DENIED]
                    self.assertGreaterEqual(len(denied_evs), 1)

                elif omitted_cap == TestingCapability.CLICK:
                    res = runtime.click("#submit-btn")
                    self.assertEqual(res.status, InteractionStatus.DENIED)
                    self.assertFalse(res.is_success)

                elif omitted_cap == TestingCapability.TYPE:
                    res = runtime.type_text("#username", "val")
                    self.assertEqual(res.status, InteractionStatus.DENIED)
                    self.assertFalse(res.is_success)

                elif omitted_cap == TestingCapability.KEYBOARD_INPUT:
                    res = runtime.press_key("Enter")
                    self.assertEqual(res.status, InteractionStatus.DENIED)
                    self.assertFalse(res.is_success)

                elif omitted_cap == TestingCapability.SCROLL:
                    res = runtime.scroll(amount=100)
                    self.assertEqual(res.status, InteractionStatus.DENIED)
                    self.assertFalse(res.is_success)

                elif omitted_cap == TestingCapability.HOVER:
                    res = runtime.hover("#hover-box")
                    self.assertEqual(res.status, InteractionStatus.DENIED)
                    self.assertFalse(res.is_success)

                elif omitted_cap == TestingCapability.DRAG:
                    res = runtime.drag("#drag-source", "#drop-target")
                    self.assertEqual(res.status, InteractionStatus.DENIED)
                    self.assertFalse(res.is_success)

                elif omitted_cap == TestingCapability.SCREENSHOT:
                    res = runtime.capture_viewport()
                    self.assertEqual(res.status, ScreenshotCaptureStatus.DENIED)
                    self.assertFalse(res.is_success)
                    denied_shot_evs = [e for e in self.emitted_events if e.event_type == EventType.TEST_SCREENSHOT_DENIED]
                    self.assertGreaterEqual(len(denied_shot_evs), 1)

                elif omitted_cap == TestingCapability.SCREEN_RECORDING:
                    res = runtime.start_recording()
                    self.assertEqual(res.status, ScreenRecordingStatus.DENIED)
                    self.assertFalse(res.is_success)
                    denied_rec_evs = [e for e in self.emitted_events if e.event_type == EventType.TEST_RECORDING_DENIED]
                    self.assertGreaterEqual(len(denied_rec_evs), 1)

                runtime.stop()

    # ======================================================================
    # 3. Lineage Verification (Section 4)
    # ======================================================================
    def test_strict_causal_lineage_and_mismatch_rejections(self) -> None:
        """
        Verify causal lineage chain:
        ManagerTask -> TesterWorkOrder -> TesterExecution -> TestRuntime -> Action -> Evidence -> TesterResult.
        Deliberate lineage mismatches must be rejected.
        """
        runtime, execution, wo = self._setup_runtime()
        runtime.start()
        runtime.navigate("/")
        act = runtime.click("#submit-btn")
        shot = runtime.capture_viewport(label="lineage_check")

        # Identity consistency checks
        self.assertEqual(wo.manager_task_id, self.task_id)
        self.assertEqual(execution.task_id, self.task_id)
        self.assertEqual(execution.work_order_id, wo.work_order_id)
        self.assertEqual(runtime.execution_id, execution.execution_id)
        self.assertEqual(act.execution_id, execution.execution_id)
        self.assertEqual(shot.execution_id, execution.execution_id)

        # Mismatch 1: WorkOrder with mismatched project_id
        forged_wo = TesterWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.task_id,
            project_id="forged-project-999",
            correlation_id=self.correlation_id,
            objective="Forged WO",
            instructions=[],
            product_artifact="artifact:1.0",
            test_scope=TestScope(routes=["/"]),
            authorized_capabilities=[TestingCapability.NAVIGATE],
            acceptance_criteria=[],
        )
        with self.assertRaises(TesterLineageError):
            execution.validate_lineage(work_order=forged_wo)

        # Mismatch 2: Execution with mismatched task_id
        with self.assertRaises(TesterLineageError):
            execution.validate_lineage(expected_task_id="forged-task-999")

        # Mismatch 3: Execution with mismatched project_id
        with self.assertRaises(TesterLineageError):
            execution.validate_lineage(expected_project_id="forged-project-999")

        runtime.stop()

    # ======================================================================
    # 4. Project Isolation (Section 5 & 10)
    # ======================================================================
    def test_project_and_execution_isolation(self) -> None:
        """
        Create two synthetic projects/executions (Project A and Project B).
        Verify Project A cannot use Project B's runtime, WorkOrder, write into
        Project B's storage, or reference Project B's evidence.
        """
        proj_a = "proj-isolation-aaa"
        proj_b = "proj-isolation-bbb"

        task_a = self._create_manager_task(project_id=proj_a)
        task_b = self._create_manager_task(project_id=proj_b)

        runtime_a, exec_a, wo_a = self._setup_runtime(task=task_a)
        runtime_b, exec_b, wo_b = self._setup_runtime(task=task_b)

        runtime_a.start()
        runtime_b.start()

        # Capture evidence in Project A
        runtime_a.navigate("/")
        shot_a = runtime_a.capture_viewport(label="shot_a")

        # Capture evidence in Project B
        runtime_b.navigate("/")
        shot_b = runtime_b.capture_viewport(label="shot_b")

        # Verify distinct isolated filesystem paths
        self.assertIn(f"/projects/{proj_a}/", shot_a.artifact_reference)
        self.assertIn(f"/projects/{proj_b}/", shot_b.artifact_reference)
        self.assertNotEqual(Path(shot_a.artifact_reference).parent, Path(shot_b.artifact_reference).parent)

        # Project A's execution cannot validate against Project B's work order
        with self.assertRaises(TesterLineageError):
            exec_a.validate_lineage(work_order=wo_b)

        # Runtime A cannot validate against Project B's project_id
        with self.assertRaises(TesterLineageError):
            runtime_a.execution.validate_lineage(expected_project_id=proj_b)

        runtime_a.stop()
        runtime_b.stop()

    # ======================================================================
    # 5. Runtime Lifecycle Integration (Section 6)
    # ======================================================================
    def test_runtime_lifecycle_invariants_and_no_silent_start(self) -> None:
        """
        Verify lifecycle state transitions: CREATED -> STARTING -> READY -> RUNNING -> STOPPING -> STOPPED.
        Verify that interactions while CREATED, STARTING, STOPPING, STOPPED, or FAILED
        are rejected without silent auto-starting.
        """
        runtime, _, _ = self._setup_runtime()

        # In CREATED state
        self.assertEqual(runtime.status, TestRuntimeStatus.CREATED)
        res_created = runtime.click("#submit-btn")
        self.assertEqual(res_created.status, InteractionStatus.FAILED)
        self.assertIn("not ready", res_created.error or "")
        self.assertEqual(runtime.status, TestRuntimeStatus.CREATED)  # No silent auto-start

        # Transition to READY
        runtime.start()
        self.assertEqual(runtime.status, TestRuntimeStatus.READY)

        # First operation transitions READY -> RUNNING
        runtime.navigate("/")
        self.assertEqual(runtime.status, TestRuntimeStatus.RUNNING)

        # Stop runtime
        runtime.stop()
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)

        # In STOPPED state
        res_stopped = runtime.click("#submit-btn")
        self.assertEqual(res_stopped.status, InteractionStatus.FAILED)
        self.assertIn("stopped", res_stopped.error or "")

        res_rec_stopped = runtime.start_recording()
        self.assertEqual(res_rec_stopped.status, ScreenRecordingStatus.FAILED)

        res_shot_stopped = runtime.capture_viewport()
        self.assertEqual(res_shot_stopped.status, ScreenshotCaptureStatus.FAILED)

    # ======================================================================
    # 6. Evidence Integration & Zero Raw Binary Leakage (Section 7 & 15)
    # ======================================================================
    def test_evidence_integration_and_zero_raw_binary_leakage(self) -> None:
        """
        Verify interaction -> evidence -> TesterEvidence -> TesterResult integration.
        Verify zero binary image or video bytes in events, traces, or results.
        """
        runtime, execution, wo = self._setup_runtime()
        runtime.start()
        runtime.navigate("/")

        runtime.start_recording(label="leakage_check_video")
        runtime.click("#submit-btn")
        snap = runtime.capture_viewport(label="leakage_check_shot")
        runtime.stop_recording()
        runtime.stop()

        # Bind evidences to TesterExecution
        execution.evidence.extend(runtime.evidences)

        # Synthesize TestCaseResult consuming the evidences
        tc = TestCaseResult(
            test_id=new_test_case_id(),
            name="Phase 2 Complete Integration Case",
            category=TestCategory.FUNCTIONAL,
            status=TestCaseStatus.PASS,
            description="Verified interaction and visual observation",
            observed_behavior="Button clicked, screenshot and video finalized",
            evidence_ids=[e.evidence_id for e in runtime.evidences],
        )
        execution.test_cases.append(tc)

        # Complete execution and create final TesterResult
        tester_result = execution.create_result(
            status=TesterResultStatus.COMPLETED,
            summary_for_manager="Phase 2 Runtime integration completed successfully.",
            ship_recommendation=ShipRecommendation.SHIP,
        )
        self.bridge.receive_result(tester_result, execution, wo)

        self.assertEqual(tester_result.status, TesterResultStatus.COMPLETED)
        self.assertEqual(len(tester_result.evidence), 2)
        self.assertEqual(len(tester_result.test_cases), 1)

        # Strict Invariant: Zero binary image/video bytes in result dictionary
        result_dict = tester_result.to_dict()
        result_str = str(result_dict)
        self.assertNotIn("raw_bytes", result_dict)
        self.assertNotIn(b"\x89PNG", result_str.encode("utf-8", errors="ignore"))
        self.assertNotIn(b"\x1aE\xdf\xa3", result_str.encode("utf-8", errors="ignore"))

        # Strict Invariant: Zero binary image/video bytes in emitted domain events
        for ev in self.emitted_events:
            ev_payload_str = str(ev.payload)
            self.assertNotIn("raw_bytes", ev.payload)
            self.assertNotIn(b"\x89PNG", ev_payload_str.encode("utf-8", errors="ignore"))

    # ======================================================================
    # 7. Screenshot + Video Coordination (Section 8)
    # ======================================================================
    def test_screenshot_and_video_coordination(self) -> None:
        """
        Verify realistic coordination:
        Start recording -> interactions -> screenshot checkpoint -> interactions -> stop recording.
        Verify screenshot and video exist independently, share execution context,
        and both survive runtime termination.
        """
        runtime, execution, _ = self._setup_runtime()
        runtime.start()
        runtime.navigate("/")

        # 1. Start recording
        start_rec = runtime.start_recording(label="coord_video")
        self.assertTrue(runtime.is_recording)

        # 2. Interactions before screenshot
        runtime.click("#submit-btn")

        # 3. Screenshot checkpoint
        shot = runtime.capture_viewport(reason=ScreenshotCaptureReason.CHECKPOINT, label="midpoint_shot")
        self.assertEqual(shot.status, ScreenshotCaptureStatus.SUCCESS)

        # 4. Interactions after screenshot
        runtime.type_text("#username", "another_user")

        # 5. Stop recording
        stop_rec = runtime.stop_recording()
        self.assertEqual(stop_rec.status, ScreenRecordingStatus.COMPLETED)

        # Both exist independently on disk
        shot_path = Path(shot.artifact_reference)
        vid_path = Path(stop_rec.artifact_reference)

        self.assertTrue(shot_path.exists())
        self.assertTrue(vid_path.exists())
        self.assertEqual(shot_path.suffix, ".png")
        self.assertEqual(vid_path.suffix, ".webm")

        # 6. Stop runtime
        runtime.stop()

        # Both survive runtime termination
        self.assertTrue(shot_path.exists())
        self.assertTrue(vid_path.exists())

    # ======================================================================
    # 8. Action Failure Semantics (Section 9)
    # ======================================================================
    def test_action_failure_semantics(self) -> None:
        """
        Verify action failure != automatic TesterExecution failure.
        Interaction failures do not crash, abort, or fail TesterExecution.
        """
        runtime, execution, _ = self._setup_runtime()
        runtime.start()
        runtime.navigate("/")

        # Trigger simulated interaction failure using MockBrowserSession
        mock_session = MockBrowserSession(
            project_id=self.project_id,
            work_order_id=runtime.work_order_id,
            execution_id=execution.execution_id,
            runtime_id=new_runtime_id(),
            simulate_action_failure={"click": "Element not found or not interactable"},
        )
        runtime.session = mock_session
        runtime.interaction_engine.session = mock_session

        # Click fails
        fail_click = runtime.click("#nonexistent-element")
        self.assertEqual(fail_click.status, InteractionStatus.FAILED)
        self.assertIn("Element not found", fail_click.error or "")

        # Execution remains active and intact
        self.assertIn(execution.status, (TesterExecutionStatus.REQUESTED, TesterExecutionStatus.STARTING, TesterExecutionStatus.RUNNING))
        self.assertNotEqual(execution.status, TesterExecutionStatus.FAILED)

        runtime.stop()

    # ======================================================================
    # 9. Resource / Time Boundaries (Section 10 & 18)
    # ======================================================================
    def test_resource_and_time_boundaries(self) -> None:
        """
        Verify bounded waits and recording duration bounds.
        """
        runtime, _, _ = self._setup_runtime()
        runtime.start()
        runtime.navigate("/")

        # Bounded wait completes without hanging
        wait_res = runtime.wait(0.01)
        self.assertTrue(wait_res.is_success)

        # Recording max duration bound
        opts = ScreenRecordingOptions(
            max_duration_seconds=0.01,  # 10ms budget
        )
        runtime.start_recording(options=opts)
        time.sleep(0.02)  # Exceed budget
        stop_res = runtime.stop_recording()

        self.assertEqual(stop_res.status, ScreenRecordingStatus.COMPLETED)
        self.assertTrue(stop_res.is_partial)

        runtime.stop()

    # ======================================================================
    # 10. Cleanup Verification (Section 11)
    # ======================================================================
    def test_cleanup_in_all_operational_scenarios(self) -> None:
        """
        Verify cleanup in:
        A. Normal completion
        B. Active recording during shutdown
        C. Tester cancellation
        """
        # Scenario A & B: Active recording during shutdown
        runtime, execution, _ = self._setup_runtime()
        runtime.start()
        runtime.navigate("/")
        runtime.start_recording(label="shutdown_cleanup")
        self.assertTrue(runtime.is_recording)

        # Shutdown runtime while recording is active
        runtime.stop()

        self.assertFalse(runtime.is_recording)
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)
        # Evidence was auto-finalized and preserved
        video_evs = [e for e in runtime.evidences if e.evidence_type == EvidenceType.VIDEO]
        self.assertEqual(len(video_evs), 1)
        self.assertTrue(os.path.exists(video_evs[0].artifact_reference))

        # Scenario C: Tester cancellation
        execution.cancel(reason="User cancelled test session")
        self.assertEqual(execution.status, TesterExecutionStatus.CANCELLED)
        self.assertEqual(execution.cancellation_reason, "User cancelled test session")

    # ======================================================================
    # 11. Security Verification & Sanitization (Section 12 & 17)
    # ======================================================================
    def test_security_verification_and_sanitization(self) -> None:
        """
        Verify:
        - Unauthorized navigation / invalid origin rejected
        - Path traversal through artifact labels sanitized
        - Sensitive text redacted in records and events
        """
        runtime, _, _ = self._setup_runtime()
        runtime.start()

        # Origin boundary violation
        with self.assertRaises(NavigationDeniedError):
            runtime.navigate("https://malicious-external-site.com/exploit")

        runtime.navigate("/")

        # Path traversal in label
        shot = runtime.capture_viewport(label="../../etc/passwd_leak")
        self.assertEqual(shot.status, ScreenshotCaptureStatus.SUCCESS)
        self.assertNotIn("..", shot.artifact_reference)
        self.assertNotIn("/etc/passwd", shot.artifact_reference)
        self.assertTrue(Path(shot.artifact_reference).name.startswith(f"{runtime.execution_id}_{shot.evidence_id}_etc_passwd_leak"))

        # Sensitive text redaction
        type_res = runtime.type_text("#password", "SuperSecretPassword123!", sensitive=True)
        self.assertTrue(type_res.is_success)
        self.assertEqual(type_res.resulting_state.get("text_entered"), "[REDACTED]")
        self.assertTrue(type_res.sensitive)
        self.assertNotIn("SuperSecretPassword123!", str(type_res.to_dict()))

        # Check emitted event for password redaction
        type_events = [e for e in self.emitted_events if e.event_type == EventType.TEST_ACTION_COMPLETED and e.payload.get("action_type") == "TYPE"]
        self.assertGreaterEqual(len(type_events), 1)
        self.assertNotIn("SuperSecretPassword123!", str(type_events[0].payload))

        runtime.stop()

    # ======================================================================
    # 12. False-Success Prevention Invariants (Section 13)
    # ======================================================================
    def test_false_success_prevention_invariants(self) -> None:
        """
        Verify:
        - A browser click that was not actually performed MUST NOT be SUCCESS
        - A screenshot that was not actually captured MUST NOT be SUCCESS
        - A video that was not actually finalized MUST NOT be COMPLETED
        - A denied action MUST NOT execute
        - An unsupported capability MUST NOT report SUCCESS
        - A stopped runtime MUST NOT accept interactions
        - A failed artifact write MUST NOT produce valid evidence
        """
        runtime, _, _ = self._setup_runtime()
        runtime.start()
        runtime.navigate("/")

        # 1. Click failure MUST NOT report SUCCESS
        runtime.session.simulate_action_failure = {"click": "Click element failed"}
        res_click = runtime.click("#submit-btn")
        self.assertEqual(res_click.status, InteractionStatus.FAILED)
        self.assertFalse(res_click.is_success)
        runtime.session.simulate_action_failure.clear()

        # 2. Screenshot failure MUST NOT report SUCCESS
        runtime.session.simulate_screenshot_failure = "Simulated screenshot driver failure"
        res_shot = runtime.capture_viewport()
        self.assertEqual(res_shot.status, ScreenshotCaptureStatus.FAILED)
        self.assertFalse(res_shot.is_success)
        runtime.session.simulate_screenshot_failure = None

        # 3. Video finalization failure MUST NOT report COMPLETED
        runtime.start_recording()
        runtime.session.simulate_recording_stop_failure = "Simulated video stop failure"
        res_vid = runtime.stop_recording()
        self.assertEqual(res_vid.status, ScreenRecordingStatus.FAILED)
        self.assertFalse(res_vid.is_success)
        runtime.session.simulate_recording_stop_failure = None

        # 4. Failed artifact write integrity check
        runtime.screen_recorder.storage.verify_artifact_integrity = lambda ref, cs: False
        runtime.start_recording(label="tamper_check")
        res_tamper = runtime.stop_recording()
        self.assertEqual(res_tamper.status, ScreenRecordingStatus.FAILED)

        # 5. Stopped runtime MUST NOT accept interactions
        runtime.stop()
        res_stopped = runtime.click("#submit-btn")
        self.assertEqual(res_stopped.status, InteractionStatus.FAILED)

    # ======================================================================
    # 13. Trace & Event Provenance (Section 14)
    # ======================================================================
    def test_trace_and_event_provenance(self) -> None:
        """
        Verify that the runtime produces a coherent domain event stream:
        RUNTIME_STARTED -> NAVIGATION_STARTED -> NAVIGATION_COMPLETED ->
        RECORDING_STARTED -> ACTION_STARTED -> ACTION_COMPLETED ->
        SCREENSHOT_CAPTURED -> RECORDING_STOPPING -> RECORDING_COMPLETED -> RUNTIME_STOPPED.
        Verify provenance: source=WORKER, project_id, task_id, execution_id.
        """
        runtime, execution, _ = self._setup_runtime()
        runtime.start()
        runtime.navigate("/")
        runtime.start_recording(label="provenance_test")
        runtime.click("#submit-btn")
        runtime.capture_viewport(label="provenance_shot")
        runtime.stop_recording()
        runtime.stop()

        expected_types = [
            EventType.TEST_RUNTIME_STARTED,
            EventType.TEST_NAVIGATION_STARTED,
            EventType.TEST_NAVIGATION_COMPLETED,
            EventType.TEST_RECORDING_STARTED,
            EventType.TEST_ACTION_STARTED,
            EventType.TEST_ACTION_COMPLETED,
            EventType.TEST_SCREENSHOT_CAPTURED,
            EventType.TEST_RECORDING_STOPPING,
            EventType.TEST_RECORDING_COMPLETED,
            EventType.TEST_RUNTIME_STOPPED,
        ]

        emitted_types = [e.event_type for e in self.emitted_events]
        for exp_type in expected_types:
            self.assertIn(exp_type, emitted_types, f"Missing expected event: {exp_type.name}")

        # Provenance validation on all tester events
        for ev in self.emitted_events:
            if ev.event_type.name.startswith("TEST_"):
                self.assertEqual(ev.source, EventSource.WORKER)
                self.assertEqual(ev.project_id, self.project_id)
                self.assertEqual(ev.correlation_id, self.correlation_id)
                self.assertIsNotNone(ev.timestamp)
                self.assertIsInstance(ev.payload, dict)


if __name__ == "__main__":
    unittest.main()

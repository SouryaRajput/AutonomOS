from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import time
from typing import List
import unittest

from core.events.model import Event
from core.events.types import EventSource, EventType
from core.tester.contracts.action import TestActionRecord
from core.tester.contracts.browser_runtime import BrowserTestRuntime
from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.identifiers import (
    new_execution_id,
    new_runtime_id,
    new_work_order_id,
)
from core.tester.contracts.recording import (
    ScreenRecordingOptions,
    ScreenRecordingResult,
    VideoArtifactStorage,
)
from core.tester.contracts.scope import TestScope
from core.tester.contracts.session import HttpBrowserSession
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.types import (
    EnvironmentType,
    EvidenceType,
    RecordingCaptureReason,
    ScreenRecordingStatus,
    TestActionStatus,
    TestCategory,
    TestRuntimeStatus,
    TesterActionType,
    TesterExecutionStatus,
    TestingCapability,
)


def handle_test_app_request(path: str) -> tuple[int, dict[str, str], str]:
    """In-process mock web server for deterministic screen recording integration testing."""
    if path in ("/", "/login"):
        html = """
        <!DOCTYPE html>
        <html>
            <head><title>Secure Login Portal</title></head>
            <body>
                <h1>Sign In</h1>
                <form id="login-form">
                    <input type="text" id="username" placeholder="Username" />
                    <input type="password" id="password" placeholder="Password" />
                    <button type="submit" id="submit-btn">Login</button>
                </form>
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
                <h1>Application Overview</h1>
                <div class="metrics">
                    <span id="metric-cpu">CPU: 12%</span>
                    <span id="metric-mem">Memory: 45%</span>
                </div>
            </body>
        </html>
        """
        return 200, {"Content-Type": "text/html"}, html.strip()
    else:
        return 404, {"Content-Type": "text/plain"}, "Not Found"


class TestTesterScreenRecordingIntegration(unittest.TestCase):
    """
    End-to-end integration test suite for Tester V1 Phase 2.5: Screen Recording and Video Evidence Capture.
    
    Verifies full lifecycle with active browser session:
    1. TestEnvironment + TesterWorkOrder + TesterExecution setup
    2. Session startup with live HTTP document rendering
    3. Navigation to target pages
    4. Screen recording initiation (START_RECORDING)
    5. Interaction sequence execution (type, wait, navigate) during recording
    6. Recording termination (STOP_RECORDING) and video artifact finalization
    7. Checksum and artifact persistence verification on disk
    8. Authoritative TesterEvidence binding into runtime.evidences (evidence_type=VIDEO)
    9. Domain events emission (TEST_RECORDING_STARTED, TEST_RECORDING_STOPPING, TEST_RECORDING_COMPLETED)
    10. Proof of zero raw video binary bytes in events, logs, or results
    11. Clean shutdown and proof that video artifacts survive session/runtime termination
    12. Enforcement of capability boundaries (unauthorized denial without phantom files)
    13. Automatic graceful recording finalization on runtime shutdown
    """

    def setUp(self) -> None:
        self.project_id = "proj-recording-integ-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.base_url = "http://127.0.0.1:8080"
        self.emitted_events: List[Event] = []
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_base = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _event_sink(self, event: Event) -> None:
        self.emitted_events.append(event)

    def _create_work_order(
        self,
        authorized_capabilities: list[TestingCapability] | None = None,
    ) -> TesterWorkOrder:
        caps = authorized_capabilities or [
            TestingCapability.NAVIGATE,
            TestingCapability.SCREEN_RECORDING,
            TestingCapability.SCREENSHOT,
            TestingCapability.CLICK,
            TestingCapability.TYPE,
            TestingCapability.WAIT,
        ]
        return TesterWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id="mtask-recording-integ-01",
            project_id=self.project_id,
            correlation_id="corr-recording-integ-01",
            objective="Capture screen video evidence during login and dashboard flows",
            instructions=[
                "Navigate to /login",
                "Start screen recording",
                "Enter credentials and submit",
                "Navigate to /dashboard",
                "Stop screen recording and preserve video evidence",
            ],
            product_artifact="web-frontend:2.5.0",
            test_scope=TestScope(routes=["/login", "/dashboard"], features=["auth", "video_evidence"]),
            test_categories=[TestCategory.FUNCTIONAL, TestCategory.INTEGRATION],
            authorized_capabilities=caps,
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-video-01",
                    description="Video evidence captured, verified, and bound to execution",
                )
            ],
        )

    def _create_execution(self) -> TesterExecution:
        return TesterExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id="mtask-recording-integ-01",
            project_id=self.project_id,
            correlation_id="corr-recording-integ-01",
        )

    def test_end_to_end_screen_recording_flow(self) -> None:
        """
        Verify complete sequential video evidence capture flow:
        start runtime -> navigate -> start recording -> interact -> navigate -> stop recording -> verify artifact & evidence -> stop runtime.
        """
        wo = self._create_work_order()
        execution = self._create_execution()

        env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            application_url=self.base_url,
            environment_type=EnvironmentType.BROWSER,
        )

        session = HttpBrowserSession(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=new_runtime_id(),
            initial_url=self.base_url,
            app_handler=handle_test_app_request,
        )

        storage = VideoArtifactStorage(base_dir=str(self.storage_base))

        runtime = BrowserTestRuntime(
            execution=execution,
            work_order=wo,
            environment=env,
            session=session,
            runtime_id=session.runtime_id,
            event_sink=self._event_sink,
        )
        runtime.video_storage = storage
        runtime.screen_recorder.storage = storage

        # 1. Start Runtime
        runtime.start()
        self.assertEqual(runtime.status, TestRuntimeStatus.READY)

        # 2. Navigate to /login
        nav_action = runtime.navigate("/login")
        self.assertEqual(runtime.status, TestRuntimeStatus.RUNNING)
        self.assertEqual(nav_action.status, TestActionStatus.SUCCESS)

        # 3. Start Screen Recording
        start_res = runtime.start_recording(
            reason=RecordingCaptureReason.TEST_FLOW,
            label="auth_dashboard_sequence",
            fps=30,
        )
        self.assertEqual(start_res.status, ScreenRecordingStatus.RECORDING)
        self.assertTrue(runtime.is_recording)
        self.assertTrue(session.is_recording)

        # 4. Perform User Interactions during recording
        type_res = runtime.type_text("#username", "tester_user")
        self.assertTrue(type_res.is_success)

        # 5. Navigate to /dashboard during recording
        nav_dash = runtime.navigate("/dashboard")
        self.assertEqual(nav_dash.status, TestActionStatus.SUCCESS)

        time.sleep(0.01)

        # 6. Stop Screen Recording
        stop_res = runtime.stop_recording(reason=RecordingCaptureReason.TEST_FLOW)

        self.assertEqual(stop_res.status, ScreenRecordingStatus.COMPLETED)
        self.assertTrue(stop_res.is_success)
        self.assertFalse(runtime.is_recording)
        self.assertFalse(session.is_recording)

        # 7. Verify Video Artifact on disk
        self.assertIsNotNone(stop_res.artifact_reference)
        art_path = Path(stop_res.artifact_reference)
        self.assertTrue(art_path.exists())
        self.assertTrue(art_path.is_file())
        self.assertEqual(art_path.suffix, ".webm")
        self.assertGreater(stop_res.file_size or 0, 0)
        self.assertIsNotNone(stop_res.checksum)

        # Verify cryptographic checksum of artifact file on disk
        disk_bytes = art_path.read_bytes()
        self.assertEqual(hashlib.sha256(disk_bytes).hexdigest(), stop_res.checksum)

        # 8. Verify Authoritative TesterEvidence Integration
        self.assertEqual(len(runtime.evidences), 1)
        evidence = runtime.evidences[0]
        self.assertEqual(evidence.evidence_type, EvidenceType.VIDEO)
        self.assertEqual(evidence.execution_id, self.execution_id)
        self.assertEqual(evidence.work_order_id, self.work_order_id)
        self.assertEqual(evidence.artifact_reference, stop_res.artifact_reference)
        self.assertEqual(evidence.checksum, stop_res.checksum)
        self.assertEqual(evidence.metadata["format"], "webm")
        self.assertEqual(evidence.metadata["fps"], 30)
        self.assertGreaterEqual(evidence.metadata["duration_ms"], 0)

        # 9. Verify Domain Events Emitted
        start_events = [e for e in self.emitted_events if e.event_type == EventType.TEST_RECORDING_STARTED]
        self.assertEqual(len(start_events), 1)
        self.assertEqual(start_events[0].source, EventSource.WORKER)
        self.assertEqual(start_events[0].payload["evidence_id"], stop_res.evidence_id)
        self.assertNotIn("raw_bytes", start_events[0].payload)

        stopping_events = [e for e in self.emitted_events if e.event_type == EventType.TEST_RECORDING_STOPPING]
        self.assertEqual(len(stopping_events), 1)
        self.assertEqual(stopping_events[0].source, EventSource.WORKER)

        completed_events = [e for e in self.emitted_events if e.event_type == EventType.TEST_RECORDING_COMPLETED]
        self.assertEqual(len(completed_events), 1)
        self.assertEqual(completed_events[0].source, EventSource.WORKER)
        self.assertEqual(completed_events[0].payload["evidence_id"], stop_res.evidence_id)
        self.assertEqual(completed_events[0].payload["checksum"], stop_res.checksum)
        self.assertNotIn("raw_bytes", completed_events[0].payload)

        # 10. Clean Runtime Shutdown
        runtime.stop()
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)

        # 11. Invariant Check: Artifact survives session and runtime termination
        self.assertTrue(art_path.exists())
        self.assertEqual(hashlib.sha256(art_path.read_bytes()).hexdigest(), stop_res.checksum)

    def test_unauthorized_screen_recording_denial_flow(self) -> None:
        """
        Verify that attempting to record without authorized SCREEN_RECORDING capability
        is deterministically denied, emits TEST_RECORDING_DENIED, and leaves zero phantom files.
        """
        wo = self._create_work_order(authorized_capabilities=[TestingCapability.NAVIGATE])
        execution = self._create_execution()

        env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            application_url=self.base_url,
            environment_type=EnvironmentType.BROWSER,
        )

        session = HttpBrowserSession(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=new_runtime_id(),
            initial_url=self.base_url,
            app_handler=handle_test_app_request,
        )

        storage = VideoArtifactStorage(base_dir=str(self.storage_base))

        runtime = BrowserTestRuntime(
            execution=execution,
            work_order=wo,
            environment=env,
            session=session,
            runtime_id=session.runtime_id,
            event_sink=self._event_sink,
        )
        runtime.video_storage = storage
        runtime.screen_recorder.storage = storage

        runtime.start()
        runtime.navigate("/login")

        # Attempt start recording
        denied_result = runtime.start_recording(label="unauthorized_flow")
        self.assertEqual(denied_result.status, ScreenRecordingStatus.DENIED)
        self.assertFalse(denied_result.is_success)
        self.assertIsNone(denied_result.artifact_reference)
        self.assertIn("not authorized", denied_result.error.lower())
        self.assertFalse(runtime.is_recording)

        # Verify denial event
        denied_events = [e for e in self.emitted_events if e.event_type == EventType.TEST_RECORDING_DENIED]
        self.assertEqual(len(denied_events), 1)
        self.assertNotIn("raw_bytes", denied_events[0].payload)

        # Verify no evidence was appended
        self.assertEqual(len(runtime.evidences), 0)

        # Verify no recording files created in storage
        recordings_dir = (
            self.storage_base
            / "projects"
            / self.project_id
            / "executions"
            / self.execution_id
            / "recordings"
        )
        if recordings_dir.exists():
            self.assertEqual(len(list(recordings_dir.iterdir())), 0)

    def test_runtime_shutdown_with_active_recording_flow(self) -> None:
        """
        Verify that shutting down the runtime while a screen recording is active
        automatically finalizes and preserves the video artifact and evidence.
        """
        wo = self._create_work_order()
        execution = self._create_execution()

        env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            application_url=self.base_url,
            environment_type=EnvironmentType.BROWSER,
        )

        session = HttpBrowserSession(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=new_runtime_id(),
            initial_url=self.base_url,
            app_handler=handle_test_app_request,
        )

        storage = VideoArtifactStorage(base_dir=str(self.storage_base))

        runtime = BrowserTestRuntime(
            execution=execution,
            work_order=wo,
            environment=env,
            session=session,
            runtime_id=session.runtime_id,
            event_sink=self._event_sink,
        )
        runtime.video_storage = storage
        runtime.screen_recorder.storage = storage

        runtime.start()
        runtime.navigate("/login")

        # Start recording
        runtime.start_recording(label="shutdown_auto_finalize")
        self.assertTrue(runtime.is_recording)

        # Stop runtime directly
        runtime.stop()

        self.assertFalse(runtime.is_recording)
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)

        # Artifact must exist and be registered
        self.assertEqual(len(runtime.evidences), 1)
        ev = runtime.evidences[0]
        self.assertEqual(ev.evidence_type, EvidenceType.VIDEO)
        self.assertTrue(os.path.exists(ev.artifact_reference))
        self.assertEqual(hashlib.sha256(Path(ev.artifact_reference).read_bytes()).hexdigest(), ev.checksum)


if __name__ == "__main__":
    unittest.main()

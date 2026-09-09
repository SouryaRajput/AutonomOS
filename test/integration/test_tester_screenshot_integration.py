from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
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
from core.tester.contracts.scope import TestScope
from core.tester.contracts.screenshot import (
    ScreenshotArtifactStorage,
    ScreenshotCaptureOptions,
    ScreenshotCaptureResult,
)
from core.tester.contracts.session import HttpBrowserSession
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.types import (
    EnvironmentType,
    EvidenceType,
    ScreenshotCaptureReason,
    ScreenshotCaptureStatus,
    TestActionStatus,
    TestCategory,
    TestRuntimeStatus,
    TesterActionType,
    TesterExecutionStatus,
    TestingCapability,
)


def handle_test_app_request(path: str) -> tuple[int, dict[str, str], str]:
    """In-process mock web server for deterministic screenshot integration testing."""
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


class TestTesterScreenshotIntegration(unittest.TestCase):
    """
    End-to-end integration test suite for Tester V1 Phase 2.4: Screenshot Capture & Visual Evidence.
    
    Verifies full lifecycle with active browser session:
    1. TestEnvironment + TesterWorkOrder + TesterExecution setup
    2. Session startup with live HTTP document rendering
    3. Navigation to target pages
    4. Screenshot capture across multiple phases (BEFORE_ACTION, AFTER_ACTION, CHECKPOINT)
    5. Checksum and artifact persistence verification
    6. Authoritative TesterEvidence binding into runtime.evidences
    7. Clean shutdown and proof that artifacts survive session/runtime termination
    8. Enforcement of capability boundaries with proper event emission and zero raw binary leakage
    """

    def setUp(self) -> None:
        self.project_id = "proj-screenshot-integ-01"
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
            TestingCapability.SCREENSHOT,
            TestingCapability.CLICK,
            TestingCapability.TYPE,
            TestingCapability.WAIT,
        ]
        return TesterWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id="mtask-screenshot-integ-01",
            project_id=self.project_id,
            correlation_id="corr-screenshot-integ-01",
            objective="Capture visual evidence during login and dashboard flows",
            instructions=[
                "Navigate to /login",
                "Capture screenshot before interaction",
                "Enter credentials and submit",
                "Navigate to /dashboard",
                "Capture dashboard screenshot as visual evidence",
            ],
            product_artifact="web-frontend:2.4.0",
            test_scope=TestScope(routes=["/login", "/dashboard"], features=["auth", "dashboard_visual"]),
            test_categories=[TestCategory.FUNCTIONAL, TestCategory.INTEGRATION],
            authorized_capabilities=caps,
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-visual-01",
                    description="Visual evidence captured and verified on disk",
                )
            ],
        )

    def _create_execution(self) -> TesterExecution:
        return TesterExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id="mtask-screenshot-integ-01",
            project_id=self.project_id,
            correlation_id="corr-screenshot-integ-01",
        )

    def test_end_to_end_screenshot_capture_flow(self) -> None:
        """
        Verify complete sequential visual evidence capture flow:
        start -> navigate -> capture (BEFORE_ACTION) -> interact -> navigate -> capture (AFTER_ACTION) -> capture (CHECKPOINT) -> stop.
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

        storage = ScreenshotArtifactStorage(base_dir=str(self.storage_base))

        runtime = BrowserTestRuntime(
            execution=execution,
            work_order=wo,
            environment=env,
            session=session,
            runtime_id=session.runtime_id,
            event_sink=self._event_sink,
        )
        runtime.screenshot_storage = storage
        runtime.screenshot_service.storage = storage

        # 1. Start Runtime
        runtime.start()
        self.assertEqual(runtime.status, TestRuntimeStatus.READY)

        # 2. Navigate to /login
        nav_action = runtime.navigate("/login")
        self.assertEqual(runtime.status, TestRuntimeStatus.RUNNING)
        self.assertEqual(nav_action.status, TestActionStatus.SUCCESS)

        # 3. Capture Initial Viewport Screenshot (BEFORE_ACTION)
        cap1 = runtime.capture_viewport(
            reason=ScreenshotCaptureReason.BEFORE_ACTION,
            label="login_page",
        )
        self.assertEqual(cap1.status, ScreenshotCaptureStatus.SUCCESS)
        self.assertTrue(cap1.is_success)
        self.assertIsNotNone(cap1.artifact_reference)
        self.assertTrue(os.path.exists(cap1.artifact_reference))
        self.assertGreater(cap1.file_size or 0, 0)
        self.assertIsNotNone(cap1.checksum)

        # Verify cryptographic checksum of artifact file on disk
        file1_bytes = Path(cap1.artifact_reference).read_bytes()
        self.assertEqual(hashlib.sha256(file1_bytes).hexdigest(), cap1.checksum)

        # 4. Perform User Interaction
        type_res = runtime.type_text("#username", "tester_user")
        self.assertTrue(type_res.is_success)

        # 5. Navigate to /dashboard
        nav_dash = runtime.navigate("/dashboard")
        self.assertEqual(nav_dash.status, TestActionStatus.SUCCESS)

        # 6. Capture Dashboard Viewport Screenshot (AFTER_ACTION)
        cap2 = runtime.capture_viewport(
            reason=ScreenshotCaptureReason.AFTER_ACTION,
            label="dashboard_loaded",
        )
        self.assertEqual(cap2.status, ScreenshotCaptureStatus.SUCCESS)
        self.assertTrue(os.path.exists(cap2.artifact_reference))

        # 7. Capture Checkpoint Screenshot (CHECKPOINT)
        cap3 = runtime.capture_viewport(
            reason=ScreenshotCaptureReason.CHECKPOINT,
            label="milestone_1",
        )
        self.assertEqual(cap3.status, ScreenshotCaptureStatus.SUCCESS)
        self.assertTrue(os.path.exists(cap3.artifact_reference))

        # 8. Verify Captured Screenshots History
        self.assertEqual(len(runtime.captured_screenshots), 3)
        self.assertEqual(runtime.captured_screenshots[0].evidence_id, cap1.evidence_id)
        self.assertEqual(runtime.captured_screenshots[1].evidence_id, cap2.evidence_id)
        self.assertEqual(runtime.captured_screenshots[2].evidence_id, cap3.evidence_id)

        # 9. Verify Authoritative TesterEvidence Integration
        self.assertEqual(len(runtime.evidences), 3)
        for ev in runtime.evidences:
            self.assertEqual(ev.evidence_type, EvidenceType.SCREENSHOT)
            self.assertEqual(ev.execution_id, self.execution_id)
            self.assertEqual(ev.work_order_id, self.work_order_id)
            self.assertTrue(os.path.exists(ev.artifact_reference))
            # Verify file bytes match evidence checksum
            actual_hash = hashlib.sha256(Path(ev.artifact_reference).read_bytes()).hexdigest()
            self.assertEqual(ev.checksum, actual_hash)

        # 10. Verify Domain Events Emitted
        captured_events = [e for e in self.emitted_events if e.event_type == EventType.TEST_SCREENSHOT_CAPTURED]
        self.assertEqual(len(captured_events), 3)
        for ev_event in captured_events:
            self.assertEqual(ev_event.source, EventSource.WORKER)
            self.assertIn("checksum", ev_event.payload)
            self.assertIn("artifact_reference", ev_event.payload)
            self.assertNotIn("raw_bytes", ev_event.payload)

        # 11. Clean Runtime Shutdown
        runtime.stop()
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)

        # 12. Invariant Check: Artifacts survive session and runtime termination
        for cap in (cap1, cap2, cap3):
            art_path = Path(cap.artifact_reference)
            self.assertTrue(art_path.exists())
            self.assertEqual(hashlib.sha256(art_path.read_bytes()).hexdigest(), cap.checksum)

    def test_unauthorized_screenshot_denial_flow(self) -> None:
        """
        Verify that attempting to capture screenshots without authorized SCREENSHOT capability
        is deterministically denied, emits TEST_SCREENSHOT_DENIED, and leaves zero phantom files.
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

        storage = ScreenshotArtifactStorage(base_dir=str(self.storage_base))

        runtime = BrowserTestRuntime(
            execution=execution,
            work_order=wo,
            environment=env,
            session=session,
            runtime_id=session.runtime_id,
            event_sink=self._event_sink,
        )
        runtime.screenshot_storage = storage
        runtime.screenshot_service.storage = storage

        runtime.start()
        runtime.navigate("/login")

        # Attempt capture
        denied_result = runtime.capture_viewport(label="should_fail")
        self.assertEqual(denied_result.status, ScreenshotCaptureStatus.DENIED)
        self.assertFalse(denied_result.is_success)
        self.assertIsNone(denied_result.artifact_reference)
        self.assertIn("not authorized", denied_result.error.lower())

        # Verify denial event
        denied_events = [e for e in self.emitted_events if e.event_type == EventType.TEST_SCREENSHOT_DENIED]
        self.assertEqual(len(denied_events), 1)
        self.assertNotIn("raw_bytes", denied_events[0].payload)

        # Verify no evidence was appended
        self.assertEqual(len(runtime.evidences), 0)

        # Verify no files created in storage
        shots_dir = self.storage_base / "projects" / self.project_id / "executions" / self.execution_id / "screenshots"
        if shots_dir.exists():
            self.assertEqual(len(list(shots_dir.iterdir())), 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any, List
import unittest

from core.events.model import Event
from core.events.types import EventSource, EventType
from core.tester.contracts.boundary import TESTER_ALLOWED_CAPABILITIES
from core.tester.contracts.browser_runtime import BrowserTestRuntime
from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.finding import TesterEvidence
from core.tester.contracts.identifiers import (
    new_evidence_id,
    new_execution_id,
    new_runtime_id,
    new_work_order_id,
    validate_evidence_id,
)
from core.tester.contracts.scope import TestScope
from core.tester.contracts.screenshot import (
    ScreenshotArtifactStorage,
    ScreenshotCaptureOptions,
    ScreenshotCaptureResult,
    ScreenshotCaptureService,
    VisualObservation,
)
from core.tester.contracts.session import MockBrowserSession
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    SessionStoppedError,
    SessionUnavailableError,
    TesterValidationError,
)
from core.tester.types import (
    EnvironmentType,
    EvidenceType,
    ScreenshotCaptureReason,
    ScreenshotCaptureStatus,
    TestCategory,
    TestRuntimeStatus,
    TesterActionType,
    TesterExecutionStatus,
    TestingCapability,
)


class TestScreenshotCapture(unittest.TestCase):
    """
    Comprehensive unit test suite for Tester V1 Phase 2.4: Screenshot Capture.
    
    Validates:
    1. test_successful_screenshot_capture
    2. test_screenshot_capability_authorization
    3. test_screenshot_denial
    4. test_runtime_not_ready_rejection
    5. test_session_unavailable
    6. test_invalid_capture_options
    7. test_artifact_creation
    8. test_artifact_reference
    9. test_checksum_generation
    10. test_evidence_provenance
    11. test_execution_ownership
    12. test_project_isolation
    13. test_full_page_support_and_not_supported_behavior
    14. test_capture_reason
    15. test_viewport_metadata
    16. test_sensitive_metadata_handling
    17. test_artifact_persistence_after_runtime_shutdown
    18. test_collision_resistant_naming
    19. test_no_screenshot_on_failed_validation
    20. test_no_raw_screenshot_bytes_in_events_or_results
    """

    def setUp(self) -> None:
        self.project_id = "proj-screenshot-test-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.runtime_id = new_runtime_id()
        self.app_url = "https://app.autonomos.internal/dashboard"
        self.emitted_events: List[Event] = []
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_base = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _event_sink(self, event: Event) -> None:
        self.emitted_events.append(event)

    def _create_sample_work_order(
        self,
        authorized_capabilities: list[TestingCapability] | None = None,
    ) -> TesterWorkOrder:
        all_caps = [
            TestingCapability.NAVIGATE,
            TestingCapability.SCREENSHOT,
            TestingCapability.CLICK,
        ]
        return TesterWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id="mtask-screenshot-01",
            project_id=self.project_id,
            correlation_id="corr-screenshot-01",
            objective="Evaluate screenshot capture infrastructure",
            instructions=["Capture screenshots of the dashboard"],
            product_artifact="web-frontend:2.4.0",
            test_scope=TestScope(
                routes=["/dashboard"],
                features=["visual_evidence"],
            ),
            test_categories=[TestCategory.FUNCTIONAL],
            authorized_capabilities=authorized_capabilities if authorized_capabilities is not None else all_caps,
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-screenshot-01",
                    description="Screenshots captured cleanly and verified",
                )
            ],
        )

    def _create_sample_execution(self) -> TesterExecution:
        return TesterExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id="mtask-screenshot-01",
            project_id=self.project_id,
            correlation_id="corr-screenshot-01",
        )

    def _create_runtime(
        self,
        work_order: TesterWorkOrder | None = None,
        session: MockBrowserSession | None = None,
        unsupported_capabilities: set[TestingCapability] | None = None,
        supports_full_page: bool = True,
        simulate_screenshot_failure: str | None = None,
        simulate_screenshot_timeout: bool = False,
    ) -> tuple[BrowserTestRuntime, MockBrowserSession]:
        wo = work_order or self._create_sample_work_order()
        execution = self._create_sample_execution()

        env = TestEnvironment(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            application_url=self.app_url,
            environment_type=EnvironmentType.BROWSER,
        )

        mock_session = session or MockBrowserSession(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=self.runtime_id,
            initial_url=self.app_url,
            supports_full_page=supports_full_page,
            simulate_screenshot_failure=simulate_screenshot_failure,
            simulate_screenshot_timeout=simulate_screenshot_timeout,
        )

        storage = ScreenshotArtifactStorage(base_dir=str(self.storage_base))

        runtime = BrowserTestRuntime(
            environment=env,
            work_order=wo,
            execution=execution,
            session=mock_session,
            runtime_id=self.runtime_id,
            event_sink=self._event_sink,
        )
        runtime.screenshot_storage = storage
        runtime.screenshot_service.storage = storage

        if unsupported_capabilities:
            runtime.session.unsupported_capabilities.update(unsupported_capabilities)

        return runtime, mock_session

    # 1. test_successful_screenshot_capture
    def test_successful_screenshot_capture(self) -> None:
        runtime, mock_session = self._create_runtime()
        runtime.start()

        result = runtime.capture_viewport(reason=ScreenshotCaptureReason.MANUAL_REQUEST, label="test_page")

        self.assertEqual(result.status, ScreenshotCaptureStatus.SUCCESS)
        self.assertIsNotNone(result.artifact_reference)
        self.assertTrue(os.path.exists(result.artifact_reference))
        self.assertGreater(result.file_size or 0, 0)
        self.assertIsNotNone(result.checksum)
        self.assertEqual(result.format, "png")
        self.assertEqual(result.width, 1280)
        self.assertEqual(result.height, 720)
        self.assertIsNone(result.error)

        # Verify event emitted
        captured_events = [e for e in self.emitted_events if e.event_type == EventType.TEST_SCREENSHOT_CAPTURED]
        self.assertEqual(len(captured_events), 1)
        ev_payload = captured_events[0].payload
        self.assertEqual(ev_payload["status"], "RUNNING")
        self.assertEqual(ev_payload["checksum"], result.checksum)
        self.assertNotIn("raw_bytes", ev_payload)

    # 2. test_screenshot_capability_authorization
    def test_screenshot_capability_authorization(self) -> None:
        # Check all 3 boundaries
        self.assertIn(TestingCapability.SCREENSHOT, TESTER_ALLOWED_CAPABILITIES)
        runtime, _ = self._create_runtime()
        self.assertIn(TestingCapability.SCREENSHOT, runtime.work_order.authorized_capabilities)
        self.assertIn(TestingCapability.SCREENSHOT, runtime.effective_capabilities)

    # 3. test_screenshot_denial
    def test_screenshot_denial(self) -> None:
        # Create work order lacking SCREENSHOT capability
        wo = self._create_sample_work_order(authorized_capabilities=[TestingCapability.NAVIGATE])
        runtime, _ = self._create_runtime(work_order=wo)
        runtime.start()

        result = runtime.capture_viewport()
        self.assertEqual(result.status, ScreenshotCaptureStatus.DENIED)
        self.assertIn("not authorized", result.error.lower())
        self.assertIsNone(result.artifact_reference)

        # Verify TEST_SCREENSHOT_DENIED emitted
        denied_events = [e for e in self.emitted_events if e.event_type == EventType.TEST_SCREENSHOT_DENIED]
        self.assertEqual(len(denied_events), 1)

    # 4. test_runtime_not_ready_rejection
    def test_runtime_not_ready_rejection(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()
        runtime.stop()  # STOPPED state

        result = runtime.capture_viewport()
        self.assertEqual(result.status, ScreenshotCaptureStatus.FAILED)
        self.assertIn("stopped", result.error.lower())

    # 5. test_session_unavailable
    def test_session_unavailable(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()
        runtime.screenshot_service.session = None

        result = runtime.capture_viewport()
        self.assertEqual(result.status, ScreenshotCaptureStatus.FAILED)
        self.assertIn("unavailable", result.error.lower())

    # 6. test_invalid_capture_options
    def test_invalid_capture_options(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        # Invalid format
        options = ScreenshotCaptureOptions(format="gif")
        with self.assertRaises(TesterValidationError):
            options.validate()

        # Timeout <= 0
        options_timeout = ScreenshotCaptureOptions(timeout_seconds=-1.0)
        with self.assertRaises(TesterValidationError):
            options_timeout.validate()

    # 7. test_artifact_creation
    def test_artifact_creation(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        result = runtime.capture_viewport(label="dashboard_view")
        self.assertEqual(result.status, ScreenshotCaptureStatus.SUCCESS)

        artifact_path = Path(result.artifact_reference)
        self.assertTrue(artifact_path.exists())
        self.assertTrue(artifact_path.name.endswith(".png"))
        self.assertIn("dashboard_view", artifact_path.name)
        self.assertIn(self.project_id, str(artifact_path))
        self.assertIn(self.execution_id, str(artifact_path))

    # 8. test_artifact_reference
    def test_artifact_reference(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        result = runtime.capture_viewport()
        self.assertEqual(result.status, ScreenshotCaptureStatus.SUCCESS)
        self.assertTrue(Path(result.artifact_reference).is_absolute())

    # 9. test_checksum_generation
    def test_checksum_generation(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        result = runtime.capture_viewport()
        self.assertEqual(result.status, ScreenshotCaptureStatus.SUCCESS)

        file_bytes = Path(result.artifact_reference).read_bytes()
        actual_sha256 = hashlib.sha256(file_bytes).hexdigest()
        self.assertEqual(result.checksum, actual_sha256)

    # 10. test_evidence_provenance
    def test_evidence_provenance(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        result = runtime.capture_viewport()
        self.assertEqual(result.status, ScreenshotCaptureStatus.SUCCESS)

        # Check runtime.evidences
        self.assertEqual(len(runtime.evidences), 1)
        evidence = runtime.evidences[0]
        self.assertEqual(evidence.evidence_id, result.evidence_id)
        self.assertEqual(evidence.evidence_type, EvidenceType.SCREENSHOT)
        self.assertEqual(evidence.artifact_reference, result.artifact_reference)
        self.assertEqual(evidence.checksum, result.checksum)
        self.assertEqual(evidence.execution_id, self.execution_id)
        self.assertEqual(evidence.metadata["format"], "png")

    # 11. test_execution_ownership
    def test_execution_ownership(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        result = runtime.capture_viewport()
        self.assertEqual(result.execution_id, self.execution_id)
        self.assertEqual(result.runtime_id, self.runtime_id)

    # 12. test_project_isolation
    def test_project_isolation(self) -> None:
        runtime1, _ = self._create_runtime()
        runtime1.start()
        result1 = runtime1.capture_viewport(label="r1")

        # Create runtime with different project_id
        self.project_id = "proj-other-unique"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.runtime_id = new_runtime_id()
        runtime2, _ = self._create_runtime()
        runtime2.start()
        result2 = runtime2.capture_viewport(label="r2")

        self.assertIn("proj-screenshot-test-01", result1.artifact_reference)
        self.assertIn("proj-other-unique", result2.artifact_reference)
        self.assertNotEqual(Path(result1.artifact_reference).parent, Path(result2.artifact_reference).parent)

    # 13. test_full_page_support_and_not_supported_behavior
    def test_full_page_support_and_not_supported_behavior(self) -> None:
        # Backend does not support full page
        runtime_no_fp, _ = self._create_runtime(supports_full_page=False)
        runtime_no_fp.start()

        result_unsupported = runtime_no_fp.capture_screenshot(full_page=True)
        self.assertEqual(result_unsupported.status, ScreenshotCaptureStatus.NOT_SUPPORTED)
        self.assertIn("not supported", result_unsupported.error.lower())

        # Backend supports full page
        runtime_fp, _ = self._create_runtime(supports_full_page=True)
        runtime_fp.start()

        result_supported = runtime_fp.capture_screenshot(full_page=True)
        self.assertEqual(result_supported.status, ScreenshotCaptureStatus.SUCCESS)
        self.assertTrue(result_supported.viewport.get("full_page"))

    # 14. test_capture_reason
    def test_capture_reason(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        for reason in [
            ScreenshotCaptureReason.MANUAL_REQUEST,
            ScreenshotCaptureReason.BEFORE_ACTION,
            ScreenshotCaptureReason.AFTER_ACTION,
            ScreenshotCaptureReason.STATE_CHANGE,
            ScreenshotCaptureReason.FAILURE,
            ScreenshotCaptureReason.CHECKPOINT,
        ]:
            res = runtime.capture_viewport(reason=reason)
            self.assertEqual(res.status, ScreenshotCaptureStatus.SUCCESS)
            self.assertEqual(res.capture_reason, reason)

    # 15. test_viewport_metadata
    def test_viewport_metadata(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        result = runtime.capture_viewport()
        self.assertEqual(result.status, ScreenshotCaptureStatus.SUCCESS)
        self.assertIn("width", result.viewport)
        self.assertIn("height", result.viewport)
        self.assertIn("device_scale_factor", result.viewport)
        self.assertIn("full_page", result.viewport)
        self.assertEqual(result.viewport["width"], 1280)
        self.assertEqual(result.viewport["height"], 720)

    # 16. test_sensitive_metadata_handling
    def test_sensitive_metadata_handling(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        result = runtime.capture_viewport(sensitive=True)
        self.assertEqual(result.status, ScreenshotCaptureStatus.SUCCESS)
        self.assertTrue(result.sensitive)

        data = result.to_dict()
        self.assertTrue(data["sensitive"])

        # Create visual observation
        obs = result.to_observation(description="Password input field")
        self.assertTrue(obs.metadata.get("sensitive"))

    # 17. test_artifact_persistence_after_runtime_shutdown
    def test_artifact_persistence_after_runtime_shutdown(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        result = runtime.capture_viewport()
        self.assertEqual(result.status, ScreenshotCaptureStatus.SUCCESS)
        artifact_path = Path(result.artifact_reference)
        self.assertTrue(artifact_path.exists())

        runtime.stop()
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)

        # File must still exist and checksum remain valid
        self.assertTrue(artifact_path.exists())
        actual_sha256 = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        self.assertEqual(result.checksum, actual_sha256)

    # 18. test_collision_resistant_naming
    def test_collision_resistant_naming(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        res1 = runtime.capture_viewport(label="rapid")
        res2 = runtime.capture_viewport(label="rapid")

        self.assertNotEqual(res1.artifact_reference, res2.artifact_reference)
        self.assertNotEqual(res1.evidence_id, res2.evidence_id)
        self.assertTrue(Path(res1.artifact_reference).exists())
        self.assertTrue(Path(res2.artifact_reference).exists())

    # 19. test_no_screenshot_on_failed_validation
    def test_no_screenshot_on_failed_validation(self) -> None:
        # Simulate session failure
        runtime, _ = self._create_runtime(simulate_screenshot_failure="Driver crash")
        runtime.start()

        result = runtime.capture_viewport()
        self.assertEqual(result.status, ScreenshotCaptureStatus.FAILED)
        self.assertIsNone(result.artifact_reference)

        # Verify no screenshots dir was written with broken files
        target_dir = self.storage_base / "projects" / self.project_id / "executions" / self.execution_id / "screenshots"
        if target_dir.exists():
            self.assertEqual(len(list(target_dir.iterdir())), 0)

    # 20. test_no_raw_screenshot_bytes_in_events_or_results
    def test_no_raw_screenshot_bytes_in_events_or_results(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        result = runtime.capture_viewport()
        self.assertEqual(result.status, ScreenshotCaptureStatus.SUCCESS)

        # Check result to_dict()
        result_dict = result.to_dict()
        for k, v in result_dict.items():
            self.assertNotIsInstance(v, bytes, f"Key '{k}' in result_dict contains raw bytes")

        # Check observation to_dict()
        obs = result.to_observation()
        obs_dict = obs.to_dict()
        for k, v in obs_dict.items():
            self.assertNotIsInstance(v, bytes, f"Key '{k}' in obs_dict contains raw bytes")

        # Check all emitted events
        for event in self.emitted_events:
            for k, v in event.payload.items():
                self.assertNotIsInstance(v, bytes, f"Event {event.event_type} payload key '{k}' contains raw bytes")

        # Check evidence metadata
        for ev in runtime.evidences:
            for k, v in ev.metadata.items():
                self.assertNotIsInstance(v, bytes, f"Evidence metadata key '{k}' contains raw bytes")


if __name__ == "__main__":
    unittest.main()

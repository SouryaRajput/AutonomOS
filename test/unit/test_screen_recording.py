from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import time
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
from core.tester.contracts.recording import (
    ScreenRecordingOptions,
    ScreenRecordingResult,
    ScreenRecordingService,
    VideoArtifactStorage,
    VideoObservation,
)
from core.tester.contracts.scope import TestScope
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
    RecordingCaptureReason,
    ScreenRecordingStatus,
    TestCategory,
    TestRuntimeStatus,
    TesterActionType,
    TesterExecutionStatus,
    TestingCapability,
)


class TestScreenRecording(unittest.TestCase):
    """
    Comprehensive unit test suite for Tester V1 Phase 2.5: Screen Recording.
    
    Validates:
    1. test_recording_start
    2. test_recording_stop
    3. test_recording_capability_authorization
    4. test_unauthorized_recording_denial
    5. test_runtime_not_ready_rejection
    6. test_unsupported_recording
    7. test_artifact_creation
    8. test_artifact_reference
    9. test_checksum_generation
    10. test_recording_metadata
    11. test_recording_lifecycle
    12. test_start_failure
    13. test_stop_finalization_failure
    14. test_runtime_ownership
    15. test_project_isolation
    16. test_recording_event_provenance
    17. test_time_resource_bounds
    18. test_shutdown_with_active_recording
    19. test_partial_recording_handling
    20. test_no_fake_success
    """

    def setUp(self) -> None:
        self.project_id = "proj-recording-test-01"
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
            TestingCapability.SCREEN_RECORDING,
            TestingCapability.CLICK,
        ]
        return TesterWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id="mtask-recording-01",
            project_id=self.project_id,
            correlation_id="corr-recording-01",
            objective="Evaluate screen recording infrastructure",
            instructions=["Record interaction flows on dashboard"],
            product_artifact="web-frontend:2.5.0",
            test_scope=TestScope(
                routes=["/dashboard"],
                features=["video_evidence"],
            ),
            test_categories=[TestCategory.FUNCTIONAL],
            authorized_capabilities=authorized_capabilities if authorized_capabilities is not None else all_caps,
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-recording-01",
                    description="Screen recordings captured cleanly and verified",
                )
            ],
        )

    def _create_sample_execution(self) -> TesterExecution:
        return TesterExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id="mtask-recording-01",
            project_id=self.project_id,
            correlation_id="corr-recording-01",
        )

    def _create_runtime(
        self,
        work_order: TesterWorkOrder | None = None,
        session: MockBrowserSession | None = None,
        unsupported_capabilities: set[TestingCapability] | None = None,
        simulate_recording_start_failure: str | None = None,
        simulate_recording_stop_failure: str | None = None,
        simulate_recording_timeout: bool = False,
        custom_video_bytes: bytes | None = None,
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
            simulate_recording_start_failure=simulate_recording_start_failure,
            simulate_recording_stop_failure=simulate_recording_stop_failure,
            simulate_recording_timeout=simulate_recording_timeout,
            custom_video_bytes=custom_video_bytes,
        )

        storage = VideoArtifactStorage(base_dir=str(self.storage_base))

        runtime = BrowserTestRuntime(
            environment=env,
            work_order=wo,
            execution=execution,
            session=mock_session,
            runtime_id=self.runtime_id,
            event_sink=self._event_sink,
        )
        runtime.video_storage = storage
        runtime.screen_recorder.storage = storage

        if unsupported_capabilities:
            runtime.session.unsupported_capabilities.update(unsupported_capabilities)

        return runtime, mock_session

    # 1. test_recording_start
    def test_recording_start(self) -> None:
        runtime, mock_session = self._create_runtime()
        runtime.start()

        self.assertFalse(runtime.is_recording)
        res = runtime.start_recording(reason=RecordingCaptureReason.TEST_FLOW, label="login_flow")

        self.assertEqual(res.status, ScreenRecordingStatus.RECORDING)
        self.assertTrue(runtime.is_recording)
        self.assertTrue(mock_session.is_recording)
        self.assertEqual(len(mock_session.recorded_recordings), 1)
        self.assertEqual(mock_session.recorded_recordings[0]["event"], "start")

        start_events = [e for e in self.emitted_events if e.event_type == EventType.TEST_RECORDING_STARTED]
        self.assertEqual(len(start_events), 1)
        self.assertEqual(start_events[0].payload["evidence_id"], res.evidence_id)
        self.assertEqual(start_events[0].payload["capture_reason"], RecordingCaptureReason.TEST_FLOW.value)

    # 2. test_recording_stop
    def test_recording_stop(self) -> None:
        runtime, mock_session = self._create_runtime()
        runtime.start()

        start_res = runtime.start_recording(reason=RecordingCaptureReason.TEST_FLOW, label="test_stop")
        self.assertTrue(runtime.is_recording)

        stop_res = runtime.stop_recording(reason=RecordingCaptureReason.TEST_FLOW)

        self.assertFalse(runtime.is_recording)
        self.assertFalse(mock_session.is_recording)
        self.assertEqual(stop_res.status, ScreenRecordingStatus.COMPLETED)
        self.assertTrue(stop_res.is_success)
        self.assertIsNotNone(stop_res.artifact_reference)
        self.assertTrue(os.path.exists(stop_res.artifact_reference))
        self.assertIsNotNone(stop_res.checksum)
        self.assertGreater(stop_res.file_size or 0, 0)
        self.assertIsNotNone(stop_res.evidence)
        self.assertEqual(stop_res.evidence.evidence_type, EvidenceType.VIDEO)
        self.assertEqual(stop_res.evidence.checksum, stop_res.checksum)

        stop_events = [e for e in self.emitted_events if e.event_type == EventType.TEST_RECORDING_COMPLETED]
        self.assertEqual(len(stop_events), 1)
        self.assertEqual(stop_events[0].payload["evidence_id"], stop_res.evidence_id)
        self.assertEqual(stop_events[0].payload["checksum"], stop_res.checksum)

    # 3. test_recording_capability_authorization
    def test_recording_capability_authorization(self) -> None:
        wo = self._create_sample_work_order(authorized_capabilities=[
            TestingCapability.NAVIGATE,
            TestingCapability.SCREEN_RECORDING,
        ])
        runtime, _ = self._create_runtime(work_order=wo)
        runtime.start()

        self.assertIn(TestingCapability.SCREEN_RECORDING, TESTER_ALLOWED_CAPABILITIES)
        self.assertIn(TestingCapability.SCREEN_RECORDING, runtime.effective_capabilities)

        res = runtime.start_recording(reason=RecordingCaptureReason.TEST_FLOW)
        self.assertEqual(res.status, ScreenRecordingStatus.RECORDING)
        stop_res = runtime.stop_recording()
        self.assertEqual(stop_res.status, ScreenRecordingStatus.COMPLETED)

    # 4. test_unauthorized_recording_denial
    def test_unauthorized_recording_denial(self) -> None:
        wo = self._create_sample_work_order(authorized_capabilities=[
            TestingCapability.NAVIGATE,
            TestingCapability.SCREENSHOT,
            # SCREEN_RECORDING deliberately omitted
        ])
        runtime, mock_session = self._create_runtime(work_order=wo)
        runtime.start()

        self.assertNotIn(TestingCapability.SCREEN_RECORDING, runtime.effective_capabilities)

        res = runtime.start_recording(reason=RecordingCaptureReason.TEST_FLOW)
        self.assertEqual(res.status, ScreenRecordingStatus.DENIED)
        self.assertIn("not authorized", res.error or "")
        self.assertFalse(runtime.is_recording)
        self.assertFalse(mock_session.is_recording)

        denied_events = [e for e in self.emitted_events if e.event_type == EventType.TEST_RECORDING_DENIED]
        self.assertEqual(len(denied_events), 1)

    # 5. test_runtime_not_ready_rejection
    def test_runtime_not_ready_rejection(self) -> None:
        runtime, mock_session = self._create_runtime()

        # In CREATED state
        self.assertEqual(runtime.status, TestRuntimeStatus.CREATED)
        res_created = runtime.start_recording()
        self.assertEqual(res_created.status, ScreenRecordingStatus.FAILED)
        self.assertIn("not ready", res_created.error or "")
        self.assertFalse(runtime.is_recording)

        # Start runtime and transition to STOPPED
        runtime.start()
        runtime.stop()
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)

        res_stopped = runtime.start_recording()
        self.assertEqual(res_stopped.status, ScreenRecordingStatus.FAILED)
        self.assertIn("stopped", res_stopped.error or "")
        self.assertFalse(runtime.is_recording)

    # 6. test_unsupported_recording
    def test_unsupported_recording(self) -> None:
        runtime, mock_session = self._create_runtime(
            unsupported_capabilities={TestingCapability.SCREEN_RECORDING}
        )
        runtime.start()

        self.assertNotIn(TestingCapability.SCREEN_RECORDING, mock_session.supported_recording_capabilities())

        res = runtime.start_recording(reason=RecordingCaptureReason.TEST_FLOW)
        self.assertEqual(res.status, ScreenRecordingStatus.NOT_SUPPORTED)
        self.assertIn("not supported by driver backend", res.error or "")
        self.assertFalse(runtime.is_recording)

    # 7. test_artifact_creation
    def test_artifact_creation(self) -> None:
        runtime, mock_session = self._create_runtime()
        runtime.start()

        runtime.start_recording(label="flow_artifact")
        res = runtime.stop_recording()

        self.assertEqual(res.status, ScreenRecordingStatus.COMPLETED)
        art_path = Path(res.artifact_reference or "")
        self.assertTrue(art_path.exists())
        self.assertTrue(art_path.is_file())
        self.assertEqual(art_path.suffix, ".webm")

        # Verify parent directory path structure
        expected_parent = (
            self.storage_base
            / "projects"
            / self.project_id
            / "executions"
            / self.execution_id
            / "recordings"
        )
        self.assertEqual(art_path.parent.resolve(), expected_parent.resolve())

    # 8. test_artifact_reference
    def test_artifact_reference(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        runtime.start_recording(label="ref_test")
        res = runtime.stop_recording()

        self.assertIsNotNone(res.artifact_reference)
        ref_path = Path(res.artifact_reference)
        self.assertTrue(ref_path.is_absolute())
        self.assertTrue(ref_path.exists())

        # Observation reference
        obs = res.to_observation(description="Test reference")
        self.assertEqual(obs.evidence_id, res.evidence_id)
        self.assertEqual(obs.metadata["artifact_reference"], str(ref_path))

    # 9. test_checksum_generation
    def test_checksum_generation(self) -> None:
        test_video_payload = b"\x1a\x45\xdf\xa3AUTONOMOS_CUSTOM_VIDEO_PAYLOAD_TEST_12345"
        expected_hash = hashlib.sha256(test_video_payload).hexdigest()

        runtime, _ = self._create_runtime(custom_video_bytes=test_video_payload)
        runtime.start()

        runtime.start_recording(label="checksum_test")
        res = runtime.stop_recording()

        self.assertEqual(res.status, ScreenRecordingStatus.COMPLETED)
        self.assertEqual(res.checksum, expected_hash)
        disk_bytes = Path(res.artifact_reference).read_bytes()
        self.assertEqual(hashlib.sha256(disk_bytes).hexdigest(), expected_hash)

    # 10. test_recording_metadata
    def test_recording_metadata(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        runtime.start_recording(
            reason=RecordingCaptureReason.CHECKPOINT,
            label="meta_test",
            format="webm",
            fps=60,
        )
        time.sleep(0.01)
        res = runtime.stop_recording()

        self.assertEqual(res.status, ScreenRecordingStatus.COMPLETED)
        self.assertEqual(res.width, 1280)
        self.assertEqual(res.height, 720)
        self.assertEqual(res.format, "webm")
        self.assertEqual(res.fps, 60)
        self.assertGreater(res.file_size or 0, 0)
        self.assertGreaterEqual(res.duration_ms or 0, 0)
        self.assertEqual(res.capture_reason, RecordingCaptureReason.CHECKPOINT)

        # Serialized dict integrity
        d = res.to_dict()
        self.assertNotIn("raw_bytes", d)
        self.assertEqual(d["fps"], 60)
        self.assertEqual(d["format"], "webm")
        self.assertEqual(d["capture_reason"], "CHECKPOINT")

    # 11. test_recording_lifecycle
    def test_recording_lifecycle(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        recorder = runtime.screen_recorder
        self.assertEqual(recorder.status, ScreenRecordingStatus.IDLE)
        self.assertFalse(recorder.is_recording)

        # Attempt to stop when IDLE fails gracefully
        stop_idle = recorder.stop_recording()
        self.assertEqual(stop_idle.status, ScreenRecordingStatus.FAILED)
        self.assertIn("not RECORDING", stop_idle.error or "")

        # Start recording
        start_res = recorder.start_recording()
        self.assertEqual(start_res.status, ScreenRecordingStatus.RECORDING)
        self.assertEqual(recorder.status, ScreenRecordingStatus.RECORDING)
        self.assertTrue(recorder.is_recording)

        # Attempt to double-start fails
        start_again = recorder.start_recording()
        self.assertEqual(start_again.status, ScreenRecordingStatus.FAILED)
        self.assertIn("already active", start_again.error or "")
        self.assertTrue(recorder.is_recording)

        # Stop recording
        stop_res = recorder.stop_recording()
        self.assertEqual(stop_res.status, ScreenRecordingStatus.COMPLETED)
        self.assertEqual(recorder.status, ScreenRecordingStatus.COMPLETED)
        self.assertFalse(recorder.is_recording)

    # 12. test_start_failure
    def test_start_failure(self) -> None:
        runtime, mock_session = self._create_runtime(
            simulate_recording_start_failure="Hardware video encoder initialization failed."
        )
        runtime.start()

        res = runtime.start_recording()
        self.assertEqual(res.status, ScreenRecordingStatus.FAILED)
        self.assertIn("Hardware video encoder initialization failed", res.error or "")
        self.assertFalse(runtime.is_recording)
        self.assertEqual(runtime.screen_recorder.status, ScreenRecordingStatus.FAILED)

        failed_events = [e for e in self.emitted_events if e.event_type == EventType.TEST_RECORDING_FAILED]
        self.assertEqual(len(failed_events), 1)

    # 13. test_stop_finalization_failure
    def test_stop_finalization_failure(self) -> None:
        runtime, mock_session = self._create_runtime(
            simulate_recording_stop_failure="Video container stream corrupted during finalization."
        )
        runtime.start()

        start_res = runtime.start_recording()
        self.assertEqual(start_res.status, ScreenRecordingStatus.RECORDING)

        stop_res = runtime.stop_recording()
        self.assertEqual(stop_res.status, ScreenRecordingStatus.FAILED)
        self.assertIn("stream corrupted", stop_res.error or "")
        self.assertFalse(runtime.is_recording)

        failed_events = [e for e in self.emitted_events if e.event_type == EventType.TEST_RECORDING_FAILED]
        self.assertEqual(len(failed_events), 1)

    # 14. test_runtime_ownership
    def test_runtime_ownership(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        runtime.start_recording()
        res = runtime.stop_recording()

        self.assertEqual(res.execution_id, self.execution_id)
        self.assertEqual(res.runtime_id, self.runtime_id)
        self.assertIsNotNone(res.evidence)
        self.assertEqual(res.evidence.execution_id, self.execution_id)
        self.assertEqual(res.evidence.work_order_id, self.work_order_id)

    # 15. test_project_isolation
    def test_project_isolation(self) -> None:
        proj_a = "proj-alpha"
        runtime1, _ = self._create_runtime()
        runtime1.start()

        runtime1.start_recording(label="video_a")
        res_a = runtime1.stop_recording()

        # Switch project_id and IDs
        self.project_id = "proj-beta"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.runtime_id = new_runtime_id()
        runtime2, _ = self._create_runtime()
        runtime2.start()

        runtime2.start_recording(label="video_b")
        res_b = runtime2.stop_recording()

        self.assertIn("proj-recording-test-01", res_a.artifact_reference)
        self.assertIn("proj-beta", res_b.artifact_reference)
        self.assertNotEqual(Path(res_a.artifact_reference).parent, Path(res_b.artifact_reference).parent)

    # 16. test_recording_event_provenance
    def test_recording_event_provenance(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        runtime.start_recording(reason=RecordingCaptureReason.FAILURE, label="failure_flow")
        res = runtime.stop_recording()

        recording_events = [e for e in self.emitted_events if e.event_type.name.startswith("TEST_RECORDING_")]
        self.assertGreaterEqual(len(recording_events), 2)
        for ev in recording_events:
            self.assertEqual(ev.source, EventSource.WORKER)
            self.assertEqual(ev.project_id, self.project_id)
            self.assertEqual(ev.correlation_id, "corr-recording-01")
            self.assertEqual(ev.payload["work_order_id"], self.work_order_id)
            self.assertEqual(ev.payload["execution_id"], self.execution_id)
            self.assertEqual(ev.payload["evidence_id"], res.evidence_id)

    # 17. test_time_resource_bounds
    def test_time_resource_bounds(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        opts = ScreenRecordingOptions(
            max_duration_seconds=0.01,  # 10 milliseconds budget
        )
        runtime.start_recording(options=opts)
        time.sleep(0.02)  # Exceed 10ms budget
        res = runtime.stop_recording()

        self.assertEqual(res.status, ScreenRecordingStatus.COMPLETED)
        self.assertTrue(res.is_partial)
        self.assertTrue(res.evidence.metadata["is_partial"])

    # 18. test_shutdown_with_active_recording
    def test_shutdown_with_active_recording(self) -> None:
        runtime, mock_session = self._create_runtime()
        runtime.start()

        runtime.start_recording(label="shutdown_auto_stop")
        self.assertTrue(runtime.is_recording)

        # Shutting down runtime should gracefully finalize the active recording
        runtime.stop()

        self.assertFalse(runtime.is_recording)
        self.assertFalse(mock_session.is_recording)
        self.assertEqual(runtime.status, TestRuntimeStatus.STOPPED)

        # Artifact must exist and be intact
        completed_recordings = [r for r in runtime.recording_history if r.status == ScreenRecordingStatus.COMPLETED]
        self.assertEqual(len(completed_recordings), 1)
        final_rec = completed_recordings[0]
        self.assertTrue(os.path.exists(final_rec.artifact_reference))
        self.assertIsNotNone(final_rec.checksum)

    # 19. test_partial_recording_handling
    def test_partial_recording_handling(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        runtime.start_recording(label="partial_test")
        res = runtime.stop_recording(is_partial=True)

        self.assertEqual(res.status, ScreenRecordingStatus.COMPLETED)
        self.assertTrue(res.is_partial)
        self.assertIsNotNone(res.evidence)
        self.assertTrue(res.evidence.metadata["is_partial"])

    # 20. test_no_fake_success
    def test_no_fake_success(self) -> None:
        runtime, _ = self._create_runtime()
        runtime.start()

        # Monkey-patch verify_artifact_integrity to simulate corrupted/missing write
        def fake_verify(art_ref: str, checksum: str) -> bool:
            return False

        runtime.screen_recorder.storage.verify_artifact_integrity = fake_verify

        runtime.start_recording(label="fake_success_test")
        res = runtime.stop_recording()

        self.assertEqual(res.status, ScreenRecordingStatus.FAILED)
        self.assertFalse(res.is_success)
        self.assertIn("Integrity verification failed", res.error or "")


if __name__ == "__main__":
    unittest.main()

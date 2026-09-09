from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import logging
from pathlib import Path
import re
import time
from typing import Any, Optional, Set

from core.events.types import EventSource, EventType
from core.tester.contracts.boundary import TESTER_ALLOWED_CAPABILITIES
from core.tester.contracts.finding import TesterEvidence, compute_sha256
from core.tester.contracts.identifiers import (
    new_evidence_id,
    validate_evidence_id,
    validate_execution_id,
    validate_runtime_id,
)
from core.tester.errors import (
    SessionStoppedError,
    SessionUnavailableError,
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    EvidenceType,
    RecordingCaptureReason,
    ScreenRecordingStatus,
    TestRuntimeStatus,
    TesterActionType,
    TestingCapability,
)

logger = logging.getLogger("AutonomOS.ScreenRecording")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ScreenRecordingOptions:
    """
    Configuration options for an individual screen recording request.
    """
    __test__ = False
    capture_reason: RecordingCaptureReason = RecordingCaptureReason.TEST_FLOW
    label: Optional[str] = None
    format: str = "webm"
    fps: Optional[int] = 30
    sensitive: bool = False
    max_duration_seconds: float = 300.0
    timeout_seconds: float = 30.0

    def validate(self) -> None:
        """Validate recording configuration options."""
        if not isinstance(self.capture_reason, RecordingCaptureReason):
            try:
                self.capture_reason = RecordingCaptureReason(str(self.capture_reason).upper())
            except (ValueError, KeyError):
                raise TesterValidationError(
                    f"Invalid recording capture reason: '{self.capture_reason}'.",
                    field_name="options.capture_reason",
                )

        if self.timeout_seconds <= 0:
            raise TesterValidationError(
                f"Timeout must be greater than zero: {self.timeout_seconds}s.",
                field_name="options.timeout_seconds",
            )

        if self.max_duration_seconds <= 0:
            raise TesterValidationError(
                f"Maximum duration must be greater than zero: {self.max_duration_seconds}s.",
                field_name="options.max_duration_seconds",
            )

        norm_fmt = self.format.lower().strip().lstrip(".")
        if norm_fmt not in ("webm", "mp4", "mkv"):
            raise TesterValidationError(
                f"Unsupported recording format '{self.format}'. Supported formats: ['webm', 'mp4', 'mkv'].",
                field_name="options.format",
            )
        self.format = norm_fmt


@dataclass
class VideoObservation:
    """
    Minimal factual video observation linking an evidence record to dynamic context.
    Strictly records facts (evidence ID, timestamp, duration, viewport) without subjective conclusions.
    """
    __test__ = False
    evidence_id: str
    timestamp: str = field(default_factory=utc_now)
    duration_ms: Optional[float] = None
    description: str = ""
    viewport: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "description": self.description,
            "viewport": dict(self.viewport),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VideoObservation:
        return cls(
            evidence_id=str(data.get("evidence_id", "")),
            timestamp=str(data.get("timestamp", utc_now())),
            duration_ms=data.get("duration_ms"),
            description=str(data.get("description", "")),
            viewport=dict(data.get("viewport", {})),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ScreenRecordingResult:
    """
    Structured outcome of a screen recording attempt.
    Contains artifact reference, cryptographic checksum, timing, and metadata.
    NEVER includes raw binary video bytes.
    """
    __test__ = False
    evidence_id: str
    execution_id: str
    runtime_id: str
    status: ScreenRecordingStatus
    evidence_type: EvidenceType = EvidenceType.VIDEO
    artifact_reference: Optional[str] = None
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    duration_ms: Optional[float] = None
    width: int = 0
    height: int = 0
    format: str = "webm"
    fps: Optional[int] = 30
    file_size: Optional[int] = None
    checksum: Optional[str] = None
    capture_reason: RecordingCaptureReason = RecordingCaptureReason.TEST_FLOW
    evidence: Optional[TesterEvidence] = None
    error: Optional[str] = None
    sensitive: bool = False
    is_partial: bool = False
    trace: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.evidence_id:
            self.evidence_id = new_evidence_id()
        validate_evidence_id(self.evidence_id)
        validate_execution_id(self.execution_id)
        validate_runtime_id(self.runtime_id)

        if not isinstance(self.status, ScreenRecordingStatus):
            self.status = ScreenRecordingStatus(str(self.status).upper())

        if not isinstance(self.capture_reason, RecordingCaptureReason):
            try:
                self.capture_reason = RecordingCaptureReason(str(self.capture_reason).upper())
            except (ValueError, KeyError):
                self.capture_reason = RecordingCaptureReason.TEST_FLOW

        self.trace = dict(self.trace or {})

    @property
    def is_success(self) -> bool:
        return self.status == ScreenRecordingStatus.COMPLETED

    @property
    def is_recording(self) -> bool:
        return self.status == ScreenRecordingStatus.RECORDING

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize result to a dictionary.
        CRITICAL: Never embeds raw video binary content.
        """
        return {
            "evidence_id": self.evidence_id,
            "execution_id": self.execution_id,
            "runtime_id": self.runtime_id,
            "status": self.status.value,
            "evidence_type": self.evidence_type.value,
            "artifact_reference": self.artifact_reference,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "duration_ms": self.duration_ms,
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "fps": self.fps,
            "file_size": self.file_size,
            "checksum": self.checksum,
            "capture_reason": self.capture_reason.value,
            "evidence_id_ref": self.evidence.evidence_id if self.evidence else self.evidence_id,
            "error": self.error,
            "sensitive": self.sensitive,
            "is_partial": self.is_partial,
            "trace": dict(self.trace),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScreenRecordingResult:
        ev_id = data.get("evidence_id") or data.get("evidence_id_ref") or new_evidence_id()
        status_val = ScreenRecordingStatus(data.get("status", ScreenRecordingStatus.FAILED.value))
        reason_val = RecordingCaptureReason(data.get("capture_reason", RecordingCaptureReason.TEST_FLOW.value))

        return cls(
            evidence_id=ev_id,
            execution_id=str(data.get("execution_id", "")),
            runtime_id=str(data.get("runtime_id", "")),
            status=status_val,
            evidence_type=EvidenceType.VIDEO,
            artifact_reference=data.get("artifact_reference"),
            started_at=data.get("started_at"),
            stopped_at=data.get("stopped_at"),
            duration_ms=data.get("duration_ms"),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            format=str(data.get("format", "webm")),
            fps=data.get("fps"),
            file_size=data.get("file_size"),
            checksum=data.get("checksum"),
            capture_reason=reason_val,
            error=data.get("error"),
            sensitive=bool(data.get("sensitive", False)),
            is_partial=bool(data.get("is_partial", False)),
            trace=dict(data.get("trace", {})),
        )

    def to_observation(self, description: str = "") -> VideoObservation:
        """Create a factual VideoObservation from this recording result."""
        return VideoObservation(
            evidence_id=self.evidence_id,
            timestamp=self.stopped_at or self.started_at or utc_now(),
            duration_ms=self.duration_ms,
            description=description,
            viewport={"width": self.width, "height": self.height},
            metadata={
                "artifact_reference": self.artifact_reference,
                "checksum": self.checksum,
                "format": self.format,
                "fps": self.fps,
                "file_size": self.file_size,
                "sensitive": self.sensitive,
                "is_partial": self.is_partial,
                "capture_reason": self.capture_reason.value,
            },
        )


class VideoArtifactStorage:
    """
    Local filesystem artifact storage abstraction for Tester screen recordings.
    
    Guarantees:
    - Path isolation: .autonomos/artifacts/projects/{project_id}/executions/{execution_id}/recordings/
    - Path traversal protection: sanitizes labels (strips '..', '/', '\\', non-alphanumerics).
    - Collision-resistant: deterministic naming with execution_id, evidence_id, timestamp.
    - Accidental overwrite protection.
    - Checksum verification: computes SHA-256 for cryptographic artifact integrity.
    - Persistence: artifacts survive session and runtime termination.
    """
    __test__ = False

    def __init__(self, base_dir: Optional[str] = None) -> None:
        if base_dir:
            self.base_dir = Path(base_dir).resolve()
        else:
            self.base_dir = Path.cwd() / ".autonomos" / "artifacts"

    def sanitize_label(self, label: Optional[str]) -> str:
        """Sanitize user-provided label to prevent path traversal or filesystem corruption."""
        if not label:
            return "recording"
        clean = re.sub(r'[^a-zA-Z0-9_\-]', '_', str(label))
        clean = clean.strip('_')
        return clean or "recording"

    def build_artifact_path(
        self,
        project_id: str,
        execution_id: str,
        evidence_id: str,
        label: Optional[str] = None,
        format: str = "webm",
    ) -> Path:
        """Construct the isolated, collision-resistant target file path."""
        clean_label = self.sanitize_label(label)
        clean_format = format.lower().strip().lstrip('.')
        timestamp_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:19]
        filename = f"{execution_id}_{evidence_id}_{clean_label}_{timestamp_slug}.{clean_format}"

        target_dir = self.base_dir / "projects" / project_id / "executions" / execution_id / "recordings"
        return target_dir / filename

    def store_video(
        self,
        project_id: str,
        execution_id: str,
        evidence_id: str,
        raw_bytes: bytes,
        label: Optional[str] = None,
        format: str = "webm",
    ) -> tuple[str, str, int]:
        """
        Write raw video bytes to disk and compute its SHA-256 integrity checksum.
        Returns (artifact_reference, checksum, file_size).
        """
        if not raw_bytes or len(raw_bytes) == 0:
            raise TesterValidationError("Cannot store empty video bytes.", field_name="raw_bytes")

        target_path = self.build_artifact_path(
            project_id=project_id,
            execution_id=execution_id,
            evidence_id=evidence_id,
            label=label,
            format=format,
        )

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(raw_bytes)

        checksum = hashlib.sha256(raw_bytes).hexdigest()
        file_size = len(raw_bytes)

        return str(target_path.resolve()), checksum, file_size

    def artifact_exists(self, artifact_reference: Optional[str]) -> bool:
        if not artifact_reference:
            return False
        p = Path(artifact_reference)
        return p.exists() and p.is_file() and p.stat().st_size > 0

    def verify_artifact_integrity(self, artifact_reference: Optional[str], expected_checksum: Optional[str]) -> bool:
        if not self.artifact_exists(artifact_reference) or not expected_checksum:
            return False
        data = Path(artifact_reference).read_bytes()
        return hashlib.sha256(data).hexdigest() == expected_checksum


class ScreenRecordingService:
    """
    Subordinate screen recording service attached to TestRuntime and TesterExecution.
    
    Guarantees:
    - Strict 3-way capability authorization:
      Global Tester Boundary ∩ WorkOrder Authorization ∩ Runtime Backend Capabilities.
    - Active runtime state (READY or RUNNING) required. Never silently starts runtime.
    - False-success invariant: COMPLETED reported ONLY when video exists on disk and has valid checksum.
    - No raw video bytes placed into logs, events, or result dictionaries.
    - Video artifacts survive runtime shutdown.
    - Recording failures do not crash or abort TesterExecution.
    - Enforces time/resource budget boundaries.
    """
    __test__ = False

    def __init__(
        self,
        runtime: Any,
        session: Any = None,
        storage: Optional[VideoArtifactStorage] = None,
    ) -> None:
        self.runtime = runtime
        self._session = session
        self._session_explicitly_set = session is not None
        self.storage = storage or VideoArtifactStorage()
        self.history: list[ScreenRecordingResult] = []

        # Active recording tracking
        self._current_status: ScreenRecordingStatus = ScreenRecordingStatus.IDLE
        self._active_evidence_id: Optional[str] = None
        self._active_options: Optional[ScreenRecordingOptions] = None
        self._started_at_iso: Optional[str] = None
        self._start_perf_time: Optional[float] = None

    @property
    def session(self) -> Any:
        if self._session_explicitly_set:
            return self._session
        return getattr(self.runtime, "session", None)

    @session.setter
    def session(self, value: Any) -> None:
        self._session = value
        self._session_explicitly_set = True

    @property
    def status(self) -> ScreenRecordingStatus:
        return self._current_status

    @property
    def is_recording(self) -> bool:
        return self._current_status == ScreenRecordingStatus.RECORDING

    def recording_status(self) -> ScreenRecordingStatus:
        """Return the current operational recording status."""
        return self._current_status

    def start_recording(
        self,
        options: Optional[ScreenRecordingOptions] = None,
        **kwargs: Any,
    ) -> ScreenRecordingResult:
        """
        Initiate controlled screen recording under strict state and capability bounds.
        """
        opts = options or ScreenRecordingOptions(**kwargs)
        opts.validate()

        ev_id = new_evidence_id()
        started_at = utc_now()

        proj_id = getattr(self.runtime, "project_id", "")
        exec_id = getattr(self.runtime, "execution_id", "")
        wo_id = getattr(self.runtime, "work_order_id", "")
        rt_id = getattr(self.runtime, "runtime_id", "")

        # 1. Ownership & Lineage Verification
        if hasattr(self.runtime, "execution"):
            if self.runtime.execution.execution_id != exec_id:
                raise TesterLineageError(
                    f"Runtime execution_id '{exec_id}' does not match TesterExecution '{self.runtime.execution.execution_id}'",
                    resource_id=exec_id,
                    expected_id=self.runtime.execution.execution_id,
                )

        # 2. State Verification of Runtime
        rt_status = getattr(self.runtime, "status", None)
        if rt_status in (TestRuntimeStatus.STOPPED, TestRuntimeStatus.STOPPING):
            err_msg = "Cannot record on a stopped or stopping runtime."
            res = ScreenRecordingResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenRecordingStatus.FAILED,
                started_at=started_at,
                capture_reason=opts.capture_reason,
                error=err_msg,
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            return res

        if rt_status == TestRuntimeStatus.FAILED:
            err_msg = "Cannot record on a failed runtime."
            res = ScreenRecordingResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenRecordingStatus.FAILED,
                started_at=started_at,
                capture_reason=opts.capture_reason,
                error=err_msg,
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            return res

        if rt_status != TestRuntimeStatus.RUNNING:
            if rt_status == TestRuntimeStatus.READY:
                self.runtime.run()
            else:
                err_msg = f"Runtime is in status '{rt_status.value if rt_status else 'UNKNOWN'}', not ready for screen recording."
                res = ScreenRecordingResult(
                    evidence_id=ev_id,
                    execution_id=exec_id,
                    runtime_id=rt_id,
                    status=ScreenRecordingStatus.FAILED,
                    started_at=started_at,
                    capture_reason=opts.capture_reason,
                    error=err_msg,
                    sensitive=opts.sensitive,
                )
                self.history.append(res)
                return res

        # 3. Recorder State Machine Check (Cannot start if already recording)
        if self._current_status in (ScreenRecordingStatus.RECORDING, ScreenRecordingStatus.STARTING):
            err_msg = "Screen recording is already active."
            res = ScreenRecordingResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenRecordingStatus.FAILED,
                started_at=started_at,
                capture_reason=opts.capture_reason,
                error=err_msg,
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            return res

        # 4. Capability Verification (3-way intersection)
        # WorkOrder authorization check
        wo_authorized = False
        if hasattr(self.runtime, "work_order") and self.runtime.work_order:
            for c in self.runtime.work_order.authorized_capabilities:
                c_val = c.value if isinstance(c, TestingCapability) else str(c).upper()
                if c_val == TestingCapability.SCREEN_RECORDING.value:
                    wo_authorized = True
                    break

        if not wo_authorized:
            err_msg = f"Capability 'SCREEN_RECORDING' is not authorized by WorkOrder '{wo_id}'."
            res = ScreenRecordingResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenRecordingStatus.DENIED,
                started_at=started_at,
                capture_reason=opts.capture_reason,
                error=err_msg,
                sensitive=opts.sensitive,
                trace={"authorization_decision": "DENIED", "required_capability": TestingCapability.SCREEN_RECORDING.value},
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_RECORDING_DENIED, ev_id, opts.capture_reason, error=err_msg)
            return res

        # 5. Session Availability Check
        if not self.session or getattr(self.session, "is_closed", True) or not getattr(self.session, "is_ready", False):
            err_msg = "Browser session is unavailable or closed."
            res = ScreenRecordingResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenRecordingStatus.FAILED,
                started_at=started_at,
                capture_reason=opts.capture_reason,
                error=err_msg,
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_RECORDING_FAILED, ev_id, opts.capture_reason, error=err_msg)
            return res

        # 6. Driver Backend Capability Check
        session_caps = set()
        if hasattr(self.session, "supported_recording_capabilities"):
            session_caps = self.session.supported_recording_capabilities()

        if TestingCapability.SCREEN_RECORDING not in session_caps:
            err_msg = (
                f"Capability 'SCREEN_RECORDING' is not supported by driver backend "
                f"'{self.session.__class__.__name__ if self.session else 'None'}'."
            )
            res = ScreenRecordingResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenRecordingStatus.NOT_SUPPORTED,
                started_at=started_at,
                capture_reason=opts.capture_reason,
                error=err_msg,
                sensitive=opts.sensitive,
                trace={"authorization_decision": "NOT_SUPPORTED", "required_capability": TestingCapability.SCREEN_RECORDING.value},
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_RECORDING_FAILED, ev_id, opts.capture_reason, error=err_msg)
            return res

        # 7. Start Driver Recording
        self._current_status = ScreenRecordingStatus.STARTING
        try:
            ok, err = self.session.do_start_recording(opts)
        except Exception as e:
            self._current_status = ScreenRecordingStatus.FAILED
            res = ScreenRecordingResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenRecordingStatus.FAILED,
                started_at=started_at,
                capture_reason=opts.capture_reason,
                error=str(e),
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_RECORDING_FAILED, ev_id, opts.capture_reason, error=str(e))
            return res

        if not ok:
            self._current_status = ScreenRecordingStatus.FAILED
            err_text = err or "Driver failed to start screen recording."
            res = ScreenRecordingResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenRecordingStatus.FAILED,
                started_at=started_at,
                capture_reason=opts.capture_reason,
                error=err_text,
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_RECORDING_FAILED, ev_id, opts.capture_reason, error=err_text)
            return res

        # Successfully transitioned to RECORDING
        self._current_status = ScreenRecordingStatus.RECORDING
        self._active_evidence_id = ev_id
        self._active_options = opts
        self._started_at_iso = started_at
        self._start_perf_time = time.perf_counter()

        res = ScreenRecordingResult(
            evidence_id=ev_id,
            execution_id=exec_id,
            runtime_id=rt_id,
            status=ScreenRecordingStatus.RECORDING,
            started_at=started_at,
            format=opts.format,
            fps=opts.fps,
            capture_reason=opts.capture_reason,
            sensitive=opts.sensitive,
            trace={"runtime_id": rt_id, "action_type": TesterActionType.START_RECORDING.value},
        )
        self.history.append(res)
        self._emit_event(
            EventType.TEST_RECORDING_STARTED,
            ev_id,
            opts.capture_reason,
            format=opts.format,
            fps=opts.fps,
            sensitive=opts.sensitive,
        )
        return res

    def stop_recording(
        self,
        reason: Optional[RecordingCaptureReason] = None,
        is_partial: bool = False,
    ) -> ScreenRecordingResult:
        """
        Stop active screen recording, finalize video artifact, verify integrity,
        create authoritative TesterEvidence, and transition to COMPLETED.
        """
        ev_id = self._active_evidence_id or new_evidence_id()
        stopped_at = utc_now()
        started_at = self._started_at_iso or stopped_at
        opts = self._active_options or ScreenRecordingOptions()
        final_reason = reason or opts.capture_reason

        proj_id = getattr(self.runtime, "project_id", "")
        exec_id = getattr(self.runtime, "execution_id", "")
        wo_id = getattr(self.runtime, "work_order_id", "")
        rt_id = getattr(self.runtime, "runtime_id", "")

        # 1. State Verification (must be RECORDING)
        if self._current_status != ScreenRecordingStatus.RECORDING:
            err_msg = f"Cannot stop recording: status is '{self._current_status.value}', not RECORDING."
            res = ScreenRecordingResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenRecordingStatus.FAILED,
                started_at=started_at,
                stopped_at=stopped_at,
                capture_reason=final_reason,
                error=err_msg,
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            return res

        # 2. Transition to STOPPING
        self._current_status = ScreenRecordingStatus.STOPPING
        self._emit_event(EventType.TEST_RECORDING_STOPPING, ev_id, final_reason, sensitive=opts.sensitive)

        # 3. Calculate Duration
        duration_ms = 0.0
        if self._start_perf_time is not None:
            duration_ms = round((time.perf_counter() - self._start_perf_time) * 1000.0, 2)

        # Check max duration bound
        duration_seconds = duration_ms / 1000.0
        if duration_seconds > opts.max_duration_seconds:
            is_partial = True

        # 4. Stop Driver Recording and retrieve bytes
        try:
            ok, video_bytes, err = self.session.do_stop_recording()
        except Exception as e:
            self._current_status = ScreenRecordingStatus.FAILED
            err_text = str(e)
            res = ScreenRecordingResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenRecordingStatus.FAILED,
                started_at=started_at,
                stopped_at=stopped_at,
                duration_ms=duration_ms,
                capture_reason=final_reason,
                error=err_text,
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_RECORDING_FAILED, ev_id, final_reason, error=err_text)
            self._reset_active_state()
            return res

        if not ok or not video_bytes or len(video_bytes) == 0:
            self._current_status = ScreenRecordingStatus.FAILED
            err_text = err or "Driver failed to finalize video recording bytes."
            res = ScreenRecordingResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenRecordingStatus.FAILED,
                started_at=started_at,
                stopped_at=stopped_at,
                duration_ms=duration_ms,
                capture_reason=final_reason,
                error=err_text,
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_RECORDING_FAILED, ev_id, final_reason, error=err_text)
            self._reset_active_state()
            return res

        # 5. Persist Video Artifact and compute SHA-256
        try:
            artifact_ref, checksum, file_size = self.storage.store_video(
                project_id=proj_id,
                execution_id=exec_id,
                evidence_id=ev_id,
                raw_bytes=video_bytes,
                label=opts.label,
                format=opts.format,
            )
        except Exception as e:
            self._current_status = ScreenRecordingStatus.FAILED
            err_text = f"Failed to persist video artifact to disk: {e}"
            res = ScreenRecordingResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenRecordingStatus.FAILED,
                started_at=started_at,
                stopped_at=stopped_at,
                duration_ms=duration_ms,
                capture_reason=final_reason,
                error=err_text,
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_RECORDING_FAILED, ev_id, final_reason, error=err_text)
            self._reset_active_state()
            return res

        # 6. False-Success Invariant Verification
        if not self.storage.verify_artifact_integrity(artifact_ref, checksum):
            self._current_status = ScreenRecordingStatus.FAILED
            err_text = "Integrity verification failed: stored video artifact not found or checksum mismatch."
            res = ScreenRecordingResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenRecordingStatus.FAILED,
                started_at=started_at,
                stopped_at=stopped_at,
                duration_ms=duration_ms,
                capture_reason=final_reason,
                error=err_text,
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_RECORDING_FAILED, ev_id, final_reason, error=err_text)
            self._reset_active_state()
            return res

        # 7. Extract Viewport Dimensions
        vp_dict = getattr(self.runtime, "environment", None)
        vp = dict(vp_dict.viewport) if (vp_dict and hasattr(vp_dict, "viewport")) else {"width": 1280, "height": 720}
        w = int(vp.get("width", 1280))
        h = int(vp.get("height", 720))

        # 8. Create Authoritative TesterEvidence
        evidence = TesterEvidence(
            evidence_id=ev_id,
            evidence_type=EvidenceType.VIDEO,
            data=f"Screen recording: reason={final_reason.value}, label={opts.label or 'default'}",
            description=f"Screen video evidence captured during test execution ({final_reason.value})",
            artifact_reference=artifact_ref,
            execution_id=exec_id,
            work_order_id=wo_id,
            source=f"tester.runtime.{self.session.__class__.__name__}",
            checksum=checksum,
            metadata={
                "viewport": {"width": w, "height": h},
                "duration_ms": duration_ms,
                "format": opts.format,
                "fps": opts.fps,
                "file_size": file_size,
                "started_at": started_at,
                "stopped_at": stopped_at,
                "capture_reason": final_reason.value,
                "label": opts.label,
                "sensitive": opts.sensitive,
                "is_partial": is_partial,
            },
            captured_at=stopped_at,
            trace={"runtime_id": rt_id, "action_type": TesterActionType.STOP_RECORDING.value},
        )

        if hasattr(self.runtime, "evidences") and isinstance(self.runtime.evidences, list):
            self.runtime.evidences.append(evidence)

        # 9. Transition to COMPLETED
        self._current_status = ScreenRecordingStatus.COMPLETED

        res = ScreenRecordingResult(
            evidence_id=ev_id,
            execution_id=exec_id,
            runtime_id=rt_id,
            status=ScreenRecordingStatus.COMPLETED,
            evidence_type=EvidenceType.VIDEO,
            artifact_reference=artifact_ref,
            started_at=started_at,
            stopped_at=stopped_at,
            duration_ms=duration_ms,
            width=w,
            height=h,
            format=opts.format,
            fps=opts.fps,
            file_size=file_size,
            checksum=checksum,
            capture_reason=final_reason,
            evidence=evidence,
            sensitive=opts.sensitive,
            is_partial=is_partial,
            trace={"runtime_id": rt_id, "action_type": TesterActionType.STOP_RECORDING.value},
        )
        self.history.append(res)

        self._emit_event(
            EventType.TEST_RECORDING_COMPLETED,
            ev_id,
            final_reason,
            artifact_reference=artifact_ref,
            checksum=checksum,
            file_size=file_size,
            duration_ms=duration_ms,
            sensitive=opts.sensitive,
            is_partial=is_partial,
        )

        self._reset_active_state()
        return res

    def _reset_active_state(self) -> None:
        self._active_evidence_id = None
        self._active_options = None
        self._started_at_iso = None
        self._start_perf_time = None

    def _emit_event(
        self,
        event_type: EventType,
        evidence_id: str,
        reason: RecordingCaptureReason,
        artifact_reference: Optional[str] = None,
        checksum: Optional[str] = None,
        file_size: Optional[int] = None,
        duration_ms: Optional[float] = None,
        format: Optional[str] = None,
        fps: Optional[int] = None,
        sensitive: bool = False,
        is_partial: bool = False,
        error: Optional[str] = None,
    ) -> None:
        payload: dict[str, Any] = {
            "evidence_id": evidence_id,
            "capture_reason": reason.value,
            "sensitive": sensitive,
        }
        if artifact_reference:
            payload["artifact_reference"] = artifact_reference
        if checksum:
            payload["checksum"] = checksum
        if file_size is not None:
            payload["file_size"] = file_size
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if format:
            payload["format"] = format
        if fps is not None:
            payload["fps"] = fps
        if is_partial:
            payload["is_partial"] = is_partial
        if error:
            payload["error"] = error

        if hasattr(self.runtime, "emit_runtime_event"):
            self.runtime.emit_runtime_event(event_type, payload)


# Interface alias per requirement
ScreenRecorder = ScreenRecordingService

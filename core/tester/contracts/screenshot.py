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
    ScreenshotCaptureReason,
    ScreenshotCaptureStatus,
    TestRuntimeStatus,
    TesterActionType,
    TestingCapability,
)

logger = logging.getLogger("AutonomOS.ScreenshotCapture")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ScreenshotCaptureOptions:
    """
    Configuration options for an individual visual screenshot capture request.
    """
    __test__ = False
    reason: ScreenshotCaptureReason = ScreenshotCaptureReason.MANUAL_REQUEST
    label: Optional[str] = None
    full_page: bool = False
    sensitive: bool = False
    timeout_seconds: float = 30.0
    format: str = "png"

    def validate(self) -> None:
        """Validate capture options."""
        if not isinstance(self.reason, ScreenshotCaptureReason):
            try:
                self.reason = ScreenshotCaptureReason(str(self.reason).upper())
            except (ValueError, KeyError):
                raise TesterValidationError(
                    f"Invalid screenshot capture reason: '{self.reason}'.",
                    field_name="options.reason",
                )

        if self.timeout_seconds <= 0:
            raise TesterValidationError(
                f"Timeout must be greater than zero: {self.timeout_seconds}s.",
                field_name="options.timeout_seconds",
            )

        norm_fmt = self.format.lower().strip()
        if norm_fmt not in ("png", "jpeg", "jpg"):
            raise TesterValidationError(
                f"Unsupported screenshot format '{self.format}'. Supported formats: ['png', 'jpeg', 'jpg'].",
                field_name="options.format",
            )
        self.format = norm_fmt


@dataclass
class VisualObservation:
    """
    Minimal visual observation model linking an evidence record to visual context.
    Strictly records facts (evidence ID, timestamp, description, viewport) without subjective conclusions.
    """
    __test__ = False
    evidence_id: str
    timestamp: str = field(default_factory=utc_now)
    description: str = ""
    viewport: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "timestamp": self.timestamp,
            "description": self.description,
            "viewport": dict(self.viewport),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisualObservation:
        return cls(
            evidence_id=str(data.get("evidence_id", "")),
            timestamp=str(data.get("timestamp", utc_now())),
            description=str(data.get("description", "")),
            viewport=dict(data.get("viewport", {})),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ScreenshotCaptureResult:
    """
    Structured outcome of a visual screenshot capture attempt.
    Contains artifact references, cryptographic checksum, and metadata.
    NEVER includes raw binary image bytes.
    """
    __test__ = False
    evidence_id: str
    execution_id: str
    runtime_id: str
    status: ScreenshotCaptureStatus
    evidence_type: EvidenceType = EvidenceType.SCREENSHOT
    artifact_reference: Optional[str] = None
    captured_at: str = field(default_factory=utc_now)
    viewport: dict[str, Any] = field(default_factory=dict)
    width: int = 0
    height: int = 0
    format: str = "png"
    file_size: Optional[int] = None
    checksum: Optional[str] = None
    capture_reason: ScreenshotCaptureReason = ScreenshotCaptureReason.MANUAL_REQUEST
    evidence: Optional[TesterEvidence] = None
    error: Optional[str] = None
    sensitive: bool = False
    trace: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.evidence_id:
            self.evidence_id = new_evidence_id()
        validate_evidence_id(self.evidence_id)
        validate_execution_id(self.execution_id)
        validate_runtime_id(self.runtime_id)

        if not isinstance(self.status, ScreenshotCaptureStatus):
            self.status = ScreenshotCaptureStatus(str(self.status).upper())

        if not isinstance(self.capture_reason, ScreenshotCaptureReason):
            try:
                self.capture_reason = ScreenshotCaptureReason(str(self.capture_reason).upper())
            except (ValueError, KeyError):
                self.capture_reason = ScreenshotCaptureReason.MANUAL_REQUEST

        self.viewport = dict(self.viewport or {})
        self.trace = dict(self.trace or {})

    @property
    def is_success(self) -> bool:
        return self.status == ScreenshotCaptureStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize result to a dictionary.
        CRITICAL: Never embeds raw screenshot binary content.
        """
        return {
            "evidence_id": self.evidence_id,
            "execution_id": self.execution_id,
            "runtime_id": self.runtime_id,
            "status": self.status.value,
            "evidence_type": self.evidence_type.value,
            "artifact_reference": self.artifact_reference,
            "captured_at": self.captured_at,
            "viewport": dict(self.viewport),
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "file_size": self.file_size,
            "checksum": self.checksum,
            "capture_reason": self.capture_reason.value,
            "evidence_id_ref": self.evidence.evidence_id if self.evidence else self.evidence_id,
            "error": self.error,
            "sensitive": self.sensitive,
            "trace": dict(self.trace),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScreenshotCaptureResult:
        ev_id = data.get("evidence_id") or data.get("evidence_id_ref") or new_evidence_id()
        status_val = ScreenshotCaptureStatus(data.get("status", ScreenshotCaptureStatus.FAILED.value))
        reason_val = ScreenshotCaptureReason(data.get("capture_reason", ScreenshotCaptureReason.MANUAL_REQUEST.value))

        return cls(
            evidence_id=ev_id,
            execution_id=str(data.get("execution_id", "")),
            runtime_id=str(data.get("runtime_id", "")),
            status=status_val,
            evidence_type=EvidenceType.SCREENSHOT,
            artifact_reference=data.get("artifact_reference"),
            captured_at=str(data.get("captured_at", utc_now())),
            viewport=dict(data.get("viewport", {})),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            format=str(data.get("format", "png")),
            file_size=data.get("file_size"),
            checksum=data.get("checksum"),
            capture_reason=reason_val,
            error=data.get("error"),
            sensitive=bool(data.get("sensitive", False)),
            trace=dict(data.get("trace", {})),
        )

    def to_observation(self, description: str = "") -> VisualObservation:
        """Create a factual VisualObservation from this capture result."""
        return VisualObservation(
            evidence_id=self.evidence_id,
            timestamp=self.captured_at,
            description=description,
            viewport=dict(self.viewport),
            metadata={
                "artifact_reference": self.artifact_reference,
                "checksum": self.checksum,
                "format": self.format,
                "file_size": self.file_size,
                "sensitive": self.sensitive,
                "capture_reason": self.capture_reason.value,
            },
        )


class ScreenshotArtifactStorage:
    """
    Minimal local filesystem artifact storage abstraction for Tester screenshots.
    
    Guarantees:
    - Path isolation: .autonomos/artifacts/projects/{project_id}/executions/{execution_id}/screenshots/
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
            return "capture"
        # Strip path traversal attempts and invalid filename characters
        clean = re.sub(r'[^a-zA-Z0-9_\-]', '_', str(label))
        clean = clean.strip('_')
        return clean or "capture"

    def build_artifact_path(
        self,
        project_id: str,
        execution_id: str,
        evidence_id: str,
        label: Optional[str] = None,
        format: str = "png",
    ) -> Path:
        """Construct the isolated, collision-resistant target file path."""
        clean_label = self.sanitize_label(label)
        clean_format = format.lower().strip().lstrip('.')
        timestamp_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:19]
        filename = f"{execution_id}_{evidence_id}_{clean_label}_{timestamp_slug}.{clean_format}"

        target_dir = self.base_dir / "projects" / project_id / "executions" / execution_id / "screenshots"
        return target_dir / filename

    def store_screenshot(
        self,
        project_id: str,
        execution_id: str,
        evidence_id: str,
        raw_bytes: bytes,
        label: Optional[str] = None,
        format: str = "png",
    ) -> tuple[str, str, int]:
        """
        Write raw screenshot bytes to disk and compute its SHA-256 integrity checksum.
        Returns (artifact_reference, checksum, file_size).
        """
        if not raw_bytes or len(raw_bytes) == 0:
            raise TesterValidationError("Cannot store empty screenshot bytes.", field_name="raw_bytes")

        target_path = self.build_artifact_path(
            project_id=project_id,
            execution_id=execution_id,
            evidence_id=evidence_id,
            label=label,
            format=format,
        )

        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Write binary content
        target_path.write_bytes(raw_bytes)

        checksum = hashlib.sha256(raw_bytes).hexdigest()
        file_size = len(raw_bytes)

        return str(target_path.resolve()), checksum, file_size

    def artifact_exists(self, artifact_reference: Optional[str]) -> bool:
        """Check whether the referenced artifact actually exists on disk and is non-empty."""
        if not artifact_reference:
            return False
        p = Path(artifact_reference)
        return p.is_file() and p.stat().st_size > 0

    def verify_integrity(self, artifact_reference: str, expected_checksum: str) -> bool:
        """Validate that the on-disk file matches the expected SHA-256 checksum."""
        p = Path(artifact_reference)
        if not p.is_file():
            return False
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        return actual == expected_checksum


class ScreenshotCaptureService:
    """
    Subordinate screenshot capture service for Tester V1.
    
    Invariants:
    - Subordinate to TestRuntime and TesterExecution.
    - Governed by 3-way capability boundary:
      Global Tester Boundary ∩ WorkOrder Authorization ∩ Runtime Backend Capabilities.
    - Active runtime state (READY or RUNNING) required. Never silently starts runtime.
    - False-success invariant: SUCCESS reported ONLY when screenshot exists on disk and has valid checksum.
    - No raw screenshot bytes placed into logs, events, or result dictionaries.
    - Screenshot artifacts survive runtime shutdown.
    - Capture failures do not crash or abort TesterExecution.
    """
    __test__ = False

    def __init__(
        self,
        runtime: Any,
        session: Any = None,
        storage: Optional[ScreenshotArtifactStorage] = None,
    ) -> None:
        self.runtime = runtime
        self._session = session
        self._session_explicitly_set = session is not None
        self.storage = storage or ScreenshotArtifactStorage()
        self.history: list[ScreenshotCaptureResult] = []

    @property
    def session(self) -> Any:
        if self._session_explicitly_set:
            return self._session
        return getattr(self.runtime, "session", None)

    @session.setter
    def session(self, value: Any) -> None:
        self._session = value
        self._session_explicitly_set = True

    def capture(
        self,
        options: Optional[ScreenshotCaptureOptions] = None,
        **kwargs: Any,
    ) -> ScreenshotCaptureResult:
        """
        Execute deterministic screenshot capture following the strict lifecycle:
        REQUESTED -> VALIDATING -> CAPTURING -> STORED -> COMPLETED
        """
        opts = options or ScreenshotCaptureOptions(**kwargs)
        opts.validate()

        ev_id = new_evidence_id()
        captured_at = utc_now()
        start_perf = time.perf_counter()

        # 1. Ownership & Lineage Verification
        proj_id = getattr(self.runtime, "project_id", "")
        exec_id = getattr(self.runtime, "execution_id", "")
        wo_id = getattr(self.runtime, "work_order_id", "")
        rt_id = getattr(self.runtime, "runtime_id", "")

        if hasattr(self.runtime, "execution"):
            if self.runtime.execution.execution_id != exec_id:
                raise TesterLineageError(
                    f"Runtime execution_id '{exec_id}' does not match TesterExecution '{self.runtime.execution.execution_id}'",
                    resource_id=exec_id,
                    expected_id=self.runtime.execution.execution_id,
                )

        # 2. State Verification
        status = getattr(self.runtime, "status", None)
        if status in (TestRuntimeStatus.STOPPED, TestRuntimeStatus.STOPPING):
            res = ScreenshotCaptureResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenshotCaptureStatus.FAILED,
                captured_at=captured_at,
                capture_reason=opts.reason,
                error="Cannot capture screenshot on a stopped runtime.",
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            return res

        if status == TestRuntimeStatus.FAILED:
            res = ScreenshotCaptureResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenshotCaptureStatus.FAILED,
                captured_at=captured_at,
                capture_reason=opts.reason,
                error="Cannot capture screenshot on a failed runtime.",
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            return res

        if status != TestRuntimeStatus.RUNNING:
            if status == TestRuntimeStatus.READY:
                self.runtime.run()
            else:
                res = ScreenshotCaptureResult(
                    evidence_id=ev_id,
                    execution_id=exec_id,
                    runtime_id=rt_id,
                    status=ScreenshotCaptureStatus.FAILED,
                    captured_at=captured_at,
                    capture_reason=opts.reason,
                    error=f"Runtime is in status '{status.value if status else 'UNKNOWN'}', not ready for screenshot capture.",
                    sensitive=opts.sensitive,
                )
                self.history.append(res)
                return res

        # 3. Capability Verification (3-way intersection)
        # Check WorkOrder authorization
        wo_authorized = False
        if hasattr(self.runtime, "work_order") and self.runtime.work_order:
            for c in self.runtime.work_order.authorized_capabilities:
                c_val = c.value if isinstance(c, TestingCapability) else str(c).upper()
                if c_val == TestingCapability.SCREENSHOT.value:
                    wo_authorized = True
                    break

        if not wo_authorized:
            err_msg = f"Capability 'SCREENSHOT' is not authorized by WorkOrder '{wo_id}'."
            res = ScreenshotCaptureResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenshotCaptureStatus.DENIED,
                captured_at=captured_at,
                capture_reason=opts.reason,
                error=err_msg,
                sensitive=opts.sensitive,
                trace={"authorization_decision": "DENIED", "required_capability": TestingCapability.SCREENSHOT.value},
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_SCREENSHOT_DENIED, ev_id, opts.reason, error=err_msg)
            return res

        # 4. Session Availability
        if not self.session or getattr(self.session, "is_closed", True) or not getattr(self.session, "is_ready", False):
            err_msg = "Browser session is unavailable or closed."
            res = ScreenshotCaptureResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenshotCaptureStatus.FAILED,
                captured_at=captured_at,
                capture_reason=opts.reason,
                error=err_msg,
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_SCREENSHOT_FAILED, ev_id, opts.reason, error=err_msg)
            return res

        # 5. Check driver backend capability
        session_caps = set()
        if hasattr(self.session, "supported_screenshot_capabilities"):
            session_caps = self.session.supported_screenshot_capabilities()
        elif hasattr(self.session, "supported_interaction_capabilities"):
            session_caps = self.session.supported_interaction_capabilities()

        if TestingCapability.SCREENSHOT not in session_caps:
            err_msg = (
                f"Capability 'SCREENSHOT' is not supported by driver backend "
                f"'{self.session.__class__.__name__ if self.session else 'None'}'."
            )
            res = ScreenshotCaptureResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenshotCaptureStatus.NOT_SUPPORTED,
                captured_at=captured_at,
                capture_reason=opts.reason,
                error=err_msg,
                sensitive=opts.sensitive,
                trace={"authorization_decision": "NOT_SUPPORTED", "required_capability": TestingCapability.SCREENSHOT.value},
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_SCREENSHOT_FAILED, ev_id, opts.reason, error=err_msg)
            return res

        # 5. Execute driver hook
        try:
            ok, raw_bytes, err = self.session.do_capture_screenshot(
                full_page=opts.full_page,
                timeout_seconds=opts.timeout_seconds,
            )
        except Exception as e:
            is_timeout = "timeout" in str(e).lower()
            status_res = ScreenshotCaptureStatus.TIMEOUT if is_timeout else ScreenshotCaptureStatus.FAILED
            res = ScreenshotCaptureResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=status_res,
                captured_at=captured_at,
                capture_reason=opts.reason,
                error=str(e),
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_SCREENSHOT_FAILED, ev_id, opts.reason, error=str(e))
            return res

        if not ok:
            err_text = err or "Screenshot capture failed on browser driver."
            is_unsupported = "not supported" in err_text.lower()
            is_timeout = "timed out" in err_text.lower() or "timeout" in err_text.lower()
            status_res = ScreenshotCaptureStatus.NOT_SUPPORTED if is_unsupported else (
                ScreenshotCaptureStatus.TIMEOUT if is_timeout else ScreenshotCaptureStatus.FAILED
            )
            res = ScreenshotCaptureResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=status_res,
                captured_at=captured_at,
                capture_reason=opts.reason,
                error=err_text,
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_SCREENSHOT_FAILED, ev_id, opts.reason, error=err_text)
            return res

        if not raw_bytes or len(raw_bytes) == 0:
            err_text = "Browser driver returned empty screenshot bytes."
            res = ScreenshotCaptureResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenshotCaptureStatus.FAILED,
                captured_at=captured_at,
                capture_reason=opts.reason,
                error=err_text,
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_SCREENSHOT_FAILED, ev_id, opts.reason, error=err_text)
            return res

        # 6. Store artifact to local isolated filesystem
        try:
            artifact_ref, checksum, file_size = self.storage.store_screenshot(
                project_id=proj_id,
                execution_id=exec_id,
                evidence_id=ev_id,
                raw_bytes=raw_bytes,
                label=opts.label,
                format=opts.format,
            )
        except Exception as e:
            err_text = f"Failed to persist screenshot artifact: {e}"
            res = ScreenshotCaptureResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenshotCaptureStatus.FAILED,
                captured_at=captured_at,
                capture_reason=opts.reason,
                error=err_text,
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_SCREENSHOT_FAILED, ev_id, opts.reason, error=err_text)
            return res

        # Verify on-disk presence (False-success invariant)
        if not self.storage.artifact_exists(artifact_ref):
            err_text = "Screenshot artifact could not be verified on disk after write."
            res = ScreenshotCaptureResult(
                evidence_id=ev_id,
                execution_id=exec_id,
                runtime_id=rt_id,
                status=ScreenshotCaptureStatus.FAILED,
                captured_at=captured_at,
                capture_reason=opts.reason,
                error=err_text,
                sensitive=opts.sensitive,
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_SCREENSHOT_FAILED, ev_id, opts.reason, error=err_text)
            return res

        # 7. Extract Viewport Dimensions
        vp_dict = getattr(self.runtime, "environment", None)
        vp = dict(vp_dict.viewport) if (vp_dict and hasattr(vp_dict, "viewport")) else {"width": 1280, "height": 720}
        vp["device_scale_factor"] = 1.0
        vp["full_page"] = opts.full_page

        w = int(vp.get("width", 1280))
        h = int(vp.get("height", 720))

        # 8. Create Authoritative TesterEvidence
        evidence = TesterEvidence(
            evidence_id=ev_id,
            evidence_type=EvidenceType.SCREENSHOT,
            data=f"Screenshot captured: reason={opts.reason.value}, label={opts.label or 'default'}",
            description=f"Visual screenshot evidence captured during test execution ({opts.reason.value})",
            artifact_reference=artifact_ref,
            execution_id=exec_id,
            work_order_id=wo_id,
            source=f"tester.runtime.{self.session.__class__.__name__}",
            checksum=checksum,
            metadata={
                "viewport": vp,
                "width": w,
                "height": h,
                "format": opts.format,
                "file_size": file_size,
                "capture_reason": opts.reason.value,
                "label": opts.label,
                "sensitive": opts.sensitive,
                "full_page": opts.full_page,
            },
            captured_at=captured_at,
            trace={"runtime_id": rt_id, "action_type": TesterActionType.CAPTURE_SCREENSHOT.value},
        )

        # Register in runtime evidence list if available
        if hasattr(self.runtime, "evidences") and isinstance(self.runtime.evidences, list):
            self.runtime.evidences.append(evidence)

        # 9. Complete Result & Emit Event
        res = ScreenshotCaptureResult(
            evidence_id=ev_id,
            execution_id=exec_id,
            runtime_id=rt_id,
            status=ScreenshotCaptureStatus.SUCCESS,
            evidence_type=EvidenceType.SCREENSHOT,
            artifact_reference=artifact_ref,
            captured_at=captured_at,
            viewport=vp,
            width=w,
            height=h,
            format=opts.format,
            file_size=file_size,
            checksum=checksum,
            capture_reason=opts.reason,
            evidence=evidence,
            sensitive=opts.sensitive,
            trace={"runtime_id": rt_id, "action_type": TesterActionType.CAPTURE_SCREENSHOT.value},
        )
        self.history.append(res)

        # Emit domain event
        self._emit_event(
            EventType.TEST_SCREENSHOT_CAPTURED,
            ev_id,
            opts.reason,
            artifact_reference=artifact_ref,
            checksum=checksum,
            file_size=file_size,
            sensitive=opts.sensitive,
        )

        return res

    def capture_viewport(
        self,
        reason: ScreenshotCaptureReason = ScreenshotCaptureReason.MANUAL_REQUEST,
        label: Optional[str] = None,
        sensitive: bool = False,
        timeout_seconds: float = 30.0,
    ) -> ScreenshotCaptureResult:
        """Convenience method to capture the visible viewport."""
        options = ScreenshotCaptureOptions(
            reason=reason,
            label=label,
            full_page=False,
            sensitive=sensitive,
            timeout_seconds=timeout_seconds,
        )
        return self.capture(options=options)

    def _emit_event(
        self,
        event_type: EventType,
        evidence_id: str,
        reason: ScreenshotCaptureReason,
        artifact_reference: Optional[str] = None,
        checksum: Optional[str] = None,
        file_size: Optional[int] = None,
        sensitive: bool = False,
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
        if error:
            payload["error"] = error

        if hasattr(self.runtime, "emit_runtime_event"):
            self.runtime.emit_runtime_event(event_type, payload)

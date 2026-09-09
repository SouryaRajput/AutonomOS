from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence

from core.events.types import EventType
from core.tester.contracts.finding import TesterEvidence
from core.tester.contracts.identifiers import (
    new_ocr_id,
    new_observation_id,
    validate_evidence_id,
    validate_execution_id,
    validate_ocr_id,
)
from core.tester.contracts.observation import TesterObservation
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    EvidenceType,
    ObservationType,
    OCRStatus,
)

logger = logging.getLogger("AutonomOS.Tester.OCR")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Patterns indicating evaluative judgment rather than factual observation
FORBIDDEN_EVALUATIVE_KEYS = {
    "is_defect",
    "defect",
    "is_failure",
    "verdict",
    "pass_fail",
    "passed",
    "failed",
    "defect_severity",
}


# ---------------------------------------------------------------------------
# Bounding Box & Text Region Models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BoundingBox:
    """
    Structured geometric boundary for visual elements and text regions.
    Represents coordinates and dimensions (pixel or normalized).
    """
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        for name, val in (("x", self.x), ("y", self.y), ("width", self.width), ("height", self.height)):
            if not isinstance(val, (int, float)):
                raise TesterValidationError(
                    f"BoundingBox {name} must be a number, got {type(val).__name__}."
                )
        if self.width < 0 or self.height < 0:
            raise TesterValidationError(
                f"BoundingBox width and height must be non-negative, got width={self.width}, height={self.height}."
            )

    def to_dict(self) -> dict[str, float]:
        return {
            "x": float(self.x),
            "y": float(self.y),
            "width": float(self.width),
            "height": float(self.height),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BoundingBox:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for BoundingBox, got {type(data).__name__}.")
        for key in ("x", "y", "width", "height"):
            if key not in data:
                raise TesterValidationError(f"Missing required BoundingBox field '{key}'.")
        return cls(
            x=float(data["x"]),
            y=float(data["y"]),
            width=float(data["width"]),
            height=float(data["height"]),
        )


@dataclass(frozen=True)
class TextRegion:
    """
    Discrete recognized text segment localized within an image.
    Preserves exact region text, bounding box coordinates, and recognition confidence.
    """
    text: str
    bounding_box: BoundingBox
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TesterValidationError(f"TextRegion text must be a string, got {type(self.text).__name__}.")
        if not isinstance(self.bounding_box, BoundingBox):
            if isinstance(self.bounding_box, dict):
                object.__setattr__(self, "bounding_box", BoundingBox.from_dict(self.bounding_box))
            else:
                raise TesterValidationError(
                    f"TextRegion bounding_box must be a BoundingBox or dict, got {type(self.bounding_box).__name__}."
                )
        if not isinstance(self.confidence, (int, float)):
            raise TesterValidationError(f"TextRegion confidence must be a number, got {type(self.confidence).__name__}.")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise TesterValidationError(
                f"TextRegion confidence must be between 0.0 and 1.0, got {self.confidence}."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "bounding_box": self.bounding_box.to_dict(),
            "confidence": float(self.confidence),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TextRegion:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for TextRegion, got {type(data).__name__}.")
        if "text" not in data:
            raise TesterValidationError("Missing required TextRegion field 'text'.")
        if "bounding_box" not in data:
            raise TesterValidationError("Missing required TextRegion field 'bounding_box'.")
        bbox = data["bounding_box"]
        if isinstance(bbox, dict):
            bbox = BoundingBox.from_dict(bbox)
        return cls(
            text=str(data["text"]),
            bounding_box=bbox,
            confidence=float(data.get("confidence", 1.0)),
        )


# ---------------------------------------------------------------------------
# OCR Result Model
# ---------------------------------------------------------------------------

@dataclass
class OCRResult:
    """
    Structured outcome of an OCR text extraction operation.
    
    Contains recognized visible text, localized bounding regions, overall confidence,
    and causal lineage back to source screenshot evidence.
    
    Invariants:
    - Purely observational: OCR results NEVER assert test pass/fail or detect defects.
    - Preserves uncertainty: low-confidence readings are preserved without artificial rounding.
    - Causal lineage: points explicitly to source_evidence_id and execution_id.
    - Bounded: limits on region count and character length prevent memory exhaustion.
    """
    __test__ = False
    ocr_id: str
    execution_id: str
    project_id: str
    source_evidence_id: str
    status: OCRStatus = OCRStatus.SUCCESS
    extracted_text: str = ""
    text_regions: list[TextRegion] = field(default_factory=list)
    confidence: float = 1.0
    processing_metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now)
    trace: dict[str, Any] = field(default_factory=dict)
    observation_id: Optional[str] = None
    error_message: Optional[str] = None

    def __post_init__(self) -> None:
        # 1. Identifier validations
        validate_ocr_id(self.ocr_id)
        validate_execution_id(self.execution_id)
        validate_evidence_id(self.source_evidence_id)
        if not self.project_id or not str(self.project_id).strip():
            raise TesterLineageError("OCRResult must have a valid non-empty project_id for tenant isolation.")

        # 2. Status normalization
        if isinstance(self.status, str):
            try:
                self.status = OCRStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = OCRStatus.FAILED

        # 3. Confidence bounds
        if not isinstance(self.confidence, (int, float)):
            raise TesterValidationError(f"OCRResult confidence must be a number, got {type(self.confidence).__name__}.")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise TesterValidationError(
                f"OCRResult confidence must be in range [0.0, 1.0], got {self.confidence}."
            )

        # 4. Text regions normalization
        normalized_regions: list[TextRegion] = []
        for r in self.text_regions:
            if isinstance(r, TextRegion):
                normalized_regions.append(r)
            elif isinstance(r, dict):
                normalized_regions.append(TextRegion.from_dict(r))
            else:
                raise TesterValidationError(
                    f"Invalid item in text_regions: expected TextRegion or dict, got {type(r).__name__}."
                )
        self.text_regions = normalized_regions

        # 5. Guard against evaluative judgments
        for key in FORBIDDEN_EVALUATIVE_KEYS:
            if key in self.processing_metadata:
                raise TesterBoundaryViolationError(
                    action="OCR_EVALUATION",
                    reason=(
                        f"OCRResult contains forbidden evaluative key '{key}' in processing_metadata. "
                        "OCR is strictly an observation mechanism and cannot perform evaluation or defect detection."
                    ),
                )

    def to_observation(
        self,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        description: str = "",
    ) -> TesterObservation:
        """
        Bind this OCRResult into a factual, immutable Phase 4.1 TEXT observation.
        Retains complete causal lineage back to source_evidence_id and ocr_id.
        """
        obs_id = new_observation_id()
        engine_name = str(self.processing_metadata.get("engine", "engine"))

        # Build descriptive narrative
        if description:
            desc = description
        elif self.status == OCRStatus.SUCCESS:
            region_count = len(self.text_regions)
            snippet = (self.extracted_text[:60] + "...") if len(self.extracted_text) > 60 else self.extracted_text
            desc = f"OCR extracted {region_count} region(s) from evidence '{self.source_evidence_id}': '{snippet}'"
        else:
            desc = f"OCR extraction completed with status {self.status.value}: {self.error_message or 'No additional details.'}"

        state = {
            "ocr_id": self.ocr_id,
            "source_evidence_id": self.source_evidence_id,
            "status": self.status.value,
            "extracted_text": self.extracted_text,
            "text_regions": [r.to_dict() for r in self.text_regions],
            "region_count": len(self.text_regions),
            "confidence": float(self.confidence),
            "error_message": self.error_message,
        }

        provenance = {
            "evidence_type": "OCR",
            "ocr_id": self.ocr_id,
            "source_evidence_id": self.source_evidence_id,
            "engine": engine_name,
            "status": self.status.value,
            "trace": dict(self.trace),
        }

        obs = TesterObservation(
            observation_id=obs_id,
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
            runtime_id=runtime_id,
            observation_type=ObservationType.TEXT,
            description=desc,
            observed_state=state,
            evidence_ids=[self.source_evidence_id],
            source=f"ocr.{engine_name}",
            confidence=float(self.confidence),
            is_uncertain=(float(self.confidence) < 0.8),
            provenance=provenance,
            trace_id=self.trace.get("trace_id") if isinstance(self.trace, dict) else None,
            metadata=dict(self.processing_metadata),
        )

        self.observation_id = obs_id
        return obs

    # Boundary protection: OCR must NEVER directly produce defects or findings
    def to_defect(self, *args: Any, **kwargs: Any) -> Any:
        """Reject direct defect creation from OCR observations."""
        raise TesterBoundaryViolationError(
            action="OCR_TO_DEFECT",
            reason=(
                "OCRResult cannot directly produce a TesterDefect. OCR is an observation mechanism. "
                "Defect identification requires explicit evaluation and comparison against acceptance criteria."
            ),
        )

    def to_finding(self, *args: Any, **kwargs: Any) -> Any:
        """Reject direct finding creation from OCR observations."""
        raise TesterBoundaryViolationError(
            action="OCR_TO_FINDING",
            reason=(
                "OCRResult cannot directly produce a TesterFinding. OCR is an observation mechanism. "
                "Findings require evaluative analysis by the Tester."
            ),
        )

    def assert_verdict(self, *args: Any, **kwargs: Any) -> Any:
        """Reject asserting test verdicts from OCR observations."""
        raise TesterBoundaryViolationError(
            action="OCR_ASSERT_VERDICT",
            reason=(
                "OCR cannot assert pass/fail verdicts. OCR failure simply means text could not be extracted."
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ocr_id": self.ocr_id,
            "execution_id": self.execution_id,
            "project_id": self.project_id,
            "source_evidence_id": self.source_evidence_id,
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "extracted_text": self.extracted_text,
            "text_regions": [r.to_dict() for r in self.text_regions],
            "confidence": float(self.confidence),
            "processing_metadata": dict(self.processing_metadata),
            "timestamp": self.timestamp,
            "trace": dict(self.trace),
            "observation_id": self.observation_id,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OCRResult:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for OCRResult, got {type(data).__name__}.")
        regions = [
            TextRegion.from_dict(r) if isinstance(r, dict) else r
            for r in data.get("text_regions", [])
        ]
        status = data.get("status", OCRStatus.SUCCESS)
        if isinstance(status, str):
            try:
                status = OCRStatus(status.upper())
            except ValueError:
                status = OCRStatus.SUCCESS
        return cls(
            ocr_id=data["ocr_id"],
            execution_id=data["execution_id"],
            project_id=data["project_id"],
            source_evidence_id=data["source_evidence_id"],
            status=status,
            extracted_text=str(data.get("extracted_text", "")),
            text_regions=regions,
            confidence=float(data.get("confidence", 1.0)),
            processing_metadata=dict(data.get("processing_metadata", {})),
            timestamp=str(data.get("timestamp", utc_now())),
            trace=dict(data.get("trace", {})),
            observation_id=data.get("observation_id"),
            error_message=data.get("error_message"),
        )


# ---------------------------------------------------------------------------
# OCR Provider Interface
# ---------------------------------------------------------------------------

class OCRProvider(ABC):
    """
    Abstract interface for bounded OCR text extraction providers.
    
    Implementations may interface with Tesseract, EasyOCR, vision models, or test mocks.
    All providers must adhere to the bounded observation contract:
    - Never assert test pass/fail verdicts or defect identification.
    - Gracefully handle extraction failures without crashing the execution.
    - Enforce strict input boundaries to prevent unauthorized disk/file access.
    """

    @abstractmethod
    def extract_text(
        self,
        evidence: TesterEvidence,
        execution: Optional[Any] = None,
        options: Optional[dict[str, Any]] = None,
    ) -> OCRResult:
        """
        Extract visible text and text regions from authorized image evidence.
        
        Args:
            evidence: Authorized screenshot or image evidence.
            execution: Optional active TesterExecution context for lineage and tenant checks.
            options: Optional bounded configuration (max_text_length, max_regions, timeouts).
            
        Returns:
            OCRResult containing extracted text, localized regions, and confidence.
        """
        pass


# ---------------------------------------------------------------------------
# Deterministic Mock OCR Provider
# ---------------------------------------------------------------------------

class MockOCRProvider(OCRProvider):
    """
    Deterministic, bounded mock OCR provider for testing and offline execution.
    
    Features:
    - Predefined text and bounding regions configuration.
    - Canned results per evidence_id.
    - Low-confidence and partial readings simulation.
    - Controlled failure simulation (FAILED status).
    - Unavailability simulation (UNAVAILABLE status).
    - Timeout simulation (TIMEOUT status).
    - Unsupported evidence type detection (UNSUPPORTED status).
    - Strict authorization boundary checking (rejects unauthorized evidence).
    - Bounded text length and region count enforcement.
    """

    DEFAULT_MAX_TEXT_LENGTH = 100_000
    DEFAULT_MAX_REGIONS = 1_000

    def __init__(
        self,
        default_text: str = "",
        default_regions: Optional[Sequence[TextRegion]] = None,
        default_confidence: float = 1.0,
        default_status: OCRStatus = OCRStatus.SUCCESS,
        simulate_failure: bool = False,
        simulate_unavailable: bool = False,
        simulate_timeout: bool = False,
        failure_error_message: Optional[str] = None,
        max_text_length: int = DEFAULT_MAX_TEXT_LENGTH,
        max_regions: int = DEFAULT_MAX_REGIONS,
        event_bus: Optional[Any] = None,
    ) -> None:
        self.default_text = default_text
        self.default_regions: list[TextRegion] = list(default_regions or [])
        self.default_confidence = float(default_confidence)
        self.default_status = default_status
        self.simulate_failure = simulate_failure
        self.simulate_unavailable = simulate_unavailable
        self.simulate_timeout = simulate_timeout
        self.failure_error_message = failure_error_message
        self.max_text_length = max_text_length
        self.max_regions = max_regions
        self.event_bus = event_bus
        self.canned_results: dict[str, dict[str, Any]] = {}

    def set_evidence_result(
        self,
        evidence_id: str,
        text: str,
        regions: Optional[Sequence[TextRegion]] = None,
        confidence: float = 1.0,
        status: OCRStatus = OCRStatus.SUCCESS,
        error_message: Optional[str] = None,
    ) -> None:
        """Register canned deterministic OCR results for a specific evidence_id."""
        self.canned_results[evidence_id] = {
            "text": text,
            "regions": list(regions or []),
            "confidence": float(confidence),
            "status": status,
            "error_message": error_message,
        }

    def set_canned_result(
        self,
        key: str,
        extracted_text: str = "",
        text: Optional[str] = None,
        regions: Optional[Sequence[TextRegion]] = None,
        confidence: float = 1.0,
        status: OCRStatus = OCRStatus.SUCCESS,
        error_message: Optional[str] = None,
    ) -> None:
        """Register canned deterministic OCR results for an evidence ID or artifact pattern."""
        self.canned_results[key] = {
            "text": extracted_text if extracted_text else (text or ""),
            "regions": list(regions or []),
            "confidence": float(confidence),
            "status": status,
            "error_message": error_message,
        }

    def set_failure(self, simulate: bool = True, error_message: str = "Simulated OCR engine failure.") -> None:
        """Configure simulation of OCR failure."""
        self.simulate_failure = simulate
        self.failure_error_message = error_message

    def set_unavailable(self, simulate: bool = True) -> None:
        """Configure simulation of OCR engine unavailability."""
        self.simulate_unavailable = simulate

    def set_timeout(self, simulate: bool = True) -> None:
        """Configure simulation of OCR extraction timeout."""
        self.simulate_timeout = simulate

    def _emit_event(self, event_type: EventType, ocr_id: str, evidence_id: str, **payload: Any) -> None:
        if self.event_bus is not None and hasattr(self.event_bus, "emit"):
            try:
                self.event_bus.emit(
                    event_type=event_type,
                    payload={"ocr_id": ocr_id, "evidence_id": evidence_id, **payload},
                )
            except Exception as e:
                logger.debug(f"Failed to emit OCR event: {e}")

    def extract_text(
        self,
        evidence: TesterEvidence,
        execution: Optional[Any] = None,
        options: Optional[dict[str, Any]] = None,
    ) -> OCRResult:
        """
        Execute bounded text extraction on authorized image evidence.
        """
        opts = options or {}
        ocr_id = new_ocr_id()

        # 1. Type validation
        if not isinstance(evidence, TesterEvidence):
            raise TesterValidationError(
                f"Expected TesterEvidence instance, got {type(evidence).__name__}."
            )

        # 2. Strict Input & Authorization Boundary Checks
        # Validate that arbitrary file traversal is strictly rejected
        if evidence.artifact_reference:
            ref = str(evidence.artifact_reference)
            if ".." in ref or ref.startswith(("/etc", "/var", "/tmp/unauthorized", "/sys", "/proc")):
                raise TesterBoundaryViolationError(
                    action="UNAUTHORIZED_DISK_ACCESS",
                    reason=f"OCR cannot access arbitrary or unauthorized filesystem paths: '{ref}'.",
                )

        # Check tenant and execution boundary alignment if execution context is provided
        if execution is not None:
            exec_id = getattr(execution, "execution_id", None)
            proj_id = getattr(execution, "project_id", None)

            if evidence.execution_id and exec_id and evidence.execution_id != exec_id:
                raise TesterLineageError(
                    f"Unauthorized evidence: evidence execution_id ('{evidence.execution_id}') "
                    f"does not match current execution ('{exec_id}')."
                )

            ev_proj = evidence.metadata.get("project_id") if evidence.metadata else None
            if ev_proj and proj_id and ev_proj != proj_id:
                raise TesterBoundaryViolationError(
                    action="OCR_PROJECT_ISOLATION",
                    reason=(
                        f"Unauthorized evidence: evidence project_id ('{ev_proj}') "
                        f"does not match execution project_id ('{proj_id}')."
                    ),
                )

            # Check if evidence is marked unauthorized
            if evidence.metadata and evidence.metadata.get("unauthorized", False):
                raise TesterBoundaryViolationError(
                    action="UNAUTHORIZED_EVIDENCE",
                    reason=f"Evidence '{evidence.evidence_id}' is explicitly marked unauthorized.",
                )

        execution_id = (
            getattr(execution, "execution_id", None)
            or evidence.execution_id
            or "texec-unknown"
        )
        project_id = (
            getattr(execution, "project_id", None)
            or (evidence.metadata.get("project_id") if evidence.metadata else None)
            or "default-project"
        )

        # 3. Evidence Type Compatibility Check
        ev_type = str(evidence.evidence_type.value if hasattr(evidence.evidence_type, "value") else evidence.evidence_type).upper()
        if ev_type not in {"SCREENSHOT", "IMAGE"}:
            err_msg = f"Evidence type '{ev_type}' is not supported for OCR extraction. Supported types: SCREENSHOT, IMAGE."
            res = OCRResult(
                ocr_id=ocr_id,
                execution_id=execution_id,
                project_id=project_id,
                source_evidence_id=evidence.evidence_id,
                status=OCRStatus.UNSUPPORTED,
                extracted_text="",
                text_regions=[],
                confidence=0.0,
                processing_metadata={
                    "engine": "mock_ocr",
                    "evidence_type": ev_type,
                },
                error_message=err_msg,
            )
            self._emit_event(EventType.TEST_OCR_FAILED, ocr_id, evidence.evidence_id, error=err_msg)
            return res

        # 4. Simulated Engine States (Unavailable, Timeout, Failure)
        sim_unavail = opts.get("simulate_unavailable", self.simulate_unavailable)
        if sim_unavail:
            err_msg = "OCR engine is unavailable or uninstalled."
            res = OCRResult(
                ocr_id=ocr_id,
                execution_id=execution_id,
                project_id=project_id,
                source_evidence_id=evidence.evidence_id,
                status=OCRStatus.UNAVAILABLE,
                extracted_text="",
                text_regions=[],
                confidence=0.0,
                processing_metadata={"engine": "mock_ocr", "simulated": True},
                error_message=err_msg,
            )
            self._emit_event(EventType.TEST_OCR_FAILED, ocr_id, evidence.evidence_id, error=err_msg)
            return res

        sim_timeout = opts.get("simulate_timeout", self.simulate_timeout)
        if sim_timeout:
            err_msg = "OCR extraction timed out after configured duration."
            res = OCRResult(
                ocr_id=ocr_id,
                execution_id=execution_id,
                project_id=project_id,
                source_evidence_id=evidence.evidence_id,
                status=OCRStatus.TIMEOUT,
                extracted_text="",
                text_regions=[],
                confidence=0.0,
                processing_metadata={"engine": "mock_ocr", "simulated": True},
                error_message=err_msg,
            )
            self._emit_event(EventType.TEST_OCR_FAILED, ocr_id, evidence.evidence_id, error=err_msg)
            return res

        sim_fail = opts.get("simulate_failure", self.simulate_failure)
        if sim_fail:
            err_msg = opts.get("failure_error_message") or self.failure_error_message or "OCR extraction failed."
            res = OCRResult(
                ocr_id=ocr_id,
                execution_id=execution_id,
                project_id=project_id,
                source_evidence_id=evidence.evidence_id,
                status=OCRStatus.FAILED,
                extracted_text="",
                text_regions=[],
                confidence=0.0,
                processing_metadata={"engine": "mock_ocr", "simulated": True},
                error_message=err_msg,
            )
            self._emit_event(EventType.TEST_OCR_FAILED, ocr_id, evidence.evidence_id, error=err_msg)
            return res

        # 5. Extract Text & Regions (Canned or Default)
        canned = None
        if evidence.evidence_id in self.canned_results:
            canned = self.canned_results[evidence.evidence_id]
        elif evidence.artifact_reference:
            for key, val in self.canned_results.items():
                if key in evidence.artifact_reference or evidence.artifact_reference in key:
                    canned = val
                    break

        if canned is not None:
            raw_text = str(canned.get("text", ""))
            raw_regions = list(canned.get("regions", []))
            conf = float(canned.get("confidence", 1.0))
            status = canned.get("status", OCRStatus.SUCCESS)
            error_message = canned.get("error_message")
        else:
            raw_text = self.default_text
            raw_regions = list(self.default_regions)
            conf = self.default_confidence
            status = self.default_status
            error_message = None

        # 6. Enforce Bounded Resource Limits
        max_chars = opts.get("max_text_length", self.max_text_length)
        max_regs = opts.get("max_regions", self.max_regions)
        text_truncated = False
        regions_truncated = False

        if len(raw_text) > max_chars:
            final_text = raw_text[:max_chars]
            text_truncated = True
        else:
            final_text = raw_text

        if len(raw_regions) > max_regs:
            final_regions = raw_regions[:max_regs]
            regions_truncated = True
        else:
            final_regions = raw_regions

        metadata = {
            "engine": "mock_ocr",
            "duration_ms": 0.5,
            "region_count": len(final_regions),
            "char_count": len(final_text),
            "text_truncated": text_truncated,
            "regions_truncated": regions_truncated,
        }

        result = OCRResult(
            ocr_id=ocr_id,
            execution_id=execution_id,
            project_id=project_id,
            source_evidence_id=evidence.evidence_id,
            status=status,
            extracted_text=final_text,
            text_regions=final_regions,
            confidence=conf,
            processing_metadata=metadata,
            error_message=error_message,
        )

        if status == OCRStatus.SUCCESS:
            self._emit_event(
                EventType.TEST_OCR_EXTRACTED,
                ocr_id,
                evidence.evidence_id,
                region_count=len(final_regions),
                confidence=conf,
            )
        else:
            self._emit_event(
                EventType.TEST_OCR_FAILED,
                ocr_id,
                evidence.evidence_id,
                error=error_message,
            )

        return result

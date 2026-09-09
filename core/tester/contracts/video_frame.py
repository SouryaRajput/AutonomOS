from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import math
from typing import Any, Optional, Sequence, Union

from core.events.types import EventType
from core.tester.contracts.finding import TesterEvidence
from core.tester.contracts.identifiers import (
    new_evidence_id,
    new_frame_observation_id,
    new_observation_id,
    validate_evidence_id,
    validate_execution_id,
    validate_frame_observation_id,
    validate_observation_id,
    validate_step_id,
    validate_test_case_id,
)
from core.tester.contracts.observation import TesterObservation
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    EvidenceType,
    FrameExtractionStatus,
    FrameSelectionStrategy,
    ObservationType,
)

logger = logging.getLogger("AutonomOS.Tester.VideoFrame")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Strictly forbidden evaluative keys to enforce descriptive boundary
FORBIDDEN_EVALUATIVE_KEYS = {
    "is_defect",
    "defect",
    "is_failure",
    "verdict",
    "pass_fail",
    "passed",
    "failed",
    "defect_severity",
    "animation_broken",
    "quality_score",
    "ux_score",
    "video_quality",
}

# Maximum hard cap on extracted frames to prevent memory exhaustion and unbounded processing
MAX_FRAME_BUDGET_LIMIT = 50


# ---------------------------------------------------------------------------
# Frame Position & Extraction Options
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FramePosition:
    """
    Identifies a discrete moment or index within a video recording.
    At least one of timestamp_ms or frame_index must be specified.
    """
    timestamp_ms: Optional[float] = None
    frame_index: Optional[int] = None

    def __post_init__(self) -> None:
        if self.timestamp_ms is None and self.frame_index is None:
            raise TesterValidationError("FramePosition requires at least timestamp_ms or frame_index.")
        if self.timestamp_ms is not None:
            if not isinstance(self.timestamp_ms, (int, float)) or isinstance(self.timestamp_ms, bool):
                raise TesterValidationError("FramePosition timestamp_ms must be a numeric float.")
            if self.timestamp_ms < 0:
                raise TesterValidationError(f"FramePosition timestamp_ms cannot be negative, got {self.timestamp_ms}.")
        if self.frame_index is not None:
            if not isinstance(self.frame_index, int) or isinstance(self.frame_index, bool):
                raise TesterValidationError("FramePosition frame_index must be an integer.")
            if self.frame_index < 0:
                raise TesterValidationError(f"FramePosition frame_index cannot be negative, got {self.frame_index}.")

    @property
    def seconds(self) -> Optional[float]:
        return (self.timestamp_ms / 1000.0) if self.timestamp_ms is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_ms": float(self.timestamp_ms) if self.timestamp_ms is not None else None,
            "frame_index": int(self.frame_index) if self.frame_index is not None else None,
            "seconds": self.seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FramePosition:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for FramePosition, got {type(data).__name__}.")
        ts = data.get("timestamp_ms")
        idx = data.get("frame_index")
        return cls(
            timestamp_ms=float(ts) if ts is not None else None,
            frame_index=int(idx) if idx is not None else None,
        )


@dataclass
class FrameExtractionOptions:
    """
    Bounded configuration specifying which moments of a video to observe.
    Strictly bounded by max_frames to prevent unbounded frame scanning.
    """
    strategy: FrameSelectionStrategy = FrameSelectionStrategy.TIMESTAMP
    timestamps_ms: list[float] = field(default_factory=list)
    frame_indices: list[int] = field(default_factory=list)
    interval_ms: Optional[float] = None
    sample_count: Optional[int] = None
    start_time_ms: float = 0.0
    end_time_ms: Optional[float] = None
    max_frames: int = 10
    timeout_seconds: float = 30.0
    persist_frames: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.strategy, str):
            try:
                self.strategy = FrameSelectionStrategy(self.strategy.upper())
            except (ValueError, KeyError):
                self.strategy = FrameSelectionStrategy.TIMESTAMP
        self.max_frames = max(1, min(int(self.max_frames), MAX_FRAME_BUDGET_LIMIT))
        self.timestamps_ms = [float(t) for t in self.timestamps_ms]
        self.frame_indices = [int(i) for i in self.frame_indices]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "timestamps_ms": list(self.timestamps_ms),
            "frame_indices": list(self.frame_indices),
            "interval_ms": self.interval_ms,
            "sample_count": self.sample_count,
            "start_time_ms": self.start_time_ms,
            "end_time_ms": self.end_time_ms,
            "max_frames": self.max_frames,
            "timeout_seconds": self.timeout_seconds,
            "persist_frames": self.persist_frames,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FrameExtractionOptions:
        strat = data.get("strategy", FrameSelectionStrategy.TIMESTAMP)
        if isinstance(strat, str):
            try:
                strat = FrameSelectionStrategy(strat.upper())
            except ValueError:
                strat = FrameSelectionStrategy.TIMESTAMP
        return cls(
            strategy=strat,
            timestamps_ms=list(data.get("timestamps_ms", [])),
            frame_indices=list(data.get("frame_indices", [])),
            interval_ms=data.get("interval_ms"),
            sample_count=data.get("sample_count"),
            start_time_ms=float(data.get("start_time_ms", 0.0)),
            end_time_ms=data.get("end_time_ms"),
            max_frames=int(data.get("max_frames", 10)),
            timeout_seconds=float(data.get("timeout_seconds", 30.0)),
            persist_frames=bool(data.get("persist_frames", True)),
        )


# ---------------------------------------------------------------------------
# Video Frame Observation Model
# ---------------------------------------------------------------------------

@dataclass
class VideoFrameObservation:
    """
    Factual observation recorded at a specific moment of a video recording.
    
    Connects:
    TestCase -> TestStep -> Video Evidence -> Frame Position -> Frame Observation -> Frame Artifact
    
    Invariants:
    - Purely observational: records what was visible at a timestamp, never infers 'broken animation' or defect.
    - Preserves lineage: points to source_video_evidence_id, execution_id, and frame_evidence_id.
    - Zero evaluation: calling to_defect(), to_finding(), or passing evaluative keys raises TesterBoundaryViolationError.
    - Failure states: SUCCESS, FAILED, UNAVAILABLE, UNSUPPORTED, TIMEOUT without failing product execution.
    """
    __test__ = False
    frame_observation_id: str
    execution_id: str
    project_id: str
    source_video_evidence_id: str
    frame_position: FramePosition
    status: FrameExtractionStatus = FrameExtractionStatus.SUCCESS
    test_case_id: Optional[str] = None
    test_step_id: Optional[str] = None
    runtime_id: Optional[str] = None
    frame_evidence_reference: Optional[str] = None
    frame_evidence_id: Optional[str] = None
    viewport: Optional[dict[str, Any]] = None
    observation_id: Optional[str] = None
    description: str = ""
    confidence: float = 1.0
    is_uncertain: bool = False
    error_message: Optional[str] = None
    observation_metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    trace_id: Optional[str] = None
    timestamp: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        # 1. Identifier validations
        validate_frame_observation_id(self.frame_observation_id)
        validate_execution_id(self.execution_id)
        validate_evidence_id(self.source_video_evidence_id)
        if not self.project_id or not str(self.project_id).strip():
            raise TesterLineageError("VideoFrameObservation must have a valid non-empty project_id.")
        if self.test_case_id is not None:
            validate_test_case_id(self.test_case_id)
        if self.test_step_id is not None:
            validate_step_id(self.test_step_id)
        if self.frame_evidence_id is not None:
            validate_evidence_id(self.frame_evidence_id)
        if self.observation_id is not None:
            validate_observation_id(self.observation_id)

        # 2. Status normalization
        if isinstance(self.status, str):
            try:
                self.status = FrameExtractionStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = FrameExtractionStatus.FAILED

        # 3. Position normalization
        if isinstance(self.frame_position, dict):
            self.frame_position = FramePosition.from_dict(self.frame_position)

        # 4. Confidence & uncertainty bounds
        if not isinstance(self.confidence, (int, float)):
            raise TesterValidationError(f"Confidence must be a number, got {type(self.confidence).__name__}.")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise TesterValidationError(f"Confidence must be in range [0.0, 1.0], got {self.confidence}.")
        if self.confidence < 1.0 or self.status != FrameExtractionStatus.SUCCESS:
            self.is_uncertain = True

        # 5. Guard against evaluative judgments
        for forbidden in FORBIDDEN_EVALUATIVE_KEYS:
            for container_name, container in (
                ("observation_metadata", self.observation_metadata),
                ("provenance", self.provenance),
            ):
                if forbidden in container:
                    raise TesterBoundaryViolationError(
                        action="FRAME_EVALUATION",
                        reason=(
                            f"VideoFrameObservation contains forbidden evaluative key '{forbidden}' in {container_name}. "
                            "Frame observation is descriptive only and cannot contain quality scoring or defect detection."
                        ),
                    )

    def to_observation(self, description: str = "") -> TesterObservation:
        """
        Bind this VideoFrameObservation into a factual Phase 4.1 TesterObservation (type=VIDEO_FRAME).
        Retains complete causal lineage back to source_video_evidence_id and frame_observation_id.
        """
        obs_id = new_observation_id()
        source_name = str(self.provenance.get("source", "video_frame_extractor"))

        # Build descriptive factual narrative
        pos_str = ""
        if self.frame_position.seconds is not None:
            pos_str += f"t={self.frame_position.seconds:.2f}s"
        if self.frame_position.frame_index is not None:
            pos_str += f" (frame {self.frame_position.frame_index})"

        if description:
            desc = description
        elif self.status == FrameExtractionStatus.SUCCESS:
            desc = f"At {pos_str}, video frame observed from evidence '{self.source_video_evidence_id}'."
        else:
            desc = f"Frame observation at {pos_str} completed with status {self.status.value}: {self.error_message or 'No details.'}"

        state = {
            "frame_observation_id": self.frame_observation_id,
            "source_video_evidence_id": self.source_video_evidence_id,
            "frame_position": self.frame_position.to_dict(),
            "status": self.status.value,
            "frame_evidence_reference": self.frame_evidence_reference,
            "frame_evidence_id": self.frame_evidence_id,
            "viewport": dict(self.viewport or {}),
            "confidence": float(self.confidence),
            "error_message": self.error_message,
        }
        state.update(self.observation_metadata)

        provenance = {
            "evidence_type": "VIDEO_FRAME",
            "frame_observation_id": self.frame_observation_id,
            "source_video_evidence_id": self.source_video_evidence_id,
            "frame_evidence_id": self.frame_evidence_id,
            "source": source_name,
            "status": self.status.value,
        }
        provenance.update(self.provenance)

        ev_ids = [self.source_video_evidence_id]
        if self.frame_evidence_id:
            ev_ids.append(self.frame_evidence_id)

        obs = TesterObservation(
            observation_id=obs_id,
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            test_step_id=self.test_step_id,
            runtime_id=self.runtime_id,
            observation_type=ObservationType.VIDEO_FRAME,
            description=desc,
            observed_state=state,
            evidence_ids=ev_ids,
            source=source_name,
            confidence=float(self.confidence),
            is_uncertain=self.is_uncertain,
            provenance=provenance,
            trace_id=self.trace_id,
            metadata=dict(self.observation_metadata),
        )

        self.observation_id = obs_id
        return obs

    # Boundary protection: Frame observations must NEVER directly produce defects or findings
    def to_defect(self, *args: Any, **kwargs: Any) -> Any:
        """Reject direct defect creation from frame observations."""
        raise TesterBoundaryViolationError(
            action="FRAME_TO_DEFECT",
            reason=(
                "VideoFrameObservation cannot directly produce a TesterDefect. Frame observation is descriptive only. "
                "Defect identification requires explicit comparison against acceptance criteria in downstream phases."
            ),
        )

    def to_finding(self, *args: Any, **kwargs: Any) -> Any:
        """Reject direct finding creation from frame observations."""
        raise TesterBoundaryViolationError(
            action="FRAME_TO_FINDING",
            reason=(
                "VideoFrameObservation cannot directly produce a TesterFinding. Frame observation is descriptive only."
            ),
        )

    def assert_verdict(self, *args: Any, **kwargs: Any) -> Any:
        """Reject asserting test verdicts from frame observations."""
        raise TesterBoundaryViolationError(
            action="FRAME_ASSERT_VERDICT",
            reason=(
                "Frame observation cannot assert pass/fail verdicts. Extraction failure does NOT equal product failure."
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_observation_id": self.frame_observation_id,
            "execution_id": self.execution_id,
            "project_id": self.project_id,
            "source_video_evidence_id": self.source_video_evidence_id,
            "frame_position": self.frame_position.to_dict(),
            "status": self.status.value,
            "test_case_id": self.test_case_id,
            "test_step_id": self.test_step_id,
            "runtime_id": self.runtime_id,
            "frame_evidence_reference": self.frame_evidence_reference,
            "frame_evidence_id": self.frame_evidence_id,
            "viewport": dict(self.viewport or {}),
            "observation_id": self.observation_id,
            "description": self.description,
            "confidence": float(self.confidence),
            "is_uncertain": bool(self.is_uncertain),
            "error_message": self.error_message,
            "observation_metadata": dict(self.observation_metadata),
            "provenance": dict(self.provenance),
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VideoFrameObservation:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for VideoFrameObservation, got {type(data).__name__}.")

        pos = data.get("frame_position")
        if isinstance(pos, dict):
            pos = FramePosition.from_dict(pos)

        status = data.get("status", FrameExtractionStatus.SUCCESS)
        if isinstance(status, str):
            try:
                status = FrameExtractionStatus(status.upper())
            except ValueError:
                status = FrameExtractionStatus.FAILED

        return cls(
            frame_observation_id=data["frame_observation_id"],
            execution_id=data["execution_id"],
            project_id=data["project_id"],
            source_video_evidence_id=data["source_video_evidence_id"],
            frame_position=pos,
            status=status,
            test_case_id=data.get("test_case_id"),
            test_step_id=data.get("test_step_id"),
            runtime_id=data.get("runtime_id"),
            frame_evidence_reference=data.get("frame_evidence_reference"),
            frame_evidence_id=data.get("frame_evidence_id"),
            viewport=dict(data.get("viewport", {})),
            observation_id=data.get("observation_id"),
            description=str(data.get("description", "")),
            confidence=float(data.get("confidence", 1.0)),
            is_uncertain=bool(data.get("is_uncertain", False)),
            error_message=data.get("error_message"),
            observation_metadata=dict(data.get("observation_metadata", {})),
            provenance=dict(data.get("provenance", {})),
            trace_id=data.get("trace_id"),
            timestamp=str(data.get("timestamp", utc_now())),
        )


# ---------------------------------------------------------------------------
# Bounded Video Frame Extractor Service
# ---------------------------------------------------------------------------

class VideoFrameExtractor:
    """
    Bounded frame observation service over explicitly recorded video evidence.
    
    Principles:
    - Never scans every frame by default; enforces strict sample/interval budgets.
    - Strictly inspects authorized video evidence belonging to the current execution.
    - Handles extraction failures (corrupted video, timeout, unavailable) gracefully.
    - Frame extraction failure != test failure.
    """

    def __init__(
        self,
        default_max_frames: int = 10,
        event_bus: Optional[Any] = None,
        simulate_timeout: bool = False,
        simulate_failure: bool = False,
        simulate_unavailable: bool = False,
        failure_error_message: Optional[str] = None,
    ) -> None:
        self.default_max_frames = default_max_frames
        self.event_bus = event_bus
        self.simulate_timeout = simulate_timeout
        self.simulate_failure = simulate_failure
        self.simulate_unavailable = simulate_unavailable
        self.failure_error_message = failure_error_message
        self.canned_frames: dict[str, list[VideoFrameObservation]] = {}

    def set_canned_frames(self, evidence_id: str, frames: Sequence[VideoFrameObservation]) -> None:
        """Register canned deterministic frame observations for testing."""
        self.canned_frames[evidence_id] = list(frames)

    def extract_frames(
        self,
        evidence: TesterEvidence,
        options: Optional[FrameExtractionOptions] = None,
        execution: Optional[Any] = None,
        work_order: Optional[Any] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
    ) -> list[VideoFrameObservation]:
        """
        Extract bounded frame observations from authorized video evidence.
        """
        opts = options or FrameExtractionOptions()

        # 1. Type validation
        if not isinstance(evidence, TesterEvidence):
            raise TesterValidationError(
                f"Expected TesterEvidence instance, got {type(evidence).__name__}."
            )

        # 2. Strict Input & Authorization Boundary Checks
        # Validate arbitrary file access / directory traversal
        if evidence.artifact_reference:
            ref = str(evidence.artifact_reference)
            if ".." in ref or ref.startswith(("/etc", "/var", "/tmp/unauthorized", "/sys", "/proc")):
                raise TesterBoundaryViolationError(
                    action="UNAUTHORIZED_VIDEO_ACCESS",
                    reason=f"Cannot access arbitrary or unauthorized video filesystem path: '{ref}'.",
                )

        # Isolation checks against execution context
        if execution is not None:
            exec_id = getattr(execution, "execution_id", None)
            proj_id = getattr(execution, "project_id", None)

            if evidence.execution_id and exec_id and evidence.execution_id != exec_id:
                raise TesterLineageError(
                    f"Unauthorized video evidence: evidence execution_id ('{evidence.execution_id}') "
                    f"does not match current execution ('{exec_id}')."
                )

            ev_proj = evidence.metadata.get("project_id") if evidence.metadata else None
            if ev_proj and proj_id and ev_proj != proj_id:
                raise TesterBoundaryViolationError(
                    action="VIDEO_PROJECT_ISOLATION",
                    reason=(
                        f"Unauthorized video evidence: evidence project_id ('{ev_proj}') "
                        f"does not match execution project_id ('{proj_id}')."
                    ),
                )

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
        if ev_type != "VIDEO":
            err_msg = f"Evidence type '{ev_type}' is unsupported for video frame extraction. Must be VIDEO."
            fail_obs = VideoFrameObservation(
                frame_observation_id=new_frame_observation_id(),
                execution_id=execution_id,
                project_id=project_id,
                source_video_evidence_id=evidence.evidence_id,
                frame_position=FramePosition(timestamp_ms=0.0, frame_index=0),
                status=FrameExtractionStatus.UNSUPPORTED,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
                error_message=err_msg,
                confidence=0.0,
                provenance={"source": "video_frame_extractor", "evidence_type": ev_type},
            )
            return [fail_obs]

        # 4. Check for Missing or Non-existent Video
        if (
            (evidence.metadata and (evidence.metadata.get("missing") or evidence.metadata.get("file_missing") or evidence.metadata.get("not_found")))
            or (evidence.artifact_reference == "")
            or (not evidence.artifact_reference and not evidence.details)
        ):
            err_msg = f"Video evidence source file is missing or not found: '{evidence.artifact_reference}'."
            return [
                VideoFrameObservation(
                    frame_observation_id=new_frame_observation_id(),
                    execution_id=execution_id,
                    project_id=project_id,
                    source_video_evidence_id=evidence.evidence_id,
                    frame_position=FramePosition(timestamp_ms=0.0, frame_index=0),
                    status=FrameExtractionStatus.UNAVAILABLE,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                    error_message=err_msg,
                    confidence=0.0,
                    provenance={"source": "video_frame_extractor", "missing": True},
                )
            ]

        # 5. Simulation Hooks (Timeout, Unavailable, Failure)
        if self.simulate_unavailable:
            err_msg = "Video frame extraction service is unavailable or decoder codec missing."
            return [
                VideoFrameObservation(
                    frame_observation_id=new_frame_observation_id(),
                    execution_id=execution_id,
                    project_id=project_id,
                    source_video_evidence_id=evidence.evidence_id,
                    frame_position=FramePosition(timestamp_ms=0.0, frame_index=0),
                    status=FrameExtractionStatus.UNAVAILABLE,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                    error_message=err_msg,
                    confidence=0.0,
                    provenance={"source": "video_frame_extractor", "simulated": True},
                )
            ]

        if self.simulate_timeout:
            err_msg = f"Video frame extraction timed out after {opts.timeout_seconds}s."
            return [
                VideoFrameObservation(
                    frame_observation_id=new_frame_observation_id(),
                    execution_id=execution_id,
                    project_id=project_id,
                    source_video_evidence_id=evidence.evidence_id,
                    frame_position=FramePosition(timestamp_ms=0.0, frame_index=0),
                    status=FrameExtractionStatus.TIMEOUT,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                    error_message=err_msg,
                    confidence=0.0,
                    provenance={"source": "video_frame_extractor", "simulated": True},
                )
            ]

        if self.simulate_failure:
            err_msg = self.failure_error_message or "Video decoding error or corrupted container."
            return [
                VideoFrameObservation(
                    frame_observation_id=new_frame_observation_id(),
                    execution_id=execution_id,
                    project_id=project_id,
                    source_video_evidence_id=evidence.evidence_id,
                    frame_position=FramePosition(timestamp_ms=0.0, frame_index=0),
                    status=FrameExtractionStatus.FAILED,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                    error_message=err_msg,
                    confidence=0.0,
                    provenance={"source": "video_frame_extractor", "simulated": True},
                )
            ]

        # 5. Check Canned Frames
        if evidence.evidence_id in self.canned_frames:
            canned = self.canned_frames[evidence.evidence_id]
            # Clamp to budget limit
            max_budget = min(opts.max_frames, MAX_FRAME_BUDGET_LIMIT)
            return canned[:max_budget]

        # 6. Extract Metadata
        meta = evidence.metadata or {}
        duration_ms = float(meta.get("duration_ms", 3000.0))
        fps = int(meta.get("fps", 30))
        vp = meta.get("viewport")

        # 7. Compute Target Frame Positions According to Strategy
        target_positions: list[FramePosition] = []
        max_budget = min(opts.max_frames, MAX_FRAME_BUDGET_LIMIT)

        # Budget enforcement from WorkOrder if available
        if work_order is not None:
            if hasattr(work_order, "iteration_budget") and work_order.iteration_budget is not None:
                max_budget = min(max_budget, int(work_order.iteration_budget))
            elif hasattr(work_order, "max_iterations") and work_order.max_iterations is not None:
                max_budget = min(max_budget, int(work_order.max_iterations))

        if opts.strategy == FrameSelectionStrategy.TIMESTAMP:
            if not opts.timestamps_ms:
                # Default small sample if no timestamps provided
                target_positions.append(FramePosition(timestamp_ms=0.0, frame_index=0))
            else:
                for ts in opts.timestamps_ms:
                    idx = int(round((ts / 1000.0) * fps)) if fps > 0 else 0
                    target_positions.append(FramePosition(timestamp_ms=ts, frame_index=idx))

        elif opts.strategy == FrameSelectionStrategy.FRAME_INDEX:
            if not opts.frame_indices:
                target_positions.append(FramePosition(timestamp_ms=0.0, frame_index=0))
            else:
                for idx in opts.frame_indices:
                    ts = (idx / fps * 1000.0) if fps > 0 else 0.0
                    target_positions.append(FramePosition(timestamp_ms=ts, frame_index=idx))

        elif opts.strategy == FrameSelectionStrategy.INTERVAL:
            interval = float(opts.interval_ms or 500.0)
            if interval <= 0:
                interval = 500.0
            start_t = max(0.0, opts.start_time_ms)
            end_t = min(duration_ms, float(opts.end_time_ms or duration_ms))
            current = start_t
            while current <= end_t and len(target_positions) < max_budget:
                idx = int(round((current / 1000.0) * fps)) if fps > 0 else 0
                target_positions.append(FramePosition(timestamp_ms=current, frame_index=idx))
                current += interval

        elif opts.strategy == FrameSelectionStrategy.SAMPLE:
            sample_cnt = min(max_budget, int(opts.sample_count or 5))
            if sample_cnt <= 1:
                target_positions.append(FramePosition(timestamp_ms=0.0, frame_index=0))
            else:
                step = duration_ms / float(sample_cnt - 1)
                for s in range(sample_cnt):
                    ts = min(duration_ms, s * step)
                    idx = int(round((ts / 1000.0) * fps)) if fps > 0 else 0
                    target_positions.append(FramePosition(timestamp_ms=ts, frame_index=idx))

        # Enforce budget truncation
        if len(target_positions) > max_budget:
            target_positions = target_positions[:max_budget]

        # 8. Generate Frame Observations
        results: list[VideoFrameObservation] = []
        for pos in target_positions:
            f_obs_id = new_frame_observation_id()
            # Validate timestamp bounds
            if pos.timestamp_ms is not None and pos.timestamp_ms > (duration_ms + 1000.0):
                # Out of video range
                f_obs = VideoFrameObservation(
                    frame_observation_id=f_obs_id,
                    execution_id=execution_id,
                    project_id=project_id,
                    source_video_evidence_id=evidence.evidence_id,
                    frame_position=pos,
                    status=FrameExtractionStatus.FAILED,
                    test_case_id=test_case_id,
                    test_step_id=test_step_id,
                    runtime_id=evidence.metadata.get("runtime_id"),
                    error_message=f"Requested timestamp {pos.timestamp_ms}ms exceeds video duration {duration_ms}ms.",
                    confidence=0.0,
                    provenance={"source": "video_frame_extractor", "strategy": opts.strategy.value},
                )
                results.append(f_obs)
                continue

            # Deterministic simulated frame artifact reference
            frame_ref = (
                f"artifacts/frames/{evidence.evidence_id}_f{pos.frame_index or 0}.png"
                if opts.persist_frames
                else None
            )

            f_obs = VideoFrameObservation(
                frame_observation_id=f_obs_id,
                execution_id=execution_id,
                project_id=project_id,
                source_video_evidence_id=evidence.evidence_id,
                frame_position=pos,
                status=FrameExtractionStatus.SUCCESS,
                test_case_id=test_case_id,
                test_step_id=test_step_id,
                runtime_id=evidence.metadata.get("runtime_id"),
                frame_evidence_reference=frame_ref,
                viewport=vp,
                confidence=1.0,
                observation_metadata={
                    "duration_ms": duration_ms,
                    "fps": fps,
                    "strategy": opts.strategy.value,
                },
                provenance={
                    "source": "video_frame_extractor",
                    "strategy": opts.strategy.value,
                    "source_evidence_id": evidence.evidence_id,
                },
            )
            results.append(f_obs)

        return results

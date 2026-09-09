from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Sequence, Union

from core.events.types import EventType
from core.tester.contracts.identifiers import (
    new_observation_id,
    validate_evidence_id,
    validate_execution_id,
    validate_observation_id,
    validate_runtime_id,
    validate_step_id,
    validate_test_case_id,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    ObservationConfidence,
    ObservationType,
)

logger = logging.getLogger("AutonomOS.Tester.Observation")


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


@dataclass
class TesterObservation:
    """
    Structured, immutable descriptive observation recorded during test execution.
    
    Connects:
    TestCase -> TestStep -> Runtime State -> Observation -> Evidence
    
    Invariants:
    - Purely descriptive: records WHAT WAS OBSERVED, never decides pass/fail or identifies defects.
    - Strictly immutable once initialized: cannot be modified after creation.
    - Explicit uncertainty: uncertain observations are flagged (is_uncertain=True) and never converted into facts.
    - Preservation of causal lineage: execution_id, project_id, test_case_id, test_step_id, and evidence_ids.
    - Evaluation API rejection: cannot directly produce TesterDefect or TesterFinding.
    """
    __test__ = False
    observation_id: str
    execution_id: str
    project_id: str
    test_case_id: Optional[str] = None
    test_step_id: Optional[str] = None
    runtime_id: Optional[str] = None
    observation_type: ObservationType = ObservationType.OTHER
    description: str = ""
    observed_state: dict[str, Any] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=utc_now)
    source: str = ""
    confidence: float = 1.0
    is_uncertain: bool = False
    provenance: dict[str, Any] = field(default_factory=dict)
    trace_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    _initialized: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        # 1. Identifier & Lineage Validation
        validate_observation_id(self.observation_id)
        validate_execution_id(self.execution_id)
        if not self.project_id or not str(self.project_id).strip():
            raise TesterLineageError(
                "TesterObservation must have a valid non-empty project_id for project isolation."
            )
        if self.test_case_id is not None:
            validate_test_case_id(self.test_case_id)
        if self.test_step_id is not None:
            validate_step_id(self.test_step_id)
        if self.runtime_id is not None:
            validate_runtime_id(self.runtime_id)

        # 2. Type Normalization
        if isinstance(self.observation_type, str):
            try:
                self.observation_type = ObservationType(self.observation_type.upper())
            except (ValueError, KeyError):
                self.observation_type = ObservationType.OTHER

        # 3. Evidence References Validation
        valid_ev_ids: list[str] = []
        for eid in self.evidence_ids:
            if eid:
                validate_evidence_id(str(eid))
                valid_ev_ids.append(str(eid))
        self.evidence_ids = valid_ev_ids

        # 4. Confidence & Uncertainty Quantification
        if not isinstance(self.confidence, (int, float)):
            raise TesterValidationError(
                f"Confidence must be a numeric float, got {type(self.confidence).__name__}.",
                field_name="confidence",
            )
        if self.confidence < 0.0 or self.confidence > 1.0:
            raise TesterValidationError(
                f"Confidence score {self.confidence} is out of valid bounds [0.0, 1.0].",
                field_name="confidence",
            )
        # Any observation with confidence < 1.0 is strictly marked uncertain
        if self.confidence < 1.0:
            self.is_uncertain = True

        # 5. Defensive copy of dictionary collections
        self.observed_state = dict(self.observed_state)
        self.provenance = dict(self.provenance)
        self.metadata = dict(self.metadata)

        # 6. Evaluation vs. Observation Enforcement
        for forbidden in FORBIDDEN_EVALUATIVE_KEYS:
            if forbidden in self.observed_state or forbidden in self.metadata:
                raise TesterBoundaryViolationError(
                    action="RECORD_EVALUATION_AS_OBSERVATION",
                    reason=(
                        f"Observation contains forbidden evaluative key '{forbidden}'. "
                        "Observations are descriptive only and must not record defects or pass/fail decisions."
                    ),
                )

        # 7. Lock instance to enforce strict historical immutability
        object.__setattr__(self, "_initialized", True)

    def __setattr__(self, name: str, value: Any) -> None:
        """Enforce strict immutability once created and persisted."""
        if getattr(self, "_initialized", False):
            raise TesterBoundaryViolationError(
                action="MUTATE_OBSERVATION",
                reason=(
                    f"Observation '{self.observation_id}' is immutable. "
                    f"Cannot modify field '{name}'. Create a new observation instead."
                ),
            )
        super().__setattr__(name, value)

    # ----------------------------------------------------------------------
    # Prohibit Producing Defects or Findings Directly
    # ----------------------------------------------------------------------

    def to_defect(self, *args: Any, **kwargs: Any) -> Any:
        """Strictly prohibited: Observation cannot produce a defect directly."""
        raise TesterBoundaryViolationError(
            action="PRODUCE_DEFECT_FROM_OBSERVATION",
            reason=(
                "Observations are purely descriptive and cannot produce TesterDefect directly. "
                "Defect identification belongs to a downstream evaluation phase."
            ),
        )

    def to_finding(self, *args: Any, **kwargs: Any) -> Any:
        """Strictly prohibited: Observation cannot produce a finding directly."""
        raise TesterBoundaryViolationError(
            action="PRODUCE_FINDING_FROM_OBSERVATION",
            reason=(
                "Observations are purely descriptive and cannot produce TesterFinding directly. "
                "Finding evaluation belongs to a downstream evaluation phase."
            ),
        )

    # ----------------------------------------------------------------------
    # Serialization
    # ----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "execution_id": self.execution_id,
            "project_id": self.project_id,
            "test_case_id": self.test_case_id,
            "test_step_id": self.test_step_id,
            "runtime_id": self.runtime_id,
            "observation_type": self.observation_type.value,
            "description": self.description,
            "observed_state": dict(self.observed_state),
            "evidence_ids": list(self.evidence_ids),
            "timestamp": self.timestamp,
            "source": self.source,
            "confidence": self.confidence,
            "is_uncertain": self.is_uncertain,
            "provenance": dict(self.provenance),
            "trace_id": self.trace_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TesterObservation:
        obs_type_raw = data.get("observation_type", ObservationType.OTHER.value)
        try:
            obs_type = ObservationType(str(obs_type_raw).upper())
        except (ValueError, KeyError):
            obs_type = ObservationType.OTHER

        return cls(
            observation_id=str(data.get("observation_id", "")),
            execution_id=str(data.get("execution_id", "")),
            project_id=str(data.get("project_id", "")),
            test_case_id=data.get("test_case_id"),
            test_step_id=data.get("test_step_id"),
            runtime_id=data.get("runtime_id"),
            observation_type=obs_type,
            description=str(data.get("description", "")),
            observed_state=dict(data.get("observed_state", {})),
            evidence_ids=list(data.get("evidence_ids", [])),
            timestamp=str(data.get("timestamp", utc_now())),
            source=str(data.get("source", "")),
            confidence=float(data.get("confidence", 1.0)),
            is_uncertain=bool(data.get("is_uncertain", False)),
            provenance=dict(data.get("provenance", {})),
            trace_id=data.get("trace_id"),
            metadata=dict(data.get("metadata", {})),
        )


class ObservationBinder:
    """
    Deterministic factory and evidence binder for Tester observations.
    Binds evidence produced by Tester runtime capabilities (screenshots, recordings,
    DOM/geometry, text, metrics) into factual TesterObservation records.
    """
    __test__ = False

    @classmethod
    def _extract_evidence_id(cls, evidence: Any) -> Optional[str]:
        if evidence is None:
            return None
        if isinstance(evidence, str):
            return evidence
        if hasattr(evidence, "evidence_id"):
            return str(evidence.evidence_id)
        if isinstance(evidence, dict) and "evidence_id" in evidence:
            return str(evidence["evidence_id"])
        return None

    @classmethod
    def bind_screenshot(
        cls,
        execution_id: str,
        project_id: str,
        evidence: Any,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        description: str = "",
        observed_state: Optional[dict[str, Any]] = None,
        viewport: Optional[dict[str, Any]] = None,
        confidence: float = 1.0,
        trace_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> TesterObservation:
        """Bind a visual screenshot capture result into a SCREEN observation."""
        ev_id = cls._extract_evidence_id(evidence)
        ev_ids = [ev_id] if ev_id else []

        state = dict(observed_state or {})
        if viewport is not None:
            state["viewport"] = dict(viewport)
        elif hasattr(evidence, "viewport") and getattr(evidence, "viewport", None):
            state.setdefault("viewport", dict(evidence.viewport))
        if hasattr(evidence, "artifact_reference") and getattr(evidence, "artifact_reference", None):
            state.setdefault("artifact_reference", evidence.artifact_reference)
        state.setdefault("status", "SUCCESS")

        provenance = {
            "evidence_type": "SCREENSHOT",
            "evidence_id": ev_id,
            "source_type": type(evidence).__name__,
        }

        return TesterObservation(
            observation_id=new_observation_id(),
            execution_id=execution_id,
            project_id=project_id,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
            runtime_id=runtime_id,
            observation_type=ObservationType.SCREEN,
            description=description or "Visual screen state captured via screenshot.",
            observed_state=state,
            evidence_ids=ev_ids,
            source="browser.screenshot",
            confidence=confidence,
            provenance=provenance,
            trace_id=trace_id,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def bind_video_frame(
        cls,
        execution_id: str,
        project_id: str,
        evidence: Any,
        frame_timestamp_ms: Optional[float] = None,
        frame_index: Optional[int] = None,
        timestamp_ms: Optional[float] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        description: str = "",
        observed_state: Optional[dict[str, Any]] = None,
        confidence: float = 1.0,
        trace_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> TesterObservation:
        """Bind a video frame or segment from a screen recording into a VIDEO_FRAME observation."""
        ev_id = cls._extract_evidence_id(evidence)
        ev_ids = [ev_id] if ev_id else []

        effective_ts = frame_timestamp_ms if frame_timestamp_ms is not None else timestamp_ms
        state = dict(observed_state or {})
        if effective_ts is not None:
            state["frame_timestamp_ms"] = effective_ts
        if frame_index is not None:
            state["frame_index"] = frame_index
        if hasattr(evidence, "duration_ms") and getattr(evidence, "duration_ms", None) is not None:
            state.setdefault("recording_duration_ms", evidence.duration_ms)

        provenance = {
            "evidence_type": "SCREEN_RECORDING",
            "evidence_id": ev_id,
            "source_type": type(evidence).__name__,
        }

        return TesterObservation(
            observation_id=new_observation_id(),
            execution_id=execution_id,
            project_id=project_id,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
            runtime_id=runtime_id,
            observation_type=ObservationType.VIDEO_FRAME,
            description=description or "Video frame observation from screen recording.",
            observed_state=state,
            evidence_ids=ev_ids,
            source="screen_recording.video_frame",
            confidence=confidence,
            provenance=provenance,
            trace_id=trace_id,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def bind_geometry(
        cls,
        execution_id: str,
        project_id: str,
        bounding_box: dict[str, Any],
        evidence: Optional[Any] = None,
        target_element: Optional[str] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        description: str = "",
        confidence: float = 1.0,
        trace_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> TesterObservation:
        """Bind spatial coordinates or element geometry into a GEOMETRY observation."""
        ev_id = cls._extract_evidence_id(evidence)
        ev_ids = [ev_id] if ev_id else []

        state = {
            "bounding_box": dict(bounding_box),
            "target_element": target_element,
        }

        provenance = {
            "evidence_type": "GEOMETRY",
            "evidence_id": ev_id,
            "target_element": target_element,
        }

        return TesterObservation(
            observation_id=new_observation_id(),
            execution_id=execution_id,
            project_id=project_id,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
            runtime_id=runtime_id,
            observation_type=ObservationType.GEOMETRY,
            description=description or (
                f"Element '{target_element}' observed at geometry {bounding_box}."
                if target_element
                else f"Geometry observed at {bounding_box}."
            ),
            observed_state=state,
            evidence_ids=ev_ids,
            source="dom.bounding_rect",
            confidence=confidence,
            provenance=provenance,
            trace_id=trace_id,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def bind_text(
        cls,
        execution_id: str,
        project_id: str,
        text: str,
        evidence: Optional[Any] = None,
        target: Optional[str] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        description: str = "",
        confidence: float = 1.0,
        is_uncertain: bool = False,
        trace_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> TesterObservation:
        """Bind textual content into a TEXT observation."""
        ev_id = cls._extract_evidence_id(evidence)
        ev_ids = [ev_id] if ev_id else []

        state = {
            "text": str(text),
            "target": target,
        }

        provenance = {
            "evidence_type": "TEXT",
            "evidence_id": ev_id,
            "target": target,
        }

        return TesterObservation(
            observation_id=new_observation_id(),
            execution_id=execution_id,
            project_id=project_id,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
            runtime_id=runtime_id,
            observation_type=ObservationType.TEXT,
            description=description or f"Text observed: '{text}'.",
            observed_state=state,
            evidence_ids=ev_ids,
            source="dom.text_content",
            confidence=confidence,
            is_uncertain=is_uncertain,
            provenance=provenance,
            trace_id=trace_id,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def bind_ui_state(
        cls,
        execution_id: str,
        project_id: str,
        ui_state: dict[str, Any],
        evidence: Optional[Any] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        description: str = "",
        confidence: float = 1.0,
        trace_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> TesterObservation:
        """Bind interactive component or DOM state into a UI_STATE observation."""
        ev_id = cls._extract_evidence_id(evidence)
        ev_ids = [ev_id] if ev_id else []

        provenance = {
            "evidence_type": "UI_STATE",
            "evidence_id": ev_id,
        }

        return TesterObservation(
            observation_id=new_observation_id(),
            execution_id=execution_id,
            project_id=project_id,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
            runtime_id=runtime_id,
            observation_type=ObservationType.UI_STATE,
            description=description or "Interactive UI state observed.",
            observed_state=dict(ui_state),
            evidence_ids=ev_ids,
            source="ui.interaction_engine",
            confidence=confidence,
            provenance=provenance,
            trace_id=trace_id,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def bind_runtime_state(
        cls,
        execution_id: str,
        project_id: str,
        runtime_state: dict[str, Any],
        evidence: Optional[Any] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        description: str = "",
        confidence: float = 1.0,
        trace_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> TesterObservation:
        """Bind runtime environment snapshot into a RUNTIME_STATE observation."""
        ev_id = cls._extract_evidence_id(evidence)
        ev_ids = [ev_id] if ev_id else []

        provenance = {
            "evidence_type": "RUNTIME_STATE",
            "evidence_id": ev_id,
        }

        return TesterObservation(
            observation_id=new_observation_id(),
            execution_id=execution_id,
            project_id=project_id,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
            runtime_id=runtime_id,
            observation_type=ObservationType.RUNTIME_STATE,
            description=description or "Runtime environment state observed.",
            observed_state=dict(runtime_state),
            evidence_ids=ev_ids,
            source="test_runtime.environment",
            confidence=confidence,
            provenance=provenance,
            trace_id=trace_id,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def bind_metric(
        cls,
        execution_id: str,
        project_id: str,
        metric_name: str,
        metric_value: Any,
        unit: Optional[str] = None,
        evidence: Optional[Any] = None,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        description: str = "",
        confidence: float = 1.0,
        trace_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> TesterObservation:
        """Bind quantitative performance or operational metrics into a METRIC observation."""
        ev_id = cls._extract_evidence_id(evidence)
        ev_ids = [ev_id] if ev_id else []

        state = {
            "metric_name": metric_name,
            "metric_value": metric_value,
            "unit": unit,
        }

        provenance = {
            "evidence_type": "METRIC",
            "evidence_id": ev_id,
            "metric_name": metric_name,
        }

        return TesterObservation(
            observation_id=new_observation_id(),
            execution_id=execution_id,
            project_id=project_id,
            test_case_id=test_case_id,
            test_step_id=test_step_id,
            runtime_id=runtime_id,
            observation_type=ObservationType.METRIC,
            description=description or f"Metric '{metric_name}' observed: {metric_value}{(' ' + unit) if unit else ''}.",
            observed_state=state,
            evidence_ids=ev_ids,
            source="metric_monitor",
            confidence=confidence,
            provenance=provenance,
            trace_id=trace_id,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def bind_ocr_result(
        cls,
        ocr_result: Any,
        test_case_id: Optional[str] = None,
        test_step_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        description: str = "",
    ) -> TesterObservation:
        """Bind an OCRResult into a factual TEXT observation."""
        if hasattr(ocr_result, "to_observation"):
            return ocr_result.to_observation(
                test_case_id=test_case_id,
                test_step_id=test_step_id,
                runtime_id=runtime_id,
                description=description,
            )
        raise TesterValidationError(
            f"Expected OCRResult instance with to_observation(), got {type(ocr_result).__name__}."
        )

    @classmethod
    def bind_geometry_observation(
        cls,
        geometry_observation: Any,
        description: str = "",
    ) -> TesterObservation:
        """Bind a GeometryObservation into a factual GEOMETRY observation."""
        if hasattr(geometry_observation, "to_observation"):
            return geometry_observation.to_observation(description=description)
        raise TesterValidationError(
            f"Expected GeometryObservation instance with to_observation(), got {type(geometry_observation).__name__}."
        )

    @classmethod
    def bind_video_frame_observation(
        cls,
        video_frame_observation: Any,
        description: str = "",
    ) -> TesterObservation:
        """Bind a VideoFrameObservation into a factual VIDEO_FRAME observation."""
        if hasattr(video_frame_observation, "to_observation"):
            return video_frame_observation.to_observation(description=description)
        raise TesterValidationError(
            f"Expected VideoFrameObservation instance with to_observation(), got {type(video_frame_observation).__name__}."
        )

    @classmethod
    def emit_observation_event(
        cls,
        event_bus: Any,
        observation: TesterObservation,
    ) -> None:
        """Emit TEST_OBSERVATION_RECORDED event to configured event bus."""
        if event_bus is not None and hasattr(event_bus, "emit"):
            try:
                event_bus.emit(
                    event_type=EventType.TEST_OBSERVATION_RECORDED,
                    payload={
                        "observation_id": observation.observation_id,
                        "execution_id": observation.execution_id,
                        "project_id": observation.project_id,
                        "observation_type": (
                            observation.observation_type.value
                            if hasattr(observation.observation_type, "value")
                            else str(observation.observation_type)
                        ),
                        "evidence_ids": list(observation.evidence_ids),
                        "confidence": float(observation.confidence),
                        "is_uncertain": bool(observation.is_uncertain),
                    },
                )
            except Exception as e:
                logger.debug(f"Failed to emit observation event: {e}")

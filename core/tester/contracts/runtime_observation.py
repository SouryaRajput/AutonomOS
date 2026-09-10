from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Optional

from core.tester.contracts.identifiers import (
    new_runtime_event_id,
    validate_evidence_id,
    validate_execution_id,
    validate_runtime_event_id,
    validate_test_case_id,
)
from core.tester.contracts.observation import TesterObservation
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    ObservationType,
    RuntimeEventSeverity,
    RuntimeEventType,
)

logger = logging.getLogger("AutonomOS.Tester.RuntimeObservation")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RuntimeObservation:
    """
    Structured descriptive observation of an individual runtime or network event.
    
    Represents actionable application behavior observed during testing:
    - HTTP requests and responses
    - Console errors and warnings
    - Process exceptions and crashes
    - Navigation failures
    - Resource failures
    - API interaction failures

    Invariants:
    1. Purely Descriptive: Records what happened in the runtime/network layer.
       Does NOT directly create or classify defects (TesterDefect creation is forbidden here).
    2. Causal Lineage: Preserves execution_id, project_id, optional test_case_id, evidence, trace.
    3. Noise Separation: Distinguishes application-owned errors from irrelevant external/host noise.
    4. Severity Quantification: Represents technical seriousness (INFO, WARNING, ERROR, CRITICAL).
    """
    __test__ = False
    event_id: str
    execution_id: str
    project_id: str
    event_type: RuntimeEventType
    message: str
    test_case_id: Optional[str] = None
    timestamp: str = field(default_factory=utc_now)
    url: Optional[str] = None
    method: Optional[str] = None
    status_code: Optional[int] = None
    source: str = ""
    severity: RuntimeEventSeverity = RuntimeEventSeverity.INFO
    evidence_ids: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    is_application_owned: bool = True
    duration_ms: Optional[float] = None
    is_required_resource: Optional[bool] = None

    def __post_init__(self) -> None:
        if not self.event_id:
            self.event_id = new_runtime_event_id()
        validate_runtime_event_id(self.event_id)
        validate_execution_id(self.execution_id)
        if not self.project_id or not str(self.project_id).strip():
            raise TesterLineageError("RuntimeObservation requires a non-empty project_id for project isolation.")
        if self.test_case_id is not None:
            validate_test_case_id(self.test_case_id)

        # Normalize event_type
        if isinstance(self.event_type, str):
            try:
                self.event_type = RuntimeEventType(self.event_type.upper())
            except (ValueError, KeyError):
                self.event_type = RuntimeEventType.HTTP_REQUEST

        # Normalize severity
        if isinstance(self.severity, str):
            try:
                self.severity = RuntimeEventSeverity(self.severity.upper())
            except (ValueError, KeyError):
                self.severity = RuntimeEventSeverity.INFO

        # Validate evidence IDs
        valid_evs: list[str] = []
        for eid in self.evidence_ids:
            if eid:
                validate_evidence_id(str(eid))
                valid_evs.append(str(eid))
        self.evidence_ids = valid_evs

        self.provenance = dict(self.provenance or {})
        self.trace = dict(self.trace or {})
        self.metadata = dict(self.metadata or {})

    @property
    def type(self) -> RuntimeEventType:
        """Alias for event_type per Phase 5.3 specification."""
        return self.event_type

    @property
    def path(self) -> Optional[str]:
        """Alias for url / path."""
        return self.url

    @property
    def is_failure(self) -> bool:
        """Return True if event represents an actionable error or critical failure."""
        return self.severity in (RuntimeEventSeverity.ERROR, RuntimeEventSeverity.CRITICAL)

    @property
    def is_critical(self) -> bool:
        """Return True if event has CRITICAL technical severity."""
        return self.severity == RuntimeEventSeverity.CRITICAL

    @property
    def is_warning(self) -> bool:
        """Return True if event has WARNING technical severity."""
        return self.severity == RuntimeEventSeverity.WARNING

    @property
    def is_info(self) -> bool:
        """Return True if event has INFO technical severity."""
        return self.severity == RuntimeEventSeverity.INFO

    def to_observation(self) -> TesterObservation:
        """
        Convert this RuntimeObservation into a canonical descriptive TesterObservation.
        Connects runtime evaluation into the global observation aggregation layer.
        """
        state = {
            "runtime_event_id": self.event_id,
            "event_type": self.event_type.value,
            "url": self.url,
            "method": self.method,
            "status_code": self.status_code,
            "message": self.message,
            "source": self.source,
            "severity": self.severity.value,
            "is_application_owned": self.is_application_owned,
            "duration_ms": self.duration_ms,
            "is_required_resource": self.is_required_resource,
        }
        if self.metadata:
            state["metadata"] = dict(self.metadata)

        # Generate corresponding tobs- observation ID
        obs_id = f"tobs-{self.event_id.replace('trtevt-', '')}"

        return TesterObservation(
            observation_id=obs_id,
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_case_id=self.test_case_id,
            observation_type=ObservationType.RUNTIME_STATE,
            description=f"[{self.event_type.value} - {self.severity.value}] {self.message}",
            observed_state=state,
            evidence_ids=list(self.evidence_ids),
            timestamp=self.timestamp,
            source=self.source or "RuntimeEvaluator",
            provenance=dict(self.provenance),
            trace_id=self.trace.get("trace_id"),
        )

    def to_defect(self, *args, **kwargs) -> Any:
        """Strict boundary protection: Runtime events cannot directly synthesize defects."""
        raise TesterBoundaryViolationError(
            action="CREATE_DEFECT_FROM_OBSERVATION",
            reason=(
                f"RuntimeObservation '{self.event_id}' is an observation layer object. "
                "Runtime events cannot directly create TesterDefect records; evaluation and defect classification remain separate."
            ),
        )

    def create_defect(self, *args, **kwargs) -> Any:
        """Alias boundary protection for defect generation."""
        return self.to_defect(*args, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to standard dictionary."""
        return {
            "event_id": self.event_id,
            "execution_id": self.execution_id,
            "project_id": self.project_id,
            "test_case_id": self.test_case_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type.value,
            "type": self.event_type.value,
            "url": self.url,
            "path": self.url,
            "method": self.method,
            "status_code": self.status_code,
            "message": self.message,
            "source": self.source,
            "severity": self.severity.value,
            "evidence_ids": list(self.evidence_ids),
            "provenance": dict(self.provenance),
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "is_application_owned": self.is_application_owned,
            "duration_ms": self.duration_ms,
            "is_required_resource": self.is_required_resource,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeObservation:
        """Deserialize from dictionary."""
        raw_type = data.get("event_type") or data.get("type", RuntimeEventType.HTTP_REQUEST.value)
        try:
            event_type = RuntimeEventType(str(raw_type).upper())
        except (ValueError, KeyError):
            event_type = RuntimeEventType.HTTP_REQUEST

        raw_sev = data.get("severity", RuntimeEventSeverity.INFO.value)
        try:
            severity = RuntimeEventSeverity(str(raw_sev).upper())
        except (ValueError, KeyError):
            severity = RuntimeEventSeverity.INFO

        return cls(
            event_id=str(data.get("event_id") or new_runtime_event_id()),
            execution_id=str(data.get("execution_id", "")),
            project_id=str(data.get("project_id", "")),
            event_type=event_type,
            message=str(data.get("message", "")),
            test_case_id=data.get("test_case_id"),
            timestamp=str(data.get("timestamp") or utc_now()),
            url=data.get("url") or data.get("path"),
            method=data.get("method"),
            status_code=data.get("status_code"),
            source=str(data.get("source", "")),
            severity=severity,
            evidence_ids=list(data.get("evidence_ids", [])),
            provenance=dict(data.get("provenance", {})),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            is_application_owned=bool(data.get("is_application_owned", True)),
            duration_ms=data.get("duration_ms"),
            is_required_resource=data.get("is_required_resource"),
        )

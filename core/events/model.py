from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Optional
import uuid

from core.events.types import EventSource, EventType


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def new_event_id() -> str:
    """Generate unique event identifier with 'evt-' prefix."""
    return f"evt-{uuid.uuid4()}"


@dataclass
class Event:
    """
    Immutable historical fact recorded by the AutonomOS runtime.
    Represents an authoritative record of what occurred in the system.
    """
    event_id: str
    event_type: EventType
    timestamp: str
    source: EventSource
    correlation_id: str
    payload: dict[str, Any]
    sequence_number: Optional[int] = None
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    worker_id: Optional[str] = None
    artifact_id: Optional[str] = None
    causation_id: Optional[str] = None
    schema_version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sequence_number": self.sequence_number,
            "event_type": self.event_type.value if isinstance(self.event_type, EventType) else self.event_type,
            "timestamp": self.timestamp,
            "source": self.source.value if isinstance(self.source, EventSource) else self.source,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "artifact_id": self.artifact_id,
            "schema_version": self.schema_version,
            "payload": self.payload,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        return cls(
            event_id=data["event_id"],
            sequence_number=data.get("sequence_number"),
            event_type=EventType(data["event_type"]) if isinstance(data["event_type"], str) else data["event_type"],
            timestamp=data.get("timestamp", utc_now()),
            source=EventSource(data.get("source", EventSource.RUNTIME.value)),
            correlation_id=data["correlation_id"],
            causation_id=data.get("causation_id"),
            project_id=data.get("project_id"),
            task_id=data.get("task_id"),
            worker_id=data.get("worker_id"),
            artifact_id=data.get("artifact_id"),
            schema_version=data.get("schema_version", 1),
            payload=dict(data.get("payload", {})),
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def create(
        cls,
        event_type: EventType,
        source: EventSource = EventSource.RUNTIME,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
        event_id: Optional[str] = None,
    ) -> "Event":
        """Factory constructor with automatic ID and timestamp generation."""
        cid = correlation_id or str(uuid.uuid4())
        eid = event_id or new_event_id()
        return cls(
            event_id=eid,
            event_type=event_type,
            timestamp=utc_now(),
            source=source,
            correlation_id=cid,
            causation_id=causation_id,
            project_id=project_id,
            task_id=task_id,
            worker_id=worker_id,
            artifact_id=artifact_id,
            payload=payload or {},
            metadata=metadata or {},
        )

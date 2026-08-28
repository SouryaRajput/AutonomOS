from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Optional
from core.events.model import Event
from core.events.activity import ActivityItem

@dataclass
class EventDTO:
    event_id: str
    event_type: str
    timestamp: str
    source: str
    project_id: Optional[str]
    task_id: Optional[str]
    worker_id: Optional[str]
    payload: Dict[str, Any]
    sequence_number: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "source": self.source,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "payload": self.payload,
            "sequence_number": self.sequence_number
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EventDTO:
        return cls(
            event_id=data["event_id"],
            event_type=data["event_type"],
            timestamp=data["timestamp"],
            source=data["source"],
            project_id=data.get("project_id"),
            task_id=data.get("task_id"),
            worker_id=data.get("worker_id"),
            payload=data.get("payload", {}),
            sequence_number=data.get("sequence_number", 0)
        )

    @classmethod
    def from_domain(cls, event: Event) -> EventDTO:
        return cls(
            event_id=event.event_id,
            event_type=event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type),
            timestamp=event.timestamp,
            source=event.source.value if hasattr(event.source, 'value') else str(event.source),
            project_id=event.project_id,
            task_id=event.task_id,
            worker_id=event.worker_id,
            payload=event.payload,
            sequence_number=event.sequence_number
        )

@dataclass
class ActivityItemDTO:
    icon: str
    title: str
    subtitle: str
    level: str
    timestamp: str
    event_type: str
    project_id: Optional[str]
    task_id: Optional[str]
    worker_id: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "icon": self.icon,
            "title": self.title,
            "subtitle": self.subtitle,
            "level": self.level,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ActivityItemDTO:
        return cls(
            icon=data["icon"],
            title=data["title"],
            subtitle=data["subtitle"],
            level=data["level"],
            timestamp=data["timestamp"],
            event_type=data["event_type"],
            project_id=data.get("project_id"),
            task_id=data.get("task_id"),
            worker_id=data.get("worker_id")
        )

    @classmethod
    def from_domain(cls, item: ActivityItem) -> ActivityItemDTO:
        return cls(
            icon=item.icon,
            title=item.title,
            subtitle=item.subtitle,
            level=item.level.value if hasattr(item.level, 'value') else str(item.level),
            timestamp=item.timestamp,
            event_type=item.event_type.value if hasattr(item.event_type, 'value') else str(item.event_type),
            project_id=item.project_id,
            task_id=item.task_id,
            worker_id=item.worker_id
        )

@dataclass
class EventStreamEnvelope:
    type: str  # 'event'|'heartbeat'|'resync'
    sequence_number: int
    data: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "sequence_number": self.sequence_number,
            "data": self.data
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EventStreamEnvelope:
        return cls(
            type=data.get("type", "event"),
            sequence_number=data.get("sequence_number", 0),
            data=data.get("data", {})
        )

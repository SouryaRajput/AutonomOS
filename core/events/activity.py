from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from core.events.model import Event
from core.events.types import EventSource, EventType


class ActivityLevel(str, Enum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass
class ActivityItem:
    """
    Product-facing projection of a system event.
    Designed for consumption by UI components (Activity Feed, Task Timelines, Chat notifications).
    """
    id: str
    event_id: str
    event_type: str
    timestamp: str
    title: str
    description: str
    level: ActivityLevel = ActivityLevel.INFO
    icon: str = "activity"
    actor: Optional[str] = None
    target: Optional[str] = None
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    worker_id: Optional[str] = None
    artifact_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "title": self.title,
            "description": self.description,
            "level": self.level.value if isinstance(self.level, ActivityLevel) else self.level,
            "icon": self.icon,
            "actor": self.actor,
            "target": self.target,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "artifact_id": self.artifact_id,
            "metadata": self.metadata,
        }


class ActivityProjector:
    """Projects technical low-level Event streams into human-friendly ActivityItems."""

    @classmethod
    def project(cls, event: Event) -> ActivityItem:
        p = event.payload
        t = event.event_type
        level = ActivityLevel.INFO
        icon = "activity"
        title = t.value
        description = ""

        # Project Events
        if t == EventType.PROJECT_CREATED:
            icon = "project_create"
            level = ActivityLevel.SUCCESS
            title = f"Project '{p.get('name', event.project_id)}' created"
            description = p.get("description", "")

        elif t == EventType.PROJECT_UPDATED:
            icon = "project_edit"
            title = f"Project '{event.project_id}' updated"

        elif t == EventType.PROJECT_DELETED:
            icon = "project_delete"
            level = ActivityLevel.WARNING
            title = f"Project '{event.project_id}' deleted"

        # Worker Events
        elif t == EventType.WORKER_REGISTERED:
            icon = "worker_add"
            level = ActivityLevel.SUCCESS
            title = f"Worker '{event.worker_id}' registered ({p.get('role', 'Unknown role')})"

        elif t == EventType.WORKER_ASSIGNED:
            icon = "worker_assign"
            title = f"Worker '{event.worker_id}' assigned to task '{event.task_id}'"

        elif t == EventType.WORKER_STARTED:
            icon = "worker_run"
            title = f"Worker '{event.worker_id}' started working on task '{event.task_id}'"

        elif t == EventType.WORKER_FAILED:
            icon = "alert_error"
            level = ActivityLevel.ERROR
            title = f"Worker '{event.worker_id}' encountered a failure"
            description = p.get("reason", "")

        elif t == EventType.WORKER_BECAME_IDLE:
            icon = "worker_idle"
            title = f"Worker '{event.worker_id}' is now idle"

        # Task Events
        elif t == EventType.TASK_CREATED:
            icon = "task_add"
            title = f"Task created: '{p.get('title', event.task_id)}'"
            description = p.get("objective", "")

        elif t == EventType.TASK_READY:
            icon = "task_ready"
            title = f"Task '{event.task_id}' is now ready"
            description = p.get("reason", "")

        elif t == EventType.TASK_ASSIGNED:
            icon = "task_assign"
            title = f"Task '{event.task_id}' assigned to worker '{event.worker_id}'"

        elif t == EventType.TASK_STARTED:
            icon = "task_start"
            title = f"Task '{event.task_id}' started (Attempt {p.get('attempt', 1)})"

        elif t == EventType.TASK_COMPLETED:
            icon = "task_success"
            level = ActivityLevel.SUCCESS
            title = f"Task '{event.task_id}' completed successfully"
            description = p.get("summary", "")

        elif t == EventType.TASK_FAILED:
            icon = "task_error"
            level = ActivityLevel.ERROR
            title = f"Task '{event.task_id}' failed"
            description = p.get("reason", "")

        elif t == EventType.TASK_RETRYING:
            icon = "task_retry"
            level = ActivityLevel.WARNING
            title = f"Task '{event.task_id}' retrying (Attempt {p.get('attempt', 1)}/{p.get('max_attempts', 3)})"
            description = p.get("reason", "")

        elif t == EventType.TASK_BLOCKED:
            icon = "task_block"
            level = ActivityLevel.WARNING
            title = f"Task '{event.task_id}' blocked"

        elif t == EventType.TASK_CANCELLED:
            icon = "task_cancel"
            level = ActivityLevel.WARNING
            title = f"Task '{event.task_id}' cancelled"
            description = p.get("reason", "")

        # Dependency Events
        elif t == EventType.DEPENDENCY_ADDED:
            icon = "dependency"
            title = f"Dependency linked: '{p.get('dependent_task_id')}' requires '{p.get('prerequisite_task_id')}'"

        # Artifact Events
        elif t == EventType.ARTIFACT_CREATED:
            icon = "artifact"
            level = ActivityLevel.SUCCESS
            title = f"Artifact produced: '{p.get('path', event.artifact_id)}'"
            description = p.get("description", "")

        # Memory & Knowledge Events
        elif t == EventType.MEMORY_CREATED:
            icon = "memory_create"
            level = ActivityLevel.SUCCESS
            title = f"Memory recorded: '{p.get('title', event.payload.get('relative_path', 'document'))}'"
            description = p.get("summary", "")

        elif t == EventType.MEMORY_UPDATED:
            icon = "memory_edit"
            title = f"Memory updated: '{p.get('title', event.payload.get('relative_path', 'document'))}'"
            description = f"Updated to version {p.get('version', 1)}"

        elif t == EventType.MEMORY_DELETED:
            icon = "memory_delete"
            level = ActivityLevel.WARNING
            title = f"Memory deleted: '{p.get('relative_path', 'document')}'"

        elif t == EventType.DECISION_CREATED:
            icon = "decision"
            level = ActivityLevel.SUCCESS
            title = f"Architectural Decision: '{p.get('title', 'ADR')}'"
            description = p.get("summary", "")

        elif t == EventType.REPORT_CREATED:
            icon = "report"
            level = ActivityLevel.SUCCESS
            title = f"Report filed: '{p.get('title', 'Task Report')}'"
            description = p.get("summary", "")

        elif t == EventType.ISSUE_RECORDED:
            icon = "issue_open"
            level = ActivityLevel.WARNING
            title = f"Issue recorded: '{p.get('title', 'Known Problem')}'"
            description = p.get("description", "")

        elif t == EventType.ISSUE_UPDATED:
            icon = "issue_update"
            title = f"Issue status updated: '{p.get('title', 'Known Problem')}'"
            description = f"Status: {p.get('status', 'OPEN')}"

        # Worker Self-Reports
        elif t == EventType.WORKER_PROGRESS_LOGGED:
            icon = "worker_log"
            title = f"[{event.worker_id or 'Worker'}] {p.get('event_type', 'Progress')}"
            description = str(p.get("data", p))

        # Errors
        elif t in (EventType.RUNTIME_ERROR, EventType.EXECUTION_FAILED):
            icon = "alert_error"
            level = ActivityLevel.ERROR
            title = f"Runtime Error: {p.get('error', 'Unknown Error')}"
            description = p.get("message", str(p))

        return ActivityItem(
            id=f"act-{event.event_id}",
            event_id=event.event_id,
            event_type=event.event_type.value,
            timestamp=event.timestamp,
            title=title,
            description=description,
            level=level,
            icon=icon,
            actor=event.worker_id or event.source.value,
            target=event.task_id or event.project_id or event.artifact_id,
            project_id=event.project_id,
            task_id=event.task_id,
            worker_id=event.worker_id,
            artifact_id=event.artifact_id,
            metadata=event.metadata,
        )


def format_event_log_line(event: Event) -> str:
    """Format an event as a compact, colored-friendly CLI debug log line."""
    # Extract time component
    ts_time = event.timestamp.split("T")[-1].replace("Z", "")[:8] if "T" in event.timestamp else event.timestamp
    seq = f"#{event.sequence_number:04d}" if event.sequence_number is not None else "#----"
    
    parts = [f"[{ts_time}]", seq, f"[{event.event_type.value}]"]

    if event.task_id:
        parts.append(f"task={event.task_id}")
    if event.worker_id:
        parts.append(f"worker={event.worker_id}")
    if event.artifact_id:
        parts.append(f"artifact={event.artifact_id}")

    # Add key details from payload
    if "summary" in event.payload:
        parts.append(f"— {event.payload['summary']}")
    elif "reason" in event.payload:
        parts.append(f"— reason: {event.payload['reason']}")
    elif "title" in event.payload:
        parts.append(f"— '{event.payload['title']}'")

    return " ".join(parts)

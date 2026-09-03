from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ActivityStatus(str, Enum):
    """Execution status for a workforce activity presentation unit."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    STALLED = "STALLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FileOperationType(str, Enum):
    """Type of file operation performed during execution."""
    READ = "READ"
    CREATED = "CREATED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"


@dataclass
class CommandLineItem:
    """Sanitized command execution item with secrets redacted."""
    command: str
    exit_code: Optional[int] = None
    duration_ms: Optional[float] = None
    is_running: bool = False
    output_preview: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "is_running": self.is_running,
            "output_preview": self.output_preview,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandLineItem:
        return cls(
            command=data.get("command", ""),
            exit_code=data.get("exit_code"),
            duration_ms=data.get("duration_ms"),
            is_running=data.get("is_running", False),
            output_preview=data.get("output_preview", ""),
            timestamp=data.get("timestamp", ""),
        )


@dataclass
class FileActivityItem:
    """Tracked file modification, creation, or deletion."""
    path: str
    operation: FileOperationType
    size_bytes: Optional[int] = None
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "operation": self.operation.value if isinstance(self.operation, FileOperationType) else str(self.operation),
            "size_bytes": self.size_bytes,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileActivityItem:
        op = data.get("operation", "READ")
        try:
            op_enum = FileOperationType(op)
        except Exception:
            op_enum = FileOperationType.READ
        return cls(
            path=data.get("path", ""),
            operation=op_enum,
            size_bytes=data.get("size_bytes"),
            timestamp=data.get("timestamp", ""),
        )


@dataclass
class WorkerActivityItem:
    """High-level specialist worker or crawler state."""
    worker_id: str
    name: str
    role: str
    status: str = "IDLE"  # IDLE, RUNNING, WAITING, COMPLETED, FAILED
    current_action: str = ""
    task_target: str = ""
    error: Optional[str] = None
    started_at: str = ""
    completed_at: Optional[str] = None

    @property
    def action(self) -> str:
        return self.current_action

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "name": self.name,
            "role": self.role,
            "status": self.status,
            "current_action": self.current_action,
            "task_target": self.task_target,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerActivityItem:
        return cls(
            worker_id=data.get("worker_id", ""),
            name=data.get("name", "Worker"),
            role=data.get("role", "Specialist"),
            status=data.get("status", "IDLE"),
            current_action=data.get("current_action", ""),
            task_target=data.get("task_target", ""),
            error=data.get("error"),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at"),
        )


@dataclass
class ExecutionActivity:
    """
    Sanitized, structured intermediate presentation model for workforce execution.
    Maintains real-time visibility into what the autonomous system is doing right now,
    what steps it completed, commands run, files read/changed, active workers, and metrics.
    """
    activity_id: str
    project_id: str
    task_id: str = ""
    correlation_id: str = ""
    worker_id: str = ""
    worker_type: str = "Manager"
    title: str = "Workforce Execution"
    description: str = ""
    status: ActivityStatus = ActivityStatus.PENDING
    start_time: str = ""
    end_time: Optional[str] = None
    duration_ms: Optional[float] = None
    current_action: str = "Idle"
    completed_actions: list[str] = field(default_factory=list)
    commands: list[CommandLineItem] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_changed: list[FileActivityItem] = field(default_factory=list)
    workers: list[WorkerActivityItem] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    error_summary: Optional[str] = None
    waiting_reason: Optional[str] = None
    is_live: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity_id": self.activity_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "correlation_id": self.correlation_id,
            "worker_id": self.worker_id,
            "worker_type": self.worker_type,
            "title": self.title,
            "description": self.description,
            "status": self.status.value if isinstance(self.status, ActivityStatus) else str(self.status),
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "current_action": self.current_action,
            "completed_actions": list(self.completed_actions),
            "commands": [c.to_dict() for c in self.commands],
            "files_read": list(self.files_read),
            "files_changed": [f.to_dict() for f in self.files_changed],
            "workers": [w.to_dict() for w in self.workers],
            "metrics": dict(self.metrics),
            "error_summary": self.error_summary,
            "waiting_reason": self.waiting_reason,
            "is_live": self.is_live,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionActivity:
        st = data.get("status", "PENDING")
        try:
            status_enum = ActivityStatus(st)
        except Exception:
            status_enum = ActivityStatus.PENDING

        return cls(
            activity_id=data.get("activity_id", ""),
            project_id=data.get("project_id", ""),
            task_id=data.get("task_id", ""),
            correlation_id=data.get("correlation_id", ""),
            worker_id=data.get("worker_id", ""),
            worker_type=data.get("worker_type", "Manager"),
            title=data.get("title", "Workforce Execution"),
            description=data.get("description", ""),
            status=status_enum,
            start_time=data.get("start_time", ""),
            end_time=data.get("end_time"),
            duration_ms=data.get("duration_ms"),
            current_action=data.get("current_action", "Idle"),
            completed_actions=list(data.get("completed_actions", [])),
            commands=[CommandLineItem.from_dict(c) for c in data.get("commands", [])],
            files_read=list(data.get("files_read", [])),
            files_changed=[FileActivityItem.from_dict(f) for f in data.get("files_changed", [])],
            workers=[WorkerActivityItem.from_dict(w) for w in data.get("workers", [])],
            metrics=dict(data.get("metrics", {})),
            error_summary=data.get("error_summary"),
            waiting_reason=data.get("waiting_reason"),
            is_live=data.get("is_live", True),
        )

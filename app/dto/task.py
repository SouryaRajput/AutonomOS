from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from core.models import Task

@dataclass
class TaskSummaryDTO:
    id: str
    title: str
    status: str
    assigned_worker_name: Optional[str]
    priority: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "assigned_worker_name": self.assigned_worker_name,
            "priority": self.priority
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskSummaryDTO:
        return cls(
            id=data["id"],
            title=data["title"],
            status=data["status"],
            assigned_worker_name=data.get("assigned_worker_name"),
            priority=data.get("priority", 0)
        )

@dataclass
class TaskDTO:
    id: str
    project_id: str
    title: str
    objective: str
    status: str
    priority: int
    risk: str
    assigned_worker_id: Optional[str]
    assigned_worker_name: Optional[str]
    dependency_ids: List[str]
    artifact_ids: List[str]
    attempts: int
    max_attempts: int
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "objective": self.objective,
            "status": self.status,
            "priority": self.priority,
            "risk": self.risk,
            "assigned_worker_id": self.assigned_worker_id,
            "assigned_worker_name": self.assigned_worker_name,
            "dependency_ids": self.dependency_ids,
            "artifact_ids": self.artifact_ids,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskDTO:
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            title=data["title"],
            objective=data["objective"],
            status=data["status"],
            priority=data.get("priority", 0),
            risk=data.get("risk", "low"),
            assigned_worker_id=data.get("assigned_worker_id"),
            assigned_worker_name=data.get("assigned_worker_name"),
            dependency_ids=data.get("dependency_ids", []),
            artifact_ids=data.get("artifact_ids", []),
            attempts=data.get("attempts", 0),
            max_attempts=data.get("max_attempts", 3),
            created_at=data["created_at"],
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at")
        )

    @classmethod
    def from_domain(cls, task: Task, worker_name: Optional[str] = None) -> TaskDTO:
        return cls(
            id=task.id,
            project_id=task.project_id,
            title=task.title,
            objective=task.objective,
            status=task.status.value if hasattr(task.status, 'value') else str(task.status),
            priority=task.priority,
            risk=task.risk.value if hasattr(task.risk, 'value') else str(task.risk),
            assigned_worker_id=task.assigned_worker_id,
            assigned_worker_name=worker_name,
            dependency_ids=task.dependency_ids,
            artifact_ids=task.artifact_ids,
            attempts=task.attempts,
            max_attempts=task.max_attempts,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at
        )

@dataclass
class TaskCreateRequest:
    project_id: str
    title: str
    objective: str
    priority: int
    risk: str
    dependencies: List[str]
    max_attempts: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "title": self.title,
            "objective": self.objective,
            "priority": self.priority,
            "risk": self.risk,
            "dependencies": self.dependencies,
            "max_attempts": self.max_attempts
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskCreateRequest:
        return cls(
            project_id=data["project_id"],
            title=data["title"],
            objective=data["objective"],
            priority=data.get("priority", 0),
            risk=data.get("risk", "low"),
            dependencies=data.get("dependencies", []),
            max_attempts=data.get("max_attempts", 3)
        )

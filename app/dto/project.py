from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from core.models import Project

@dataclass
class ProjectSummaryDTO:
    id: str
    name: str
    status: str
    task_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "task_count": self.task_count
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ProjectSummaryDTO:
        return cls(
            id=data["id"],
            name=data["name"],
            status=data["status"],
            task_count=data.get("task_count", 0)
        )

@dataclass
class ProjectDTO:
    id: str
    name: str
    description: str
    root_path: str
    status: str
    task_count: int
    worker_count: int
    created_at: str
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "root_path": self.root_path,
            "status": self.status,
            "task_count": self.task_count,
            "worker_count": self.worker_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ProjectDTO:
        return cls(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            root_path=data["root_path"],
            status=data["status"],
            task_count=data.get("task_count", 0),
            worker_count=data.get("worker_count", 0),
            created_at=data["created_at"],
            updated_at=data["updated_at"]
        )

    @classmethod
    def from_domain(cls, project: Project, task_count: int = 0, worker_count: int = 0) -> ProjectDTO:
        return cls(
            id=project.id,
            name=project.name,
            description=project.description,
            root_path=project.root_path,
            status=project.status.value if hasattr(project.status, 'value') else str(project.status),
            task_count=task_count,
            worker_count=worker_count,
            created_at=project.created_at,
            updated_at=project.updated_at
        )

@dataclass
class ProjectCreateRequest:
    name: str
    root_path: str
    description: Optional[str] = None
    configuration: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "root_path": self.root_path,
            "description": self.description,
            "configuration": self.configuration
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ProjectCreateRequest:
        return cls(
            name=data["name"],
            root_path=data["root_path"],
            description=data.get("description"),
            configuration=data.get("configuration")
        )

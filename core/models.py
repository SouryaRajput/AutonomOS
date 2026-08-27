from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.enums import (
    ArtifactType,
    DependencyType,
    ProjectStatus,
    RiskLevel,
    TaskStatus,
    WorkerStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    """Generate UUIDv4 string."""
    return str(uuid.uuid4())


@dataclass
class Project:
    """Root workspace boundary representing a project."""
    id: str
    name: str
    description: str
    root_path: str
    status: ProjectStatus = ProjectStatus.ACTIVE
    configuration: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "root_path": self.root_path,
            "status": self.status.value if isinstance(self.status, ProjectStatus) else self.status,
            "configuration": self.configuration,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            root_path=data["root_path"],
            status=ProjectStatus(data["status"]) if isinstance(data["status"], str) else data["status"],
            configuration=data.get("configuration", {}),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
        )


@dataclass
class WorkerManifest:
    """Static metadata, capabilities, permissions, and policies for a worker."""
    id: str
    name: str
    role: str
    description: str
    version: str = "1.0.0"
    capabilities: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    model_policy: dict[str, Any] = field(default_factory=dict)
    status: WorkerStatus = WorkerStatus.REGISTERED
    active_task_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "description": self.description,
            "version": self.version,
            "capabilities": self.capabilities,
            "permissions": self.permissions,
            "tools": self.tools,
            "model_policy": self.model_policy,
            "status": self.status.value if isinstance(self.status, WorkerStatus) else self.status,
            "active_task_id": self.active_task_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkerManifest":
        return cls(
            id=data["id"],
            name=data["name"],
            role=data["role"],
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            capabilities=list(data.get("capabilities", [])),
            permissions=list(data.get("permissions", [])),
            tools=list(data.get("tools", [])),
            model_policy=dict(data.get("model_policy", {})),
            status=WorkerStatus(data["status"]) if isinstance(data["status"], str) else data["status"],
            active_task_id=data.get("active_task_id"),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
        )


@dataclass
class Task:
    """Discrete unit of work within a project."""
    id: str
    project_id: str
    title: str
    objective: str
    parent_task_id: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 1
    risk: RiskLevel = RiskLevel.LOW
    assigned_worker: Optional[str] = None
    dependencies: list[str] = field(default_factory=list)  # list of prerequisite task IDs
    success_criteria: list[dict[str, Any]] = field(default_factory=list)
    context_references: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)  # list of artifact IDs
    attempts: int = 0
    max_attempts: int = 3
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "objective": self.objective,
            "parent_task_id": self.parent_task_id,
            "status": self.status.value if isinstance(self.status, TaskStatus) else self.status,
            "priority": self.priority,
            "risk": self.risk.value if isinstance(self.risk, RiskLevel) else self.risk,
            "assigned_worker": self.assigned_worker,
            "dependencies": self.dependencies,
            "success_criteria": self.success_criteria,
            "context_references": self.context_references,
            "artifacts": self.artifacts,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            title=data["title"],
            objective=data.get("objective", ""),
            parent_task_id=data.get("parent_task_id"),
            status=TaskStatus(data["status"]) if isinstance(data["status"], str) else data["status"],
            priority=data.get("priority", 1),
            risk=RiskLevel(data.get("risk", RiskLevel.LOW.value)) if isinstance(data.get("risk"), str) else data.get("risk", RiskLevel.LOW),
            assigned_worker=data.get("assigned_worker"),
            dependencies=list(data.get("dependencies", [])),
            success_criteria=list(data.get("success_criteria", [])),
            context_references=list(data.get("context_references", [])),
            artifacts=list(data.get("artifacts", [])),
            attempts=data.get("attempts", 0),
            max_attempts=data.get("max_attempts", 3),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
        )


@dataclass
class Dependency:
    """Directed dependency relation between two tasks."""
    id: str
    dependent_task_id: str      # Task that is blocked
    prerequisite_task_id: str   # Task that must be completed first
    dependency_type: DependencyType = DependencyType.STRICT_SUCCESS
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "dependent_task_id": self.dependent_task_id,
            "prerequisite_task_id": self.prerequisite_task_id,
            "dependency_type": self.dependency_type.value if isinstance(self.dependency_type, DependencyType) else self.dependency_type,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Dependency":
        return cls(
            id=data["id"],
            dependent_task_id=data["dependent_task_id"],
            prerequisite_task_id=data["prerequisite_task_id"],
            dependency_type=DependencyType(data.get("dependency_type", DependencyType.STRICT_SUCCESS.value)),
            created_at=data.get("created_at", utc_now()),
        )


@dataclass
class Artifact:
    """Tangible result or file produced during task execution."""
    id: str
    project_id: str
    task_id: str
    worker_id: str
    type: ArtifactType
    path: str
    description: str
    checksum: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "type": self.type.value if isinstance(self.type, ArtifactType) else self.type,
            "path": self.path,
            "description": self.description,
            "checksum": self.checksum,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Artifact":
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            task_id=data["task_id"],
            worker_id=data["worker_id"],
            type=ArtifactType(data["type"]) if isinstance(data["type"], str) else data["type"],
            path=data["path"],
            description=data.get("description", ""),
            checksum=data.get("checksum"),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", utc_now()),
        )


@dataclass
class Evidence:
    """Concrete proof validating a task claim (e.g. exit codes, diffs, outputs)."""
    id: str
    task_id: str
    evidence_type: str
    data: str
    checksum: Optional[str] = None
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "evidence_type": self.evidence_type,
            "data": self.data,
            "checksum": self.checksum,
            "created_at": self.created_at,
        }


@dataclass
class WorkerOutput:
    """Standard output packet returned by a worker after execution."""
    success: bool
    summary: str
    report_markdown: str = ""
    created_artifacts: list[dict[str, Any]] = field(default_factory=list)  # list of artifact descriptors to register
    evidence_list: list[Evidence] = field(default_factory=list)
    error_message: Optional[str] = None

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any, Optional

from core.workflow.types import (
    ApprovalStatus,
    HandoffType,
    ReassignmentReason,
    WorkflowPriority,
    WorkflowStatus,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkflowBudget:
    """Execution bounds and cost limits for a multi-worker workflow."""
    max_iterations: int = 10
    max_test_commands: int = 50
    max_cost: float = 5.0
    max_wall_time_s: int = 600
    max_consecutive_failures: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_iterations": self.max_iterations,
            "max_test_commands": self.max_test_commands,
            "max_cost": self.max_cost,
            "max_wall_time_s": self.max_wall_time_s,
            "max_consecutive_failures": self.max_consecutive_failures,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowBudget:
        return cls(
            max_iterations=int(data.get("max_iterations", 10)),
            max_test_commands=int(data.get("max_test_commands", 50)),
            max_cost=float(data.get("max_cost", 5.0)),
            max_wall_time_s=int(data.get("max_wall_time_s", 600)),
            max_consecutive_failures=int(data.get("max_consecutive_failures", 3)),
        )


@dataclass
class WorkerHandoff:
    """
    Structured work product handoff between tasks and specialist workers.
    Contains compressed summaries, artifact IDs, and evidence IDs without duplicating raw content.
    """
    id: str
    project_id: str
    source_worker: str
    source_task_id: str
    destination_worker: Optional[str] = None
    destination_task_id: Optional[str] = None
    handoff_type: HandoffType = HandoffType.GENERAL
    artifacts: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    summary: str = ""
    requirements: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    workflow_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "source_worker": self.source_worker,
            "source_task_id": self.source_task_id,
            "destination_worker": self.destination_worker,
            "destination_task_id": self.destination_task_id,
            "handoff_type": self.handoff_type.value if isinstance(self.handoff_type, HandoffType) else self.handoff_type,
            "artifacts": self.artifacts,
            "evidence": self.evidence,
            "summary": self.summary,
            "requirements": self.requirements,
            "warnings": self.warnings,
            "workflow_id": self.workflow_id,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerHandoff:
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            source_worker=data.get("source_worker", ""),
            source_task_id=data.get("source_task_id", ""),
            destination_worker=data.get("destination_worker"),
            destination_task_id=data.get("destination_task_id"),
            handoff_type=HandoffType(data.get("handoff_type", "GENERAL")),
            artifacts=list(data.get("artifacts", [])),
            evidence=list(data.get("evidence", [])),
            summary=data.get("summary", ""),
            requirements=list(data.get("requirements", [])),
            warnings=list(data.get("warnings", [])),
            workflow_id=data.get("workflow_id"),
            created_at=data.get("created_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ExecutionAttempt:
    """Historical record of an individual worker task execution pass."""
    id: str
    task_id: str
    attempt_number: int
    worker_id: str
    worker_version: str
    status: str
    workflow_id: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: float = 0.0
    started_at: str = field(default_factory=utc_now)
    completed_at: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "attempt_number": self.attempt_number,
            "worker_id": self.worker_id,
            "worker_version": self.worker_version,
            "status": self.status,
            "workflow_id": self.workflow_id,
            "error_message": self.error_message,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionAttempt:
        return cls(
            id=data["id"],
            task_id=data["task_id"],
            attempt_number=int(data.get("attempt_number", 1)),
            worker_id=data.get("worker_id", ""),
            worker_version=data.get("worker_version", "1.0.0"),
            status=data.get("status", "SUCCESS"),
            workflow_id=data.get("workflow_id"),
            error_message=data.get("error_message"),
            duration_ms=float(data.get("duration_ms", 0.0)),
            started_at=data.get("started_at", utc_now()),
            completed_at=data.get("completed_at"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class DefectLinkage:
    """Tracks the lifecycle of a diagnosed defect across the workforce."""
    defect_id: str
    originating_task_id: str
    originating_worker_id: str
    fix_task_id: Optional[str] = None
    fix_worker_id: Optional[str] = None
    retest_task_id: Optional[str] = None
    retest_worker_id: Optional[str] = None
    status: str = "OPEN"  # OPEN, IN_PROGRESS, RESOLVED, VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "defect_id": self.defect_id,
            "originating_task_id": self.originating_task_id,
            "originating_worker_id": self.originating_worker_id,
            "fix_task_id": self.fix_task_id,
            "fix_worker_id": self.fix_worker_id,
            "retest_task_id": self.retest_task_id,
            "retest_worker_id": self.retest_worker_id,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DefectLinkage:
        return cls(
            defect_id=data["defect_id"],
            originating_task_id=data.get("originating_task_id", ""),
            originating_worker_id=data.get("originating_worker_id", ""),
            fix_task_id=data.get("fix_task_id"),
            fix_worker_id=data.get("fix_worker_id"),
            retest_task_id=data.get("retest_task_id"),
            retest_worker_id=data.get("retest_worker_id"),
            status=data.get("status", "OPEN"),
        )


@dataclass
class WorkforceWorkflow:
    """The central multi-worker execution unit in AutonomOS."""
    id: str
    project_id: str
    title: str
    objective: str
    root_task_id: Optional[str] = None
    status: WorkflowStatus = WorkflowStatus.CREATED
    priority: WorkflowPriority = WorkflowPriority.NORMAL
    tasks: list[str] = field(default_factory=list)
    current_step: int = 0
    budget: WorkflowBudget = field(default_factory=WorkflowBudget)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    completed_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "objective": self.objective,
            "root_task_id": self.root_task_id,
            "status": self.status.value if isinstance(self.status, WorkflowStatus) else self.status,
            "priority": self.priority.value if isinstance(self.priority, WorkflowPriority) else self.priority,
            "tasks": self.tasks,
            "current_step": self.current_step,
            "budget": self.budget.to_dict(),
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkforceWorkflow:
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            title=data.get("title", ""),
            objective=data.get("objective", ""),
            root_task_id=data.get("root_task_id"),
            status=WorkflowStatus(data.get("status", "CREATED")),
            priority=WorkflowPriority(data.get("priority", "NORMAL")),
            tasks=list(data.get("tasks", [])),
            current_step=int(data.get("current_step", 0)),
            budget=WorkflowBudget.from_dict(data.get("budget", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
            completed_at=data.get("completed_at"),
        )


@dataclass
class WorkflowSnapshot:
    """Compact structured state snapshot for Manager context retrieval."""
    workflow_id: str
    project_id: str
    title: str
    status: WorkflowStatus
    active_tasks: list[str] = field(default_factory=list)
    completed_tasks: list[str] = field(default_factory=list)
    blocked_tasks: list[str] = field(default_factory=list)
    failed_tasks: list[str] = field(default_factory=list)
    worker_assignments: dict[str, str] = field(default_factory=dict)
    recent_handoffs: list[WorkerHandoff] = field(default_factory=list)
    open_defects: list[DefectLinkage] = field(default_factory=list)
    iteration_count: int = 0
    budget_used: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "project_id": self.project_id,
            "title": self.title,
            "status": self.status.value if isinstance(self.status, WorkflowStatus) else self.status,
            "active_tasks": self.active_tasks,
            "completed_tasks": self.completed_tasks,
            "blocked_tasks": self.blocked_tasks,
            "failed_tasks": self.failed_tasks,
            "worker_assignments": self.worker_assignments,
            "recent_handoffs": [h.to_dict() for h in self.recent_handoffs],
            "open_defects": [d.to_dict() for d in self.open_defects],
            "iteration_count": self.iteration_count,
            "budget_used": self.budget_used,
        }

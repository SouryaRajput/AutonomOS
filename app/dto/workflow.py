from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from core.workflow.model import WorkforceWorkflow, WorkerHandoff

@dataclass
class WorkflowDTO:
    id: str
    project_id: str
    title: str
    status: str
    task_ids: List[str]
    current_step: Optional[int]
    iterations: int
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "status": self.status,
            "task_ids": self.task_ids,
            "current_step": self.current_step,
            "iterations": self.iterations,
            "created_at": self.created_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WorkflowDTO:
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            title=data["title"],
            status=data["status"],
            task_ids=data.get("task_ids", []),
            current_step=data.get("current_step"),
            iterations=data.get("iterations", 0),
            created_at=data["created_at"]
        )

    @classmethod
    def from_domain(cls, workflow: WorkforceWorkflow) -> WorkflowDTO:
        return cls(
            id=workflow.id,
            project_id=workflow.project_id,
            title=workflow.title,
            status=workflow.status.value if hasattr(workflow.status, 'value') else str(workflow.status),
            task_ids=workflow.task_ids,
            current_step=workflow.current_step,
            iterations=workflow.iterations,
            created_at=workflow.created_at
        )

@dataclass
class HandoffDTO:
    id: str
    source_worker: str
    destination_worker: str
    summary: str
    handoff_type: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_worker": self.source_worker,
            "destination_worker": self.destination_worker,
            "summary": self.summary,
            "handoff_type": self.handoff_type,
            "created_at": self.created_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> HandoffDTO:
        return cls(
            id=data["id"],
            source_worker=data["source_worker"],
            destination_worker=data["destination_worker"],
            summary=data["summary"],
            handoff_type=data["handoff_type"],
            created_at=data["created_at"]
        )

    @classmethod
    def from_domain(cls, handoff: WorkerHandoff) -> HandoffDTO:
        return cls(
            id=handoff.id,
            source_worker=handoff.source_worker,
            destination_worker=handoff.destination_worker,
            summary=handoff.summary,
            handoff_type=handoff.handoff_type.value if hasattr(handoff.handoff_type, 'value') else str(handoff.handoff_type),
            created_at=handoff.created_at
        )

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from core.manager.model import ManagerStatus, Plan, CycleResult

@dataclass
class ManagerStatusDTO:
    project_id: str
    is_active: bool
    current_activity: str
    active_plan_id: Optional[str]
    pending_user_input: bool
    stagnation_detected: bool
    total_cycles: int
    total_cost: float
    summary_text: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "is_active": self.is_active,
            "current_activity": self.current_activity,
            "active_plan_id": self.active_plan_id,
            "pending_user_input": self.pending_user_input,
            "stagnation_detected": self.stagnation_detected,
            "total_cycles": self.total_cycles,
            "total_cost": self.total_cost,
            "summary_text": self.summary_text
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ManagerStatusDTO:
        return cls(
            project_id=data["project_id"],
            is_active=data.get("is_active", False),
            current_activity=data.get("current_activity", ""),
            active_plan_id=data.get("active_plan_id"),
            pending_user_input=data.get("pending_user_input", False),
            stagnation_detected=data.get("stagnation_detected", False),
            total_cycles=data.get("total_cycles", 0),
            total_cost=data.get("total_cost", 0.0),
            summary_text=data.get("summary_text", "")
        )

    @classmethod
    def from_domain(cls, status: ManagerStatus) -> ManagerStatusDTO:
        return cls(
            project_id=status.project_id,
            is_active=status.is_active,
            current_activity=status.current_activity,
            active_plan_id=status.active_plan_id,
            pending_user_input=status.pending_user_input,
            stagnation_detected=status.stagnation_detected,
            total_cycles=status.total_cycles,
            total_cost=status.total_cost,
            summary_text=status.summary_text
        )

@dataclass
class PlanDTO:
    id: str
    project_id: str
    objective: str
    milestones: List[str]
    status: str
    version: int
    task_summaries: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "objective": self.objective,
            "milestones": self.milestones,
            "status": self.status,
            "version": self.version,
            "task_summaries": self.task_summaries
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PlanDTO:
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            objective=data["objective"],
            milestones=data.get("milestones", []),
            status=data["status"],
            version=data.get("version", 1),
            task_summaries=data.get("task_summaries", [])
        )

    @classmethod
    def from_domain(cls, plan: Plan) -> PlanDTO:
        return cls(
            id=plan.id,
            project_id=plan.project_id,
            objective=plan.objective,
            milestones=plan.milestones,
            status=plan.status.value if hasattr(plan.status, 'value') else str(plan.status),
            version=plan.version,
            task_summaries=plan.task_summaries
        )

@dataclass
class CycleResultDTO:
    cycle_id: str
    progress_detected: bool
    completed: bool
    waiting: bool
    user_input_required: bool
    status_summary: str
    actions_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "progress_detected": self.progress_detected,
            "completed": self.completed,
            "waiting": self.waiting,
            "user_input_required": self.user_input_required,
            "status_summary": self.status_summary,
            "actions_count": self.actions_count
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CycleResultDTO:
        return cls(
            cycle_id=data["cycle_id"],
            progress_detected=data.get("progress_detected", False),
            completed=data.get("completed", False),
            waiting=data.get("waiting", False),
            user_input_required=data.get("user_input_required", False),
            status_summary=data.get("status_summary", ""),
            actions_count=data.get("actions_count", 0)
        )

    @classmethod
    def from_domain(cls, result: CycleResult) -> CycleResultDTO:
        return cls(
            cycle_id=result.cycle_id,
            progress_detected=result.progress_detected,
            completed=result.completed,
            waiting=result.waiting,
            user_input_required=result.user_input_required,
            status_summary=result.status_summary,
            actions_count=len(result.actions) if hasattr(result, 'actions') and result.actions else 0
        )

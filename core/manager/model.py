from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import uuid

from core.context.model import ContextBudget
from core.enums import RiskLevel, TaskStatus, WorkerStatus
from core.events.model import Event, utc_now
from core.inference.model import ModelRequirement
from core.inference.types import RoutingProfile
from core.manager.types import (
    AutonomyLevel,
    ConfidenceLevel,
    ManagerActionType,
    PlanStatus,
)
from core.models import Artifact, Project, Task, WorkerManifest


@dataclass
class ManagerAction:
    """A single discrete action proposed by the Manager Agent to the Controller."""
    action_type: ManagerActionType
    parameters: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type.value if hasattr(self.action_type, "value") else str(self.action_type),
            "parameters": self.parameters,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ManagerAction:
        atype_raw = data.get("action_type") or data.get("action")
        try:
            atype = ManagerActionType(atype_raw)
        except (ValueError, TypeError):
            atype = atype_raw
        return cls(
            action_type=atype,
            parameters=dict(data.get("parameters", {})),
            rationale=str(data.get("rationale", "")),
        )


@dataclass
class ManagerDecision:
    """Machine-validatable decision package produced by the Manager Agent."""
    decision_id: str
    cycle_id: str
    project_id: str
    reasoning_summary: str
    actions: list[ManagerAction] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    confidence_level: ConfidenceLevel = ConfidenceLevel.CERTAIN
    plan_update: Optional[dict[str, Any]] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "cycle_id": self.cycle_id,
            "project_id": self.project_id,
            "reasoning_summary": self.reasoning_summary,
            "actions": [a.to_dict() for a in self.actions],
            "assumptions": self.assumptions,
            "risks": self.risks,
            "confidence_level": self.confidence_level.value if isinstance(self.confidence_level, ConfidenceLevel) else self.confidence_level,
            "plan_update": self.plan_update,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ManagerDecision:
        conf_raw = data.get("confidence_level", ConfidenceLevel.CERTAIN.value)
        try:
            conf = ConfidenceLevel(conf_raw)
        except ValueError:
            conf = ConfidenceLevel.CERTAIN

        actions = [
            ManagerAction.from_dict(a) if isinstance(a, dict) else a
            for a in data.get("actions", [])
        ]

        return cls(
            decision_id=data["decision_id"],
            cycle_id=data.get("cycle_id", f"cycle-{uuid.uuid4().hex[:6]}"),
            project_id=data["project_id"],
            reasoning_summary=data.get("reasoning_summary", ""),
            actions=actions,
            assumptions=list(data.get("assumptions", [])),
            risks=list(data.get("risks", [])),
            confidence_level=conf,
            plan_update=data.get("plan_update"),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )


@dataclass
class Plan:
    """Versioned project execution plan generated and adapted by the Manager."""
    id: str
    project_id: str
    objective: str
    tasks: list[dict[str, Any]] = field(default_factory=list)
    milestones: list[str] = field(default_factory=list)
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    status: PlanStatus = PlanStatus.ACTIVE
    version: int = 1
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "objective": self.objective,
            "tasks": self.tasks,
            "milestones": self.milestones,
            "dependencies": self.dependencies,
            "status": self.status.value if isinstance(self.status, PlanStatus) else self.status,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        status_raw = data.get("status", PlanStatus.ACTIVE.value)
        try:
            status = PlanStatus(status_raw)
        except ValueError:
            status = PlanStatus.ACTIVE

        return cls(
            id=data["id"],
            project_id=data["project_id"],
            objective=data.get("objective", ""),
            tasks=list(data.get("tasks", [])),
            milestones=list(data.get("milestones", [])),
            dependencies=dict(data.get("dependencies", {})),
            status=status,
            version=int(data.get("version", 1)),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ManagerState:
    """Structured, bounded operational state gathered from the deterministic runtime."""
    project_id: str
    objective: str
    current_plan: Optional[Plan]
    tasks: list[Task] = field(default_factory=list)
    workers: list[WorkerManifest] = field(default_factory=list)
    active_task_ids: list[str] = field(default_factory=list)
    completed_task_ids: list[str] = field(default_factory=list)
    failed_task_ids: list[str] = field(default_factory=list)
    blocked_task_ids: list[str] = field(default_factory=list)
    recent_events: list[Event] = field(default_factory=list)
    recent_artifacts: list[Artifact] = field(default_factory=list)
    verification_summaries: list[dict[str, Any]] = field(default_factory=list)
    unresolved_issues: list[dict[str, Any]] = field(default_factory=list)
    pending_user_questions: list[dict[str, Any]] = field(default_factory=list)
    cycle_count: int = 0
    consecutive_idle_cycles: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "objective": self.objective,
            "current_plan": self.current_plan.to_dict() if self.current_plan else None,
            "tasks_count": len(self.tasks),
            "active_tasks": self.active_task_ids,
            "completed_tasks": self.completed_task_ids,
            "failed_tasks": self.failed_task_ids,
            "blocked_tasks": self.blocked_task_ids,
            "workers": [w.to_dict() for w in self.workers],
            "recent_events_count": len(self.recent_events),
            "recent_artifacts": [a.to_dict() for a in self.recent_artifacts],
            "verification_summaries": self.verification_summaries,
            "unresolved_issues": self.unresolved_issues,
            "pending_user_questions": self.pending_user_questions,
            "cycle_count": self.cycle_count,
            "consecutive_idle_cycles": self.consecutive_idle_cycles,
        }


@dataclass
class ManagerConfig:
    """Execution parameters and guardrails governing Manager Agent behavior."""
    autonomy_level: AutonomyLevel = AutonomyLevel.AUTONOMOUS
    max_actions_per_cycle: int = 10
    max_cycles_without_progress: int = 5
    retry_budget_per_task: int = 3
    max_replan_count: int = 5
    max_cost_per_project: float = 10.0
    context_budget: ContextBudget = field(default_factory=lambda: ContextBudget(max_tokens=6000))
    model_requirements: ModelRequirement = field(default_factory=ModelRequirement)
    routing_profile: RoutingProfile = RoutingProfile.BEST_AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "autonomy_level": self.autonomy_level.value,
            "max_actions_per_cycle": self.max_actions_per_cycle,
            "max_cycles_without_progress": self.max_cycles_without_progress,
            "retry_budget_per_task": self.retry_budget_per_task,
            "max_replan_count": self.max_replan_count,
            "max_cost_per_project": self.max_cost_per_project,
            "context_budget": self.context_budget.to_dict(),
            "model_requirements": self.model_requirements.to_dict(),
            "routing_profile": self.routing_profile.value,
        }


@dataclass
class ManagerStatus:
    """Read-only operational status representation of the Manager Orchestrator."""
    project_id: str
    is_active: bool
    current_activity: str
    active_plan_id: Optional[str] = None
    current_cycle_id: Optional[str] = None
    last_decision_id: Optional[str] = None
    pending_user_input: bool = False
    stagnation_detected: bool = False
    total_cycles: int = 0
    total_actions_executed: int = 0
    total_cost: float = 0.0
    summary_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "is_active": self.is_active,
            "current_activity": self.current_activity,
            "active_plan_id": self.active_plan_id,
            "current_cycle_id": self.current_cycle_id,
            "last_decision_id": self.last_decision_id,
            "pending_user_input": self.pending_user_input,
            "stagnation_detected": self.stagnation_detected,
            "total_cycles": self.total_cycles,
            "total_actions_executed": self.total_actions_executed,
            "total_cost": round(self.total_cost, 4),
            "summary_text": self.summary_text,
        }


@dataclass
class ActionResult:
    """Deterministic validation and execution result of a single proposed ManagerAction."""
    action: ManagerAction
    accepted: bool
    reason: str = ""
    execution_output: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.to_dict(),
            "accepted": self.accepted,
            "reason": self.reason,
            "execution_output": self.execution_output,
        }


@dataclass
class CycleResult:
    """Comprehensive summary of a single execution cycle of the Manager Controller."""
    cycle_id: str
    decision: Optional[ManagerDecision]
    results: list[ActionResult] = field(default_factory=list)
    progress_detected: bool = False
    completed: bool = False
    waiting: bool = False
    user_input_required: bool = False
    escalated: bool = False
    status_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "decision": self.decision.to_dict() if self.decision else None,
            "results": [r.to_dict() for r in self.results],
            "progress_detected": self.progress_detected,
            "completed": self.completed,
            "waiting": self.waiting,
            "user_input_required": self.user_input_required,
            "escalated": self.escalated,
            "status_summary": self.status_summary,
        }

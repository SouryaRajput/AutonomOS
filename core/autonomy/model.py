from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.enums import RiskLevel
from core.autonomy.types import (
    ActionCategory,
    ApprovalRequestStatus,
    AutonomyLevel,
    DecisionRequestStatus,
    PolicyDecisionResult,
    UserInputStatus,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AutonomyPolicy:
    """Configurable project or system-level policy governing autonomous execution."""
    id: str
    project_id: str
    autonomy_level: AutonomyLevel = AutonomyLevel.BALANCED
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    approval_required_actions: list[ActionCategory] = field(default_factory=list)
    max_cost_limit: float = 10.0
    max_iterations: int = 15
    max_external_actions_per_hour: int = 10
    version: int = 1
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "autonomy_level": self.autonomy_level.value if isinstance(self.autonomy_level, AutonomyLevel) else self.autonomy_level,
            "allowed_tools": self.allowed_tools,
            "denied_tools": self.denied_tools,
            "approval_required_actions": [
                a.value if isinstance(a, ActionCategory) else a for a in self.approval_required_actions
            ],
            "max_cost_limit": self.max_cost_limit,
            "max_iterations": self.max_iterations,
            "max_external_actions_per_hour": self.max_external_actions_per_hour,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AutonomyPolicy:
        acts = [ActionCategory(a) for a in data.get("approval_required_actions", [])]
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            autonomy_level=AutonomyLevel(data.get("autonomy_level", "BALANCED")),
            allowed_tools=list(data.get("allowed_tools", [])),
            denied_tools=list(data.get("denied_tools", [])),
            approval_required_actions=acts,
            max_cost_limit=float(data.get("max_cost_limit", 10.0)),
            max_iterations=int(data.get("max_iterations", 15)),
            max_external_actions_per_hour=int(data.get("max_external_actions_per_hour", 10)),
            version=int(data.get("version", 1)),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class PolicyExplanation:
    """Structured rationale explaining a policy evaluation decision without exposing chain-of-thought."""
    decision: PolicyDecisionResult
    risk_level: RiskLevel
    matched_rules: list[str] = field(default_factory=list)
    rejected_rules: list[str] = field(default_factory=list)
    required_approval_scope: str = ""
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value if isinstance(self.decision, PolicyDecisionResult) else self.decision,
            "risk_level": self.risk_level.value if isinstance(self.risk_level, RiskLevel) else self.risk_level,
            "matched_rules": self.matched_rules,
            "rejected_rules": self.rejected_rules,
            "required_approval_scope": self.required_approval_scope,
            "summary": self.summary,
        }


@dataclass
class PolicyDecision:
    """Deterministic evaluation outcome for an action request."""
    id: str
    action: str
    category: ActionCategory
    result: PolicyDecisionResult
    risk_level: RiskLevel
    policy_id: str
    reason: str
    explanation: PolicyExplanation
    timestamp: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "category": self.category.value if isinstance(self.category, ActionCategory) else self.category,
            "result": self.result.value if isinstance(self.result, PolicyDecisionResult) else self.result,
            "risk_level": self.risk_level.value if isinstance(self.risk_level, RiskLevel) else self.risk_level,
            "policy_id": self.policy_id,
            "reason": self.reason,
            "explanation": self.explanation.to_dict(),
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class ApprovalRequest:
    """Scoped, auditable human authorization request."""
    id: str
    project_id: str
    workflow_id: Optional[str]
    task_id: str
    worker_id: str
    action: str
    category: ActionCategory
    risk_level: RiskLevel
    reason: str
    requested_scope: str
    affected_resources: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    status: ApprovalRequestStatus = ApprovalRequestStatus.PENDING
    created_at: str = field(default_factory=utc_now)
    expires_at: Optional[str] = None
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "workflow_id": self.workflow_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "action": self.action,
            "category": self.category.value if isinstance(self.category, ActionCategory) else self.category,
            "risk_level": self.risk_level.value if isinstance(self.risk_level, RiskLevel) else self.risk_level,
            "reason": self.reason,
            "requested_scope": self.requested_scope,
            "affected_resources": self.affected_resources,
            "evidence": self.evidence,
            "status": self.status.value if isinstance(self.status, ApprovalRequestStatus) else self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "rejection_reason": self.rejection_reason,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApprovalRequest:
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            workflow_id=data.get("workflow_id"),
            task_id=data["task_id"],
            worker_id=data["worker_id"],
            action=data["action"],
            category=ActionCategory(data.get("category", "MODIFY")),
            risk_level=RiskLevel(data.get("risk_level", "HIGH")),
            reason=data.get("reason", ""),
            requested_scope=data.get("requested_scope", ""),
            affected_resources=list(data.get("affected_resources", [])),
            evidence=list(data.get("evidence", [])),
            status=ApprovalRequestStatus(data.get("status", "PENDING")),
            created_at=data.get("created_at", utc_now()),
            expires_at=data.get("expires_at"),
            decided_at=data.get("decided_at"),
            decided_by=data.get("decided_by"),
            rejection_reason=data.get("rejection_reason"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class UserInputRequest:
    """User clarification or question request."""
    id: str
    project_id: str
    workflow_id: Optional[str]
    task_id: str
    question: str
    context: str = ""
    answer: Optional[str] = None
    status: UserInputStatus = UserInputStatus.PENDING
    created_at: str = field(default_factory=utc_now)
    answered_at: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "workflow_id": self.workflow_id,
            "task_id": self.task_id,
            "question": self.question,
            "context": self.context,
            "answer": self.answer,
            "status": self.status.value if isinstance(self.status, UserInputStatus) else self.status,
            "created_at": self.created_at,
            "answered_at": self.answered_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserInputRequest:
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            workflow_id=data.get("workflow_id"),
            task_id=data["task_id"],
            question=data.get("question", ""),
            context=data.get("context", ""),
            answer=data.get("answer"),
            status=UserInputStatus(data.get("status", "PENDING")),
            created_at=data.get("created_at", utc_now()),
            answered_at=data.get("answered_at"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class DecisionRequest:
    """Structured architectural or product decision request."""
    id: str
    project_id: str
    workflow_id: Optional[str]
    task_id: str
    title: str
    options: list[str] = field(default_factory=list)
    chosen_option: Optional[str] = None
    rationale: str = ""
    status: DecisionRequestStatus = DecisionRequestStatus.PENDING
    created_at: str = field(default_factory=utc_now)
    decided_at: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "workflow_id": self.workflow_id,
            "task_id": self.task_id,
            "title": self.title,
            "options": self.options,
            "chosen_option": self.chosen_option,
            "rationale": self.rationale,
            "status": self.status.value if isinstance(self.status, DecisionRequestStatus) else self.status,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionRequest:
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            workflow_id=data.get("workflow_id"),
            task_id=data["task_id"],
            title=data.get("title", ""),
            options=list(data.get("options", [])),
            chosen_option=data.get("chosen_option"),
            rationale=data.get("rationale", ""),
            status=DecisionRequestStatus(data.get("status", "PENDING")),
            created_at=data.get("created_at", utc_now()),
            decided_at=data.get("decided_at"),
            metadata=dict(data.get("metadata", {})),
        )

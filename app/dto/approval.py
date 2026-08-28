from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from core.autonomy.model import ApprovalRequest, UserInputRequest, DecisionRequest

@dataclass
class ApprovalRequestDTO:
    id: str
    project_id: str
    task_id: Optional[str]
    worker_id: Optional[str]
    action: str
    risk_level: str
    status: str
    reason: str
    created_at: str
    expires_at: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "action": self.action,
            "risk_level": self.risk_level,
            "status": self.status,
            "reason": self.reason,
            "created_at": self.created_at,
            "expires_at": self.expires_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ApprovalRequestDTO:
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            task_id=data.get("task_id"),
            worker_id=data.get("worker_id"),
            action=data["action"],
            risk_level=data["risk_level"],
            status=data["status"],
            reason=data["reason"],
            created_at=data["created_at"],
            expires_at=data.get("expires_at")
        )

    @classmethod
    def from_domain(cls, req: ApprovalRequest) -> ApprovalRequestDTO:
        return cls(
            id=req.id,
            project_id=req.project_id,
            task_id=req.task_id,
            worker_id=req.worker_id,
            action=req.action,
            risk_level=req.risk_level.value if hasattr(req.risk_level, 'value') else str(req.risk_level),
            status=req.status.value if hasattr(req.status, 'value') else str(req.status),
            reason=req.reason,
            created_at=req.created_at,
            expires_at=req.expires_at
        )

@dataclass
class UserInputRequestDTO:
    id: str
    project_id: str
    task_id: Optional[str]
    question: str
    context: str
    status: str
    answer: Optional[str]
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "question": self.question,
            "context": self.context,
            "status": self.status,
            "answer": self.answer,
            "created_at": self.created_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> UserInputRequestDTO:
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            task_id=data.get("task_id"),
            question=data["question"],
            context=data.get("context", ""),
            status=data["status"],
            answer=data.get("answer"),
            created_at=data["created_at"]
        )

    @classmethod
    def from_domain(cls, req: UserInputRequest) -> UserInputRequestDTO:
        return cls(
            id=req.id,
            project_id=req.project_id,
            task_id=req.task_id,
            question=req.question,
            context=req.context,
            status=req.status.value if hasattr(req.status, 'value') else str(req.status),
            answer=req.answer,
            created_at=req.created_at
        )

@dataclass
class DecisionRequestDTO:
    id: str
    project_id: str
    task_id: Optional[str]
    title: str
    options: List[str]
    status: str
    chosen_option: Optional[str]
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "title": self.title,
            "options": self.options,
            "status": self.status,
            "chosen_option": self.chosen_option,
            "created_at": self.created_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DecisionRequestDTO:
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            task_id=data.get("task_id"),
            title=data["title"],
            options=data.get("options", []),
            status=data["status"],
            chosen_option=data.get("chosen_option"),
            created_at=data["created_at"]
        )

    @classmethod
    def from_domain(cls, req: DecisionRequest) -> DecisionRequestDTO:
        return cls(
            id=req.id,
            project_id=req.project_id,
            task_id=req.task_id,
            title=req.title,
            options=req.options,
            status=req.status.value if hasattr(req.status, 'value') else str(req.status),
            chosen_option=req.chosen_option,
            created_at=req.created_at
        )

@dataclass
class ApprovalResponse:
    approved: bool
    reason: Optional[str]
    decided_by: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "reason": self.reason,
            "decided_by": self.decided_by
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ApprovalResponse:
        return cls(
            approved=data.get("approved", False),
            reason=data.get("reason"),
            decided_by=data.get("decided_by", "system")
        )

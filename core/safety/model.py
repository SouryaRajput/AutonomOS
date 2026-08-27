from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.enums import RiskLevel
from core.safety.types import (
    CheckpointStatus,
    CheckpointType,
    RollbackStatus,
    SafetyAction,
    ScopeDeviationType,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SafetyConfig:
    """
    Configuration policy defining safety constraints for a project or runtime.
    """
    auto_checkpoint: bool = True
    max_retries: int = 2
    max_changed_files: int = 25
    max_diff_bytes: int = 500000
    protected_paths: list[str] = field(
        default_factory=lambda: [
            ".git",
            ".autonomos/memory/architecture.md",
            ".autonomos/memory/project-map.md",
            ".env",
            "production.env",
            "secrets",
        ]
    )
    allow_high_risk: bool = False
    strict_scope_enforcement: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "auto_checkpoint": self.auto_checkpoint,
            "max_retries": self.max_retries,
            "max_changed_files": self.max_changed_files,
            "max_diff_bytes": self.max_diff_bytes,
            "protected_paths": self.protected_paths,
            "allow_high_risk": self.allow_high_risk,
            "strict_scope_enforcement": self.strict_scope_enforcement,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SafetyConfig:
        return cls(
            auto_checkpoint=data.get("auto_checkpoint", True),
            max_retries=data.get("max_retries", 2),
            max_changed_files=data.get("max_changed_files", 25),
            max_diff_bytes=data.get("max_diff_bytes", 500000),
            protected_paths=list(data.get("protected_paths", [])),
            allow_high_risk=data.get("allow_high_risk", False),
            strict_scope_enforcement=data.get("strict_scope_enforcement", True),
        )


@dataclass
class SafetyDecision:
    """
    Structured outcome of a pre-execution safety evaluation.
    """
    decision: SafetyAction
    risk_level: RiskLevel
    reasons: list[str] = field(default_factory=list)
    required_checkpoint: bool = False
    required_approval: bool = False
    allowed_paths: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value if isinstance(self.decision, SafetyAction) else self.decision,
            "risk_level": self.risk_level.value if isinstance(self.risk_level, RiskLevel) else self.risk_level,
            "reasons": self.reasons,
            "required_checkpoint": self.required_checkpoint,
            "required_approval": self.required_approval,
            "allowed_paths": self.allowed_paths,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SafetyDecision:
        return cls(
            decision=SafetyAction(data["decision"]),
            risk_level=RiskLevel(data["risk_level"]),
            reasons=list(data.get("reasons", [])),
            required_checkpoint=data.get("required_checkpoint", False),
            required_approval=data.get("required_approval", False),
            allowed_paths=list(data.get("allowed_paths", [])),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class Checkpoint:
    """
    Recoverable project state representation captured before risky worker execution.
    """
    id: str
    project_id: str
    task_id: str
    worker_id: str
    checkpoint_type: CheckpointType
    status: CheckpointStatus = CheckpointStatus.CREATED
    state_reference: dict[str, Any] = field(default_factory=dict)  # git hash, files snapshot map, dirty files map
    memory_reference: dict[str, int] = field(default_factory=dict)  # doc_id -> version
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "checkpoint_type": self.checkpoint_type.value if isinstance(self.checkpoint_type, CheckpointType) else self.checkpoint_type,
            "status": self.status.value if isinstance(self.status, CheckpointStatus) else self.status,
            "state_reference": self.state_reference,
            "memory_reference": self.memory_reference,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Checkpoint:
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            task_id=data["task_id"],
            worker_id=data["worker_id"],
            checkpoint_type=CheckpointType(data["checkpoint_type"]),
            status=CheckpointStatus(data.get("status", "CREATED")),
            state_reference=dict(data.get("state_reference", {})),
            memory_reference=dict(data.get("memory_reference", {})),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ChangeRecord:
    """Individual recorded modification made to a file."""
    path: str
    change_type: str  # "created", "modified", "deleted"
    before_hash: Optional[str] = None
    after_hash: Optional[str] = None
    size: int = 0


@dataclass
class ScopeDeviation:
    """Structured report of an detected out-of-scope operation."""
    deviation_type: ScopeDeviationType
    target_path: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "deviation_type": self.deviation_type.value if isinstance(self.deviation_type, ScopeDeviationType) else self.deviation_type,
            "target_path": self.target_path,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class RollbackResult:
    """
    Structured outcome of a state recovery / rollback operation.
    """
    rollback_id: str
    checkpoint_id: str
    status: RollbackStatus
    restored_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    preserved_user_files: list[str] = field(default_factory=list)
    error_message: Optional[str] = None
    verified: bool = False
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rollback_id": self.rollback_id,
            "checkpoint_id": self.checkpoint_id,
            "status": self.status.value if isinstance(self.status, RollbackStatus) else self.status,
            "restored_files": self.restored_files,
            "deleted_files": self.deleted_files,
            "preserved_user_files": self.preserved_user_files,
            "error_message": self.error_message,
            "verified": self.verified,
            "duration_ms": round(self.duration_ms, 2),
            "metadata": self.metadata,
        }

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.verification.types import CheckStatus, CheckType, VerificationStatus


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SuccessCriterion:
    """
    Explicit specification of what must be verified for a task to be deemed successful.
    """
    id: str
    description: str
    check_type: CheckType
    parameters: dict[str, Any] = field(default_factory=dict)
    required: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "check_type": self.check_type.value if isinstance(self.check_type, CheckType) else self.check_type,
            "parameters": self.parameters,
            "required": self.required,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SuccessCriterion:
        return cls(
            id=data["id"],
            description=data["description"],
            check_type=CheckType(data["check_type"]),
            parameters=dict(data.get("parameters", {})),
            required=data.get("required", True),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class VerificationCheck:
    """
    Execution record of an individual verification check against evidence.
    """
    id: str
    verification_id: str
    check_type: CheckType
    description: str
    criterion_id: Optional[str] = None
    expected_result: Any = None
    actual_result: Any = None
    status: CheckStatus = CheckStatus.NOT_RUN
    required: bool = True
    evidence_ids: list[str] = field(default_factory=list)
    error_message: Optional[str] = None
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "verification_id": self.verification_id,
            "criterion_id": self.criterion_id,
            "check_type": self.check_type.value if isinstance(self.check_type, CheckType) else self.check_type,
            "description": self.description,
            "expected_result": self.expected_result,
            "actual_result": self.actual_result,
            "status": self.status.value if isinstance(self.status, CheckStatus) else self.status,
            "required": self.required,
            "evidence_ids": self.evidence_ids,
            "error_message": self.error_message,
            "duration_ms": round(self.duration_ms, 2),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationCheck:
        return cls(
            id=data["id"],
            verification_id=data["verification_id"],
            criterion_id=data.get("criterion_id"),
            check_type=CheckType(data["check_type"]),
            description=data["description"],
            expected_result=data.get("expected_result"),
            actual_result=data.get("actual_result"),
            status=CheckStatus(data.get("status", "NOT_RUN")),
            required=data.get("required", True),
            evidence_ids=list(data.get("evidence_ids", [])),
            error_message=data.get("error_message"),
            duration_ms=float(data.get("duration_ms", 0.0)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class VerificationPlan:
    """
    Structured plan containing all verification criteria required for a task.
    """
    id: str
    task_id: str
    criteria: list[SuccessCriterion] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "criteria": [c.to_dict() for c in self.criteria],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationPlan:
        return cls(
            id=data["id"],
            task_id=data["task_id"],
            criteria=[SuccessCriterion.from_dict(c) for c in data.get("criteria", [])],
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class Verification:
    """
    Authoritative verification session evaluating task output against evidence and success criteria.
    """
    id: str
    project_id: str
    task_id: str
    checkpoint_id: Optional[str] = None
    status: VerificationStatus = VerificationStatus.PENDING
    checks: list[VerificationCheck] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    summary: str = ""
    started_at: str = field(default_factory=utc_now)
    completed_at: Optional[str] = None
    duration_ms: float = 0.0
    report_markdown: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "checkpoint_id": self.checkpoint_id,
            "status": self.status.value if isinstance(self.status, VerificationStatus) else self.status,
            "checks": [c.to_dict() for c in self.checks],
            "evidence_ids": self.evidence_ids,
            "summary": self.summary,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": round(self.duration_ms, 2),
            "report_markdown": self.report_markdown,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Verification:
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            task_id=data["task_id"],
            checkpoint_id=data.get("checkpoint_id"),
            status=VerificationStatus(data.get("status", "PENDING")),
            checks=[VerificationCheck.from_dict(c) for c in data.get("checks", [])],
            evidence_ids=list(data.get("evidence_ids", [])),
            summary=data.get("summary", ""),
            started_at=data.get("started_at", utc_now()),
            completed_at=data.get("completed_at"),
            duration_ms=float(data.get("duration_ms", 0.0)),
            report_markdown=data.get("report_markdown", ""),
            metadata=dict(data.get("metadata", {})),
        )

    def to_result(self) -> "VerificationResult":
        passed = [c.id for c in self.checks if c.status == CheckStatus.PASSED]
        failed = [c.id for c in self.checks if c.status == CheckStatus.FAILED]
        uncertain = [c.id for c in self.checks if c.status in (CheckStatus.UNCERTAIN, CheckStatus.ERROR)]
        return VerificationResult(
            verification_id=self.id,
            status=self.status,
            passed_checks=passed,
            failed_checks=failed,
            uncertain_checks=uncertain,
            evidence_ids=list(self.evidence_ids),
            summary=self.summary,
            report_markdown=self.report_markdown,
            metadata=dict(self.metadata),
        )


@dataclass
class VerificationResult:
    """
    Immutable summary result returned after verification completes.
    """
    verification_id: str
    status: VerificationStatus
    passed_checks: list[str] = field(default_factory=list)
    failed_checks: list[str] = field(default_factory=list)
    uncertain_checks: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    summary: str = ""
    report_markdown: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verification_id": self.verification_id,
            "status": self.status.value if isinstance(self.status, VerificationStatus) else self.status,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "uncertain_checks": self.uncertain_checks,
            "evidence_ids": self.evidence_ids,
            "summary": self.summary,
            "report_markdown": self.report_markdown,
            "metadata": self.metadata,
        }

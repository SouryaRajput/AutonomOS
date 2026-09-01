from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.research.types import CrawlerCapability, CrawlerTaskStatus


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CrawlerTask:
    """
    A concrete, bounded data collection task assigned to an individual crawler.
    Maintains strict lineage to the parent ResearchRequest, ResearchPlan, and target ResearchQuestion.
    """
    task_id: str
    request_id: str
    plan_id: str
    question_id: str
    query_or_target: str
    objective: str = ""
    required_capability: CrawlerCapability = CrawlerCapability.WEB_SEARCH
    required_capabilities: list[CrawlerCapability] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    priority: int = 50
    parameters: dict[str, Any] = field(default_factory=dict)
    status: CrawlerTaskStatus = CrawlerTaskStatus.PENDING
    assigned_crawler_id: Optional[str] = None
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timeout_seconds: int = 60
    attempts: int = 0
    max_attempts: int = 3
    error_message: Optional[str] = None
    cancellation_reason: Optional[str] = None
    cancelled_at: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.required_capabilities:
            self.required_capabilities = [self.required_capability]
        elif self.required_capability not in self.required_capabilities:
            self.required_capabilities.insert(0, self.required_capability)
        if not self.objective:
            self.objective = self.query_or_target

    def cancel(self, reason: str = "Cancelled by supervisor") -> None:
        """Mark this crawler task as cancelled."""
        self.status = CrawlerTaskStatus.CANCELLED
        self.cancellation_reason = reason
        self.cancelled_at = utc_now()
        self.completed_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "request_id": self.request_id,
            "plan_id": self.plan_id,
            "question_id": self.question_id,
            "query_or_target": self.query_or_target,
            "objective": self.objective,
            "required_capability": self.required_capability.value if isinstance(self.required_capability, CrawlerCapability) else str(self.required_capability),
            "required_capabilities": [
                c.value if isinstance(c, CrawlerCapability) else str(c) for c in self.required_capabilities
            ],
            "constraints": list(self.constraints),
            "priority": self.priority,
            "parameters": self.parameters,
            "status": self.status.value if isinstance(self.status, CrawlerTaskStatus) else str(self.status),
            "assigned_crawler_id": self.assigned_crawler_id,
            "correlation_id": self.correlation_id,
            "timeout_seconds": self.timeout_seconds,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "error_message": self.error_message,
            "cancellation_reason": self.cancellation_reason,
            "cancelled_at": self.cancelled_at,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrawlerTask:
        cap_raw = data.get("required_capability", CrawlerCapability.WEB_SEARCH.value)
        try:
            cap = CrawlerCapability(cap_raw)
        except (ValueError, TypeError):
            cap = CrawlerCapability.WEB_SEARCH

        caps_raw = data.get("required_capabilities", [])
        caps: list[CrawlerCapability] = []
        for c in caps_raw:
            try:
                caps.append(CrawlerCapability(c))
            except (ValueError, TypeError):
                pass
        if not caps:
            caps = [cap]

        st_raw = data.get("status", CrawlerTaskStatus.PENDING.value)
        try:
            status = CrawlerTaskStatus(st_raw)
        except (ValueError, TypeError):
            status = CrawlerTaskStatus.PENDING

        req_id = data.get("request_id") or data.get("researchRequestId", "")

        return cls(
            task_id=data.get("task_id", f"ctask-{uuid.uuid4().hex[:8]}"),
            request_id=req_id,
            plan_id=data.get("plan_id", ""),
            question_id=data.get("question_id", ""),
            query_or_target=data.get("query_or_target", ""),
            objective=data.get("objective", ""),
            required_capability=cap,
            required_capabilities=caps,
            constraints=list(data.get("constraints", [])),
            priority=int(data.get("priority", 50)),
            parameters=dict(data.get("parameters", {})),
            status=status,
            assigned_crawler_id=data.get("assigned_crawler_id"),
            correlation_id=data.get("correlation_id", str(uuid.uuid4())),
            timeout_seconds=int(data.get("timeout_seconds", 60)),
            attempts=int(data.get("attempts", 0)),
            max_attempts=int(data.get("max_attempts", 3)),
            error_message=data.get("error_message"),
            cancellation_reason=data.get("cancellation_reason"),
            cancelled_at=data.get("cancelled_at"),
            created_at=data.get("created_at", utc_now()),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            metadata=dict(data.get("metadata", {})),
        )

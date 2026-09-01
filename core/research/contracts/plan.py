from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.question import ResearchQuestion
from core.research.contracts.request import ResearchScope
from core.research.types import ResearchMode


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ResearchPlan:
    """
    Structured execution plan produced during the PLANNING phase.
    Decomposes the research request into discrete questions, planned steps,
    and granular CrawlerTasks for dynamic allocation.
    """
    plan_id: str
    request_id: str
    objective: str
    mode: ResearchMode = ResearchMode.STANDARD
    scope: ResearchScope = field(default_factory=ResearchScope)
    questions: list[ResearchQuestion] = field(default_factory=list)
    crawler_tasks: list[CrawlerTask] = field(default_factory=list)
    planned_steps: list[str] = field(default_factory=list)
    allocated_crawler_count: int = 1
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "request_id": self.request_id,
            "objective": self.objective,
            "mode": self.mode.value if isinstance(self.mode, ResearchMode) else str(self.mode),
            "scope": self.scope.to_dict(),
            "questions": [q.to_dict() for q in self.questions],
            "crawler_tasks": [t.to_dict() for t in self.crawler_tasks],
            "planned_steps": list(self.planned_steps),
            "allocated_crawler_count": self.allocated_crawler_count,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchPlan:
        m_raw = data.get("mode", ResearchMode.STANDARD.value)
        try:
            mode = ResearchMode(m_raw)
        except (ValueError, TypeError):
            mode = ResearchMode.STANDARD

        questions = [
            ResearchQuestion.from_dict(q) if isinstance(q, dict) else q
            for q in data.get("questions", [])
        ]
        tasks = [
            CrawlerTask.from_dict(t) if isinstance(t, dict) else t
            for t in data.get("crawler_tasks", [])
        ]

        return cls(
            plan_id=data.get("plan_id", f"rplan-{uuid.uuid4().hex[:8]}"),
            request_id=data.get("request_id", ""),
            objective=data.get("objective", ""),
            mode=mode,
            scope=ResearchScope.from_dict(data.get("scope", {})),
            questions=questions,
            crawler_tasks=tasks,
            planned_steps=list(data.get("planned_steps", [])),
            allocated_crawler_count=int(data.get("allocated_crawler_count", 1)),
            correlation_id=data.get("correlation_id", str(uuid.uuid4())),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )

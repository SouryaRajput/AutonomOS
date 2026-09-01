from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.research.types import ResearchMode, SourceType


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "res-req") -> str:
    """Generate unique identifier with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@dataclass
class ResearchScope:
    """Resource bounds, domain constraints, and depth bounds for a research request."""
    allowed_domains: list[str] = field(default_factory=list)
    excluded_domains: list[str] = field(default_factory=list)
    preferred_source_types: list[SourceType] = field(default_factory=list)
    recency_days: Optional[int] = None
    max_crawlers: int = 5
    max_searches: int = 6
    max_fetches: int = 10
    max_sources: int = 15
    max_inference_calls: int = 5
    cost_limit: float = 1.0
    timeout_seconds: int = 300
    min_evidence_per_question: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_domains": list(self.allowed_domains),
            "excluded_domains": list(self.excluded_domains),
            "preferred_source_types": [
                s.value if isinstance(s, SourceType) else str(s) for s in self.preferred_source_types
            ],
            "recency_days": self.recency_days,
            "max_crawlers": self.max_crawlers,
            "max_searches": self.max_searches,
            "max_fetches": self.max_fetches,
            "max_sources": self.max_sources,
            "max_inference_calls": self.max_inference_calls,
            "cost_limit": self.cost_limit,
            "timeout_seconds": self.timeout_seconds,
            "min_evidence_per_question": self.min_evidence_per_question,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchScope:
        sources: list[SourceType] = []
        for s in data.get("preferred_source_types", []):
            try:
                sources.append(SourceType(s))
            except (ValueError, TypeError):
                sources.append(SourceType.OTHER)

        return cls(
            allowed_domains=list(data.get("allowed_domains", [])),
            excluded_domains=list(data.get("excluded_domains", [])),
            preferred_source_types=sources,
            recency_days=data.get("recency_days"),
            max_crawlers=int(data.get("max_crawlers", 5)),
            max_searches=int(data.get("max_searches", 6)),
            max_fetches=int(data.get("max_fetches", 10)),
            max_sources=int(data.get("max_sources", 15)),
            max_inference_calls=int(data.get("max_inference_calls", 5)),
            cost_limit=float(data.get("cost_limit", 1.0)),
            timeout_seconds=int(data.get("timeout_seconds", 300)),
            min_evidence_per_question=int(data.get("min_evidence_per_question", 1)),
        )


@dataclass
class ResearchRequest:
    """
    Formal, machine-validatable request contract sent by the Manager or runtime to the Researcher.
    Establishes the root correlation context for all downstream planning, crawler tasks, and reports.
    """
    request_id: str
    project_id: str
    task_id: str
    objective: str
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    mode: ResearchMode = ResearchMode.STANDARD
    questions: list[str] = field(default_factory=list)
    scope: ResearchScope = field(default_factory=ResearchScope)
    constraints: list[str] = field(default_factory=list)
    required_output_format: str = "markdown_report"
    priority: int = 50
    context_references: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "correlation_id": self.correlation_id,
            "objective": self.objective,
            "mode": self.mode.value if isinstance(self.mode, ResearchMode) else str(self.mode),
            "questions": list(self.questions),
            "scope": self.scope.to_dict(),
            "constraints": list(self.constraints),
            "required_output_format": self.required_output_format,
            "priority": self.priority,
            "context_references": self.context_references,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchRequest:
        m_raw = data.get("mode", ResearchMode.STANDARD.value)
        try:
            mode = ResearchMode(m_raw)
        except (ValueError, TypeError):
            mode = ResearchMode.STANDARD

        return cls(
            request_id=data.get("request_id", new_id("req")),
            project_id=data.get("project_id", ""),
            task_id=data.get("task_id", ""),
            correlation_id=data.get("correlation_id", str(uuid.uuid4())),
            objective=data.get("objective", ""),
            mode=mode,
            questions=list(data.get("questions", [])),
            scope=ResearchScope.from_dict(data.get("scope", {})),
            constraints=list(data.get("constraints", [])),
            required_output_format=str(data.get("required_output_format", "markdown_report")),
            priority=int(data.get("priority", 50)),
            context_references=list(data.get("context_references", [])),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )

    @classmethod
    def from_task(cls, task: Any) -> ResearchRequest:
        """Construct a strongly-typed ResearchRequest from a runtime Task model."""
        meta = getattr(task, "metadata", {}) or {}
        
        mode_val = meta.get("mode", ResearchMode.STANDARD.value)
        try:
            mode = ResearchMode(mode_val)
        except (ValueError, TypeError):
            mode = ResearchMode.STANDARD

        # Build scope from metadata overrides
        scope_data = dict(meta.get("scope", {}))
        if "allowed_domains" in meta:
            scope_data["allowed_domains"] = meta["allowed_domains"]
        if "excluded_domains" in meta:
            scope_data["excluded_domains"] = meta["excluded_domains"]
        if "recency_days" in meta:
            scope_data["recency_days"] = meta["recency_days"]
        if "max_crawlers" in meta:
            scope_data["max_crawlers"] = meta["max_crawlers"]

        # Default max searches based on mode
        if "max_searches" not in scope_data:
            if mode == ResearchMode.DEEP:
                scope_data["max_searches"] = 10
            elif mode == ResearchMode.QUICK:
                scope_data["max_searches"] = 3
            else:
                scope_data["max_searches"] = 6

        questions = list(meta.get("questions", []))
        constraints = list(meta.get("constraints", []))

        return cls(
            request_id=f"req-{task.id}",
            project_id=getattr(task, "project_id", ""),
            task_id=getattr(task, "id", ""),
            correlation_id=getattr(task, "id", str(uuid.uuid4())),
            objective=getattr(task, "objective", "") or getattr(task, "title", ""),
            mode=mode,
            questions=questions,
            scope=ResearchScope.from_dict(scope_data),
            constraints=constraints,
            priority=getattr(task, "priority", 50),
            context_references=getattr(task, "context_references", []) or [],
            metadata=meta,
        )

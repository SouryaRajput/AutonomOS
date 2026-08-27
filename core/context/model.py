from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Optional
import uuid

from core.context.types import ContextPriority, ContextSourceType, ContextWarningType


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    """
    Deterministic token estimation heuristic.
    Approximates GPT/Claude/Llama tokenizers: ~4 characters per token or word count + punctuation.
    Uses max(1, math.ceil(len(text) / 4.0)) with minimum 1 token for non-empty text.
    """
    if not text:
        return 0
    # Standard 4 chars per token heuristic
    char_tokens = math.ceil(len(text) / 4.0)
    # Word count heuristic
    word_tokens = math.ceil(len(text.split()) * 1.3)
    return max(1, max(char_tokens, word_tokens))


@dataclass
class ContextBudget:
    """
    Defines the context capacity constraints for a worker execution context.
    """
    max_tokens: int = 4000
    max_characters: int = 16000
    max_items: int = 25
    reserved_mandatory_tokens: int = 1000

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "max_characters": self.max_characters,
            "max_items": self.max_items,
            "reserved_mandatory_tokens": self.reserved_mandatory_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextBudget":
        return cls(
            max_tokens=data.get("max_tokens", 4000),
            max_characters=data.get("max_characters", 16000),
            max_items=data.get("max_items", 25),
            reserved_mandatory_tokens=data.get("reserved_mandatory_tokens", 1000),
        )


@dataclass
class ContextRequest:
    """
    Formal request for assembling relevant context for a specific task and worker.
    """
    project_id: str
    task_id: str
    worker_id: Optional[str] = None
    request_id: str = field(default_factory=lambda: f"ctx-req-{uuid.uuid4().hex[:10]}")
    budget: ContextBudget = field(default_factory=ContextBudget)
    required_sources: list[ContextSourceType] = field(default_factory=list)
    excluded_sources: list[ContextSourceType] = field(default_factory=list)
    focus_areas: list[str] = field(default_factory=list)  # Keywords, file paths, or component names
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "budget": self.budget.to_dict(),
            "required_sources": [s.value if isinstance(s, ContextSourceType) else s for s in self.required_sources],
            "excluded_sources": [s.value if isinstance(s, ContextSourceType) else s for s in self.excluded_sources],
            "focus_areas": self.focus_areas,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextRequest":
        return cls(
            request_id=data["request_id"],
            project_id=data["project_id"],
            task_id=data["task_id"],
            worker_id=data.get("worker_id"),
            budget=ContextBudget.from_dict(data.get("budget", {})),
            required_sources=[ContextSourceType(s) for s in data.get("required_sources", [])],
            excluded_sources=[ContextSourceType(s) for s in data.get("excluded_sources", [])],
            focus_areas=list(data.get("focus_areas", [])),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )


@dataclass
class ContextItem:
    """
    A single candidate or selected unit of contextual information.
    """
    id: str
    source_type: ContextSourceType
    source_id: str
    title: str
    content: str
    is_reference_only: bool = False  # If True, content contains metadata/pointer rather than full body
    priority: ContextPriority = ContextPriority.MEDIUM
    is_required: bool = False
    relevance_score: float = 0.0
    scoring_reasons: list[str] = field(default_factory=list)
    token_estimate: int = 0
    character_count: int = 0
    freshness_score: float = 1.0
    checksum: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.character_count:
            self.character_count = len(self.content)
        if not self.token_estimate:
            self.token_estimate = estimate_tokens(self.content)
        if not self.checksum:
            self.checksum = compute_checksum(self.content)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_type": self.source_type.value if isinstance(self.source_type, ContextSourceType) else self.source_type,
            "source_id": self.source_id,
            "title": self.title,
            "content": self.content,
            "is_reference_only": self.is_reference_only,
            "priority": self.priority.value if isinstance(self.priority, ContextPriority) else self.priority,
            "is_required": self.is_required,
            "relevance_score": round(self.relevance_score, 4),
            "scoring_reasons": self.scoring_reasons,
            "token_estimate": self.token_estimate,
            "character_count": self.character_count,
            "freshness_score": round(self.freshness_score, 4),
            "checksum": self.checksum,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextItem":
        return cls(
            id=data["id"],
            source_type=ContextSourceType(data["source_type"]),
            source_id=data["source_id"],
            title=data["title"],
            content=data["content"],
            is_reference_only=data.get("is_reference_only", False),
            priority=ContextPriority(data.get("priority", "MEDIUM")),
            is_required=data.get("is_required", False),
            relevance_score=float(data.get("relevance_score", 0.0)),
            scoring_reasons=list(data.get("scoring_reasons", [])),
            token_estimate=int(data.get("token_estimate", 0)),
            character_count=int(data.get("character_count", 0)),
            freshness_score=float(data.get("freshness_score", 1.0)),
            checksum=data.get("checksum", ""),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ContextWarning:
    """Structured warning emitted during context assembly."""
    warning_type: ContextWarningType
    message: str
    target_id: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "warning_type": self.warning_type.value if isinstance(self.warning_type, ContextWarningType) else self.warning_type,
            "message": self.message,
            "target_id": self.target_id,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextWarning":
        return cls(
            warning_type=ContextWarningType(data["warning_type"]),
            message=data["message"],
            target_id=data.get("target_id"),
            details=dict(data.get("details", {})),
        )


@dataclass
class ContextPackage:
    """
    The final assembled, bounded, and ranked context package delivered to an AI worker.
    """
    request_id: str
    project_id: str
    task_id: str
    worker_id: Optional[str]
    items: list[ContextItem]
    total_estimated_tokens: int
    total_characters: int
    budget: ContextBudget
    warnings: list[ContextWarning] = field(default_factory=list)
    candidate_count: int = 0
    selected_count: int = 0
    assembled_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_item_by_id(self, item_id: str) -> Optional[ContextItem]:
        for it in self.items:
            if it.id == item_id or it.source_id == item_id:
                return it
        return None

    def get_items_by_source_type(self, source_type: ContextSourceType) -> list[ContextItem]:
        return [it for it in self.items if it.source_type == source_type]

    def format_as_prompt_section(self) -> str:
        """
        Render the structured context package as clean, structured Markdown suitable for prompt injection.
        """
        lines = [
            f"# Context Package for Task: `{self.task_id}`",
            f"**Project**: `{self.project_id}` | **Worker**: `{self.worker_id or 'General'}`",
            f"**Estimated Tokens**: {self.total_estimated_tokens}/{self.budget.max_tokens} | **Items**: {len(self.items)}",
            "",
        ]

        if self.warnings:
            lines.append("## Context Warnings & Discrepancies")
            for w in self.warnings:
                lines.append(f"- **[{w.warning_type.value}]**: {w.message}")
            lines.append("")

        lines.append("---")

        for idx, item in enumerate(self.items, 1):
            ref_badge = " *(Reference Pointer)*" if item.is_reference_only else ""
            req_badge = " `[REQUIRED]`" if item.is_required else ""
            lines.append(f"## {idx}. [{item.source_type.value}] {item.title}{req_badge}{ref_badge}")
            lines.append(f"*Source*: `{item.source_id}` | *Relevance*: {item.relevance_score:.2f} | *Tokens*: ~{item.token_estimate}")
            if item.scoring_reasons:
                lines.append(f"*Selection Rationale*: {'; '.join(item.scoring_reasons)}")
            lines.append("")
            lines.append(item.content.strip())
            lines.append("")
            lines.append("---")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "items": [it.to_dict() for it in self.items],
            "total_estimated_tokens": self.total_estimated_tokens,
            "total_characters": self.total_characters,
            "budget": self.budget.to_dict(),
            "warnings": [w.to_dict() for w in self.warnings],
            "candidate_count": self.candidate_count,
            "selected_count": self.selected_count,
            "assembled_at": self.assembled_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextPackage":
        return cls(
            request_id=data["request_id"],
            project_id=data["project_id"],
            task_id=data["task_id"],
            worker_id=data.get("worker_id"),
            items=[ContextItem.from_dict(it) for it in data.get("items", [])],
            total_estimated_tokens=int(data.get("total_estimated_tokens", 0)),
            total_characters=int(data.get("total_characters", 0)),
            budget=ContextBudget.from_dict(data.get("budget", {})),
            warnings=[ContextWarning.from_dict(w) for w in data.get("warnings", [])],
            candidate_count=int(data.get("candidate_count", 0)),
            selected_count=int(data.get("selected_count", 0)),
            assembled_at=data.get("assembled_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )

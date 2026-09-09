from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from core.tester.contracts.identifiers import (
    new_recommendation_id,
    validate_recommendation_id,
)
from core.tester.errors import TesterValidationError
from core.tester.types import RecommendationPriority


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TesterRecommendation:
    """
    Advisory recommendation reported by Tester to Manager.
    
    Invariants:
    - Purely advisory: does NOT authorize code changes.
    - Does NOT automatically trigger Programmer worker.
    - Does NOT automatically spawn new TesterExecutions.
    - Does NOT create infinite improvement loops.
    """
    __test__ = False
    recommendation_id: str
    title: str
    description: str
    related_finding_id: Optional[str] = None
    evidence_ids: list[str] = field(default_factory=list)
    priority: RecommendationPriority = RecommendationPriority.MEDIUM
    confidence: float = 1.0
    trace: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_recommendation_id(self.recommendation_id)
        if not self.title or not self.title.strip():
            raise TesterValidationError("Recommendation title cannot be empty.", field_name="title")

        if isinstance(self.priority, str):
            try:
                self.priority = RecommendationPriority(self.priority.upper())
            except (ValueError, KeyError):
                self.priority = RecommendationPriority.MEDIUM

        if not (0.0 <= float(self.confidence) <= 1.0):
            raise TesterValidationError(
                f"Confidence must be between 0.0 and 1.0, got {self.confidence}.",
                field_name="confidence",
            )

        self.evidence_ids = list(self.evidence_ids)
        self.trace = dict(self.trace)

    def validate(self) -> None:
        validate_recommendation_id(self.recommendation_id)
        if not self.title or not self.title.strip():
            raise TesterValidationError("Recommendation title cannot be empty.", field_name="title")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise TesterValidationError(
                f"Confidence must be between 0.0 and 1.0, got {self.confidence}.",
                field_name="confidence",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "title": self.title,
            "description": self.description,
            "related_finding_id": self.related_finding_id,
            "evidence_ids": list(self.evidence_ids),
            "priority": self.priority.value if hasattr(self.priority, "value") else str(self.priority),
            "confidence": self.confidence,
            "trace": dict(self.trace),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TesterRecommendation:
        prio_raw = data.get("priority", RecommendationPriority.MEDIUM.value)
        try:
            priority = RecommendationPriority(str(prio_raw).upper())
        except (ValueError, KeyError):
            priority = RecommendationPriority.MEDIUM

        return cls(
            recommendation_id=str(data.get("recommendation_id", new_recommendation_id())),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            related_finding_id=data.get("related_finding_id"),
            evidence_ids=list(data.get("evidence_ids", [])),
            priority=priority,
            confidence=float(data.get("confidence", 1.0)),
            trace=dict(data.get("trace", {})),
            created_at=str(data.get("created_at", utc_now())),
        )

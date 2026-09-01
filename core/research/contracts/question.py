from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import uuid

from core.research.types import (
    CrawlerCapability,
    EvidenceSufficiency,
    ResearchQuestionStatus,
    SourceType,
)


@dataclass
class ResearchQuestion:
    """
    An individual decomposed research question or hypothesis being actively investigated.
    Retains parent lineage to the creating ResearchRequest and tracks linked CrawlerTasks and Evidence.
    """
    question_id: str
    question_text: str
    request_id: str = ""
    plan_id: str = ""
    status: ResearchQuestionStatus = ResearchQuestionStatus.UNANSWERED
    sufficiency: EvidenceSufficiency = EvidenceSufficiency.INSUFFICIENT
    required_capabilities: list[CrawlerCapability] = field(default_factory=lambda: [CrawlerCapability.WEB_SEARCH])
    target_source_types: list[SourceType] = field(default_factory=list)
    assigned_crawler_task_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    answered_findings: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question_text": self.question_text,
            "request_id": self.request_id,
            "plan_id": self.plan_id,
            "status": self.status.value if isinstance(self.status, ResearchQuestionStatus) else str(self.status),
            "sufficiency": self.sufficiency.value if isinstance(self.sufficiency, EvidenceSufficiency) else str(self.sufficiency),
            "required_capabilities": [
                c.value if isinstance(c, CrawlerCapability) else str(c) for c in self.required_capabilities
            ],
            "target_source_types": [
                s.value if isinstance(s, SourceType) else str(s) for s in self.target_source_types
            ],
            "assigned_crawler_task_ids": list(self.assigned_crawler_task_ids),
            "evidence_ids": list(self.evidence_ids),
            "answered_findings": list(self.answered_findings),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchQuestion:
        st_raw = data.get("status", ResearchQuestionStatus.UNANSWERED.value)
        try:
            status = ResearchQuestionStatus(st_raw)
        except (ValueError, TypeError):
            status = ResearchQuestionStatus.UNANSWERED

        suff_raw = data.get("sufficiency", EvidenceSufficiency.INSUFFICIENT.value)
        try:
            sufficiency = EvidenceSufficiency(suff_raw)
        except (ValueError, TypeError):
            sufficiency = EvidenceSufficiency.INSUFFICIENT

        caps: list[CrawlerCapability] = []
        for c in data.get("required_capabilities", []):
            try:
                caps.append(CrawlerCapability(c))
            except (ValueError, TypeError):
                caps.append(CrawlerCapability.WEB_SEARCH)
        if not caps:
            caps = [CrawlerCapability.WEB_SEARCH]

        target_sources: list[SourceType] = []
        for s in data.get("target_source_types", []):
            try:
                target_sources.append(SourceType(s))
            except (ValueError, TypeError):
                target_sources.append(SourceType.OTHER)

        return cls(
            question_id=data.get("question_id", f"q-{uuid.uuid4().hex[:6]}"),
            question_text=data.get("question_text", ""),
            request_id=data.get("request_id", ""),
            plan_id=data.get("plan_id", ""),
            status=status,
            sufficiency=sufficiency,
            required_capabilities=caps,
            target_source_types=target_sources,
            assigned_crawler_task_ids=list(data.get("assigned_crawler_task_ids", [])),
            evidence_ids=list(data.get("evidence_ids", [])),
            answered_findings=list(data.get("answered_findings", [])),
            notes=str(data.get("notes", "")),
        )

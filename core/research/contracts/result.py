from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.research.contracts.crawler_report import CrawlerReport
from core.research.contracts.evidence import EvidenceItem, Source
from core.research.contracts.plan import ResearchPlan
from core.research.contracts.question import ResearchQuestion
from core.research.types import (
    FactClassification,
    ResearchConfidence,
    ResearchMode,
    ResearchResultStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ResearchFinding:
    """A distinct, evidence-grounded finding with strict epistemic classification."""
    finding_id: str
    claim: str
    classification: FactClassification = FactClassification.FACT
    confidence: ResearchConfidence = ResearchConfidence.SUPPORTED
    source_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    corroborating_source_ids: list[str] = field(default_factory=list)
    conflicting_source_ids: list[str] = field(default_factory=list)
    reasoning: str = ""
    project_implications: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "claim": self.claim,
            "classification": self.classification.value if isinstance(self.classification, FactClassification) else str(self.classification),
            "confidence": self.confidence.value if isinstance(self.confidence, ResearchConfidence) else str(self.confidence),
            "source_ids": list(self.source_ids),
            "evidence_ids": list(self.evidence_ids),
            "corroborating_source_ids": list(self.corroborating_source_ids),
            "conflicting_source_ids": list(self.conflicting_source_ids),
            "reasoning": self.reasoning,
            "project_implications": self.project_implications,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchFinding:
        c_raw = data.get("classification", FactClassification.FACT.value)
        try:
            classification = FactClassification(c_raw)
        except (ValueError, TypeError):
            classification = FactClassification.FACT

        conf_raw = data.get("confidence", ResearchConfidence.SUPPORTED.value)
        try:
            confidence = ResearchConfidence(conf_raw)
        except (ValueError, TypeError):
            confidence = ResearchConfidence.SUPPORTED

        return cls(
            finding_id=data.get("finding_id", f"f-{uuid.uuid4().hex[:6]}"),
            claim=data.get("claim", ""),
            classification=classification,
            confidence=confidence,
            source_ids=list(data.get("source_ids", [])),
            evidence_ids=list(data.get("evidence_ids", [])),
            corroborating_source_ids=list(data.get("corroborating_source_ids", [])),
            conflicting_source_ids=list(data.get("conflicting_source_ids", [])),
            reasoning=str(data.get("reasoning", "")),
            project_implications=str(data.get("project_implications", "")),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ResearchContradiction:
    """Explicitly identified contradiction between independent sources."""
    contradiction_id: str
    topic: str
    claim_a: str
    sources_a: list[str]
    claim_b: str
    sources_b: list[str]
    analysis: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "contradiction_id": self.contradiction_id,
            "topic": self.topic,
            "claim_a": self.claim_a,
            "sources_a": list(self.sources_a),
            "claim_b": self.claim_b,
            "sources_b": list(self.sources_b),
            "analysis": self.analysis,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchContradiction:
        return cls(
            contradiction_id=data.get("contradiction_id", f"c-{uuid.uuid4().hex[:6]}"),
            topic=data.get("topic", ""),
            claim_a=data.get("claim_a", ""),
            sources_a=list(data.get("sources_a", [])),
            claim_b=data.get("claim_b", ""),
            sources_b=list(data.get("sources_b", [])),
            analysis=str(data.get("analysis", "")),
        )


@dataclass
class ResearchKnowledgeGap:
    """An explicit unknown or area where reliable evidence could not be found."""
    gap_id: str
    topic: str
    question: str
    reason: str
    impact: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "topic": self.topic,
            "question": self.question,
            "reason": self.reason,
            "impact": self.impact,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchKnowledgeGap:
        return cls(
            gap_id=data.get("gap_id", f"gap-{uuid.uuid4().hex[:6]}"),
            topic=data.get("topic", ""),
            question=data.get("question", ""),
            reason=data.get("reason", ""),
            impact=str(data.get("impact", "")),
        )


@dataclass
class ResearchRecommendation:
    """Actionable advice derived from findings, explicitly separated from facts."""
    recommendation_id: str
    action: str
    rationale: str
    supporting_finding_ids: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    tradeoffs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "action": self.action,
            "rationale": self.rationale,
            "supporting_finding_ids": list(self.supporting_finding_ids),
            "risks": list(self.risks),
            "tradeoffs": list(self.tradeoffs),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchRecommendation:
        return cls(
            recommendation_id=data.get("recommendation_id", f"rec-{uuid.uuid4().hex[:6]}"),
            action=data.get("action", ""),
            rationale=data.get("rationale", ""),
            supporting_finding_ids=list(data.get("supporting_finding_ids", [])),
            risks=list(data.get("risks", [])),
            tradeoffs=list(data.get("tradeoffs", [])),
        )


@dataclass
class ResearchResult:
    """
    Final comprehensive, structured research package returned to the Manager.
    Traceable to the parent ResearchRequest with distinction between verified and partial results.
    """
    task_id: str
    request_id: str
    project_id: str
    objective: str
    mode: ResearchMode = ResearchMode.STANDARD
    status: ResearchResultStatus = ResearchResultStatus.VERIFIED
    plan: Optional[ResearchPlan] = None
    questions: list[ResearchQuestion] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    findings: list[ResearchFinding] = field(default_factory=list)
    evidence: list[EvidenceItem] = field(default_factory=list)
    contradictions: list[ResearchContradiction] = field(default_factory=list)
    knowledge_gaps: list[ResearchKnowledgeGap] = field(default_factory=list)
    recommendations: list[ResearchRecommendation] = field(default_factory=list)
    crawler_reports: list[CrawlerReport] = field(default_factory=list)
    report_artifact_id: Optional[str] = None
    report_path: Optional[str] = None
    evidence_ids: list[str] = field(default_factory=list)
    summary_for_manager: str = ""
    total_crawlers_spawned: int = 0
    total_tasks_executed: int = 0
    cost_estimate: float = 0.0
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "request_id": self.request_id,
            "project_id": self.project_id,
            "objective": self.objective,
            "mode": self.mode.value if isinstance(self.mode, ResearchMode) else str(self.mode),
            "status": self.status.value if isinstance(self.status, ResearchResultStatus) else str(self.status),
            "plan": self.plan.to_dict() if self.plan else None,
            "questions": [q.to_dict() for q in self.questions],
            "sources": [s.to_dict() for s in self.sources],
            "findings": [f.to_dict() for f in self.findings],
            "evidence": [e.to_dict() for e in self.evidence],
            "contradictions": [c.to_dict() for c in self.contradictions],
            "knowledge_gaps": [g.to_dict() for g in self.knowledge_gaps],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "crawler_reports": [cr.to_dict() for cr in self.crawler_reports],
            "report_artifact_id": self.report_artifact_id,
            "report_path": self.report_path,
            "evidence_ids": list(self.evidence_ids),
            "summary_for_manager": self.summary_for_manager,
            "total_crawlers_spawned": self.total_crawlers_spawned,
            "total_tasks_executed": self.total_tasks_executed,
            "cost_estimate": self.cost_estimate,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchResult:
        m_raw = data.get("mode", ResearchMode.STANDARD.value)
        try:
            mode = ResearchMode(m_raw)
        except (ValueError, TypeError):
            mode = ResearchMode.STANDARD

        st_raw = data.get("status", ResearchResultStatus.VERIFIED.value)
        try:
            status = ResearchResultStatus(st_raw)
        except (ValueError, TypeError):
            status = ResearchResultStatus.VERIFIED

        plan_data = data.get("plan")
        plan = ResearchPlan.from_dict(plan_data) if plan_data and isinstance(plan_data, dict) else plan_data

        return cls(
            task_id=data.get("task_id", ""),
            request_id=data.get("request_id", ""),
            project_id=data.get("project_id", ""),
            objective=data.get("objective", ""),
            mode=mode,
            status=status,
            plan=plan,
            questions=[ResearchQuestion.from_dict(q) if isinstance(q, dict) else q for q in data.get("questions", [])],
            sources=[Source.from_dict(s) if isinstance(s, dict) else s for s in data.get("sources", [])],
            findings=[ResearchFinding.from_dict(f) if isinstance(f, dict) else f for f in data.get("findings", [])],
            evidence=[EvidenceItem.from_dict(e) if isinstance(e, dict) else e for e in data.get("evidence", [])],
            contradictions=[ResearchContradiction.from_dict(c) if isinstance(c, dict) else c for c in data.get("contradictions", [])],
            knowledge_gaps=[ResearchKnowledgeGap.from_dict(g) if isinstance(g, dict) else g for g in data.get("knowledge_gaps", [])],
            recommendations=[ResearchRecommendation.from_dict(r) if isinstance(r, dict) else r for r in data.get("recommendations", [])],
            crawler_reports=[CrawlerReport.from_dict(cr) if isinstance(cr, dict) else cr for cr in data.get("crawler_reports", [])],
            report_artifact_id=data.get("report_artifact_id"),
            report_path=data.get("report_path"),
            evidence_ids=list(data.get("evidence_ids", [])),
            summary_for_manager=str(data.get("summary_for_manager", "")),
            total_crawlers_spawned=int(data.get("total_crawlers_spawned", 0)),
            total_tasks_executed=int(data.get("total_tasks_executed", 0)),
            cost_estimate=float(data.get("cost_estimate", 0.0)),
            correlation_id=data.get("correlation_id", str(uuid.uuid4())),
            created_at=data.get("created_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )

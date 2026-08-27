from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any, Optional
import uuid

from workers.researcher.types import (
    FactClassification,
    ResearchConfidence,
    ResearchMode,
    ResearchQuestionStatus,
    SourceType,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class Source:
    """Normalized, deduplicated representation of a research source."""
    source_id: str
    title: str
    url_or_ref: str
    publisher: str = ""
    source_type: SourceType = SourceType.OTHER
    accessed_at: str = field(default_factory=utc_now)
    publication_date: Optional[str] = None
    relevance_score: float = 1.0
    reliability_score: float = 1.0
    content_snippet: str = ""
    content_checksum: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "url_or_ref": self.url_or_ref,
            "publisher": self.publisher,
            "source_type": self.source_type.value if isinstance(self.source_type, SourceType) else self.source_type,
            "accessed_at": self.accessed_at,
            "publication_date": self.publication_date,
            "relevance_score": self.relevance_score,
            "reliability_score": self.reliability_score,
            "content_snippet": self.content_snippet,
            "content_checksum": self.content_checksum,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Source:
        st_raw = data.get("source_type", "OTHER")
        try:
            st = SourceType(st_raw)
        except (ValueError, TypeError):
            st = SourceType.OTHER
        return cls(
            source_id=data["source_id"],
            title=data.get("title", ""),
            url_or_ref=data.get("url_or_ref", ""),
            publisher=data.get("publisher", ""),
            source_type=st,
            accessed_at=data.get("accessed_at", utc_now()),
            publication_date=data.get("publication_date"),
            relevance_score=float(data.get("relevance_score", 1.0)),
            reliability_score=float(data.get("reliability_score", 1.0)),
            content_snippet=data.get("content_snippet", ""),
            content_checksum=data.get("content_checksum", ""),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ResearchQuestion:
    """An individual decomposed research question being actively tracked."""
    question_id: str
    question_text: str
    status: ResearchQuestionStatus = ResearchQuestionStatus.UNANSWERED
    target_source_types: list[SourceType] = field(default_factory=list)
    answered_findings: list[str] = field(default_factory=list)  # finding_ids
    evidence_ids: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question_text": self.question_text,
            "status": self.status.value if isinstance(self.status, ResearchQuestionStatus) else self.status,
            "target_source_types": [
                s.value if isinstance(s, SourceType) else s for s in self.target_source_types
            ],
            "answered_findings": self.answered_findings,
            "evidence_ids": self.evidence_ids,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchQuestion:
        st_raw = data.get("status", "UNANSWERED")
        try:
            status = ResearchQuestionStatus(st_raw)
        except (ValueError, TypeError):
            status = ResearchQuestionStatus.UNANSWERED

        target_sources: list[SourceType] = []
        for s in data.get("target_source_types", []):
            try:
                target_sources.append(SourceType(s))
            except (ValueError, TypeError):
                target_sources.append(SourceType.OTHER)

        return cls(
            question_id=data["question_id"],
            question_text=data.get("question_text", ""),
            status=status,
            target_source_types=target_sources,
            answered_findings=list(data.get("answered_findings", [])),
            evidence_ids=list(data.get("evidence_ids", [])),
            notes=str(data.get("notes", "")),
        )


@dataclass
class ResearchFinding:
    """A distinct, evidence-backed finding with strict epistemic classification."""
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
            "classification": self.classification.value if isinstance(self.classification, FactClassification) else self.classification,
            "confidence": self.confidence.value if isinstance(self.confidence, ResearchConfidence) else self.confidence,
            "source_ids": self.source_ids,
            "evidence_ids": self.evidence_ids,
            "corroborating_source_ids": self.corroborating_source_ids,
            "conflicting_source_ids": self.conflicting_source_ids,
            "reasoning": self.reasoning,
            "project_implications": self.project_implications,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchFinding:
        c_raw = data.get("classification", "FACT")
        try:
            classification = FactClassification(c_raw)
        except (ValueError, TypeError):
            classification = FactClassification.FACT

        conf_raw = data.get("confidence", "SUPPORTED")
        try:
            confidence = ResearchConfidence(conf_raw)
        except (ValueError, TypeError):
            confidence = ResearchConfidence.SUPPORTED

        return cls(
            finding_id=data["finding_id"],
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
    """Explicitly identified contradiction or conflicting claims between sources."""
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
            "sources_a": self.sources_a,
            "claim_b": self.claim_b,
            "sources_b": self.sources_b,
            "analysis": self.analysis,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchContradiction:
        return cls(
            contradiction_id=data["contradiction_id"],
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
            gap_id=data["gap_id"],
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
            "supporting_finding_ids": self.supporting_finding_ids,
            "risks": self.risks,
            "tradeoffs": self.tradeoffs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchRecommendation:
        return cls(
            recommendation_id=data["recommendation_id"],
            action=data.get("action", ""),
            rationale=data.get("rationale", ""),
            supporting_finding_ids=list(data.get("supporting_finding_ids", [])),
            risks=list(data.get("risks", [])),
            tradeoffs=list(data.get("tradeoffs", [])),
        )


@dataclass
class ResearchScope:
    """Resource constraints, domain boundaries, and freshness requirements."""
    allowed_domains: list[str] = field(default_factory=list)
    excluded_domains: list[str] = field(default_factory=list)
    preferred_source_types: list[SourceType] = field(default_factory=list)
    recency_days: Optional[int] = None
    max_searches: int = 6
    max_fetches: int = 10
    max_sources: int = 15
    max_inference_calls: int = 5
    cost_limit: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_domains": self.allowed_domains,
            "excluded_domains": self.excluded_domains,
            "preferred_source_types": [
                s.value if isinstance(s, SourceType) else s for s in self.preferred_source_types
            ],
            "recency_days": self.recency_days,
            "max_searches": self.max_searches,
            "max_fetches": self.max_fetches,
            "max_sources": self.max_sources,
            "max_inference_calls": self.max_inference_calls,
            "cost_limit": self.cost_limit,
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
            max_searches=int(data.get("max_searches", 6)),
            max_fetches=int(data.get("max_fetches", 10)),
            max_sources=int(data.get("max_sources", 15)),
            max_inference_calls=int(data.get("max_inference_calls", 5)),
            cost_limit=float(data.get("cost_limit", 1.0)),
        )


@dataclass
class ResearchTaskSpec:
    """Normalized, machine-parsed specification of an incoming research task."""
    objective: str
    mode: ResearchMode = ResearchMode.STANDARD
    scope: ResearchScope = field(default_factory=ResearchScope)
    questions: list[ResearchQuestion] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    required_output_format: str = "markdown_report"
    preferred_style: str = "analytical"
    raw_task_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "mode": self.mode.value if isinstance(self.mode, ResearchMode) else self.mode,
            "scope": self.scope.to_dict(),
            "questions": [q.to_dict() for q in self.questions],
            "constraints": self.constraints,
            "required_output_format": self.required_output_format,
            "preferred_style": self.preferred_style,
            "raw_task_metadata": self.raw_task_metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchTaskSpec:
        m_raw = data.get("mode", "STANDARD")
        try:
            mode = ResearchMode(m_raw)
        except (ValueError, TypeError):
            mode = ResearchMode.STANDARD

        return cls(
            objective=data.get("objective", ""),
            mode=mode,
            scope=ResearchScope.from_dict(data.get("scope", {})),
            questions=[ResearchQuestion.from_dict(q) for q in data.get("questions", [])],
            constraints=list(data.get("constraints", [])),
            required_output_format=str(data.get("required_output_format", "markdown_report")),
            preferred_style=str(data.get("preferred_style", "analytical")),
            raw_task_metadata=dict(data.get("raw_task_metadata", {})),
        )


@dataclass
class ResearchPlan:
    """Internal step-by-step plan created by the Researcher."""
    plan_id: str
    objective: str
    mode: ResearchMode
    scope: ResearchScope
    questions: list[ResearchQuestion]
    planned_steps: list[str] = field(default_factory=list)
    current_step_index: int = 0
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "objective": self.objective,
            "mode": self.mode.value if isinstance(self.mode, ResearchMode) else self.mode,
            "scope": self.scope.to_dict(),
            "questions": [q.to_dict() for q in self.questions],
            "planned_steps": self.planned_steps,
            "current_step_index": self.current_step_index,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchPlan:
        m_raw = data.get("mode", "STANDARD")
        try:
            mode = ResearchMode(m_raw)
        except (ValueError, TypeError):
            mode = ResearchMode.STANDARD

        return cls(
            plan_id=data["plan_id"],
            objective=data.get("objective", ""),
            mode=mode,
            scope=ResearchScope.from_dict(data.get("scope", {})),
            questions=[ResearchQuestion.from_dict(q) for q in data.get("questions", [])],
            planned_steps=list(data.get("planned_steps", [])),
            current_step_index=int(data.get("current_step_index", 0)),
            created_at=data.get("created_at", utc_now()),
        )


@dataclass
class ResearchResult:
    """Structured, machine-consumable package produced upon research completion."""
    task_id: str
    project_id: str
    objective: str
    mode: ResearchMode
    plan: ResearchPlan
    questions: list[ResearchQuestion] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    findings: list[ResearchFinding] = field(default_factory=list)
    contradictions: list[ResearchContradiction] = field(default_factory=list)
    knowledge_gaps: list[ResearchKnowledgeGap] = field(default_factory=list)
    recommendations: list[ResearchRecommendation] = field(default_factory=list)
    report_artifact_id: Optional[str] = None
    report_path: Optional[str] = None
    evidence_ids: list[str] = field(default_factory=list)
    summary_for_manager: str = ""
    total_searches: int = 0
    total_fetches: int = 0
    cost_estimate: float = 0.0
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "objective": self.objective,
            "mode": self.mode.value if isinstance(self.mode, ResearchMode) else self.mode,
            "plan": self.plan.to_dict(),
            "questions": [q.to_dict() for q in self.questions],
            "sources": [s.to_dict() for s in self.sources],
            "findings": [f.to_dict() for f in self.findings],
            "contradictions": [c.to_dict() for c in self.contradictions],
            "knowledge_gaps": [g.to_dict() for g in self.knowledge_gaps],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "report_artifact_id": self.report_artifact_id,
            "report_path": self.report_path,
            "evidence_ids": self.evidence_ids,
            "summary_for_manager": self.summary_for_manager,
            "total_searches": self.total_searches,
            "total_fetches": self.total_fetches,
            "cost_estimate": self.cost_estimate,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchResult:
        m_raw = data.get("mode", "STANDARD")
        try:
            mode = ResearchMode(m_raw)
        except (ValueError, TypeError):
            mode = ResearchMode.STANDARD

        return cls(
            task_id=data["task_id"],
            project_id=data["project_id"],
            objective=data.get("objective", ""),
            mode=mode,
            plan=ResearchPlan.from_dict(data["plan"]),
            questions=[ResearchQuestion.from_dict(q) for q in data.get("questions", [])],
            sources=[Source.from_dict(s) for s in data.get("sources", [])],
            findings=[ResearchFinding.from_dict(f) for f in data.get("findings", [])],
            contradictions=[ResearchContradiction.from_dict(c) for c in data.get("contradictions", [])],
            knowledge_gaps=[ResearchKnowledgeGap.from_dict(g) for g in data.get("knowledge_gaps", [])],
            recommendations=[ResearchRecommendation.from_dict(r) for r in data.get("recommendations", [])],
            report_artifact_id=data.get("report_artifact_id"),
            report_path=data.get("report_path"),
            evidence_ids=list(data.get("evidence_ids", [])),
            summary_for_manager=str(data.get("summary_for_manager", "")),
            total_searches=int(data.get("total_searches", 0)),
            total_fetches=int(data.get("total_fetches", 0)),
            cost_estimate=float(data.get("cost_estimate", 0.0)),
            created_at=data.get("created_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )

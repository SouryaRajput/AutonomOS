from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.research.contracts.crawler_report import CrawlerReport
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import EvidenceItem, Source
from core.research.contracts.plan import ResearchPlan
from core.research.contracts.question import ResearchQuestion
from core.research.contracts.request import ResearchRequest
from core.research.contracts.result import (
    ResearchContradiction,
    ResearchFinding,
    ResearchKnowledgeGap,
    ResearchRecommendation,
)
from core.research.state.lifecycle import ResearchStateMachine
from core.research.types import (
    CrawlerReportStatus,
    CrawlerTaskStatus,
    EvidenceSufficiency,
    ResearchLifecycleState,
    ResearchQuestionStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StateTransitionRecord:
    """Record of a historical state transition."""
    from_state: str
    to_state: str
    reason: str
    timestamp: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_state": self.from_state,
            "to_state": self.to_state,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateTransitionRecord:
        return cls(
            from_state=data.get("from_state", ""),
            to_state=data.get("to_state", ""),
            reason=data.get("reason", ""),
            timestamp=data.get("timestamp", utc_now()),
        )


@dataclass
class ResearchState:
    """
    Authoritative operational state container tracking an ongoing research lifecycle.
    Encapsulates the request, plan, questions, crawler tasks, active crawlers,
    received reports, evidence pool, findings, and deterministic state transitions.
    """
    request: ResearchRequest
    current_state: ResearchLifecycleState = ResearchLifecycleState.RESEARCH_REQUESTED
    plan: Optional[ResearchPlan] = None
    questions: dict[str, ResearchQuestion] = field(default_factory=dict)
    crawler_tasks: dict[str, CrawlerTask] = field(default_factory=dict)
    active_crawlers: dict[str, str] = field(default_factory=dict)
    received_reports: list[CrawlerReport] = field(default_factory=list)
    evidence_pool: list[EvidenceItem] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    findings: list[ResearchFinding] = field(default_factory=list)
    contradictions: list[ResearchContradiction] = field(default_factory=list)
    knowledge_gaps: list[ResearchKnowledgeGap] = field(default_factory=list)
    recommendations: list[ResearchRecommendation] = field(default_factory=list)
    state_history: list[StateTransitionRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 2
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def transition_to(self, target_state: ResearchLifecycleState, reason: str = "") -> None:
        """
        Deterministically validate and transition the research state.
        Updates state history and timestamp.
        """
        ResearchStateMachine.validate_transition(self.current_state, target_state, reason=reason)
        record = StateTransitionRecord(
            from_state=self.current_state.value,
            to_state=target_state.value,
            reason=reason,
            timestamp=utc_now(),
        )
        self.state_history.append(record)
        self.current_state = target_state
        self.updated_at = utc_now()

    def add_question(self, question: ResearchQuestion) -> None:
        """Register a decomposed question into the active research state."""
        question.request_id = self.request.request_id
        if self.plan:
            question.plan_id = self.plan.plan_id
        self.questions[question.question_id] = question
        self.updated_at = utc_now()

    def add_crawler_task(self, task: CrawlerTask) -> None:
        """Register a crawler task into the active research state."""
        task.request_id = self.request.request_id
        if self.plan:
            task.plan_id = self.plan.plan_id
        self.crawler_tasks[task.task_id] = task
        if task.question_id in self.questions:
            if task.task_id not in self.questions[task.question_id].assigned_crawler_task_ids:
                self.questions[task.question_id].assigned_crawler_task_ids.append(task.task_id)
        self.updated_at = utc_now()

    def record_crawler_report(self, report: CrawlerReport) -> None:
        """Record an incoming crawler execution report."""
        report.request_id = self.request.request_id
        self.received_reports.append(report)
        
        # Update corresponding task status
        if report.crawler_task_id in self.crawler_tasks:
            t = self.crawler_tasks[report.crawler_task_id]
            if report.status == CrawlerReportStatus.SUCCESS:
                t.status = CrawlerTaskStatus.COMPLETED
            elif report.status == CrawlerReportStatus.PARTIAL:
                t.status = CrawlerTaskStatus.COMPLETED
            elif report.status == CrawlerReportStatus.TIMED_OUT:
                t.status = CrawlerTaskStatus.TIMED_OUT
            else:
                t.status = CrawlerTaskStatus.FAILED
            t.completed_at = utc_now()
            t.error_message = report.error_message

        # Ingest extracted evidence items into evidence pool with deduplication
        existing_evidence_ids = {e.evidence_id for e in self.evidence_pool}
        for ev in report.extracted_evidence:
            if ev.evidence_id not in existing_evidence_ids:
                self.evidence_pool.append(ev)
                existing_evidence_ids.add(ev.evidence_id)
                # Link evidence to question
                if ev.provenance.question_id and ev.provenance.question_id in self.questions:
                    q = self.questions[ev.provenance.question_id]
                    if ev.evidence_id not in q.evidence_ids:
                        q.evidence_ids.append(ev.evidence_id)

        # Ingest raw sources into source pool
        existing_source_urls = {s.url_or_ref for s in self.sources}
        for raw_src in report.raw_sources:
            if raw_src.url_or_ref and raw_src.url_or_ref not in existing_source_urls:
                src_obj = Source(
                    source_id=f"src-{len(self.sources) + 1}",
                    title=raw_src.title or raw_src.url_or_ref,
                    url_or_ref=raw_src.url_or_ref,
                    publisher=raw_src.publisher,
                    source_type=raw_src.source_type,
                    content_snippet=raw_src.content_snippet,
                    content_checksum=raw_src.checksum,
                    metadata=raw_src.metadata,
                )
                self.sources.append(src_obj)
                existing_source_urls.add(raw_src.url_or_ref)

        self.updated_at = utc_now()

    def add_evidence(self, item: EvidenceItem) -> None:
        """Directly add a verified evidence item to the pool."""
        self.evidence_pool.append(item)
        if item.provenance.question_id and item.provenance.question_id in self.questions:
            q = self.questions[item.provenance.question_id]
            if item.evidence_id not in q.evidence_ids:
                q.evidence_ids.append(item.evidence_id)
        self.updated_at = utc_now()

    def has_sufficient_evidence(self) -> bool:
        """
        Determine whether all active questions have sufficient evidence backing them.
        Prevents assuming 'crawlers finished = research complete'.
        """
        if not self.questions:
            return len(self.evidence_pool) > 0
        min_ev = self.request.scope.min_evidence_per_question
        for q in self.questions.values():
            if len(q.evidence_ids) < min_ev:
                return False
        return True

    def is_terminal(self) -> bool:
        """Check if the state is in a terminal state."""
        return ResearchStateMachine.is_terminal(self.current_state)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "current_state": self.current_state.value if isinstance(self.current_state, ResearchLifecycleState) else str(self.current_state),
            "plan": self.plan.to_dict() if self.plan else None,
            "questions": {k: v.to_dict() for k, v in self.questions.items()},
            "crawler_tasks": {k: v.to_dict() for k, v in self.crawler_tasks.items()},
            "active_crawlers": self.active_crawlers,
            "received_reports_count": len(self.received_reports),
            "evidence_pool_count": len(self.evidence_pool),
            "sources_count": len(self.sources),
            "findings_count": len(self.findings),
            "contradictions_count": len(self.contradictions),
            "knowledge_gaps_count": len(self.knowledge_gaps),
            "recommendations_count": len(self.recommendations),
            "state_history": [s.to_dict() for s in self.state_history],
            "errors": self.errors,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchState:
        st_raw = data.get("current_state", ResearchLifecycleState.RESEARCH_REQUESTED.value)
        try:
            current_state = ResearchLifecycleState(st_raw)
        except (ValueError, TypeError):
            current_state = ResearchLifecycleState.RESEARCH_REQUESTED

        req_data = data.get("request", {})
        request = ResearchRequest.from_dict(req_data) if isinstance(req_data, dict) else req_data

        plan_data = data.get("plan")
        plan = ResearchPlan.from_dict(plan_data) if plan_data and isinstance(plan_data, dict) else plan_data

        questions = {
            k: ResearchQuestion.from_dict(v) if isinstance(v, dict) else v
            for k, v in data.get("questions", {}).items()
        }
        tasks = {
            k: CrawlerTask.from_dict(v) if isinstance(v, dict) else v
            for k, v in data.get("crawler_tasks", {}).items()
        }
        reports = [
            CrawlerReport.from_dict(r) if isinstance(r, dict) else r
            for r in data.get("received_reports", [])
        ]
        evidence = [
            EvidenceItem.from_dict(e) if isinstance(e, dict) else e
            for e in data.get("evidence_pool", [])
        ]
        sources = [
            Source.from_dict(s) if isinstance(s, dict) else s
            for s in data.get("sources", [])
        ]
        history = [
            StateTransitionRecord.from_dict(h) if isinstance(h, dict) else h
            for h in data.get("state_history", [])
        ]

        return cls(
            request=request,
            current_state=current_state,
            plan=plan,
            questions=questions,
            crawler_tasks=tasks,
            active_crawlers=dict(data.get("active_crawlers", {})),
            received_reports=reports,
            evidence_pool=evidence,
            sources=sources,
            findings=[ResearchFinding.from_dict(f) if isinstance(f, dict) else f for f in data.get("findings", [])],
            contradictions=[ResearchContradiction.from_dict(c) if isinstance(c, dict) else c for c in data.get("contradictions", [])],
            knowledge_gaps=[ResearchKnowledgeGap.from_dict(g) if isinstance(g, dict) else g for g in data.get("knowledge_gaps", [])],
            recommendations=[ResearchRecommendation.from_dict(r) if isinstance(r, dict) else r for r in data.get("recommendations", [])],
            state_history=history,
            errors=list(data.get("errors", [])),
            retry_count=int(data.get("retry_count", 0)),
            max_retries=int(data.get("max_retries", 2)),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
        )

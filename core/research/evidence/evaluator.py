from __future__ import annotations

import logging
import re
from typing import Optional
import uuid

from core.research.contracts.evidence import EvidenceItem
from core.research.contracts.question import ResearchQuestion
from core.research.contracts.result import (
    ResearchContradiction,
    ResearchFinding,
    ResearchKnowledgeGap,
)
from core.research.state.model import ResearchState
from core.research.types import (
    EvidenceSufficiency,
    FactClassification,
    ResearchConfidence,
    ResearchQuestionStatus,
    SourceType,
)

logger = logging.getLogger("AutonomOS.Research.EvidenceEvaluator")


class EvidenceEvaluator:
    """
    Evaluates evidence grounding, coverage, contradictions, and knowledge gaps.
    Enforces that research completion is determined by evidence adequacy, not merely crawler termination.
    """

    RELIABILITY_BASELINES: dict[SourceType, float] = {
        SourceType.OFFICIAL_DOCUMENTATION: 0.95,
        SourceType.OFFICIAL_ANNOUNCEMENT: 0.95,
        SourceType.PRIMARY_SOURCE: 0.90,
        SourceType.ACADEMIC: 0.90,
        SourceType.REPOSITORY: 0.85,
        SourceType.PROJECT_MEMORY: 0.85,
        SourceType.NEWS: 0.70,
        SourceType.COMMUNITY: 0.60,
        SourceType.BLOG: 0.60,
        SourceType.FORUM: 0.50,
        SourceType.OTHER: 0.50,
    }

    @classmethod
    def assess_source_reliability(cls, source_type: SourceType, url: str = "") -> float:
        """Assign baseline credibility score grounded in source authority."""
        base = cls.RELIABILITY_BASELINES.get(source_type, 0.50)
        u_lower = url.lower()
        if "github.com" in u_lower and "/releases" in u_lower:
            return min(1.0, base + 0.10)
        return base

    @classmethod
    def evaluate_coverage(cls, state: ResearchState) -> tuple[EvidenceSufficiency, list[ResearchKnowledgeGap]]:
        """
        Evaluate whether the accumulated evidence pool adequately answers all research questions.
        Updates question statuses and creates explicit ResearchKnowledgeGap entries for unanswered questions.
        """
        gaps: list[ResearchKnowledgeGap] = []
        min_ev = state.request.scope.min_evidence_per_question
        
        answered_count = 0
        total_questions = len(state.questions)

        if total_questions == 0:
            if len(state.evidence_pool) > 0:
                return EvidenceSufficiency.SUFFICIENT, []
            return EvidenceSufficiency.INSUFFICIENT, [
                ResearchKnowledgeGap(
                    gap_id=f"gap-{uuid.uuid4().hex[:6]}",
                    topic=state.request.objective,
                    question="No research questions were formulated",
                    reason="Request lacked questions and automatic decomposition yielded no sub-questions.",
                    impact="Cannot verify claims without grounded questions.",
                )
            ]

        for q in state.questions.values():
            linked_evidence = [e for e in state.evidence_pool if e.evidence_id in q.evidence_ids or e.provenance.question_id == q.question_id]
            
            if len(linked_evidence) >= min_ev:
                q.sufficiency = EvidenceSufficiency.SUFFICIENT
                q.status = ResearchQuestionStatus.ANSWERED
                answered_count += 1
            elif len(linked_evidence) > 0:
                q.sufficiency = EvidenceSufficiency.PARTIAL
                q.status = ResearchQuestionStatus.PARTIALLY_ANSWERED
                gaps.append(
                    ResearchKnowledgeGap(
                        gap_id=f"gap-{q.question_id}",
                        topic=q.question_text[:50],
                        question=q.question_text,
                        reason=f"Only {len(linked_evidence)}/{min_ev} required evidence items gathered.",
                        impact="Confidence is limited; findings for this question remain provisional.",
                    )
                )
            else:
                q.sufficiency = EvidenceSufficiency.INSUFFICIENT
                q.status = ResearchQuestionStatus.UNKNOWN
                gaps.append(
                    ResearchKnowledgeGap(
                        gap_id=f"gap-{q.question_id}",
                        topic=q.question_text[:50],
                        question=q.question_text,
                        reason="No reliable evidence gathered from any crawler source.",
                        impact="Unanswered question. Downstream decisions must not assume positive answers.",
                    )
                )

        state.knowledge_gaps = gaps

        if answered_count == total_questions:
            return EvidenceSufficiency.SUFFICIENT, gaps
        elif answered_count > 0:
            return EvidenceSufficiency.PARTIAL, gaps
        else:
            return EvidenceSufficiency.INSUFFICIENT, gaps

    @classmethod
    def detect_contradictions(
        cls,
        findings: list[ResearchFinding],
        evidence: Optional[list[EvidenceItem]] = None,
    ) -> list[ResearchContradiction]:
        """
        Analyze findings and evidence to identify cross-source contradictions.
        Preserves opposing claims without hallucinating false certainty.
        """
        contradictions: list[ResearchContradiction] = []

        for f in findings:
            if f.confidence == ResearchConfidence.CONFLICTING or len(f.conflicting_source_ids) > 0:
                contra_id = f"c-{f.finding_id}"
                c = ResearchContradiction(
                    contradiction_id=contra_id,
                    topic=f.claim[:50],
                    claim_a=f.claim,
                    sources_a=list(f.source_ids),
                    claim_b=f.reasoning or "Opposing claim in conflicting sources",
                    sources_b=list(f.conflicting_source_ids),
                    analysis=f"Contradiction identified between primary sources {f.source_ids} and conflicting sources {f.conflicting_source_ids}.",
                )
                contradictions.append(c)

        return contradictions

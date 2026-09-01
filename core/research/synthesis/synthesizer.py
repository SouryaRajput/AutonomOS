from __future__ import annotations

import logging
import re
from typing import Optional
import uuid

from core.research.contracts.evidence import EvidenceItem, Source
from core.research.contracts.result import (
    ResearchContradiction,
    ResearchFinding,
    ResearchKnowledgeGap,
    ResearchRecommendation,
    ResearchResult,
)
from core.research.state.model import ResearchState
from core.research.types import (
    FactClassification,
    ResearchConfidence,
    ResearchResultStatus,
)

logger = logging.getLogger("AutonomOS.Research.Synthesizer")


class ResearchSynthesizer:
    """
    Synthesizes evaluated evidence into a structured ResearchResult, human-readable Markdown report,
    and a concise summary for the Manager.
    Distinguishes between verified results, partial results, and insufficient evidence.
    """

    @classmethod
    def synthesize_result(
        cls,
        state: ResearchState,
        report_artifact_id: Optional[str] = None,
        report_path: Optional[str] = None,
    ) -> ResearchResult:
        """
        Assemble the final ResearchResult from the evaluated state.
        """
        # Determine overall result status
        if not state.evidence_pool and not state.findings:
            result_status = ResearchResultStatus.INSUFFICIENT_EVIDENCE
        elif state.contradictions:
            result_status = ResearchResultStatus.CONFLICTING
        elif state.knowledge_gaps:
            result_status = ResearchResultStatus.PARTIAL
        else:
            result_status = ResearchResultStatus.VERIFIED

        # Generate findings from evidence if findings list is empty (fallback deterministic synthesis)
        findings = list(state.findings)
        if not findings and state.evidence_pool:
            for i, ev in enumerate(state.evidence_pool):
                src_id = f"src-{i+1}"
                f_obj = ResearchFinding(
                    finding_id=f"f-{i+1}",
                    claim=ev.extracted_fact,
                    classification=ev.classification,
                    confidence=ev.confidence,
                    source_ids=[src_id],
                    evidence_ids=[ev.evidence_id],
                    reasoning=f"Extracted by crawler from source reference: {ev.provenance.source_ref}",
                )
                findings.append(f_obj)

        summary_text = cls.generate_manager_summary(
            objective=state.request.objective,
            status=result_status,
            findings=findings,
            contradictions=state.contradictions,
            knowledge_gaps=state.knowledge_gaps,
            recommendations=state.recommendations,
            sources_count=len(state.sources),
        )

        return ResearchResult(
            task_id=state.request.task_id,
            request_id=state.request.request_id,
            project_id=state.request.project_id,
            objective=state.request.objective,
            mode=state.request.mode,
            status=result_status,
            plan=state.plan,
            questions=list(state.questions.values()),
            sources=list(state.sources),
            findings=findings,
            evidence=list(state.evidence_pool),
            contradictions=list(state.contradictions),
            knowledge_gaps=list(state.knowledge_gaps),
            recommendations=list(state.recommendations),
            crawler_reports=list(state.received_reports),
            report_artifact_id=report_artifact_id,
            report_path=report_path,
            evidence_ids=[e.evidence_id for e in state.evidence_pool],
            summary_for_manager=summary_text,
            total_crawlers_spawned=len(state.active_crawlers) or (state.plan.allocated_crawler_count if state.plan else 1),
            total_tasks_executed=len(state.received_reports),
            correlation_id=state.request.correlation_id,
            metadata={
                "state_transitions_count": len(state.state_history),
                "retry_count": state.retry_count,
            },
        )

    @classmethod
    def generate_manager_summary(
        cls,
        objective: str,
        status: ResearchResultStatus,
        findings: list[ResearchFinding],
        contradictions: list[ResearchContradiction],
        knowledge_gaps: list[ResearchKnowledgeGap],
        recommendations: list[ResearchRecommendation],
        sources_count: int,
    ) -> str:
        """Construct a concise, high-signal summary formatted specifically for Manager consumption."""
        lines = [f"**Research Summary: {objective}** (Status: {status.value})", ""]

        if status == ResearchResultStatus.INSUFFICIENT_EVIDENCE:
            lines.append("⚠️ **Insufficient Evidence**: Crawlers were unable to obtain verified evidence for this objective.")
            for g in knowledge_gaps:
                lines.append(f"- **Gap**: {g.question} — Reason: {g.reason}")
            return "\n".join(lines)

        if findings:
            lines.append("### Key Findings")
            for f in findings[:5]:
                conf_badge = f"[{f.confidence.value}]"
                lines.append(f"- {conf_badge} **{f.claim}**")
            lines.append("")

        if contradictions:
            lines.append("### ⚠️ Preserved Contradictions")
            for c in contradictions:
                lines.append(f"- **{c.topic}**: Claim A: '{c.claim_a}' vs Claim B: '{c.claim_b}'")
            lines.append("")

        if knowledge_gaps:
            lines.append("### Knowledge Gaps")
            for g in knowledge_gaps:
                lines.append(f"- **{g.question}**: {g.reason}")
            lines.append("")

        if recommendations:
            lines.append("### Recommendations")
            for r in recommendations:
                lines.append(f"- **{r.action}**: {r.rationale}")
            lines.append("")

        lines.append(f"*Sources consulted: {sources_count}*")
        return "\n".join(lines).strip()

    @classmethod
    def generate_markdown_report(cls, result: ResearchResult) -> str:
        """Generate a complete, structured Markdown artifact for human inspection."""
        lines = [
            f"# Research Report: {result.objective}",
            "",
            f"- **Status**: `{result.status.value}`",
            f"- **Mode**: `{result.mode.value}`",
            f"- **Task ID**: `{result.task_id}`",
            f"- **Request ID**: `{result.request_id}`",
            f"- **Generated At**: `{result.created_at}`",
            f"- **Crawlers Spawned**: `{result.total_crawlers_spawned}` | **Tasks Executed**: `{result.total_tasks_executed}`",
            "",
            "## Executive Summary",
            result.summary_for_manager,
            "",
        ]

        if result.questions:
            lines.append("## Investigated Questions")
            for q in result.questions:
                lines.append(f"- **{q.question_text}** — `{q.status.value}` ({q.sufficiency.value})")
            lines.append("")

        if result.findings:
            lines.append("## Evidence-Backed Findings")
            for f in result.findings:
                lines.append(f"### Finding `{f.finding_id}`: {f.claim}")
                lines.append(f"- **Classification**: `{f.classification.value}`")
                lines.append(f"- **Confidence**: `{f.confidence.value}`")
                lines.append(f"- **Sources**: {', '.join(f.source_ids) or 'None'}")
                if f.reasoning:
                    lines.append(f"- **Reasoning**: {f.reasoning}")
                if f.project_implications:
                    lines.append(f"- **Project Implications**: {f.project_implications}")
                lines.append("")

        if result.contradictions:
            lines.append("## Identified Contradictions")
            for c in result.contradictions:
                lines.append(f"### `{c.contradiction_id}`: {c.topic}")
                lines.append(f"- **Claim A**: {c.claim_a} (Sources: {c.sources_a})")
                lines.append(f"- **Claim B**: {c.claim_b} (Sources: {c.sources_b})")
                if c.analysis:
                    lines.append(f"- **Analysis**: {c.analysis}")
                lines.append("")

        if result.knowledge_gaps:
            lines.append("## Known Gaps & Uncertainties")
            for g in result.knowledge_gaps:
                lines.append(f"- **{g.topic}**: {g.question}")
                lines.append(f"  - *Reason*: {g.reason}")
                if g.impact:
                    lines.append(f"  - *Impact*: {g.impact}")
            lines.append("")

        if result.recommendations:
            lines.append("## Strategic Recommendations")
            for r in result.recommendations:
                lines.append(f"### Proposal: {r.action}")
                lines.append(f"- **Rationale**: {r.rationale}")
                if r.risks:
                    lines.append(f"- **Risks**: {', '.join(r.risks)}")
                if r.tradeoffs:
                    lines.append(f"- **Tradeoffs**: {', '.join(r.tradeoffs)}")
                lines.append("")

        if result.sources:
            lines.append("## Sources & Provenance")
            for s in result.sources:
                lines.append(f"- `[{s.source_id}]` **{s.title}** (`{s.source_type.value}`)")
                if s.url_or_ref:
                    lines.append(f"  - Ref: {s.url_or_ref}")
                if s.content_checksum:
                    lines.append(f"  - SHA-256: `{s.content_checksum[:16]}...`")

        return "\n".join(lines).strip()

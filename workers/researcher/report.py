from __future__ import annotations

from workers.researcher.model import ResearchResult
from workers.researcher.types import FactClassification, ResearchConfidence


class ResearchReportGenerator:
    """
    Generates human-readable Markdown research reports with explicit fact
    classifications, contradiction analysis, knowledge gaps, and source citation tables.
    """

    @classmethod
    def generate_markdown_report(cls, result: ResearchResult) -> str:
        lines: list[str] = []

        lines.append(f"# Research Report: {result.objective}")
        lines.append(f"**Research Mode**: `{result.mode.value}` | **Generated**: `{result.created_at}`")
        lines.append(f"**Sources Analyzed**: `{len(result.sources)}` | **Findings Identified**: `{len(result.findings)}`")
        lines.append("")

        # 1. Executive Summary
        lines.append("## 1. Executive Summary")
        if result.summary_for_manager:
            lines.append(result.summary_for_manager)
        else:
            lines.append("Investigation completed across gathered literature and project memory.")
        lines.append("")

        # 2. Research Questions & Status
        lines.append("## 2. Research Questions & Status")
        lines.append("| ID | Question | Status | Findings |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for q in result.questions:
            q_status = q.status.value if hasattr(q.status, "value") else str(q.status)
            q_findings = ", ".join(q.answered_findings) if q.answered_findings else "None"
            lines.append(f"| `{q.question_id}` | {q.question_text} | `{q_status}` | {q_findings} |")
        lines.append("")

        # 3. Detailed Findings
        lines.append("## 3. Key Findings")
        if not result.findings:
            lines.append("No specific findings were synthesized.")
        else:
            for f in result.findings:
                class_str = f.classification.value if hasattr(f.classification, "value") else str(f.classification)
                conf_str = f.confidence.value if hasattr(f.confidence, "value") else str(f.confidence)
                src_str = ", ".join([f"`{s}`" for s in f.source_ids]) if f.source_ids else "None"
                
                lines.append(f"### [{class_str}] {f.claim}")
                lines.append(f"- **Finding ID**: `{f.finding_id}`")
                lines.append(f"- **Confidence**: `{conf_str}`")
                lines.append(f"- **Supporting Sources**: {src_str}")
                if f.corroborating_source_ids:
                    corrob_str = ", ".join([f"`{s}`" for s in f.corroborating_source_ids])
                    lines.append(f"- **Corroborating Sources**: {corrob_str}")
                if f.conflicting_source_ids:
                    conflict_str = ", ".join([f"`{s}`" for s in f.conflicting_source_ids])
                    lines.append(f"- **Conflicting Sources**: {conflict_str}")
                if f.reasoning:
                    lines.append(f"- **Evidence & Rationale**: {f.reasoning}")
                if f.project_implications:
                    lines.append(f"- **Project Implications**: {f.project_implications}")
                lines.append("")

        # 4. Contradiction Analysis
        if result.contradictions:
            lines.append("## 4. Cross-Source Contradiction Analysis")
            for c in result.contradictions:
                lines.append(f"### Contradiction: {c.topic}")
                lines.append(f"- **Claim A**: {c.claim_a} (Sources: {', '.join(c.sources_a)})")
                lines.append(f"- **Claim B**: {c.claim_b} (Sources: {', '.join(c.sources_b)})")
                lines.append(f"- **Analysis**: {c.analysis}")
                lines.append("")

        # 5. Knowledge Gaps & Uncertainties
        if result.knowledge_gaps:
            lines.append("## 5. Knowledge Gaps & Limitations")
            for g in result.knowledge_gaps:
                lines.append(f"- **{g.topic}** (`{g.gap_id}`): {g.question}")
                lines.append(f"  - *Reason*: {g.reason}")
                if g.impact:
                    lines.append(f"  - *Impact*: {g.impact}")
            lines.append("")

        # 6. Recommendations
        if result.recommendations:
            lines.append("## 6. Strategic Recommendations")
            lines.append("> [!NOTE]")
            lines.append("> Recommendations represent actionable advice derived from evidence and project goals, explicitly separated from verified facts.")
            lines.append("")
            for r in result.recommendations:
                lines.append(f"### Recommendation: {r.action}")
                lines.append(f"- **Rationale**: {r.rationale}")
                if r.supporting_finding_ids:
                    supp_str = ", ".join([f"`{f}`" for f in r.supporting_finding_ids])
                    lines.append(f"- **Supporting Findings**: {supp_str}")
                if r.risks:
                    lines.append(f"- **Identified Risks**: {', '.join(r.risks)}")
                if r.tradeoffs:
                    lines.append(f"- **Tradeoffs**: {', '.join(r.tradeoffs)}")
                lines.append("")

        # 7. Sources & Citations Table
        lines.append("## 7. Sources & Citations")
        if not result.sources:
            lines.append("No external sources recorded.")
        else:
            lines.append("| ID | Source Title | Type | URL / Reference | Reliability |")
            lines.append("| :--- | :--- | :--- | :--- | :--- |")
            for s in result.sources:
                st_str = s.source_type.value if hasattr(s.source_type, "value") else str(s.source_type)
                lines.append(f"| `{s.source_id}` | {s.title} | `{st_str}` | {s.url_or_ref} | {s.reliability_score:.2f} |")
        lines.append("")

        return "\n".join(lines)

    @classmethod
    def generate_manager_summary(cls, result: ResearchResult) -> str:
        """
        Produce a concise 3-6 bullet summary for Manager handoff without loading
        the entire research report into downstream reasoning context.
        """
        bullets: list[str] = []
        bullets.append(f"Research completed for objective: '{result.objective}'")
        bullets.append(f"Synthesized {len(result.findings)} findings from {len(result.sources)} sources.")

        # Top fact / finding
        facts = [f for f in result.findings if f.classification == FactClassification.FACT]
        claims = [f for f in result.findings if f.classification == FactClassification.SOURCE_CLAIM]
        top_findings = facts[:2] or claims[:2]
        for f in top_findings:
            bullets.append(f"Key Finding [{f.classification.value}]: {f.claim}")

        # Contradictions or Knowledge Gaps
        if result.contradictions:
            bullets.append(f"Warning: {len(result.contradictions)} contradiction(s) detected between sources.")
        if result.knowledge_gaps:
            bullets.append(f"Uncertainty: {len(result.knowledge_gaps)} knowledge gap(s) identified.")

        # Top Recommendation
        if result.recommendations:
            bullets.append(f"Recommendation: {result.recommendations[0].action}")

        if result.report_path:
            bullets.append(f"Full report available at artifact `{result.report_path}`.")

        return "\n".join([f"- {b}" for b in bullets])

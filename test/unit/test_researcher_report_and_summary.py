from __future__ import annotations

import unittest

from workers.researcher.model import (
    ResearchContradiction,
    ResearchFinding,
    ResearchKnowledgeGap,
    ResearchPlan,
    ResearchQuestion,
    ResearchRecommendation,
    ResearchResult,
    ResearchScope,
    Source,
)
from workers.researcher.report import ResearchReportGenerator
from workers.researcher.types import (
    FactClassification,
    ResearchConfidence,
    ResearchMode,
    ResearchQuestionStatus,
    SourceType,
)


class TestResearcherReportAndSummary(unittest.TestCase):
    """Unit tests for Markdown report formatting and Manager concise summaries."""

    def setUp(self):
        self.source = Source(
            source_id="src-1",
            title="PostgreSQL 16 Release Notes",
            url_or_ref="https://www.postgresql.org/docs/16/release-16.html",
            source_type=SourceType.OFFICIAL_DOCUMENTATION,
            reliability_score=0.95,
        )
        self.question = ResearchQuestion(
            question_id="q-1",
            question_text="Does PostgreSQL 16 support bidirectional replication?",
            status=ResearchQuestionStatus.ANSWERED,
            answered_findings=["f-1"],
        )
        self.finding = ResearchFinding(
            finding_id="f-1",
            claim="PostgreSQL 16 introduced bidirectional logical replication support.",
            classification=FactClassification.FACT,
            confidence=ResearchConfidence.WELL_SUPPORTED,
            source_ids=["src-1"],
            reasoning="Official release notes explicitly list pg_createsubscriber and bidirectional replication.",
            project_implications="Enables active-active cross-region setup.",
        )
        self.recommendation = ResearchRecommendation(
            recommendation_id="rec-1",
            action="Upgrade PostgreSQL clusters to version 16.",
            rationale="Needed for cross-region replication feature.",
            supporting_finding_ids=["f-1"],
            risks=["Requires scheduled maintenance downtime during migration."],
        )
        self.plan = ResearchPlan(
            plan_id="plan-1",
            objective="Evaluate PostgreSQL 16 replication features",
            mode=ResearchMode.STANDARD,
            scope=ResearchScope(),
            questions=[self.question],
        )
        self.result = ResearchResult(
            task_id="t-1",
            project_id="p-1",
            objective="Evaluate PostgreSQL 16 replication features",
            mode=ResearchMode.STANDARD,
            plan=self.plan,
            questions=[self.question],
            sources=[self.source],
            findings=[self.finding],
            contradictions=[],
            knowledge_gaps=[],
            recommendations=[self.recommendation],
            report_artifact_id="art-rep-1",
            report_path="research/pg16_eval.md",
        )

    def test_markdown_report_structure(self):
        report = ResearchReportGenerator.generate_markdown_report(self.result)
        self.assertIn("# Research Report: Evaluate PostgreSQL 16 replication features", report)
        self.assertIn("## 1. Executive Summary", report)
        self.assertIn("## 2. Research Questions & Status", report)
        self.assertIn("## 3. Key Findings", report)
        self.assertIn("[FACT] PostgreSQL 16 introduced bidirectional logical replication support.", report)
        self.assertIn("## 6. Strategic Recommendations", report)
        self.assertIn("### Recommendation: Upgrade PostgreSQL clusters to version 16.", report)
        self.assertIn("## 7. Sources & Citations", report)
        self.assertIn("| `src-1` | PostgreSQL 16 Release Notes | `OFFICIAL_DOCUMENTATION`", report)

    def test_manager_summary_conciseness(self):
        summary = ResearchReportGenerator.generate_manager_summary(self.result)
        self.assertIn("Research completed for objective", summary)
        self.assertIn("Key Finding [FACT]: PostgreSQL 16 introduced bidirectional logical replication support.", summary)
        self.assertIn("Recommendation: Upgrade PostgreSQL clusters to version 16.", summary)
        self.assertIn("Full report available at artifact `research/pg16_eval.md`.", summary)
        self.assertTrue(len(summary.splitlines()) <= 8)


if __name__ == "__main__":
    unittest.main()

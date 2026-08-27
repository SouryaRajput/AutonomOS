from __future__ import annotations

import unittest

from workers.researcher.model import (
    ResearchContradiction,
    ResearchFinding,
    ResearchKnowledgeGap,
    ResearchPlan,
    ResearchRecommendation,
    ResearchQuestion,
    ResearchResult,
    ResearchScope,
    ResearchTaskSpec,
    Source,
)
from workers.researcher.types import (
    FactClassification,
    ResearchConfidence,
    ResearchMode,
    ResearchQuestionStatus,
    SourceType,
)


class TestResearcherModelsAndTypes(unittest.TestCase):
    """Unit tests for Researcher data models, schemas, and serialization."""

    def test_source_serialization(self):
        source = Source(
            source_id="src-1",
            title="FastAPI Official Docs",
            url_or_ref="https://fastapi.tiangolo.com/tutorial/",
            publisher="FastAPI",
            source_type=SourceType.OFFICIAL_DOCUMENTATION,
            relevance_score=0.95,
            reliability_score=0.95,
            content_snippet="FastAPI is a modern, fast web framework.",
            content_checksum="abc123hash",
        )
        data = source.to_dict()
        self.assertEqual(data["source_id"], "src-1")
        self.assertEqual(data["source_type"], "OFFICIAL_DOCUMENTATION")
        self.assertEqual(data["reliability_score"], 0.95)

        restored = Source.from_dict(data)
        self.assertEqual(restored.source_id, source.source_id)
        self.assertEqual(restored.source_type, SourceType.OFFICIAL_DOCUMENTATION)
        self.assertEqual(restored.content_checksum, "abc123hash")

    def test_research_finding_classification_and_confidence(self):
        finding = ResearchFinding(
            finding_id="f-1",
            claim="PostgreSQL 16 supports bi-directional logical replication.",
            classification=FactClassification.FACT,
            confidence=ResearchConfidence.WELL_SUPPORTED,
            source_ids=["src-1", "src-2"],
            corroborating_source_ids=["src-2"],
            reasoning="Documented in official release notes and confirmed in architectural overview.",
            project_implications="Can be used for multi-region replication.",
        )
        data = finding.to_dict()
        self.assertEqual(data["classification"], "FACT")
        self.assertEqual(data["confidence"], "WELL_SUPPORTED")

        restored = ResearchFinding.from_dict(data)
        self.assertEqual(restored.classification, FactClassification.FACT)
        self.assertEqual(restored.confidence, ResearchConfidence.WELL_SUPPORTED)
        self.assertEqual(len(restored.source_ids), 2)

    def test_research_contradiction_and_knowledge_gap(self):
        contra = ResearchContradiction(
            contradiction_id="c-1",
            topic="SQLite Concurrency Limit",
            claim_a="SQLite supports concurrent WAL readers and writers.",
            sources_a=["src-a"],
            claim_b="SQLite allows only one active writer at a time.",
            sources_b=["src-b"],
            analysis="WAL mode allows multiple concurrent readers with one writer, not concurrent writers.",
        )
        data = contra.to_dict()
        restored = ResearchContradiction.from_dict(data)
        self.assertEqual(restored.contradiction_id, "c-1")
        self.assertEqual(len(restored.sources_a), 1)

        gap = ResearchKnowledgeGap(
            gap_id="gap-1",
            topic="Redis Clustering Latency in Staging",
            question="What is the p99 latency under 50k ops/sec?",
            reason="Benchmark metrics unavailable in public docs.",
            impact="Requires local load test before production deployment.",
        )
        gap_data = gap.to_dict()
        restored_gap = ResearchKnowledgeGap.from_dict(gap_data)
        self.assertEqual(restored_gap.gap_id, "gap-1")
        self.assertIn("Benchmark metrics", restored_gap.reason)

    def test_research_plan_and_result_roundtrip(self):
        scope = ResearchScope(
            allowed_domains=["fastapi.tiangolo.com", "docs.python.org"],
            max_searches=4,
            max_fetches=8,
        )
        q1 = ResearchQuestion(
            question_id="q-1",
            question_text="Does framework X support async streaming?",
            status=ResearchQuestionStatus.ANSWERED,
            target_source_types=[SourceType.OFFICIAL_DOCUMENTATION],
            answered_findings=["f-1"],
        )
        plan = ResearchPlan(
            plan_id="plan-001",
            objective="Evaluate Python async frameworks",
            mode=ResearchMode.STANDARD,
            scope=scope,
            questions=[q1],
            planned_steps=["1. Gather docs", "2. Synthesize"],
        )

        result = ResearchResult(
            task_id="task-123",
            project_id="proj-456",
            objective="Evaluate Python async frameworks",
            mode=ResearchMode.STANDARD,
            plan=plan,
            questions=[q1],
            sources=[],
            findings=[],
            contradictions=[],
            knowledge_gaps=[],
            recommendations=[
                ResearchRecommendation(
                    recommendation_id="rec-1",
                    action="Adopt FastAPI for async streaming endpoints.",
                    rationale="High throughput, built-in OpenAPI schema.",
                )
            ],
            report_artifact_id="art-report-01",
            report_path="research/fastapi_eval.md",
            summary_for_manager="FastAPI is recommended for async streaming.",
        )

        res_data = result.to_dict()
        self.assertEqual(res_data["mode"], "STANDARD")
        self.assertEqual(res_data["report_artifact_id"], "art-report-01")

        restored_res = ResearchResult.from_dict(res_data)
        self.assertEqual(restored_res.task_id, "task-123")
        self.assertEqual(len(restored_res.recommendations), 1)
        self.assertEqual(restored_res.recommendations[0].action, "Adopt FastAPI for async streaming endpoints.")


if __name__ == "__main__":
    unittest.main()

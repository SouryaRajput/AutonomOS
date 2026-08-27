from __future__ import annotations

import unittest

from workers.researcher.evaluator import SourceEvaluator
from workers.researcher.model import (
    ResearchFinding,
    ResearchPlan,
    ResearchQuestion,
    ResearchRecommendation,
    ResearchResult,
    ResearchScope,
    Source,
)
from workers.researcher.types import (
    FactClassification,
    ResearchConfidence,
    ResearchMode,
    ResearchQuestionStatus,
    SourceType,
)


class TestResearcherSourceDedupAndEvaluator(unittest.TestCase):
    """Unit tests for URL normalization, deduplication, domain checks, and result validation."""

    def test_normalize_url_strips_tracking_and_slashes(self):
        url1 = "https://www.docs.python.org/3/library/asyncio.html/?utm_source=twitter&utm_medium=social"
        url2 = "http://docs.python.org/3/library/asyncio.html"
        norm1 = SourceEvaluator.normalize_url(url1)
        norm2 = SourceEvaluator.normalize_url(url2)
        self.assertEqual(norm1, norm2)
        self.assertEqual(norm1, "//docs.python.org/3/library/asyncio.html")

    def test_is_domain_allowed_and_excluded(self):
        self.assertTrue(
            SourceEvaluator.is_domain_allowed(
                "https://docs.python.org/3/",
                allowed_domains=["python.org"],
                excluded_domains=["spam.com"],
            )
        )
        self.assertFalse(
            SourceEvaluator.is_domain_allowed(
                "https://spam.com/article",
                allowed_domains=[],
                excluded_domains=["spam.com"],
            )
        )
        self.assertFalse(
            SourceEvaluator.is_domain_allowed(
                "https://unrelated.org/news",
                allowed_domains=["python.org"],
                excluded_domains=[],
            )
        )

    def test_deduplicate_sources_removes_duplicate_urls_and_titles(self):
        candidates = [
            {"title": "Python Asyncio Docs", "url": "https://docs.python.org/3/library/asyncio.html?utm_source=1"},
            {"title": "Python Asyncio Docs", "url": "https://docs.python.org/3/library/asyncio.html"},
            {"title": "FastAPI Guide", "url": "https://fastapi.tiangolo.com/"},
        ]
        deduped = SourceEvaluator.deduplicate_sources(candidates)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["title"], "Python Asyncio Docs")
        self.assertEqual(deduped[1]["title"], "FastAPI Guide")

    def test_classify_source_type_and_reliability(self):
        st_docs = SourceEvaluator.classify_source_type("https://fastapi.tiangolo.com/docs/tutorial/")
        self.assertEqual(st_docs, SourceType.OFFICIAL_DOCUMENTATION)
        rel_docs = SourceEvaluator.assess_source_reliability(st_docs)
        self.assertEqual(rel_docs, 0.95)

        st_forum = SourceEvaluator.classify_source_type("https://stackoverflow.com/questions/12345")
        self.assertEqual(st_forum, SourceType.FORUM)
        rel_forum = SourceEvaluator.assess_source_reliability(st_forum)
        self.assertEqual(rel_forum, 0.45)

    def test_validate_research_result_flags_missing_fields_and_unknown_sources(self):
        src1 = Source(source_id="src-1", title="Doc 1", url_or_ref="https://example.com")
        plan = ResearchPlan(
            plan_id="p-1",
            objective="Test objective",
            mode=ResearchMode.STANDARD,
            scope=ResearchScope(),
            questions=[],
        )
        result = ResearchResult(
            task_id="t-1",
            project_id="p-1",
            objective="Test objective",
            mode=ResearchMode.STANDARD,
            plan=plan,
            sources=[src1],
            findings=[
                ResearchFinding(
                    finding_id="f-1",
                    claim="Valid claim",
                    source_ids=["src-999"],  # Non-existent source
                )
            ],
            recommendations=[ResearchRecommendation(recommendation_id="r-1", action="Do X", rationale="Because Y")],
            report_artifact_id="art-1",
            summary_for_manager="Summary here",
        )

        valid, errors = SourceEvaluator.validate_research_result(result)
        self.assertFalse(valid)
        self.assertTrue(any("unknown source src-999" in err for err in errors))


if __name__ == "__main__":
    unittest.main()

"""
End-to-End Integration and Golden Path Test for Web Search Capability (Phase 1 / Part 2 / Step 7).

Verifies the complete flow:
Manager -> ResearchRequest -> Researcher -> ResearchPlan -> CrawlerTask(WEB_SEARCH)
-> CrawlerSpawner -> CrawlerRegistry -> CrawlerSupervisor -> WebSearchCrawler
-> SearchProvider -> CrawlerReport -> Researcher -> ResearchResult

Also covers the real-world user scenario:
"Research the latest technologies that can make this portfolio more animated and 3D."
"""
from __future__ import annotations

import unittest

from core.research.contracts.crawler_report import CrawlerReport
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.plan import ResearchPlan
from core.research.contracts.request import ResearchRequest, ResearchScope
from core.research.contracts.result import ResearchResult
from core.research.crawler.registry import CrawlerRegistry
from core.research.crawler.web_search import WebSearchCrawler
from core.research.orchestration.spawner import CrawlerSpawner
from core.research.orchestration.supervisor import CrawlerSupervisor
from core.research.researcher import Researcher
from core.research.search.config import SearchConfig
from core.research.search.test_provider import TestSearchProvider
from core.research.types import (
    CrawlerCapability,
    CrawlerReportStatus,
    ResearchLifecycleState,
    ResearchResultStatus,
)


class TestWebSearchEndToEndGoldenPath(unittest.TestCase):

    def setUp(self):
        self.registry = CrawlerRegistry()
        self.spawner = CrawlerSpawner(registry=self.registry)
        self.supervisor = CrawlerSupervisor()
        self.researcher = Researcher(
            registry=self.registry,
            spawner=self.spawner,
            supervisor=self.supervisor,
        )

        self.test_provider = TestSearchProvider()
        # Preload structured search fixtures
        self.test_provider.set_fixture(
            "portfolio 3d animation threejs gsap spline",
            [
                {
                    "title": "Three.js and WebGL Interactive Experiences",
                    "url": "https://threejs.org/docs/introduction/Animation-system",
                    "snippet": "Three.js provides GPU-accelerated 3D scenes, shaders, and physics animations for modern web portfolios.",
                    "published_date": "2026-01-15T00:00:00Z",
                },
                {
                    "title": "GSAP ScrollTrigger for 3D Portfolios",
                    "url": "https://greensock.com/scrolltrigger/",
                    "snippet": "ScrollTrigger coordinates 3D camera sweeps and canvas timeline animations seamlessly on scroll.",
                    "published_date": "2026-02-01T00:00:00Z",
                },
                {
                    "title": "Spline 3D Real-time Web Embeds",
                    "url": "https://spline.design/features",
                    "snippet": "Export interactive 3D assets and vector animations directly into web frontends.",
                    "published_date": None,
                },
            ],
        )

    # 1. Deterministic End-to-End Pipeline Test
    def test_01_deterministic_end_to_end_pipeline(self):
        # Step 1: Create ResearchRequest requiring WEB_SEARCH
        request = ResearchRequest(
            request_id="req-e2e-search-01",
            project_id="proj-e2e-01",
            task_id="task-e2e-01",
            objective="Identify top libraries and frameworks for 3D interactive web portfolios.",
            scope=ResearchScope(
                max_crawlers=2,
                max_sources=5,
                timeout_seconds=30,
            ),
        )

        # Step 2: Execute entire 13-stage deterministic research pipeline
        result, state = self.researcher.execute_research(request)

        # Step 3: Verify complete lifecycle execution
        self.assertIsInstance(result, ResearchResult)
        self.assertEqual(result.request_id, request.request_id)
        self.assertIn(result.status, (ResearchResultStatus.VERIFIED, ResearchResultStatus.PARTIAL))
        self.assertEqual(state.current_state, ResearchLifecycleState.COMPLETE)

        # Step 4: Verify CrawlerTasks and CrawlerReports
        self.assertTrue(len(state.crawler_tasks) >= 1)
        self.assertTrue(len(state.received_reports) >= 1)

        for task in state.crawler_tasks.values():
            self.assertEqual(task.request_id, request.request_id)
            self.assertEqual(task.required_capability, CrawlerCapability.WEB_SEARCH)

        for report in state.received_reports:
            self.assertEqual(report.status, CrawlerReportStatus.SUCCESS)
            self.assertEqual(report.request_id, request.request_id)
            self.assertTrue(len(report.raw_sources) >= 1)
            self.assertTrue(len(report.extracted_evidence) >= 1)

            # Check raw source checksums and lineage
            for src in report.raw_sources:
                self.assertTrue(src.checksum)
                self.assertEqual(src.publisher, src.publisher.lower())

        # Step 5: Verify findings and synthesis report
        self.assertTrue(len(result.findings) >= 1)
        self.assertTrue(len(result.summary_for_manager) >= 1)

    # 2. Real User Scenario Test
    def test_02_portfolio_animated_3d_user_scenario(self):
        user_query = "Research the latest technologies that can make this portfolio more animated and 3D."

        request = ResearchRequest(
            request_id="req-portfolio-3d-01",
            project_id="proj-p-01",
            task_id="task-p-01",
            objective=user_query,
            scope=ResearchScope(max_crawlers=2, max_sources=5),
        )

        result, state = self.researcher.execute_research(request)
        self.assertIn(result.status, (ResearchResultStatus.VERIFIED, ResearchResultStatus.PARTIAL))
        self.assertEqual(state.current_state, ResearchLifecycleState.COMPLETE)

        # Check that evidence captures 3D animation insights
        self.assertTrue(len(state.evidence_pool) >= 1)
        evidence_text = " ".join([ev.content_snippet for ev in state.evidence_pool])
        self.assertTrue(
            "three" in evidence_text.lower() or "3d" in evidence_text.lower() or "animation" in evidence_text.lower() or "portfolio" in evidence_text.lower(),
            f"Expected 3D animation terms in evidence, got: {evidence_text}",
        )


if __name__ == "__main__":
    unittest.main()

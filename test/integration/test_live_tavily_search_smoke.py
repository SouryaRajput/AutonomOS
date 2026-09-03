"""
Opt-in Live Provider Smoke Test for Tavily Search Provider (Phase 1 / Part 2 / Step 7).

IMPORTANT:
This test is OPT-IN only and will be SKIPPED during normal repository and CI test runs.
To execute live testing against the real external Tavily API endpoint:
    export RUN_LIVE_SEARCH_TESTS=1
    export TAVILY_API_KEY="tvly-your-real-key"
    python3 -m unittest test.integration.test_live_tavily_search_smoke -v
"""
from __future__ import annotations

import os
import unittest

from core.research.contracts.crawler_task import CrawlerTask
from core.research.crawler.web_search import WebSearchCrawler
from core.research.search.config import SearchConfig
from core.research.search.providers.tavily import TavilySearchProvider
from core.research.types import CrawlerCapability, CrawlerReportStatus


class TestLiveTavilySearchSmoke(unittest.TestCase):
    """
    Live external integration test for Tavily Search Provider.
    Guaranteed to be skipped unless explicitly opted-in via RUN_LIVE_SEARCH_TESTS=1.
    """

    def setUp(self):
        self.is_opted_in = os.getenv("RUN_LIVE_SEARCH_TESTS") == "1"
        self.api_key = os.getenv("TAVILY_API_KEY") or os.getenv("SEARCH_API_KEY")

        if not self.is_opted_in:
            self.skipTest(
                "Skipping live search smoke test. To enable, set RUN_LIVE_SEARCH_TESTS=1 and TAVILY_API_KEY='...'"
            )

        if not self.api_key:
            self.fail(
                "RUN_LIVE_SEARCH_TESTS=1 was specified, but TAVILY_API_KEY (or SEARCH_API_KEY) environment variable is missing."
            )

        self.config = SearchConfig(
            provider_type="tavily",
            api_key=self.api_key,
            timeout_seconds=15.0,
            default_limit=3,
        )
        self.provider = TavilySearchProvider(config=self.config)
        self.crawler = WebSearchCrawler(
            crawler_id="crawler.live_smoke.01",
            provider=self.provider,
            config=self.config,
        )

    def test_live_search_query_execution(self):
        task = CrawlerTask(
            task_id="ctask-live-01",
            request_id="req-live-01",
            plan_id="plan-live-01",
            question_id="q-live-01",
            query_or_target="WebGL Three.js physics engine",
            required_capability=CrawlerCapability.WEB_SEARCH,
            parameters={"limit": 2},
        )

        report = self.crawler.execute_crawler_task(task)
        self.assertEqual(report.status, CrawlerReportStatus.SUCCESS)
        self.assertTrue(len(report.raw_sources) >= 1)
        self.assertTrue(len(report.extracted_evidence) >= 1)

        # Assert no secrets in report
        self.assertNotIn(self.api_key, str(report.to_dict()))

        # Assert provenance
        self.assertEqual(report.request_id, "req-live-01")
        self.assertEqual(report.crawler_task_id, "ctask-live-01")
        self.assertEqual(report.metadata.get("provider"), "tavily")


if __name__ == "__main__":
    unittest.main()

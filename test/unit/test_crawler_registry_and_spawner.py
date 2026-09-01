from __future__ import annotations

import unittest

from core.research.contracts.plan import ResearchPlan
from core.research.contracts.question import ResearchQuestion
from core.research.contracts.request import ResearchScope
from core.research.contracts.crawler_task import CrawlerTask
from core.research.crawler.base import BaseCrawler
from core.research.crawler.registry import CrawlerRegistry
from core.research.orchestration.spawner import CrawlerSpawner
from core.research.types import CrawlerCapability, CrawlerReportStatus, ResearchMode
from workers.crawler.worker import CrawlerWorker


class DummyCustomCrawler(BaseCrawler):
    """Custom crawler implementation for testing registration."""
    def __init__(self, crawler_id: str):
        super().__init__(
            crawler_id=crawler_id,
            capabilities=[CrawlerCapability.REPOSITORY_INSPECTION, CrawlerCapability.API_QUERY],
        )

    def execute_crawler_task(self, task, context=None):
        from core.research.contracts.crawler_report import CrawlerReport
        return CrawlerReport(
            report_id=f"crep-{task.task_id}",
            crawler_task_id=task.task_id,
            crawler_id=self.crawler_id,
            request_id=task.request_id,
            status=CrawlerReportStatus.SUCCESS,
            summary=f"Custom crawler executed {task.query_or_target}",
        )


class TestCrawlerRegistryAndSpawner(unittest.TestCase):
    """Unit tests for CrawlerRegistry, dynamic CrawlerSpawner scaling (1..N), and capability routing."""

    def setUp(self):
        self.registry = CrawlerRegistry()
        self.spawner = CrawlerSpawner(self.registry)

    def test_crawler_registration_and_capability_lookup(self):
        crawler_web = CrawlerWorker(crawler_id="crawler.web.test", capabilities=[CrawlerCapability.WEB_SEARCH])
        crawler_custom = DummyCustomCrawler(crawler_id="crawler.custom.test")

        self.registry.register_crawler_instance(crawler_web)
        self.registry.register_crawler_instance(crawler_custom)

        # Lookup by ID
        self.assertEqual(self.registry.get_crawler("crawler.web.test").id, "crawler.web.test")
        self.assertEqual(self.registry.get_crawler("crawler.custom.test").id, "crawler.custom.test")

        # Lookup by capability
        web_crawlers = self.registry.get_crawlers_for_capability(CrawlerCapability.WEB_SEARCH)
        self.assertEqual(len(web_crawlers), 1)
        self.assertEqual(web_crawlers[0].id, "crawler.web.test")

        repo_crawlers = self.registry.get_crawlers_for_capability(CrawlerCapability.REPOSITORY_INSPECTION)
        self.assertEqual(len(repo_crawlers), 1)
        self.assertEqual(repo_crawlers[0].id, "crawler.custom.test")

        # List all capabilities
        all_caps = self.registry.list_available_capabilities()
        self.assertIn(CrawlerCapability.WEB_SEARCH, all_caps)
        self.assertIn(CrawlerCapability.REPOSITORY_INSPECTION, all_caps)

    def test_dynamic_spawner_scales_single_crawler(self):
        """Test dynamic scaling: 1 research request -> 1 crawler."""
        q1 = ResearchQuestion(
            question_id="q-1",
            question_text="What is WebAssembly?",
            required_capabilities=[CrawlerCapability.WEB_SEARCH],
        )
        plan = ResearchPlan(
            plan_id="plan-1",
            request_id="req-1",
            objective="Analyze WebAssembly basics",
            mode=ResearchMode.QUICK,
            scope=ResearchScope(max_crawlers=1),
            questions=[q1],
        )

        crawlers = self.spawner.spawn_crawlers_for_plan(plan)
        self.assertEqual(len(crawlers), 1)
        self.assertEqual(plan.allocated_crawler_count, 1)
        self.assertTrue(crawlers[0].has_capability(CrawlerCapability.WEB_SEARCH))

    def test_dynamic_spawner_scales_multiple_crawlers(self):
        """Test dynamic scaling: 1 research request -> N crawlers (e.g. 5 crawlers)."""
        q1 = ResearchQuestion(question_id="q-1", question_text="Web doc", required_capabilities=[CrawlerCapability.WEB_SEARCH])
        q2 = ResearchQuestion(question_id="q-2", question_text="Fetch page", required_capabilities=[CrawlerCapability.WEB_FETCH])
        q3 = ResearchQuestion(question_id="q-3", question_text="Repo code", required_capabilities=[CrawlerCapability.REPOSITORY_INSPECTION])
        q4 = ResearchQuestion(question_id="q-4", question_text="API query", required_capabilities=[CrawlerCapability.API_QUERY])
        q5 = ResearchQuestion(question_id="q-5", question_text="Memory lookup", required_capabilities=[CrawlerCapability.PROJECT_MEMORY_LOOKUP])

        tasks = [
            CrawlerTask(task_id=f"t-{i}", request_id="req-multi", plan_id="p-multi", question_id=f"q-{i}", query_or_target=f"target {i}", required_capability=cap)
            for i, cap in enumerate([
                CrawlerCapability.WEB_SEARCH,
                CrawlerCapability.WEB_FETCH,
                CrawlerCapability.REPOSITORY_INSPECTION,
                CrawlerCapability.API_QUERY,
                CrawlerCapability.PROJECT_MEMORY_LOOKUP,
            ], start=1)
        ]

        plan = ResearchPlan(
            plan_id="plan-multi",
            request_id="req-multi",
            objective="Multi-domain investigation",
            mode=ResearchMode.DEEP,
            scope=ResearchScope(max_crawlers=5),
            questions=[q1, q2, q3, q4, q5],
            crawler_tasks=tasks,
        )

        crawlers = self.spawner.spawn_crawlers_for_plan(plan)
        self.assertEqual(len(crawlers), 5)
        self.assertEqual(plan.allocated_crawler_count, 5)

        # Verify all 5 distinct crawler IDs are registered and unique
        crawler_ids = {c.id for c in crawlers}
        self.assertEqual(len(crawler_ids), 5)

    def test_crawler_worker_task_execution(self):
        crawler = CrawlerWorker(crawler_id="crawler.worker.exec.1")
        task = CrawlerTask(
            task_id="ctask-exec-1",
            request_id="req-exec-1",
            plan_id="plan-exec-1",
            question_id="q-exec-1",
            query_or_target="sqlite WAL concurrency",
            required_capability=CrawlerCapability.WEB_SEARCH,
        )

        report = crawler.execute_crawler_task(task)
        self.assertEqual(report.crawler_task_id, "ctask-exec-1")
        self.assertEqual(report.request_id, "req-exec-1")
        self.assertEqual(report.status, CrawlerReportStatus.SUCCESS)
        self.assertTrue(len(report.extracted_evidence) >= 1)
        self.assertEqual(report.extracted_evidence[0].provenance.request_id, "req-exec-1")
        self.assertEqual(report.extracted_evidence[0].provenance.crawler_id, "crawler.worker.exec.1")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.request import ResearchRequest, ResearchScope
from core.research.crawler.mock_crawler import MockCrawler
from core.research.crawler.registry import CrawlerRegistry
from core.research.orchestration.spawner import CrawlerSpawner
from core.research.orchestration.supervisor import CrawlerSupervisor
from core.research.state.model import ResearchState
from core.research.types import (
    CrawlerCapability,
    CrawlerHealthStatus,
    CrawlerReportStatus,
    CrawlerStatus,
    CrawlerTaskStatus,
)


class TestCrawlerWorkforceOrchestration(unittest.TestCase):
    """
    Unit tests for Crawler Workforce dynamic allocation, 1..N scaling,
    cancellation, dynamic replacement, and on-demand expansion.
    """

    def setUp(self):
        self.registry = CrawlerRegistry()
        self.spawner = CrawlerSpawner(self.registry)
        self.supervisor = CrawlerSupervisor()
        self.request = ResearchRequest(
            request_id="req-wf-001",
            project_id="proj-wf-1",
            task_id="t-wf-1",
            objective="Evaluate vector database architectures",
        )
        self.state = ResearchState(request=self.request)

    def test_spawn_single_crawler_and_execute(self):
        """Test spawning 1 crawler dynamically and executing a task."""
        crawler = self.spawner.spawn_crawler(capabilities=[CrawlerCapability.WEB_SEARCH])
        self.assertEqual(len(self.registry.list_active_crawlers()), 1)
        self.assertTrue(crawler.has_capability(CrawlerCapability.WEB_SEARCH))

        task = CrawlerTask(
            task_id="task-single-1",
            request_id="req-wf-001",
            plan_id="plan-1",
            question_id="q-1",
            query_or_target="sqlite WAL concurrency",
            required_capability=CrawlerCapability.WEB_SEARCH,
        )

        report = self.supervisor.execute_task(crawler, task)
        self.assertEqual(report.status, CrawlerReportStatus.SUCCESS)
        self.assertEqual(task.status, CrawlerTaskStatus.COMPLETED)
        self.assertEqual(crawler.tasks_completed, 1)

    def test_spawn_three_concurrent_crawlers(self):
        """Test spawning 3 crawlers and distributing 3 tasks concurrently."""
        c1 = self.spawner.spawn_crawler(capabilities=[CrawlerCapability.WEB_SEARCH], name="Crawler-1")
        c2 = self.spawner.spawn_crawler(capabilities=[CrawlerCapability.WEB_FETCH], name="Crawler-2")
        c3 = self.spawner.spawn_crawler(capabilities=[CrawlerCapability.API_QUERY], name="Crawler-3")

        self.assertEqual(len(self.registry.list_active_crawlers()), 3)

        tasks = [
            CrawlerTask(task_id="t-1", request_id="req-wf-001", plan_id="p-1", question_id="q-1", query_or_target="q1 search", required_capability=CrawlerCapability.WEB_SEARCH),
            CrawlerTask(task_id="t-2", request_id="req-wf-001", plan_id="p-1", question_id="q-2", query_or_target="q2 fetch", required_capability=CrawlerCapability.WEB_FETCH),
            CrawlerTask(task_id="t-3", request_id="req-wf-001", plan_id="p-1", question_id="q-3", query_or_target="q3 api", required_capability=CrawlerCapability.API_QUERY),
        ]

        reports = self.supervisor.assign_and_execute_all(
            crawlers=[c1, c2, c3],
            tasks=tasks,
            state=self.state,
        )

        self.assertEqual(len(reports), 3)
        for rep in reports:
            self.assertEqual(rep.status, CrawlerReportStatus.SUCCESS)
            self.assertEqual(rep.request_id, "req-wf-001")

        self.assertEqual(len(self.state.received_reports), 3)
        self.assertTrue(len(self.state.evidence_pool) >= 3)

    def test_spawn_five_crawlers_with_capability_matching(self):
        """Test spawning 5 crawlers and verifying capability-based matching."""
        caps = [
            CrawlerCapability.WEB_SEARCH,
            CrawlerCapability.WEB_FETCH,
            CrawlerCapability.DOCUMENT_SCRAPING,
            CrawlerCapability.API_QUERY,
            CrawlerCapability.REPOSITORY_INSPECTION,
        ]
        crawlers = [self.spawner.spawn_crawler(capabilities=[cap]) for cap in caps]
        self.assertEqual(len(crawlers), 5)
        self.assertEqual(len(self.registry.list_active_crawlers()), 5)

        # Test finding eligible crawlers by capability
        repo_crawlers = self.registry.find_eligible_crawlers([CrawlerCapability.REPOSITORY_INSPECTION])
        self.assertEqual(len(repo_crawlers), 1)
        self.assertEqual(repo_crawlers[0].id, crawlers[4].id)

    def test_dynamic_expansion_spawn_additional_crawlers_later(self):
        """Test spawning additional crawlers later on-demand when evidence gaps arise."""
        # Initial workforce: 1 crawler
        initial_crawlers = [self.spawner.spawn_crawler(capabilities=[CrawlerCapability.WEB_SEARCH])]
        self.assertEqual(len(self.registry.list_active_crawlers()), 1)

        # Later, Researcher determines 2 additional crawlers are needed
        additional_crawlers = self.spawner.spawn_additional_crawlers(
            needed_capabilities=[CrawlerCapability.REPOSITORY_INSPECTION, CrawlerCapability.API_QUERY],
            count=2,
        )
        self.assertEqual(len(additional_crawlers), 2)
        self.assertEqual(len(self.registry.list_active_crawlers()), 3)

        # Verify all 3 crawlers are healthy and idle
        idle_crawlers = self.registry.list_idle_crawlers()
        self.assertEqual(len(idle_crawlers), 3)

    def test_dynamic_replacement_of_failed_crawler(self):
        """Test dynamic replacement: simulated failure -> detection -> spawn replacement -> retry."""
        # Register a failing crawler
        failing_crawler = MockCrawler(
            crawler_id="crawler.failing.sim",
            capabilities=[CrawlerCapability.WEB_SEARCH],
            simulate_failure=True,
            simulate_error_message="Connection reset by peer: 502 Bad Gateway",
        )
        self.registry.register_crawler_instance(failing_crawler)

        task = CrawlerTask(
            task_id="task-retry-1",
            request_id="req-wf-001",
            plan_id="plan-1",
            question_id="q-1",
            query_or_target="arrow flight sql benchmarks",
            required_capability=CrawlerCapability.WEB_SEARCH,
        )

        # 1. Initial attempt fails
        initial_report = self.supervisor.execute_task(failing_crawler, task)
        self.assertEqual(initial_report.status, CrawlerReportStatus.FAILED)
        self.assertEqual(task.status, CrawlerTaskStatus.FAILED)
        self.assertIn("502 Bad Gateway", initial_report.error_message)

        # 2. Dynamic replacement triggered
        replacement_report = self.supervisor.replace_failed_crawler_and_retry(
            failed_crawler=failing_crawler,
            task=task,
            spawner=self.spawner,
            state=self.state,
        )

        # 3. Verify replacement succeeded
        self.assertEqual(replacement_report.status, CrawlerReportStatus.SUCCESS)
        self.assertEqual(task.status, CrawlerTaskStatus.COMPLETED)
        self.assertNotEqual(replacement_report.crawler_id, "crawler.failing.sim")
        self.assertTrue(len(replacement_report.extracted_evidence) >= 1)

    def test_task_cancellation_during_execution(self):
        """Test cancelling a task and verifying crawler and report transition to CANCELLED."""
        crawler = MockCrawler(
            crawler_id="crawler.slow.mock",
            capabilities=[CrawlerCapability.WEB_SEARCH],
            execution_delay=0.05,
        )
        task = CrawlerTask(
            task_id="task-cancel-test",
            request_id="req-wf-001",
            plan_id="plan-1",
            question_id="q-1",
            query_or_target="long query",
            required_capability=CrawlerCapability.WEB_SEARCH,
        )

        # Pre-cancel task before dispatch
        task.cancel(reason="Coverage threshold reached early")
        report = self.supervisor.execute_task(crawler, task)

        self.assertEqual(task.status, CrawlerTaskStatus.CANCELLED)
        self.assertEqual(report.status, CrawlerReportStatus.FAILED)
        self.assertIn("Coverage threshold reached early", report.summary)

    def test_workforce_health_summary(self):
        """Test workforce registry and supervisor health aggregation."""
        self.spawner.spawn_crawler(capabilities=[CrawlerCapability.WEB_SEARCH])
        self.spawner.spawn_crawler(capabilities=[CrawlerCapability.WEB_FETCH])

        summary = self.registry.get_crawler_health_summary()
        self.assertEqual(summary["total_crawlers"], 2)
        self.assertEqual(summary["healthy_count"], 2)
        self.assertEqual(summary["busy_count"], 0)
        self.assertEqual(summary["idle_count"], 2)


if __name__ == "__main__":
    unittest.main()

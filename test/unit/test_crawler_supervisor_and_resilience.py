from __future__ import annotations

import unittest

from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.request import ResearchRequest
from core.research.crawler.base import BaseCrawler
from core.research.orchestration.supervisor import CrawlerSupervisor
from core.research.state.model import ResearchState
from core.research.types import CrawlerCapability, CrawlerReportStatus, CrawlerTaskStatus
from workers.crawler.worker import CrawlerWorker


class FailingCrawler(BaseCrawler):
    """Simulates a crawler that encounters an unhandled runtime error or crash."""
    def __init__(self, crawler_id: str):
        super().__init__(crawler_id=crawler_id, capabilities=[CrawlerCapability.WEB_SEARCH])

    def execute_crawler_task(self, task, context=None):
        raise ConnectionResetError("Remote host forcibly closed an existing connection")


class TestCrawlerSupervisorAndResilience(unittest.TestCase):
    """Unit tests for CrawlerSupervisor error isolation, failure handling, and task tracking."""

    def setUp(self):
        self.supervisor = CrawlerSupervisor()
        self.request = ResearchRequest(
            request_id="req-sup-1",
            project_id="proj-1",
            task_id="task-1",
            objective="Resilience test",
        )
        self.state = ResearchState(request=self.request)

    def test_supervisor_captures_crawler_failures_without_silent_disappearance(self):
        """Failed crawlers must not silently disappear. They must produce a failed CrawlerReport."""
        failing_crawler = FailingCrawler(crawler_id="crawler.failing.1")
        task = CrawlerTask(
            task_id="ctask-fail-1",
            request_id="req-sup-1",
            plan_id="plan-1",
            question_id="q-1",
            query_or_target="https://flaky-endpoint.org",
            required_capability=CrawlerCapability.WEB_SEARCH,
        )
        self.state.add_crawler_task(task)

        reports = self.supervisor.assign_and_execute_all(
            crawlers=[failing_crawler],
            tasks=[task],
            state=self.state,
        )

        self.assertEqual(len(reports), 1)
        rep = reports[0]
        self.assertEqual(rep.status, CrawlerReportStatus.FAILED)
        self.assertIn("Remote host forcibly closed", rep.error_message)
        self.assertEqual(rep.crawler_id, "crawler.failing.1")
        self.assertEqual(rep.crawler_task_id, "ctask-fail-1")

        # Verify task in state is marked FAILED
        self.assertEqual(self.state.crawler_tasks["ctask-fail-1"].status, CrawlerTaskStatus.FAILED)
        self.assertEqual(len(self.state.received_reports), 1)

    def test_supervisor_distributes_tasks_across_multiple_crawlers(self):
        c1 = CrawlerWorker(crawler_id="crawler.worker.1")
        c2 = CrawlerWorker(crawler_id="crawler.worker.2")

        tasks = [
            CrawlerTask(task_id=f"t-{i}", request_id="req-sup-1", plan_id="p-1", question_id=f"q-{i}", query_or_target=f"query {i}")
            for i in range(1, 5)
        ]
        for t in tasks:
            self.state.add_crawler_task(t)

        reports = self.supervisor.assign_and_execute_all(
            crawlers=[c1, c2],
            tasks=tasks,
            state=self.state,
        )

        self.assertEqual(len(reports), 4)
        c1_reports = [r for r in reports if r.crawler_id == "crawler.worker.1"]
        c2_reports = [r for r in reports if r.crawler_id == "crawler.worker.2"]

        # Tasks should be distributed across both crawlers
        self.assertEqual(len(c1_reports), 2)
        self.assertEqual(len(c2_reports), 2)
        self.assertEqual(len(self.state.received_reports), 4)


if __name__ == "__main__":
    unittest.main()

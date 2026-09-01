from __future__ import annotations

import unittest

from core.research.contracts.crawler_task import CrawlerTask
from core.research.crawler.lifecycle import CrawlerStateMachine
from core.research.crawler.mock_crawler import MockCrawler
from core.research.errors import InvalidStateTransitionError
from core.research.types import (
    CrawlerCapability,
    CrawlerHealthStatus,
    CrawlerReportStatus,
    CrawlerStatus,
    CrawlerTaskStatus,
)


class TestCrawlerLifecycleAndStateMachine(unittest.TestCase):
    """Unit tests for CrawlerStateMachine, MockCrawler lifecycle progression, health, and heartbeats."""

    def test_valid_crawler_lifecycle_transitions(self):
        """Test valid state transitions: CREATED -> QUEUED -> RUNNING -> COMPLETED -> QUEUED -> TERMINATED."""
        # Check transition validity directly on state machine
        self.assertTrue(CrawlerStateMachine.is_valid_transition(CrawlerStatus.CREATED, CrawlerStatus.QUEUED))
        self.assertTrue(CrawlerStateMachine.is_valid_transition(CrawlerStatus.QUEUED, CrawlerStatus.RUNNING))
        self.assertTrue(CrawlerStateMachine.is_valid_transition(CrawlerStatus.RUNNING, CrawlerStatus.COMPLETED))
        self.assertTrue(CrawlerStateMachine.is_valid_transition(CrawlerStatus.COMPLETED, CrawlerStatus.QUEUED))
        self.assertTrue(CrawlerStateMachine.is_valid_transition(CrawlerStatus.QUEUED, CrawlerStatus.TERMINATED))

        # Check failure and cancellation paths
        self.assertTrue(CrawlerStateMachine.is_valid_transition(CrawlerStatus.RUNNING, CrawlerStatus.FAILED))
        self.assertTrue(CrawlerStateMachine.is_valid_transition(CrawlerStatus.FAILED, CrawlerStatus.QUEUED))
        self.assertTrue(CrawlerStateMachine.is_valid_transition(CrawlerStatus.RUNNING, CrawlerStatus.CANCELLED))
        self.assertTrue(CrawlerStateMachine.is_valid_transition(CrawlerStatus.CANCELLED, CrawlerStatus.QUEUED))

    def test_invalid_crawler_lifecycle_transitions_raise_error(self):
        """Test invalid state jumps raise InvalidStateTransitionError."""
        # CREATED cannot jump directly to RUNNING without being QUEUED
        with self.assertRaises(InvalidStateTransitionError):
            CrawlerStateMachine.validate_transition(CrawlerStatus.CREATED, CrawlerStatus.RUNNING)

        # TERMINATED is terminal; cannot transition to anything
        with self.assertRaises(InvalidStateTransitionError):
            CrawlerStateMachine.validate_transition(CrawlerStatus.TERMINATED, CrawlerStatus.RUNNING)

        with self.assertRaises(InvalidStateTransitionError):
            CrawlerStateMachine.validate_transition(CrawlerStatus.TERMINATED, CrawlerStatus.QUEUED)

    def test_mock_crawler_golden_path_lifecycle(self):
        """Test MockCrawler progresses through CREATED -> QUEUED -> RUNNING -> COMPLETED."""
        crawler = MockCrawler(crawler_id="crawler.mock.golden")
        # Upon initialization, MockCrawler transitions to QUEUED
        self.assertEqual(crawler.status, CrawlerStatus.QUEUED)
        self.assertEqual(crawler.health, CrawlerHealthStatus.HEALTHY)

        task = CrawlerTask(
            task_id="ctask-mock-1",
            request_id="req-1",
            plan_id="plan-1",
            question_id="q-1",
            query_or_target="arrow flight sql",
            required_capability=CrawlerCapability.WEB_SEARCH,
        )

        report = crawler.execute_crawler_task(task)
        self.assertEqual(report.status, CrawlerReportStatus.SUCCESS)
        self.assertEqual(crawler.status, CrawlerStatus.COMPLETED)
        self.assertEqual(crawler.tasks_completed, 1)
        self.assertEqual(crawler.tasks_failed, 0)
        self.assertTrue(len(report.extracted_evidence) >= 1)

        # Resetting crawler puts it back in QUEUED ready for next task
        crawler.reset_status()
        self.assertEqual(crawler.status, CrawlerStatus.QUEUED)
        self.assertIsNone(crawler.current_task)

    def test_mock_crawler_simulated_failure_path(self):
        """Test MockCrawler failure progression: QUEUED -> RUNNING -> FAILED."""
        crawler = MockCrawler(
            crawler_id="crawler.mock.fail",
            simulate_failure=True,
            simulate_error_message="Host unreachable: 503 Gateway Timeout",
        )
        self.assertEqual(crawler.status, CrawlerStatus.QUEUED)

        task = CrawlerTask(
            task_id="ctask-mock-fail",
            request_id="req-1",
            plan_id="plan-1",
            question_id="q-1",
            query_or_target="unreachable domain",
            required_capability=CrawlerCapability.WEB_SEARCH,
        )

        report = crawler.execute_crawler_task(task)
        self.assertEqual(report.status, CrawlerReportStatus.FAILED)
        self.assertEqual(crawler.status, CrawlerStatus.FAILED)
        self.assertEqual(crawler.tasks_failed, 1)
        self.assertIn("503 Gateway Timeout", report.error_message)

    def test_mock_crawler_cancellation(self):
        """Test task cancellation transitions crawler to CANCELLED."""
        crawler = MockCrawler(crawler_id="crawler.mock.cancel")
        task = CrawlerTask(
            task_id="ctask-mock-cancel",
            request_id="req-1",
            plan_id="plan-1",
            question_id="q-1",
            query_or_target="long running search",
            required_capability=CrawlerCapability.WEB_SEARCH,
        )

        # Simulate cancellation before execution
        task.cancel(reason="User aborted request")
        report = crawler.execute_crawler_task(task)
        self.assertEqual(report.status, CrawlerReportStatus.FAILED)
        self.assertEqual(crawler.status, CrawlerStatus.CANCELLED)
        self.assertIn("User aborted request", report.summary)

    def test_crawler_heartbeat_and_termination(self):
        crawler = MockCrawler(crawler_id="crawler.mock.term")
        initial_hb = crawler.last_heartbeat
        new_hb = crawler.heartbeat()
        self.assertIsNotNone(new_hb)

        crawler.terminate()
        self.assertEqual(crawler.status, CrawlerStatus.TERMINATED)
        self.assertEqual(crawler.health, CrawlerHealthStatus.DEAD)
        self.assertTrue(CrawlerStateMachine.is_terminal(crawler.status))


if __name__ == "__main__":
    unittest.main()

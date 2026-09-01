from __future__ import annotations

import unittest

from core.research.contracts.request import ResearchRequest, ResearchScope
from core.research.researcher import Researcher
from core.research.types import (
    CrawlerCapability,
    ResearchLifecycleState,
    ResearchMode,
    ResearchResultStatus,
)


class TestResearcherOrchestratorFlow(unittest.TestCase):
    """Unit tests for the complete 13-stage deterministic Researcher orchestrator flow."""

    def setUp(self):
        self.researcher = Researcher()

    def test_full_orchestration_flow_and_lineage_graph(self):
        """Verify that a ResearchRequest executes through the full 13-stage lifecycle with unbroken lineage."""
        request = ResearchRequest(
            request_id="req-orch-100",
            project_id="proj-orch-1",
            task_id="task-orch-1",
            objective="Evaluate Arrow Flight SQL for distributed data engines",
            mode=ResearchMode.STANDARD,
            questions=[
                "What is Apache Arrow Flight SQL?",
                "Does Arrow Flight SQL support TLS and authentication?",
            ],
            scope=ResearchScope(max_crawlers=3, min_evidence_per_question=1),
        )

        result, state = self.researcher.execute_research(request)

        # 1. State machine completed
        self.assertEqual(state.current_state, ResearchLifecycleState.COMPLETE)
        self.assertTrue(state.is_terminal())

        # 2. Verify all major lifecycle states were traversed in order
        traversed_states = [h.to_state for h in state.state_history]
        self.assertIn(ResearchLifecycleState.UNDERSTANDING.value, traversed_states)
        self.assertIn(ResearchLifecycleState.PLANNING.value, traversed_states)
        self.assertIn(ResearchLifecycleState.CRAWLER_ALLOCATION.value, traversed_states)
        self.assertIn(ResearchLifecycleState.CRAWLERS_RUNNING.value, traversed_states)
        self.assertIn(ResearchLifecycleState.EVIDENCE_COLLECTION.value, traversed_states)
        self.assertIn(ResearchLifecycleState.EVIDENCE_EVALUATION.value, traversed_states)
        self.assertIn(ResearchLifecycleState.COVERAGE_CHECK.value, traversed_states)
        self.assertIn(ResearchLifecycleState.SYNTHESIS.value, traversed_states)
        self.assertIn(ResearchLifecycleState.RESEARCH_VERIFIED.value, traversed_states)
        self.assertIn(ResearchLifecycleState.RESULT_DELIVERED.value, traversed_states)
        self.assertIn(ResearchLifecycleState.COMPLETE.value, traversed_states)

        # 3. Lineage Graph Verification
        self.assertEqual(result.request_id, "req-orch-100")
        self.assertEqual(result.task_id, "task-orch-1")
        self.assertEqual(result.project_id, "proj-orch-1")
        self.assertIsNotNone(result.plan)
        self.assertEqual(result.plan.request_id, "req-orch-100")

        # Verify CrawlerTasks have parent lineage to request, plan, and questions
        self.assertTrue(len(result.plan.crawler_tasks) >= 2)
        question_ids = {q.question_id for q in result.questions}
        for task in result.plan.crawler_tasks:
            self.assertEqual(task.request_id, "req-orch-100")
            self.assertEqual(task.plan_id, result.plan.plan_id)
            self.assertIn(task.question_id, question_ids)

        # Verify CrawlerReports have lineage to tasks and request
        self.assertTrue(len(result.crawler_reports) >= 2)
        task_ids = {t.task_id for t in result.plan.crawler_tasks}
        for rep in result.crawler_reports:
            self.assertEqual(rep.request_id, "req-orch-100")
            self.assertIn(rep.crawler_task_id, task_ids)

        # Verify EvidenceItems retain full provenance
        self.assertTrue(len(result.evidence) >= 2)
        for ev in result.evidence:
            self.assertEqual(ev.provenance.request_id, "req-orch-100")
            self.assertIn(ev.provenance.crawler_task_id, task_ids)
            self.assertTrue(len(ev.checksum) > 0)

        # 4. Result validation
        self.assertEqual(result.status, ResearchResultStatus.VERIFIED)
        self.assertTrue(len(result.findings) >= 2)
        self.assertIn("Arrow Flight SQL", result.summary_for_manager)

    def test_dynamic_scaling_single_vs_multiple_crawlers(self):
        """Verify Researcher dynamically provisions 1 crawler for 1 question and N crawlers for multiple."""
        # 1. Single crawler case
        req_single = ResearchRequest(
            request_id="req-single",
            project_id="proj-1",
            task_id="t-1",
            objective="Check single library version",
            questions=["What is the latest pydantic version?"],
            scope=ResearchScope(max_crawlers=1),
        )
        res_single, state_single = self.researcher.execute_research(req_single)
        self.assertEqual(res_single.total_crawlers_spawned, 1)

        # 2. Multi-crawler case (3 questions with max_crawlers=3)
        req_multi = ResearchRequest(
            request_id="req-multi",
            project_id="proj-1",
            task_id="t-2",
            objective="Compare database engines",
            questions=[
                "What is PostgreSQL?",
                "What is ClickHouse?",
                "What is DuckDB?",
            ],
            scope=ResearchScope(max_crawlers=3),
        )
        res_multi, state_multi = self.researcher.execute_research(req_multi)
        self.assertEqual(res_multi.total_crawlers_spawned, 3)


if __name__ == "__main__":
    unittest.main()

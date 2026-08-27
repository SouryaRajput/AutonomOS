from __future__ import annotations

import unittest

from core.models import Task
from workers.researcher.planner import ResearchPlanner
from workers.researcher.types import ResearchMode, ResearchQuestionStatus


class TestResearcherPlannerAndTaskSpec(unittest.TestCase):
    """Unit tests for task parsing, question decomposition, and plan creation."""

    def test_parse_task_spec_with_explicit_questions_and_domains(self):
        task = Task(
            id="t-res-01",
            project_id="proj-1",
            title="Research SQLite Concurrency",
            objective="Analyze WAL mode and busy handler tradeoffs",
            metadata={
                "mode": "DEEP",
                "allowed_domains": ["sqlite.org", "github.com"],
                "recency_days": 365,
                "questions": [
                    "How does WAL mode affect reader-writer concurrency?",
                    "What is the recommended busy timeout setting?",
                ],
                "constraints": ["Must support multi-process readers"],
            },
        )

        spec = ResearchPlanner.parse_task_spec(task)
        self.assertEqual(spec.mode, ResearchMode.DEEP)
        self.assertEqual(len(spec.questions), 2)
        self.assertEqual(spec.questions[0].question_text, "How does WAL mode affect reader-writer concurrency?")
        self.assertEqual(spec.scope.allowed_domains, ["sqlite.org", "github.com"])
        self.assertEqual(spec.scope.recency_days, 365)
        self.assertEqual(spec.scope.max_searches, 10)  # DEEP mode default

    def test_automatic_objective_decomposition_for_unstructured_tasks(self):
        task = Task(
            id="t-res-02",
            project_id="proj-1",
            title="Evaluate PostgreSQL vs MongoDB for time-series data",
            objective="Compare PostgreSQL and MongoDB for time-series ingestion",
        )

        spec = ResearchPlanner.parse_task_spec(task)
        self.assertEqual(spec.mode, ResearchMode.STANDARD)
        self.assertTrue(len(spec.questions) >= 2)
        self.assertIn("capabilities", spec.questions[0].question_text.lower())

    def test_create_plan_step_sequence(self):
        task = Task(
            id="t-res-03",
            project_id="proj-1",
            title="Research OAuth2 Library",
            objective="Find active Python OAuth2 authorization server libraries",
        )
        spec = ResearchPlanner.parse_task_spec(task)
        plan = ResearchPlanner.create_plan(spec)

        self.assertIsNotNone(plan.plan_id)
        self.assertEqual(len(plan.planned_steps), 6)
        self.assertIn("Inspect Project Context", plan.planned_steps[0])
        self.assertIn("Produce Research Report", plan.planned_steps[-1])

    def test_generate_search_queries_with_domain_constraints(self):
        task = Task(
            id="t-res-04",
            project_id="proj-1",
            title="OAuth PKCE Flow",
            objective="Verify PKCE support in Authlib",
            metadata={"allowed_domains": ["authlib.org"]},
        )
        spec = ResearchPlanner.parse_task_spec(task)
        q = spec.questions[0]
        queries = ResearchPlanner.generate_search_queries(q, spec)

        self.assertTrue(len(queries) >= 1)
        self.assertTrue(any("site:authlib.org" in query for query in queries))


if __name__ == "__main__":
    unittest.main()

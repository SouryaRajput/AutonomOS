import unittest

from core.context.model import ContextBudget, ContextItem, ContextRequest
from core.context.scoring import RelevanceScorer
from core.context.types import ContextPriority, ContextSourceType
from core.models import Task, utc_now


class TestContextScoring(unittest.TestCase):

    def setUp(self):
        self.task = Task(
            id="task-auth-1",
            project_id="proj-1",
            title="Implement OAuth2 and JWT authentication",
            objective="Build secure JWT token validation and OAuth2 redirect routes.",
            context_references=[{"path": "core/auth/jwt.py"}],
            dependencies=["task-db-setup"],
            created_at=utc_now(),
        )
        self.request = ContextRequest(
            project_id="proj-1",
            task_id=self.task.id,
            focus_areas=["core/auth/jwt.py", "security"],
        )
        self.explicit_refs = {"core/auth/jwt.py", "task-db-setup"}

    def test_task_objective_scores_1_and_mandatory(self):
        item = ContextItem(
            id="item-task",
            source_type=ContextSourceType.TASK_OBJECTIVE,
            source_id="task-auth-1",
            title="Task Objective",
            content="Task details",
        )
        scored = RelevanceScorer.score_item(item, self.task, self.request, self.explicit_refs)
        self.assertEqual(scored.relevance_score, 1.0)
        self.assertEqual(scored.priority, ContextPriority.MANDATORY)
        self.assertTrue(scored.is_required)

    def test_explicit_reference_receives_boost_and_high_priority(self):
        item = ContextItem(
            id="item-ref-file",
            source_type=ContextSourceType.REPOSITORY_FILE,
            source_id="core/auth/jwt.py",
            title="JWT Service Implementation",
            content="def verify_jwt(): pass",
            metadata={"relative_path": "core/auth/jwt.py"},
        )
        scored = RelevanceScorer.score_item(item, self.task, self.request, self.explicit_refs)
        # Base (0.35) + Explicit boost (+0.40) + Keyword/Focus matches >= 0.85 -> HIGH
        self.assertGreaterEqual(scored.relevance_score, 0.75)
        self.assertEqual(scored.priority, ContextPriority.HIGH)
        self.assertTrue(any("Explicitly referenced" in r for r in scored.scoring_reasons))

    def test_keyword_overlap_and_focus_area_scoring(self):
        item = ContextItem(
            id="item-adr",
            source_type=ContextSourceType.DECISION,
            source_id="dec-0004",
            title="ADR 0004: OAuth2 Security Guidelines",
            content="Guidelines for OAuth2 token expiry and security.",
            metadata={"tags": ["oauth2", "security"]},
        )
        scored = RelevanceScorer.score_item(item, self.task, self.request, set())
        # Base (0.50) + Keywords ("oauth2", "security") (+0.30) + Focus area match (+0.20) + Freshness (+0.05)
        self.assertGreaterEqual(scored.relevance_score, 0.80)
        self.assertTrue(any("keyword" in r.lower() or "focus area" in r.lower() for r in scored.scoring_reasons))

    def test_unrelated_item_scores_lower_and_optional(self):
        item = ContextItem(
            id="item-unrelated",
            source_type=ContextSourceType.REPORT,
            source_id="rep-unrelated-999",
            title="Report: UI Button Colors",
            content="Changed primary button from green to blue.",
            metadata={"task_id": "task-unrelated-999", "tags": ["css", "styling"], "updated_at": "2020-01-01T00:00:00Z"},
        )
        scored = RelevanceScorer.score_item(item, self.task, self.request, set())
        self.assertLess(scored.relevance_score, 0.50)
        self.assertIn(scored.priority, (ContextPriority.LOW, ContextPriority.OPTIONAL))

    def test_freshness_calculation(self):
        recent_ts = utc_now()
        f_recent = RelevanceScorer.calculate_freshness(recent_ts)
        self.assertGreaterEqual(f_recent, 0.95)

        old_ts = "2020-01-01T00:00:00+00:00"
        f_old = RelevanceScorer.calculate_freshness(old_ts)
        self.assertLessEqual(f_old, 0.60)


if __name__ == "__main__":
    unittest.main()

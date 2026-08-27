import unittest

from core.context.model import (
    ContextBudget,
    ContextItem,
    ContextPackage,
    ContextRequest,
    ContextWarning,
    estimate_tokens,
)
from core.context.types import ContextPriority, ContextSourceType, ContextWarningType


class TestContextModels(unittest.TestCase):

    def test_estimate_tokens_heuristic(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("hello"), 2)
        text = "The quick brown fox jumps over the lazy dog."
        tokens = estimate_tokens(text)
        self.assertGreaterEqual(tokens, 9)
        self.assertLessEqual(tokens, 20)

    def test_context_budget_serialization(self):
        budget = ContextBudget(max_tokens=2000, max_characters=8000, max_items=10)
        d = budget.to_dict()
        self.assertEqual(d["max_tokens"], 2000)
        self.assertEqual(d["max_items"], 10)

        restored = ContextBudget.from_dict(d)
        self.assertEqual(restored.max_tokens, 2000)
        self.assertEqual(restored.max_characters, 8000)

    def test_context_request_serialization(self):
        req = ContextRequest(
            project_id="proj-1",
            task_id="task-100",
            worker_id="worker.programmer",
            budget=ContextBudget(max_tokens=3000),
            focus_areas=["auth", "jwt"],
        )
        d = req.to_dict()
        self.assertEqual(d["project_id"], "proj-1")
        self.assertEqual(d["task_id"], "task-100")
        self.assertEqual(d["focus_areas"], ["auth", "jwt"])

        restored = ContextRequest.from_dict(d)
        self.assertEqual(restored.request_id, req.request_id)
        self.assertEqual(restored.budget.max_tokens, 3000)
        self.assertEqual(restored.focus_areas, ["auth", "jwt"])

    def test_context_item_auto_properties_and_serialization(self):
        item = ContextItem(
            id="item-1",
            source_type=ContextSourceType.ARCHITECTURE,
            source_id="arch-001",
            title="System Architecture",
            content="# System Architecture\nCore invariants here.",
            priority=ContextPriority.HIGH,
            relevance_score=0.85,
            scoring_reasons=["Core architecture (+0.70)", "Explicit reference (+0.40)"],
        )
        self.assertGreater(item.character_count, 0)
        self.assertGreater(item.token_estimate, 0)
        self.assertIsNotNone(item.checksum)

        d = item.to_dict()
        self.assertEqual(d["source_type"], "ARCHITECTURE")
        self.assertEqual(d["priority"], "HIGH")
        self.assertEqual(len(d["scoring_reasons"]), 2)

        restored = ContextItem.from_dict(d)
        self.assertEqual(restored.id, "item-1")
        self.assertEqual(restored.source_type, ContextSourceType.ARCHITECTURE)
        self.assertEqual(restored.relevance_score, 0.85)

    def test_context_package_prompt_formatting(self):
        item1 = ContextItem(
            id="item-1",
            source_type=ContextSourceType.TASK_OBJECTIVE,
            source_id="task-1",
            title="Implement Auth",
            content="# Objective: Write JWT Auth",
            priority=ContextPriority.MANDATORY,
            is_required=True,
            relevance_score=1.0,
            scoring_reasons=["Task Objective (Mandatory)"],
        )
        item2 = ContextItem(
            id="item-2",
            source_type=ContextSourceType.DECISION,
            source_id="dec-1",
            title="ADR: JWT Format",
            content="Use HS256 algorithm.",
            priority=ContextPriority.HIGH,
            relevance_score=0.90,
            scoring_reasons=["Explicit reference"],
        )
        warning = ContextWarning(
            warning_type=ContextWarningType.MISSING_FILE,
            message="File 'missing.py' not found on disk.",
            target_id="missing.py",
        )

        package = ContextPackage(
            request_id="req-123",
            project_id="proj-1",
            task_id="task-1",
            worker_id="worker.programmer",
            items=[item1, item2],
            total_estimated_tokens=item1.token_estimate + item2.token_estimate,
            total_characters=len(item1.content) + len(item2.content),
            budget=ContextBudget(),
            warnings=[warning],
            candidate_count=5,
            selected_count=2,
        )

        prompt_str = package.format_as_prompt_section()
        self.assertIn("Context Package for Task: `task-1`", prompt_str)
        self.assertIn("Context Warnings & Discrepancies", prompt_str)
        self.assertIn("[MISSING_FILE]", prompt_str)
        self.assertIn("[TASK_OBJECTIVE] Implement Auth `[REQUIRED]`", prompt_str)
        self.assertIn("[DECISION] ADR: JWT Format", prompt_str)
        self.assertIn("HS256 algorithm", prompt_str)

        # Lookup helpers
        self.assertEqual(package.get_item_by_id("item-1").id, "item-1")
        self.assertEqual(len(package.get_items_by_source_type(ContextSourceType.DECISION)), 1)


if __name__ == "__main__":
    unittest.main()

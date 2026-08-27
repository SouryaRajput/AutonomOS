import json
import unittest

from core.context.model import ContextBudget, ContextItem, ContextPackage
from core.context.types import ContextSourceType
from core.events.model import Event, utc_now
from core.events.types import EventSource, EventType
from core.manager.model import ManagerState, Plan
from core.manager.prompt import build_manager_prompt, parse_manager_decision
from core.manager.types import ConfidenceLevel, ManagerActionType, PlanStatus
from core.models import Task, WorkerManifest


class TestManagerPromptAndParser(unittest.TestCase):

    def test_build_manager_prompt_structure(self):
        state = ManagerState(
            project_id="proj-test",
            objective="Build microservice architecture",
            current_plan=Plan(id="p1", project_id="proj-test", objective="Build microservice", version=1, milestones=["M1"]),
            tasks=[
                Task(id="t1", project_id="proj-test", title="Design DB", objective="Schema", status="READY"),
            ],
            workers=[
                WorkerManifest(id="w1", name="Worker 1", role="ENGINEER", description="Dev", capabilities=["CODE_GENERATION"]),
            ],
            cycle_count=1,
            consecutive_idle_cycles=0,
        )

        ctx_pkg = ContextPackage(
            request_id="req-1",
            project_id="proj-test",
            task_id="t1",
            worker_id="w1",
            items=[
                ContextItem(id="item-1", source_type=ContextSourceType.PROJECT_MAP, source_id="pm-1", title="project-map.md", content="Service A depends on DB B"),
            ],
            total_estimated_tokens=10,
            total_characters=50,
            budget=ContextBudget(),
        )

        messages = build_manager_prompt(state=state, context_package=ctx_pkg)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].role, "system")
        self.assertIn("AutonomOS Workforce Manager", messages[0].content)

        user_content = messages[1].content
        self.assertIn("Build microservice architecture", user_content)
        self.assertIn("Design DB", user_content)
        self.assertIn("Worker 1", user_content)
        self.assertIn("[UNTRUSTED_PROJECT_DATA]", user_content)
        self.assertIn("Service A depends on DB B", user_content)

    def test_parse_valid_json_decision(self):
        raw = json.dumps({
            "reasoning_summary": "Ready to launch first phase tasks.",
            "confidence_level": "CERTAIN",
            "assumptions": ["Repo initialized"],
            "risks": [],
            "actions": [
                {
                    "action_type": "CREATE_TASK",
                    "parameters": {"title": "Init repository", "priority": 90},
                    "rationale": "Base project setup",
                }
            ],
        })

        decision = parse_manager_decision(raw, project_id="p1", cycle_id="c1")
        self.assertEqual(decision.project_id, "p1")
        self.assertEqual(decision.cycle_id, "c1")
        self.assertEqual(decision.confidence_level, ConfidenceLevel.CERTAIN)
        self.assertEqual(len(decision.actions), 1)
        self.assertEqual(decision.actions[0].action_type, ManagerActionType.CREATE_TASK)

    def test_parse_json_embedded_in_markdown_fences(self):
        raw = """Here is my decision:
```json
{
  "reasoning_summary": "Assigning worker to task.",
  "confidence_level": "LOW_UNCERTAINTY",
  "actions": [
    {
      "action_type": "ASSIGN_TASK",
      "parameters": {"task_id": "t1", "worker_id": "w1"},
      "rationale": "Worker is idle and qualified"
    }
  ]
}
```
Let me know if you need changes."""

        decision = parse_manager_decision(raw, project_id="p1", cycle_id="c1")
        self.assertEqual(decision.confidence_level, ConfidenceLevel.LOW_UNCERTAINTY)
        self.assertEqual(len(decision.actions), 1)
        self.assertEqual(decision.actions[0].action_type, ManagerActionType.ASSIGN_TASK)

    def test_parse_malformed_json_fallback(self):
        raw = "This is not json at all! Something failed."
        decision = parse_manager_decision(raw, project_id="p1", cycle_id="c1")
        self.assertEqual(decision.confidence_level, ConfidenceLevel.HIGH_RISK_UNCERTAINTY)
        self.assertEqual(len(decision.actions), 1)
        self.assertEqual(decision.actions[0].action_type, ManagerActionType.WAIT)
        self.assertIn("invalid JSON", decision.reasoning_summary)


if __name__ == "__main__":
    unittest.main()

import json
import unittest

from core.enums import TaskStatus
from core.events.types import EventType
from core.inference.provider import MockProvider
from core.manager.controller import ManagerController
from core.manager.types import ManagerActionType, PlanStatus
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from workers.dummy_worker import DummyWorker


class TestManagerControllerCycle(unittest.TestCase):

    def setUp(self):
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(self.store)
        self.project = self.runtime.create_project("Cycle Test", "Test Controller Cycles")
        self.worker = DummyWorker("worker-1", "Test Worker")
        self.runtime.register_worker(self.worker)

        # Configure deterministic mock response for ManagerAgent
        provider = self.runtime.providers.get_provider("mock-provider")
        if isinstance(provider, MockProvider):
            self.mock_provider = provider

    def tearDown(self):
        self.runtime.close()

    def test_execute_single_cycle_creates_plan_and_tasks(self):
        canned_decision = {
            "reasoning_summary": "Starting project planning cycle.",
            "confidence_level": "CERTAIN",
            "assumptions": ["All tools available"],
            "risks": [],
            "plan_update": {
                "objective": "Build Test Project",
                "milestones": ["Milestone 1: Setup"],
            },
            "actions": [
                {
                    "action_type": "CREATE_TASK",
                    "parameters": {"title": "Task 1", "objective": "Do task 1", "priority": 80},
                    "rationale": "First priority task",
                },
                {
                    "action_type": "UPDATE_MEMORY",
                    "parameters": {"memory_type": "DECISION", "title": "Architecture Decision 001", "content": "Use SQLite"},
                    "rationale": "Persist architectural choice",
                },
            ],
        }
        self.mock_provider.set_canned_response(json.dumps(canned_decision))

        result = self.runtime.step_manager(self.project.id)
        self.assertTrue(result.progress_detected)
        self.assertEqual(len(result.results), 2)
        self.assertTrue(result.results[0].accepted)
        self.assertTrue(result.results[1].accepted)

        # Verify Plan created
        active_plan = self.runtime.get_active_plan(self.project.id)
        self.assertIsNotNone(active_plan)
        self.assertEqual(active_plan.version, 1)

        # Verify Task created
        tasks = self.runtime.tasks.list_tasks(self.project.id)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].title, "Task 1")

        # Verify Memory Document created
        docs = self.runtime.memory.list_memories(self.project.id)
        decision_docs = [d for d in docs if "Architecture Decision 001" in d.title]
        self.assertEqual(len(decision_docs), 1)

        # Verify Events emitted
        events = self.runtime.get_events(project_id=self.project.id)
        event_types = [e.event_type for e in events]
        self.assertIn(EventType.MANAGER_CYCLE_STARTED, event_types)
        self.assertIn(EventType.MANAGER_DECISION_CREATED, event_types)
        self.assertIn(EventType.MANAGER_PLAN_CREATED, event_types)
        self.assertIn(EventType.MANAGER_ACTION_ACCEPTED, event_types)
        self.assertIn(EventType.MANAGER_CYCLE_COMPLETED, event_types)


if __name__ == "__main__":
    unittest.main()

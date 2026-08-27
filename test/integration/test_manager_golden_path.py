import json
import unittest

from core.enums import TaskStatus
from core.events.types import EventType
from core.inference.provider import MockProvider
from core.manager.types import ManagerActionType, PlanStatus
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from workers.dummy_worker import DummyWorker


class TestManagerGoldenPath(unittest.TestCase):

    def setUp(self):
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(self.store)
        self.project = self.runtime.create_project("Golden Path Project", "Build autonomous microservice")
        self.worker = DummyWorker("worker-engineer", "Fullstack Engineer")
        self.runtime.register_worker(self.worker)

        provider = self.runtime.providers.get_provider("mock-provider")
        if isinstance(provider, MockProvider):
            self.mock_provider = provider

    def tearDown(self):
        self.runtime.close()

    def test_complete_autonomous_golden_path(self):
        # --- Cycle 1: Manager plans and creates task ---
        cycle_1_decision = {
            "reasoning_summary": "Planning project decomposition: Creating initial build task.",
            "confidence_level": "CERTAIN",
            "plan_update": {
                "objective": "Build autonomous microservice",
                "milestones": ["Milestone 1: Service Core"],
            },
            "actions": [
                {
                    "action_type": "CREATE_TASK",
                    "parameters": {
                        "title": "Implement Microservice Core",
                        "objective": "Create baseline service endpoints",
                        "priority": 90,
                    },
                    "rationale": "First priority task for Milestone 1",
                }
            ],
        }
        self.mock_provider.set_canned_response(json.dumps(cycle_1_decision))

        res1 = self.runtime.step_manager(self.project.id)
        self.assertTrue(res1.progress_detected)
        tasks = self.runtime.tasks.list_tasks(self.project.id)
        self.assertEqual(len(tasks), 1)
        task_id = tasks[0].id

        # --- Cycle 2: Manager assigns task to capable worker ---
        cycle_2_decision = {
            "reasoning_summary": f"Assigning task {task_id} to worker-engineer.",
            "confidence_level": "CERTAIN",
            "actions": [
                {
                    "action_type": "ASSIGN_TASK",
                    "parameters": {"task_id": task_id, "worker_id": "worker-engineer"},
                    "rationale": "Worker is idle and eligible",
                }
            ],
        }
        self.mock_provider.set_canned_response(json.dumps(cycle_2_decision))

        res2 = self.runtime.step_manager(self.project.id)
        self.assertTrue(res2.progress_detected)

        # Worker executes task
        task = self.runtime.tasks.get_task(task_id)
        self.assertEqual(task.status, TaskStatus.ASSIGNED)
        self.runtime.run_task(task_id)

        # Verify task is completed by worker and verified
        task_after = self.runtime.tasks.get_task(task_id)
        self.assertEqual(task_after.status, TaskStatus.COMPLETED)

        # --- Cycle 3: Manager observes completed task and completes project ---
        cycle_3_decision = {
            "reasoning_summary": "All tasks in active plan are completed and verified.",
            "confidence_level": "CERTAIN",
            "actions": [
                {
                    "action_type": "COMPLETE_PROJECT",
                    "parameters": {"summary": "Microservice core successfully delivered"},
                    "rationale": "All milestone criteria satisfied",
                }
            ],
        }
        self.mock_provider.set_canned_response(json.dumps(cycle_3_decision))

        res3 = self.runtime.step_manager(self.project.id)
        self.assertTrue(res3.completed)

        # Verify Plan status updated to COMPLETED
        active_plan = self.runtime.get_active_plan(self.project.id)
        self.assertEqual(active_plan.status, PlanStatus.COMPLETED)

        # Verify full event lineage
        events = self.runtime.get_events(project_id=self.project.id)
        event_types = [e.event_type for e in events]
        self.assertIn(EventType.MANAGER_PLAN_CREATED, event_types)
        self.assertIn(EventType.TASK_CREATED, event_types)
        self.assertIn(EventType.TASK_ASSIGNED, event_types)
        self.assertIn(EventType.TASK_COMPLETED, event_types)
        self.assertIn(EventType.MANAGER_ACTION_ACCEPTED, event_types)


if __name__ == "__main__":
    unittest.main()

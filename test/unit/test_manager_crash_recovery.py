import json
import os
import shutil
import tempfile
import unittest

from core.enums import TaskStatus
from core.inference.provider import MockProvider
from core.manager.types import ManagerActionType, PlanStatus
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from workers.dummy_worker import DummyWorker


class TestManagerCrashRecovery(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "crash_test.db")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_manager_state_reconstruction_after_restart(self):
        # 1. Initialize runtime with disk-backed SQLite store
        runtime1 = WorkforceRuntime.with_sqlite(self.db_path)
        project = runtime1.create_project("Persistent Project", "Test crash recovery")
        worker = DummyWorker("worker-persist", "Persistent Worker")
        runtime1.register_worker(worker)

        mock_provider1 = runtime1.providers.get_provider("mock-provider")
        if isinstance(mock_provider1, MockProvider):
            canned_decision = {
                "reasoning_summary": "Creating persistent plan and task.",
                "confidence_level": "CERTAIN",
                "plan_update": {
                    "objective": "Build Persistent Engine",
                    "milestones": ["Milestone 1"],
                },
                "actions": [
                    {
                        "action_type": "CREATE_TASK",
                        "parameters": {"title": "Task Pre-Crash", "priority": 85},
                        "rationale": "Must survive crash",
                    }
                ],
            }
            mock_provider1.set_canned_response(json.dumps(canned_decision))

        # Execute cycle
        res1 = runtime1.step_manager(project.id)
        self.assertTrue(res1.progress_detected)

        # Close runtime (simulating process crash)
        runtime1.close()

        # 2. Re-open runtime from same database
        runtime2 = WorkforceRuntime.with_sqlite(self.db_path)
        runtime2.register_worker_instance_only(DummyWorker("worker-persist", "Persistent Worker"))

        # Check Plan restored
        plan = runtime2.get_active_plan(project.id)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.objective, "Build Persistent Engine")
        self.assertEqual(plan.version, 1)

        # Check Task restored
        tasks = runtime2.tasks.list_tasks(project.id)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].title, "Task Pre-Crash")

        # Check Manager Decisions restored
        decisions = runtime2.store.list_manager_decisions_for_project(project.id)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].reasoning_summary, "Creating persistent plan and task.")

        # Check Manager State reconstruction
        state = runtime2.manager.gather_state(project.id)
        self.assertEqual(state.project_id, project.id)
        self.assertEqual(len(state.tasks), 1)
        self.assertIsNotNone(state.current_plan)

        runtime2.close()


if __name__ == "__main__":
    unittest.main()

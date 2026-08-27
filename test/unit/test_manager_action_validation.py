import unittest

from core.enums import ProjectStatus, RiskLevel, TaskStatus
from core.manager.controller import ManagerController
from core.manager.model import ManagerAction, ManagerConfig
from core.manager.types import ManagerActionType
from core.models import Project, Task, WorkerManifest
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from workers.dummy_worker import DummyWorker


class TestManagerActionValidation(unittest.TestCase):

    def setUp(self):
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(self.store)
        self.project = self.runtime.create_project("Validation Test", "Test Action Validation")
        self.worker = DummyWorker("worker-1", "Test Worker")
        self.runtime.register_worker(self.worker)
        self.controller = ManagerController(self.runtime)

    def tearDown(self):
        self.runtime.close()

    def test_create_task_action_validation(self):
        # Valid task creation
        action = ManagerAction(
            action_type=ManagerActionType.CREATE_TASK,
            parameters={"title": "Setup DB Schema", "objective": "Create tables", "priority": 75},
        )
        res = self.controller._validate_and_execute_action(self.project.id, action, "test-causation")
        self.assertTrue(res.accepted)
        self.assertIsNotNone(res.execution_output)
        self.assertEqual(res.execution_output["title"], "Setup DB Schema")

        # Invalid task creation (empty title)
        bad_action = ManagerAction(
            action_type=ManagerActionType.CREATE_TASK,
            parameters={"title": "", "objective": "No title"},
        )
        bad_res = self.controller._validate_and_execute_action(self.project.id, bad_action, "test-causation")
        self.assertFalse(bad_res.accepted)
        self.assertIn("Task title cannot be empty", bad_res.reason)

    def test_assign_task_action_validation(self):
        task = self.runtime.create_task(self.project.id, "Build API", "REST API")

        # Valid assignment
        assign_action = ManagerAction(
            action_type=ManagerActionType.ASSIGN_TASK,
            parameters={"task_id": task.id, "worker_id": "worker-1"},
        )
        res = self.controller._validate_and_execute_action(self.project.id, assign_action, "test-causation")
        self.assertTrue(res.accepted)

        # Assignment to non-existent worker
        bad_assign = ManagerAction(
            action_type=ManagerActionType.ASSIGN_TASK,
            parameters={"task_id": task.id, "worker_id": "ghost-worker"},
        )
        bad_res = self.controller._validate_and_execute_action(self.project.id, bad_assign, "test-causation")
        self.assertFalse(bad_res.accepted)
        self.assertIn("does not exist", bad_res.reason)

    def test_assign_task_with_unsatisfied_dependencies_rejected(self):
        prereq = self.runtime.create_task(self.project.id, "Prereq Task", "Do first")
        dependent = self.runtime.create_task(self.project.id, "Dependent Task", "Do second")
        self.runtime.tasks.add_dependency(dependent.id, prereq.id)

        # Try assigning dependent while prereq is pending
        action = ManagerAction(
            action_type=ManagerActionType.ASSIGN_TASK,
            parameters={"task_id": dependent.id, "worker_id": "worker-1"},
        )
        res = self.controller._validate_and_execute_action(self.project.id, action, "test-causation")
        self.assertFalse(res.accepted)
        self.assertIn("unsatisfied dependencies", res.reason)

    def test_request_retry_budget_enforcement(self):
        task = self.runtime.create_task(self.project.id, "Failing Task", "Will fail")
        task.attempts = 3
        self.runtime.store.save_task(task)

        # Retry should be rejected because attempts >= retry_budget_per_task (3)
        action = ManagerAction(
            action_type=ManagerActionType.REQUEST_RETRY,
            parameters={"task_id": task.id, "reason": "Try again"},
        )
        res = self.controller._validate_and_execute_action(self.project.id, action, "test-causation")
        self.assertFalse(res.accepted)
        self.assertIn("retry budget exhausted", res.reason)

    def test_complete_project_validation(self):
        t1 = self.runtime.create_task(self.project.id, "Task 1", "Work 1")
        # Try completing project while task 1 is not COMPLETED
        action = ManagerAction(
            action_type=ManagerActionType.COMPLETE_PROJECT,
            parameters={"summary": "Done"},
        )
        res = self.controller._validate_and_execute_action(self.project.id, action, "test-causation")
        self.assertFalse(res.accepted)
        self.assertIn("are not COMPLETED", res.reason)


if __name__ == "__main__":
    unittest.main()

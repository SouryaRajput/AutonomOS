import json
import unittest

from core.enums import TaskStatus
from core.events.types import EventType
from core.inference.provider import MockProvider
from core.manager.types import ManagerActionType, PlanStatus
from core.models import Task, WorkerManifest, WorkerOutput
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from pkg.sdk.worker import Worker, WorkerRuntimeContext
from workers.dummy_worker import DummyWorker


class FailingWorker(Worker):
    """Worker that intentionally throws an error on first attempt, then succeeds on retry."""

    def __init__(self, worker_id: str, name: str):
        self._manifest = WorkerManifest(
            id=worker_id,
            name=name,
            role="ENGINEER",
            description="Flaky worker",
            capabilities=["CODE_GENERATION", "MIGRATION"],
            permissions=["*"],
        )
        self.call_count = 0

    def get_manifest(self) -> WorkerManifest:
        return self._manifest

    def execute_task(self, context: WorkerRuntimeContext, task: Task) -> WorkerOutput:
        self.call_count += 1
        if self.call_count == 1:
            raise RuntimeError("Database connection timed out during migration")
        return WorkerOutput(success=True, summary="Migration completed on retry")


class TestManagerFailureAndReplanning(unittest.TestCase):

    def setUp(self):
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(self.store)
        self.project = self.runtime.create_project("Replan Project", "Resilient workflow execution")
        self.worker = FailingWorker("worker-flaky", "Flaky Worker")
        self.runtime.register_worker(self.worker)

        provider = self.runtime.providers.get_provider("mock-provider")
        if isinstance(provider, MockProvider):
            self.mock_provider = provider

    def tearDown(self):
        self.runtime.close()

    def test_failure_handling_and_replanning(self):
        # 1. Manager creates initial task
        decision_1 = {
            "reasoning_summary": "Creating database migration task.",
            "plan_update": {"objective": "Run DB migrations", "milestones": ["Migration"]},
            "actions": [
                {
                    "action_type": "CREATE_TASK",
                    "parameters": {"title": "Apply Migrations", "priority": 90},
                }
            ],
        }
        self.mock_provider.set_canned_response(json.dumps(decision_1))
        self.runtime.step_manager(self.project.id)

        task = self.runtime.tasks.list_tasks(self.project.id)[0]

        # 2. Assign and execute -> Worker fails
        self.runtime.assign_task(task.id, "worker-flaky")
        self.runtime.run_task(task.id)

        task_after_fail = self.runtime.tasks.get_task(task.id)
        self.assertEqual(task_after_fail.status, TaskStatus.RETRYING)
        self.assertEqual(task_after_fail.attempts, 1)

        # 3. Manager inspects failure evidence and proposes RETRY + REPLAN
        decision_2 = {
            "reasoning_summary": "Task failed due to connection timeout. Triggering retry and updating plan.",
            "plan_update": {
                "objective": "Run DB migrations with retry strategy",
                "milestones": ["Migration", "Verification"],
                "reason": "Incorporating retry resilience",
            },
            "actions": [
                {
                    "action_type": "REQUEST_RETRY",
                    "parameters": {"task_id": task.id, "reason": "Transient network timeout"},
                    "rationale": "Retry task with active worker",
                }
            ],
        }
        self.mock_provider.set_canned_response(json.dumps(decision_2))
        res2 = self.runtime.step_manager(self.project.id)
        self.assertTrue(res2.progress_detected)

        # Check plan was revised
        active_plan = self.runtime.get_active_plan(self.project.id)
        self.assertEqual(active_plan.version, 2)

        # Check task was transitioned to RETRYING
        task_retrying = self.runtime.tasks.get_task(task.id)
        self.assertEqual(task_retrying.status, TaskStatus.RETRYING)

        # 4. Worker re-executes task and succeeds
        self.runtime.assign_task(task.id, "worker-flaky")
        self.runtime.run_task(task.id)

        task_success = self.runtime.tasks.get_task(task.id)
        self.assertEqual(task_success.status, TaskStatus.COMPLETED)

        # Verify replan event in history
        events = self.runtime.get_events(project_id=self.project.id)
        event_types = [e.event_type for e in events]
        self.assertIn(EventType.MANAGER_REPLAN, event_types)


if __name__ == "__main__":
    unittest.main()

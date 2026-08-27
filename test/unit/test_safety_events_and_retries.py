from pathlib import Path
import shutil
import tempfile
import unittest

from core.enums import TaskStatus
from core.events.types import EventType
from core.models import Project, Task, WorkerManifest, utc_now
from core.runtime.workforce_runtime import WorkforceRuntime
from core.safety.model import SafetyConfig
from core.storage.memory_store import MemoryStore
from workers.dummy_worker import DummyWorker


class TestSafetyEventsAndRetries(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.store = MemoryStore()
        self.runtime = WorkforceRuntime(self.store, safety_config=SafetyConfig(auto_checkpoint=True, max_retries=2))

        self.project = self.runtime.projects.create_project(
            name="Retry Project",
            description="Testing safety events and retries",
            root_path=self.workspace,
        )

        self.failing_worker = DummyWorker(
            worker_id="worker.failing",
            name="Failing Worker",
            should_fail=True,
            failure_message="Deterministic execution failure simulation",
        )
        self.runtime.workers.register_worker(self.failing_worker)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_retry_limit_and_rollback_events(self):
        task = self.runtime.tasks.create_task(
            project_id=self.project.id,
            title="Failing Task",
            objective="Simulate failure and rollback cycle",
        )
        task.max_attempts = 2
        self.store.save_task(task)

        self.runtime.tasks.assign_task(task.id, self.failing_worker.get_manifest().id)

        # 1. First Attempt -> Fails -> Rolls back -> Transitions to RETRYING
        out1 = self.runtime.run_task(task.id)
        self.assertFalse(out1.success)

        task_after_1 = self.runtime.tasks.get_task(task.id)
        self.assertEqual(task_after_1.status, TaskStatus.RETRYING)
        self.assertEqual(task_after_1.attempts, 1)

        # 2. Second Attempt -> Fails -> Rolls back -> Reaches max_attempts -> Transitions to FAILED
        out2 = self.runtime.run_task(task.id)
        self.assertFalse(out2.success)

        task_after_2 = self.runtime.tasks.get_task(task.id)
        self.assertEqual(task_after_2.status, TaskStatus.FAILED)
        self.assertEqual(task_after_2.attempts, 2)

        # Verify event stream
        events = self.store.list_events(task_id=task.id)
        event_types = [e.event_type for e in events]

        self.assertIn(EventType.CHECKPOINT_CREATED, event_types)
        self.assertIn(EventType.CHECKPOINT_ROLLBACK_STARTED, event_types)
        self.assertIn(EventType.CHECKPOINT_ROLLBACK_COMPLETED, event_types)
        self.assertIn(EventType.TASK_RETRYING, event_types)
        self.assertIn(EventType.TASK_FAILED, event_types)

        # Verify causation and correlation
        rb_start = next(e for e in events if e.event_type == EventType.CHECKPOINT_ROLLBACK_STARTED)
        self.assertEqual(rb_start.correlation_id, task.id)


if __name__ == "__main__":
    unittest.main()

import pathlib
import shutil
import tempfile
import unittest

from core.enums import ArtifactType, TaskStatus, WorkerStatus
from core.events.model import Event
from core.events.types import EventSource, EventType
from core.runtime.workforce_runtime import WorkforceRuntime
from workers.dummy_worker import DummyWorker


class TestEventIntegrity(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(pathlib.Path(self.temp_dir) / "test_integrity.db")
        self.runtime = WorkforceRuntime.with_sqlite(self.db_path)

    def tearDown(self):
        self.runtime.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_live_event_subscription(self):
        captured_events: list[Event] = []
        self.runtime.subscribe_events(lambda e: captured_events.append(e))

        project = self.runtime.create_project(name="Stream Project", root_path=self.temp_dir)
        worker = DummyWorker(worker_id="worker.stream")
        self.runtime.register_worker(worker)
        task = self.runtime.create_task(project_id=project.id, title="Stream Task")
        self.runtime.assign_task(task.id, worker.get_manifest().id)
        self.runtime.run_task(task.id)

        # Verify real-time events were published
        self.assertGreaterEqual(len(captured_events), 6)
        types = [e.event_type for e in captured_events]
        self.assertIn(EventType.PROJECT_CREATED, types)
        self.assertIn(EventType.WORKER_REGISTERED, types)
        self.assertIn(EventType.TASK_CREATED, types)
        self.assertIn(EventType.TASK_ASSIGNED, types)
        self.assertIn(EventType.TASK_STARTED, types)
        self.assertIn(EventType.TASK_COMPLETED, types)

    def test_failed_execution_event_stream(self):
        project = self.runtime.create_project(name="Failing Project", root_path=self.temp_dir)
        failing_worker = DummyWorker(
            worker_id="worker.fail",
            should_fail=True,
            failure_message="Simulated disk full",
        )
        self.runtime.register_worker(failing_worker)
        task = self.runtime.create_task(
            project_id=project.id,
            title="Fail Task",
            max_attempts=2,
            task_id="task-failing",
        )
        self.runtime.assign_task(task.id, failing_worker.get_manifest().id)

        # Attempt 1 -> Should fail and transition to RETRYING
        self.runtime.run_task(task.id)

        timeline_1 = self.runtime.get_task_timeline(task.id)
        types_1 = [e.event_type for e in timeline_1]
        self.assertIn(EventType.TASK_STARTED, types_1)
        self.assertIn(EventType.WORKER_FAILED, types_1)
        self.assertIn(EventType.TASK_RETRYING, types_1)

        # Attempt 2 -> Should fail and transition to FAILED (exhausted attempts)
        # Re-assign and run attempt 2
        self.runtime.assign_task(task.id, failing_worker.get_manifest().id)
        self.runtime.run_task(task.id)

        timeline_2 = self.runtime.get_task_timeline(task.id)
        types_2 = [e.event_type for e in timeline_2]
        self.assertIn(EventType.TASK_FAILED, types_2)
        self.assertEqual(self.runtime.tasks.get_task(task.id).status, TaskStatus.FAILED)

    def test_worker_progress_log_is_non_authoritative(self):
        project = self.runtime.create_project(name="Progress Project", root_path=self.temp_dir)
        worker = DummyWorker(worker_id="worker.progress")
        self.runtime.register_worker(worker)
        task = self.runtime.create_task(project_id=project.id, title="Progress Task")
        self.runtime.assign_task(task.id, worker.get_manifest().id)
        self.runtime.run_task(task.id)

        worker_events = [
            e for e in self.runtime.get_events(task_id=task.id)
            if e.source == EventSource.WORKER
        ]
        self.assertGreaterEqual(len(worker_events), 1)
        for we in worker_events:
            self.assertEqual(we.event_type, EventType.WORKER_PROGRESS_LOGGED)
            self.assertEqual(we.source, EventSource.WORKER)


if __name__ == "__main__":
    unittest.main()

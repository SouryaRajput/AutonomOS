from pathlib import Path
import shutil
import tempfile
import unittest

from core.enums import TaskStatus
from core.events.types import EventType
from core.runtime.workforce_runtime import WorkforceRuntime
from core.safety.model import SafetyConfig
from core.storage.sqlite_store import SQLiteStore
from workers.dummy_worker import DummyWorker


class TestEndToEndFailurePath(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.temp_dir) / "autonomos_failure.db")
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.runtime = WorkforceRuntime.with_sqlite(self.db_path)
        self.runtime.safety_config = SafetyConfig(auto_checkpoint=True, max_retries=1)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_failure_golden_path_triggers_automatic_rollback_and_preserves_evidence(self):
        # 1. Setup Project & Base Knowledge
        project = self.runtime.projects.create_project(
            name="Failure Path Project",
            description="Testing failure golden path and rollback",
            root_path=self.workspace,
        )

        initial_file = Path(self.workspace) / "important_data.csv"
        initial_file.write_text("id,val\n1,alpha\n2,beta\n")

        init_mem = self.runtime.memory.record_task_memory(
            project_id=project.id,
            task_id="task-init",
            title="Initial Baseline Memory",
            objective="Establish baseline state",
            findings="Baseline recorded successfully",
        )

        # 2. Register failing worker
        failing_worker = DummyWorker(
            worker_id="worker.failing.golden",
            name="Failing Golden Worker",
            artifact_filename="corrupted_file.txt",
            artifact_content="CORRUPTED",
            should_fail=True,
            failure_message="Simulated golden failure exception",
        )
        self.runtime.workers.register_worker(failing_worker)

        # 3. Create Task & Assign
        task = self.runtime.tasks.create_task(
            project_id=project.id,
            title="Risky Mutation Task",
            objective="Attempt risky modification with failure simulation",
        )
        task.max_attempts = 1
        self.runtime.store.save_task(task)
        self.runtime.tasks.assign_task(task.id, failing_worker.get_manifest().id)

        # 4. Run Task -> Worker executes tool (writes corrupted_file.txt), then reports failure -> triggers Rollback
        output = self.runtime.run_task(task.id)
        self.assertFalse(output.success)

        # 5. Verify State Restoration
        task_final = self.runtime.tasks.get_task(task.id)
        self.assertEqual(task_final.status, TaskStatus.FAILED)

        # Pre-existing file preserved
        self.assertEqual(initial_file.read_text(), "id,val\n1,alpha\n2,beta\n")

        # Corrupted worker file must have been rolled back / deleted!
        self.assertFalse((Path(self.workspace) / "corrupted_file.txt").exists())

        # Verify Event Log preserved the complete audit trail
        events = self.runtime.store.list_events(task_id=task.id)
        event_types = [e.event_type for e in events]

        self.assertIn(EventType.CHECKPOINT_CREATED, event_types)
        self.assertIn(EventType.TOOL_REQUESTED, event_types)
        self.assertIn(EventType.CHECKPOINT_ROLLBACK_STARTED, event_types)
        self.assertIn(EventType.CHECKPOINT_ROLLBACK_COMPLETED, event_types)
        self.assertIn(EventType.TASK_FAILED, event_types)

        # 6. Verify SQLite Persistence across Process Restart
        reloaded_runtime = WorkforceRuntime.with_sqlite(self.db_path)
        persisted_task = reloaded_runtime.tasks.get_task(task.id)
        self.assertEqual(persisted_task.status, TaskStatus.FAILED)
        persisted_events = reloaded_runtime.store.list_events(task_id=task.id)
        self.assertEqual(len(persisted_events), len(events))


if __name__ == "__main__":
    unittest.main()

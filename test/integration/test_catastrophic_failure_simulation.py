from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from core.enums import TaskStatus
from core.events.types import EventType
from core.runtime.workforce_runtime import WorkforceRuntime
from core.safety.model import RollbackResult, SafetyConfig
from core.safety.rollback import RollbackManager
from core.safety.types import RollbackStatus
from core.storage.sqlite_store import SQLiteStore
from workers.dummy_worker import DummyWorker


class TestCatastrophicFailureSimulation(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.temp_dir) / "autonomos_catastrophic.db")
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.runtime = WorkforceRuntime.with_sqlite(self.db_path)
        self.runtime.safety_config = SafetyConfig(auto_checkpoint=True, max_retries=3)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_catastrophic_rollback_failure_halts_safely_and_blocks_infinite_loops(self):
        project = self.runtime.projects.create_project(
            name="Catastrophic Simulation Project",
            description="Testing catastrophic rollback failure handling",
            root_path=self.workspace,
        )

        failing_worker = DummyWorker(
            worker_id="worker.catastrophic",
            name="Catastrophic Worker",
            should_fail=True,
            failure_message="Simulated worker crash",
        )
        self.runtime.workers.register_worker(failing_worker)

        task = self.runtime.tasks.create_task(
            project_id=project.id,
            title="Catastrophic Task",
            objective="Simulate unrecoverable rollback failure",
        )
        task.max_attempts = 3
        self.runtime.store.save_task(task)
        self.runtime.tasks.assign_task(task.id, failing_worker.get_manifest().id)

        # Mock RollbackManager.rollback to simulate an unrecoverable disk/snapshot corruption
        with patch.object(
            RollbackManager,
            "rollback",
            return_value=RollbackResult(
                rollback_id="rb-catastrophic-err",
                checkpoint_id="chk-mock",
                status=RollbackStatus.FAILED,
                error_message="Catastrophic I/O failure: Snapshot storage corrupted.",
                verified=False,
            ),
        ):
            output = self.runtime.run_task(task.id)
            self.assertFalse(output.success)

        # Invariant Check: The system MUST NOT retry when rollback fails!
        # It must immediately halt, transition task to FAILED, and emit CHECKPOINT_ROLLBACK_FAILED
        task_final = self.runtime.tasks.get_task(task.id)
        self.assertEqual(task_final.status, TaskStatus.FAILED)
        self.assertEqual(task_final.attempts, 1)  # Did not attempt attempt 2 or 3!

        events = self.runtime.store.list_events(task_id=task.id)
        event_types = [e.event_type for e in events]

        self.assertIn(EventType.CHECKPOINT_ROLLBACK_FAILED, event_types)
        self.assertIn(EventType.TASK_FAILED, event_types)
        self.assertNotIn(EventType.TASK_RETRYING, event_types)


if __name__ == "__main__":
    unittest.main()

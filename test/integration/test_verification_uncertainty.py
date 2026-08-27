from pathlib import Path
import shutil
import tempfile
import unittest

from core.enums import TaskStatus
from core.events.types import EventType
from core.models import WorkerOutput
from core.runtime.workforce_runtime import WorkforceRuntime
from core.safety.model import SafetyConfig
from core.verification.model import SuccessCriterion
from core.verification.types import CheckType
from pkg.sdk.worker import Worker, WorkerRuntimeContext
from workers.dummy_worker import DummyWorker


class TestVerificationUncertainty(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.runtime = WorkforceRuntime.with_sqlite(str(Path(self.temp_dir) / "state.db"))
        self.runtime.safety_config = SafetyConfig(auto_checkpoint=True, max_retries=1)

        self.project = self.runtime.projects.create_project(
            name="Uncertainty Project",
            description="Testing verification uncertainty handling",
            root_path=self.workspace,
        )

        self.worker = DummyWorker(
            worker_id="worker.dummy.uncertain",
            name="Uncertainty Worker",
        )
        self.runtime.workers.register_worker(self.worker)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_timeout_command_produces_uncertain_and_blocks_completion(self):
        task = self.runtime.tasks.create_task(
            project_id=self.project.id,
            title="Uncertain Task",
            objective="Simulate verification timeout",
        )
        task.max_attempts = 1
        # Set a test suite command that sleeps longer than timeout_seconds
        task.success_criteria = [
            SuccessCriterion(
                id="crit-timeout",
                description="Long running test command that times out",
                check_type=CheckType.TEST_SUITE,
                parameters={"command": "sleep 10", "timeout_seconds": 1},
                required=True,
            ).to_dict()
        ]
        self.runtime.store.save_task(task)
        self.runtime.tasks.assign_task(task.id, self.worker.get_manifest().id)

        output = self.runtime.run_task(task.id)

        # Invariant: Output must be rejected (not success)
        self.assertFalse(output.success)

        # Invariant: Task must not be completed
        final_task = self.runtime.tasks.get_task(task.id)
        self.assertEqual(final_task.status, TaskStatus.FAILED)

        # Invariant: VERIFICATION_UNCERTAIN event must be emitted
        events = self.runtime.store.list_events(task_id=task.id)
        event_types = [e.event_type for e in events]
        self.assertIn(EventType.VERIFICATION_UNCERTAIN, event_types)
        self.assertNotIn(EventType.TASK_COMPLETED, event_types)


if __name__ == "__main__":
    unittest.main()

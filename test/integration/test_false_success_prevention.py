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


class LiarWorker(DummyWorker):
    """Worker that always falsely claims success without doing the actual work."""

    def execute_task(self, context: WorkerRuntimeContext, task) -> WorkerOutput:
        # Falsely claims completion without creating the expected file
        return WorkerOutput(
            success=True,
            summary="I swear everything was implemented perfectly and tests pass!",
            report_markdown="# Done\nAll requirements satisfied.",
        )


class TestFalseSuccessPrevention(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.runtime = WorkforceRuntime.with_sqlite(str(Path(self.temp_dir) / "state.db"))
        self.runtime.safety_config = SafetyConfig(auto_checkpoint=True, max_retries=1)

        self.project = self.runtime.projects.create_project(
            name="False Success Prevention Project",
            description="Testing that worker claims do not bypass verification",
            root_path=self.workspace,
        )

        self.worker = LiarWorker(
            worker_id="worker.liar",
            name="Overconfident Worker",
        )
        self.runtime.workers.register_worker(self.worker)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_worker_claim_overruled_by_failed_verification(self):
        # Create a task with an explicit SuccessCriterion: required_feature.py must exist
        task = self.runtime.tasks.create_task(
            project_id=self.project.id,
            title="Implement Core Feature",
            objective="Write required_feature.py",
        )
        task.max_attempts = 1
        task.success_criteria = [
            SuccessCriterion(
                id="crit-feat",
                description="Verify required_feature.py exists on disk.",
                check_type=CheckType.FILE_EXISTS,
                parameters={"path": "required_feature.py"},
                required=True,
            ).to_dict()
        ]
        self.runtime.store.save_task(task)
        self.runtime.tasks.assign_task(task.id, self.worker.get_manifest().id)

        # Run task: LiarWorker returns success=True, but VerificationEngine finds required_feature.py missing
        output = self.runtime.run_task(task.id)

        # Invariant: Output must be rejected as failure
        self.assertFalse(output.success)
        self.assertIn("rejected by Verification System", output.summary)

        # Invariant: Task must be FAILED, NOT COMPLETED
        final_task = self.runtime.tasks.get_task(task.id)
        self.assertEqual(final_task.status, TaskStatus.FAILED)

        # Invariant: VERIFICATION_FAILED event must be logged
        events = self.runtime.store.list_events(task_id=task.id)
        event_types = [e.event_type for e in events]
        self.assertIn(EventType.VERIFICATION_FAILED, event_types)
        self.assertNotIn(EventType.TASK_COMPLETED, event_types)


if __name__ == "__main__":
    unittest.main()

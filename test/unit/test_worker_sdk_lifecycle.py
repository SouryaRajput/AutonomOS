import unittest

from core.errors import WorkerNotEligibleError
from core.models import Task, WorkerManifest, WorkerOutput, utc_now
from pkg.sdk.harness import WorkerTestHarness
from pkg.sdk.worker import Worker, WorkerRuntimeContext


class LifecycleTrackingWorker(Worker):
    """Worker that records which lifecycle hooks were invoked in order."""

    def __init__(self):
        self.lifecycle_log: list[str] = []
        self._manifest = WorkerManifest(
            id="worker.lifecycle.tracker",
            name="Lifecycle Tracker",
            role="Tester",
            description="Lifecycle tracker worker",
            capabilities=["test.lifecycle"],
            permissions=["*"],
            created_at=utc_now(),
        )

    def get_manifest(self) -> WorkerManifest:
        return self._manifest

    def initialize(self) -> None:
        self.lifecycle_log.append("INITIALIZE")

    def validate_task(self, task: Task) -> None:
        self.lifecycle_log.append("VALIDATE_TASK")
        if "INCOMPATIBLE" in task.title:
            raise WorkerNotEligibleError(self._manifest.id, "Incompatible task title.")

    def before_task(self, context: WorkerRuntimeContext, task: Task) -> None:
        self.lifecycle_log.append("BEFORE_TASK")

    def execute_task(self, context: WorkerRuntimeContext, task: Task) -> WorkerOutput:
        self.lifecycle_log.append("EXECUTE_TASK")
        return WorkerOutput(success=True, summary="Lifecycle completed")

    def after_task(self, context: WorkerRuntimeContext, task: Task, output: WorkerOutput) -> None:
        self.lifecycle_log.append(f"AFTER_TASK(success={output.success})")

    def shutdown(self) -> None:
        self.lifecycle_log.append("SHUTDOWN")


class TestWorkerSDKLifecycle(unittest.TestCase):

    def test_complete_worker_lifecycle_hook_sequence(self):
        harness = WorkerTestHarness()
        worker = LifecycleTrackingWorker()

        output = harness.run(worker)
        self.assertTrue(output.success)

        expected_sequence = [
            "INITIALIZE",
            "VALIDATE_TASK",
            "BEFORE_TASK",
            "EXECUTE_TASK",
            "AFTER_TASK(success=True)",
            "SHUTDOWN",
        ]
        self.assertEqual(worker.lifecycle_log, expected_sequence)

    def test_worker_validation_rejection(self):
        harness = WorkerTestHarness()
        worker = LifecycleTrackingWorker()

        bad_task = Task(
            id="task-bad",
            project_id="proj-1",
            title="INCOMPATIBLE Task",
            objective="Should fail validation",
            created_at=utc_now(),
        )

        with self.assertRaises(WorkerNotEligibleError):
            harness.run(worker, task=bad_task)


if __name__ == "__main__":
    unittest.main()

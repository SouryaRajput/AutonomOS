from pathlib import Path
import shutil
import tempfile
import unittest

from core.enums import TaskStatus, WorkerStatus
from core.events.types import EventType
from core.runtime.workforce_runtime import WorkforceRuntime
from core.safety.model import SafetyConfig
from core.verification.model import SuccessCriterion
from core.verification.types import CheckType
from workers.file_inspector_worker import FileInspectorWorker


class TestWorkerSDKGoldenPath(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.db_path = str(Path(self.temp_dir) / "autonomos.db")
        self.runtime = WorkforceRuntime.with_sqlite(self.db_path)
        self.runtime.safety_config = SafetyConfig(auto_checkpoint=True, max_retries=1)

        self.project = self.runtime.projects.create_project(
            name="SDK Golden Path Project",
            description="Testing full Worker SDK integration in runtime",
            root_path=self.workspace,
        )

        self.worker = FileInspectorWorker(
            worker_id="worker.sdk.inspector",
            target_file="src/calculator.py",
            sample_file_content="def add(a, b):\n    return a + b\n",
        )
        self.runtime.workers.register_worker(self.worker)

    def tearDown(self):
        self.runtime.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_file_inspector_worker_golden_path_execution(self):
        # 1. Create Task with SuccessCriteria requiring the target file and report
        task = self.runtime.tasks.create_task(
            project_id=self.project.id,
            title="Inspect Calculator Module",
            objective="Ensure src/calculator.py is analyzed and documented",
        )
        task.success_criteria = [
            SuccessCriterion(
                id="crit-target-file",
                description="Verify src/calculator.py exists",
                check_type=CheckType.FILE_EXISTS,
                parameters={"path": "src/calculator.py"},
                required=True,
            ).to_dict(),
        ]
        self.runtime.store.save_task(task)

        # 2. Assign Task to Worker
        assigned_task, assigned_worker = self.runtime.assign_task(task.id, self.worker.get_manifest().id)
        self.assertEqual(assigned_task.status, TaskStatus.ASSIGNED)
        self.assertEqual(assigned_worker.status, WorkerStatus.ASSIGNED)

        # 3. Run Task through WorkforceRuntime
        output = self.runtime.run_task(task.id)

        # 4. Assertions on Output & State
        self.assertTrue(output.success, msg=f"Worker failed with summary: {output.summary}, error: {output.error_message}")
        self.assertIn("File inspection completed", output.summary)

        completed_task = self.runtime.tasks.get_task(task.id)
        self.assertEqual(completed_task.status, TaskStatus.COMPLETED)

        # Verify physical files created in workspace
        self.assertTrue((Path(self.workspace) / "src" / "calculator.py").exists())
        self.assertTrue((Path(self.workspace) / "reports" / f"inspection_{task.id}.md").exists())

        # Verify full Event stream
        events = self.runtime.get_events(task_id=task.id)
        event_types = [e.event_type for e in events]

        self.assertIn(EventType.TASK_STARTED, event_types)
        self.assertIn(EventType.WORKER_STARTED, event_types)
        self.assertIn(EventType.CHECKPOINT_CREATED, event_types)
        self.assertIn(EventType.WORKER_PROGRESS_LOGGED, event_types)
        self.assertIn(EventType.TOOL_REQUESTED, event_types)
        self.assertIn(EventType.TOOL_COMPLETED, event_types)
        self.assertIn(EventType.INFERENCE_REQUESTED, event_types)
        self.assertIn(EventType.INFERENCE_COMPLETED, event_types)
        self.assertIn(EventType.ARTIFACT_CREATED, event_types)
        self.assertIn(EventType.EVIDENCE_RECORDED, event_types)
        self.assertIn(EventType.VERIFICATION_PASSED, event_types)
        self.assertIn(EventType.TASK_COMPLETED, event_types)


if __name__ == "__main__":
    unittest.main()

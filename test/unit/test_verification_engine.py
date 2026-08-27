from pathlib import Path
import shutil
import tempfile
import unittest

from core.models import Project, Task, WorkerManifest, utc_now
from core.runtime.artifact_registry import ArtifactRegistry
from core.safety.checkpoint import CheckpointManager
from core.safety.model import SafetyConfig
from core.storage.memory_store import MemoryStore
from core.tools.runtime import ToolRuntime
from core.verification.engine import VerificationEngine
from core.verification.model import SuccessCriterion, VerificationPlan
from core.verification.types import CheckType, VerificationStatus


class TestVerificationEngine(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.store = MemoryStore()
        self.artifacts = ArtifactRegistry(self.store)
        self.checkpoints = CheckpointManager(self.store)
        self.tools = ToolRuntime(
            store=self.store,
            artifact_registry=self.artifacts,
            checkpoint_manager=self.checkpoints,
            safety_config=SafetyConfig(),
        )

        self.engine = VerificationEngine(
            tool_runtime=self.tools,
            artifact_registry=self.artifacts,
            store=self.store,
        )

        self.project = Project(
            id="proj-ve-1",
            name="Engine Project",
            description="Testing engine",
            root_path=self.workspace,
            created_at=utc_now(),
        )
        self.store.save_project(self.project)

        self.task = Task(
            id="task-ve-1",
            project_id="proj-ve-1",
            title="Engine Task",
            objective="Testing engine aggregation",
            created_at=utc_now(),
        )
        self.store.save_task(self.task)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_all_required_checks_pass(self):
        (Path(self.workspace) / "file1.txt").write_text("ok")
        (Path(self.workspace) / "file2.txt").write_text("ok")

        plan = VerificationPlan(
            id="plan-1",
            task_id=self.task.id,
            criteria=[
                SuccessCriterion(id="c1", description="f1 exists", check_type=CheckType.FILE_EXISTS, parameters={"path": "file1.txt"}, required=True),
                SuccessCriterion(id="c2", description="f2 exists", check_type=CheckType.FILE_EXISTS, parameters={"path": "file2.txt"}, required=True),
            ],
        )

        result = self.engine.verify_task(self.project, self.task, plan=plan)
        self.assertEqual(result.status, VerificationStatus.PASSED)
        self.assertIn("satisfied criteria", result.summary)
        self.assertIn("Verification Report", result.report_markdown)

    def test_required_check_fails(self):
        (Path(self.workspace) / "file1.txt").write_text("ok")
        # file2 missing

        plan = VerificationPlan(
            id="plan-2",
            task_id=self.task.id,
            criteria=[
                SuccessCriterion(id="c1", description="f1 exists", check_type=CheckType.FILE_EXISTS, parameters={"path": "file1.txt"}, required=True),
                SuccessCriterion(id="c2", description="f2 exists", check_type=CheckType.FILE_EXISTS, parameters={"path": "file2.txt"}, required=True),
            ],
        )

        result = self.engine.verify_task(self.project, self.task, plan=plan)
        self.assertEqual(result.status, VerificationStatus.FAILED)
        self.assertIn("1 required check(s) failed", result.summary)
        self.assertIn("Failure & Diagnostic Details", result.report_markdown)

    def test_optional_check_fails_while_required_passes(self):
        (Path(self.workspace) / "required_file.txt").write_text("ok")
        # optional missing

        plan = VerificationPlan(
            id="plan-3",
            task_id=self.task.id,
            criteria=[
                SuccessCriterion(id="c1", description="req exists", check_type=CheckType.FILE_EXISTS, parameters={"path": "required_file.txt"}, required=True),
                SuccessCriterion(id="c2", description="optional exists", check_type=CheckType.FILE_EXISTS, parameters={"path": "opt_missing.txt"}, required=False),
            ],
        )

        result = self.engine.verify_task(self.project, self.task, plan=plan)
        self.assertEqual(result.status, VerificationStatus.PASSED)


if __name__ == "__main__":
    unittest.main()

import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from core.enums import ArtifactType
from core.models import Project, Task, WorkerManifest, utc_now
from core.runtime.artifact_registry import ArtifactRegistry
from core.safety.checkpoint import CheckpointManager
from core.safety.model import SafetyConfig
from core.storage.memory_store import MemoryStore
from core.tools.runtime import ToolRuntime
from core.verification.adapters.artifact_adapter import ArtifactCheckAdapter
from core.verification.adapters.base import CheckExecutionContext
from core.verification.adapters.command_adapter import CommandCheckAdapter
from core.verification.adapters.file_adapter import FileCheckAdapter, compute_file_sha256
from core.verification.adapters.git_adapter import GitCheckAdapter
from core.verification.model import SuccessCriterion
from core.verification.types import CheckStatus, CheckType


class TestVerificationAdapters(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.store = MemoryStore()
        self.artifacts = ArtifactRegistry(self.store)
        self.checkpoints = CheckpointManager(self.store)
        self.tool_runtime = ToolRuntime(
            store=self.store,
            artifact_registry=self.artifacts,
            checkpoint_manager=self.checkpoints,
            safety_config=SafetyConfig(),
        )

        self.project = Project(
            id="proj-v-1",
            name="Verification Adapter Project",
            description="Testing adapters",
            root_path=self.workspace,
            created_at=utc_now(),
        )
        self.store.save_project(self.project)

        self.task = Task(
            id="task-v-1",
            project_id="proj-v-1",
            title="Adapter Task",
            objective="Testing adapters",
            created_at=utc_now(),
        )
        self.store.save_task(self.task)

        self.context = CheckExecutionContext(
            project_id=self.project.id,
            workspace_root=self.workspace,
            task_id=self.task.id,
            worker_id="verifier",
            tool_runtime=self.tool_runtime,
            artifact_registry=self.artifacts,
            store=self.store,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_file_check_adapter_exists_and_hash_and_content(self):
        adapter = FileCheckAdapter()
        test_file = Path(self.workspace) / "output.txt"
        test_file.write_text("Hello Verification Engine!\n")
        expected_hash = hashlib.sha256(test_file.read_bytes()).hexdigest()

        # 1. FILE_EXISTS -> PASSED
        c1 = SuccessCriterion(id="c1", description="exists", check_type=CheckType.FILE_EXISTS, parameters={"path": "output.txt"})
        res1 = adapter.execute(self.context, c1, "v1")
        self.assertEqual(res1.status, CheckStatus.PASSED)

        # 2. FILE_EXISTS on missing -> FAILED
        c2 = SuccessCriterion(id="c2", description="missing", check_type=CheckType.FILE_EXISTS, parameters={"path": "nonexistent.txt"})
        res2 = adapter.execute(self.context, c2, "v1")
        self.assertEqual(res2.status, CheckStatus.FAILED)

        # 3. FILE_HASH match -> PASSED
        c3 = SuccessCriterion(id="c3", description="hash", check_type=CheckType.FILE_HASH, parameters={"path": "output.txt", "expected_hash": expected_hash})
        res3 = adapter.execute(self.context, c3, "v1")
        self.assertEqual(res3.status, CheckStatus.PASSED)

        # 4. FILE_CONTENT_MATCH -> PASSED
        c4 = SuccessCriterion(id="c4", description="content", check_type=CheckType.FILE_CONTENT_MATCH, parameters={"path": "output.txt", "pattern": "Hello Verification"})
        res4 = adapter.execute(self.context, c4, "v1")
        self.assertEqual(res4.status, CheckStatus.PASSED)

    def test_command_check_adapter_execution_and_patterns(self):
        adapter = CommandCheckAdapter()

        # 1. Passing command (echo)
        c1 = SuccessCriterion(
            id="cmd-1",
            description="echo check",
            check_type=CheckType.COMMAND_OUTPUT_MATCH,
            parameters={"command": "echo 'VERIFIED_SUCCESS'", "pattern": "VERIFIED_SUCCESS", "expected_exit_code": 0},
        )
        res1 = adapter.execute(self.context, c1, "v1")
        self.assertEqual(res1.status, CheckStatus.PASSED)

        # 2. Failing exit code
        c2 = SuccessCriterion(
            id="cmd-2",
            description="failing check",
            check_type=CheckType.COMMAND_EXIT_CODE,
            parameters={"command": "python3 -c 'import sys; sys.exit(2)'", "expected_exit_code": 0},
        )
        res2 = adapter.execute(self.context, c2, "v1")
        self.assertEqual(res2.status, CheckStatus.FAILED)

    def test_artifact_check_adapter(self):
        adapter = ArtifactCheckAdapter()
        file_path = Path(self.workspace) / "artifact_doc.md"
        file_path.write_text("# Test Artifact\nContent")
        art = self.artifacts.register_artifact(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id="verifier",
            artifact_type=ArtifactType.REPORT,
            relative_path="artifact_doc.md",
            description="Sample artifact",
            content="# Test Artifact\nContent",
        )

        c1 = SuccessCriterion(
            id="art-1",
            description="Artifact registered",
            check_type=CheckType.ARTIFACT_EXISTS,
            parameters={"artifact_id": art.id},
        )
        res1 = adapter.execute(self.context, c1, "v1")
        self.assertEqual(res1.status, CheckStatus.PASSED)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from core.enums import ToolStatus
from core.models import Project, Task, WorkerManifest, utc_now
from core.storage.memory_store import MemoryStore
from core.tools.model import ToolRequest
from core.tools.runtime import ToolRuntime


class TestShellTool(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.store = MemoryStore()
        self.tool_runtime = ToolRuntime(store=self.store)

        self.project = Project(
            id="proj-shell-1",
            name="Shell Project",
            description="Shell execution test workspace",
            root_path=self.workspace,
            created_at=utc_now(),
        )
        self.store.save_project(self.project)

        self.task = Task(
            id="task-shell-1",
            project_id="proj-shell-1",
            title="Shell Task",
            objective="Shell execution test objective",
            created_at=utc_now(),
        )
        self.store.save_task(self.task)

        self.worker = WorkerManifest(
            id="worker.shell",
            name="Shell Worker",
            role="Programmer",
            description="Shell test worker",
            permissions=["shell.execute"],
            created_at=utc_now(),
        )
        self.store.save_worker(self.worker)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_successful_shell_command_execution(self):
        cmd = f'"{sys.executable}" -c "print(\'AUTONOMOS_SHELL_OK\')"'
        req = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="shell.execute",
            arguments={"command": cmd},
        )
        res = self.tool_runtime.execute_request(req)
        self.assertEqual(res.status, ToolStatus.SUCCESS)
        self.assertEqual(res.output["exit_code"], 0)
        self.assertIn("AUTONOMOS_SHELL_OK", res.output["stdout"])

    def test_failed_command_non_zero_exit_code(self):
        cmd = f'"{sys.executable}" -c "import sys; sys.exit(42)"'
        req = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="shell.execute",
            arguments={"command": cmd},
        )
        res = self.tool_runtime.execute_request(req)
        self.assertEqual(res.status, ToolStatus.FAILED)
        self.assertEqual(res.output["exit_code"], 42)

    def test_shell_command_timeout_detection(self):
        cmd = f'"{sys.executable}" -c "import time; time.sleep(5)"'
        req = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="shell.execute",
            arguments={"command": cmd, "timeout_seconds": 1},
        )
        res = self.tool_runtime.execute_request(req)
        self.assertEqual(res.status, ToolStatus.TIMEOUT)
        self.assertIn("timed out", res.error_message)


if __name__ == "__main__":
    unittest.main()

import os
from pathlib import Path
import shutil
import tempfile
import unittest

from core.enums import ToolStatus
from core.errors import ToolPermissionDeniedError, ToolWorkspaceViolationError
from core.models import Project, Task, WorkerManifest, utc_now
from core.storage.memory_store import MemoryStore
from core.tools.model import ToolRequest
from core.tools.policy import PermissionPolicy
from core.tools.runtime import ToolRuntime


class TestToolPermissionsAndSecurity(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_a = str(Path(self.temp_dir) / "project_a")
        self.workspace_b = str(Path(self.temp_dir) / "project_b")
        Path(self.workspace_a).mkdir(parents=True, exist_ok=True)
        Path(self.workspace_b).mkdir(parents=True, exist_ok=True)

        self.store = MemoryStore()
        self.tool_runtime = ToolRuntime(store=self.store)

        # Setup Project A
        self.project_a = Project(
            id="proj-a",
            name="Project A",
            description="Test Project A workspace",
            root_path=self.workspace_a,
            created_at=utc_now(),
        )
        self.store.save_project(self.project_a)

        # Setup Project B with secret file
        self.project_b = Project(
            id="proj-b",
            name="Project B",
            description="Test Project B workspace",
            root_path=self.workspace_b,
            created_at=utc_now(),
        )
        self.store.save_project(self.project_b)
        secret_b = Path(self.workspace_b) / "secret.txt"
        secret_b.write_text("CONFIDENTIAL_PROJECT_B_DATA")

        # Setup Task in Project A
        self.task_a = Task(
            id="task-a-1",
            project_id="proj-a",
            title="Task A 1",
            objective="Task A objective",
            created_at=utc_now(),
        )
        self.store.save_task(self.task_a)

        # Setup Workers with different permissions
        self.read_only_worker = WorkerManifest(
            id="worker.reader",
            name="Reader",
            role="Reader",
            description="Read-only worker",
            permissions=["filesystem.read"],
            created_at=utc_now(),
        )
        self.store.save_worker(self.read_only_worker)

        self.full_worker = WorkerManifest(
            id="worker.full",
            name="Full Worker",
            role="Programmer",
            description="Full permission worker",
            permissions=["*"],
            created_at=utc_now(),
        )
        self.store.save_worker(self.full_worker)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_path_traversal_attack_blocked(self):
        # Attempt to read outside project workspace using ../../
        with self.assertRaises(ToolWorkspaceViolationError):
            PermissionPolicy.validate_path_confinement("../../project_b/secret.txt", self.workspace_a)

        # Attempt via ToolRuntime execution
        req = ToolRequest(
            project_id="proj-a",
            task_id="task-a-1",
            worker_id="worker.full",
            tool_id="filesystem.read_file",
            arguments={"path": "../../project_b/secret.txt"},
        )
        result = self.tool_runtime.execute_request(req)
        self.assertEqual(result.status, ToolStatus.FAILED)
        self.assertIn("escapes workspace boundary", result.error_message)

    def test_absolute_system_path_escape_blocked(self):
        # Attempt to read absolute /etc/passwd or /windows/system32
        escape_path = "/etc/passwd" if os.name != "nt" else "C:\\Windows\\System32\\drivers\\etc\\hosts"
        with self.assertRaises(ToolWorkspaceViolationError):
            PermissionPolicy.validate_path_confinement(escape_path, self.workspace_a)

        req = ToolRequest(
            project_id="proj-a",
            task_id="task-a-1",
            worker_id="worker.full",
            tool_id="filesystem.read_file",
            arguments={"path": escape_path},
        )
        result = self.tool_runtime.execute_request(req)
        self.assertEqual(result.status, ToolStatus.FAILED)
        self.assertIn("escapes workspace boundary", result.error_message)

    def test_unauthorized_tool_request_denied_before_execution(self):
        # Read-only worker attempts to execute shell command
        req = ToolRequest(
            project_id="proj-a",
            task_id="task-a-1",
            worker_id="worker.reader",
            tool_id="shell.execute",
            arguments={"command": "echo hacked"},
        )
        result = self.tool_runtime.execute_request(req)
        self.assertEqual(result.status, ToolStatus.DENIED)
        self.assertIn("Permission denied", result.error_message)

    def test_read_only_worker_cannot_delete_or_write_file(self):
        # Read-only worker attempts write
        req_write = ToolRequest(
            project_id="proj-a",
            task_id="task-a-1",
            worker_id="worker.reader",
            tool_id="filesystem.write_file",
            arguments={"path": "test.txt", "content": "unauthorized"},
        )
        result_write = self.tool_runtime.execute_request(req_write)
        self.assertEqual(result_write.status, ToolStatus.DENIED)

        # Read-only worker attempts delete
        req_del = ToolRequest(
            project_id="proj-a",
            task_id="task-a-1",
            worker_id="worker.reader",
            tool_id="filesystem.delete_file",
            arguments={"path": "test.txt"},
        )
        result_del = self.tool_runtime.execute_request(req_del)
        self.assertEqual(result_del.status, ToolStatus.DENIED)

    def test_secret_environment_variable_redaction(self):
        # Set sensitive environment variable on host process
        os.environ["AWS_SECRET_ACCESS_KEY"] = "MOCK_SUPER_SECRET_KEY"
        os.environ["OPENAI_API_KEY"] = "sk-mock-12345"
        os.environ["DATABASE_PASSWORD"] = "secret_db_pass"

        try:
            sanitized = PermissionPolicy.sanitize_environment(include_host_safe=True)
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", sanitized)
            self.assertNotIn("OPENAI_API_KEY", sanitized)
            self.assertNotIn("DATABASE_PASSWORD", sanitized)
            # Safe vars must be preserved
            self.assertIn("PATH", sanitized)
        finally:
            del os.environ["AWS_SECRET_ACCESS_KEY"]
            del os.environ["OPENAI_API_KEY"]
            del os.environ["DATABASE_PASSWORD"]


if __name__ == "__main__":
    unittest.main()

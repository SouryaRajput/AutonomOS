from pathlib import Path
import shutil
import tempfile
import unittest

from core.enums import ToolStatus
from core.models import Project, Task, WorkerManifest, utc_now
from core.storage.memory_store import MemoryStore
from core.tools.model import ToolRequest
from core.tools.runtime import ToolRuntime


class TestFilesystemTool(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.store = MemoryStore()
        self.tool_runtime = ToolRuntime(store=self.store)

        self.project = Project(
            id="proj-fs-1",
            name="FS Project",
            description="FS test project workspace",
            root_path=self.workspace,
            created_at=utc_now(),
        )
        self.store.save_project(self.project)

        self.task = Task(
            id="task-fs-1",
            project_id="proj-fs-1",
            title="FS Task",
            objective="FS execution objective",
            created_at=utc_now(),
        )
        self.store.save_task(self.task)

        self.worker = WorkerManifest(
            id="worker.fs",
            name="FS Worker",
            role="Programmer",
            description="FS test worker",
            permissions=["filesystem.*"],
            created_at=utc_now(),
        )
        self.store.save_worker(self.worker)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_write_read_file_and_exists(self):
        # 1. Write file
        req_write = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="filesystem.write_file",
            arguments={"path": "src/hello.py", "content": "print('hello world')\n"},
        )
        res_write = self.tool_runtime.execute_request(req_write)
        self.assertEqual(res_write.status, ToolStatus.SUCCESS)

        # 2. Check exists
        req_exists = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="filesystem.file_exists",
            arguments={"path": "src/hello.py"},
        )
        res_exists = self.tool_runtime.execute_request(req_exists)
        self.assertEqual(res_exists.status, ToolStatus.SUCCESS)
        self.assertTrue(res_exists.output["exists"])

        # 3. Read file
        req_read = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="filesystem.read_file",
            arguments={"path": "src/hello.py"},
        )
        res_read = self.tool_runtime.execute_request(req_read)
        self.assertEqual(res_read.status, ToolStatus.SUCCESS)
        self.assertEqual(res_read.output["content"], "print('hello world')\n")

    def test_list_directory_and_stat_file(self):
        # Create subfiles
        src_dir = Path(self.workspace) / "pkg"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "mod1.py").write_text("# mod 1")
        (src_dir / "mod2.py").write_text("# mod 2")

        # List directory
        req_list = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="filesystem.list_directory",
            arguments={"path": "pkg"},
        )
        res_list = self.tool_runtime.execute_request(req_list)
        self.assertEqual(res_list.status, ToolStatus.SUCCESS)
        entries = [e["name"] for e in res_list.output["entries"]]
        self.assertIn("mod1.py", entries)
        self.assertIn("mod2.py", entries)

        # Stat file
        req_stat = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="filesystem.stat_file",
            arguments={"path": "pkg/mod1.py"},
        )
        res_stat = self.tool_runtime.execute_request(req_stat)
        self.assertEqual(res_stat.status, ToolStatus.SUCCESS)
        self.assertTrue(res_stat.output["is_file"])
        self.assertFalse(res_stat.output["is_dir"])
        self.assertEqual(res_stat.output["size"], 7)

    def test_delete_file(self):
        test_file = Path(self.workspace) / "temp.txt"
        test_file.write_text("temporary data")
        self.assertTrue(test_file.exists())

        req_del = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="filesystem.delete_file",
            arguments={"path": "temp.txt"},
        )
        res_del = self.tool_runtime.execute_request(req_del)
        self.assertEqual(res_del.status, ToolStatus.SUCCESS)
        self.assertFalse(test_file.exists())

    def test_read_missing_file_returns_failed_status(self):
        req_read = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="filesystem.read_file",
            arguments={"path": "does_not_exist.py"},
        )
        res_read = self.tool_runtime.execute_request(req_read)
        self.assertEqual(res_read.status, ToolStatus.FAILED)
        self.assertIn("not found", res_read.error_message)


if __name__ == "__main__":
    unittest.main()

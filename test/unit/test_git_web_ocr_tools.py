from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from core.enums import ToolStatus
from core.models import Project, Task, WorkerManifest, utc_now
from core.runtime.artifact_registry import ArtifactRegistry
from core.storage.memory_store import MemoryStore
from core.tools.builtins.web import MockWebAdapter, WebTool
from core.tools.model import ToolRequest
from core.tools.runtime import ToolRuntime


class TestGitWebOCRTools(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.store = MemoryStore()
        self.artifacts = ArtifactRegistry(self.store)
        self.tool_runtime = ToolRuntime(store=self.store, artifact_registry=self.artifacts)

        self.project = Project(
            id="proj-tools-1",
            name="Tools Project",
            description="Tools test workspace",
            root_path=self.workspace,
            created_at=utc_now(),
        )
        self.store.save_project(self.project)

        self.task = Task(
            id="task-tools-1",
            project_id="proj-tools-1",
            title="Tools Task",
            objective="Tools execution test objective",
            created_at=utc_now(),
        )
        self.store.save_task(self.task)

        self.worker = WorkerManifest(
            id="worker.tools",
            name="Tools Worker",
            role="Programmer",
            description="Tools test worker",
            permissions=["*"],
            created_at=utc_now(),
        )
        self.store.save_worker(self.worker)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_git_tool_in_initialized_repo(self):
        # Initialize real temporary git repo in workspace
        subprocess.run(["git", "init"], cwd=self.workspace, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@autonomos.ai"], cwd=self.workspace, capture_output=True)
        subprocess.run(["git", "config", "user.name", "AutonomOS Test"], cwd=self.workspace, capture_output=True)

        # 1. Git Status
        req_status = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="git",
            arguments={"action": "status"},
        )
        res_status = self.tool_runtime.execute_request(req_status)
        self.assertEqual(res_status.status, ToolStatus.SUCCESS)

        # 2. Add and Commit file
        (Path(self.workspace) / "file.txt").write_text("hello git")
        req_add = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="git",
            arguments={"action": "add", "path": "."},
        )
        res_add = self.tool_runtime.execute_request(req_add)
        self.assertEqual(res_add.status, ToolStatus.SUCCESS)

        req_commit = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="git",
            arguments={"action": "commit", "message": "Initial commit"},
        )
        res_commit = self.tool_runtime.execute_request(req_commit)
        self.assertEqual(res_commit.status, ToolStatus.SUCCESS)

        # 3. Git Log
        req_log = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="git",
            arguments={"action": "log", "max_count": 5},
        )
        res_log = self.tool_runtime.execute_request(req_log)
        self.assertEqual(res_log.status, ToolStatus.SUCCESS)
        self.assertIn("Initial commit", res_log.output["stdout"])

    def test_web_search_and_fetch_with_adapter(self):
        mock_adapter = MockWebAdapter({
            "https://autonomos.ai/docs": "# AutonomOS Documentation\nAutonomous workforce runtime.",
        })
        web_tool = WebTool("web", adapter=mock_adapter)
        self.tool_runtime.registry.register_tool(web_tool)

        # Search
        req_search = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="web",
            arguments={"action": "search", "query": "python async"},
        )
        res_search = self.tool_runtime.execute_request(req_search)
        self.assertEqual(res_search.status, ToolStatus.SUCCESS)
        self.assertGreaterEqual(len(res_search.output["results"]), 1)

        # Fetch
        req_fetch = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="web",
            arguments={"action": "fetch", "url": "https://autonomos.ai/docs"},
        )
        res_fetch = self.tool_runtime.execute_request(req_fetch)
        self.assertEqual(res_fetch.status, ToolStatus.SUCCESS)
        self.assertIn("AutonomOS Documentation", res_fetch.output["content"])

    def test_screenshot_capture_registers_artifact(self):
        req_screen = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="screenshot.capture",
            arguments={"filename": "screen_test.png"},
        )
        res_screen = self.tool_runtime.execute_request(req_screen)
        self.assertEqual(res_screen.status, ToolStatus.SUCCESS)
        self.assertEqual(len(res_screen.artifacts_created), 1)

        # Verify artifact registered and file created on disk
        art_id = res_screen.artifacts_created[0]
        art = self.artifacts.get_artifact(art_id)
        self.assertTrue(art.path.endswith("screen_test.png"))
        self.assertTrue((Path(self.workspace) / "screen_test.png").exists())

    def test_ocr_extraction_from_image(self):
        img_path = Path(self.workspace) / "sample_doc.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n...")

        req_ocr = ToolRequest(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
            tool_id="ocr.extract_text",
            arguments={"image_path": "sample_doc.png"},
        )
        res_ocr = self.tool_runtime.execute_request(req_ocr)
        self.assertEqual(res_ocr.status, ToolStatus.SUCCESS)
        self.assertIn("extracted_text", res_ocr.output)
        self.assertGreaterEqual(res_ocr.output["average_confidence"], 0.90)


if __name__ == "__main__":
    unittest.main()

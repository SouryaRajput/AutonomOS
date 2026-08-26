import pathlib
import shutil
import tempfile
import unittest

from core.enums import MemoryType
from core.memory.manager import MemoryManager
from core.models import Project, Task, WorkerManifest, utc_now
from core.storage.sqlite_store import SQLiteStore


class TestMemoryValidation(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(pathlib.Path(self.temp_dir) / "test_validation.db")
        self.project_workspace = str(pathlib.Path(self.temp_dir) / "workspace")
        pathlib.Path(self.project_workspace).mkdir(parents=True, exist_ok=True)

        self.store = SQLiteStore(self.db_path)
        self.project = Project(
            id="proj-val-1",
            name="Validation App",
            description="Testing reference validation",
            root_path=self.project_workspace,
            created_at=utc_now(),
        )
        self.store.save_project(self.project)
        self.manager = MemoryManager(self.store)

        # Create a real source file on disk
        self.real_file = pathlib.Path(self.project_workspace) / "src" / "main.py"
        self.real_file.parent.mkdir(parents=True, exist_ok=True)
        self.real_file.write_text("print('hello world')")

        # Create a real task in the store
        self.real_task = Task(
            id="task-valid-1",
            project_id=self.project.id,
            title="Real Task",
            objective="Objective",
            created_at=utc_now(),
        )
        self.store.save_task(self.real_task)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_valid_references_pass(self):
        doc = self.manager.create_memory(
            project_id=self.project.id,
            memory_type=MemoryType.TASK_MEMORY,
            title="Valid Memory",
            content="# Valid Memory",
            relative_path=".autonomos/tasks/valid.md",
            references=["src/main.py"],
            related_task_id="task-valid-1",
        )
        report = self.manager.validate_memory_references(self.project.id, doc.id)
        self.assertTrue(report.is_valid)
        self.assertEqual(len(report.broken_file_references), 0)
        self.assertEqual(len(report.broken_task_references), 0)

    def test_broken_file_and_task_references_detected(self):
        doc = self.manager.create_memory(
            project_id=self.project.id,
            memory_type=MemoryType.TASK_MEMORY,
            title="Stale Memory",
            content="# Stale Memory",
            relative_path=".autonomos/tasks/stale.md",
            references=["src/deleted_file.py", "nonexistent/module.py"],
            related_task_id="task-nonexistent-999",
            related_worker_id="worker.ghost",
        )
        report = self.manager.validate_memory_references(self.project.id, doc.id)
        self.assertFalse(report.is_valid)
        self.assertEqual(len(report.broken_file_references), 2)
        self.assertIn("src/deleted_file.py", report.broken_file_references)
        self.assertIn("nonexistent/module.py", report.broken_file_references)
        self.assertEqual(report.broken_task_references, ["task-nonexistent-999"])
        self.assertEqual(report.broken_worker_references, ["worker.ghost"])

    def test_audit_project_memory(self):
        self.manager.initialize_project_memory(self.project.id, self.project.name)
        reports = self.manager.audit_project_memory(self.project.id)
        self.assertGreaterEqual(len(reports), 3)
        # All default scaffold documents should be valid
        self.assertTrue(all(r.is_valid for r in reports))


if __name__ == "__main__":
    unittest.main()

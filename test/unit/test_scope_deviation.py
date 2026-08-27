from pathlib import Path
import shutil
import tempfile
import unittest

from core.models import Project, Task, WorkerManifest, utc_now
from core.safety.checkpoint import CheckpointManager
from core.safety.model import SafetyConfig
from core.safety.scope import ScopeTracker
from core.safety.types import ScopeDeviationType
from core.storage.memory_store import MemoryStore


class TestScopeDeviation(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.store = MemoryStore()
        self.checkpoint_manager = CheckpointManager(self.store)

        self.project = Project(
            id="proj-scope-1",
            name="Scope Project",
            description="Testing scope deviations",
            root_path=self.workspace,
            created_at=utc_now(),
        )
        self.store.save_project(self.project)

        self.task = Task(
            id="task-scope-1",
            project_id="proj-scope-1",
            title="Scope Task",
            objective="Modify auth files only",
            context_references=[{"path": "src/auth/service.py"}, {"path": "src/auth/routes.py"}],
            created_at=utc_now(),
        )
        self.store.save_task(self.task)

        self.worker = WorkerManifest(
            id="worker.scope",
            name="Scope Worker",
            role="Programmer",
            description="Test worker",
            created_at=utc_now(),
        )
        self.store.save_worker(self.worker)

        # Setup initial files
        auth_dir = Path(self.workspace) / "src" / "auth"
        auth_dir.mkdir(parents=True, exist_ok=True)
        (auth_dir / "service.py").write_text("def auth(): pass")
        (auth_dir / "routes.py").write_text("def routes(): pass")

        other_dir = Path(self.workspace) / "src" / "billing"
        other_dir.mkdir(parents=True, exist_ok=True)
        (other_dir / "payment.py").write_text("def pay(): pass")

        self.config = SafetyConfig(
            max_changed_files=3,
            protected_paths=[".env", "production.env", ".autonomos/memory/architecture.md"],
            strict_scope_enforcement=True,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_in_scope_modifications_produce_no_deviations(self):
        ckpt = self.checkpoint_manager.create_checkpoint(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
        )

        # Worker modifies only allowed auth files
        (Path(self.workspace) / "src" / "auth" / "service.py").write_text("def auth(): return True")

        deviations = ScopeTracker.check_deviations(
            checkpoint=ckpt,
            project_root=self.workspace,
            task=self.task,
            config=self.config,
        )
        self.assertEqual(len(deviations), 0)

    def test_out_of_scope_modification_detected(self):
        ckpt = self.checkpoint_manager.create_checkpoint(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
        )

        # Worker touches payment.py which is outside declared scope
        (Path(self.workspace) / "src" / "billing" / "payment.py").write_text("def pay(): return 'HACK'")

        deviations = ScopeTracker.check_deviations(
            checkpoint=ckpt,
            project_root=self.workspace,
            task=self.task,
            config=self.config,
        )
        self.assertEqual(len(deviations), 1)
        self.assertEqual(deviations[0].deviation_type, ScopeDeviationType.UNEXPECTED_FILE_MODIFIED)
        self.assertIn("src/billing/payment.py", deviations[0].target_path)

    def test_protected_path_modification_detected(self):
        ckpt = self.checkpoint_manager.create_checkpoint(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
        )

        # Worker creates/modifies .env
        (Path(self.workspace) / ".env").write_text("API_KEY=hacked")

        deviations = ScopeTracker.check_deviations(
            checkpoint=ckpt,
            project_root=self.workspace,
            task=self.task,
            config=self.config,
        )
        self.assertTrue(any(d.deviation_type == ScopeDeviationType.PROTECTED_PATH_MODIFIED for d in deviations))

    def test_max_files_exceeded_detected(self):
        ckpt = self.checkpoint_manager.create_checkpoint(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
        )

        # Worker creates 5 new files (limit is 3)
        for i in range(5):
            (Path(self.workspace) / f"file_{i}.txt").write_text(f"content {i}")

        deviations = ScopeTracker.check_deviations(
            checkpoint=ckpt,
            project_root=self.workspace,
            task=self.task,
            config=self.config,
        )
        self.assertTrue(any(d.deviation_type == ScopeDeviationType.MAX_FILES_EXCEEDED for d in deviations))


if __name__ == "__main__":
    unittest.main()

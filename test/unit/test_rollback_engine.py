from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from core.memory.manager import MemoryManager
from core.models import Project, Task, WorkerManifest, utc_now
from core.safety.checkpoint import CheckpointManager
from core.safety.rollback import RollbackManager
from core.safety.types import CheckpointStatus, RollbackStatus
from core.storage.memory_store import MemoryStore


class TestRollbackEngine(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.store = MemoryStore()
        self.memory = MemoryManager(self.store)
        self.checkpoint_manager = CheckpointManager(self.store)

        self.project = Project(
            id="proj-rb-1",
            name="Rollback Project",
            description="Testing rollbacks",
            root_path=self.workspace,
            created_at=utc_now(),
        )
        self.store.save_project(self.project)

        self.task = Task(
            id="task-rb-1",
            project_id="proj-rb-1",
            title="Rollback Task",
            objective="Testing rollbacks",
            created_at=utc_now(),
        )
        self.store.save_task(self.task)

        self.worker = WorkerManifest(
            id="worker.rb",
            name="Worker RB",
            role="Programmer",
            description="Test worker",
            created_at=utc_now(),
        )
        self.store.save_worker(self.worker)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_non_git_filesystem_rollback(self):
        # 1. Setup initial state
        initial_file = Path(self.workspace) / "config.json"
        initial_file.write_text('{"env": "production", "debug": false}')

        file_to_delete = Path(self.workspace) / "will_be_deleted.txt"
        file_to_delete.write_text("pre-existing file")

        # 2. Checkpoint
        ckpt = self.checkpoint_manager.create_checkpoint(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
        )

        # 3. Worker performs changes: modifies config, creates new file, deletes a file
        initial_file.write_text('{"env": "corrupted_bad_state"}')
        worker_created = Path(self.workspace) / "bad_worker_output.py"
        worker_created.write_text("raise SystemExit('broken')")
        file_to_delete.unlink()

        self.assertFalse(file_to_delete.exists())
        self.assertTrue(worker_created.exists())

        # 4. Rollback
        result = RollbackManager.rollback(
            checkpoint=ckpt,
            project_root=self.workspace,
            store=self.store,
            memory_manager=self.memory,
        )

        self.assertEqual(result.status, RollbackStatus.SUCCESS)
        self.assertTrue(result.verified)

        # 5. Verify restored state
        self.assertEqual(initial_file.read_text(), '{"env": "production", "debug": false}')
        self.assertTrue(file_to_delete.exists())
        self.assertEqual(file_to_delete.read_text(), "pre-existing file")
        self.assertFalse(worker_created.exists())
        self.assertEqual(ckpt.status, CheckpointStatus.ROLLED_BACK)

    def test_git_scoped_rollback_preserves_unrelated_user_work(self):
        # 1. Initialize Git repository
        subprocess.run(["git", "init"], cwd=self.workspace, capture_output=True)
        subprocess.run(["git", "config", "user.email", "user@autonomos.ai"], cwd=self.workspace, capture_output=True)
        subprocess.run(["git", "config", "user.name", "AutonomOS User"], cwd=self.workspace, capture_output=True)

        tracked_base = Path(self.workspace) / "base.py"
        tracked_base.write_text("def base(): return 1\n")
        subprocess.run(["git", "add", "."], cwd=self.workspace, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.workspace, capture_output=True)

        # 2. User has local uncommitted changes before task starts
        user_modified = Path(self.workspace) / "base.py"
        user_modified.write_text("def base(): return 'USER_UNCOMMITTED_WORK'\n")

        user_untracked = Path(self.workspace) / "user_draft.md"
        user_untracked.write_text("# User Draft\nDo not delete me during rollback!")

        # 3. Checkpoint created before worker executes
        ckpt = self.checkpoint_manager.create_checkpoint(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
        )

        # 4. Worker executes and creates worker files + mutates files
        worker_file = Path(self.workspace) / "worker_generated.py"
        worker_file.write_text("def worker(): return 'FAIL'\n")

        # Worker also accidentally modifies user's base.py
        user_modified.write_text("def base(): return 'WORKER_CORRUPTED_BASE'\n")

        # 5. Worker fails -> trigger rollback
        result = RollbackManager.rollback(
            checkpoint=ckpt,
            project_root=self.workspace,
            store=self.store,
            memory_manager=self.memory,
        )

        self.assertEqual(result.status, RollbackStatus.SUCCESS)
        self.assertTrue(result.verified)

        # 6. Verify: User's pre-existing work is strictly restored/preserved!
        self.assertEqual(user_modified.read_text(), "def base(): return 'USER_UNCOMMITTED_WORK'\n")
        self.assertTrue(user_untracked.exists())
        self.assertEqual(user_untracked.read_text(), "# User Draft\nDo not delete me during rollback!")

        # Worker generated file must be deleted
        self.assertFalse(worker_file.exists())

    def test_memory_documents_rolled_back_on_failure(self):
        # 1. Initial memory doc
        doc_init = self.memory.record_task_memory(
            project_id=self.project.id,
            task_id="task-initial",
            title="Initial Task Memory",
            objective="Base objective",
            findings="Initial findings",
        )

        # 2. Checkpoint
        ckpt = self.checkpoint_manager.create_checkpoint(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
        )

        # 3. Worker creates new memory doc during failed task
        doc_failed = self.memory.record_task_memory(
            project_id=self.project.id,
            task_id=self.task.id,
            title="Corrupted Task Memory",
            objective="Corrupted objective",
            findings="Corrupted findings",
        )
        self.assertIsNotNone(self.store.get_memory_document(doc_failed.id))

        # 4. Rollback
        result = RollbackManager.rollback(
            checkpoint=ckpt,
            project_root=self.workspace,
            store=self.store,
            memory_manager=self.memory,
        )

        self.assertEqual(result.status, RollbackStatus.SUCCESS)
        # Verify failed memory doc deleted, initial remains
        self.assertIsNone(self.store.get_memory_document(doc_failed.id))
        self.assertIsNotNone(self.store.get_memory_document(doc_init.id))


if __name__ == "__main__":
    unittest.main()

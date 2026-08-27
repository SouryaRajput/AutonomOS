from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from core.models import Project, Task, WorkerManifest, utc_now
from core.safety.checkpoint import CheckpointManager, compute_file_checksum
from core.safety.types import CheckpointStatus, CheckpointType
from core.storage.memory_store import MemoryStore


class TestCheckpointManager(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = str(Path(self.temp_dir) / "workspace")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

        self.store = MemoryStore()
        self.manager = CheckpointManager(self.store)

        self.project = Project(
            id="proj-chk-1",
            name="Checkpoint Project",
            description="Testing checkpoints",
            root_path=self.workspace,
            created_at=utc_now(),
        )
        self.store.save_project(self.project)

        self.task = Task(
            id="task-chk-1",
            project_id="proj-chk-1",
            title="Task 1",
            objective="Testing checkpoints",
            created_at=utc_now(),
        )
        self.store.save_task(self.task)

        self.worker = WorkerManifest(
            id="worker.chk",
            name="Worker 1",
            role="Programmer",
            description="Test worker",
            created_at=utc_now(),
        )
        self.store.save_worker(self.worker)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_non_git_filesystem_checkpoint_creation_and_lifecycle(self):
        # Create some files in workspace
        file_a = Path(self.workspace) / "file_a.txt"
        file_a.write_text("initial content A")
        sub_dir = Path(self.workspace) / "subdir"
        sub_dir.mkdir(parents=True, exist_ok=True)
        file_b = sub_dir / "file_b.txt"
        file_b.write_text("initial content B")

        # 1. Create Checkpoint
        ckpt = self.manager.create_checkpoint(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
        )

        self.assertEqual(ckpt.status, CheckpointStatus.ACTIVE)
        self.assertEqual(ckpt.checkpoint_type, CheckpointType.FILESYSTEM_SNAPSHOT)
        self.assertIn("file_a.txt", ckpt.state_reference["snapshot_map"])
        self.assertIn("subdir/file_b.txt", ckpt.state_reference["snapshot_map"])

        # 2. Verify retrieval
        retrieved = self.manager.get_checkpoint(ckpt.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.id, ckpt.id)

        active = self.manager.get_active_checkpoint_for_task(self.task.id)
        self.assertIsNotNone(active)
        self.assertEqual(active.id, ckpt.id)

        # 3. Commit Checkpoint
        self.manager.commit_checkpoint(ckpt.id)
        self.assertEqual(ckpt.status, CheckpointStatus.COMMITTED)
        self.assertIsNone(self.manager.get_active_checkpoint_for_task(self.task.id))

    def test_git_checkpoint_preserves_pre_existing_dirty_user_files(self):
        # Initialize Git repo
        subprocess.run(["git", "init"], cwd=self.workspace, capture_output=True)
        subprocess.run(["git", "config", "user.email", "user@autonomos.ai"], cwd=self.workspace, capture_output=True)
        subprocess.run(["git", "config", "user.name", "AutonomOS User"], cwd=self.workspace, capture_output=True)

        tracked_file = Path(self.workspace) / "tracked.py"
        tracked_file.write_text("print('version 1')\n")
        subprocess.run(["git", "add", "."], cwd=self.workspace, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.workspace, capture_output=True)

        # User introduces uncommitted local modifications
        tracked_file.write_text("print('user uncommitted work')\n")
        user_untracked = Path(self.workspace) / "user_notes.md"
        user_untracked.write_text("# User's personal notes\nDo not delete!")

        # Create checkpoint
        ckpt = self.manager.create_checkpoint(
            project_id=self.project.id,
            task_id=self.task.id,
            worker_id=self.worker.id,
        )

        self.assertEqual(ckpt.checkpoint_type, CheckpointType.GIT_WORKING_TREE)
        dirty_files = ckpt.state_reference.get("dirty_user_files", {})
        self.assertIn("tracked.py", dirty_files)
        self.assertIn("user_notes.md", dirty_files)

        # Verify backup files exist
        backup_dir = Path(ckpt.state_reference["backup_dir"])
        self.assertTrue((backup_dir / "tracked.py").exists())
        self.assertTrue((backup_dir / "user_notes.md").exists())


if __name__ == "__main__":
    unittest.main()

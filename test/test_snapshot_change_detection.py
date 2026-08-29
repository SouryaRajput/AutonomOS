import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from core.workspace.filesystem import ControlledWorkspaceFS
from core.workspace.snapshot import SnapshotEngine, ProjectSnapshot, WorkspaceChanges


class TestSnapshotChangeDetection(unittest.TestCase):
    """
    Focused unit test suite verifying persistent project snapshots
    and deterministic change detection.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="autonomos_test_snap_")
        self.workspace_root = Path(self.temp_dir) / "test_workspace"
        self.workspace_root.mkdir()
        self.fs = ControlledWorkspaceFS(self.workspace_root)
        self.engine = SnapshotEngine(self.fs)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _setup_initial_workspace(self):
        self.fs.create_file("src/main.py", "def run(): print('v1')")
        self.fs.create_file("src/utils.py", "def helper(): return 42")
        self.fs.create_file("docs/readme.md", "# Project Documentation")
        # Save initial snapshot
        snapshot, _ = self.engine.sync_snapshot()
        return snapshot

    def test_unchanged_repository(self):
        """Verify that an untouched workspace produces has_changes=False and zero diffs."""
        self._setup_initial_workspace()

        changes = self.engine.detect_changes()

        self.assertFalse(changes.has_changes)
        self.assertEqual(changes.added, [])
        self.assertEqual(changes.modified, [])
        self.assertEqual(changes.deleted, [])
        self.assertEqual(changes.renamed, [])
        self.assertEqual(changes.unchanged_count, 3)

    def test_new_file_detection(self):
        """Verify that creating a new file is detected in changes.added."""
        self._setup_initial_workspace()

        # Add a new file
        self.fs.create_file("src/auth.py", "class AuthManager: pass")

        changes = self.engine.detect_changes()

        self.assertTrue(changes.has_changes)
        self.assertIn("src/auth.py", changes.added)
        self.assertEqual(changes.modified, [])
        self.assertEqual(changes.deleted, [])
        self.assertEqual(changes.renamed, [])

    def test_modified_file_detection(self):
        """Verify that modifying a file's content is detected via SHA-256 in changes.modified."""
        self._setup_initial_workspace()

        # Overwrite file with new content
        self.fs.overwrite_file("src/main.py", "def run(): print('v2 updated')")

        changes = self.engine.detect_changes()

        self.assertTrue(changes.has_changes)
        self.assertEqual(changes.added, [])
        self.assertIn("src/main.py", changes.modified)
        self.assertEqual(changes.deleted, [])
        self.assertEqual(changes.renamed, [])

    def test_deleted_file_detection(self):
        """Verify that deleting a file is detected in changes.deleted."""
        self._setup_initial_workspace()

        # Delete documentation file
        self.fs.delete_file("docs/readme.md")

        changes = self.engine.detect_changes()

        self.assertTrue(changes.has_changes)
        self.assertEqual(changes.added, [])
        self.assertEqual(changes.modified, [])
        self.assertIn("docs/readme.md", changes.deleted)
        self.assertEqual(changes.renamed, [])

    def test_rename_and_move_detection(self):
        """Verify that renaming a file with identical SHA-256 is detected as a rename."""
        self._setup_initial_workspace()

        # Rename src/utils.py to src/helpers.py
        self.fs.rename_file("src/utils.py", "src/helpers.py")

        changes = self.engine.detect_changes()

        self.assertTrue(changes.has_changes)
        self.assertEqual(changes.added, [])
        self.assertEqual(changes.modified, [])
        self.assertEqual(changes.deleted, [])
        self.assertEqual(len(changes.renamed), 1)
        self.assertEqual(changes.renamed[0]["from"], "src/utils.py")
        self.assertEqual(changes.renamed[0]["to"], "src/helpers.py")

    def test_restart_and_persistence(self):
        """Verify snapshot persistence across sessions and disk reload."""
        self._setup_initial_workspace()

        # Instantiate fresh engine pointing to the same workspace (simulating app restart)
        new_engine = SnapshotEngine(ControlledWorkspaceFS(self.workspace_root))
        loaded_snapshot = new_engine.load_last_snapshot()

        self.assertIsNotNone(loaded_snapshot)
        self.assertEqual(loaded_snapshot.file_count, 3)
        self.assertIn("src/main.py", loaded_snapshot.files)

        # Verify no changes detected right after reload
        changes = new_engine.detect_changes()
        self.assertFalse(changes.has_changes)

        # Apply an edit and verify synchronization
        self.fs.create_file("src/config.py", "DEBUG = True")
        cur_snap, fresh_changes = new_engine.sync_snapshot()

        self.assertTrue(fresh_changes.has_changes)
        self.assertIn("src/config.py", fresh_changes.added)

        # After sync, subsequent check is clean
        subsequent_changes = new_engine.detect_changes()
        self.assertFalse(subsequent_changes.has_changes)


if __name__ == "__main__":
    unittest.main()

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from core.workspace.filesystem import ControlledWorkspaceFS, WorkspaceSecurityError


class TestWorkspaceFilesystemSecurity(unittest.TestCase):
    """
    Focused test suite verifying the Manager Workspace filesystem abstraction
    and strict security confinement boundary.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="autonomos_test_fs_")
        self.workspace_root = Path(self.temp_dir) / "project"
        self.workspace_root.mkdir()
        self.fs = ControlledWorkspaceFS(self.workspace_root)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    # --- 1. Security & Confinement Validation ---

    def test_prevent_path_traversal_dot_dot(self):
        """Verify that attempts to traverse outside workspace with '..' are rejected."""
        with self.assertRaises(WorkspaceSecurityError):
            self.fs.resolve_safe_path("../../etc/passwd")

        with self.assertRaises(WorkspaceSecurityError):
            self.fs.read_file("../../../secret.key")

        with self.assertRaises(WorkspaceSecurityError):
            self.fs.create_file("../outside.txt", "payload")

    def test_prevent_arbitrary_absolute_path_outside_workspace(self):
        """Verify that absolute paths outside workspace are strictly rejected."""
        outside_path = "/etc/hosts" if os.name != "nt" else "C:\\Windows\\System32"
        with self.assertRaises(WorkspaceSecurityError):
            self.fs.resolve_safe_path(outside_path)

        with self.assertRaises(WorkspaceSecurityError):
            self.fs.read_file(outside_path)

    def test_prevent_null_byte_injection(self):
        """Verify that null bytes in paths are blocked."""
        with self.assertRaises(WorkspaceSecurityError):
            self.fs.resolve_safe_path("safe.txt\0/etc/passwd")

    def test_protect_git_and_root_from_deletion(self):
        """Verify that .git and workspace root cannot be deleted."""
        git_dir = self.workspace_root / ".git"
        git_dir.mkdir(parents=True, exist_ok=True)

        with self.assertRaises(WorkspaceSecurityError):
            self.fs.delete_directory(".git", recursive=True)

        with self.assertRaises(WorkspaceSecurityError):
            self.fs.delete_directory("", recursive=True)

    # --- 2. Complete Filesystem CRUD Operations ---

    def test_file_create_read_overwrite_edit_delete(self):
        """Test full file lifecycle inside the confined workspace."""
        # 1. Create file
        p = self.fs.create_file("src/main.py", "print('hello world')")
        self.assertTrue(p.exists())
        self.assertTrue(self.fs.exists("src/main.py"))
        self.assertTrue(self.fs.is_file("src/main.py"))

        # Cannot create already existing file with create_file
        with self.assertRaises(FileExistsError):
            self.fs.create_file("src/main.py", "new content")

        # 2. Read file
        content = self.fs.read_file("src/main.py")
        self.assertEqual(content, "print('hello world')")

        # 3. Edit file (search & replace)
        self.fs.edit_file("src/main.py", "'hello world'", "'hello autonomos'")
        self.assertEqual(self.fs.read_file("src/main.py"), "print('hello autonomos')")

        # Edit non-existent snippet raises error
        with self.assertRaises(ValueError):
            self.fs.edit_file("src/main.py", "missing snippet", "foo")

        # 4. Overwrite file
        self.fs.overwrite_file("src/main.py", "def run(): pass")
        self.assertEqual(self.fs.read_file("src/main.py"), "def run(): pass")

        # 5. File hash and stat
        fhash = self.fs.compute_file_hash("src/main.py")
        self.assertGreater(len(fhash), 0)
        st = self.fs.stat_file("src/main.py")
        self.assertTrue(st["is_file"])

        # 6. Delete file
        self.fs.delete_file("src/main.py")
        self.assertFalse(self.fs.exists("src/main.py"))

    def test_file_rename_and_move(self):
        """Test renaming and moving files inside workspace."""
        self.fs.create_file("module.py", "data = 1")

        # Rename file
        self.fs.rename_file("module.py", "module_v2.py")
        self.assertFalse(self.fs.exists("module.py"))
        self.assertTrue(self.fs.exists("module_v2.py"))

        # Move file to subfolder
        self.fs.create_directory("pkg")
        self.fs.move_file("module_v2.py", "pkg/module.py")
        self.assertFalse(self.fs.exists("module_v2.py"))
        self.assertTrue(self.fs.exists("pkg/module.py"))
        self.assertEqual(self.fs.read_file("pkg/module.py"), "data = 1")

    def test_directory_create_rename_move_list_delete(self):
        """Test directory lifecycle operations inside workspace."""
        # 1. Create directory
        self.fs.create_directory("services/api")
        self.assertTrue(self.fs.exists("services/api"))
        self.assertTrue(self.fs.is_dir("services/api"))

        # Add files inside
        self.fs.create_file("services/api/routes.py", "# routes")
        self.fs.create_file("services/api/models.py", "# models")

        # 2. List directory
        items = self.fs.list_directory("services/api")
        item_names = [it["name"] for it in items]
        self.assertIn("routes.py", item_names)
        self.assertIn("models.py", item_names)

        # 3. Rename directory
        self.fs.rename_directory("services/api", "services/v1_api")
        self.assertFalse(self.fs.exists("services/api"))
        self.assertTrue(self.fs.exists("services/v1_api/routes.py"))

        # 4. Move directory
        self.fs.create_directory("backend")
        self.fs.move_directory("services/v1_api", "backend/api")
        self.assertTrue(self.fs.exists("backend/api/routes.py"))

        # 5. Delete directory
        self.fs.delete_directory("backend", recursive=True)
        self.assertFalse(self.fs.exists("backend"))

    def test_fs_audit_logging(self):
        """Verify that operations are logged to .autonomos/fs_audit.jsonl."""
        self.fs.create_file("app.py", "print('app')")
        self.fs.read_file("app.py")

        audit_path = self.workspace_root / ".autonomos" / "fs_audit.jsonl"
        self.assertTrue(audit_path.exists())

        with open(audit_path, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        actions = [entry["action"] for entry in lines]
        self.assertIn("create_file", actions)
        self.assertIn("read_file", actions)


if __name__ == "__main__":
    unittest.main()

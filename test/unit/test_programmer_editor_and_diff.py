from __future__ import annotations

import unittest

from core.enums import ToolStatus
from core.tools.model import ToolResult
from pkg.sdk.harness import WorkerTestHarness
from workers.programmer.editor import CodeEditor, ScopeViolationError
from workers.programmer.model import ProgrammingScope
from workers.programmer.types import FileChangeType


class TestProgrammerEditorAndDiff(unittest.TestCase):
    """Unit tests for CodeEditor, diff generation, and scope enforcement."""

    def setUp(self):
        self.harness = WorkerTestHarness()
        self.mock_files: dict[str, str] = {}

        def fake_fs_read(args):
            p = args.get("path")
            if p in self.mock_files:
                return ToolResult("res-1", "req-1", "filesystem.read_file", ToolStatus.SUCCESS, output=self.mock_files[p])
            return ToolResult("res-1", "req-1", "filesystem.read_file", ToolStatus.ERROR, error="File not found")

        def fake_fs_write(args):
            p = args.get("path")
            self.mock_files[p] = args.get("content", "")
            return ToolResult("res-1", "req-1", "filesystem.write_file", ToolStatus.SUCCESS, output={"bytes_written": len(self.mock_files[p])})

        def fake_fs_delete(args):
            p = args.get("path")
            self.mock_files.pop(p, None)
            return ToolResult("res-1", "req-1", "filesystem.delete_file", ToolStatus.SUCCESS, output={"deleted": True})

        self.harness.mock_tool("filesystem.read_file", fake_fs_read)
        self.harness.mock_tool("filesystem.write_file", fake_fs_write)
        self.harness.mock_tool("filesystem.delete_file", fake_fs_delete)

        self.scope = ProgrammingScope(
            allowed_paths=["src/auth/*", "tests/*"],
            excluded_paths=[".git"],
            max_files_modified=5,
        )
        self.editor = CodeEditor(self.harness.context, self.scope)

    def test_write_new_file_and_diff(self):
        change = self.editor.write_file(
            path="src/auth/service.py",
            content="def authenticate(token):\n    return token == 'valid'\n",
            description="Created auth service",
        )
        self.assertEqual(change.path, "src/auth/service.py")
        self.assertEqual(change.change_type, FileChangeType.CREATE)
        self.assertIsNone(change.original_checksum)
        self.assertIsNotNone(change.new_checksum)
        self.assertIn("+def authenticate(token):", change.diff)

    def test_modify_existing_file_and_diff(self):
        # First write
        self.editor.write_file("src/auth/service.py", "def authenticate():\n    pass\n")

        # Now modify
        change = self.editor.write_file(
            path="src/auth/service.py",
            content="def authenticate():\n    return True\n",
            description="Updated return value",
        )
        self.assertEqual(change.change_type, FileChangeType.MODIFY)
        self.assertIsNotNone(change.original_checksum)
        self.assertIn("-    pass", change.diff)
        self.assertIn("+    return True", change.diff)

    def test_delete_file(self):
        self.editor.write_file("src/auth/old.py", "old content\n")
        change = self.editor.delete_file("src/auth/old.py", description="Removed deprecated file")
        self.assertEqual(change.change_type, FileChangeType.DELETE)
        self.assertIn("-old content", change.diff)

    def test_rename_file(self):
        self.editor.write_file("src/auth/v1.py", "version 1 code\n")
        change = self.editor.rename_file(
            source_path="src/auth/v1.py",
            destination_path="src/auth/v2.py",
            description="Migrated v1 to v2",
        )
        self.assertEqual(change.change_type, FileChangeType.RENAME)
        self.assertEqual(change.path, "src/auth/v2.py")
        self.assertEqual(change.old_path, "src/auth/v1.py")

    def test_out_of_scope_write_raises_error(self):
        with self.assertRaises(ScopeViolationError):
            self.editor.write_file("unauthorized/hacked.py", "evil code")


if __name__ == "__main__":
    unittest.main()

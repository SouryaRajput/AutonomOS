import unittest

from core.errors import ToolNotFoundError
from core.models import WorkerManifest, utc_now
from core.tools.builtins.filesystem import FilesystemTool
from core.tools.builtins.shell import ShellTool
from core.tools.registry import ToolRegistry


class TestToolRegistry(unittest.TestCase):

    def setUp(self):
        self.registry = ToolRegistry()

    def test_register_and_get_tool(self):
        fs_tool = FilesystemTool("filesystem.read_file")
        self.registry.register_tool(fs_tool)

        retrieved = self.registry.get_tool("filesystem.read_file")
        self.assertEqual(retrieved.get_definition().id, "filesystem.read_file")

    def test_get_nonexistent_tool_raises_error(self):
        with self.assertRaises(ToolNotFoundError):
            self.registry.get_tool("nonexistent.tool")

    def test_unregister_tool(self):
        fs_tool = FilesystemTool("filesystem.write_file")
        self.registry.register_tool(fs_tool)
        self.assertTrue(self.registry.unregister_tool("filesystem.write_file"))
        self.assertFalse(self.registry.unregister_tool("filesystem.write_file"))

    def test_list_tools_and_find_by_capability(self):
        self.registry.register_tool(FilesystemTool("filesystem.read_file"))
        self.registry.register_tool(ShellTool("shell.execute"))

        all_tools = self.registry.list_tools()
        self.assertEqual(len(all_tools), 2)

        shell_tools = self.registry.find_by_capability("shell.execute")
        self.assertEqual(len(shell_tools), 1)
        self.assertEqual(shell_tools[0].get_definition().id, "shell.execute")

    def test_list_tools_for_worker_with_specific_permissions(self):
        self.registry.register_tool(FilesystemTool("filesystem.read_file"))
        self.registry.register_tool(FilesystemTool("filesystem.write_file"))
        self.registry.register_tool(ShellTool("shell.execute"))

        # Worker with read-only permission
        read_only_worker = WorkerManifest(
            id="worker.reader",
            name="Reader Worker",
            role="Reader",
            description="Read only test worker",
            permissions=["filesystem.read"],
            created_at=utc_now(),
        )
        available = self.registry.list_tools_for_worker(read_only_worker)
        tool_ids = [t.id for t in available]
        self.assertIn("filesystem.read_file", tool_ids)
        self.assertNotIn("filesystem.write_file", tool_ids)
        self.assertNotIn("shell.execute", tool_ids)

        # Worker with wildcard filesystem permission
        fs_worker = WorkerManifest(
            id="worker.fs",
            name="FS Worker",
            role="FS",
            description="FS wildcard test worker",
            permissions=["filesystem.*"],
            created_at=utc_now(),
        )
        available_fs = self.registry.list_tools_for_worker(fs_worker)
        fs_ids = [t.id for t in available_fs]
        self.assertIn("filesystem.read_file", fs_ids)
        self.assertIn("filesystem.write_file", fs_ids)
        self.assertNotIn("shell.execute", fs_ids)

        # Worker with root wildcard permission
        admin_worker = WorkerManifest(
            id="worker.admin",
            name="Admin Worker",
            role="Admin",
            description="Admin test worker",
            permissions=["*"],
            created_at=utc_now(),
        )
        available_admin = self.registry.list_tools_for_worker(admin_worker)
        self.assertEqual(len(available_admin), 3)


if __name__ == "__main__":
    unittest.main()

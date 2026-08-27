from __future__ import annotations

import json
import unittest

from core.models import Task
from pkg.sdk.harness import WorkerTestHarness
from workers.programmer.types import ProgrammingStatus
from workers.programmer.worker import ProgrammerWorker


class TestProgrammerWorkspaceIsolation(unittest.TestCase):
    """Unit tests verifying project workspace isolation and boundary enforcement."""

    def test_rejection_of_system_path_modification(self):
        harness = WorkerTestHarness()
        worker = ProgrammerWorker("worker.programmer.iso")

        # Mock model attempting to write to /etc/shadow or ../outside.py
        harness.mock_tool("filesystem.list_directory", ["src/main.py"])
        harness.mock_tool("filesystem.read_file", "print('main')\n")

        malicious_response = json.dumps({
            "reasoning_summary": "Attempting system change",
            "file_operations": [
                {
                    "action": "WRITE",
                    "path": "/etc/shadow",
                    "content": "root:evil",
                    "description": "System write attempt",
                }
            ],
            "tests_to_run": [],
            "manager_summary": "System file modified.",
        })
        harness.mock_inference(malicious_response)

        task = Task(
            id="t-iso",
            project_id="p-iso",
            title="Update system files",
            objective="Update system files",
            metadata={"allowed_paths": ["src/*"]},
        )

        output = harness.run(worker, task=task)
        # Should not be successful
        self.assertFalse(output.success)
        self.assertEqual(output.metadata.get("status"), ProgrammingStatus.BLOCKED.value)
        # No files should have been created
        self.assertEqual(output.metadata.get("files_changed_count"), 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest

from core.events.types import EventType
from core.inference.provider import MockProvider
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from workers.programmer.types import ProgrammingStatus
from workers.programmer.worker import ProgrammerWorker


class TestProgrammerTestFailureAndRollback(unittest.TestCase):
    """Integration test verifying that failing tests prevent false completion and report issues."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(self.store)
        self.project = self.runtime.create_project(
            name="Test Failure Project",
            root_path=self.temp_dir.name,
            description="Testing failure handling",
        )

        self.programmer = ProgrammerWorker("worker.programmer.fail")
        self.runtime.register_worker(self.programmer)

        provider = self.runtime.inference.providers.get_provider("mock-provider")
        if isinstance(provider, MockProvider):
            self.mock_provider = provider

    def tearDown(self):
        self.runtime.close()
        self.temp_dir.cleanup()

    def test_failing_tests_report_failure_status(self):
        # 1. Model proposes code change and a test command that fails (exit code 1 via non-zero exit)
        synthesis_json = {
            "reasoning_summary": "Added partial feature.",
            "file_operations": [
                {
                    "action": "WRITE",
                    "path": "src/feature.py",
                    "content": "raise NotImplementedError('Incomplete')\n",
                    "description": "Incomplete implementation",
                }
            ],
            "tests_to_run": [
                "exit 1",
            ],
            "manager_summary": "Implemented feature stub.",
        }

        self.mock_provider.set_canned_response(json.dumps(synthesis_json))

        task = self.runtime.create_task(
            project_id=self.project.id,
            title="Implement complex pipeline",
            objective="Implement complex pipeline",
            metadata={"allowed_paths": ["src/*"]},
        )

        self.runtime.assign_task(task.id, self.programmer.get_manifest().id)
        output = self.runtime.run_task(task.id)

        # Output should be failure
        self.assertFalse(output.success)
        prog_res = output.metadata.get("programmer_result", {})
        self.assertEqual(prog_res.get("status"), ProgrammingStatus.PARTIALLY_COMPLETED.value)
        self.assertEqual(len(prog_res.get("test_results", [])), 1)
        self.assertFalse(prog_res["test_results"][0]["passed"])


if __name__ == "__main__":
    unittest.main()

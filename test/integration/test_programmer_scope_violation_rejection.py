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


class TestProgrammerScopeViolationRejection(unittest.TestCase):
    """Integration test verifying rejection of out-of-scope modifications."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(self.store)
        self.project = self.runtime.create_project(
            name="Scope Project",
            root_path=self.temp_dir.name,
            description="Testing scope boundaries",
        )

        self.programmer = ProgrammerWorker("worker.programmer.scope")
        self.runtime.register_worker(self.programmer)

        self.mock_provider = self.runtime.providers.get_provider("mock-provider")

    def tearDown(self):
        self.runtime.close()
        self.temp_dir.cleanup()

    def test_out_of_scope_edit_causes_task_block(self):
        # 1. Model attempts to modify unauthorized files outside allowed scope
        synthesis_json = {
            "reasoning_summary": "Attempting changes across multiple modules.",
            "file_operations": [
                {
                    "action": "WRITE",
                    "path": "src/payments/stripe.py",
                    "content": "print('stripe')\n",
                    "description": "Unauthorized payment change",
                }
            ],
            "tests_to_run": [],
            "manager_summary": "Attempted out of scope edit.",
        }

        self.mock_provider.set_canned_response(json.dumps(synthesis_json))

        # 2. Create task with strict allowed_paths scope
        task = self.runtime.create_task(
            project_id=self.project.id,
            title="Update auth documentation only",
            objective="Update auth documentation only",
            metadata={
                "allowed_paths": ["docs/auth.md"],
            },
        )

        self.runtime.assign_task(task.id, self.programmer.get_manifest().id)
        output = self.runtime.run_task(task.id)
        self.assertFalse(output.success)
        prog_res = output.metadata.get("programmer_result", {})
        self.assertEqual(prog_res.get("status"), ProgrammingStatus.BLOCKED.value)
        self.assertEqual(len(prog_res.get("changes", [])), 0)


if __name__ == "__main__":
    unittest.main()

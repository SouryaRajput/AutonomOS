from __future__ import annotations

import json
import tempfile
import unittest

from core.enums import ArtifactType
from core.events.types import EventType
from core.inference.provider import MockProvider
from core.runtime.workforce_runtime import WorkforceRuntime
from core.storage.sqlite_store import SQLiteStore
from workers.programmer.types import ProgrammingMode, ProgrammingStatus
from workers.programmer.worker import ProgrammerWorker


class TestProgrammerBugfixGoldenPath(unittest.TestCase):
    """Integration test verifying bug-fix workflow, baseline awareness, and regression prevention."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(":memory:")
        self.runtime = WorkforceRuntime(self.store)
        self.project = self.runtime.create_project(
            name="Bugfix Project",
            root_path=self.temp_dir.name,
            description="Fix zero division in calculator service",
        )

        self.programmer = ProgrammerWorker("worker.programmer.bugfix")
        self.runtime.register_worker(self.programmer)

        provider = self.runtime.inference.providers.get_provider("mock-provider")
        if isinstance(provider, MockProvider):
            self.mock_provider = provider

    def tearDown(self):
        self.runtime.close()
        self.temp_dir.cleanup()

    def test_bugfix_reproduction_and_resolution(self):
        # 1. Model response applying bugfix and regression test
        synthesis_json = {
            "reasoning_summary": "Handled zero divisor safely by returning None or raising ValueError.",
            "file_operations": [
                {
                    "action": "WRITE",
                    "path": "src/calculator.py",
                    "content": (
                        "def divide(a: float, b: float) -> float | None:\n"
                        "    if b == 0:\n"
                        "        return None\n"
                        "    return a / b\n"
                    ),
                    "description": "Safe division handling zero denominator",
                },
                {
                    "action": "WRITE",
                    "path": "tests/test_calculator.py",
                    "content": (
                        "import unittest\n"
                        "from src.calculator import divide\n\n"
                        "class TestCalculator(unittest.TestCase):\n"
                        "    def test_divide_normal(self):\n"
                        "        self.assertEqual(divide(10, 2), 5.0)\n"
                        "    def test_divide_by_zero_safe(self):\n"
                        "        self.assertIsNone(divide(10, 0))\n"
                    ),
                    "description": "Regression test for zero division",
                },
            ],
            "tests_to_run": [
                "echo 'Running calculator regression suite: 2 passed'",
            ],
            "assumptions": [],
            "warnings": [],
            "self_review": {
                "checks_passed": True,
                "modified_files_in_scope": True,
                "tests_added_or_updated": True,
                "regression_risk": "LOW",
            },
            "manager_summary": "Fixed ZeroDivisionError and added regression test.",
        }

        self.mock_provider.set_canned_response(json.dumps(synthesis_json))

        # 2. Create bugfix task with baseline expectation
        task = self.runtime.create_task(
            project_id=self.project.id,
            title="Fix ZeroDivisionError in calculator service",
            objective="Fix ZeroDivisionError when denominator is zero and add regression test",
            metadata={
                "mode": "BUGFIX",
                "allowed_paths": ["src/calculator.py", "tests/test_calculator.py"],
                "test_expectations": ["echo 'Running calculator regression suite: 2 passed'"],
            },
        )

        self.runtime.assign_task(task.id, self.programmer.get_manifest().id)
        output = self.runtime.run_task(task.id)

        self.assertTrue(output.success)
        self.assertIn("Fixed ZeroDivisionError", output.summary)

        prog_res = output.metadata.get("programmer_result", {})
        self.assertEqual(prog_res.get("mode"), ProgrammingMode.BUGFIX.value)
        self.assertEqual(prog_res.get("status"), ProgrammingStatus.SUCCESS.value)


if __name__ == "__main__":
    unittest.main()

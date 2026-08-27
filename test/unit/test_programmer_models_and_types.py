from __future__ import annotations

import unittest

from workers.programmer.model import (
    BuildExecutionResult,
    ChangeRecord,
    FileChange,
    ProgrammerResult,
    ProgrammingPlan,
    ProgrammingScope,
    ProgrammingTaskSpec,
    SelfReviewResult,
    TestExecutionResult,
    compute_checksum,
)
from workers.programmer.types import (
    FileChangeType,
    ProgrammingMode,
    ProgrammingStatus,
    TestFailureType,
)


class TestProgrammerModelsAndTypes(unittest.TestCase):
    """Unit tests verifying serialization, deserialization, and integrity of Programmer data models."""

    def test_compute_checksum(self):
        content = "print('Hello AutonomOS')\n"
        cs1 = compute_checksum(content)
        cs2 = compute_checksum(content.encode("utf-8"))
        self.assertEqual(cs1, cs2)
        self.assertEqual(len(cs1), 64)

    def test_programming_scope_serialization(self):
        scope = ProgrammingScope(
            allowed_paths=["src/auth", "tests/auth"],
            excluded_paths=[".git", ".autonomos"],
            max_files_modified=5,
        )
        d = scope.to_dict()
        restored = ProgrammingScope.from_dict(d)
        self.assertEqual(restored.allowed_paths, ["src/auth", "tests/auth"])
        self.assertEqual(restored.excluded_paths, [".git", ".autonomos"])
        self.assertEqual(restored.max_files_modified, 5)

    def test_programming_task_spec_serialization(self):
        spec = ProgrammingTaskSpec(
            objective="Add OAuth2 login support",
            mode=ProgrammingMode.FEATURE,
            requirements=["Implement OAuth flow", "Support Google provider"],
            constraints=["No external heavy frameworks"],
            test_expectations=["python3 -m unittest test_oauth.py"],
        )
        d = spec.to_dict()
        restored = ProgrammingTaskSpec.from_dict(d)
        self.assertEqual(restored.objective, "Add OAuth2 login support")
        self.assertEqual(restored.mode, ProgrammingMode.FEATURE)
        self.assertEqual(len(restored.requirements), 2)
        self.assertEqual(len(restored.test_expectations), 1)

    def test_programming_plan_serialization(self):
        plan = ProgrammingPlan(
            plan_id="plan-123",
            objective="Refactor database layer",
            mode=ProgrammingMode.REFACTOR,
            planned_steps=["1. Inspect schema", "2. Refactor queries", "3. Test"],
            affected_files=["src/db.py"],
            estimated_complexity="HIGH",
        )
        d = plan.to_dict()
        restored = ProgrammingPlan.from_dict(d)
        self.assertEqual(restored.plan_id, "plan-123")
        self.assertEqual(restored.mode, ProgrammingMode.REFACTOR)
        self.assertEqual(len(restored.planned_steps), 3)

    def test_file_change_serialization(self):
        fc = FileChange(
            path="src/auth.py",
            change_type=FileChangeType.MODIFY,
            original_checksum="abc12345",
            new_checksum="def67890",
            diff="--- a/src/auth.py\n+++ b/src/auth.py\n@@ -1 +1 @@\n-old\n+new\n",
            description="Updated authentication handler",
        )
        d = fc.to_dict()
        restored = FileChange.from_dict(d)
        self.assertEqual(restored.path, "src/auth.py")
        self.assertEqual(restored.change_type, FileChangeType.MODIFY)
        self.assertEqual(restored.original_checksum, "abc12345")
        self.assertIn("+new", restored.diff)

    def test_test_and_build_execution_results_serialization(self):
        tr = TestExecutionResult(
            command="pytest tests/unit",
            exit_code=1,
            duration_ms=250.5,
            passed=False,
            output="AssertionError: expected True but got False",
            failure_classification=TestFailureType.IMPLEMENTATION_FAILURE,
            error_summary="AssertionError",
        )
        d_tr = tr.to_dict()
        restored_tr = TestExecutionResult.from_dict(d_tr)
        self.assertEqual(restored_tr.command, "pytest tests/unit")
        self.assertFalse(restored_tr.passed)
        self.assertEqual(restored_tr.failure_classification, TestFailureType.IMPLEMENTATION_FAILURE)

        br = BuildExecutionResult(
            command="cargo build --release",
            exit_code=0,
            duration_ms=1200.0,
            passed=True,
            output="Finished release [optimized] target(s)",
        )
        d_br = br.to_dict()
        restored_br = BuildExecutionResult.from_dict(d_br)
        self.assertTrue(restored_br.passed)
        self.assertEqual(restored_br.exit_code, 0)

    def test_change_record_and_programmer_result_serialization(self):
        cr = ChangeRecord(
            task_id="task-1",
            project_id="proj-1",
            files_created=["src/new.py"],
            files_modified=["src/main.py"],
            requirements_addressed=["Feature 1"],
            artifacts=["art-1"],
        )
        res = ProgrammerResult(
            task_id="task-1",
            project_id="proj-1",
            objective="Implement Feature 1",
            mode=ProgrammingMode.FEATURE,
            change_record=cr,
            status=ProgrammingStatus.SUCCESS,
            summary_for_manager="Feature 1 implemented cleanly.",
        )
        d_res = res.to_dict()
        restored_res = ProgrammerResult.from_dict(d_res)
        self.assertEqual(restored_res.task_id, "task-1")
        self.assertEqual(restored_res.status, ProgrammingStatus.SUCCESS)
        self.assertIsNotNone(restored_res.change_record)
        self.assertEqual(restored_res.change_record.files_created, ["src/new.py"])


if __name__ == "__main__":
    unittest.main()

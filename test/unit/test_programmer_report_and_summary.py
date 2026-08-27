from __future__ import annotations

import unittest

from workers.programmer.model import (
    ChangeRecord,
    FileChange,
    ProgrammerResult,
    ProgrammingPlan,
    SelfReviewResult,
    TestExecutionResult,
)
from workers.programmer.report import ProgrammingReportGenerator
from workers.programmer.types import (
    FileChangeType,
    ProgrammingMode,
    ProgrammingStatus,
)


class TestProgrammerReportAndSummary(unittest.TestCase):
    """Unit tests for implementation report and Manager summary generation."""

    def test_report_generation(self):
        ch = FileChange(
            path="src/auth.py",
            change_type=FileChangeType.MODIFY,
            original_checksum="11111111",
            new_checksum="22222222",
            diff="--- old\n+++ new\n",
            description="Added token validation",
        )
        tr = TestExecutionResult(
            command="python3 -m unittest test_auth.py",
            exit_code=0,
            duration_ms=45.2,
            passed=True,
        )
        cr = ChangeRecord(
            task_id="t-1",
            project_id="p-1",
            files_modified=["src/auth.py"],
            requirements_addressed=["Add token validation"],
            tests_run=[tr],
        )
        res = ProgrammerResult(
            task_id="t-1",
            project_id="p-1",
            objective="Add token validation",
            mode=ProgrammingMode.FEATURE,
            change_record=cr,
            changes=[ch],
            test_results=[tr],
            self_review=SelfReviewResult(checks_passed=True, regression_risk="LOW"),
            status=ProgrammingStatus.SUCCESS,
            summary_for_manager="Token validation completed.",
        )

        md = ProgrammingReportGenerator.generate_markdown_report(res)
        self.assertIn("# Implementation Report: Add token validation", md)
        self.assertIn("## 1. Executive Summary", md)
        self.assertIn("## 2. Requirements Traceability", md)
        self.assertIn("## 3. Changes Made", md)
        self.assertIn("src/auth.py", md)
        self.assertIn("```diff", md)
        self.assertIn("python3 -m unittest test_auth.py", md)

        summary = ProgrammingReportGenerator.generate_manager_summary(res)
        self.assertIn("Implementation completed", summary)
        self.assertIn("Files Changed: 0 created, 1 modified, 0 deleted.", summary)
        self.assertIn("Tests: 1 passed, 0 failed", summary)


if __name__ == "__main__":
    unittest.main()

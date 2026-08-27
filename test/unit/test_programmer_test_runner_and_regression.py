from __future__ import annotations

import unittest

from pkg.sdk.harness import WorkerTestHarness
from workers.programmer.model import TestExecutionResult
from workers.programmer.tester import TestRunner
from workers.programmer.types import TestFailureType


class TestProgrammerTestRunnerAndRegression(unittest.TestCase):
    """Unit tests for TestRunner failure classification, baseline tracking, and regression detection."""

    def test_failure_classification(self):
        fc1 = TestRunner.classify_failure("python3 test.py", "pytest: command not found", exit_code=127)
        self.assertEqual(fc1, TestFailureType.ENVIRONMENT_FAILURE)

        fc2 = TestRunner.classify_failure("python3 test.py", "ModuleNotFoundError: No module named 'fastapi'", exit_code=1)
        self.assertEqual(fc2, TestFailureType.DEPENDENCY_FAILURE)

        fc3 = TestRunner.classify_failure("python3 test.py", "SyntaxError: invalid syntax in test_auth.py", exit_code=1)
        self.assertEqual(fc3, TestFailureType.TEST_FAILURE)

        fc4 = TestRunner.classify_failure("python3 test.py", "AssertionError: expected 200 but got 401", exit_code=1)
        self.assertEqual(fc4, TestFailureType.IMPLEMENTATION_FAILURE)

        fc5 = TestRunner.classify_failure("python3 test.py", "Failed assertion", exit_code=1, is_preexisting=True)
        self.assertEqual(fc5, TestFailureType.PREEXISTING_FAILURE)

    def test_regression_detection(self):
        baseline = [
            TestExecutionResult("test_a", exit_code=0, duration_ms=10.0, passed=True),
            TestExecutionResult("test_b", exit_code=1, duration_ms=10.0, passed=False),
        ]

        # Case 1: test_a still passes, test_b still fails -> No new regression
        current_no_reg = [
            TestExecutionResult("test_a", exit_code=0, duration_ms=10.0, passed=True),
            TestExecutionResult("test_b", exit_code=1, duration_ms=10.0, passed=False),
        ]
        has_reg, notes = TestRunner.evaluate_regressions(baseline, current_no_reg)
        self.assertFalse(has_reg)
        self.assertEqual(len(notes), 0)

        # Case 2: test_a now fails -> Regression detected!
        current_with_reg = [
            TestExecutionResult("test_a", exit_code=1, duration_ms=10.0, passed=False),
            TestExecutionResult("test_b", exit_code=1, duration_ms=10.0, passed=False),
        ]
        has_reg, notes = TestRunner.evaluate_regressions(baseline, current_with_reg)
        self.assertTrue(has_reg)
        self.assertEqual(len(notes), 1)
        self.assertIn("Test regression detected on command 'test_a'", notes[0])


if __name__ == "__main__":
    unittest.main()

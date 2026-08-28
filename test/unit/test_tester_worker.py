import os
import shutil
import tempfile
import unittest

from core.enums import ArtifactType
from core.events.types import EventType
from core.models import Project, Task
from pkg.sdk.harness import WorkerTestHarness
from pkg.sdk.types import WorkerConfig
from workers.tester.executor import TestExecutor
from workers.tester.inspector import ImplementationInspector
from workers.tester.investigator import DefectInvestigator
from workers.tester.model import (
    Defect,
    RequirementTrace,
    TestResult,
    TestSuiteResult,
    TesterResult,
    TestingPlan,
    TestingScope,
    TestingTaskSpec,
)
from workers.tester.planner import TestingPlanner
from workers.tester.report import TestingReportGenerator
from workers.tester.types import (
    DefectSeverity,
    FailureClassification,
    RequirementVerificationStatus,
    RootCauseConfidence,
    TestCategory,
    TestExecutionStatus,
    TesterFinalStatus,
)
from workers.tester.ui_validator import UIValidator
from workers.tester.worker import TesterWorker


class TestTesterWorkerUnit(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.project_root = os.path.join(self.tmp_dir, "project")
        os.makedirs(self.project_root, exist_ok=True)
        self.project = Project(
            id="proj-tester-unit",
            name="Tester Unit Project",
            description="Testing QA worker",
            root_path=self.project_root,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_planner_requirement_decomposition(self):
        task = Task(
            id="task-test-plan-1",
            project_id=self.project.id,
            title="Implement user authentication and session persistence",
            objective="Build secure login endpoint and SQLite session token storage",
            success_criteria=[
                {"description": "Valid credentials return 200 OK with session token"},
                {"description": "Invalid credentials return 401 Unauthorized"},
            ],
            metadata={
                "affected_components": ["auth", "storage"],
                "changed_files": ["core/auth.py", "core/session.py"],
            },
        )
        spec = TestingPlanner.parse_task_spec(task)
        self.assertEqual(len(spec.requirements), 2)
        self.assertEqual(spec.affected_components, ["auth", "storage"])

        plan = TestingPlanner.create_plan(spec)
        self.assertIsNotNone(plan.plan_id)
        self.assertTrue(len(plan.planned_phases) >= 4)
        self.assertIn(TestCategory.UNIT, plan.test_categories)
        self.assertIn(TestCategory.SECURITY, plan.test_categories)
        self.assertIn(TestCategory.PERSISTENCE, plan.test_categories)
        self.assertEqual(len(plan.requirement_traces), 2)

    def test_investigator_failure_classification(self):
        harness = WorkerTestHarness(worker_id="worker.tester.test", project_id=self.project.id)
        context = harness.context
        investigator = DefectInvestigator(context)

        # 1. Implementation bug
        failed_test = TestResult(
            test_id="t-fail-1",
            name="test_auth_validation",
            category=TestCategory.UNIT,
            status=TestExecutionStatus.FAILED,
            exit_code=1,
            command="python3 -m unittest test_auth.py",
            output_snippet="AssertionError: Expected 200 but got 500",
            error_message="AssertionError: Expected 200 but got 500",
        )
        defect, record = investigator.investigate_failure(
            failed_test=failed_test,
            affected_requirement="Valid credentials return 200",
            affected_component="auth_service",
            relevant_files=["auth.py"],
        )
        self.assertEqual(defect.classification, FailureClassification.IMPLEMENTATION_BUG)
        self.assertEqual(defect.severity, DefectSeverity.HIGH)
        self.assertIn("AssertionError", defect.suspected_cause)
        self.assertEqual(record.reproducibility, "REPRODUCIBLE")

        # 2. Regression
        regr_test = TestResult(
            test_id="t-regr-1",
            name="test_existing_feature",
            category=TestCategory.REGRESSION,
            status=TestExecutionStatus.FAILED,
            exit_code=1,
            is_regression=True,
            output_snippet="AssertionError: regression occurred",
        )
        regr_defect, regr_record = investigator.investigate_failure(failed_test=regr_test)
        self.assertEqual(regr_defect.classification, FailureClassification.REGRESSION)
        self.assertEqual(regr_defect.severity, DefectSeverity.HIGH)

    def test_report_generator(self):
        plan = TestingPlan(
            plan_id="p-1",
            objective="Verify auth module",
            strategy_summary="Test unit and regression suites",
            test_categories=[TestCategory.UNIT, TestCategory.REGRESSION],
            requirement_traces=[
                RequirementTrace(
                    requirement_id="req-1",
                    description="Valid auth returns 200",
                    status=RequirementVerificationStatus.VERIFIED,
                    test_ids=["t-1"],
                )
            ],
        )
        suite = TestSuiteResult(
            suite_id="s-1",
            suite_name="Unit Suite",
            total=1,
            passed=1,
            failed=0,
            test_results=[
                TestResult(
                    test_id="t-1",
                    name="test_auth_ok",
                    category=TestCategory.UNIT,
                    status=TestExecutionStatus.PASSED,
                    exit_code=0,
                    command="pytest",
                )
            ],
        )
        result = TesterResult(
            task_id="task-123",
            project_id=self.project.id,
            objective="Verify auth module",
            plan=plan,
            suites=[suite],
            requirement_traces=plan.requirement_traces,
            status=TesterFinalStatus.VERIFIED,
            report_path="testing/auth_report.md",
        )

        md = TestingReportGenerator.generate_markdown_report(result)
        self.assertIn("# Test & Quality Assurance Report", md)
        self.assertIn("Requirement Traceability Matrix", md)
        self.assertIn("Unit Suite", md)

        summary = TestingReportGenerator.generate_manager_summary(result)
        self.assertIn("QA Evaluation Status: **VERIFIED**", summary)
        self.assertIn("1/1 passed", summary)

    def test_tester_worker_execution_clean_pass(self):
        # Create a real python test file in the project
        test_file_path = os.path.join(self.project_root, "test_calculator.py")
        with open(test_file_path, "w") as f:
            f.write("""
import unittest
class TestCalc(unittest.TestCase):
    def test_add(self):
        self.assertEqual(1 + 1, 2)
if __name__ == '__main__':
    unittest.main()
""")

        harness = WorkerTestHarness(worker_id="worker.tester.unit", project_id=self.project.id)
        harness.mock_tool("shell.execute", {
            "exit_code": 0,
            "stdout": "Ran 1 test in 0.001s\n\nOK",
            "stderr": "",
        })
        worker = TesterWorker("worker.tester.unit")

        task = Task(
            id="task-tester-exec-1",
            project_id=self.project.id,
            title="Test Calculator Addition",
            objective="Verify calculator addition implementation",
            success_criteria=[{"description": "Calculator addition passes all tests"}],
            metadata={
                "test_commands": [f"python3 {test_file_path}"],
            },
        )

        output = harness.run(worker, task)
        self.assertTrue(output.success)
        self.assertIn("QA Evaluation Status: **VERIFIED**", output.summary)
        self.assertEqual(len(output.created_artifacts), 1)

        # Check evidence was recorded
        self.assertGreater(len(output.metadata.get("evidence_ids", [])), 0)

        # Verify event sequence
        events = [e["event_type"] for e in harness.emitted_events]
        self.assertIn(EventType.TESTER_STARTED.value, events)
        self.assertIn(EventType.TEST_PLAN_CREATED.value, events)
        self.assertIn(EventType.TEST_STARTED.value, events)
        self.assertIn(EventType.TEST_COMPLETED.value, events)
        self.assertIn(EventType.TESTER_COMPLETED.value, events)

    def test_tester_worker_execution_with_failure(self):
        # Create a failing test file
        failing_file = os.path.join(self.project_root, "test_failing.py")
        with open(failing_file, "w") as f:
            f.write("""
import unittest
class TestFail(unittest.TestCase):
    def test_broken(self):
        self.assertEqual(1, 2, "Expected 1 to equal 2")
if __name__ == '__main__':
    unittest.main()
""")

        harness = WorkerTestHarness(worker_id="worker.tester.unit", project_id=self.project.id)
        harness.mock_tool("shell.execute", {
            "exit_code": 1,
            "stdout": "",
            "stderr": "AssertionError: 1 != 2",
        })
        worker = TesterWorker("worker.tester.unit")

        task = Task(
            id="task-tester-exec-fail",
            project_id=self.project.id,
            title="Test Failing Component",
            objective="Verify failing component",
            success_criteria=[{"description": "Component works without failure"}],
            metadata={
                "test_commands": [f"python3 {failing_file}"],
            },
        )

        output = harness.run(worker, task)
        self.assertFalse(output.success)
        self.assertIn("Defects Diagnosed", output.summary)
        self.assertEqual(output.metadata["defects_count"], 1)

        events = [e["event_type"] for e in harness.emitted_events]
        self.assertIn(EventType.DEFECT_DETECTED.value, events)
        self.assertIn(EventType.TESTER_FAILED.value, events)


if __name__ == "__main__":
    unittest.main()

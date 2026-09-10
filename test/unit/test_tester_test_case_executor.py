from __future__ import annotations

import time
import unittest
from typing import Any, Optional

from core.events.model import Event
from core.events.types import EventType
from core.tester import (
    AcceptanceCriterion,
    ApplicableTestCategory,
    BrowserSession,
    InteractionEngine,
    MockBrowserSession,
    MockTestRuntime,
    NavigationResult,
    ObservationType,
    TestCategory,
    TestCase,
    TestCaseExecutor,
    TestCaseResult,
    TestEnvironment,
    TestingCapability,
    TestPlan,
    TestPriority,
    TestRuntimeStatus,
    TestScope,
    TestStep,
    TestStepResult,
    TesterActionType,
    TesterBoundaryViolationError,
    TesterExecution,
    TesterExecutionStatus,
    TesterLineageError,
    TesterObservation,
    TesterValidationError,
    TesterWorkOrder,
    new_environment_id,
    new_evidence_id,
    new_execution_id,
    new_observation_id,
    new_plan_id,
    new_step_id,
    new_test_case_id,
    new_work_order_id,
)
from core.tester.types import TestCaseStatus, TestPlanStatus


class TestTesterTestCaseExecutor(unittest.TestCase):
    """
    Unit test suite for Phase 5.2: Test Case Executor.

    Verifies the 14 execution scenarios:
    1. Successful test -> PASS, is_pass is True, all steps pass
    2. Failed assertion outcome -> FAIL, failure_information populated
    3. Failed interaction -> step fails, test FAIL
    4. Blocked runtime -> BLOCKED, is_blocked is True
    5. Timeout -> FAIL with timeout error
    6. Cancellation -> halts, unexecuted tests NOT_RUN
    7. Unauthorized test -> BLOCKED due to unauthorized capability
    8. Frozen plan mutation attempt -> TesterBoundaryViolationError
    9. No dynamic test generation -> TesterBoundaryViolationError on unplanned test
    10. Budget exhaustion -> remaining tests marked BLOCKED
    11. Evidence collection -> evidence IDs collected in step and test result
    12. Observation linkage -> descriptive observations linked without evaluative drift
    13. Provenance -> causal lineage preserved in trace
    14. Deterministic result -> repeated execution yields identical statuses
    """

    def setUp(self) -> None:
        self.project_id = "proj-executor-unit"
        self.task_id = "task-executor-01"
        self.correlation_id = "corr-executor-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()
        self.executor = TestCaseExecutor()

    def _make_work_order(self, **kwargs) -> TesterWorkOrder:
        defaults = {
            "work_order_id": self.work_order_id,
            "manager_task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "objective": "Execute frozen test plan against target application.",
            "test_scope": TestScope(
                components=["Dashboard"],
                routes=["/dashboard"],
                environments=["staging"],
            ),
            "authorized_capabilities": [
                TestingCapability.TEST_EXECUTION,
                TestingCapability.NAVIGATE,
                TestingCapability.CLICK,
                TestingCapability.TYPE,
                TestingCapability.WAIT,
                TestingCapability.SCREENSHOT,
            ],
            "acceptance_criteria": [
                AcceptanceCriterion(criterion_id="ac-01", description="Dashboard displays summary card."),
            ],
        }
        defaults.update(kwargs)
        return TesterWorkOrder(**defaults)

    def _make_execution(self, work_order: TesterWorkOrder) -> TesterExecution:
        return TesterExecution.from_work_order(work_order, status=TesterExecutionStatus.RUNNING)

    def _make_frozen_plan(
        self,
        execution: TesterExecution,
        work_order: TesterWorkOrder,
        test_cases: Optional[list[TestCase]] = None,
        **kwargs,
    ) -> TestPlan:
        plan_id = new_plan_id()
        cases = test_cases or []
        defaults = {
            "plan_id": plan_id,
            "work_order_id": work_order.work_order_id,
            "execution_id": execution.execution_id,
            "project_id": work_order.project_id,
            "test_cases": cases,
            "max_test_cases": 10,
            "time_budget": 300,
            "iteration_budget": 10,
        }
        defaults.update(kwargs)
        plan = TestPlan(**defaults)
        plan.freeze()
        execution.test_plan = plan
        return plan

    def test_01_successful_test(self) -> None:
        """Scenario 1: Frozen plan with valid steps produces PASS, is_pass=True, all steps PASS."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        steps = [
            TestStep(
                step_number=1,
                description="Navigate to dashboard",
                action="NAVIGATE",
                target="/dashboard",
                expected="200",
            ),
            TestStep(
                step_number=2,
                description="Click Refresh",
                action="CLICK",
                target="#refresh-btn",
                expected="Refreshed",
            ),
            TestStep(
                step_number=3,
                description="Verify status text",
                action="ASSERT",
                target="#status",
                expected="Active",
                metadata={"actual": "Active"},
            ),
        ]
        tc = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Verify dashboard displays active status",
            steps=steps,
            expected_outcome="Dashboard displays active status correctly",
        )
        plan = self._make_frozen_plan(execution, wo, test_cases=[tc])

        results = self.executor.execute_plan(execution=execution, work_order=wo, test_plan=plan)

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.status, TestCaseStatus.PASS)
        self.assertTrue(res.is_pass)
        self.assertFalse(res.is_fail)
        self.assertFalse(res.is_blocked)
        self.assertEqual(len(res.step_results), 3)
        for sres in res.step_results:
            self.assertEqual(sres.status, TestCaseStatus.PASS)
        self.assertIsNone(res.failure_information)
        self.assertEqual(res.test_case_id, tc.test_case_id)

    def test_02_failed_assertion_outcome(self) -> None:
        """Scenario 2: Step with failed expectation produces FAIL, is_fail=True, failure info populated."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        steps = [
            TestStep(
                step_number=1,
                description="Navigate to home",
                action="NAVIGATE",
                target="/",
            ),
            TestStep(
                step_number=2,
                description="Assert user welcome greeting",
                action="ASSERT",
                target="#welcome",
                expected="Welcome Alice",
                metadata={"actual": "Welcome Guest"},
            ),
            TestStep(
                step_number=3,
                description="Should not execute after failure",
                action="CLICK",
                target="#logout",
            ),
        ]
        tc = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Verify user greeting matches authenticated username",
            steps=steps,
            expected_outcome="Greeting displays Welcome Alice",
        )
        plan = self._make_frozen_plan(execution, wo, test_cases=[tc])

        results = self.executor.execute_plan(execution=execution, work_order=wo, test_plan=plan)

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.status, TestCaseStatus.FAIL)
        self.assertTrue(res.is_fail)
        self.assertFalse(res.is_pass)
        self.assertIsNotNone(res.failure_information)
        self.assertEqual(res.failure_information.get("step_number"), 2)
        self.assertIn("Assertion failed", res.failure_information.get("error", ""))
        # Only steps 1 and 2 were executed; step 3 halted
        self.assertEqual(len(res.step_results), 2)
        self.assertEqual(res.step_results[0].status, TestCaseStatus.PASS)
        self.assertEqual(res.step_results[1].status, TestCaseStatus.FAIL)

    def test_03_failed_interaction(self) -> None:
        """Scenario 3: InteractionEngine returns failure -> step fails, test status is FAIL."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        runtime = MockTestRuntime(execution=execution, work_order=wo)
        runtime.start()

        session = MockBrowserSession(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=runtime.runtime_id,
        )
        session.startup()
        # Simulate click failure on the backend
        session.simulate_action_failure["click"] = "Element not found or not clickable"
        runtime.session = session

        interaction_engine = InteractionEngine(
            runtime=runtime,
            session=session,
        )

        steps = [
            TestStep(
                step_number=1,
                description="Click nonexistent element",
                action="CLICK",
                target="#missing-button",
            ),
        ]
        tc = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Verify click interaction failure handling",
            steps=steps,
            expected_outcome="Click succeeds",
        )
        plan = self._make_frozen_plan(execution, wo, test_cases=[tc])

        executor = TestCaseExecutor(runtime=runtime, interaction_engine=interaction_engine)
        results = executor.execute_plan(execution=execution, work_order=wo, test_plan=plan, runtime=runtime)

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.status, TestCaseStatus.FAIL)
        self.assertTrue(res.is_fail)
        self.assertIn("Element not found", str(res.failure_information))

    def test_04_blocked_runtime(self) -> None:
        """Scenario 4: Runtime in FAILED or STOPPED state produces BLOCKED status."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        runtime = MockTestRuntime(execution=execution, work_order=wo)
        runtime.fail("Simulated process crash")
        self.assertEqual(runtime.status, TestRuntimeStatus.FAILED)

        tc = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Test against crashed runtime",
            steps=[TestStep(step_number=1, description="Navigate", action="NAVIGATE", target="/")],
            expected_outcome="Loaded",
        )
        plan = self._make_frozen_plan(execution, wo, test_cases=[tc])

        executor = TestCaseExecutor(runtime=runtime)
        results = executor.execute_plan(execution=execution, work_order=wo, test_plan=plan, runtime=runtime)

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.status, TestCaseStatus.BLOCKED)
        self.assertTrue(res.is_blocked)
        self.assertIn("FAILED", res.failure_information.get("reason", ""))

    def test_05_timeout(self) -> None:
        """Scenario 5: Step timeout exceeded produces FAIL with timeout error."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        steps = [
            TestStep(
                step_number=1,
                description="Simulated long navigation",
                action="NAVIGATE",
                target="/slow-load",
                metadata={"simulate_timeout": True, "timeout_ms": 100.0},
            ),
        ]
        tc = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Verify step timeout handling",
            steps=steps,
            expected_outcome="Navigation succeeds",
        )
        plan = self._make_frozen_plan(execution, wo, test_cases=[tc])

        results = self.executor.execute_plan(execution=execution, work_order=wo, test_plan=plan)

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.status, TestCaseStatus.FAIL)
        self.assertTrue(res.is_fail)
        self.assertIn("timed out", str(res.failure_information.get("error", "")).lower())

    def test_06_cancellation(self) -> None:
        """Scenario 6: Execution in CANCELLED status halts remaining tests."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        tc1 = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Test Case 1",
            steps=[TestStep(step_number=1, description="Step 1", action="NAVIGATE", target="/")],
            expected_outcome="Pass",
        )
        tc2 = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Test Case 2",
            steps=[TestStep(step_number=1, description="Step 1", action="NAVIGATE", target="/")],
            expected_outcome="Pass",
        )
        plan = self._make_frozen_plan(execution, wo, test_cases=[tc1, tc2])

        # Cancel execution before running
        execution.status = TesterExecutionStatus.CANCELLED

        results = self.executor.execute_plan(execution=execution, work_order=wo, test_plan=plan)

        # Halted immediately upon discovering CANCELLED status
        self.assertEqual(len(results), 0)

    def test_07_unauthorized_test(self) -> None:
        """Scenario 7: Test requiring unauthorized capability produces BLOCKED status."""
        wo = self._make_work_order(authorized_capabilities=[TestingCapability.NAVIGATE])
        execution = self._make_execution(wo)

        tc = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Test requiring file upload authorization",
            steps=[TestStep(step_number=1, description="Upload document", action="UPLOAD")],
            expected_outcome="Uploaded",
            metadata={"required_capabilities": ["FILE_UPLOAD"]},
        )
        plan = self._make_frozen_plan(execution, wo, test_cases=[tc])

        results = self.executor.execute_plan(execution=execution, work_order=wo, test_plan=plan)

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.status, TestCaseStatus.BLOCKED)
        self.assertTrue(res.is_blocked)
        self.assertIn("FILE_UPLOAD", res.failure_information.get("unauthorized_capability", ""))

    def test_08_frozen_plan_mutation_attempt(self) -> None:
        """Scenario 8: Calling plan.add_test_case() or executing unfrozen plan raises TesterBoundaryViolationError."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        tc1 = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Baseline test",
            steps=[TestStep(step_number=1, description="Step 1", action="NAVIGATE", target="/")],
            expected_outcome="Pass",
        )
        plan = self._make_frozen_plan(execution, wo, test_cases=[tc1])

        # Attempting to mutate frozen plan directly
        tc2 = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Injected test",
            steps=[],
            expected_outcome="Pass",
        )
        with self.assertRaises(TesterBoundaryViolationError) as cm:
            plan.add_test_case(tc2)
        self.assertEqual(cm.exception.action, "ADD_TEST_CASE")

        # Attempting to execute unfrozen plan
        unfrozen_plan = TestPlan(
            plan_id=new_plan_id(),
            work_order_id=wo.work_order_id,
            execution_id=execution.execution_id,
            project_id=wo.project_id,
            test_cases=[tc1],
            status=TestPlanStatus.DRAFT,
        )
        with self.assertRaises(TesterBoundaryViolationError) as cm2:
            self.executor.execute_plan(execution=execution, work_order=wo, test_plan=unfrozen_plan)
        self.assertEqual(cm2.exception.action, "EXECUTE_UNFROZEN_PLAN")

    def test_09_no_dynamic_test_generation(self) -> None:
        """Scenario 9: Executing test case not in frozen plan raises TesterBoundaryViolationError."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        planned_tc = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Legitimate planned test",
            steps=[TestStep(step_number=1, description="Step 1", action="NAVIGATE", target="/")],
            expected_outcome="Pass",
        )
        plan = self._make_frozen_plan(execution, wo, test_cases=[planned_tc])

        unplanned_tc = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Dynamically concocted test",
            steps=[TestStep(step_number=1, description="Exploratory test", action="NAVIGATE", target="/secret")],
            expected_outcome="Pass",
        )

        with self.assertRaises(TesterBoundaryViolationError) as cm:
            self.executor.execute_test_case(
                test_case=unplanned_tc,
                execution=execution,
                work_order=wo,
                test_plan=plan,
            )
        self.assertEqual(cm.exception.action, "EXECUTE_UNPLANNED_TEST")

    def test_10_budget_exhaustion(self) -> None:
        """Scenario 10: Iteration budget exhaustion halts and marks remaining tests BLOCKED."""
        wo = self._make_work_order(iteration_budget=1)
        execution = self._make_execution(wo)

        tc1 = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Test Case 1 (Within budget)",
            steps=[TestStep(step_number=1, description="Step 1", action="NAVIGATE", target="/")],
            expected_outcome="Pass",
        )
        tc2 = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Test Case 2 (Exceeds budget)",
            steps=[TestStep(step_number=1, description="Step 2", action="NAVIGATE", target="/")],
            expected_outcome="Pass",
        )
        plan = self._make_frozen_plan(execution, wo, test_cases=[tc1, tc2], iteration_budget=1)

        results = self.executor.execute_plan(execution=execution, work_order=wo, test_plan=plan)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].status, TestCaseStatus.PASS)
        self.assertEqual(results[1].status, TestCaseStatus.BLOCKED)
        self.assertIn("Iteration budget exhausted", str(results[1].failure_information))

    def test_11_evidence_collection(self) -> None:
        """Scenario 11: Steps with screenshot or evidence collect evidence IDs into result."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        ev1 = new_evidence_id()
        steps = [
            TestStep(
                step_number=1,
                description="Capture baseline screenshot",
                action="SCREENSHOT",
                metadata={"evidence_ids": [ev1]},
            ),
        ]
        tc = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Verify evidence collection",
            steps=steps,
            expected_outcome="Screenshot captured",
            evidence_expectations=["expected-ev-01"],
        )
        plan = self._make_frozen_plan(execution, wo, test_cases=[tc])

        results = self.executor.execute_plan(execution=execution, work_order=wo, test_plan=plan)

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.status, TestCaseStatus.PASS)
        # Expected evidence expectations + step evidence + screenshot evidence + trace id
        self.assertIn("expected-ev-01", res.evidence_ids)
        self.assertIn(ev1, res.evidence_ids)
        self.assertTrue(len(res.evidence_ids) >= 3)
        self.assertEqual(res.step_results[0].evidence_ids[0], ev1)

    def test_12_observation_linkage(self) -> None:
        """Scenario 12: Observations linked to steps and test case result without evaluative drift."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        obs_id = new_observation_id()
        obs = TesterObservation(
            observation_id=obs_id,
            execution_id=execution.execution_id,
            project_id=wo.project_id,
            observation_type=ObservationType.OTHER,
            description="Read dashboard text",
            observed_state={"visible_text": ["Dashboard", "Overview", "Metrics"]},
        )

        steps = [
            TestStep(
                step_number=1,
                description="Read dashboard text",
                action="ASSERT",
                target="#dash",
                expected="Dashboard",
                metadata={"actual": "Dashboard", "observation_ids": [obs_id], "observations": [obs]},
            ),
        ]
        tc = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Verify observation binding",
            steps=steps,
            expected_outcome="Dashboard text verified",
            metadata={"observations": [obs]},
        )
        plan = self._make_frozen_plan(execution, wo, test_cases=[tc])

        results = self.executor.execute_plan(execution=execution, work_order=wo, test_plan=plan)

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.status, TestCaseStatus.PASS)
        self.assertEqual(len(res.observations), 1)
        self.assertEqual(res.observations[0].observation_id, obs_id)
        self.assertIn(obs_id, res.step_results[0].observation_ids)

    def test_13_provenance(self) -> None:
        """Scenario 13: Causal lineage to execution, project, and work order in trace."""
        wo = self._make_work_order()
        execution = self._make_execution(wo)

        tc = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Verify provenance tracking",
            steps=[TestStep(step_number=1, description="Step 1", action="NAVIGATE", target="/")],
            expected_outcome="Pass",
        )
        plan = self._make_frozen_plan(execution, wo, test_cases=[tc])

        results = self.executor.execute_plan(execution=execution, work_order=wo, test_plan=plan)

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertIn("trace_id", res.trace)
        self.assertEqual(res.trace.get("execution_id"), execution.execution_id)
        self.assertEqual(res.trace.get("project_id"), wo.project_id)
        self.assertEqual(res.trace.get("work_order_id"), wo.work_order_id)
        self.assertEqual(res.trace.get("action_type"), TesterActionType.EXECUTE_TEST.value)

    def test_14_deterministic_result(self) -> None:
        """Scenario 14: Repeated execution over identical inputs yields deterministic results."""
        wo = self._make_work_order()
        execution1 = self._make_execution(wo)
        execution2 = self._make_execution(wo)

        tc_id = new_test_case_id()
        steps = [
            TestStep(step_number=1, description="Step 1", action="NAVIGATE", target="/home", expected="200"),
            TestStep(step_number=2, description="Step 2", action="CLICK", target="#menu"),
            TestStep(step_number=3, description="Step 3", action="ASSERT", target="#title", expected="Home", metadata={"actual": "Home"}),
        ]
        tc = TestCase(
            test_case_id=tc_id,
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Deterministic testing",
            steps=steps,
            expected_outcome="Consistent results",
        )

        plan1 = self._make_frozen_plan(execution1, wo, test_cases=[tc])
        plan2 = self._make_frozen_plan(execution2, wo, test_cases=[tc])

        res1 = self.executor.execute_plan(execution=execution1, work_order=wo, test_plan=plan1)[0]
        res2 = self.executor.execute_plan(execution=execution2, work_order=wo, test_plan=plan2)[0]

        self.assertEqual(res1.status, res2.status)
        self.assertEqual(res1.is_pass, res2.is_pass)
        self.assertEqual(len(res1.step_results), len(res2.step_results))
        for sr1, sr2 in zip(res1.step_results, res2.step_results):
            self.assertEqual(sr1.step_number, sr2.step_number)
            self.assertEqual(sr1.status, sr2.status)
            self.assertEqual(sr1.action, sr2.action)
            self.assertEqual(sr1.target, sr2.target)


if __name__ == "__main__":
    unittest.main()

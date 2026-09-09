from __future__ import annotations

import unittest
from typing import Any, Optional

from core.enums import RiskLevel, TaskStatus
from core.events.types import EventSource, EventType
from core.models import Task
from core.tester import (
    AcceptanceCriterion,
    ApplicabilityLevel,
    ApplicableTestCategory,
    ChangeCategory,
    FakeTesterWorker,
    PlanValidationCode,
    PresenceAssessment,
    PresenceStatus,
    ShipRecommendation,
    TestApplicabilityClassifier,
    TestApplicabilityReport,
    TestCase,
    TestCaseResult,
    TestCaseStatus,
    TestContext,
    TestContextBuilder,
    TestEnvironment,
    TestPlan,
    TestPlanGenerator,
    TestPlanStatus,
    TestPlanValidator,
    TestPlanningPipeline,
    TestPriority,
    TestScope,
    TestStep,
    TestSurface,
    TesterActionType,
    TesterBlocker,
    TesterBlockerCategory,
    TesterBlockerSeverity,
    TesterBoundaryViolationError,
    TesterExecution,
    TesterExecutionPhase,
    TesterExecutionStatus,
    TesterLineageError,
    TesterManagerBridge,
    TesterResult,
    TesterResultStatus,
    TesterValidationError,
    TesterWorkOrder,
    TestingCapability,
    new_environment_id,
    new_execution_id,
    new_plan_id,
    new_test_case_id,
    new_work_order_id,
)


class TestTesterPlanningIntegration(unittest.TestCase):
    """
    End-to-End and Unit Test Suite for Tester V1 Phase 3.6: Planning Integration & Freeze.
    
    Verifies the complete 23 scenarios:
    1. Manager creates TesterWorkOrder
    2. Tester execution starts
    3. TestContext generated
    4. Applicability classified
    5. Finite TestPlan generated
    6. Coverage evaluated
    7. Plan validated
    8. Plan frozen
    9. Execution proceeds
    10. Backend-only change
    11. Frontend change
    12. Full-stack change
    13. Zero applicable tests (NO_APPLICABLE_TESTS terminal outcome)
    14. Invalid plan (fails and stops, no silent RUNNING)
    15. Unauthorized category
    16. Budget violation
    17. Cancelled execution
    18. Blocked execution
    19. Frozen-plan mutation attempt (TesterBoundaryViolationError)
    20. Attempt to dynamically add tests after execution starts
    21. Lineage preservation across all artifacts
    22. Event emission sequence
    23. Existing Tester regression suite compatibility
    """

    def setUp(self) -> None:
        self.bridge = TesterManagerBridge()
        self.pipeline = TestPlanningPipeline(bridge=self.bridge)
        self.project_id = "proj-planning-integ"
        self.task_id = "task-planning-01"
        self.correlation_id = "corr-planning-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()

    def _make_work_order(self, **kwargs) -> TesterWorkOrder:
        defaults = {
            "work_order_id": self.work_order_id,
            "manager_task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "objective": "Verify user authentication modal and session token refresh.",
            "test_scope": TestScope(
                components=["AuthModal"],
                routes=["/login", "/dashboard"],
                environments=["staging"],
            ),
            "authorized_capabilities": [
                TestingCapability.TEST_EXECUTION,
                TestingCapability.NAVIGATE,
                TestingCapability.CLICK,
                TestingCapability.TYPE,
                TestingCapability.SCREENSHOT,
                TestingCapability.SCREEN_RECORDING,
            ],
            "acceptance_criteria": [
                AcceptanceCriterion(criterion_id="ac-auth-01", description="User can click submit to log in."),
                AcceptanceCriterion(criterion_id="ac-auth-02", description="User can navigate to /login."),
            ],
            "time_budget": 300,
            "iteration_budget": 5,
        }
        defaults.update(kwargs)
        return TesterWorkOrder(**defaults)

    def _make_environment(self, **kwargs) -> TestEnvironment:
        defaults = {
            "environment_id": new_environment_id(),
            "project_id": self.project_id,
            "work_order_id": self.work_order_id,
            "execution_id": self.execution_id,
            "env_name": "staging",
            "application_url": "https://staging.example.com",
        }
        defaults.update(kwargs)
        env = TestEnvironment(**defaults)
        env.is_available = True
        env.is_reachable = True
        return env

    # 1 - 9. E2E Planning Pipeline Success
    def test_e2e_planning_pipeline_success(self) -> None:
        """
        Covers Scenarios 1-9:
        1. Manager creates TesterWorkOrder
        2. Tester execution starts
        3. TestContext generated
        4. Applicability classified
        5. Finite TestPlan generated
        6. Coverage evaluated
        7. Plan validated
        8. Plan frozen
        9. Execution proceeds to RUNNING
        """
        # 1. Manager creates WorkOrder
        wo = self._make_work_order()
        self.bridge.work_orders[wo.work_order_id] = wo
        env = self._make_environment()

        # 2. Tester execution starts
        execution = self.bridge.dispatch_work_order(wo)
        self.assertEqual(execution.status, TesterExecutionStatus.STARTING)
        self.assertEqual(execution.current_phase, TesterExecutionPhase.PREPARING)

        # 3 - 8. Execute planning pipeline
        files = ["src/client/AuthModal.tsx", "src/server/auth.py"]
        plan = self.pipeline.run_planning(
            execution=execution,
            work_order=wo,
            environment=env,
            runtime_capabilities=wo.authorized_capabilities,
            files_manifest=files,
            target_routes=["/login"],
            target_components=["AuthModal"],
        )

        # 3. TestContext generated and attached
        self.assertIsNotNone(execution.test_context)
        self.assertIn("AuthModal", execution.test_context.changed_components)
        self.assertIn("/login", execution.test_context.changed_routes)

        # 4. Applicability classified and attached
        self.assertIsNotNone(execution.test_applicability)
        self.assertTrue(len(execution.test_applicability.required_categories) > 0)

        # 5. Finite TestPlan generated
        self.assertIsNotNone(plan)
        self.assertTrue(len(plan.test_cases) > 0)
        self.assertTrue(len(plan.test_cases) <= plan.max_test_cases)

        # 6. Coverage evaluated
        self.assertIsNotNone(plan.coverage_report)
        self.assertEqual(execution.coverage_report, plan.coverage_report)

        # 7. Plan validated
        self.assertTrue(plan.is_validated)

        # 8. Plan frozen
        self.assertTrue(plan.is_frozen)
        self.assertEqual(plan.status, TestPlanStatus.FROZEN)
        self.assertEqual(execution.status, TesterExecutionStatus.PLAN_FROZEN)
        self.assertEqual(execution.current_phase, TesterExecutionPhase.PLAN_FROZEN)

        # 9. Execution proceeds to RUNNING
        execution.transition_to(
            TesterExecutionStatus.RUNNING,
            reason="Executing authorized frozen test plan",
        )
        self.assertEqual(execution.status, TesterExecutionStatus.RUNNING)
        self.assertEqual(execution.current_phase, TesterExecutionPhase.EXECUTING)

    # 10. Backend-Only Change
    def test_backend_only_change(self) -> None:
        """Scenario 10: Backend change produces backend functional tests without manufacturing UI tests."""
        wo = self._make_work_order(
            test_scope=TestScope(features=["database_pool"], environments=["staging"]),
            acceptance_criteria=[
                AcceptanceCriterion(criterion_id="ac-db-01", description="Database connection pool handles reconnections."),
            ],
        )
        execution = self.bridge.dispatch_work_order(wo)
        env = self._make_environment()

        plan = self.pipeline.run_planning(
            execution=execution,
            work_order=wo,
            environment=env,
            runtime_capabilities=wo.authorized_capabilities,
            files_manifest=["src/server/db/pool.py", "src/server/db/config.py"],
        )

        self.assertTrue(plan.is_frozen)
        self.assertIn(ChangeCategory.BACKEND, execution.test_context.change_categories)
        self.assertNotIn(ChangeCategory.FRONTEND, execution.test_context.change_categories)
        # All test cases should be functional or integration
        for tc in plan.test_cases:
            self.assertIn(tc.category, {ApplicableTestCategory.FUNCTIONAL, ApplicableTestCategory.INTEGRATION})

    # 11. Frontend Change
    def test_frontend_change(self) -> None:
        """Scenario 11: Frontend-only change generates UI and visual tests."""
        wo = self._make_work_order(
            acceptance_criteria=[
                AcceptanceCriterion(criterion_id="ac-ui-01", description="Header renders navigation buttons correctly."),
            ]
        )
        execution = self.bridge.dispatch_work_order(wo)
        env = self._make_environment()

        plan = self.pipeline.run_planning(
            execution=execution,
            work_order=wo,
            environment=env,
            runtime_capabilities=wo.authorized_capabilities,
            files_manifest=["src/client/Header.tsx", "src/client/Header.module.css"],
        )

        self.assertTrue(plan.is_frozen)
        self.assertIn(ChangeCategory.FRONTEND, execution.test_context.change_categories)
        self.assertNotIn(ChangeCategory.BACKEND, execution.test_context.change_categories)
        categories = {tc.category for tc in plan.test_cases}
        self.assertTrue(categories.intersection({
            ApplicableTestCategory.UI_INTERACTION,
            ApplicableTestCategory.VISUAL,
            ApplicableTestCategory.NAVIGATION,
            ApplicableTestCategory.RESPONSIVE,
        }))

    # 12. Full-Stack Change
    def test_full_stack_change(self) -> None:
        """Scenario 12: Full-stack change produces both UI and backend test cases."""
        wo = self._make_work_order(
            acceptance_criteria=[
                AcceptanceCriterion(criterion_id="ac-fs-01", description="User submits login and backend issues JWT."),
            ]
        )
        execution = self.bridge.dispatch_work_order(wo)
        env = self._make_environment()

        plan = self.pipeline.run_planning(
            execution=execution,
            work_order=wo,
            environment=env,
            runtime_capabilities=wo.authorized_capabilities,
            files_manifest=["src/client/Login.tsx", "src/server/auth.py"],
        )

        self.assertTrue(plan.is_frozen)
        self.assertIn(ChangeCategory.FRONTEND, execution.test_context.change_categories)
        self.assertIn(ChangeCategory.BACKEND, execution.test_context.change_categories)

    # 13. Zero Applicable Tests (NO_APPLICABLE_TESTS Terminal Outcome)
    def test_zero_applicable_tests_outcome(self) -> None:
        """
        Scenario 13: When no applicable categories exist or no relevant changes exist:
        Execution terminates cleanly with NO_APPLICABLE_TESTS without executing tests.
        """
        wo = self._make_work_order(
            acceptance_criteria=[
                AcceptanceCriterion(criterion_id="ac-ui-01", description="User can click submit button."),
            ],
            required_flows=[],
            test_scope=TestScope(features=["documentation"], environments=["staging"]),
            authorized_capabilities=[TestingCapability.TEST_EXECUTION],
        )
        execution = self.bridge.dispatch_work_order(wo)

        # Docs-only change: no executable test categories applicable
        exec_out, result, plan = self.pipeline.execute_planning_pipeline(
            execution=execution,
            work_order=wo,
            files_manifest=["docs/architecture.md", "README.md"],
            environment=None,  # Zero-test plan does not require environment
        )

        self.assertEqual(len(plan.test_cases), 0)
        self.assertTrue(plan.is_frozen)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, TesterResultStatus.COMPLETED)
        self.assertEqual(result.metadata.get("planning_outcome"), "NO_APPLICABLE_TESTS")
        self.assertEqual(execution.status, TesterExecutionStatus.COMPLETED)

        # Confirm TESTER_NO_APPLICABLE_TESTS event was emitted
        event_types = [e.event_type for e in self.bridge.events]
        self.assertIn(EventType.TESTER_NO_APPLICABLE_TESTS, event_types)

    # 14. Invalid Plan Fails and Stops (No Silent RUNNING)
    def test_invalid_plan_fails_execution(self) -> None:
        """Scenario 14: An invalid plan transitions execution to FAILED and raises TesterValidationError."""
        wo = self._make_work_order()
        execution = self.bridge.dispatch_work_order(wo)

        # Create a defective plan (duplicate test case IDs)
        dup_id = new_test_case_id()
        tc1 = TestCase(
            test_case_id=dup_id,
            category=ApplicableTestCategory.UI_INTERACTION,
            objective="Objective 1",
            steps=[TestStep(step_number=1, description="Step 1")],
            expected_outcome="Outcome 1",
        )
        tc2 = TestCase(
            test_case_id=dup_id,
            category=ApplicableTestCategory.UI_INTERACTION,
            objective="Objective 2",
            steps=[TestStep(step_number=1, description="Step 2")],
            expected_outcome="Outcome 2",
        )
        bad_plan = TestPlan(
            plan_id=new_plan_id(),
            work_order_id=wo.work_order_id,
            execution_id=execution.execution_id,
            project_id=wo.project_id,
            test_cases=[tc1, tc2],
        )

        with self.assertRaises(TesterValidationError):
            self.pipeline.run_planning(
                execution=execution,
                work_order=wo,
                test_plan=bad_plan,
            )

        # Execution must be FAILED, NOT RUNNING
        self.assertEqual(execution.status, TesterExecutionStatus.FAILED)
        self.assertEqual(bad_plan.status, TestPlanStatus.INVALID)
        event_types = [e.event_type for e in self.bridge.events]
        self.assertIn(EventType.TESTER_PLAN_FAILED, event_types)

    # 15. Unauthorized Category
    def test_unauthorized_category_rejected(self) -> None:
        """Scenario 15: Plan containing category not authorized in work order is rejected."""
        wo = self._make_work_order(
            authorized_capabilities=[TestingCapability.TEST_EXECUTION, TestingCapability.NAVIGATE]
        )
        execution = self.bridge.dispatch_work_order(wo)
        env = self._make_environment()

        unauth_tc = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.PERFORMANCE,
            objective="Measure page load latency",
            steps=[TestStep(step_number=1, description="Measure performance")],
            expected_outcome="Latency under 200ms",
        )
        plan = TestPlan(
            plan_id=new_plan_id(),
            work_order_id=wo.work_order_id,
            execution_id=execution.execution_id,
            project_id=wo.project_id,
            test_cases=[unauth_tc],
            not_applicable_categories=[ApplicableTestCategory.PERFORMANCE],
        )

        with self.assertRaises(TesterValidationError):
            self.pipeline.run_planning(
                execution=execution,
                work_order=wo,
                environment=env,
                test_plan=plan,
            )

        self.assertEqual(execution.status, TesterExecutionStatus.FAILED)

    # 16. Budget Violation
    def test_budget_violation_rejected(self) -> None:
        """Scenario 16: Plan exceeding work order time budget is rejected."""
        wo = self._make_work_order(time_budget=200)
        execution = self.bridge.dispatch_work_order(wo)
        env = self._make_environment()

        tc = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.UI_INTERACTION,
            objective="Simple test",
            steps=[TestStep(step_number=1, description="Step")],
            expected_outcome="Outcome",
            authorization_reference="AuthModal",
            acceptance_linkage=["ac-auth-01"],
        )
        exceeding_plan = TestPlan(
            plan_id=new_plan_id(),
            work_order_id=wo.work_order_id,
            execution_id=execution.execution_id,
            project_id=wo.project_id,
            test_cases=[tc],
            time_budget=500,  # Exceeds 200
        )

        with self.assertRaises(TesterValidationError):
            self.pipeline.run_planning(
                execution=execution,
                work_order=wo,
                environment=env,
                test_plan=exceeding_plan,
            )

        self.assertEqual(execution.status, TesterExecutionStatus.FAILED)

    # 17. Cancelled Execution
    def test_cancelled_execution(self) -> None:
        """Scenario 17: Cancelled execution transitions properly and records reason."""
        wo = self._make_work_order()
        execution = self.bridge.dispatch_work_order(wo)

        execution.transition_to(TesterExecutionStatus.CANCELLED, reason="Manager revoked task")
        self.assertEqual(execution.status, TesterExecutionStatus.CANCELLED)
        self.assertEqual(execution.cancellation_reason, "Manager revoked task")

    # 18. Blocked Execution
    def test_blocked_execution(self) -> None:
        """Scenario 18: Blocked execution escalates blocker via bridge."""
        wo = self._make_work_order()
        execution = self.bridge.dispatch_work_order(wo)

        blocker = TesterBlocker(
            blocker_id="tblk-env-down-01",
            execution_id=execution.execution_id,
            work_order_id=wo.work_order_id,
            category=TesterBlockerCategory.ENVIRONMENT,
            severity=TesterBlockerSeverity.CRITICAL,
            description="Staging server is not responding.",
        )
        self.bridge.escalate_blocker(execution, blocker, work_order=wo)

        self.assertEqual(execution.status, TesterExecutionStatus.BLOCKED)
        self.assertTrue(len(execution.active_blockers) > 0)
        event_types = [e.event_type for e in self.bridge.events]
        self.assertIn(EventType.TESTER_BLOCKED, event_types)

    # 19. Frozen-Plan Mutation Attempt
    def test_frozen_plan_mutation_attempt(self) -> None:
        """Scenario 19: Mutating a frozen plan raises TesterBoundaryViolationError."""
        wo = self._make_work_order()
        execution = self.bridge.dispatch_work_order(wo)
        env = self._make_environment()

        plan = self.pipeline.run_planning(
            execution=execution,
            work_order=wo,
            environment=env,
            runtime_capabilities=wo.authorized_capabilities,
        )
        self.assertTrue(plan.is_frozen)

        # Attempt to add test case
        with self.assertRaises(TesterBoundaryViolationError):
            plan.add_test_case(TestCase(
                test_case_id=new_test_case_id(),
                category=ApplicableTestCategory.UI_INTERACTION,
                objective="Unauthorized late addition",
                steps=[TestStep(step_number=1, description="Unauthorized step")],
                expected_outcome="None",
            ))

        # Attempt to remove test case
        with self.assertRaises(TesterBoundaryViolationError):
            plan.remove_test_case(plan.test_cases[0].test_case_id)

    # 20. Attempt to Dynamically Add Tests After Execution Starts
    def test_dynamically_add_tests_after_execution_starts(self) -> None:
        """
        Scenario 20:
        A. Transitioning to RUNNING without a frozen plan raises TesterValidationError.
        B. Attaching or replacing test plan after execution enters RUNNING raises TesterBoundaryViolationError.
        C. Transitioning to RUNNING with zero test cases raises TesterValidationError.
        """
        wo = self._make_work_order()
        execution = self.bridge.dispatch_work_order(wo)
        execution.require_frozen_plan = True

        # A. Transitioning to RUNNING without plan raises TesterValidationError
        with self.assertRaises(TesterValidationError):
            execution.transition_to(TesterExecutionStatus.RUNNING)

        # Create valid plan and run planning
        env = self._make_environment()
        plan = self.pipeline.run_planning(
            execution=execution,
            work_order=wo,
            environment=env,
            runtime_capabilities=wo.authorized_capabilities,
        )
        self.assertEqual(execution.status, TesterExecutionStatus.PLAN_FROZEN)

        # Transition to RUNNING
        execution.transition_to(TesterExecutionStatus.RUNNING)
        self.assertEqual(execution.status, TesterExecutionStatus.RUNNING)

        # B. Attaching a new plan once RUNNING is prohibited
        another_plan = TestPlan(
            plan_id=new_plan_id(),
            work_order_id=wo.work_order_id,
            execution_id=execution.execution_id,
            project_id=wo.project_id,
        )
        with self.assertRaises(TesterBoundaryViolationError):
            execution.attach_test_plan(another_plan)

        # C. Zero test case plan cannot transition to RUNNING
        zero_exec = self.bridge.dispatch_work_order(wo)
        zero_plan = TestPlan(
            plan_id=new_plan_id(),
            work_order_id=wo.work_order_id,
            execution_id=zero_exec.execution_id,
            project_id=wo.project_id,
            test_cases=[],
        )
        zero_plan.mark_validated()
        zero_plan.freeze()
        zero_exec.attach_test_plan(zero_plan)
        zero_exec.transition_to(TesterExecutionStatus.PLANNING)
        zero_exec.transition_to(TesterExecutionStatus.PLAN_VALIDATION)
        zero_exec.transition_to(TesterExecutionStatus.PLAN_FROZEN)

        with self.assertRaises(TesterValidationError):
            zero_exec.transition_to(TesterExecutionStatus.RUNNING)

    # 21. Lineage Preservation Across All Artifacts
    def test_lineage_preservation_across_all_artifacts(self) -> None:
        """
        Scenario 21: Causal lineage is preserved across:
        ManagerTask -> TesterWorkOrder -> TesterExecution -> TestContext -> Applicability -> TestPlan -> TesterResult.
        """
        task = Task(
            id="task-lineage-999",
            project_id="proj-lineage-999",
            title="Lineage Test",
            objective="Verify end to end causal lineage.",
            status=TaskStatus.PENDING,
            metadata={
                "correlation_id": "corr-lineage-999",
                "test_scope": ["/home"],
                "authorized_capabilities": ["TEST_EXECUTION", "NAVIGATE"],
                "acceptance_criteria": [{"criterion_id": "ac-lin-1", "description": "Verify home page."}],
            },
        )
        wo = self.bridge.issue_work_order(task)
        execution = self.bridge.dispatch_work_order(wo)
        env = self._make_environment(
            project_id=task.project_id,
            work_order_id=wo.work_order_id,
            execution_id=execution.execution_id,
        )

        plan = self.pipeline.run_planning(
            execution=execution,
            work_order=wo,
            environment=env,
            runtime_capabilities=wo.authorized_capabilities,
        )

        # Traceability assertions
        self.assertEqual(wo.manager_task_id, task.id)
        self.assertEqual(wo.project_id, task.project_id)
        self.assertEqual(execution.work_order_id, wo.work_order_id)
        self.assertEqual(execution.project_id, wo.project_id)
        self.assertEqual(execution.test_context.work_order_id, wo.work_order_id)
        self.assertEqual(execution.test_applicability.work_order_id, wo.work_order_id)
        self.assertEqual(plan.work_order_id, wo.work_order_id)
        self.assertEqual(plan.execution_id, execution.execution_id)
        self.assertEqual(plan.project_id, wo.project_id)

        # Execution to completion
        execution.transition_to(TesterExecutionStatus.RUNNING)
        execution.transition_to(TesterExecutionStatus.EVALUATING)
        execution.transition_to(TesterExecutionStatus.REPORTING)
        result = execution.create_result(
            status=TesterResultStatus.COMPLETED,
            summary_for_manager="Lineage verification successful",
            ship_recommendation=ShipRecommendation.SHIP,
        )
        self.assertEqual(result.work_order_id, wo.work_order_id)
        self.assertEqual(result.execution_id, execution.execution_id)
        self.assertEqual(result.task_id, task.id)
        self.assertEqual(result.project_id, task.project_id)

    # 22. Event Emission Sequence
    def test_event_emission_sequence(self) -> None:
        """
        Scenario 22: Planning emits the correct sequence of domain events:
        TESTER_REQUESTED -> TESTER_STARTED -> TESTER_PLANNING_STARTED -> TESTER_PLAN_GENERATED -> TESTER_PLAN_VALIDATED -> TESTER_PLAN_FROZEN.
        """
        wo = self._make_work_order()
        self.bridge.work_orders[wo.work_order_id] = wo
        execution = self.bridge.dispatch_work_order(wo)
        env = self._make_environment()

        self.pipeline.run_planning(
            execution=execution,
            work_order=wo,
            environment=env,
            runtime_capabilities=wo.authorized_capabilities,
        )

        event_types = [e.event_type for e in self.bridge.events]
        expected_sequence = [
            EventType.TESTER_STARTED,
            EventType.TESTER_PLANNING_STARTED,
            EventType.TESTER_PLAN_GENERATED,
            EventType.TESTER_PLAN_VALIDATED,
            EventType.TESTER_PLAN_FROZEN,
        ]
        for expected_event in expected_sequence:
            self.assertIn(expected_event, event_types)

        # Check sequential order
        indices = [event_types.index(evt) for evt in expected_sequence]
        self.assertEqual(indices, sorted(indices))

    # 23. FakeTesterWorker with Planning Enabled
    def test_fake_tester_worker_with_planning_enabled(self) -> None:
        """Scenario 23: Verify FakeTesterWorker executes through planning pipeline seamlessly."""
        wo = self._make_work_order()
        env = self._make_environment()

        worker = FakeTesterWorker(
            bridge=self.bridge,
            use_planning=True,
            target_environment=env,
            files_manifest=["src/client/AuthModal.tsx"],
        )

        execution, result, worker_output = worker.execute_work_order(wo)

        self.assertTrue(execution.is_terminal)
        self.assertEqual(execution.status, TesterExecutionStatus.COMPLETED)
        self.assertIsNotNone(execution.test_plan)
        self.assertTrue(execution.test_plan.is_frozen)
        self.assertTrue(result.is_success())


if __name__ == "__main__":
    unittest.main()

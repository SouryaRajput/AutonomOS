from __future__ import annotations

import copy
import unittest
from typing import Any, Optional

from core.tester import (
    AcceptanceCriterion,
    ApplicabilityLevel,
    ApplicableTestCategory,
    CategoryApplicability,
    ChangeCategory,
    PlanValidationCode,
    PresenceAssessment,
    PresenceStatus,
    TestApplicabilityClassifier,
    TestApplicabilityReport,
    TestCase,
    TestContext,
    TestCoverageEvaluator,
    TestEnvironment,
    TestPlan,
    TestPlanGenerator,
    TestPlanStatus,
    TestPlanValidationIssue,
    TestPlanValidationResult,
    TestPlanValidator,
    TestPriority,
    TestScope,
    TestStep,
    TestSurface,
    TesterBoundaryViolationError,
    TesterExecution,
    TesterExecutionStatus,
    TesterValidationError,
    TesterWorkOrder,
    TestingCapability,
    ValidationIssueSeverity,
    new_environment_id,
    new_execution_id,
    new_plan_id,
    new_test_case_id,
    new_work_order_id,
)


class TestTesterPlanValidator(unittest.TestCase):
    """
    Unit test suite for Tester V1 Phase 3.5: Test Plan Validation.
    Validates:
    1. test_valid_plan
    2. test_invalid_lineage
    3. test_unauthorized_category
    4. test_out_of_scope_test
    5. test_budget_exceeded
    6. test_incomplete_test_case
    7. test_missing_runtime_capability
    8. test_missing_environment
    9. test_unbounded_plan
    10. test_duplicate_tests
    11. test_recursive_test_definition
    12. test_valid_zero_test_plan
    13. test_frozen_plan_immutability
    14. test_deterministic_validation
    """

    def setUp(self) -> None:
        self.validator = TestPlanValidator()
        self.classifier = TestApplicabilityClassifier()
        self.generator = TestPlanGenerator(classifier=self.classifier)
        self.project_id = "proj-plan-val-01"
        self.task_id = "task-plan-val-01"
        self.correlation_id = "corr-plan-val-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()

    def _make_work_order(self, **kwargs) -> TesterWorkOrder:
        defaults = {
            "work_order_id": self.work_order_id,
            "manager_task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "objective": "Validate test plan authorization, coverage, and bounds.",
            "test_scope": TestScope(
                components=["AuthModal"],
                routes=["/login"],
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
            ],
            "time_budget": 300,
            "iteration_budget": 5,
        }
        defaults.update(kwargs)
        return TesterWorkOrder(**defaults)

    def _make_context(self, **kwargs) -> TestContext:
        defaults = {
            "project_id": self.project_id,
            "work_order_id": self.work_order_id,
            "execution_id": self.execution_id,
            "source_revision": "rev-val-12345",
            "frontend": PresenceAssessment(status=PresenceStatus.PRESENT),
            "backend": PresenceAssessment(status=PresenceStatus.PRESENT),
            "database": PresenceAssessment(status=PresenceStatus.ABSENT),
            "change_categories": [ChangeCategory.FRONTEND],
            "changed_files": ["src/client/AuthModal.tsx"],
            "changed_modules": [],
            "changed_components": ["AuthModal"],
            "changed_routes": ["/login"],
            "changed_apis": [],
        }
        defaults.update(kwargs)
        return TestContext(**defaults)

    def _make_environment(self, **kwargs) -> TestEnvironment:
        is_avail = kwargs.pop("is_available", True)
        is_reach = kwargs.pop("is_reachable", True)
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
        env.is_available = is_avail
        env.is_reachable = is_reach
        return env

    def _make_valid_test_case(self, **kwargs) -> TestCase:
        defaults = {
            "test_case_id": new_test_case_id(),
            "category": ApplicableTestCategory.UI_INTERACTION,
            "objective": "Verify user can submit login form.",
            "preconditions": ["AuthModal loaded."],
            "steps": [
                TestStep(step_number=1, description="Click login submit button", action="CLICK", target="login-submit"),
                TestStep(step_number=2, description="Observe submission response", expected="User logged in"),
            ],
            "expected_outcome": "User is successfully authenticated.",
            "priority": TestPriority.HIGH,
            "acceptance_linkage": ["ac-auth-01"],
            "evidence_expectations": ["OBSERVATION", "SCREENSHOT"],
            "authorization_reference": "AuthModal",
            "covered_surfaces": [TestSurface.UI, TestSurface.USER_INTERACTION],
            "covered_categories": [ApplicableTestCategory.UI_INTERACTION],
            "dependencies": [],
        }
        defaults.update(kwargs)
        return TestCase(**defaults)

    def _make_test_plan(self, test_cases: Optional[list[TestCase]] = None, **kwargs) -> TestPlan:
        if test_cases is None:
            test_cases = [self._make_valid_test_case()]
        defaults = {
            "plan_id": new_plan_id(),
            "work_order_id": self.work_order_id,
            "execution_id": self.execution_id,
            "project_id": self.project_id,
            "context_revision": "rev-val-12345",
            "test_cases": test_cases,
            "required_categories": [ApplicableTestCategory.UI_INTERACTION],
            "optional_categories": [],
            "not_applicable_categories": [ApplicableTestCategory.PERFORMANCE],
            "max_test_cases": 25,
            "time_budget": 300,
            "iteration_budget": 5,
            "status": TestPlanStatus.DRAFT,
            "estimated_execution_scope": "Evaluation of UI interaction.",
        }
        defaults.update(kwargs)
        return TestPlan(**defaults)

    # 1. Valid Plan
    def test_valid_plan(self) -> None:
        """A properly formed plan matching WorkOrder authorization, context, criteria, and environment passes."""
        wo = self._make_work_order()
        ctx = self._make_context()
        env = self._make_environment()
        plan = self._make_test_plan()

        result = self.validator.validate(
            test_plan=plan,
            work_order=wo,
            test_context=ctx,
            environment=env,
            runtime_capabilities=[
                TestingCapability.TEST_EXECUTION,
                TestingCapability.CLICK,
                TestingCapability.TYPE,
                TestingCapability.SCREENSHOT,
            ],
        )

        self.assertTrue(result.is_valid)
        self.assertEqual(len(result.issues), 0)
        self.assertIn("validated successfully", result.summary)

        # Transitions DRAFT -> VALIDATED -> FROZEN
        self.assertEqual(plan.status, TestPlanStatus.DRAFT)
        plan.mark_validated()
        self.assertTrue(plan.is_validated)
        self.assertEqual(plan.status, TestPlanStatus.VALIDATED)
        plan.freeze()
        self.assertTrue(plan.is_frozen)
        self.assertEqual(plan.status, TestPlanStatus.FROZEN)

    # 2. Invalid Lineage
    def test_invalid_lineage(self) -> None:
        """Mismatched work_order_id, execution_id, or project_id fails with INVALID_LINEAGE."""
        wo = self._make_work_order()
        ctx = self._make_context()
        env = self._make_environment()

        # Mismatched work_order_id
        bad_wo_plan = self._make_test_plan(work_order_id="two-wrong-id")
        res1 = self.validator.validate(bad_wo_plan, wo, ctx, env)
        self.assertFalse(res1.is_valid)
        codes1 = [i.code for i in res1.issues]
        self.assertIn(PlanValidationCode.INVALID_LINEAGE, codes1)

        # Mismatched execution_id
        bad_exec_plan = self._make_test_plan(execution_id="texec-wrong-id")
        res2 = self.validator.validate(bad_exec_plan, wo, ctx, env)
        self.assertFalse(res2.is_valid)
        codes2 = [i.code for i in res2.issues]
        self.assertIn(PlanValidationCode.INVALID_LINEAGE, codes2)

        # Mismatched project_id
        bad_proj_plan = self._make_test_plan(project_id="proj-wrong")
        res3 = self.validator.validate(bad_proj_plan, wo, ctx, env)
        self.assertFalse(res3.is_valid)
        codes3 = [i.code for i in res3.issues]
        self.assertIn(PlanValidationCode.INVALID_LINEAGE, codes3)

    # 3. Unauthorized Category
    def test_unauthorized_category(self) -> None:
        """Test cases containing a category not authorized in work_order fail with UNAUTHORIZED_CATEGORY."""
        wo = self._make_work_order(
            authorized_capabilities=[
                TestingCapability.TEST_EXECUTION,
                TestingCapability.NAVIGATE,
            ]
        )
        ctx = self._make_context()
        env = self._make_environment()

        unauth_tc = self._make_valid_test_case(
            category=ApplicableTestCategory.PERFORMANCE,
            evidence_expectations=["SCREENSHOT"],
        )
        plan = self._make_test_plan(
            test_cases=[unauth_tc],
            not_applicable_categories=[ApplicableTestCategory.PERFORMANCE],
        )

        result = self.validator.validate(plan, wo, ctx, env)
        self.assertFalse(result.is_valid)
        codes = [i.code for i in result.issues]
        self.assertIn(PlanValidationCode.UNAUTHORIZED_CATEGORY, codes)

    # 4. Out of Scope Test
    def test_out_of_scope_test(self) -> None:
        """Test targeting a forbidden route, component, or unauthorized environment fails with OUT_OF_SCOPE."""
        wo = self._make_work_order(
            test_scope=TestScope(
                components=["AuthModal"],
                routes=["/login"],
                environments=["staging"],
            )
        )
        ctx = self._make_context()
        env = self._make_environment()

        # Test step targeting forbidden route
        out_scope_tc = self._make_valid_test_case(
            steps=[
                TestStep(step_number=1, description="Navigate to admin settings", action="NAVIGATE", target="/admin/danger"),
            ]
        )
        plan = self._make_test_plan(test_cases=[out_scope_tc])

        result = self.validator.validate(plan, wo, ctx, env)
        self.assertFalse(result.is_valid)
        codes = [i.code for i in result.issues]
        self.assertIn(PlanValidationCode.OUT_OF_SCOPE, codes)

        # Environment out of scope
        bad_env = self._make_environment(env_name="production")
        valid_plan = self._make_test_plan()
        env_res = self.validator.validate(valid_plan, wo, ctx, bad_env)
        self.assertFalse(env_res.is_valid)
        self.assertIn(PlanValidationCode.OUT_OF_SCOPE, [i.code for i in env_res.issues])

    # 5. Budget Exceeded
    def test_budget_exceeded(self) -> None:
        """Plan exceeding work_order time_budget, iteration_budget, or max_test_cases fails with BUDGET_EXCEEDED."""
        wo = self._make_work_order(time_budget=300, iteration_budget=5)
        ctx = self._make_context()
        env = self._make_environment()

        # Exceed time budget
        time_plan = self._make_test_plan(time_budget=500)
        res1 = self.validator.validate(time_plan, wo, ctx, env)
        self.assertFalse(res1.is_valid)
        self.assertIn(PlanValidationCode.BUDGET_EXCEEDED, [i.code for i in res1.issues])

        # Exceed iteration budget
        iter_plan = self._make_test_plan(iteration_budget=10)
        res2 = self.validator.validate(iter_plan, wo, ctx, env)
        self.assertFalse(res2.is_valid)
        self.assertIn(PlanValidationCode.BUDGET_EXCEEDED, [i.code for i in res2.issues])

        # Exceed max_test_cases (dynamically modified to bypass post-init check)
        tc1 = self._make_valid_test_case()
        tc2 = self._make_valid_test_case(objective="Second test case")
        cases_plan = self._make_test_plan(test_cases=[tc1, tc2], max_test_cases=10)
        cases_plan.max_test_cases = 1
        res3 = self.validator.validate(cases_plan, wo, ctx, env)
        self.assertFalse(res3.is_valid)
        self.assertIn(PlanValidationCode.BUDGET_EXCEEDED, [i.code for i in res3.issues])

    # 6. Incomplete Test Case
    def test_incomplete_test_case(self) -> None:
        """Missing objective, steps, or expected outcome fails with structured codes."""
        wo = self._make_work_order()
        ctx = self._make_context()
        env = self._make_environment()

        # Missing objective (dynamically cleared)
        tc_no_obj = self._make_valid_test_case()
        tc_no_obj.objective = ""
        res1 = self.validator.validate(self._make_test_plan(test_cases=[tc_no_obj]), wo, ctx, env)
        self.assertFalse(res1.is_valid)
        self.assertIn(PlanValidationCode.EMPTY_TEST_OBJECTIVE, [i.code for i in res1.issues])

        # Missing steps
        tc_no_steps = self._make_valid_test_case()
        tc_no_steps.steps = []
        res2 = self.validator.validate(self._make_test_plan(test_cases=[tc_no_steps]), wo, ctx, env)
        self.assertFalse(res2.is_valid)
        self.assertIn(PlanValidationCode.INVALID_TEST_CASE, [i.code for i in res2.issues])

        # Missing expected outcome
        tc_no_outcome = self._make_valid_test_case()
        tc_no_outcome.expected_outcome = ""
        res3 = self.validator.validate(self._make_test_plan(test_cases=[tc_no_outcome]), wo, ctx, env)
        self.assertFalse(res3.is_valid)
        self.assertIn(PlanValidationCode.EMPTY_EXPECTED_OUTCOME, [i.code for i in res3.issues])

        # Step missing description
        tc_bad_step = self._make_valid_test_case()
        bad_step = TestStep(step_number=1, description="valid")
        bad_step.description = ""
        tc_bad_step.steps = [bad_step]
        res4 = self.validator.validate(self._make_test_plan(test_cases=[tc_bad_step]), wo, ctx, env)
        self.assertFalse(res4.is_valid)
        self.assertIn(PlanValidationCode.INVALID_TEST_CASE, [i.code for i in res4.issues])

    # 7. Missing Runtime Capability
    def test_missing_runtime_capability(self) -> None:
        """Test case requiring screenshot or video capability when runtime does not support it fails."""
        wo = self._make_work_order()
        ctx = self._make_context()
        env = self._make_environment()

        tc = self._make_valid_test_case(evidence_expectations=["SCREENSHOT", "VIDEO"])
        plan = self._make_test_plan(test_cases=[tc])

        # Runtime has CLICK but neither SCREENSHOT nor SCREEN_RECORDING
        result = self.validator.validate(
            plan,
            wo,
            ctx,
            env,
            runtime_capabilities=[TestingCapability.CLICK],
        )
        self.assertFalse(result.is_valid)
        codes = [i.code for i in result.issues]
        self.assertIn(PlanValidationCode.MISSING_RUNTIME_CAPABILITY, codes)

    # 8. Missing Environment
    def test_missing_environment(self) -> None:
        """Executable plan without an environment or with unavailable/unreachable environment fails."""
        wo = self._make_work_order()
        ctx = self._make_context()
        plan = self._make_test_plan()

        # No environment passed and none in context
        res1 = self.validator.validate(plan, wo, ctx, environment=None)
        self.assertFalse(res1.is_valid)
        self.assertIn(PlanValidationCode.MISSING_ENVIRONMENT, [i.code for i in res1.issues])

        # Environment marked unavailable
        unavail_env = self._make_environment(is_available=False)
        res2 = self.validator.validate(plan, wo, ctx, environment=unavail_env)
        self.assertFalse(res2.is_valid)
        self.assertIn(PlanValidationCode.MISSING_ENVIRONMENT, [i.code for i in res2.issues])

        # Environment marked unreachable
        unreach_env = self._make_environment(is_reachable=False)
        res3 = self.validator.validate(plan, wo, ctx, environment=unreach_env)
        self.assertFalse(res3.is_valid)
        self.assertIn(PlanValidationCode.MISSING_ENVIRONMENT, [i.code for i in res3.issues])

    # 9. Unbounded Plan
    def test_unbounded_plan(self) -> None:
        """Test case exceeding maximum step bounds (> 50 steps) fails with UNBOUNDED_PLAN."""
        wo = self._make_work_order()
        ctx = self._make_context()
        env = self._make_environment()

        steps_51 = [
            TestStep(step_number=i, description=f"Step {i} execution")
            for i in range(1, 52)
        ]
        tc_long = self._make_valid_test_case(steps=steps_51)
        plan = self._make_test_plan(test_cases=[tc_long])

        result = self.validator.validate(plan, wo, ctx, env)
        self.assertFalse(result.is_valid)
        self.assertIn(PlanValidationCode.UNBOUNDED_PLAN, [i.code for i in result.issues])

    # 10. Duplicate Tests
    def test_duplicate_tests(self) -> None:
        """Duplicate test case IDs or identical action signatures fail with DUPLICATE_TEST_CASE."""
        wo = self._make_work_order()
        ctx = self._make_context()
        env = self._make_environment()

        # Duplicate ID
        dup_id = new_test_case_id()
        tc1 = self._make_valid_test_case(test_case_id=dup_id, objective="Unique 1")
        tc2 = self._make_valid_test_case(test_case_id=dup_id, objective="Unique 2")
        plan_dup_id = self._make_test_plan(test_cases=[tc1, tc2])
        res1 = self.validator.validate(plan_dup_id, wo, ctx, env)
        self.assertFalse(res1.is_valid)
        self.assertIn(PlanValidationCode.DUPLICATE_TEST_CASE, [i.code for i in res1.issues])

        # Duplicate signature (same category, objective, authorization reference)
        tc3 = self._make_valid_test_case(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.UI_INTERACTION,
            objective="Submit login",
            authorization_reference="AuthModal",
        )
        tc4 = self._make_valid_test_case(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.UI_INTERACTION,
            objective="Submit login",
            authorization_reference="AuthModal",
        )
        plan_dup_sig = self._make_test_plan(test_cases=[tc3, tc4])
        res2 = self.validator.validate(plan_dup_sig, wo, ctx, env)
        self.assertFalse(res2.is_valid)
        self.assertIn(PlanValidationCode.DUPLICATE_TEST_CASE, [i.code for i in res2.issues])

    # 11. Recursive Test Definition
    def test_recursive_test_definition(self) -> None:
        """Self-referential dependencies or circular dependency chains fail with RECURSIVE_TEST_DEFINITION."""
        wo = self._make_work_order()
        ctx = self._make_context()
        env = self._make_environment()

        # Self dependency
        tc_self_id = new_test_case_id()
        tc_self = self._make_valid_test_case(test_case_id=tc_self_id)
        tc_self.dependencies = [tc_self_id]
        res1 = self.validator.validate(self._make_test_plan(test_cases=[tc_self]), wo, ctx, env)
        self.assertFalse(res1.is_valid)
        self.assertIn(PlanValidationCode.RECURSIVE_TEST_DEFINITION, [i.code for i in res1.issues])

        # Cycle: A -> B -> A
        id_a = new_test_case_id()
        id_b = new_test_case_id()
        tc_a = self._make_valid_test_case(test_case_id=id_a, objective="Cycle A", authorization_reference="ref-a")
        tc_b = self._make_valid_test_case(test_case_id=id_b, objective="Cycle B", authorization_reference="ref-b")
        tc_a.dependencies = [id_b]
        tc_b.dependencies = [id_a]

        res2 = self.validator.validate(self._make_test_plan(test_cases=[tc_a, tc_b]), wo, ctx, env)
        self.assertFalse(res2.is_valid)
        self.assertIn(PlanValidationCode.RECURSIVE_TEST_DEFINITION, [i.code for i in res2.issues])

    # 12. Valid Zero-Test Plan
    def test_valid_zero_test_plan(self) -> None:
        """Zero-test plan is valid when no applicable categories exist or all are NOT_APPLICABLE."""
        wo = self._make_work_order(acceptance_criteria=[])
        # Context with zero changes and no components
        ctx = self._make_context(
            change_categories=[],
            changed_files=[],
            changed_modules=[],
            changed_components=[],
            changed_routes=[],
            changed_apis=[],
        )

        app_rep = self.classifier.classify(work_order=wo, test_context=ctx)
        zero_plan = self.generator.generate(
            work_order=wo,
            test_context=ctx,
            applicability_report=app_rep,
        )
        self.assertEqual(len(zero_plan.test_cases), 0)

        # Validate with validator
        result = self.validator.validate(
            test_plan=zero_plan,
            work_order=wo,
            test_context=ctx,
            environment=None,  # Zero-test plan does not require environment
        )
        self.assertTrue(result.is_valid)
        self.assertEqual(len(result.issues), 0)
        self.assertIn("NO_APPLICABLE_TESTS", result.summary)

    # 13. Frozen Plan Immutability
    def test_frozen_plan_immutability(self) -> None:
        """Once frozen, modifying test cases, coverage, or re-validating raises TesterBoundaryViolationError."""
        plan = self._make_test_plan()
        plan.mark_validated()
        plan.freeze()
        self.assertTrue(plan.is_frozen)

        # Cannot add test case
        with self.assertRaises(TesterBoundaryViolationError):
            plan.add_test_case(self._make_valid_test_case())

        # Cannot remove test case
        with self.assertRaises(TesterBoundaryViolationError):
            plan.remove_test_case("any-id")

        # Cannot attach coverage report
        with self.assertRaises(TesterBoundaryViolationError):
            evaluator = TestCoverageEvaluator(classifier=self.classifier)
            cov = evaluator.evaluate(self._make_work_order(), self._make_context(), plan)
            plan.attach_coverage_report(cov)

        # Cannot mark validated or invalid when frozen
        with self.assertRaises(TesterBoundaryViolationError):
            plan.mark_validated()
        with self.assertRaises(TesterBoundaryViolationError):
            plan.mark_invalid("Some failure")

        # Freezing an INVALID plan raises TesterValidationError
        invalid_plan = self._make_test_plan()
        invalid_plan.mark_invalid("Critical flaw detected during validation")
        self.assertEqual(invalid_plan.status, TestPlanStatus.INVALID)
        with self.assertRaises(TesterValidationError):
            invalid_plan.freeze()

        # Attaching INVALID plan to TesterExecution raises TesterValidationError
        execution = TesterExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
        )
        with self.assertRaises(TesterValidationError):
            execution.attach_test_plan(invalid_plan)

    # 14. Deterministic Validation
    def test_deterministic_validation(self) -> None:
        """Running validator multiple times on the same inputs produces identical results and issue codes."""
        wo = self._make_work_order()
        ctx = self._make_context()
        env = self._make_environment()

        dup_id = new_test_case_id()
        # Create a plan with a couple distinct issues (duplicate and out of scope)
        tc_dup1 = self._make_valid_test_case(test_case_id=dup_id, steps=[
            TestStep(step_number=1, description="Step to forbidden route", action="NAVIGATE", target="/forbidden/path")
        ])
        tc_dup2 = self._make_valid_test_case(test_case_id=dup_id, steps=[
            TestStep(step_number=1, description="Step to forbidden route", action="NAVIGATE", target="/forbidden/path")
        ])
        plan = self._make_test_plan(test_cases=[tc_dup1, tc_dup2])

        runs = []
        for _ in range(5):
            res = self.validator.validate(
                test_plan=copy.deepcopy(plan),
                work_order=wo,
                test_context=ctx,
                environment=env,
            )
            issue_signatures = [(i.code.value, i.target_id, i.message) for i in res.issues]
            runs.append((res.is_valid, issue_signatures))

        first_valid, first_issues = runs[0]
        for run_valid, run_issues in runs[1:]:
            self.assertEqual(run_valid, first_valid)
            self.assertEqual(run_issues, first_issues)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from typing import Any, Optional

from core.tester import (
    AcceptanceCriterion,
    ApplicabilityLevel,
    ApplicableTestCategory,
    CategoryApplicability,
    ChangeCategory,
    CoverageGap,
    CoverageGapReason,
    CoverageGapSeverity,
    CoverageState,
    CriterionCoverage,
    PresenceAssessment,
    PresenceStatus,
    SurfaceAssessment,
    SurfaceCoverage,
    TestApplicabilityClassifier,
    TestApplicabilityReport,
    TestCase,
    TestContext,
    TestContextBuilder,
    TestCoverageEvaluator,
    TestCoverageReport,
    TestEnvironment,
    TestPlan,
    TestPlanGenerator,
    TestPlanStatus,
    TestPrioritizer,
    TestPriority,
    TestScope,
    TestStep,
    TestSurface,
    TesterBoundaryViolationError,
    TesterExecution,
    TesterExecutionStatus,
    TestingCapability,
    TesterLineageError,
    TesterValidationError,
    TesterWorkOrder,
    new_execution_id,
    new_gap_id,
    new_plan_id,
    new_test_case_id,
    new_work_order_id,
)


class TestTesterCoverage(unittest.TestCase):
    """
    Unit test suite for Tester V1 Phase 3.4: Test Priority & Coverage.
    Validates:
    1. Complete acceptance criteria coverage
    2. Partial acceptance criteria coverage
    3. Uncovered critical criterion with explicit gap
    4. Not applicable criterion (distinguished from uncovered)
    5. Capability-related coverage gap (runtime capability unavailable)
    6. Authorization-related coverage gap (capability not authorized in work order)
    7. Mixed frontend and backend coverage across criteria and surfaces
    8. Deterministic priority scoring and ordering
    9. Dependency-aware topological ordering with cycle detection
    10. Deterministic generation and report reproducibility
    11. Lineage validation and freeze integrity
    """

    def setUp(self) -> None:
        self.classifier = TestApplicabilityClassifier()
        self.generator = TestPlanGenerator(classifier=self.classifier)
        self.evaluator = TestCoverageEvaluator(classifier=self.classifier)
        self.project_id = "proj-coverage-test"
        self.task_id = "task-coverage-01"
        self.correlation_id = "corr-coverage-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()

    def _make_work_order(self, **kwargs) -> TesterWorkOrder:
        defaults = {
            "work_order_id": self.work_order_id,
            "manager_task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "objective": "Verify priority and coverage evaluation.",
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
            "source_revision": "rev-test-12345",
            "frontend": PresenceAssessment(status=PresenceStatus.PRESENT),
            "backend": PresenceAssessment(status=PresenceStatus.PRESENT),
            "database": PresenceAssessment(status=PresenceStatus.ABSENT),
            "change_categories": [ChangeCategory.FRONTEND, ChangeCategory.BACKEND],
            "changed_files": ["src/client/AuthModal.tsx", "src/server/auth.py"],
            "changed_modules": ["src.server.auth"],
            "changed_components": ["AuthModal"],
            "changed_routes": ["/login"],
            "changed_apis": ["/api/auth"],
        }
        defaults.update(kwargs)
        return TestContext(**defaults)

    # 1. Complete Acceptance Coverage
    def test_complete_acceptance_coverage(self) -> None:
        """All authorized acceptance criteria are linked and covered by multi-step test cases."""
        wo = self._make_work_order(
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-auth-01",
                    description="User can click submit to log in.",
                ),
                AcceptanceCriterion(
                    criterion_id="ac-auth-02",
                    description="User can navigate to /login route.",
                ),
                AcceptanceCriterion(
                    criterion_id="ac-auth-03",
                    description="Backend auth service validates credentials.",
                ),
            ]
        )
        ctx = self._make_context()

        plan = self.generator.generate(work_order=wo, test_context=ctx)
        self.assertIsNotNone(plan.coverage_report)
        report = plan.coverage_report

        self.assertIn("ac-auth-01", report.criteria_coverage)
        self.assertIn("ac-auth-02", report.criteria_coverage)
        self.assertIn("ac-auth-03", report.criteria_coverage)
        self.assertEqual(report.criteria_coverage["ac-auth-01"].state, CoverageState.COVERED)
        self.assertEqual(report.criteria_coverage["ac-auth-02"].state, CoverageState.COVERED)
        self.assertEqual(report.criteria_coverage["ac-auth-03"].state, CoverageState.COVERED)
        self.assertTrue(report.is_fully_covered)
        self.assertEqual(len(report.critical_gaps), 0)

    # 2. Partial Acceptance Coverage
    def test_partial_coverage(self) -> None:
        """A test case with only 1 step provides only partial coverage."""
        wo = self._make_work_order(
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-single-step",
                    description="User submits credentials.",
                )
            ]
        )
        ctx = self._make_context()

        # Build plan with single-step test case
        tc = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.UI_INTERACTION,
            objective="Evaluate acceptance criterion: User submits credentials.",
            steps=[TestStep(step_number=1, description="Only click submit without verification")],
            acceptance_linkage=["ac-single-step"],
            covered_surfaces=[TestSurface.UI],
            covered_categories=[ApplicableTestCategory.UI_INTERACTION],
        )
        plan = TestPlan(
            plan_id=new_plan_id(),
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_cases=[tc],
            required_categories=[ApplicableTestCategory.UI_INTERACTION],
        )
        report = self.evaluator.evaluate(work_order=wo, test_context=ctx, test_plan=plan)

        self.assertIn("ac-single-step", report.criteria_coverage)
        self.assertEqual(
            report.criteria_coverage["ac-single-step"].state,
            CoverageState.PARTIALLY_COVERED,
        )

    # 3. Uncovered Critical Criterion
    def test_uncovered_critical_criterion(self) -> None:
        """Missing test for critical criterion results in explicit CRITICAL gap and state UNCOVERED."""
        wo = self._make_work_order(
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-missing-crit",
                    description="Critical payment checkout processing.",
                )
            ]
        )
        ctx = self._make_context()

        # Plan with 0 test cases targeting this criterion
        plan = TestPlan(
            plan_id=new_plan_id(),
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            project_id=self.project_id,
            test_cases=[],
            required_categories=[ApplicableTestCategory.FUNCTIONAL],
        )
        report = self.evaluator.evaluate(work_order=wo, test_context=ctx, test_plan=plan)

        self.assertIn("ac-missing-crit", report.criteria_coverage)
        cov = report.criteria_coverage["ac-missing-crit"]
        self.assertEqual(cov.state, CoverageState.UNCOVERED)
        self.assertIsNotNone(cov.gap)
        self.assertEqual(cov.gap.reason, CoverageGapReason.MISSING_TEST)
        self.assertEqual(cov.gap.severity, CoverageGapSeverity.CRITICAL)
        self.assertTrue(cov.gap.gap_id.startswith("tgap-"))
        self.assertIn(cov.gap, report.critical_gaps)
        self.assertFalse(report.is_fully_covered)

    # 4. Not Applicable Criterion
    def test_not_applicable_criterion(self) -> None:
        """Criterion targeting an absent layer is classified NOT_APPLICABLE, not UNCOVERED."""
        wo = self._make_work_order(
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-responsive-layout",
                    description="Verify responsive layout adapts on mobile viewport.",
                )
            ]
        )
        # Context is pure backend: frontend is ABSENT
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.ABSENT),
            backend=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.BACKEND],
            changed_components=[],
            changed_routes=[],
        )

        app_report = self.classifier.classify(work_order=wo, test_context=ctx)
        self.assertIn(ApplicableTestCategory.RESPONSIVE, app_report.not_applicable_categories)

        plan = self.generator.generate(work_order=wo, test_context=ctx, applicability_report=app_report)
        report = plan.coverage_report
        self.assertIsNotNone(report)

        cov = report.criteria_coverage["ac-responsive-layout"]
        self.assertEqual(cov.state, CoverageState.NOT_APPLICABLE)
        self.assertIsNotNone(cov.gap)
        self.assertEqual(cov.gap.reason, CoverageGapReason.NOT_APPLICABLE)
        self.assertEqual(cov.gap.severity, CoverageGapSeverity.LOW)
        # Not-applicable gaps are NOT critical gaps
        self.assertNotIn(cov.gap, report.critical_gaps)

    # 5. Capability Related Gap
    def test_capability_related_gap(self) -> None:
        """When a required testing capability is unavailable at runtime, gap reason is UNAVAILABLE_CAPABILITY."""
        wo = self._make_work_order(
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-anim",
                    description="Animation transition completes smoothly.",
                )
            ],
            authorized_capabilities=[
                TestingCapability.TEST_EXECUTION,
                TestingCapability.SCREEN_RECORDING,
            ],
        )
        ctx = self._make_context()

        # Classify with SCREEN_RECORDING unavailable in runtime
        app_report = self.classifier.classify(
            work_order=wo,
            test_context=ctx,
            runtime_capabilities=[TestingCapability.TEST_EXECUTION],  # Missing SCREEN_RECORDING
        )
        self.assertIn(ApplicableTestCategory.ANIMATION, app_report.blocked_categories)

        plan = self.generator.generate(work_order=wo, test_context=ctx, applicability_report=app_report)
        report = plan.coverage_report
        self.assertIsNotNone(report)

        cov = report.criteria_coverage["ac-anim"]
        self.assertEqual(cov.state, CoverageState.UNCOVERED)
        self.assertIsNotNone(cov.gap)
        self.assertEqual(cov.gap.reason, CoverageGapReason.UNAVAILABLE_CAPABILITY)
        self.assertEqual(cov.gap.severity, CoverageGapSeverity.HIGH)

    # 6. Authorization Related Gap
    def test_authorization_related_gap(self) -> None:
        """When a capability is not authorized in the WorkOrder, gap reason is AUTHORIZATION_LIMITATION."""
        wo = self._make_work_order(
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-visual-check",
                    description="Visual layout alignment must be pixel-exact.",
                )
            ],
            # WORK ORDER does NOT authorize SCREENSHOT capability
            authorized_capabilities=[
                TestingCapability.TEST_EXECUTION,
                TestingCapability.NAVIGATE,
                TestingCapability.CLICK,
            ],
        )
        ctx = self._make_context()

        app_report = self.classifier.classify(work_order=wo, test_context=ctx)
        self.assertIn(ApplicableTestCategory.VISUAL, app_report.not_applicable_categories)

        plan = self.generator.generate(work_order=wo, test_context=ctx, applicability_report=app_report)
        report = plan.coverage_report
        self.assertIsNotNone(report)

        cov = report.criteria_coverage["ac-visual-check"]
        self.assertEqual(cov.state, CoverageState.UNCOVERED)
        self.assertIsNotNone(cov.gap)
        self.assertEqual(cov.gap.reason, CoverageGapReason.AUTHORIZATION_LIMITATION)
        self.assertEqual(cov.gap.severity, CoverageGapSeverity.HIGH)

    # 7. Mixed Frontend and Backend Coverage
    def test_mixed_frontend_backend_coverage(self) -> None:
        """Both UI and backend acceptance criteria and surfaces are evaluated."""
        wo = self._make_work_order(
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-ui",
                    description="Button click triggers login.",
                ),
                AcceptanceCriterion(
                    criterion_id="ac-backend",
                    description="Backend service returns token response.",
                ),
            ]
        )
        ctx = self._make_context()

        plan = self.generator.generate(work_order=wo, test_context=ctx)
        report = plan.coverage_report
        self.assertIsNotNone(report)

        self.assertEqual(report.criteria_coverage["ac-ui"].state, CoverageState.COVERED)
        self.assertEqual(report.criteria_coverage["ac-backend"].state, CoverageState.COVERED)

        # Check surface coverage
        self.assertIn(TestSurface.UI, report.surface_coverage)
        self.assertIn(TestSurface.BUSINESS_LOGIC, report.surface_coverage)
        self.assertEqual(report.surface_coverage[TestSurface.UI].state, CoverageState.COVERED)
        self.assertEqual(report.surface_coverage[TestSurface.BUSINESS_LOGIC].state, CoverageState.COVERED)

        # Check category coverage
        self.assertEqual(report.category_coverage["UI_INTERACTION"], CoverageState.COVERED)
        self.assertEqual(report.category_coverage["FUNCTIONAL"], CoverageState.COVERED)

    # 8. Deterministic Priority Ordering
    def test_priority_ordering(self) -> None:
        """TestPrioritizer scores and orders tests deterministically based on explainable factors."""
        tc_crit = TestCase(
            test_case_id="ttest-001",
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Verify critical auth logic",
            priority=TestPriority.CRITICAL,
            acceptance_linkage=["ac-01"],
            covered_surfaces=[TestSurface.BUSINESS_LOGIC],
        )
        tc_direct = TestCase(
            test_case_id="ttest-002",
            category=ApplicableTestCategory.UI_INTERACTION,
            objective="Click modified AuthModal button",
            priority=TestPriority.HIGH,
            authorization_reference="AuthModal",
            covered_surfaces=[TestSurface.UI],
        )
        tc_low = TestCase(
            test_case_id="ttest-003",
            category=ApplicableTestCategory.VISUAL,
            objective="Check layout rendering",
            priority=TestPriority.MEDIUM,
            covered_surfaces=[TestSurface.VISUAL_LAYOUT],
        )
        tc_exploratory = TestCase(
            test_case_id="ttest-004",
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Secondary exploratory edge cases",
            priority=TestPriority.LOW,
            covered_surfaces=[TestSurface.BUSINESS_LOGIC],
        )

        ordered = TestPrioritizer.order_test_cases(
            [tc_exploratory, tc_low, tc_direct, tc_crit],
            changed_components=["AuthModal"],
        )

        # Critical acceptance test case must be first
        self.assertEqual(ordered[0].test_case_id, "ttest-001")
        self.assertGreaterEqual(ordered[0].priority_score, 130)  # 100 (crit) + 30 (func)
        self.assertIn("Critical Acceptance Linkage", ordered[0].priority_rationale)

        # Direct component test case must be second
        self.assertEqual(ordered[1].test_case_id, "ttest-002")
        self.assertGreaterEqual(ordered[1].priority_score, 100)  # 60 (high) + 40 (direct) + 20 (ui)

        # Exploratory test must be last due to penalty
        self.assertEqual(ordered[-1].test_case_id, "ttest-004")
        self.assertIn("Secondary / Exploratory Check [-10]", ordered[-1].priority_rationale)

    # 9. Dependency Ordering and Cycle Handling
    def test_dependency_ordering(self) -> None:
        """Topological ordering ensures prerequisite tests precede dependent tests even if lower score."""
        tc_setup = TestCase(
            test_case_id="ttest-setup",
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Initialize database test data",
            priority=TestPriority.MEDIUM,  # Lower priority score (~60)
        )
        tc_query = TestCase(
            test_case_id="ttest-query",
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Query test data from database",
            priority=TestPriority.CRITICAL,  # Higher priority score (~130)
            acceptance_linkage=["ac-query"],
            dependencies=["ttest-setup"],  # Depends explicitly on ttest-setup
        )

        # Even though tc_query has higher score, tc_setup MUST be ordered before tc_query
        ordered = TestPrioritizer.order_test_cases([tc_query, tc_setup])
        self.assertEqual(ordered[0].test_case_id, "ttest-setup")
        self.assertEqual(ordered[1].test_case_id, "ttest-query")

        # Test circular dependency handling: tc_a depends on tc_b, tc_b depends on tc_a
        tc_a = TestCase(
            test_case_id="ttest-a",
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Cycle test A",
            dependencies=["ttest-b"],
        )
        tc_b = TestCase(
            test_case_id="ttest-b",
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Cycle test B",
            dependencies=["ttest-a"],
        )
        # Should not hang or crash
        cyclic_ordered = TestPrioritizer.order_test_cases([tc_a, tc_b])
        self.assertEqual(len(cyclic_ordered), 2)

    # 10. Deterministic Generation
    def test_deterministic_results(self) -> None:
        """Repeated generation produces identical ordering, scores, IDs, and coverage reports."""
        wo = self._make_work_order(
            acceptance_criteria=[
                AcceptanceCriterion(criterion_id="ac-det-1", description="Deterministic test 1"),
                AcceptanceCriterion(criterion_id="ac-det-2", description="Deterministic test 2"),
            ]
        )
        ctx = self._make_context()

        plan1 = self.generator.generate(work_order=wo, test_context=ctx)
        plan2 = self.generator.generate(work_order=wo, test_context=ctx)

        # Number of test cases must be identical
        self.assertEqual(len(plan1.test_cases), len(plan2.test_cases))

        # Test case objectives and priority scores must be identical
        for tc1, tc2 in zip(plan1.test_cases, plan2.test_cases):
            self.assertEqual(tc1.objective, tc2.objective)
            self.assertEqual(tc1.priority_score, tc2.priority_score)
            self.assertEqual(tc1.priority_rationale, tc2.priority_rationale)
            self.assertEqual(tc1.category, tc2.category)

        # Coverage report summaries must match
        self.assertEqual(plan1.coverage_report.is_fully_covered, plan2.coverage_report.is_fully_covered)
        self.assertEqual(len(plan1.coverage_gaps), len(plan2.coverage_gaps))

    # 11. Lineage and Freeze Integrity
    def test_lineage_and_freeze_integrity(self) -> None:
        """Lineage mismatch raises TesterLineageError; frozen plan rejects mutation."""
        wo = self._make_work_order()
        ctx = self._make_context(project_id="different-project")

        with self.assertRaises(TesterLineageError):
            self.generator.generate(work_order=wo, test_context=ctx)

        # Verify frozen plan rejects changes
        valid_ctx = self._make_context()
        plan = self.generator.generate(work_order=wo, test_context=valid_ctx)
        self.assertEqual(plan.status, TestPlanStatus.FROZEN)

        with self.assertRaises(TesterBoundaryViolationError):
            plan.add_test_case(
                TestCase(
                    test_case_id=new_test_case_id(),
                    category=ApplicableTestCategory.FUNCTIONAL,
                    objective="Unauthorized injected test",
                )
            )

        # Execution session inherits coverage report from test_plan
        execution = TesterExecution(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            test_plan=plan,
        )
        self.assertIsNotNone(execution.coverage_report)
        self.assertEqual(execution.coverage_report.plan_id, plan.plan_id)
        self.assertEqual(execution.coverage_gaps, plan.coverage_gaps)

        # Coverage report lineage validation
        cov_dict = execution.coverage_report.to_dict()
        cov_dict["project_id"] = "mismatched-project"
        with self.assertRaises(TesterLineageError):
            TestCoverageReport.from_dict(cov_dict)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from typing import Any, Optional

from core.tester import (
    AcceptanceCriterion,
    ApplicabilityLevel,
    ApplicableTestCategory,
    CategoryApplicability,
    ChangeCategory,
    FindingCategory,
    PresenceAssessment,
    PresenceStatus,
    SurfaceAssessment,
    TestApplicabilityClassifier,
    TestApplicabilityReport,
    TestCase,
    TestCaseResult,
    TestCategory,
    TestContext,
    TestContextBuilder,
    TestEnvironment,
    TestPlan,
    TestPlanGenerator,
    TestPlanStatus,
    TestPriority,
    TestScope,
    TestStep,
    TestSurface,
    TesterBoundaryViolationError,
    TesterDefect,
    TesterExecution,
    TesterExecutionStatus,
    TesterFinding,
    TestingCapability,
    TesterLineageError,
    TesterValidationError,
    TesterWorkOrder,
    new_execution_id,
    new_plan_id,
    new_test_case_id,
    new_work_order_id,
)


class TestTesterPlan(unittest.TestCase):
    """
    Validation test suite for Tester V1 Phase 3.3: Finite Test Plan Generation.
    Validates:
    1. Backend-only plan generation
    2. Frontend-only plan generation
    3. Full-stack plan generation
    4. API-only plan generation
    5. Animation change plan generation
    6. CSS/layout change plan generation
    7. Explicit acceptance criterion linkage
    8. Mixed changes plan generation
    9. No applicable categories (valid 0-test plan)
    10. Maximum test case budget enforcement
    11. Time budget preservation
    12. Iteration budget preservation
    13. Frozen plan immutability enforcement
    14. No recursive test case creation / injection
    15. Unauthorized category exclusion
    16. Provenance and lineage verification
    17. Deterministic generation and sort stability
    """

    def setUp(self) -> None:
        self.generator = TestPlanGenerator()
        self.classifier = TestApplicabilityClassifier()
        self.project_id = "proj-plan-test"
        self.task_id = "task-plan-test-01"
        self.correlation_id = "corr-plan-test-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()

    def _make_work_order(self, **kwargs) -> TesterWorkOrder:
        defaults = {
            "work_order_id": self.work_order_id,
            "manager_task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "objective": "Verify finite test plan generation.",
            "test_scope": TestScope(
                components=["CoreService"],
                routes=["/test"],
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
            "change_categories": [ChangeCategory.BACKEND],
            "changed_files": ["src/service.py"],
            "changed_modules": ["src.service"],
            "changed_components": [],
            "changed_routes": [],
            "changed_apis": [],
        }
        defaults.update(kwargs)
        return TestContext(**defaults)

    def _make_execution(self, work_order: TesterWorkOrder) -> TesterExecution:
        exec_session = TesterExecution.from_work_order(work_order)
        exec_session.execution_id = self.execution_id
        return exec_session

    # =========================================================================
    # Test 1: Backend-only change plan
    # =========================================================================
    def test_backend_only_plan(self) -> None:
        """Backend-only changes produce functional/backend tests; zero UI/visual/animation tests."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.ABSENT),
            backend=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.BACKEND],
            changed_files=["src/server/auth.py"],
            changed_modules=["src.server.auth"],
        )

        plan = self.generator.generate(work_order=wo, test_context=ctx)

        self.assertTrue(plan.is_frozen)
        self.assertGreater(len(plan.test_cases), 0)
        # All test cases must be functional or integration
        for tc in plan.test_cases:
            self.assertIn(tc.category, {ApplicableTestCategory.FUNCTIONAL, ApplicableTestCategory.INTEGRATION})
            self.assertNotIn(tc.category, {
                ApplicableTestCategory.UI_INTERACTION,
                ApplicableTestCategory.VISUAL,
                ApplicableTestCategory.RESPONSIVE,
                ApplicableTestCategory.ANIMATION,
            })
            # No UI interaction evidence
            self.assertNotIn("VIDEO", tc.evidence_expectations)

    # =========================================================================
    # Test 2: Frontend-only change plan
    # =========================================================================
    def test_frontend_only_plan(self) -> None:
        """Frontend UI changes produce UI interaction and functional test cases with screenshots."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.ABSENT),
            change_categories=[ChangeCategory.FRONTEND],
            changed_files=["src/components/UserProfile.tsx"],
            changed_components=["UserProfile"],
        )

        plan = self.generator.generate(work_order=wo, test_context=ctx)

        self.assertTrue(plan.is_frozen)
        categories = {tc.category for tc in plan.test_cases}
        self.assertIn(ApplicableTestCategory.UI_INTERACTION, categories)
        # Visual evidence expected for frontend interaction
        ui_cases = [tc for tc in plan.test_cases if tc.category == ApplicableTestCategory.UI_INTERACTION]
        for tc in ui_cases:
            self.assertIn("SCREENSHOT", tc.evidence_expectations)

    # =========================================================================
    # Test 3: Full-stack change plan
    # =========================================================================
    def test_full_stack_plan(self) -> None:
        """Full-stack changes produce functional, UI interaction, and integration test cases."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.FRONTEND, ChangeCategory.BACKEND],
            changed_files=["src/server/api.py", "src/ui/Form.tsx"],
            changed_modules=["src.server.api"],
            changed_components=["Form"],
        )

        plan = self.generator.generate(work_order=wo, test_context=ctx)

        self.assertTrue(plan.is_frozen)
        categories = {tc.category for tc in plan.test_cases}
        self.assertTrue(
            categories.issubset({
                ApplicableTestCategory.FUNCTIONAL,
                ApplicableTestCategory.UI_INTERACTION,
                ApplicableTestCategory.INTEGRATION,
                ApplicableTestCategory.VISUAL,
                ApplicableTestCategory.RESPONSIVE,
                ApplicableTestCategory.NAVIGATION,
            })
        )
        self.assertIn(ApplicableTestCategory.UI_INTERACTION, categories)
        self.assertIn(ApplicableTestCategory.INTEGRATION, categories)

    # =========================================================================
    # Test 4: API-only change plan
    # =========================================================================
    def test_api_only_plan(self) -> None:
        """API endpoints produce functional/integration API test cases; zero UI tests."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.ABSENT),
            backend=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.API, ChangeCategory.BACKEND],
            changed_files=["src/api/routes.py"],
            changed_apis=["/api/v1/checkout"],
        )

        plan = self.generator.generate(work_order=wo, test_context=ctx)

        self.assertTrue(plan.is_frozen)
        categories = {tc.category for tc in plan.test_cases}
        self.assertNotIn(ApplicableTestCategory.UI_INTERACTION, categories)
        self.assertNotIn(ApplicableTestCategory.VISUAL, categories)
        self.assertNotIn(ApplicableTestCategory.ANIMATION, categories)

    # =========================================================================
    # Test 5: Animation change plan
    # =========================================================================
    def test_animation_change_plan(self) -> None:
        """CSS/animation changes produce animation test cases expecting screen recording / video."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.ABSENT),
            change_categories=[ChangeCategory.FRONTEND],
            changed_files=["styles/animations.css", "src/components/fade_transition.tsx"],
            test_surfaces=[
                SurfaceAssessment(surface=TestSurface.ANIMATION, status=PresenceStatus.PRESENT),
            ],
        )

        plan = self.generator.generate(work_order=wo, test_context=ctx)

        self.assertTrue(plan.is_frozen)
        anim_cases = [tc for tc in plan.test_cases if tc.category == ApplicableTestCategory.ANIMATION]
        self.assertGreater(len(anim_cases), 0)
        for tc in anim_cases:
            self.assertIn("VIDEO", tc.evidence_expectations)
            self.assertEqual(tc.priority, TestPriority.HIGH)

    # =========================================================================
    # Test 6: CSS layout change plan
    # =========================================================================
    def test_css_layout_change_plan(self) -> None:
        """CSS/layout changes produce visual/responsive test cases expecting screenshots."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.ABSENT),
            change_categories=[ChangeCategory.FRONTEND],
            changed_files=["styles/grid.css", "styles/layout.scss"],
        )

        plan = self.generator.generate(work_order=wo, test_context=ctx)

        self.assertTrue(plan.is_frozen)
        visual_cases = [
            tc for tc in plan.test_cases
            if tc.category in {ApplicableTestCategory.VISUAL, ApplicableTestCategory.RESPONSIVE}
        ]
        self.assertGreater(len(visual_cases), 0)
        for tc in visual_cases:
            self.assertIn("SCREENSHOT", tc.evidence_expectations)

    # =========================================================================
    # Test 7: Explicit acceptance criterion
    # =========================================================================
    def test_explicit_acceptance_criterion(self) -> None:
        """Explicit acceptance criteria produce directly linked CRITICAL priority test cases."""
        criterion = AcceptanceCriterion(
            criterion_id="ac-auth-oauth2",
            description="User authenticates successfully using OAuth2 flow.",
        )
        wo = self._make_work_order(acceptance_criteria=[criterion])
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.FRONTEND, ChangeCategory.BACKEND],
            changed_files=["src/auth/oauth.py"],
        )

        plan = self.generator.generate(work_order=wo, test_context=ctx)

        self.assertTrue(plan.is_frozen)
        linked_cases = [tc for tc in plan.test_cases if "ac-auth-oauth2" in tc.acceptance_linkage]
        self.assertEqual(len(linked_cases), 1)
        tc = linked_cases[0]
        self.assertEqual(tc.priority, TestPriority.CRITICAL)
        self.assertEqual(tc.authorization_reference, "ac-auth-oauth2")
        self.assertIn("OAuth2", tc.objective)

    # =========================================================================
    # Test 8: Mixed changes plan
    # =========================================================================
    def test_mixed_changes_plan(self) -> None:
        """Combination of changes produces bounded, non-overlapping coverage tailored to change areas."""
        wo = self._make_work_order(
            required_flows=["checkout_flow"],
        )
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.FRONTEND, ChangeCategory.BACKEND],
            changed_files=["src/routes/checkout.py", "src/components/CheckoutButton.tsx"],
            changed_routes=["/checkout"],
            changed_components=["CheckoutButton"],
        )

        plan = self.generator.generate(work_order=wo, test_context=ctx)

        self.assertTrue(plan.is_frozen)
        objectives = [tc.objective for tc in plan.test_cases]
        # No duplicate objectives
        self.assertEqual(len(objectives), len(set(objectives)))
        # Verify navigation and flow tests exist
        categories = {tc.category for tc in plan.test_cases}
        self.assertIn(ApplicableTestCategory.NAVIGATION, categories)
        self.assertIn(ApplicableTestCategory.UI_INTERACTION, categories)

    # =========================================================================
    # Test 9: No applicable categories (valid 0-test plan)
    # =========================================================================
    def test_no_applicable_categories_plan(self) -> None:
        """Documentation or zero-applicability produces valid frozen plan with 0 test cases (success, not failure)."""
        wo = self._make_work_order()
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.ABSENT),
            backend=PresenceAssessment(status=PresenceStatus.ABSENT),
            change_categories=[ChangeCategory.DOCUMENTATION],
            changed_files=["docs/README.md"],
            changed_modules=[],
            changed_components=[],
            changed_routes=[],
            changed_apis=[],
        )

        plan = self.generator.generate(work_order=wo, test_context=ctx)

        self.assertEqual(len(plan.test_cases), 0)
        self.assertTrue(plan.is_frozen)
        self.assertEqual(plan.status, TestPlanStatus.FROZEN)
        self.assertIn("Zero executable test cases applicable", plan.estimated_execution_scope)

    # =========================================================================
    # Test 10: Maximum test case budget enforcement
    # =========================================================================
    def test_maximum_test_case_budget(self) -> None:
        """Plan generation strictly truncates candidate cases to max_test_cases budget."""
        criterion = AcceptanceCriterion(criterion_id="ac-crit-1", description="Critical auth")
        wo = self._make_work_order(
            acceptance_criteria=[criterion],
            metadata={"max_test_cases": 3},
        )
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.FRONTEND, ChangeCategory.BACKEND],
            changed_files=["src/a.py", "src/b.py", "src/c.tsx"],
            changed_routes=["/r1", "/r2", "/r3", "/r4"],
            changed_components=["Comp1", "Comp2", "Comp3"],
        )

        # Generate with max_test_cases = 3
        plan = self.generator.generate(work_order=wo, test_context=ctx, max_test_cases=3)

        self.assertEqual(len(plan.test_cases), 3)
        self.assertEqual(plan.max_test_cases, 3)
        # CRITICAL priority test case from acceptance criteria must be preserved
        self.assertEqual(plan.test_cases[0].priority, TestPriority.CRITICAL)

    # =========================================================================
    # Test 11: Time budget preservation
    # =========================================================================
    def test_time_budget(self) -> None:
        """Preserves finite time budget from WorkOrder."""
        wo = self._make_work_order(time_budget=180)
        ctx = self._make_context()

        plan = self.generator.generate(work_order=wo, test_context=ctx)

        self.assertEqual(plan.time_budget, 180)

    # =========================================================================
    # Test 12: Iteration budget preservation
    # =========================================================================
    def test_iteration_budget(self) -> None:
        """Preserves finite iteration budget from WorkOrder."""
        wo = self._make_work_order(iteration_budget=2)
        ctx = self._make_context()

        plan = self.generator.generate(work_order=wo, test_context=ctx)

        self.assertEqual(plan.iteration_budget, 2)

    # =========================================================================
    # Test 13: Frozen plan immutability enforcement
    # =========================================================================
    def test_frozen_plan_immutability(self) -> None:
        """Attempting to add or remove test cases after freeze raises TesterBoundaryViolationError."""
        wo = self._make_work_order()
        ctx = self._make_context()

        plan = self.generator.generate(work_order=wo, test_context=ctx)
        self.assertTrue(plan.is_frozen)

        new_tc = TestCase(
            test_case_id=new_test_case_id(),
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Illegal dynamically added test",
            preconditions=[],
            steps=[],
            expected_outcome="Fail",
            priority=TestPriority.LOW,
            acceptance_linkage=[],
            evidence_expectations=[],
            authorization_reference="unauthorized",
        )

        with self.assertRaises(TesterBoundaryViolationError) as cm:
            plan.add_test_case(new_tc)
        self.assertIn("FROZEN", str(cm.exception))

        if plan.test_cases:
            first_id = plan.test_cases[0].test_case_id
            with self.assertRaises(TesterBoundaryViolationError) as cm:
                plan.remove_test_case(first_id)
            self.assertIn("FROZEN", str(cm.exception))

    # =========================================================================
    # Test 14: No recursive test case creation / injection
    # =========================================================================
    def test_no_recursive_test_creation(self) -> None:
        """Verifies plan cannot be expanded or injected dynamically by defects, findings, or results."""
        wo = self._make_work_order()
        ctx = self._make_context()
        exec_session = self._make_execution(wo)

        plan = self.generator.generate(work_order=wo, test_context=ctx)
        exec_session.attach_test_plan(plan)

        initial_count = len(plan.test_cases)
        initial_ids = [tc.test_case_id for tc in plan.test_cases]

        # Record defects, findings, and traces on execution
        exec_session.transition_to(TesterExecutionStatus.STARTING, reason="Starting execution")
        exec_session.transition_to(TesterExecutionStatus.RUNNING, reason="Running execution")
        exec_session.record_defect(
            title="Spurious issue found during run",
            description="Tester wants to invent a new test for this.",
        )
        exec_session.record_finding(
            category=FindingCategory.UX,
            title="UX suggestion",
            description="Invent another test?",
            recommendation="Add more tests",
        )

        # Plan must remain completely unaltered
        self.assertEqual(len(exec_session.test_plan.test_cases), initial_count)
        self.assertEqual([tc.test_case_id for tc in exec_session.test_plan.test_cases], initial_ids)

    # =========================================================================
    # Test 15: Unauthorized category exclusion
    # =========================================================================
    def test_unauthorized_category_exclusion(self) -> None:
        """WorkOrder without authorized capabilities excludes unauthorized test cases even if applicable."""
        # Work order without UI_INTERACTION or SCREEN_RECORDING capability
        wo = self._make_work_order(
            authorized_capabilities=[TestingCapability.TEST_EXECUTION],
        )
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.FRONTEND],
            changed_files=["src/styles/anim.css", "src/ui/Button.tsx"],
            changed_components=["Button"],
        )

        plan = self.generator.generate(work_order=wo, test_context=ctx)

        self.assertTrue(plan.is_frozen)
        categories = {tc.category for tc in plan.test_cases}
        self.assertNotIn(ApplicableTestCategory.UI_INTERACTION, categories)
        self.assertNotIn(ApplicableTestCategory.ANIMATION, categories)
        self.assertNotIn(ApplicableTestCategory.VIDEO, categories)

    # =========================================================================
    # Test 16: Provenance and lineage verification
    # =========================================================================
    def test_provenance(self) -> None:
        """Execution ID, Work Order ID, Project ID, and trace lineage are preserved and verifiable on TesterExecution."""
        wo = self._make_work_order()
        ctx = self._make_context()
        exec_session = self._make_execution(wo)

        plan = self.generator.generate(work_order=wo, test_context=ctx)
        exec_session.attach_test_plan(plan)

        self.assertIsNotNone(exec_session.test_plan)
        self.assertEqual(exec_session.test_plan.execution_id, self.execution_id)
        self.assertEqual(exec_session.test_plan.work_order_id, self.work_order_id)
        self.assertEqual(exec_session.test_plan.project_id, self.project_id)
        self.assertEqual(exec_session.test_plan.provenance["work_order_id"], self.work_order_id)
        self.assertEqual(exec_session.test_plan.provenance["project_id"], self.project_id)
        self.assertEqual(exec_session.test_plan.trace["generator"], "TestPlanGenerator")

        # Mismatched execution_id must raise TesterLineageError
        foreign_plan = TestPlan(
            plan_id=new_plan_id(),
            work_order_id=self.work_order_id,
            execution_id="texec-foreign-9999",
            project_id=self.project_id,
            test_cases=[],
            required_categories=[],
            optional_categories=[],
            not_applicable_categories=[],
            max_test_cases=10,
            time_budget=300,
            iteration_budget=5,
        )
        with self.assertRaises(TesterLineageError):
            exec_session.attach_test_plan(foreign_plan)

        # Mismatched project_id between work_order and test_context raises TesterLineageError in generate
        mismatched_wo = self._make_work_order(project_id="proj-different")
        with self.assertRaises(TesterLineageError):
            self.generator.generate(work_order=mismatched_wo, test_context=ctx)

    # =========================================================================
    # Test 17: Deterministic generation and sort stability
    # =========================================================================
    def test_deterministic_generation(self) -> None:
        """Repeated runs with identical inputs produce identical test cases, steps, priorities, and ordering."""
        wo = self._make_work_order(
            test_scope=TestScope(
                components=["Header", "Footer"],
                routes=["/home", "/about"],
                environments=["staging"],
            )
        )
        ctx = self._make_context(
            frontend=PresenceAssessment(status=PresenceStatus.PRESENT),
            backend=PresenceAssessment(status=PresenceStatus.PRESENT),
            change_categories=[ChangeCategory.FRONTEND],
            changed_files=["src/components/Header.tsx", "src/components/Footer.tsx"],
            changed_routes=["/home", "/about"],
            changed_components=["Header", "Footer"],
        )

        plan1 = self.generator.generate(work_order=wo, test_context=ctx)
        plan2 = self.generator.generate(work_order=wo, test_context=ctx)

        # Structural parity check
        self.assertEqual(len(plan1.test_cases), len(plan2.test_cases))
        for tc1, tc2 in zip(plan1.test_cases, plan2.test_cases):
            self.assertEqual(tc1.category, tc2.category)
            self.assertEqual(tc1.objective, tc2.objective)
            self.assertEqual(tc1.priority, tc2.priority)
            self.assertEqual(tc1.expected_outcome, tc2.expected_outcome)
            self.assertEqual(tc1.acceptance_linkage, tc2.acceptance_linkage)
            self.assertEqual(tc1.evidence_expectations, tc2.evidence_expectations)
            self.assertEqual(tc1.authorization_reference, tc2.authorization_reference)
            self.assertEqual(len(tc1.steps), len(tc2.steps))
            for s1, s2 in zip(tc1.steps, tc2.steps):
                self.assertEqual(s1.step_number, s2.step_number)
                self.assertEqual(s1.description, s2.description)
                self.assertEqual(s1.action, s2.action)
                self.assertEqual(s1.target, s2.target)
                self.assertEqual(s1.expected, s2.expected)

        # Byte-identical serialization parity check with fixed timestamps and IDs
        with patch("core.tester.contracts.plan.utc_now", return_value="2026-09-09T12:00:00Z"), \
             patch("core.tester.contracts.plan.new_plan_id", return_value="tplan-fixed01"), \
             patch("core.tester.contracts.plan.new_test_case_id", side_effect=[f"ttest-{i:04d}" for i in range(50)]):
            fixed_plan1 = self.generator.generate(work_order=wo, test_context=ctx)

        with patch("core.tester.contracts.plan.utc_now", return_value="2026-09-09T12:00:00Z"), \
             patch("core.tester.contracts.plan.new_plan_id", return_value="tplan-fixed01"), \
             patch("core.tester.contracts.plan.new_test_case_id", side_effect=[f"ttest-{i:04d}" for i in range(50)]):
            fixed_plan2 = self.generator.generate(work_order=wo, test_context=ctx)

        self.assertEqual(fixed_plan1.to_dict(), fixed_plan2.to_dict())

    # =========================================================================
    # Additional Verification: Serialization Roundtrip
    # =========================================================================
    def test_plan_serialization_roundtrip(self) -> None:
        """TestPlan serializes to and deserializes from dictionary preserving all fields."""
        wo = self._make_work_order()
        ctx = self._make_context()
        plan = self.generator.generate(work_order=wo, test_context=ctx)

        d = plan.to_dict()
        restored = TestPlan.from_dict(d)

        self.assertEqual(restored.plan_id, plan.plan_id)
        self.assertEqual(restored.work_order_id, plan.work_order_id)
        self.assertEqual(restored.execution_id, plan.execution_id)
        self.assertEqual(restored.project_id, plan.project_id)
        self.assertEqual(len(restored.test_cases), len(plan.test_cases))
        self.assertEqual(restored.status, plan.status)
        self.assertEqual(restored.max_test_cases, plan.max_test_cases)
        self.assertEqual(restored.time_budget, plan.time_budget)
        self.assertEqual(restored.iteration_budget, plan.iteration_budget)


if __name__ == "__main__":
    unittest.main()

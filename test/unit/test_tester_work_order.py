from __future__ import annotations

import unittest
from unittest.mock import patch
import uuid

from core.models import Task
from core.tester import (
    AcceptanceCriterion,
    ForbiddenTesterAction,
    InvalidTesterIdError,
    QualityThresholds,
    TESTER_ALLOWED_CAPABILITIES,
    TESTER_FORBIDDEN_ACTIONS,
    TestCategory,
    TestEnvironment,
    TesterBoundaryGuard,
    TesterBoundaryViolationError,
    TesterExecution,
    TesterExecutionStatus,
    TesterLineageError,
    TestScope,
    TesterValidationError,
    TesterWorkOrder,
    TesterWorkOrderStatus,
    TesterWorkOrderValidator,
    TestingCapability,
    WORK_ORDER_ID_PREFIX,
    new_work_order_id,
    validate_work_order_id,
)


class TestTesterWorkOrder(unittest.TestCase):
    """
    Unit tests for TesterWorkOrder authorization contract (Tester V1 Phase 1.2).
    Validates:
    1. Valid work order creation and validation
    2. Objective validation (empty, whitespace, excessive length)
    3. Identifier validation (prefix, format, blank ID rejection)
    4. Lineage integrity (task ID match, manager task binding)
    5. Project isolation enforcement (cross-project execution rejection)
    6. Scope validation (empty scope, wildcards, whitespace, explicitness)
    7. Capability authorization (granting and querying authorized capabilities)
    8. Global boundary and work order intersection (Global ∩ WorkOrder authority)
    9. Acceptance criteria modeling and validation
    10. Quality thresholds validation (defect limits, pass rate, performance bounds)
    11. Time budget validation
    12. Iteration budget enforcement (bounded iterations >= 1, anti-infinite loop)
    13. Serialization fidelity roundtrip
    14. Tampering detection and immutable revisioning
    15. Invariant: zero execution and zero source code modification during validation
    """

    def _make_valid_work_order(self, **kwargs) -> TesterWorkOrder:
        """Helper to create a fully valid baseline work order for testing variations."""
        defaults = {
            "work_order_id": new_work_order_id(),
            "manager_task_id": "task-baseline-01",
            "project_id": "proj-baseline",
            "correlation_id": "corr-baseline-01",
            "objective": "Verify user registration and email confirmation workflow",
            "instructions": [
                "Navigate to /register",
                "Submit registration form with valid test credentials",
                "Verify confirmation email received with token",
            ],
            "product_artifact": "build-pkg-v2.1.0",
            "source_revision": "git-sha-7a8b9c",
            "test_scope": TestScope(
                routes=["/register", "/confirm-email"],
                features=["registration", "email_verification"],
                flows=["user_signup_flow"],
                components=["auth_service"],
            ),
            "test_categories": [TestCategory.FUNCTIONAL, TestCategory.INTEGRATION],
            "required_flows": ["user_signup_flow"],
            "acceptance_criteria": [
                AcceptanceCriterion(
                    criterion_id="ac-reg-01",
                    description="User account is created in database with pending verification status",
                ),
                AcceptanceCriterion(
                    criterion_id="ac-reg-02",
                    description="Confirmation email contains valid one-time verification link",
                ),
            ],
            "authorized_capabilities": [
                TestingCapability.NAVIGATE,
                TestingCapability.CLICK,
                TestingCapability.TYPE,
                TestingCapability.SCREENSHOT,
            ],
            "test_environment": TestEnvironment(
                env_name="staging",
                base_url="https://staging.testapp.internal",
                variables={"MOCK_EMAIL": "true"},
            ),
            "constraints": [
                "Do not use production email domains",
                "Total runtime limit 300 seconds",
            ],
            "quality_thresholds": QualityThresholds(
                max_critical_defects=0,
                max_high_defects=0,
                min_acceptance_pass_rate=1.0,
                max_performance_threshold_ms=1500.0,
            ),
            "time_budget": 300,
            "iteration_budget": 3,
            "status": TesterWorkOrderStatus.ASSIGNED,
            "metadata": {"priority": "P1", "environment": "staging"},
        }
        defaults.update(kwargs)
        return TesterWorkOrder(**defaults)

    def test_valid_work_order_creation(self):
        """1. Verify that a fully populated TesterWorkOrder initializes correctly and passes validation."""
        wo = self._make_valid_work_order()
        wo.validate()

        self.assertTrue(wo.work_order_id.startswith(WORK_ORDER_ID_PREFIX))
        self.assertEqual(wo.manager_task_id, "task-baseline-01")
        self.assertEqual(wo.task_id, "task-baseline-01")
        self.assertEqual(wo.project_id, "proj-baseline")
        self.assertEqual(wo.correlation_id, "corr-baseline-01")
        self.assertEqual(wo.objective, "Verify user registration and email confirmation workflow")
        self.assertEqual(len(wo.instructions), 3)
        self.assertEqual(wo.product_artifact, "build-pkg-v2.1.0")
        self.assertEqual(wo.source_revision, "git-sha-7a8b9c")
        self.assertFalse(wo.test_scope.is_empty())
        self.assertEqual(len(wo.test_categories), 2)
        self.assertEqual(wo.test_categories[0], TestCategory.FUNCTIONAL)
        self.assertEqual(len(wo.acceptance_criteria), 2)
        self.assertEqual(len(wo.authorized_capabilities), 4)
        self.assertEqual(wo.test_environment.env_name, "staging")
        self.assertEqual(wo.time_budget, 300)
        self.assertEqual(wo.iteration_budget, 3)
        self.assertEqual(wo.status, TesterWorkOrderStatus.ASSIGNED)
        self.assertEqual(wo.revision_number, 1)
        self.assertIsNone(wo.parent_work_order_id)

    def test_invalid_objective(self):
        """2. Verify that TesterWorkOrder rejects empty, whitespace-only, or overly long objectives."""
        # Empty objective
        wo_empty = self._make_valid_work_order(objective="")
        with self.assertRaises(TesterValidationError) as ctx:
            wo_empty.validate()
        self.assertEqual(ctx.exception.field_name, "objective")

        # Whitespace-only objective
        wo_ws = self._make_valid_work_order(objective="   \n\t   ")
        with self.assertRaises(TesterValidationError) as ctx:
            wo_ws.validate()
        self.assertEqual(ctx.exception.field_name, "objective")

        # Excessive length objective (> 10,000 characters)
        wo_long = self._make_valid_work_order(objective="x" * 10_001)
        with self.assertRaises(TesterValidationError) as ctx:
            wo_long.validate()
        self.assertEqual(ctx.exception.field_name, "objective")

    def test_invalid_ids(self):
        """3. Verify rejection of invalid work_order_id, manager_task_id, project_id, and correlation_id."""
        # Missing prefix
        with self.assertRaises(InvalidTesterIdError):
            self._make_valid_work_order(work_order_id="invalid-id-1234")

        # Wrong prefix
        with self.assertRaises(InvalidTesterIdError):
            self._make_valid_work_order(work_order_id="pwo-12345678")

        # Invalid characters
        with self.assertRaises(InvalidTesterIdError):
            self._make_valid_work_order(work_order_id="two-invalid!@#")

        # Empty suffix
        with self.assertRaises(InvalidTesterIdError):
            self._make_valid_work_order(work_order_id="two-")

        # Empty manager_task_id
        with self.assertRaises(TesterLineageError):
            self._make_valid_work_order(manager_task_id="", task_id="")

        # Empty project_id
        with self.assertRaises(TesterLineageError):
            self._make_valid_work_order(project_id="")

        # Empty correlation_id
        with self.assertRaises(TesterLineageError):
            self._make_valid_work_order(correlation_id="")

    def test_lineage_mismatch(self):
        """4. Verify rejection when task IDs conflict or work order does not match Manager task."""
        # Task ID mismatch between manager_task_id and task_id
        with self.assertRaises(TesterLineageError):
            self._make_valid_work_order(
                manager_task_id="task-mgr-01",
                task_id="task-other-02",
            )

        wo = self._make_valid_work_order()
        # Matching manager task passes
        matching_task = Task(
            id="task-baseline-01",
            project_id="proj-baseline",
            title="Baseline Task",
            objective="Baseline Objective",
        )
        wo.validate_lineage(matching_task)

        # Mismatched manager task ID raises TesterLineageError
        foreign_task = Task(
            id="task-different-99",
            project_id="proj-baseline",
            title="Foreign Task",
            objective="Baseline Objective",
        )
        with self.assertRaises(TesterLineageError):
            wo.validate_lineage(foreign_task)

        # Raw string task ID mismatch
        with self.assertRaises(TesterLineageError):
            wo.validate_lineage("task-different-99")

    def test_project_mismatch(self):
        """5. Verify that work orders cannot be associated with or executed under a foreign project."""
        wo = self._make_valid_work_order(project_id="proj-alpha")

        # Mismatched project ID raises TesterLineageError
        cross_proj_task = Task(
            id="task-baseline-01",
            project_id="proj-beta",
            title="Cross Project Task",
            objective="Baseline Objective",
        )
        with self.assertRaises(TesterLineageError) as ctx:
            wo.validate_lineage(cross_proj_task)
        self.assertIn("Project mismatch", str(ctx.exception))

    def test_scope_validation(self):
        """6. Verify scope validation: rejects empty scope, wildcards (*, /..., all), and whitespace."""
        # Empty scope
        wo_empty_scope = self._make_valid_work_order(
            test_scope=TestScope(routes=[], features=[], flows=[], components=[])
        )
        with self.assertRaises(TesterValidationError) as ctx:
            wo_empty_scope.validate()
        self.assertEqual(ctx.exception.field_name, "test_scope")

        # Wildcard route '*'
        wo_star = self._make_valid_work_order(
            test_scope=TestScope(routes=["*"])
        )
        with self.assertRaises(TesterValidationError):
            wo_star.validate()

        # Wildcard feature 'all'
        wo_all = self._make_valid_work_order(
            test_scope=TestScope(features=["all"])
        )
        with self.assertRaises(TesterValidationError):
            wo_all.validate()

        # Wildcard route '/...'
        wo_ellipsis = self._make_valid_work_order(
            test_scope=TestScope(routes=["/..."])
        )
        with self.assertRaises(TesterValidationError):
            wo_ellipsis.validate()

        # Wildcard prefix '/api/*'
        wo_substar = self._make_valid_work_order(
            test_scope=TestScope(routes=["/api/*"])
        )
        with self.assertRaises(TesterValidationError):
            wo_substar.validate()

        # Blank entry in scope
        wo_blank_entry = self._make_valid_work_order(
            test_scope=TestScope(routes=["/valid", "   "])
        )
        with self.assertRaises(TesterValidationError):
            wo_blank_entry.validate()

        # Scope containment testing
        scope = TestScope(
            routes=["/login", "/dashboard"],
            features=["auth", "profile"],
            flows=["checkout_flow"],
            components=["cart_component"],
        )
        self.assertTrue(scope.contains_target("/login"))
        self.assertTrue(scope.contains_target("/dashboard/settings"))
        self.assertTrue(scope.contains_target("auth"))
        self.assertTrue(scope.contains_target("checkout_flow"))
        self.assertFalse(scope.contains_target("/admin/superuser"))
        self.assertFalse(scope.contains_target("unknown_service"))

    def test_capability_authorization(self):
        """7. Verify granting, normalizing, and querying authorized testing capabilities."""
        wo = self._make_valid_work_order(
            authorized_capabilities=[
                TestingCapability.NAVIGATE,
                "CLICK",
                TestingCapability.SCREENSHOT,
            ]
        )
        wo.validate()

        # Explicitly granted capabilities
        self.assertTrue(wo.is_capability_authorized(TestingCapability.NAVIGATE))
        self.assertTrue(wo.is_capability_authorized(TestingCapability.CLICK))
        self.assertTrue(wo.is_capability_authorized("CLICK"))
        self.assertTrue(wo.is_capability_authorized(TestingCapability.SCREENSHOT))

        # Capabilities allowed for Tester globally, but NOT granted to this work order
        self.assertFalse(wo.is_capability_authorized(TestingCapability.OCR))
        self.assertFalse(wo.is_capability_authorized(TestingCapability.PERFORMANCE_MEASUREMENT))
        self.assertFalse(wo.is_capability_authorized(TestingCapability.DRAG))

    def test_global_boundary_and_work_order_intersection(self):
        """
        8. Verify that effective authority is strictly: Global Boundary ∩ WorkOrder Authorization.
        Never the union. Forbidden implementer actions can never be authorized.
        """
        # Global Tester Boundary allows standard testing capabilities
        for cap in TESTER_ALLOWED_CAPABILITIES:
            self.assertTrue(TesterBoundaryGuard.is_capability_allowed(cap))

        # Global Tester Boundary strictly forbids implementer actions
        for forbidden in TESTER_FORBIDDEN_ACTIONS:
            self.assertTrue(TesterBoundaryGuard.is_action_forbidden(forbidden))
            self.assertFalse(TesterBoundaryGuard.is_capability_allowed(forbidden.value))

        # Attempting to authorize a forbidden action in work order must fail validation
        for forbidden_action in [
            ForbiddenTesterAction.MODIFY_SOURCE_CODE,
            ForbiddenTesterAction.FIX_DEFECT,
            ForbiddenTesterAction.MERGE_CODE,
            ForbiddenTesterAction.DEPLOY_PRODUCT,
        ]:
            wo_malicious = self._make_valid_work_order(
                authorized_capabilities=[
                    TestingCapability.NAVIGATE,
                    forbidden_action.value,
                ]
            )
            with self.assertRaises(TesterBoundaryViolationError) as ctx:
                wo_malicious.validate()
            self.assertEqual(ctx.exception.action, forbidden_action.value)

            # Even before validation, is_capability_authorized MUST return False for forbidden actions
            self.assertFalse(wo_malicious.is_capability_authorized(forbidden_action))
            self.assertFalse(wo_malicious.is_capability_authorized(forbidden_action.value))

        # Work order granted capability that is outside Tester capability boundary
        wo_unknown = self._make_valid_work_order(
            authorized_capabilities=[
                TestingCapability.NAVIGATE,
                "ARBITRARY_UNKNOWN_ACTION",
            ]
        )
        with self.assertRaises(TesterValidationError):
            wo_unknown.validate()
        self.assertFalse(wo_unknown.is_capability_authorized("ARBITRARY_UNKNOWN_ACTION"))

    def test_acceptance_criteria_validation(self):
        """9. Verify acceptance criteria validation: rejection of empty list, blank description, empty ID."""
        # Empty criteria
        wo_empty = self._make_valid_work_order(acceptance_criteria=[])
        with self.assertRaises(TesterValidationError) as ctx:
            wo_empty.validate()
        self.assertEqual(ctx.exception.field_name, "acceptance_criteria")

        # Blank description in criterion object
        criterion_blank = AcceptanceCriterion(criterion_id="ac-01", description="   ")
        wo_blank_desc = self._make_valid_work_order(acceptance_criteria=[criterion_blank])
        with self.assertRaises(TesterValidationError) as ctx:
            wo_blank_desc.validate()
        self.assertEqual(ctx.exception.field_name, "acceptance_criteria.description")

        # Blank criterion_id
        criterion_blank_id = AcceptanceCriterion(criterion_id="", description="Valid description")
        wo_blank_id = self._make_valid_work_order(acceptance_criteria=[criterion_blank_id])
        with self.assertRaises(TesterValidationError) as ctx:
            wo_blank_id.validate()
        self.assertEqual(ctx.exception.field_name, "acceptance_criteria.criterion_id")

        # String criteria are normalized and validated
        wo_str_criteria = self._make_valid_work_order(
            acceptance_criteria=["Page loads with 200 OK", "User token returned in header"]
        )
        wo_str_criteria.validate()
        self.assertEqual(len(wo_str_criteria.acceptance_criteria), 2)
        self.assertIsInstance(wo_str_criteria.acceptance_criteria[0], AcceptanceCriterion)

        # Blank string criterion rejected
        wo_blank_str = self._make_valid_work_order(acceptance_criteria=["   "])
        with self.assertRaises(TesterValidationError):
            wo_blank_str.validate()

    def test_quality_threshold_validation(self):
        """10. Verify quality thresholds validation: defect counts, pass rate range, performance threshold."""
        # Negative critical defects
        with self.assertRaises(TesterValidationError) as ctx:
            QualityThresholds(max_critical_defects=-1).validate()
        self.assertEqual(ctx.exception.field_name, "max_critical_defects")

        # Negative high defects
        with self.assertRaises(TesterValidationError) as ctx:
            QualityThresholds(max_high_defects=-5).validate()
        self.assertEqual(ctx.exception.field_name, "max_high_defects")

        # Pass rate > 1.0
        with self.assertRaises(TesterValidationError) as ctx:
            QualityThresholds(min_acceptance_pass_rate=1.05).validate()
        self.assertEqual(ctx.exception.field_name, "min_acceptance_pass_rate")

        # Pass rate < 0.0
        with self.assertRaises(TesterValidationError) as ctx:
            QualityThresholds(min_acceptance_pass_rate=-0.1).validate()
        self.assertEqual(ctx.exception.field_name, "min_acceptance_pass_rate")

        # Negative performance threshold
        with self.assertRaises(TesterValidationError) as ctx:
            QualityThresholds(max_performance_threshold_ms=-50.0).validate()
        self.assertEqual(ctx.exception.field_name, "max_performance_threshold_ms")

        # Valid thresholds pass
        valid_qt = QualityThresholds(
            max_critical_defects=0,
            max_high_defects=2,
            min_acceptance_pass_rate=0.9,
            max_performance_threshold_ms=800.0,
        )
        valid_qt.validate()

    def test_budget_validation(self):
        """11. Verify time budget validation: non-negative requirement."""
        # Negative time budget
        wo_neg_time = self._make_valid_work_order(time_budget=-1)
        with self.assertRaises(TesterValidationError) as ctx:
            wo_neg_time.validate()
        self.assertEqual(ctx.exception.field_name, "time_budget")

        # Zero time budget (allowed as non-negative)
        wo_zero_time = self._make_valid_work_order(time_budget=0)
        wo_zero_time.validate()

        # Positive time budget
        wo_pos_time = self._make_valid_work_order(time_budget=120)
        wo_pos_time.validate()

    def test_iteration_budget_enforcement(self):
        """
        12. Verify iteration budget enforcement: must be integer >= 1.
        Tester V1 must have an explicitly bounded iteration budget to prevent infinite improvement loops.
        """
        # Zero iterations rejected
        wo_zero_iter = self._make_valid_work_order(iteration_budget=0)
        with self.assertRaises(TesterValidationError) as ctx:
            wo_zero_iter.validate()
        self.assertEqual(ctx.exception.field_name, "iteration_budget")
        self.assertIn("infinite improvement loops", str(ctx.exception))

        # Negative iterations rejected
        wo_neg_iter = self._make_valid_work_order(iteration_budget=-3)
        with self.assertRaises(TesterValidationError) as ctx:
            wo_neg_iter.validate()
        self.assertEqual(ctx.exception.field_name, "iteration_budget")

        # Exactly 1 iteration is accepted
        wo_one_iter = self._make_valid_work_order(iteration_budget=1)
        wo_one_iter.validate()

        # Multiple iterations accepted
        wo_multi_iter = self._make_valid_work_order(iteration_budget=10)
        wo_multi_iter.validate()

    def test_serialization_roundtrip(self):
        """13. Verify complete serialization round-trip fidelity for TesterWorkOrder and nested models."""
        original = self._make_valid_work_order(
            parent_work_order_id="two-00001111",
            revision_number=3,
        )
        original.validate()

        # Serialize to dict
        data = original.to_dict()
        self.assertIsInstance(data, dict)
        self.assertEqual(data["work_order_id"], original.work_order_id)
        self.assertEqual(data["manager_task_id"], original.manager_task_id)
        self.assertEqual(data["project_id"], original.project_id)
        self.assertEqual(data["parent_work_order_id"], "two-00001111")
        self.assertEqual(data["revision_number"], 3)

        # Deserialize from dict
        restored = TesterWorkOrder.from_dict(data)
        restored.validate()

        # Assert exact fidelity
        self.assertEqual(restored.work_order_id, original.work_order_id)
        self.assertEqual(restored.manager_task_id, original.manager_task_id)
        self.assertEqual(restored.task_id, original.task_id)
        self.assertEqual(restored.project_id, original.project_id)
        self.assertEqual(restored.correlation_id, original.correlation_id)
        self.assertEqual(restored.objective, original.objective)
        self.assertEqual(restored.instructions, original.instructions)
        self.assertEqual(restored.product_artifact, original.product_artifact)
        self.assertEqual(restored.source_revision, original.source_revision)
        self.assertEqual(restored.test_scope, original.test_scope)
        self.assertEqual(restored.test_categories, original.test_categories)
        self.assertEqual(restored.required_flows, original.required_flows)
        self.assertEqual(len(restored.acceptance_criteria), len(original.acceptance_criteria))
        self.assertEqual(
            restored.acceptance_criteria[0].description,
            original.acceptance_criteria[0].description,
        )
        self.assertEqual(restored.authorized_capabilities, original.authorized_capabilities)
        self.assertEqual(restored.test_environment.env_name, original.test_environment.env_name)
        self.assertEqual(restored.test_environment.base_url, original.test_environment.base_url)
        self.assertEqual(restored.constraints, original.constraints)
        self.assertEqual(
            restored.quality_thresholds.min_acceptance_pass_rate,
            original.quality_thresholds.min_acceptance_pass_rate,
        )
        self.assertEqual(restored.time_budget, original.time_budget)
        self.assertEqual(restored.iteration_budget, original.iteration_budget)
        self.assertEqual(restored.status, original.status)
        self.assertEqual(restored.parent_work_order_id, original.parent_work_order_id)
        self.assertEqual(restored.revision_number, original.revision_number)

    def test_tampering_detection(self):
        """14. Verify tampering detection and clean revisioning via create_revision()."""
        original = self._make_valid_work_order()
        original.validate()

        # 1. Tampering with manager_task_id directly causes lineage failure
        tampered_wo = self._make_valid_work_order()
        tampered_wo.manager_task_id = "task-forged-999"
        with self.assertRaises(TesterLineageError):
            tampered_wo.validate_lineage(
                Task(id="task-baseline-01", project_id="proj-baseline", title="T", objective="O")
            )

        # 2. Immutable revisioning via create_revision()
        revision = original.create_revision(
            modifications={
                "time_budget": 600,
                "iteration_budget": 5,
                "objective": "Expanded evaluation of signup and recovery flows",
            },
            reason="Manager requested deeper investigation into password recovery",
        )
        revision.validate()

        # Original remains completely unchanged
        self.assertEqual(original.time_budget, 300)
        self.assertEqual(original.iteration_budget, 3)
        self.assertEqual(original.revision_number, 1)
        self.assertIsNone(original.parent_work_order_id)
        self.assertEqual(original.objective, "Verify user registration and email confirmation workflow")

        # Revision has new unique ID, parent link, incremented revision number, and new values
        self.assertTrue(revision.work_order_id.startswith(WORK_ORDER_ID_PREFIX))
        self.assertNotEqual(revision.work_order_id, original.work_order_id)
        self.assertEqual(revision.parent_work_order_id, original.work_order_id)
        self.assertEqual(revision.revision_number, 2)
        self.assertEqual(revision.time_budget, 600)
        self.assertEqual(revision.iteration_budget, 5)
        self.assertEqual(revision.objective, "Expanded evaluation of signup and recovery flows")
        self.assertEqual(revision.metadata["revision_reason"], "Manager requested deeper investigation into password recovery")
        self.assertEqual(revision.manager_task_id, original.manager_task_id)
        self.assertEqual(revision.project_id, original.project_id)

    def test_no_execution_or_source_modification_during_validation(self):
        """
        15. Verify invariant: zero product calls, zero browser automation, zero file writes,
        and zero test execution occur during work order initialization, validation, and revisioning.
        TesterWorkOrder is purely a declarative authorization contract.
        """
        wo = self._make_valid_work_order()

        with patch("subprocess.run") as mock_subproc, \
             patch("subprocess.Popen") as mock_popen, \
             patch("builtins.open", wraps=open) as mock_file_open:

            # Validate work order
            wo.validate()

            # Query capabilities
            _ = wo.is_capability_authorized(TestingCapability.NAVIGATE)
            _ = wo.is_capability_authorized(ForbiddenTesterAction.MODIFY_SOURCE_CODE)

            # Check lineage
            wo.validate_lineage("task-baseline-01")

            # Create revision
            rev = wo.create_revision(modifications={"time_budget": 450}, reason="Adjustment")
            rev.validate()

            # Serialization roundtrip
            d = wo.to_dict()
            _ = TesterWorkOrder.from_dict(d)

            # Assert no subprocess executions occurred
            mock_subproc.assert_not_called()
            mock_popen.assert_not_called()

            # Assert no files were opened for writing ('w', 'wb', 'a', etc.)
            for call in mock_file_open.call_args_list:
                args = call[0]
                if len(args) > 1:
                    mode = args[1]
                    self.assertNotIn("w", mode, "WorkOrder must never open files for writing")
                    self.assertNotIn("a", mode, "WorkOrder must never append to files")


if __name__ == "__main__":
    unittest.main()

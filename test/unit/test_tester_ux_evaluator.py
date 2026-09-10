from __future__ import annotations

import unittest

from core.tester.contracts.identifiers import (
    new_evidence_id,
    new_execution_id,
    new_step_id,
    new_test_case_id,
    new_ux_evaluation_id,
    validate_ux_evaluation_id,
)
from core.tester.contracts.ux import (
    UXAssertion,
    UXAssertionResult,
    UXEvaluationResult,
    UXFlowExpectation,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.evaluator.ux_evaluator import (
    MAX_UX_FINDINGS_PER_FLOW,
    UXFlowEvaluator,
)
from core.tester.types import (
    DefectSeverity,
    DefectType,
    FindingCategory,
    UXCheckType,
    UXEvaluationStatus,
)


class TestTesterUXEvaluator(unittest.TestCase):
    """
    Unit test suite for Phase 6.6: UX Flow Evaluation.
    Covers all 12 required test scenarios:
    1. successful user flow
    2. blocked flow
    3. inaccessible control
    4. missing feedback
    5. unrecoverable error
    6. onboarding failure
    7. useful UX finding
    8. subjective preference (discarded)
    9. vague recommendation (discarded)
    10. actionable recommendation (finding)
    11. no infinite UX findings (budget cap)
    12. evidence provenance, zero fixing guard, serialization
    """

    def setUp(self) -> None:
        self.execution_id = new_execution_id()
        self.test_case_id = new_test_case_id()
        self.step_id = new_step_id()
        self.evidence_id = new_evidence_id()
        self.evaluator = UXFlowEvaluator()

    def test_01_successful_user_flow(self) -> None:
        """Scenario 1: Happy-path user flow where all expectations and actions succeed."""
        expectations = [
            UXFlowExpectation(
                step_name="navigate_to_checkout",
                expected_outcome="Checkout page loaded with cart items",
                actual_outcome="Checkout page loaded with cart items",
                is_completed=True,
            ),
            UXFlowExpectation(
                step_name="submit_payment",
                expected_outcome="Order confirmation with receipt displayed",
                actual_outcome="Order confirmation with receipt displayed",
                is_completed=True,
            ),
        ]
        assertion = UXAssertion(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            step_id=self.step_id,
            flow_name="checkout_flow",
            check_type=UXCheckType.REQUIRED_ACTION_BLOCKED,
            description="Verify user can complete checkout flow",
            expectations=expectations,
            evidence_ids=[self.evidence_id],
        )

        result = self.evaluator.evaluate(assertion)
        self.assertEqual(result.overall_status, UXEvaluationStatus.PASS)
        self.assertEqual(len(result.defects), 0)
        self.assertEqual(len(result.findings), 0)
        self.assertEqual(result.discarded_count, 0)

    def test_02_blocked_flow(self) -> None:
        """Scenario 2: User flow blocked due to inability to proceed to next required step."""
        expectations = [
            UXFlowExpectation(
                step_name="enter_shipping_address",
                expected_outcome="Address validated and Next button active",
                actual_outcome="Next button remains inactive despite valid input",
                is_completed=False,
                blocker_reason="Next button validation script did not fire",
            ),
        ]
        assertion = UXAssertion(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            step_id=self.step_id,
            flow_name="checkout_shipping",
            check_type=UXCheckType.REQUIRED_ACTION_BLOCKED,
            description="Verify user can proceed from address to shipping options",
            expectations=expectations,
            evidence_ids=[self.evidence_id],
        )

        result = self.evaluator.evaluate(assertion)
        self.assertEqual(result.overall_status, UXEvaluationStatus.FAIL)
        self.assertEqual(len(result.defects), 1)
        defect = result.defects[0]
        self.assertEqual(defect.defect_type, DefectType.UX)
        self.assertIn("Blocked UX flow", defect.description)
        self.assertIn(self.evidence_id, defect.evidence_ids)

    def test_03_inaccessible_control(self) -> None:
        """Scenario 3: Control required to complete action is disabled or hidden."""
        assertion = UXAssertion(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            step_id=self.step_id,
            flow_name="settings_update",
            check_type=UXCheckType.INACCESSIBLE_CONTROL,
            description="Save button disabled or hidden for valid user input",
            expectations=[
                UXFlowExpectation(
                    step_name="save_profile",
                    expected_outcome="Save button is visible and clickable",
                    actual_outcome="Save button is opacity: 0 and pointer-events: none",
                    is_completed=False,
                    blocker_reason="Element obscured or permanently disabled",
                )
            ],
            evidence_ids=[self.evidence_id],
        )

        result = self.evaluator.evaluate(assertion)
        self.assertEqual(result.overall_status, UXEvaluationStatus.FAIL)
        self.assertEqual(len(result.defects), 1)
        self.assertEqual(result.defects[0].defect_type, DefectType.UX)
        self.assertIn("Inaccessible UI control", result.defects[0].description)

    def test_04_missing_feedback(self) -> None:
        """Scenario 4: Action performed but no feedback / spinner / confirmation rendered."""
        assertion = UXAssertion(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            step_id=self.step_id,
            flow_name="file_upload",
            check_type=UXCheckType.MISSING_FEEDBACK,
            description="User initiates large file upload with zero progress indication",
            expectations=[
                UXFlowExpectation(
                    step_name="upload_document",
                    expected_outcome="Progress bar or spinner indicates upload in flight",
                    actual_outcome="Screen is static with no visual cue that upload is occurring",
                    is_completed=False,
                )
            ],
            evidence_ids=[self.evidence_id],
        )

        result = self.evaluator.evaluate(assertion)
        self.assertEqual(result.overall_status, UXEvaluationStatus.FAIL)
        self.assertEqual(len(result.defects), 1)
        self.assertEqual(result.defects[0].defect_type, DefectType.UX)
        self.assertIn("Missing user feedback", result.defects[0].description)

    def test_05_unrecoverable_error(self) -> None:
        """Scenario 5: UI enters error state without retry, cancel, or recovery mechanism."""
        assertion = UXAssertion(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            step_id=self.step_id,
            flow_name="payment_processing",
            check_type=UXCheckType.UNRECOVERABLE_ERROR,
            description="Network timeout renders blank error screen with no back button",
            expectations=[
                UXFlowExpectation(
                    step_name="payment_timeout",
                    expected_outcome="Error message with 'Retry' or 'Go Back' link",
                    actual_outcome="Blank white screen with raw exception trace and no navigation",
                    is_completed=False,
                    blocker_reason="Dead end state requiring hard browser refresh",
                )
            ],
            evidence_ids=[self.evidence_id],
        )

        result = self.evaluator.evaluate(assertion)
        self.assertEqual(result.overall_status, UXEvaluationStatus.FAIL)
        self.assertEqual(len(result.defects), 1)
        self.assertEqual(result.defects[0].severity, DefectSeverity.HIGH)

    def test_06_onboarding_failure(self) -> None:
        """Scenario 6: First-time user onboarding modal cannot be dismissed or navigated."""
        assertion = UXAssertion(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            step_id=self.step_id,
            flow_name="welcome_modal",
            check_type=UXCheckType.ONBOARDING_FAILURE,
            description="Onboarding carousel close button non-functional",
            expectations=[
                UXFlowExpectation(
                    step_name="dismiss_onboarding",
                    expected_outcome="Close modal and view dashboard",
                    actual_outcome="Close button click does not dismiss overlay",
                    is_completed=False,
                    blocker_reason="Modal overlay traps user permanently",
                )
            ],
            evidence_ids=[self.evidence_id],
        )

        result = self.evaluator.evaluate(assertion)
        self.assertEqual(result.overall_status, UXEvaluationStatus.FAIL)
        self.assertEqual(len(result.defects), 1)
        self.assertEqual(result.defects[0].defect_type, DefectType.UX)

    def test_07_useful_ux_finding(self) -> None:
        """Scenario 7: Useful actionable UX finding without functional requirement failure."""
        assertion = UXAssertion(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            step_id=self.step_id,
            flow_name="multi_step_wizard",
            check_type=UXCheckType.CONFUSING_NAVIGATION,
            description="Step 2 requires clicking identical next buttons twice",
            expectations=[
                UXFlowExpectation(
                    step_name="wizard_navigation",
                    expected_outcome="Single navigation step to proceed",
                    actual_outcome="User must click 'Proceed' then 'Continue' on same form",
                    is_completed=True,
                )
            ],
            actionable_suggestion="Combine redundant confirmation into primary submission action",
            evidence_ids=[self.evidence_id],
        )

        result = self.evaluator.evaluate(assertion)
        self.assertEqual(result.overall_status, UXEvaluationStatus.PASS)
        self.assertEqual(len(result.defects), 0)
        self.assertEqual(len(result.findings), 1)
        finding = result.findings[0]
        self.assertEqual(finding.category, FindingCategory.RECOMMENDATION)
        self.assertIn("Combine redundant confirmation", finding.description)

    def test_08_subjective_preference_discarded(self) -> None:
        """Scenario 8: Subjective aesthetic preference is discarded as noise."""
        assertion = UXAssertion(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            step_id=self.step_id,
            flow_name="dashboard_styling",
            check_type=UXCheckType.SUBJECTIVE_PREFERENCE,
            description="I dislike the rounded corners and think teal would look much better",
            is_subjective_preference=True,
            expectations=[
                UXFlowExpectation(
                    step_name="view_dashboard",
                    expected_outcome="User likes colors",
                    actual_outcome="Buttons are blue with 8px radius",
                    is_completed=True,
                )
            ],
            evidence_ids=[self.evidence_id],
        )

        result = self.evaluator.evaluate(assertion)
        self.assertEqual(result.overall_status, UXEvaluationStatus.DISCARDED)
        self.assertEqual(len(result.defects), 0)
        self.assertEqual(len(result.findings), 0)
        self.assertEqual(result.discarded_count, 1)

    def test_09_vague_recommendation_discarded(self) -> None:
        """Scenario 9: Vague non-actionable critique is discarded as noise."""
        assertion = UXAssertion(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            step_id=self.step_id,
            flow_name="general_experience",
            check_type=UXCheckType.VAGUE_CRITIQUE,
            description="Make the overall user flow more modern and delightful",
            is_vague_critique=True,
            expectations=[
                UXFlowExpectation(
                    step_name="browse",
                    expected_outcome="Delightful app",
                    actual_outcome="Standard interface",
                    is_completed=True,
                )
            ],
            evidence_ids=[self.evidence_id],
        )

        result = self.evaluator.evaluate(assertion)
        self.assertEqual(result.overall_status, UXEvaluationStatus.DISCARDED)
        self.assertEqual(len(result.defects), 0)
        self.assertEqual(len(result.findings), 0)
        self.assertEqual(result.discarded_count, 1)

    def test_10_actionable_recommendation(self) -> None:
        """Scenario 10: Actionable recommendation without requirement failure produces a finding."""
        assertion = UXAssertion(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            step_id=self.step_id,
            flow_name="search_filter",
            check_type=UXCheckType.MISSING_NEXT_ACTION,
            description="Search results show zero results but no 'Clear filters' button",
            expectations=[
                UXFlowExpectation(
                    step_name="filter_products",
                    expected_outcome="Show results or provide clear filters shortcut",
                    actual_outcome="0 results displayed; user must manually reset 4 dropdowns",
                    is_completed=True,
                )
            ],
            actionable_suggestion="Add a 'Reset all filters' button when zero search results are returned",
            evidence_ids=[self.evidence_id],
        )

        result = self.evaluator.evaluate(assertion)
        self.assertEqual(result.overall_status, UXEvaluationStatus.PASS)
        self.assertEqual(len(result.defects), 0)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0].category, FindingCategory.RECOMMENDATION)
        self.assertIn("Reset all filters", result.findings[0].description)

    def test_11_no_infinite_ux_findings_budget_cap(self) -> None:
        """Scenario 11: Finite findings budget ensures evaluator cannot emit infinite suggestions."""
        expectations = []
        for i in range(10):
            expectations.append(
                UXFlowExpectation(
                    step_name=f"step_{i}",
                    expected_outcome=f"Expected outcome {i}",
                    actual_outcome=f"Actual outcome {i}",
                    is_completed=True,
                )
            )

        assertions = [
            UXAssertion(
                execution_id=self.execution_id,
                test_case_id=self.test_case_id,
                step_id=self.step_id,
                flow_name=f"flow_{i}",
                check_type=UXCheckType.MISSING_NEXT_ACTION,
                description=f"Actionable improvement {i}",
                actionable_suggestion=f"Provide quick shortcut for action {i}",
                expectations=[expectations[i]],
                evidence_ids=[self.evidence_id],
            )
            for i in range(10)
        ]

        results = self.evaluator.evaluate_batch(assertions)
        total_findings = sum(len(r.findings) for r in results)
        self.assertLessEqual(total_findings, MAX_UX_FINDINGS_PER_FLOW)
        self.assertEqual(MAX_UX_FINDINGS_PER_FLOW, 5)

    def test_12_evidence_provenance_zero_fixing_and_serialization(self) -> None:
        """Scenario 12: Evidence lineage, strict zero-fixing guard, and serialization roundtrip."""
        # 1. Zero fixing guard
        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.apply_fix()

        with self.assertRaises(TesterBoundaryViolationError):
            self.evaluator.auto_fix()

        # 2. Evidence provenance in defect
        assertion = UXAssertion(
            execution_id=self.execution_id,
            test_case_id=self.test_case_id,
            step_id=self.step_id,
            flow_name="profile_edit",
            check_type=UXCheckType.REQUIRED_ACTION_BLOCKED,
            description="Blocker test",
            expectations=[
                UXFlowExpectation(
                    step_name="save",
                    expected_outcome="Save succeeds",
                    actual_outcome="Save fails with unhandled JS error",
                    is_completed=False,
                )
            ],
            evidence_ids=[self.evidence_id],
        )
        result = self.evaluator.evaluate(assertion)
        self.assertEqual(len(result.defects), 1)
        defect = result.defects[0]
        self.assertIn(self.evidence_id, defect.evidence_ids)

        # 3. Serialization roundtrip
        result_dict = result.to_dict()
        restored = UXEvaluationResult.from_dict(result_dict)
        self.assertEqual(restored.evaluation_id, result.evaluation_id)
        self.assertEqual(restored.execution_id, result.execution_id)
        self.assertEqual(restored.overall_status, result.overall_status)
        self.assertEqual(len(restored.assertion_results), len(result.assertion_results))
        self.assertEqual(len(restored.defects), len(result.defects))

        # 4. Result zero fixing guard
        with self.assertRaises(TesterBoundaryViolationError):
            result.apply_fix()

        # 5. Identifier validation
        validate_ux_evaluation_id(result.evaluation_id)


if __name__ == "__main__":
    unittest.main()

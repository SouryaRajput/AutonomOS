from __future__ import annotations

import unittest
from typing import Any, Optional

from core.events.model import Event
from core.events.types import EventType
from core.tester import (
    AcceptanceCriterion,
    AcceptanceCriterionResult,
    AcceptanceCriterionStatus,
    ApplicableTestCategory,
    ClassificationResult,
    DefectClassifier,
    DefectSeverity,
    DefectType,
    FindingCategory,
    RuntimeEventSeverity,
    RuntimeEventType,
    RuntimeObservation,
    TestCase,
    TestCaseResult,
    TestCaseStatus,
    TestPlan,
    TestScope,
    TestStep,
    TestStepResult,
    TesterBoundaryViolationError,
    TesterDefect,
    TesterExecution,
    TesterExecutionStatus,
    TesterFinding,
    TesterLineageError,
    TesterValidationError,
    TesterWorkOrder,
    TestingCapability,
    new_defect_id,
    new_evidence_id,
    new_execution_id,
    new_plan_id,
    new_runtime_event_id,
    new_step_id,
    new_test_case_id,
    new_work_order_id,
)


class TestTesterDefectClassifier(unittest.TestCase):
    """
    Unit test suite for Phase 5.5: Defect & Finding Classification.

    Verifies the 13 required scenarios:
    1. Clear functional defect: Failed test case with reproduction steps & evidence -> DefectType.FUNCTIONAL
    2. Runtime defect: Application crash / fatal process failure -> DefectType.RUNTIME, CRITICAL
    3. 404 required resource: Missing required asset 404 -> DefectType.RESOURCE, MEDIUM
    4. Irrelevant 404: Optional asset 404 or external third-party tracker 404 -> NO defect
    5. Failed API: 500 error on application endpoint -> DefectType.API, HIGH
    6. Insufficient evidence: Failure without evidence -> NO defect, uncertainty finding
    7. Subjective observation: Vague opinion ("spacing could be improved") -> NO defect
    8. Warning-only observation: Console/runtime warning -> NO defect, observation finding
    9. Severity classification: Differentiated severity across crash, API, resource, text (not all CRITICAL)
    10. Evidence linkage: Real evidence IDs linked without phantom fabrication
    11. Duplicate defect handling: Multiple failures from same root cause deduplicated with combined evidence
    12. Provenance: Preserved execution_id, work_order_id, trace, acceptance_criterion_id
    13. No automatic test creation / fixing: Boundary guards raise TesterBoundaryViolationError
    """

    def setUp(self) -> None:
        self.project_id = "proj-classifier-unit"
        self.task_id = "task-classifier-01"
        self.correlation_id = "corr-classifier-01"
        self.work_order_id = new_work_order_id()

        self.work_order = TesterWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Classify evaluation failures into structured TesterDefects.",
            test_scope=TestScope(components=["Checkout", "API"], routes=["/checkout", "/api/v1"]),
            authorized_capabilities=[
                TestingCapability.TEST_EXECUTION,
                TestingCapability.BEHAVIOR_OBSERVATION,
            ],
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-pay-01",
                    description="User can submit payment successfully.",
                ),
            ],
        )

        self.execution = TesterExecution.from_work_order(self.work_order, status=TesterExecutionStatus.RUNNING)
        self.execution_id = self.execution.execution_id

        self.classifier = DefectClassifier(
            execution=self.execution,
            work_order=self.work_order,
        )

    def _make_frozen_plan(self, test_cases: list[TestCase]) -> TestPlan:
        plan = TestPlan(
            plan_id=new_plan_id(),
            work_order_id=self.work_order.work_order_id,
            execution_id=self.execution.execution_id,
            project_id=self.work_order.project_id,
            test_cases=test_cases,
        )
        plan.freeze()
        self.execution.test_plan = plan
        return plan

    def _make_step_result(
        self,
        step_number: int = 1,
        description: str = "Submit payment form",
        status: TestCaseStatus = TestCaseStatus.FAIL,
        evidence_ids: Optional[list[str]] = None,
        error: str = "Button unresponsive: Timeout 5000ms",
    ) -> TestStepResult:
        e_ids = evidence_ids if evidence_ids is not None else [new_evidence_id()]
        return TestStepResult(
            step_number=step_number,
            description=description,
            step_id=new_step_id(),
            action="CLICK",
            target="#pay-btn",
            status=status,
            expected="Payment confirmation modal",
            actual="Error: " + error,
            error=error,
            evidence_ids=e_ids,
        )

    def _make_test_case_result(
        self,
        test_case_id: str,
        name: str = "Payment Checkout Test",
        status: TestCaseStatus = TestCaseStatus.FAIL,
        step_results: Optional[list[TestStepResult]] = None,
        evidence_ids: Optional[list[str]] = None,
        observed_behavior: str = "Clicking pay button did not submit the order.",
        expected_behavior: str = "Order submitted and confirmed.",
        failure_info: Optional[dict[str, Any]] = None,
    ) -> TestCaseResult:
        steps = step_results if step_results is not None else [self._make_step_result()]
        e_ids = evidence_ids if evidence_ids is not None else [eid for s in steps for eid in s.evidence_ids]
        return TestCaseResult(
            test_id=test_case_id,
            name=name,
            execution_id=self.execution.execution_id,
            status=status,
            step_results=steps,
            evidence_ids=e_ids,
            expected_behavior=expected_behavior,
            observed_behavior=observed_behavior,
            failure_information=failure_info or {"error": "Payment submission timed out"},
        )

    def test_01_clear_functional_defect(self) -> None:
        """Scenario 1: Clear functional defect produces a structured TesterDefect."""
        tc_id = new_test_case_id()
        tc = TestCase(
            test_case_id=tc_id,
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Submit payment and receive confirmation",
            steps=[],
            expected_outcome="Confirmation screen displayed",
            acceptance_linkage=["ac-pay-01"],
        )
        plan = self._make_frozen_plan([tc])

        evi_id = new_evidence_id()
        step = self._make_step_result(step_number=1, evidence_ids=[evi_id], error="Payment failed with 422")
        tc_res = self._make_test_case_result(
            test_case_id=tc_id,
            step_results=[step],
            evidence_ids=[evi_id],
            observed_behavior="Payment form failed with HTTP 422 Unprocessable Entity",
        )

        res = self.classifier.classify(
            test_plan=plan,
            test_case_results=[tc_res],
        )

        self.assertEqual(len(res.defects), 1)
        defect = res.defects[0]
        self.assertEqual(defect.defect_type, DefectType.FUNCTIONAL)
        self.assertEqual(defect.type, DefectType.FUNCTIONAL)
        self.assertEqual(defect.test_case_id, tc_id)
        self.assertEqual(defect.evidence_ids, [evi_id])
        self.assertEqual(defect.acceptance_criterion_id, "ac-pay-01")
        self.assertTrue(len(defect.reproduction_steps) > 0)
        self.assertIn("Step 1", defect.reproduction_steps[0])
        self.assertEqual(defect.severity, DefectSeverity.HIGH)  # Tied to acceptance criterion

    def test_02_runtime_defect(self) -> None:
        """Scenario 2: Application crash evaluated as RUNTIME / CRASH defect with CRITICAL severity."""
        evi_id = new_evidence_id()
        crash_obs = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution.execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.APPLICATION_CRASH,
            severity=RuntimeEventSeverity.CRITICAL,
            message="Node.js process crashed with SIGSEGV (exit code 139)",
            is_application_owned=True,
            evidence_ids=[evi_id],
        )

        res = self.classifier.classify(runtime_observations=[crash_obs])

        self.assertEqual(len(res.defects), 1)
        defect = res.defects[0]
        self.assertEqual(defect.defect_type, DefectType.RUNTIME)
        self.assertEqual(defect.severity, DefectSeverity.CRITICAL)
        self.assertIn("SIGSEGV", defect.observed_behavior)
        self.assertEqual(defect.evidence_ids, [evi_id])

    def test_03_404_required_resource(self) -> None:
        """Scenario 3: 404 for required resource evaluated as RESOURCE defect with MEDIUM severity."""
        evi_id = new_evidence_id()
        resource_404 = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution.execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.HTTP_RESPONSE,
            severity=RuntimeEventSeverity.ERROR,
            message="GET /assets/checkout-icon.png returned 404 Not Found",
            status_code=404,
            url="https://example.com/assets/checkout-icon.png",
            is_required_resource=True,
            is_application_owned=True,
            evidence_ids=[evi_id],
        )

        res = self.classifier.classify(runtime_observations=[resource_404])

        self.assertEqual(len(res.defects), 1)
        defect = res.defects[0]
        self.assertEqual(defect.defect_type, DefectType.RESOURCE)
        self.assertEqual(defect.severity, DefectSeverity.MEDIUM)
        self.assertIn("checkout-icon.png", defect.title)
        self.assertEqual(defect.evidence_ids, [evi_id])

    def test_04_irrelevant_404(self) -> None:
        """Scenario 4: Irrelevant 404s (optional favicon or third-party tracking) do NOT become defects."""
        # A: Optional resource
        opt_404 = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution.execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.HTTP_RESPONSE,
            severity=RuntimeEventSeverity.WARNING,
            message="GET /favicon.ico returned 404",
            status_code=404,
            url="https://example.com/favicon.ico",
            is_required_resource=False,
            is_application_owned=True,
            evidence_ids=[new_evidence_id()],
        )
        # B: Third-party non-application owned request
        external_404 = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution.execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.HTTP_RESPONSE,
            severity=RuntimeEventSeverity.ERROR,
            message="GET https://analytics.thirdparty.com/collect returned 404",
            status_code=404,
            url="https://analytics.thirdparty.com/collect",
            is_application_owned=False,
            evidence_ids=[new_evidence_id()],
        )

        res = self.classifier.classify(runtime_observations=[opt_404, external_404])

        # Zero product defects!
        self.assertEqual(len(res.defects), 0)
        self.assertEqual(res.total_defects, 0)
        # Optional resource recorded as observation finding
        self.assertTrue(len(res.findings) >= 1)
        self.assertEqual(res.findings[0].category, FindingCategory.OBSERVATION)

    def test_05_failed_api(self) -> None:
        """Scenario 5: 500 error on required API endpoint evaluated as API defect with HIGH severity."""
        evi_id = new_evidence_id()
        api_500 = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution.execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.HTTP_RESPONSE,
            severity=RuntimeEventSeverity.CRITICAL,
            message="Internal Server Error: POST /api/v1/checkout returned 500",
            status_code=500,
            method="POST",
            url="https://example.com/api/v1/checkout",
            is_application_owned=True,
            is_required_resource=True,
            evidence_ids=[evi_id],
        )

        res = self.classifier.classify(runtime_observations=[api_500])

        self.assertEqual(len(res.defects), 1)
        defect = res.defects[0]
        self.assertEqual(defect.defect_type, DefectType.API)
        self.assertEqual(defect.severity, DefectSeverity.HIGH)
        self.assertIn("500", defect.title)
        self.assertEqual(defect.evidence_ids, [evi_id])

    def test_06_insufficient_evidence(self) -> None:
        """Scenario 6: Test case failure without evidence does NOT become a defect (yields UNCERTAINTY finding)."""
        tc_id = new_test_case_id()
        tc_res = self._make_test_case_result(
            test_case_id=tc_id,
            step_results=[self._make_step_result(evidence_ids=[])],
            evidence_ids=[],  # Empty evidence collection!
            observed_behavior="Unconfirmed anomaly without screenshot or log.",
        )

        res = self.classifier.classify(test_case_results=[tc_res])

        # Must NOT create a TesterDefect
        self.assertEqual(len(res.defects), 0)
        # Created an uncertainty finding
        self.assertEqual(len(res.findings), 1)
        self.assertEqual(res.findings[0].category, FindingCategory.UNCERTAINTY)
        self.assertTrue(res.findings[0].is_uncertain)

    def test_07_subjective_observation(self) -> None:
        """Scenario 7: Subjective aesthetic statements do NOT become defects."""
        tc_id = new_test_case_id()
        tc_res = self._make_test_case_result(
            test_case_id=tc_id,
            observed_behavior="The spacing could perhaps be improved and feels slightly clunky.",
            evidence_ids=[new_evidence_id()],
        )

        res = self.classifier.classify(test_case_results=[tc_res])

        # Anti-opinion rule: Zero defects created
        self.assertEqual(len(res.defects), 0)
        self.assertTrue(len(res.findings) >= 1)

    def test_08_warning_only_observation(self) -> None:
        """Scenario 8: Warning-only observation does NOT become a defect."""
        warning_obs = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution.execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.CONSOLE_WARNING,
            severity=RuntimeEventSeverity.WARNING,
            message="DeprecationWarning: Buffer() is deprecated due to security concerns.",
            is_application_owned=True,
            evidence_ids=[new_evidence_id()],
        )

        res = self.classifier.classify(runtime_observations=[warning_obs])

        self.assertEqual(len(res.defects), 0)
        self.assertEqual(len(res.findings), 1)
        self.assertEqual(res.findings[0].category, FindingCategory.OBSERVATION)

    def test_09_severity_classification(self) -> None:
        """Scenario 9: Evidence-based severity distribution (CRITICAL, HIGH, MEDIUM, LOW)."""
        # 1. Crash -> CRITICAL
        crash = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution.execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.APPLICATION_CRASH,
            severity=RuntimeEventSeverity.CRITICAL,
            message="Fatal crash: memory corruption",
            is_application_owned=True,
            evidence_ids=[new_evidence_id()],
        )
        # 2. 500 API -> HIGH
        api_err = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution.execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.HTTP_RESPONSE,
            severity=RuntimeEventSeverity.CRITICAL,
            message="POST /api/pay returned 500",
            status_code=500,
            is_application_owned=True,
            is_required_resource=True,
            evidence_ids=[new_evidence_id()],
        )
        # 3. Missing required asset -> MEDIUM
        asset_404 = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution.execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.HTTP_RESPONSE,
            severity=RuntimeEventSeverity.ERROR,
            message="GET /logo.png returned 404",
            status_code=404,
            is_required_resource=True,
            is_application_owned=True,
            evidence_ids=[new_evidence_id()],
        )
        # 4. Minor cosmetic failure -> LOW
        minor_tc = self._make_test_case_result(
            test_case_id=new_test_case_id(),
            name="Cosmetic text label test",
            failure_info={"error": "minor label discrepancy: expected 'Pay' got 'PAY'"},
            evidence_ids=[new_evidence_id()],
        )

        res = self.classifier.classify(
            runtime_observations=[crash, api_err, asset_404],
            test_case_results=[minor_tc],
        )

        self.assertEqual(len(res.defects), 4)
        severities = {d.severity for d in res.defects}
        self.assertIn(DefectSeverity.CRITICAL, severities)
        self.assertIn(DefectSeverity.HIGH, severities)
        self.assertIn(DefectSeverity.MEDIUM, severities)
        self.assertIn(DefectSeverity.LOW, severities)
        # Not all CRITICAL
        self.assertEqual(res.critical_defects, 1)
        self.assertEqual(res.high_defects, 1)
        self.assertEqual(res.medium_defects, 1)
        self.assertEqual(res.low_defects, 1)

    def test_10_evidence_linkage(self) -> None:
        """Scenario 10: Defect links authentic evidence IDs without phantom fabrication."""
        tc_id = new_test_case_id()
        e1 = new_evidence_id()
        e2 = new_evidence_id()
        tc_res = self._make_test_case_result(
            test_case_id=tc_id,
            evidence_ids=[e1, e2],
            observed_behavior="Database connection dropped during write.",
        )

        res = self.classifier.classify(test_case_results=[tc_res])

        self.assertEqual(len(res.defects), 1)
        defect = res.defects[0]
        self.assertEqual(defect.evidence_ids, [e1, e2])

    def test_11_duplicate_defect_handling(self) -> None:
        """Scenario 11: Multiple failures from same root cause consolidated into single defect."""
        e1 = new_evidence_id()
        e2 = new_evidence_id()

        # Test case fails because POST /api/checkout 500
        tc_res = self._make_test_case_result(
            test_case_id=new_test_case_id(),
            observed_behavior="API endpoint https://example.com/api/v1/checkout returned 500",
            failure_info={"error": "API endpoint https://example.com/api/v1/checkout returned 500"},
            evidence_ids=[e1],
        )

        # Runtime evaluator also captures the exact same 500 response
        runtime_err = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution.execution_id,
            project_id=self.project_id,
            event_type=RuntimeEventType.HTTP_RESPONSE,
            severity=RuntimeEventSeverity.CRITICAL,
            message="Internal Server Error: POST /api/v1/checkout returned 500",
            status_code=500,
            url="https://example.com/api/v1/checkout",
            is_application_owned=True,
            is_required_resource=True,
            evidence_ids=[e2],
        )

        res = self.classifier.classify(
            test_case_results=[tc_res],
            runtime_observations=[runtime_err],
        )

        # Should deduplicate into 1 single defect with combined evidence!
        self.assertEqual(len(res.defects), 1)
        defect = res.defects[0]
        self.assertEqual(set(defect.evidence_ids), {e1, e2})

    def test_12_provenance(self) -> None:
        """Scenario 12: Preserved execution_id, work_order_id, trace, acceptance_criterion_id."""
        tc_id = new_test_case_id()
        tc = TestCase(
            test_case_id=tc_id,
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Submit payment",
            steps=[],
            expected_outcome="Success",
            acceptance_linkage=["ac-pay-01"],
        )
        plan = self._make_frozen_plan([tc])
        tc_res = self._make_test_case_result(
            test_case_id=tc_id,
            evidence_ids=[new_evidence_id()],
        )

        res = self.classifier.classify(test_plan=plan, test_case_results=[tc_res])

        defect = res.defects[0]
        self.assertEqual(defect.execution_id, self.execution.execution_id)
        self.assertEqual(defect.work_order_id, self.work_order_id)
        self.assertEqual(defect.acceptance_criterion_id, "ac-pay-01")
        self.assertEqual(defect.provenance["project_id"], self.project_id)
        self.assertTrue(defect.trace["trace_id"].startswith("ttrace-"))

    def test_13_no_automatic_test_creation_or_fixing(self) -> None:
        """Scenario 13: Zero-fixing and zero-dynamic-test boundary guards raise TesterBoundaryViolationError."""
        with self.assertRaises(TesterBoundaryViolationError) as ctx1:
            self.classifier.apply_fix()
        self.assertIn("AUTOMATIC_FIX", str(ctx1.exception))

        with self.assertRaises(TesterBoundaryViolationError) as ctx2:
            self.classifier.auto_fix()
        self.assertIn("AUTOMATIC_FIX", str(ctx2.exception))

        with self.assertRaises(TesterBoundaryViolationError) as ctx3:
            self.classifier.create_tests()
        self.assertIn("GENERATE_TESTS", str(ctx3.exception))

        with self.assertRaises(TesterBoundaryViolationError) as ctx4:
            self.classifier.generate_tests()
        self.assertIn("GENERATE_TESTS", str(ctx4.exception))

        # Defect instance itself also raises boundary violation if repair is called
        defect = TesterDefect(
            defect_id=new_defect_id(),
            work_order_id=self.work_order_id,
            title="Broken button",
            description="Button does not submit",
            observed_behavior="Unresponsive",
        )
        with self.assertRaises(TesterBoundaryViolationError) as ctx5:
            defect.apply_fix()
        self.assertIn("AUTOMATIC_FIX", str(ctx5.exception))


if __name__ == "__main__":
    unittest.main()

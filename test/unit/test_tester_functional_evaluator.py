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
    FunctionalAcceptanceEvaluator,
    RuntimeEventSeverity,
    RuntimeEventType,
    RuntimeObservation,
    TestCase,
    TestCaseResult,
    TestCaseStatus,
    TestCategory,
    TestPlan,
    TestScope,
    TestStep,
    TestStepResult,
    TesterBoundaryViolationError,
    TesterExecution,
    TesterExecutionStatus,
    TesterLineageError,
    TesterValidationError,
    TesterWorkOrder,
    TestingCapability,
    new_evidence_id,
    new_execution_id,
    new_plan_id,
    new_result_id,
    new_runtime_event_id,
    new_step_id,
    new_test_case_id,
    new_work_order_id,
)


class TestTesterFunctionalAcceptanceEvaluator(unittest.TestCase):
    """
    Unit test suite for Phase 5.4: Functional Acceptance Evaluation.

    Verifies:
    1. Passing criterion: Supporting test passes with verified evidence -> PASS
    2. Failing criterion: Supporting test fails assertion -> FAIL
    3. Insufficient evidence: Supporting test passes but evidence list empty -> NOT_VERIFIED
    4. Blocked criterion: Supporting test blocked by auth/network -> BLOCKED
    5. Not applicable criterion: Criterion category in plan's not-applicable list -> NOT_APPLICABLE
    6. Multiple tests supporting one criterion: All must pass; aggregated evidence
    7. Runtime error causing functional failure: 500 error on required API -> FAIL
    8. False pass prevention: Action succeeds but state_verified is False -> NOT_VERIFIED
    9. Evidence linkage: Collected evidence IDs linked without inventing fake evidence
    10. Provenance: Lineage to execution, work order, project, and trace preserved
    11. Deterministic evaluation: Identical inputs yield identical outcomes
    12. Subjective criterion filtering: Aesthetic claims ("looks premium") -> NOT_APPLICABLE
    13. No fixing boundary: apply_fix() and auto_fix() raise TesterBoundaryViolationError
    14. Unfrozen plan rejected: TesterBoundaryViolationError raised on unfrozen plan
    15. Lineage and project isolation: TesterLineageError on mismatch
    16. Serialization round trip: AcceptanceCriterionResult to_dict / from_dict
    17. Domain events emitted: TEST_CRITERION_EVALUATED and TEST_ACCEPTANCE_EVALUATED
    """

    def setUp(self) -> None:
        self.project_id = "proj-functional-unit"
        self.task_id = "task-functional-01"
        self.correlation_id = "corr-functional-01"
        self.work_order_id = new_work_order_id()
        self.execution_id = new_execution_id()

        self.work_order = TesterWorkOrder(
            work_order_id=self.work_order_id,
            manager_task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective="Evaluate functional acceptance of document save and login.",
            test_scope=TestScope(components=["App", "API"], routes=["/app", "/api/v1"]),
            authorized_capabilities=[
                TestingCapability.TEST_EXECUTION,
                TestingCapability.BEHAVIOR_OBSERVATION,
            ],
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-save-01",
                    description="The Save button saves the document and updates status.",
                ),
            ],
        )

        self.execution = TesterExecution.from_work_order(self.work_order, status=TesterExecutionStatus.RUNNING)
        self.execution_id = self.execution.execution_id

        self.evaluator = FunctionalAcceptanceEvaluator(
            execution=self.execution,
            work_order=self.work_order,
        )

    def _make_frozen_plan(self, test_cases: list[TestCase], **kwargs) -> TestPlan:
        defaults = {
            "plan_id": new_plan_id(),
            "work_order_id": self.work_order.work_order_id,
            "execution_id": self.execution.execution_id,
            "project_id": self.work_order.project_id,
            "test_cases": test_cases,
            "max_test_cases": 10,
            "time_budget": 300,
            "iteration_budget": 10,
        }
        defaults.update(kwargs)
        plan = TestPlan(**defaults)
        plan.freeze()
        self.execution.test_plan = plan
        return plan

    def _make_step_result(
        self,
        step_number: int = 1,
        description: str = "Click Save button",
        status: TestCaseStatus = TestCaseStatus.PASS,
        evidence_ids: Optional[list[str]] = None,
        state_verified: bool = True,
        actual: str = "Saved",
        error: Optional[str] = None,
    ) -> TestStepResult:
        e_ids = evidence_ids if evidence_ids is not None else [new_evidence_id()]
        return TestStepResult(
            step_number=step_number,
            description=description,
            step_id=new_step_id(),
            action="CLICK",
            target="#save-btn",
            status=status,
            expected="Document saved",
            actual=actual,
            evidence_ids=e_ids,
            error=error,
            metadata={"state_verified": state_verified},
        )

    def _make_test_case_result(
        self,
        test_case_id: str,
        name: str = "Save Test",
        status: TestCaseStatus = TestCaseStatus.PASS,
        step_results: Optional[list[TestStepResult]] = None,
        evidence_ids: Optional[list[str]] = None,
        observed_behavior: str = "Document was saved successfully.",
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
            observed_behavior=observed_behavior,
            failure_information=failure_info or ({
                "reason": "Test failed step assertion"
            } if status == TestCaseStatus.FAIL else None),
        )

    def test_01_passing_criterion(self) -> None:
        """Scenario 1: Supporting test passes with verified evidence -> PASS."""
        tc_id = new_test_case_id()
        tc = TestCase(
            test_case_id=tc_id,
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Click Save button and verify document state",
            steps=[],
            expected_outcome="Document is saved",
            acceptance_linkage=["ac-save-01"],
        )
        plan = self._make_frozen_plan([tc])

        step_res = self._make_step_result(evidence_ids=["evi-valid-01"], state_verified=True)
        tc_res = self._make_test_case_result(
            test_case_id=tc_id,
            status=TestCaseStatus.PASS,
            step_results=[step_res],
            evidence_ids=["evi-valid-01"],
        )

        results = self.evaluator.evaluate(test_plan=plan, test_case_results=[tc_res])

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.criterion_id, "ac-save-01")
        self.assertEqual(res.status, AcceptanceCriterionStatus.PASS)
        self.assertTrue(res.is_pass)
        self.assertFalse(res.is_fail)
        self.assertFalse(res.is_blocked)
        self.assertFalse(res.is_not_verified)
        self.assertEqual(res.supporting_test_cases, [tc_id])
        self.assertEqual(res.evidence_ids, ["evi-valid-01"])
        self.assertEqual(res.confidence, 1.0)

    def test_02_failing_criterion(self) -> None:
        """Scenario 2: Supporting test fails assertion/step -> FAIL."""
        tc_id = new_test_case_id()
        tc = TestCase(
            test_case_id=tc_id,
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Click Save button and verify document state",
            steps=[],
            expected_outcome="Document is saved",
            acceptance_linkage=["ac-save-01"],
        )
        plan = self._make_frozen_plan([tc])

        step_res = self._make_step_result(
            status=TestCaseStatus.FAIL,
            actual="Save failed: timeout",
            error="Save timeout after 5000ms",
            state_verified=False,
        )
        tc_res = self._make_test_case_result(
            test_case_id=tc_id,
            status=TestCaseStatus.FAIL,
            step_results=[step_res],
            observed_behavior="Save button clicked but document status remained 'Unsaved'.",
            failure_info={"error": "Save timeout after 5000ms"},
        )

        results = self.evaluator.evaluate(test_plan=plan, test_case_results=[tc_res])

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.criterion_id, "ac-save-01")
        self.assertEqual(res.status, AcceptanceCriterionStatus.FAIL)
        self.assertTrue(res.is_fail)
        self.assertFalse(res.is_pass)
        self.assertIn("Save timeout after 5000ms", res.reason)

    def test_03_insufficient_evidence(self) -> None:
        """Scenario 3: Supporting test passes but evidence list is empty -> NOT_VERIFIED."""
        tc_id = new_test_case_id()
        tc = TestCase(
            test_case_id=tc_id,
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Save document",
            steps=[],
            expected_outcome="Document is saved",
            acceptance_linkage=["ac-save-01"],
        )
        plan = self._make_frozen_plan([tc])

        # Step passed but has NO evidence IDs
        step_res = self._make_step_result(evidence_ids=[], state_verified=True)
        tc_res = self._make_test_case_result(
            test_case_id=tc_id,
            status=TestCaseStatus.PASS,
            step_results=[step_res],
            evidence_ids=[],
        )

        results = self.evaluator.evaluate(test_plan=plan, test_case_results=[tc_res])

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.status, AcceptanceCriterionStatus.NOT_VERIFIED)
        self.assertTrue(res.is_not_verified)
        self.assertFalse(res.is_pass)
        self.assertIn("sufficient evidence", res.reason)

    def test_04_blocked_criterion(self) -> None:
        """Scenario 4: Supporting test blocked by runtime/auth -> BLOCKED."""
        tc_id = new_test_case_id()
        tc = TestCase(
            test_case_id=tc_id,
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Save document",
            steps=[],
            expected_outcome="Document is saved",
            acceptance_linkage=["ac-save-01"],
        )
        plan = self._make_frozen_plan([tc])

        tc_res = self._make_test_case_result(
            test_case_id=tc_id,
            status=TestCaseStatus.BLOCKED,
            observed_behavior="Session was blocked due to missing auth credentials.",
            failure_info={"reason": "Authentication token expired before test could run."},
        )

        results = self.evaluator.evaluate(test_plan=plan, test_case_results=[tc_res])

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.status, AcceptanceCriterionStatus.BLOCKED)
        self.assertTrue(res.is_blocked)
        self.assertIn("Authentication token expired", res.reason)

    def test_05_not_applicable_criterion(self) -> None:
        """Scenario 5: Criterion category in plan's not-applicable list -> NOT_APPLICABLE."""
        crit = AcceptanceCriterion(
            criterion_id="ac-perf-01",
            description="Page load latency under 200ms.",
            category=TestCategory.PERFORMANCE,
        )
        self.work_order.acceptance_criteria.append(crit)

        plan = self._make_frozen_plan(
            test_cases=[],
            not_applicable_categories=[TestCategory.PERFORMANCE],
        )

        results = self.evaluator.evaluate(test_plan=plan, test_case_results=[])

        perf_res = next(r for r in results if r.criterion_id == "ac-perf-01")
        self.assertEqual(perf_res.status, AcceptanceCriterionStatus.NOT_APPLICABLE)
        self.assertTrue(perf_res.is_not_applicable)
        self.assertIn("NOT_APPLICABLE", perf_res.reason)

    def test_06_multiple_tests_supporting_one_criterion(self) -> None:
        """Scenario 6: Multiple tests aggregate results and evidence into a single criterion result."""
        tc1_id = new_test_case_id()
        tc2_id = new_test_case_id()
        tc1 = TestCase(
            test_case_id=tc1_id,
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Save document via keyboard shortcut",
            steps=[],
            expected_outcome="Document is saved",
            acceptance_linkage=["ac-save-01"],
        )
        tc2 = TestCase(
            test_case_id=tc2_id,
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Save document via File menu",
            steps=[],
            expected_outcome="Document is saved",
            acceptance_linkage=["ac-save-01"],
        )
        plan = self._make_frozen_plan([tc1, tc2])

        tc1_res = self._make_test_case_result(
            test_case_id=tc1_id,
            status=TestCaseStatus.PASS,
            evidence_ids=["evi-shortcut-01"],
        )
        tc2_res = self._make_test_case_result(
            test_case_id=tc2_id,
            status=TestCaseStatus.PASS,
            evidence_ids=["evi-menu-02"],
        )

        results = self.evaluator.evaluate(test_plan=plan, test_case_results=[tc1_res, tc2_res])

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.status, AcceptanceCriterionStatus.PASS)
        self.assertEqual(sorted(res.supporting_test_cases), sorted([tc1_id, tc2_id]))
        self.assertEqual(sorted(res.evidence_ids), sorted(["evi-shortcut-01", "evi-menu-02"]))

    def test_07_runtime_error_causing_functional_failure(self) -> None:
        """Scenario 7: 500 error on required API causes criterion FAIL."""
        tc_id = new_test_case_id()
        tc = TestCase(
            test_case_id=tc_id,
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Save document via API",
            steps=[],
            expected_outcome="Document is saved",
            acceptance_linkage=["ac-save-01"],
        )
        plan = self._make_frozen_plan([tc])

        tc_res = self._make_test_case_result(
            test_case_id=tc_id,
            status=TestCaseStatus.PASS,
            evidence_ids=["evi-req-01"],
        )

        # Runtime observation shows a critical 500 server error occurred
        err_eid = new_evidence_id()
        runtime_err = RuntimeObservation(
            event_id=new_runtime_event_id(),
            execution_id=self.execution.execution_id,
            project_id=self.project_id,
            test_case_id=tc_id,
            event_type=RuntimeEventType.HTTP_RESPONSE,
            severity=RuntimeEventSeverity.CRITICAL,
            message="Internal Server Error: POST /api/v1/save returned 500",
            status_code=500,
            url="https://example.com/api/v1/save",
            is_application_owned=True,
            is_required_resource=True,
            evidence_ids=[err_eid],
        )

        results = self.evaluator.evaluate(
            test_plan=plan,
            test_case_results=[tc_res],
            runtime_observations=[runtime_err],
        )

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.status, AcceptanceCriterionStatus.FAIL)
        self.assertTrue(res.is_fail)
        self.assertIn("Runtime failure observed", res.observed_behavior)
        self.assertIn(err_eid, res.evidence_ids)

    def test_08_false_pass_prevention(self) -> None:
        """Scenario 8: Action succeeds but state_verified is False -> NOT_VERIFIED."""
        tc_id = new_test_case_id()
        tc = TestCase(
            test_case_id=tc_id,
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Click Save button",
            steps=[],
            expected_outcome="Document is saved",
            acceptance_linkage=["ac-save-01"],
        )
        plan = self._make_frozen_plan([tc])

        # Step succeeded in clicking the button, but resulting state could not be verified
        step_res = self._make_step_result(
            status=TestCaseStatus.PASS,
            evidence_ids=["evi-click-screenshot"],
            state_verified=False,
        )
        tc_res = self._make_test_case_result(
            test_case_id=tc_id,
            status=TestCaseStatus.PASS,
            step_results=[step_res],
            evidence_ids=["evi-click-screenshot"],
        )

        results = self.evaluator.evaluate(test_plan=plan, test_case_results=[tc_res])

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.status, AcceptanceCriterionStatus.NOT_VERIFIED)
        self.assertTrue(res.is_not_verified)
        self.assertIn("resulting application state cannot be verified", res.reason)

    def test_09_evidence_linkage(self) -> None:
        """Scenario 9: Collected evidence IDs linked to result without inventing fake evidence."""
        tc_id = new_test_case_id()
        tc = TestCase(
            test_case_id=tc_id,
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Save document",
            steps=[],
            expected_outcome="Document is saved",
            acceptance_linkage=["ac-save-01"],
        )
        plan = self._make_frozen_plan([tc])

        e_id1 = new_evidence_id()
        e_id2 = new_evidence_id()
        tc_res = self._make_test_case_result(
            test_case_id=tc_id,
            status=TestCaseStatus.PASS,
            evidence_ids=[e_id1, e_id2],
        )

        results = self.evaluator.evaluate(test_plan=plan, test_case_results=[tc_res])

        res = results[0]
        self.assertEqual(set(res.evidence_ids), {e_id1, e_id2})
        # Verify no phantom evidence IDs were generated
        self.assertEqual(len(res.evidence_ids), 2)

    def test_10_provenance(self) -> None:
        """Scenario 10: Lineage to execution, work order, project, and trace preserved."""
        tc_id = new_test_case_id()
        tc = TestCase(
            test_case_id=tc_id,
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Save document",
            steps=[],
            expected_outcome="Document is saved",
            acceptance_linkage=["ac-save-01"],
        )
        plan = self._make_frozen_plan([tc])

        tc_res = self._make_test_case_result(
            test_case_id=tc_id,
            status=TestCaseStatus.PASS,
            evidence_ids=[new_evidence_id()],
        )

        results = self.evaluator.evaluate(test_plan=plan, test_case_results=[tc_res])

        res = results[0]
        self.assertEqual(res.provenance["execution_id"], self.execution.execution_id)
        self.assertEqual(res.provenance["project_id"], self.project_id)
        self.assertEqual(res.provenance["work_order_id"], self.work_order_id)
        self.assertEqual(res.provenance["criterion_id"], "ac-save-01")
        self.assertIsNotNone(res.trace)
        self.assertTrue(res.trace["trace_id"].startswith("ttrace-"))

    def test_11_deterministic_evaluation(self) -> None:
        """Scenario 11: Identical inputs yield identical outcomes."""
        tc_id = new_test_case_id()
        tc = TestCase(
            test_case_id=tc_id,
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Save document",
            steps=[],
            expected_outcome="Document is saved",
            acceptance_linkage=["ac-save-01"],
        )
        plan = self._make_frozen_plan([tc])

        tc_res = self._make_test_case_result(
            test_case_id=tc_id,
            status=TestCaseStatus.PASS,
            evidence_ids=["evi-det-01"],
        )

        results1 = self.evaluator.evaluate(test_plan=plan, test_case_results=[tc_res])
        results2 = self.evaluator.evaluate(test_plan=plan, test_case_results=[tc_res])

        self.assertEqual(results1[0].status, results2[0].status)
        self.assertEqual(results1[0].reason, results2[0].reason)
        self.assertEqual(results1[0].confidence, results2[0].confidence)
        self.assertEqual(results1[0].evidence_ids, results2[0].evidence_ids)

    def test_12_subjective_criterion_filtering(self) -> None:
        """Scenario 12: Aesthetic claims ("looks premium") marked NOT_APPLICABLE."""
        crit = AcceptanceCriterion(
            criterion_id="ac-subjective-01",
            description="The page looks premium, modern, and has delightful UX.",
        )
        self.work_order.acceptance_criteria.append(crit)

        plan = self._make_frozen_plan([])
        results = self.evaluator.evaluate(test_plan=plan, test_case_results=[])

        subj_res = next(r for r in results if r.criterion_id == "ac-subjective-01")
        self.assertEqual(subj_res.status, AcceptanceCriterionStatus.NOT_APPLICABLE)
        self.assertTrue(subj_res.is_not_applicable)
        self.assertIn("Subjective visual/UX judgments cannot be evaluated", subj_res.reason)

    def test_13_no_fixing_boundary(self) -> None:
        """Scenario 13: Zero fixing guard: apply_fix() or auto_fix() raises TesterBoundaryViolationError."""
        with self.assertRaises(TesterBoundaryViolationError) as ctx1:
            self.evaluator.apply_fix()
        self.assertIn("AUTOMATIC_FIX", str(ctx1.exception))

        with self.assertRaises(TesterBoundaryViolationError) as ctx2:
            self.evaluator.auto_fix()
        self.assertIn("AUTOMATIC_FIX", str(ctx2.exception))

        # Result object also raises boundary violation if fix attempted
        res = AcceptanceCriterionResult(
            criterion_id="ac-01",
            description="Criterion description",
            status=AcceptanceCriterionStatus.FAIL,
        )
        with self.assertRaises(TesterBoundaryViolationError):
            res.apply_fix()

    def test_14_unfrozen_plan_rejected(self) -> None:
        """Scenario 14: Passing unfrozen TestPlan raises TesterBoundaryViolationError."""
        unfrozen_plan = TestPlan(
            plan_id=new_plan_id(),
            work_order_id=self.work_order.work_order_id,
            execution_id=self.execution.execution_id,
            project_id=self.work_order.project_id,
            test_cases=[],
        )
        # Not calling unfrozen_plan.freeze()
        with self.assertRaises(TesterBoundaryViolationError) as ctx:
            self.evaluator.evaluate(test_plan=unfrozen_plan)
        self.assertIn("is not FROZEN", str(ctx.exception))

    def test_15_lineage_and_project_isolation(self) -> None:
        """Scenario 15: Mismatches in project_id or work_order_id raise TesterLineageError."""
        foreign_plan = TestPlan(
            plan_id=new_plan_id(),
            work_order_id=self.work_order.work_order_id,
            execution_id=self.execution.execution_id,
            project_id="foreign-project-id",
            test_cases=[],
        )
        foreign_plan.freeze()

        with self.assertRaises(TesterLineageError):
            self.evaluator.evaluate(test_plan=foreign_plan)

    def test_16_serialization_round_trip(self) -> None:
        """Scenario 16: AcceptanceCriterionResult to_dict / from_dict round trip."""
        res = AcceptanceCriterionResult(
            criterion_id="ac-roundtrip-01",
            description="Verify data persistence.",
            status=AcceptanceCriterionStatus.PASS,
            supporting_test_cases=["tc-001", "tc-002"],
            evidence_ids=["evi-001"],
            observations=["obs-001"],
            reason="Verified with database audit.",
            confidence=0.95,
            provenance={"execution_id": self.execution.execution_id},
        )
        d = res.to_dict()
        restored = AcceptanceCriterionResult.from_dict(d)

        self.assertEqual(restored.criterion_id, res.criterion_id)
        self.assertEqual(restored.description, res.description)
        self.assertEqual(restored.status, res.status)
        self.assertEqual(restored.supporting_test_cases, res.supporting_test_cases)
        self.assertEqual(restored.evidence_ids, res.evidence_ids)
        self.assertEqual(restored.reason, res.reason)
        self.assertEqual(restored.confidence, res.confidence)
        self.assertEqual(restored.provenance, res.provenance)

    def test_17_domain_events_emitted(self) -> None:
        """Scenario 17: TEST_CRITERION_EVALUATED and TEST_ACCEPTANCE_EVALUATED events emitted."""
        events: list[Event] = []
        evaluator = FunctionalAcceptanceEvaluator(
            execution=self.execution,
            work_order=self.work_order,
            event_sink=events.append,
        )

        tc_id = new_test_case_id()
        tc = TestCase(
            test_case_id=tc_id,
            category=ApplicableTestCategory.FUNCTIONAL,
            objective="Save document",
            steps=[],
            expected_outcome="Document is saved",
            acceptance_linkage=["ac-save-01"],
        )
        plan = self._make_frozen_plan([tc])

        tc_res = self._make_test_case_result(
            test_case_id=tc_id,
            status=TestCaseStatus.PASS,
            evidence_ids=["evi-evt-01"],
        )

        results = evaluator.evaluate(test_plan=plan, test_case_results=[tc_res])

        self.assertEqual(len(results), 1)
        event_types = [e.event_type for e in events]
        self.assertIn(EventType.TEST_CRITERION_EVALUATED, event_types)
        self.assertIn(EventType.TEST_ACCEPTANCE_EVALUATED, event_types)


if __name__ == "__main__":
    unittest.main()

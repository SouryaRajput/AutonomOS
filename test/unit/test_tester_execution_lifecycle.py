from __future__ import annotations

import unittest

from core.tester.contracts.boundary import TESTER_ALLOWED_CAPABILITIES
from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.finding import (
    AcceptanceCriterionResult,
    TesterDefect,
    TesterEvidence,
    TesterFinding,
)
from core.tester.contracts.identifiers import (
    new_evidence_id,
    new_execution_id,
    new_test_case_id,
    new_work_order_id,
)
from core.tester.contracts.lifecycle import TesterLifecycle
from core.tester.contracts.scope import TestScope
from core.tester.contracts.test_case import TestCaseResult
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    InvalidTesterTransitionError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    AcceptanceCriterionStatus,
    DefectSeverity,
    DefectType,
    EvidenceType,
    FindingCategory,
    ShipRecommendation,
    TestCaseStatus,
    TestCategory,
    TesterActionType,
    TesterBlockerCategory,
    TesterBlockerSeverity,
    TesterExecutionPhase,
    TesterExecutionStatus,
    TesterResultStatus,
)


class TestTesterExecutionLifecycle(unittest.TestCase):
    """
    Deterministic unit tests validating the Tester execution lifecycle,
    state transition invariants, reporting boundaries, and iteration ownership.
    """

    def _create_sample_work_order(
        self,
        work_order_id: str = "two-exec-test-01",
        manager_task_id: str = "mtask-exec-01",
        project_id: str = "proj-exec-01",
        correlation_id: str = "corr-exec-01",
    ) -> TesterWorkOrder:
        return TesterWorkOrder(
            work_order_id=work_order_id,
            manager_task_id=manager_task_id,
            project_id=project_id,
            correlation_id=correlation_id,
            objective="Evaluate checkout flow resiliency",
            instructions=["Execute checkout with valid and invalid coupons"],
            product_artifact="billing_service_v2",
            test_scope=TestScope(routes=["/checkout", "/cart"], features=["coupon_validation"]),
            test_categories=[TestCategory.FUNCTIONAL, TestCategory.REGRESSION],
            authorized_capabilities=list(TESTER_ALLOWED_CAPABILITIES),
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-coupon-01",
                    description="Expired coupons are rejected with 400 Bad Request",
                )
            ],
        )

    # ----------------------------------------------------------------------
    # 1. Normal Lifecycle Path
    # ----------------------------------------------------------------------
    def test_normal_lifecycle_path(self):
        """
        Verify the canonical execution lifecycle:
        REQUESTED -> STARTING -> RUNNING -> EVALUATING -> REPORTING -> COMPLETED
        """
        wo = self._create_sample_work_order()
        execution = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.REQUESTED)

        # 1. Initial State: REQUESTED
        self.assertEqual(execution.status, TesterExecutionStatus.REQUESTED)
        self.assertEqual(execution.current_phase, TesterExecutionPhase.PREPARING)
        self.assertFalse(execution.is_terminal)
        self.assertFalse(execution.is_active)
        self.assertIsNone(execution.started_at)
        self.assertIsNone(execution.completed_at)

        # 2. Transition: REQUESTED -> STARTING
        execution.transition_to(TesterExecutionStatus.STARTING, reason="Initializing sandbox")
        self.assertEqual(execution.status, TesterExecutionStatus.STARTING)
        self.assertEqual(execution.current_phase, TesterExecutionPhase.PREPARING)
        self.assertTrue(execution.is_active)

        # 3. Transition: STARTING -> RUNNING
        execution.transition_to(TesterExecutionStatus.RUNNING, reason="Executing tests")
        self.assertEqual(execution.status, TesterExecutionStatus.RUNNING)
        self.assertEqual(execution.current_phase, TesterExecutionPhase.EXECUTING)
        self.assertIsNotNone(execution.started_at)

        # Record test execution in RUNNING
        tc = TestCaseResult(
            test_id=new_test_case_id(),
            name="test_expired_coupon",
            status=TestCaseStatus.PASS,
            description="Verify coupon rejection",
        )
        execution.record_test_case(tc)
        self.assertEqual(len(execution.test_cases), 1)

        # 4. Transition: RUNNING -> EVALUATING
        execution.transition_to(TesterExecutionStatus.EVALUATING, reason="Analyzing results")
        self.assertEqual(execution.status, TesterExecutionStatus.EVALUATING)
        self.assertEqual(execution.current_phase, TesterExecutionPhase.EVALUATING)

        # Record finding and acceptance result in EVALUATING
        execution.record_acceptance_result(
            criterion_id="ac-coupon-01",
            description="Expired coupons are rejected",
            status=AcceptanceCriterionStatus.PASS,
        )
        self.assertEqual(len(execution.acceptance_results), 1)

        # 5. Transition: EVALUATING -> REPORTING
        execution.transition_to(TesterExecutionStatus.REPORTING, reason="Compiling final report")
        self.assertEqual(execution.status, TesterExecutionStatus.REPORTING)
        self.assertEqual(execution.current_phase, TesterExecutionPhase.REPORTING)

        # 6. Transition: REPORTING -> COMPLETED
        execution.transition_to(TesterExecutionStatus.COMPLETED, reason="Report delivered to manager")
        self.assertEqual(execution.status, TesterExecutionStatus.COMPLETED)
        self.assertTrue(execution.is_terminal)
        self.assertFalse(execution.is_active)
        self.assertIsNotNone(execution.completed_at)

    # ----------------------------------------------------------------------
    # 2. Invalid State Transitions
    # ----------------------------------------------------------------------
    def test_invalid_state_transitions_rejected(self):
        """Verify that illegal transitions across states are strictly rejected with InvalidTesterTransitionError."""
        wo = self._create_sample_work_order()

        # From REQUESTED: cannot jump to COMPLETED, RUNNING, EVALUATING, REPORTING, BLOCKED
        exec_req = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.REQUESTED)
        for invalid_target in [
            TesterExecutionStatus.COMPLETED,
            TesterExecutionStatus.RUNNING,
            TesterExecutionStatus.EVALUATING,
            TesterExecutionStatus.REPORTING,
            TesterExecutionStatus.BLOCKED,
        ]:
            with self.subTest(source="REQUESTED", target=invalid_target):
                with self.assertRaises(InvalidTesterTransitionError) as ctx:
                    exec_req.transition_to(invalid_target)
                self.assertEqual(ctx.exception.current_status, "REQUESTED")
                self.assertEqual(ctx.exception.target_status, invalid_target.value)

        # From STARTING: cannot jump to COMPLETED, EVALUATING, REPORTING
        exec_start = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.STARTING)
        for invalid_target in [
            TesterExecutionStatus.COMPLETED,
            TesterExecutionStatus.EVALUATING,
            TesterExecutionStatus.REPORTING,
        ]:
            with self.subTest(source="STARTING", target=invalid_target):
                with self.assertRaises(InvalidTesterTransitionError):
                    exec_start.transition_to(invalid_target)

        # From RUNNING: cannot jump directly to COMPLETED
        exec_running = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.STARTING)
        exec_running.transition_to(TesterExecutionStatus.RUNNING)
        with self.assertRaises(InvalidTesterTransitionError):
            exec_running.transition_to(TesterExecutionStatus.COMPLETED)

        # From EVALUATING: cannot jump directly to COMPLETED
        exec_eval = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.STARTING)
        exec_eval.transition_to(TesterExecutionStatus.RUNNING)
        exec_eval.transition_to(TesterExecutionStatus.EVALUATING)
        with self.assertRaises(InvalidTesterTransitionError):
            exec_eval.transition_to(TesterExecutionStatus.COMPLETED)

        # From REPORTING: cannot jump back to RUNNING or EVALUATING
        exec_rep = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.STARTING)
        exec_rep.transition_to(TesterExecutionStatus.RUNNING)
        exec_rep.transition_to(TesterExecutionStatus.REPORTING)
        with self.assertRaises(InvalidTesterTransitionError):
            exec_rep.transition_to(TesterExecutionStatus.RUNNING)
        with self.assertRaises(InvalidTesterTransitionError):
            exec_rep.transition_to(TesterExecutionStatus.EVALUATING)

    # ----------------------------------------------------------------------
    # 3. Terminal State Immutability
    # ----------------------------------------------------------------------
    def test_terminal_state_immutability(self):
        """Verify that COMPLETED, FAILED, and CANCELLED are strictly terminal and irreversible."""
        wo = self._create_sample_work_order()

        # 1. COMPLETED cannot transition to any state
        exec_completed = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.STARTING)
        exec_completed.transition_to(TesterExecutionStatus.RUNNING)
        exec_completed.transition_to(TesterExecutionStatus.REPORTING)
        exec_completed.transition_to(TesterExecutionStatus.COMPLETED)
        self.assertTrue(exec_completed.is_terminal)

        for target in [
            TesterExecutionStatus.REQUESTED,
            TesterExecutionStatus.STARTING,
            TesterExecutionStatus.RUNNING,
            TesterExecutionStatus.EVALUATING,
            TesterExecutionStatus.REPORTING,
            TesterExecutionStatus.BLOCKED,
            TesterExecutionStatus.FAILED,
            TesterExecutionStatus.CANCELLED,
        ]:
            with self.subTest(terminal="COMPLETED", target=target):
                with self.assertRaises(InvalidTesterTransitionError):
                    exec_completed.transition_to(target)

        # 2. FAILED cannot transition to any state
        exec_failed = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.STARTING)
        exec_failed.transition_to(TesterExecutionStatus.FAILED, reason="Sandbox runner crashed")
        self.assertTrue(exec_failed.is_terminal)

        for target in [
            TesterExecutionStatus.STARTING,
            TesterExecutionStatus.RUNNING,
            TesterExecutionStatus.COMPLETED,
        ]:
            with self.subTest(terminal="FAILED", target=target):
                with self.assertRaises(InvalidTesterTransitionError):
                    exec_failed.transition_to(target)

        # 3. CANCELLED cannot transition to any state
        exec_cancelled = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.REQUESTED)
        exec_cancelled.cancel(reason="Work order revoked by manager")
        self.assertTrue(exec_cancelled.is_terminal)

        for target in [
            TesterExecutionStatus.STARTING,
            TesterExecutionStatus.RUNNING,
            TesterExecutionStatus.COMPLETED,
        ]:
            with self.subTest(terminal="CANCELLED", target=target):
                with self.assertRaises(InvalidTesterTransitionError):
                    exec_cancelled.transition_to(target)

    # ----------------------------------------------------------------------
    # 4. Execution Creation from WorkOrder Preserving Lineage
    # ----------------------------------------------------------------------
    def test_execution_creation_from_work_order_preserving_lineage(self):
        """Verify that creating execution from work order strictly inherits all causal lineage."""
        wo = self._create_sample_work_order()
        execution = TesterExecution.from_work_order(wo)

        self.assertTrue(execution.execution_id.startswith("texec-"))
        self.assertEqual(execution.work_order_id, wo.work_order_id)
        self.assertEqual(execution.task_id, wo.manager_task_id)
        self.assertEqual(execution.project_id, wo.project_id)
        self.assertEqual(execution.correlation_id, wo.correlation_id)
        self.assertEqual(execution.status, TesterExecutionStatus.REQUESTED)
        self.assertEqual(execution.current_phase, TesterExecutionPhase.PREPARING)

    # ----------------------------------------------------------------------
    # 5. Rejection of Mismatched Lineage and Project Isolation
    # ----------------------------------------------------------------------
    def test_rejection_of_mismatched_lineage_and_project(self):
        """Verify that attempts to instantiate or execute across projects/tasks are rejected."""
        # Missing required identifiers raises TesterLineageError
        with self.assertRaises(TesterLineageError):
            TesterExecution(
                execution_id=new_execution_id(),
                work_order_id="two-test-01",
                task_id="",
                project_id="proj-01",
                correlation_id="corr-01",
            )

        with self.assertRaises(TesterLineageError):
            TesterExecution(
                execution_id=new_execution_id(),
                work_order_id="two-test-01",
                task_id="task-01",
                project_id="",
                correlation_id="corr-01",
            )

        wo = self._create_sample_work_order()
        execution = TesterExecution.from_work_order(wo)

        # validate_lineage with foreign project ID raises TesterLineageError
        with self.assertRaises(TesterLineageError):
            execution.validate_lineage(expected_project_id="foreign_project")

        # validate_lineage with foreign task ID raises TesterLineageError
        with self.assertRaises(TesterLineageError):
            execution.validate_lineage(expected_task_id="foreign_task")

        # validate_lineage with mismatched work order raises TesterLineageError
        other_wo = self._create_sample_work_order(work_order_id="two-different-99")
        with self.assertRaises(TesterLineageError):
            execution.validate_lineage(work_order=other_wo)

    # ----------------------------------------------------------------------
    # 6. BLOCKED State and Unblocking
    # ----------------------------------------------------------------------
    def test_blocked_state_requires_reason_and_unblocks_cleanly(self):
        """
        Verify that BLOCKED requires factual reason/category and can resume
        cleanly to STARTING or RUNNING upon unblocking.
        """
        wo = self._create_sample_work_order()
        execution = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.STARTING)
        execution.transition_to(TesterExecutionStatus.RUNNING)

        # Transition to BLOCKED without reason raises TesterValidationError
        with self.assertRaises(TesterValidationError):
            execution.transition_to(TesterExecutionStatus.BLOCKED, reason="")

        # Escalating blocker via block()
        blk = execution.block(
            reason="Auth service database port 5432 unreachable",
            category=TesterBlockerCategory.ENVIRONMENT,
            severity=TesterBlockerSeverity.HIGH,
            required_decision="Confirm if external db tunnel should be opened",
        )
        self.assertEqual(execution.status, TesterExecutionStatus.BLOCKED)
        self.assertEqual(len(execution.active_blockers), 1)
        self.assertEqual(blk.category, TesterBlockerCategory.ENVIRONMENT)

        # Unblocking with empty notes raises TesterValidationError
        with self.assertRaises(TesterValidationError):
            execution.unblock(resolution_notes="")

        # Unblocking back to RUNNING
        execution.unblock(
            resolution_notes="Port forward established to auth db",
            resolved_by="manager",
            target_status=TesterExecutionStatus.RUNNING,
        )
        self.assertEqual(execution.status, TesterExecutionStatus.RUNNING)
        self.assertEqual(len(execution.active_blockers), 0)

        # Blocker can also unblock back to STARTING
        execution.block(
            reason="Expired API testing key",
            category=TesterBlockerCategory.PERMISSION,
        )
        self.assertEqual(execution.status, TesterExecutionStatus.BLOCKED)
        execution.unblock(
            resolution_notes="Rotated API testing token supplied",
            target_status=TesterExecutionStatus.STARTING,
        )
        self.assertEqual(execution.status, TesterExecutionStatus.STARTING)

    # ----------------------------------------------------------------------
    # 7. CANCELLED State Preserving Evidence and Partial Results
    # ----------------------------------------------------------------------
    def test_cancelled_state_preserves_evidence_and_partial_results(self):
        """
        Verify that CANCELLED preserves all accumulated traces, defects, findings,
        test cases, and evidence, and is recorded as CANCELLED rather than FAILED.
        """
        wo = self._create_sample_work_order()
        execution = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.STARTING)
        execution.transition_to(TesterExecutionStatus.RUNNING)

        # Record test case and defect before cancellation
        tc = TestCaseResult(
            test_id=new_test_case_id(),
            name="test_checkout_flow",
            status=TestCaseStatus.FAIL,
            description="Checkout flow performance test",
        )
        execution.record_test_case(tc)

        ev = TesterEvidence(
            evidence_id=new_evidence_id(),
            evidence_type=EvidenceType.TEST_OUTPUT,
            data="Checkout timed out after 30s",
        )
        execution.record_evidence(ev)

        defect = execution.record_defect(
            title="Checkout timeout on heavy cart",
            description="Cart with > 50 items fails with timeout",
            severity=DefectSeverity.HIGH,
            defect_type=DefectType.PERFORMANCE,
            evidence_ids=[ev.evidence_id],
        )

        # Cancel execution
        cancellation_reason = "Manager terminated test run due to urgent patch release"
        execution.cancel(reason=cancellation_reason)

        self.assertEqual(execution.status, TesterExecutionStatus.CANCELLED)
        self.assertTrue(execution.is_terminal)
        self.assertEqual(execution.cancellation_reason, cancellation_reason)

        # Invariant: Evidence, defects, test cases, traces preserved
        self.assertEqual(len(execution.test_cases), 1)
        self.assertEqual(len(execution.evidence), 1)
        self.assertEqual(len(execution.defects), 1)
        self.assertGreater(len(execution.traces), 0)

        # Final result reflects CANCELLED status without treating it as runtime crash
        res = execution.create_result(summary_for_manager="Session cancelled by manager")
        self.assertEqual(res.status, TesterResultStatus.CANCELLED)
        self.assertIn(ev.evidence_id, res.evidence_ids)

    # ----------------------------------------------------------------------
    # 8. Completion Validation
    # ----------------------------------------------------------------------
    def test_completion_validation(self):
        """
        Verify preconditions for COMPLETED:
        - Must be in REPORTING
        - Cannot have unresolved active blockers
        - Must have recorded at least one activity/trace
        """
        wo = self._create_sample_work_order()
        execution = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.STARTING)
        execution.transition_to(TesterExecutionStatus.RUNNING)

        # 1. Cannot complete directly from RUNNING
        with self.assertRaises(InvalidTesterTransitionError):
            execution.transition_to(TesterExecutionStatus.COMPLETED)

        # 2. Block and attempt to reach COMPLETED
        execution.block(reason="Missing test data fixtures", category=TesterBlockerCategory.MISSING_CONTEXT)
        with self.assertRaises(InvalidTesterTransitionError):
            execution.transition_to(TesterExecutionStatus.COMPLETED)

        # Unblock and proceed to REPORTING
        execution.unblock(resolution_notes="Fixtures injected", target_status=TesterExecutionStatus.RUNNING)
        execution.transition_to(TesterExecutionStatus.REPORTING)

        # 3. If an unresolved blocker is somehow present in REPORTING, completion is rejected
        fake_blocker = execution.blockers[0]
        fake_blocker.resolved_at = None  # Artificially unresolve
        with self.assertRaises(TesterValidationError):
            execution.transition_to(TesterExecutionStatus.COMPLETED)

        # Cleanly resolve blocker
        fake_blocker.resolved_at = "2026-09-09T00:00:00Z"
        execution.transition_to(TesterExecutionStatus.COMPLETED)
        self.assertEqual(execution.status, TesterExecutionStatus.COMPLETED)

    # ----------------------------------------------------------------------
    # 9. Execution Success vs. Product Acceptance Invariant
    # ----------------------------------------------------------------------
    def test_completed_allowed_despite_failed_tests_and_defects(self):
        """
        Crucial invariant:
        Execution status COMPLETED reflects successful completion of the evaluation session,
        NOT that the product passed evaluation.
        A COMPLETED execution can legitimately report failed tests, critical defects,
        and a DO_NOT_SHIP recommendation.
        """
        wo = self._create_sample_work_order()
        execution = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.STARTING)
        execution.transition_to(TesterExecutionStatus.RUNNING)

        # Record 5 failing test cases
        for i in range(5):
            execution.record_test_case(
                TestCaseResult(
                    test_id=new_test_case_id(),
                    name=f"test_security_boundary_{i}",
                    status=TestCaseStatus.FAIL,
                    description=f"Security test {i}",
                )
            )

        # Record critical defect
        ev = execution.record_evidence(
            TesterEvidence(
                evidence_id=new_evidence_id(),
                evidence_type=EvidenceType.LOG,
                data="SQL syntax error detected in authorization query",
            )
        )
        execution.record_defect(
            title="SQL Injection in auth route",
            description="Unescaped user input passed directly to database query",
            severity=DefectSeverity.CRITICAL,
            defect_type=DefectType.SECURITY,
            evidence_ids=[ev.evidence_id],
        )

        # Record failed acceptance criteria
        execution.record_acceptance_result(
            criterion_id="ac-coupon-01",
            description="Expired coupons are rejected with 400 Bad Request",
            status=AcceptanceCriterionStatus.FAIL,
            evidence_ids=[ev.evidence_id],
        )

        execution.transition_to(TesterExecutionStatus.EVALUATING)
        execution.transition_to(TesterExecutionStatus.REPORTING)

        # Execution MUST successfully transition to COMPLETED despite failing product quality
        execution.transition_to(TesterExecutionStatus.COMPLETED)
        self.assertEqual(execution.status, TesterExecutionStatus.COMPLETED)

        # Final result advisory advises DO_NOT_SHIP
        result = execution.create_result(
            summary_for_manager="Critical security vulnerabilities found in checkout.",
            ship_recommendation=ShipRecommendation.DO_NOT_SHIP,
        )
        self.assertEqual(result.status, TesterResultStatus.COMPLETED)
        self.assertEqual(result.ship_recommendation, ShipRecommendation.DO_NOT_SHIP)
        self.assertEqual(len(result.defects), 1)
        self.assertEqual(result.defects[0].severity, DefectSeverity.CRITICAL)

    # ----------------------------------------------------------------------
    # 10. Reporting Boundary: No New Test Execution
    # ----------------------------------------------------------------------
    def test_reporting_phase_boundary_rejects_new_tests(self):
        """
        Verify reporting boundary:
        Once an execution enters REPORTING (or any terminal state),
        no new test cases, evidence collection, or test action traces may be executed.
        """
        wo = self._create_sample_work_order()
        execution = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.STARTING)
        execution.transition_to(TesterExecutionStatus.RUNNING)
        execution.transition_to(TesterExecutionStatus.REPORTING)

        tc = TestCaseResult(
            test_id=new_test_case_id(),
            name="test_late_attempt",
            status=TestCaseStatus.PASS,
            description="Late test attempt",
        )
        ev = TesterEvidence(
            evidence_id=new_evidence_id(),
            evidence_type=EvidenceType.TEST_OUTPUT,
            data="late evidence",
        )

        # Rejects record_test_case while in REPORTING
        with self.assertRaises(TesterValidationError):
            execution.record_test_case(tc)

        # Rejects record_evidence while in REPORTING
        with self.assertRaises(TesterValidationError):
            execution.record_evidence(ev)

        # Rejects create_trace for EXECUTE_TEST while in REPORTING
        with self.assertRaises(TesterValidationError):
            execution.create_trace(TesterActionType.EXECUTE_TEST, {"cmd": "run_test"})

        # Rejects create_trace for CAPTURE_EVIDENCE while in REPORTING
        with self.assertRaises(TesterValidationError):
            execution.create_trace(TesterActionType.CAPTURE_EVIDENCE, {"target": "screenshot"})

        # Permitted non-test trace (e.g. reporting observation)
        obs_trace = execution.create_trace(TesterActionType.REPORT_FINDING, {"info": "Packaging final report"})
        self.assertIsNotNone(obs_trace)

    # ----------------------------------------------------------------------
    # 11. State Transition Trace Generation
    # ----------------------------------------------------------------------
    def test_state_transition_trace_generation(self):
        """Verify that every lifecycle transition records auditable history and traces."""
        wo = self._create_sample_work_order()
        execution = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.REQUESTED)

        execution.transition_to(TesterExecutionStatus.STARTING, reason="Starting container")
        execution.transition_to(TesterExecutionStatus.RUNNING, reason="Running suite")
        execution.transition_to(TesterExecutionStatus.EVALUATING, reason="Evaluating logs")
        execution.transition_to(TesterExecutionStatus.REPORTING, reason="Formatting payload")

        # 4 transitions recorded in transition_history
        self.assertEqual(len(execution.transition_history), 4)
        transitions = [(h["previous_status"], h["new_status"]) for h in execution.transition_history]
        self.assertEqual(
            transitions,
            [
                ("REQUESTED", "STARTING"),
                ("STARTING", "RUNNING"),
                ("RUNNING", "EVALUATING"),
                ("EVALUATING", "REPORTING"),
            ],
        )

        # Traces generated for each transition
        self.assertGreaterEqual(len(execution.traces), 4)
        for trace in execution.traces[:4]:
            self.assertTrue(trace.trace_id.startswith("ttrace-"))
            self.assertEqual(trace.execution_id, execution.execution_id)
            self.assertEqual(trace.work_order_id, execution.work_order_id)
            self.assertIn("transition", trace.action_details)

    # ----------------------------------------------------------------------
    # 12. No Automatic Retesting: Manager Owns Iteration
    # ----------------------------------------------------------------------
    def test_no_automatic_retesting_iteration_boundary(self):
        """
        Verify iteration boundary:
        TesterExecution does not automatically loop or spawn subsequent test runs.
        Manager owns iteration by issuing a new or revised WorkOrder.
        """
        wo = self._create_sample_work_order()
        execution = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.STARTING)
        execution.transition_to(TesterExecutionStatus.RUNNING)
        execution.transition_to(TesterExecutionStatus.REPORTING)
        execution.transition_to(TesterExecutionStatus.COMPLETED)

        # Attempting to restart or retest on the completed execution fails
        with self.assertRaises(InvalidTesterTransitionError):
            execution.transition_to(TesterExecutionStatus.STARTING)

        with self.assertRaises(InvalidTesterTransitionError):
            execution.transition_to(TesterExecutionStatus.RUNNING)

        # Verify no self-spawning iteration methods exist on execution
        self.assertFalse(hasattr(execution, "retest"))
        self.assertFalse(hasattr(execution, "retry"))
        self.assertFalse(hasattr(execution, "restart"))
        self.assertFalse(hasattr(execution, "create_next_execution"))

        # Re-testing MUST be done by Manager via revised or new WorkOrder
        revised_wo = wo.create_revision(
            modifications={"objective": "Re-evaluate checkout after bug fix"},
            reason="Fix applied by Programmer, re-verification requested by Manager",
        )
        self.assertNotEqual(revised_wo.work_order_id, wo.work_order_id)
        self.assertEqual(revised_wo.parent_work_order_id, wo.work_order_id)

        # Fresh execution attempt spawned under new WorkOrder
        new_execution = revised_wo.create_execution(status=TesterExecutionStatus.STARTING)
        self.assertNotEqual(new_execution.execution_id, execution.execution_id)
        self.assertEqual(new_execution.work_order_id, revised_wo.work_order_id)

    # ----------------------------------------------------------------------
    # 13. Allowed Transitions Matrix
    # ----------------------------------------------------------------------
    def test_allowed_transitions_matrix(self):
        """Verify the full transition matrix in TesterLifecycle matches specification."""
        self.assertEqual(
            TesterLifecycle.get_allowed_transitions(TesterExecutionStatus.REQUESTED),
            {TesterExecutionStatus.STARTING, TesterExecutionStatus.CANCELLED},
        )
        self.assertEqual(
            TesterLifecycle.get_allowed_transitions(TesterExecutionStatus.STARTING),
            {
                TesterExecutionStatus.RUNNING,
                TesterExecutionStatus.BLOCKED,
                TesterExecutionStatus.FAILED,
                TesterExecutionStatus.CANCELLED,
            },
        )
        self.assertEqual(
            TesterLifecycle.get_allowed_transitions(TesterExecutionStatus.RUNNING),
            {
                TesterExecutionStatus.EVALUATING,
                TesterExecutionStatus.REPORTING,
                TesterExecutionStatus.BLOCKED,
                TesterExecutionStatus.FAILED,
                TesterExecutionStatus.CANCELLED,
            },
        )
        self.assertEqual(
            TesterLifecycle.get_allowed_transitions(TesterExecutionStatus.EVALUATING),
            {
                TesterExecutionStatus.REPORTING,
                TesterExecutionStatus.RUNNING,
                TesterExecutionStatus.BLOCKED,
                TesterExecutionStatus.FAILED,
                TesterExecutionStatus.CANCELLED,
            },
        )
        self.assertEqual(
            TesterLifecycle.get_allowed_transitions(TesterExecutionStatus.REPORTING),
            {
                TesterExecutionStatus.COMPLETED,
                TesterExecutionStatus.FAILED,
                TesterExecutionStatus.CANCELLED,
            },
        )
        self.assertEqual(
            TesterLifecycle.get_allowed_transitions(TesterExecutionStatus.BLOCKED),
            {
                TesterExecutionStatus.STARTING,
                TesterExecutionStatus.RUNNING,
                TesterExecutionStatus.CANCELLED,
                TesterExecutionStatus.FAILED,
            },
        )
        self.assertEqual(
            TesterLifecycle.get_allowed_transitions(TesterExecutionStatus.COMPLETED),
            set(),
        )
        self.assertEqual(
            TesterLifecycle.get_allowed_transitions(TesterExecutionStatus.FAILED),
            set(),
        )
        self.assertEqual(
            TesterLifecycle.get_allowed_transitions(TesterExecutionStatus.CANCELLED),
            set(),
        )

    # ----------------------------------------------------------------------
    # 14. Serialization Fidelity Roundtrip
    # ----------------------------------------------------------------------
    def test_serialization_fidelity_roundtrip(self):
        """Verify to_dict and from_dict preserve all execution lifecycle state and child entities."""
        wo = self._create_sample_work_order()
        execution = TesterExecution.from_work_order(wo, status=TesterExecutionStatus.STARTING)
        execution.transition_to(TesterExecutionStatus.RUNNING)

        tc = TestCaseResult(
            test_id=new_test_case_id(),
            name="test_checkout",
            status=TestCaseStatus.PASS,
            description="Checkout test",
        )
        execution.record_test_case(tc)

        ev = TesterEvidence(
            evidence_id=new_evidence_id(),
            evidence_type=EvidenceType.TEST_OUTPUT,
            data="output sample",
        )
        execution.record_evidence(ev)

        defect = execution.record_defect(
            title="Defect 1",
            description="Description 1",
            severity=DefectSeverity.LOW,
            defect_type=DefectType.FUNCTIONAL,
        )

        execution.transition_to(TesterExecutionStatus.EVALUATING)
        execution.transition_to(TesterExecutionStatus.REPORTING)

        data = execution.to_dict()
        reconstituted = TesterExecution.from_dict(data)

        self.assertEqual(reconstituted.execution_id, execution.execution_id)
        self.assertEqual(reconstituted.work_order_id, execution.work_order_id)
        self.assertEqual(reconstituted.task_id, execution.task_id)
        self.assertEqual(reconstituted.project_id, execution.project_id)
        self.assertEqual(reconstituted.correlation_id, execution.correlation_id)
        self.assertEqual(reconstituted.status, TesterExecutionStatus.REPORTING)
        self.assertEqual(reconstituted.current_phase, TesterExecutionPhase.REPORTING)
        self.assertEqual(len(reconstituted.test_cases), 1)
        self.assertEqual(len(reconstituted.evidence), 1)
        self.assertEqual(len(reconstituted.defects), 1)
        self.assertEqual(len(reconstituted.transition_history), 3)


if __name__ == "__main__":
    unittest.main()

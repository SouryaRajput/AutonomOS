from __future__ import annotations

import unittest
from typing import Any

from core.enums import RiskLevel, TaskStatus, WorkerStatus
from core.events.types import EventSource, EventType
from core.models import Task, WorkerOutput
from core.tester.contracts.blocker import TesterBlocker
from core.tester.contracts.boundary import TesterBoundaryGuard
from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.finding import (
    AcceptanceCriterionResult,
    TesterDefect,
    TesterEvidence,
)
from core.tester.contracts.identifiers import (
    new_blocker_id,
    new_defect_id,
    new_evidence_id,
    new_execution_id,
    new_result_id,
    new_test_case_id,
    new_work_order_id,
)
from core.tester.contracts.lifecycle import TesterLifecycle
from core.tester.contracts.manager_bridge import (
    FakeTesterWorker,
    TesterManagerBridge,
)
from core.tester.contracts.result import TesterResult
from core.tester.contracts.scope import TestScope
from core.tester.contracts.test_case import TestCaseResult
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    InvalidTesterTransitionError,
    TesterBoundaryViolationError,
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
    TesterBlockerCategory,
    TesterBlockerSeverity,
    TesterExecutionStatus,
    TesterResultStatus,
    TesterWorkOrderStatus,
    TestingCapability,
    WorkerType,
)


class TestTesterManagerIntegration(unittest.TestCase):
    """
    End-to-End Domain Contract & Manager Integration Tests for Tester Subsystem (Phase 1.5).
    
    Verifies:
    1. Manager Task -> TesterWorkOrder -> TesterExecution -> TesterResult -> Manager (strict lineage)
    2. Lifecycle events emission sequence (TESTER_REQUESTED, STARTED, BLOCKED, COMPLETED, FAILED, CANCELLED)
    3. Authority boundaries: Manager owns WHAT/WHY/budgets/scope; Tester cannot expand scope or increase budgets
    4. Failure, Blocked, and Cancellation flows with complete provenance
    5. Execution success vs. Product acceptance: execution completes even when product acceptance fails (DO_NOT_SHIP)
    6. No-Infinite-Loop contract: execution reaches terminal state, stops, and cannot self-loop or self-retest
    7. Worker registration: identifiable as worker_type="TESTER" with allowed V1 capabilities
    """

    def setUp(self) -> None:
        self.bridge = TesterManagerBridge()
        self.sample_task = Task(
            id="tsk-mgr-test-001",
            project_id="prj-checkout-v2",
            title="Evaluate Checkout Payment Processing",
            objective="Verify stripe webhook idempotency and currency conversion edge cases",
            status=TaskStatus.PENDING,
            priority=10,
            risk=RiskLevel.MEDIUM,
            metadata={
                "correlation_id": "corr-chk-001",
                "instructions": [
                    "Validate webhook idempotency key handling",
                    "Verify EUR to USD exchange rate boundary values",
                ],
                "product_artifact": "checkout-service-bin-v2.1",
                "source_revision": "git-rev-c0ffee1",
                "test_scope": ["checkout.webhook", "checkout.currency", "checkout.db"],
                "test_categories": ["FUNCTIONAL", "REGRESSION"],
                "required_flows": ["flow_idempotent_payment", "flow_exchange_rates"],
                "acceptance_criteria": [
                    {
                        "criterion_id": "ac-chk-01",
                        "description": "Duplicate webhooks with same idempotency key return 200 without double charging",
                        "category": "FUNCTIONAL",
                    },
                    {
                        "criterion_id": "ac-chk-02",
                        "description": "Currency conversion handles negative or zero rates by failing safely",
                        "category": "REGRESSION",
                    },
                ],
                "authorized_capabilities": [
                    "TEST_EXECUTION",
                    "BEHAVIOR_OBSERVATION",
                    "EVIDENCE_COLLECTION",
                    "ACCEPTANCE_EVALUATION",
                    "DEFECT_IDENTIFICATION",
                    "RECOMMENDATION_REPORTING",
                ],
                "time_budget": 360,
                "iteration_budget": 3,
            },
        )

    def test_e2e_golden_path_and_lineage_preservation(self) -> None:
        """
        Test full golden path from Manager Task to WorkOrder to Execution to Result to WorkerOutput.
        Verifies that strict causal lineage is 100% preserved throughout the entire chain.
        """
        # 1. Manager issues work order
        work_order = self.bridge.issue_work_order(self.sample_task)

        # Lineage checks on WorkOrder
        self.assertTrue(work_order.work_order_id.startswith("two-"))
        self.assertEqual(work_order.manager_task_id, self.sample_task.id)
        self.assertEqual(work_order.project_id, self.sample_task.project_id)
        self.assertEqual(work_order.correlation_id, "corr-chk-001")
        self.assertEqual(work_order.objective, self.sample_task.objective)
        self.assertEqual(work_order.time_budget, 360)
        self.assertEqual(work_order.iteration_budget, 3)
        self.assertEqual(len(work_order.acceptance_criteria), 2)

        # Event TESTER_REQUESTED emitted
        self.assertEqual(len(self.bridge.events), 1)
        req_event = self.bridge.events[0]
        self.assertEqual(req_event.event_type, EventType.TESTER_REQUESTED)
        self.assertEqual(req_event.source, EventSource.MANAGER)
        self.assertEqual(req_event.project_id, self.sample_task.project_id)
        self.assertEqual(req_event.task_id, self.sample_task.id)
        self.assertEqual(req_event.payload["work_order_id"], work_order.work_order_id)

        # 2. Worker executes work order
        fake_worker = FakeTesterWorker(bridge=self.bridge)
        execution, result, worker_output = fake_worker.execute_work_order(work_order)

        # Lineage and status checks on Execution
        self.assertTrue(execution.is_terminal)
        self.assertEqual(execution.status, TesterExecutionStatus.COMPLETED)
        self.assertEqual(execution.work_order_id, work_order.work_order_id)
        self.assertEqual(execution.task_id, self.sample_task.id)
        self.assertEqual(execution.project_id, self.sample_task.project_id)
        self.assertEqual(execution.correlation_id, "corr-chk-001")

        # Lineage and status checks on Result
        self.assertTrue(result.result_id.startswith("tres-"))
        self.assertEqual(result.work_order_id, work_order.work_order_id)
        self.assertEqual(result.execution_id, execution.execution_id)
        self.assertEqual(result.task_id, self.sample_task.id)
        self.assertEqual(result.project_id, self.sample_task.project_id)
        self.assertEqual(result.correlation_id, "corr-chk-001")
        self.assertTrue(result.is_success())
        self.assertEqual(result.ship_recommendation, ShipRecommendation.SHIP)
        self.assertTrue(result.is_acceptance_fully_verified())

        # WorkerOutput returned to Manager
        self.assertIsInstance(worker_output, WorkerOutput)
        self.assertTrue(worker_output.success)
        self.assertEqual(worker_output.metadata["result_id"], result.result_id)
        self.assertEqual(worker_output.metadata["work_order_id"], work_order.work_order_id)
        self.assertEqual(worker_output.metadata["task_id"], self.sample_task.id)
        self.assertEqual(worker_output.metadata["project_id"], self.sample_task.project_id)
        self.assertEqual(worker_output.metadata["ship_recommendation"], "SHIP")
        self.assertTrue(worker_output.metadata["acceptance_fully_verified"])

    def test_event_emission_sequence(self) -> None:
        """
        Verify the exact sequential order of emitted events:
        TESTER_REQUESTED -> TESTER_STARTED -> TESTER_COMPLETED
        with complete identity and causal lineage on every event.
        """
        work_order = self.bridge.issue_work_order(self.sample_task)
        fake_worker = FakeTesterWorker(bridge=self.bridge)
        execution, result, _ = fake_worker.execute_work_order(work_order)

        event_types = [e.event_type for e in self.bridge.events]
        self.assertEqual(
            event_types,
            [
                EventType.TESTER_REQUESTED,
                EventType.TESTER_STARTED,
                EventType.TESTER_COMPLETED,
            ],
        )

        # 1. TESTER_REQUESTED: Source is Manager
        evt_req = self.bridge.events[0]
        self.assertEqual(evt_req.source, EventSource.MANAGER)
        self.assertEqual(evt_req.project_id, self.sample_task.project_id)
        self.assertEqual(evt_req.task_id, self.sample_task.id)
        self.assertEqual(evt_req.payload["work_order_id"], work_order.work_order_id)

        # 2. TESTER_STARTED: Source is Worker
        evt_start = self.bridge.events[1]
        self.assertEqual(evt_start.source, EventSource.WORKER)
        self.assertEqual(evt_start.project_id, self.sample_task.project_id)
        self.assertEqual(evt_start.task_id, self.sample_task.id)
        self.assertEqual(evt_start.payload["execution_id"], execution.execution_id)
        self.assertEqual(evt_start.payload["work_order_id"], work_order.work_order_id)

        # 3. TESTER_COMPLETED: Source is Worker
        evt_done = self.bridge.events[2]
        self.assertEqual(evt_done.source, EventSource.WORKER)
        self.assertEqual(evt_done.project_id, self.sample_task.project_id)
        self.assertEqual(evt_done.task_id, self.sample_task.id)
        self.assertEqual(evt_done.payload["result_id"], result.result_id)
        self.assertEqual(evt_done.payload["execution_id"], execution.execution_id)
        self.assertEqual(evt_done.payload["ship_recommendation"], "SHIP")

    def test_lineage_protection_tampering_rejection(self) -> None:
        """
        Verify that attempting to deliver a result with mismatched work_order_id,
        task_id, or execution_id raises TesterLineageError.
        """
        work_order = self.bridge.issue_work_order(self.sample_task)
        execution = self.bridge.dispatch_work_order(work_order)

        # 1. Mismatched work_order_id
        bad_wo_result = TesterResult(
            result_id=new_result_id(),
            execution_id=execution.execution_id,
            work_order_id="two-foreign-9999",
            task_id=self.sample_task.id,
            project_id=self.sample_task.project_id,
            correlation_id=work_order.correlation_id,
            status=TesterResultStatus.COMPLETED,
        )
        with self.assertRaises(TesterLineageError):
            self.bridge.receive_result(bad_wo_result, execution, work_order)

        # 2. Mismatched task_id
        bad_task_result = TesterResult(
            result_id=new_result_id(),
            execution_id=execution.execution_id,
            work_order_id=work_order.work_order_id,
            task_id="tsk-tampered-8888",
            project_id=self.sample_task.project_id,
            correlation_id=work_order.correlation_id,
            status=TesterResultStatus.COMPLETED,
        )
        with self.assertRaises(TesterLineageError):
            self.bridge.receive_result(bad_task_result, execution, work_order)

        # 3. Mismatched execution_id
        bad_exec_result = TesterResult(
            result_id=new_result_id(),
            execution_id="texec-unknown-7777",
            work_order_id=work_order.work_order_id,
            task_id=self.sample_task.id,
            project_id=self.sample_task.project_id,
            correlation_id=work_order.correlation_id,
            status=TesterResultStatus.COMPLETED,
        )
        with self.assertRaises(TesterLineageError):
            self.bridge.receive_result(bad_exec_result, execution, work_order)

        # 4. Cross-project isolation
        bad_proj_result = TesterResult(
            result_id=new_result_id(),
            execution_id=execution.execution_id,
            work_order_id=work_order.work_order_id,
            task_id=self.sample_task.id,
            project_id="prj-foreign-project-xyz",
            correlation_id=work_order.correlation_id,
            status=TesterResultStatus.COMPLETED,
        )
        with self.assertRaises(TesterLineageError):
            self.bridge.receive_result(bad_proj_result, execution, work_order)

    def test_worker_authority_boundaries_enforcement(self) -> None:
        """
        Verify the architectural authority boundary:
        Manager owns WHAT, WHY, budgets, and scope.
        Tester CANNOT expand scope, modify WorkOrder parameters, or increase budgets.
        """
        work_order = self.bridge.issue_work_order(self.sample_task)

        # 1. Budget expansion rejection
        tampered_work_order = TesterWorkOrder(
            work_order_id=work_order.work_order_id,
            manager_task_id=work_order.manager_task_id,
            project_id=work_order.project_id,
            correlation_id=work_order.correlation_id,
            objective=work_order.objective,
            test_scope=work_order.test_scope,
            time_budget=work_order.time_budget + 500,  # Unauthorized budget increase
            iteration_budget=work_order.iteration_budget,
        )
        with self.assertRaises(TesterBoundaryViolationError) as ctx:
            self.bridge.assert_authority_boundary(tampered_work_order, original_work_order=work_order)
        self.assertIn("INCREASE_BUDGET", str(ctx.exception))

        # 2. Scope expansion rejection
        expanded_scope_wo = TesterWorkOrder(
            work_order_id=work_order.work_order_id,
            manager_task_id=work_order.manager_task_id,
            project_id=work_order.project_id,
            correlation_id=work_order.correlation_id,
            objective=work_order.objective,
            test_scope=["checkout.webhook", "admin.database.wipe"],  # Unauthorized scope addition
            time_budget=work_order.time_budget,
            iteration_budget=work_order.iteration_budget,
        )
        with self.assertRaises(TesterBoundaryViolationError) as ctx:
            self.bridge.assert_authority_boundary(expanded_scope_wo, original_work_order=work_order)
        self.assertIn("EXPAND_TEST_SCOPE", str(ctx.exception))

        # 3. Test execution target outside authorized scope rejection
        execution = self.bridge.dispatch_work_order(work_order)
        execution.transition_to(TesterExecutionStatus.RUNNING, "Starting tests")
        execution.record_test_case(
            TestCaseResult(
                test_id=new_test_case_id(),
                name="unauthorized.internal.telemetry.exploit",
                status=TestCaseStatus.PASS,
            )
        )
        with self.assertRaises(TesterBoundaryViolationError) as ctx:
            self.bridge.assert_authority_boundary(work_order, execution=execution)
        self.assertIn("EXPAND_TEST_SCOPE", str(ctx.exception))

        # 4. Source code modification forbidden
        with self.assertRaises(TesterBoundaryViolationError):
            TesterBoundaryGuard.assert_action_permitted("MODIFY_SOURCE_CODE")
        with self.assertRaises(TesterBoundaryViolationError):
            TesterBoundaryGuard.assert_action_permitted("FIX_DEFECT")

    def test_blocker_escalation_and_unblocking_flow(self) -> None:
        """
        Verify operational blocker escalation from Tester to Manager and subsequent resolution:
        1. Tester encounters material blocker
        2. Escalates blocker: execution transitions to BLOCKED, emits TESTER_BLOCKED
        3. Manager resolves blocker
        4. Execution transitions back to RUNNING and completes
        """
        work_order = self.bridge.issue_work_order(self.sample_task)
        execution = self.bridge.dispatch_work_order(work_order)
        execution.transition_to(TesterExecutionStatus.RUNNING, "Running initial checks")

        blocker = TesterBlocker(
            blocker_id=new_blocker_id(),
            work_order_id=work_order.work_order_id,
            execution_id=execution.execution_id,
            task_id=self.sample_task.id,
            category=TesterBlockerCategory.ENVIRONMENT,
            severity=TesterBlockerSeverity.HIGH,
            description="Stripe test webhook endpoint returned HTTP 503 Service Unavailable",
            required_decision="Restart mock Stripe test service in staging cluster",
        )

        # Escalate
        escalated = self.bridge.escalate_blocker(execution, blocker, work_order=work_order)
        self.assertEqual(execution.status, TesterExecutionStatus.BLOCKED)
        self.assertEqual(len(execution.active_blockers), 1)
        self.assertEqual(escalated.blocker_id, blocker.blocker_id)

        # Verify event
        blocked_evt = self.bridge.events[-1]
        self.assertEqual(blocked_evt.event_type, EventType.TESTER_BLOCKED)
        self.assertEqual(blocked_evt.payload["blocker_id"], blocker.blocker_id)
        self.assertEqual(blocked_evt.payload["category"], "ENVIRONMENT")

        # Cannot complete execution while active blockers exist
        execution.status = TesterExecutionStatus.REPORTING
        with self.assertRaises(TesterValidationError):
            execution.transition_to(TesterExecutionStatus.COMPLETED)
        execution.status = TesterExecutionStatus.BLOCKED

        # Manager resolves blocker
        self.bridge.resolve_blocker(
            execution=execution,
            blocker_id=blocker.blocker_id,
            resolution="Mock Stripe service restarted and verified healthy",
            target_status=TesterExecutionStatus.RUNNING,
            work_order=work_order,
        )

        self.assertEqual(execution.status, TesterExecutionStatus.RUNNING)
        self.assertEqual(len(execution.active_blockers), 0)
        self.assertTrue(blocker.is_resolved)
        self.assertEqual(blocker.resolved_by, "manager")

    def test_cancellation_provenance_and_partial_evidence_preservation(self) -> None:
        """
        Verify cancellation flow:
        1. In-progress execution is cancelled by Manager
        2. Execution transitions to CANCELLED and records provenance
        3. Event TESTER_CANCELLED is emitted
        4. Accumulated traces and evidence are preserved
        """
        work_order = self.bridge.issue_work_order(self.sample_task)
        execution = self.bridge.dispatch_work_order(work_order)
        execution.transition_to(TesterExecutionStatus.RUNNING, "Starting tests")

        # Collect some evidence before cancellation
        ev = TesterEvidence(
            evidence_id=new_evidence_id(),
            evidence_type=EvidenceType.TEST_OUTPUT,
            description="Partial log captured prior to interruption",
            data="Init check OK",
        )
        execution.record_evidence(ev)

        # Cancel
        self.bridge.cancel_execution(
            execution=execution,
            requested_by="MANAGER",
            reason="Release cycle deferred by product team",
            work_order=work_order,
        )

        self.assertTrue(execution.is_terminal)
        self.assertEqual(execution.status, TesterExecutionStatus.CANCELLED)
        self.assertEqual(execution.cancellation_reason, "Release cycle deferred by product team")

        # Evidence preserved
        self.assertEqual(len(execution.evidence), 1)
        self.assertEqual(execution.evidence[0].evidence_id, ev.evidence_id)

        # Verify event
        cancelled_evt = self.bridge.events[-1]
        self.assertEqual(cancelled_evt.event_type, EventType.TESTER_CANCELLED)
        self.assertEqual(cancelled_evt.source, EventSource.MANAGER)
        self.assertEqual(cancelled_evt.payload["requested_by"], "MANAGER")

    def test_runtime_failure_handling(self) -> None:
        """
        Verify that an execution experiencing internal runtime failures
        transitions to FAILED and emits TESTER_FAILED.
        """
        work_order = self.bridge.issue_work_order(self.sample_task)
        fake_worker = FakeTesterWorker(bridge=self.bridge, simulate_failure=True)

        execution, result, worker_output = fake_worker.execute_work_order(work_order)

        self.assertEqual(execution.status, TesterExecutionStatus.FAILED)
        self.assertEqual(result.status, TesterResultStatus.FAILED)
        self.assertFalse(result.is_success())
        self.assertFalse(worker_output.success)

        failed_evt = self.bridge.events[-1]
        self.assertEqual(failed_evt.event_type, EventType.TESTER_FAILED)
        self.assertEqual(failed_evt.payload["execution_id"], execution.execution_id)

    def test_execution_success_vs_product_acceptance_separation(self) -> None:
        """
        CRITICAL ARCHITECTURAL INVARIANT:
        Execution Success != Product Acceptance.
        
        When tests fail, defects are found, and ship recommendation is DO_NOT_SHIP:
        - TesterExecution COMPLETED legitimately (Tester did its job)
        - TesterResult status is COMPLETED
        - WorkerOutput success is True (the worker finished evaluating)
        - Result indicates failures, defects, and advisory DO_NOT_SHIP
        - Manager receives findings and makes release determination
        """
        work_order = self.bridge.issue_work_order(self.sample_task)

        # Simulate severe defects and test failures
        critical_defect = TesterDefect(
            defect_id=new_defect_id(),
            work_order_id=work_order.work_order_id,
            execution_id="texec-temp",
            title="Double charge occurring on duplicate webhook retry",
            description="Database lock was not acquired before updating transaction status",
            severity=DefectSeverity.CRITICAL,
            defect_type=DefectType.FUNCTIONAL,
            reproduction_steps=["Send webhook idemp-100", "Send webhook idemp-100 again within 50ms"],
            expected_behavior="Second request rejected or treated as idempotent replay",
            actual_behavior="Second request debited customer credit card again",
            evidence_ids=[new_evidence_id()],
        )

        fake_worker = FakeTesterWorker(
            bridge=self.bridge,
            simulate_test_failures=True,
            simulate_defects=[critical_defect],
            ship_recommendation=ShipRecommendation.DO_NOT_SHIP,
        )

        execution, result, worker_output = fake_worker.execute_work_order(work_order)

        # 1. Execution completed successfully (not crashed)
        self.assertEqual(execution.status, TesterExecutionStatus.COMPLETED)
        self.assertTrue(execution.is_terminal)

        # 2. Result completed successfully
        self.assertEqual(result.status, TesterResultStatus.COMPLETED)
        self.assertTrue(result.is_success())

        # 3. Product acceptance failed
        self.assertEqual(result.ship_recommendation, ShipRecommendation.DO_NOT_SHIP)
        self.assertTrue(result.has_defects())
        self.assertTrue(result.has_critical_defects())
        self.assertFalse(result.is_acceptance_fully_verified())
        self.assertEqual(result.tests_failed, 1)

        # 4. WorkerOutput reflects successful evaluation with DO_NOT_SHIP advisory
        self.assertTrue(worker_output.success)
        self.assertEqual(worker_output.metadata["ship_recommendation"], "DO_NOT_SHIP")
        self.assertEqual(worker_output.metadata["defects_count"], 1)
        self.assertEqual(worker_output.metadata["critical_defects_count"], 1)
        self.assertFalse(worker_output.metadata["acceptance_fully_verified"])

        # 5. Terminal event was TESTER_COMPLETED carrying DO_NOT_SHIP payload
        completed_evt = self.bridge.events[-1]
        self.assertEqual(completed_evt.event_type, EventType.TESTER_COMPLETED)
        self.assertEqual(completed_evt.payload["ship_recommendation"], "DO_NOT_SHIP")
        self.assertEqual(completed_evt.payload["defects_count"], 1)
        self.assertEqual(completed_evt.payload["critical_defects_count"], 1)

    def test_no_infinite_loop_contract(self) -> None:
        """
        NO-INFINITE-LOOP CONTRACT TEST:
        
        Guarantees:
        1. Tester execution terminates in COMPLETED (terminal state).
        2. Attempting to transition from COMPLETED back to active states (RUNNING, STARTING) is strictly rejected.
        3. Attempting to record tests, defects, or findings in COMPLETED state is strictly rejected.
        4. Tester cannot self-retest autonomously. Retesting requires Manager to issue a new WorkOrder.
        """
        work_order = self.bridge.issue_work_order(self.sample_task)
        fake_worker = FakeTesterWorker(bridge=self.bridge)
        execution, result, _ = fake_worker.execute_work_order(work_order)

        # 1. Execution is terminal
        self.assertTrue(execution.is_terminal)
        self.assertEqual(execution.status, TesterExecutionStatus.COMPLETED)

        # 2. Cannot loop back to active states
        with self.assertRaises(InvalidTesterTransitionError):
            execution.transition_to(TesterExecutionStatus.RUNNING)
        with self.assertRaises(InvalidTesterTransitionError):
            execution.transition_to(TesterExecutionStatus.STARTING)
        with self.assertRaises(InvalidTesterTransitionError):
            execution.transition_to(TesterExecutionStatus.EVALUATING)

        # 3. Cannot execute further tests in terminal state
        with self.assertRaises(TesterValidationError):
            execution.record_test_case(
                TestCaseResult(
                    test_id=new_test_case_id(),
                    name="Loop test attempt",
                    status=TestCaseStatus.PASS,
                )
            )

        # 4. Retesting requires Manager to create a new revision or work order
        revised_wo = work_order.create_revision(
            modifications={"time_budget": 450},
            reason="Manager authorized retest with extended time budget",
        )
        self.assertNotEqual(revised_wo.work_order_id, work_order.work_order_id)
        self.assertEqual(revised_wo.parent_work_order_id, work_order.work_order_id)
        self.assertEqual(revised_wo.revision_number, 2)
        self.assertEqual(revised_wo.manager_task_id, self.sample_task.id)

    def test_worker_registration_and_manifest_capabilities(self) -> None:
        """
        Verify that Tester worker is identifiable as worker_type="TESTER"
        and exposes authorized V1 capabilities while respecting boundaries.
        """
        fake_worker = FakeTesterWorker(bridge=self.bridge)

        # 1. worker_type identification
        self.assertEqual(fake_worker.worker_type, "TESTER")
        self.assertEqual(fake_worker.worker_type, WorkerType.TESTER.value)

        # 2. Manifest compliance
        manifest = fake_worker.get_manifest()
        self.assertEqual(manifest.role, "Tester")
        self.assertEqual(manifest.status, WorkerStatus.IDLE)
        self.assertIn("TESTING", manifest.capabilities)
        self.assertIn("CODE_ANALYSIS", manifest.capabilities)
        self.assertIn("STRUCTURED_OUTPUT", manifest.capabilities)

        # 3. Forbidden capabilities are absent
        forbidden = ["MODIFY_SOURCE_CODE", "FIX_DEFECT", "DEPLOY_PRODUCT", "MERGE_CODE"]
        for f in forbidden:
            self.assertNotIn(f, manifest.capabilities)

        # 4. Standard Worker execute_task interface works
        output = fake_worker.execute_task(context=None, task=self.sample_task)
        self.assertIsInstance(output, WorkerOutput)
        self.assertTrue(output.success)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
import uuid

from core.errors import AutonomOSError
from core.models import Evidence as RuntimeEvidence, Task, WorkerOutput
from core.tester import (
    AcceptanceCriterionResult,
    AcceptanceCriterionStatus,
    ALL_TESTER_PREFIXES,
    BLOCKER_ID_PREFIX,
    DEFECT_ID_PREFIX,
    DefectSeverity,
    DefectType,
    EVIDENCE_ID_PREFIX,
    EXECUTION_ID_PREFIX,
    FINDING_ID_PREFIX,
    FindingCategory,
    ForbiddenTesterAction,
    InvalidTesterIdError,
    InvalidTesterTransitionError,
    is_tester_id,
    new_blocker_id,
    new_defect_id,
    new_evidence_id,
    new_execution_id,
    new_finding_id,
    new_result_id,
    new_trace_id,
    new_work_order_id,
    RESULT_ID_PREFIX,
    TESTER_ALLOWED_CAPABILITIES,
    TESTER_FORBIDDEN_ACTIONS,
    TesterActionType,
    TesterBlocker,
    TesterBlockerCategory,
    TesterBlockerSeverity,
    TesterBoundaryGuard,
    TesterBoundaryViolationError,
    TesterDefect,
    TesterError,
    TesterEvidence,
    TesterExecution,
    TesterExecutionStatus,
    TesterFinding,
    TesterLifecycle,
    TesterLineageError,
    TesterResult,
    TesterResultStatus,
    TesterTrace,
    TesterValidationError,
    TesterWorkOrder,
    TesterWorkOrderStatus,
    TestingCapability,
    TRACE_ID_PREFIX,
    validate_blocker_id,
    validate_defect_id,
    validate_evidence_id,
    validate_execution_id,
    validate_finding_id,
    validate_result_id,
    validate_trace_id,
    validate_work_order_id,
    WORK_ORDER_ID_PREFIX,
)


class TestTesterDomainBoundary(unittest.TestCase):
    """
    Unit tests establishing the Tester V1 domain boundary and core identity.
    Validates:
    1. Tester identity and distinguishability
    2. Invalid/malformed identifier rejection
    3. Causal lineage preservation (ManagerTask -> TesterWorkOrder -> TesterExecution -> TesterResult)
    4. Lineage tampering and mismatch detection
    5. Project isolation
    6. Tester responsibility boundary (Tester MAY vs Tester MUST NOT)
    7. Trace and cryptographic provenance
    8. Lifecycle state machine
    9. Evaluator identity (defects, findings, acceptance, uncertainty)
    10. Serialization fidelity roundtrip
    11. Compatibility with existing worker conventions
    """

    def test_tester_identity_prefixes_and_distinguishability(self):
        """Verify domain ID generation, prefix conformance, and clear distinguishability from other subsystems."""
        wo_id = new_work_order_id()
        exec_id = new_execution_id()
        trace_id = new_trace_id()
        res_id = new_result_id()
        def_id = new_defect_id()
        find_id = new_finding_id()
        evid_id = new_evidence_id()
        blk_id = new_blocker_id()

        self.assertTrue(wo_id.startswith(WORK_ORDER_ID_PREFIX))
        self.assertTrue(exec_id.startswith(EXECUTION_ID_PREFIX))
        self.assertTrue(trace_id.startswith(TRACE_ID_PREFIX))
        self.assertTrue(res_id.startswith(RESULT_ID_PREFIX))
        self.assertTrue(def_id.startswith(DEFECT_ID_PREFIX))
        self.assertTrue(find_id.startswith(FINDING_ID_PREFIX))
        self.assertTrue(evid_id.startswith(EVIDENCE_ID_PREFIX))
        self.assertTrue(blk_id.startswith(BLOCKER_ID_PREFIX))

        # Positive identification
        self.assertTrue(is_tester_id(wo_id))
        self.assertTrue(is_tester_id(exec_id))
        self.assertTrue(is_tester_id(trace_id))
        self.assertTrue(is_tester_id(res_id))
        self.assertTrue(is_tester_id(def_id))
        self.assertTrue(is_tester_id(find_id))
        self.assertTrue(is_tester_id(evid_id))
        self.assertTrue(is_tester_id(blk_id))

        # Distinguishability from Manager IDs
        self.assertFalse(is_tester_id("cycle-12345"))
        self.assertFalse(is_tester_id("decision-abcde"))
        self.assertFalse(is_tester_id("plan-98765"))

        # Distinguishability from Researcher IDs
        self.assertFalse(is_tester_id("req-12345"))
        self.assertFalse(is_tester_id("f-abcde"))
        self.assertFalse(is_tester_id("gap-12345"))
        self.assertFalse(is_tester_id("rec-12345"))

        # Distinguishability from Programmer IDs
        self.assertFalse(is_tester_id("pwo-12345"))
        self.assertFalse(is_tester_id("pexec-12345"))
        self.assertFalse(is_tester_id("ptrace-12345"))
        self.assertFalse(is_tester_id("pres-12345"))
        self.assertFalse(is_tester_id("pblk-12345"))

        # Distinguishability from generic and invalid inputs
        self.assertFalse(is_tester_id("task-001"))
        self.assertFalse(is_tester_id("proj-autonomos"))
        self.assertFalse(is_tester_id("worker.tester"))
        self.assertFalse(is_tester_id(""))
        self.assertFalse(is_tester_id(None))

    def test_invalid_identifiers_rejection(self):
        """Verify strict validation errors for malformed or wrong-prefix identifiers."""
        # Work order validation
        with self.assertRaises(InvalidTesterIdError) as ctx:
            validate_work_order_id("pwo-12345")
        self.assertEqual(ctx.exception.expected_prefix, WORK_ORDER_ID_PREFIX)
        self.assertEqual(ctx.exception.code, "INVALID_TESTER_ID")

        with self.assertRaises(InvalidTesterIdError):
            validate_work_order_id("two-")  # empty suffix

        with self.assertRaises(InvalidTesterIdError):
            validate_work_order_id("two-invalid @ id")  # forbidden character

        # Execution validation
        with self.assertRaises(InvalidTesterIdError):
            validate_execution_id("pexec-12345")
        with self.assertRaises(InvalidTesterIdError):
            validate_execution_id("texec-")

        # Trace validation
        with self.assertRaises(InvalidTesterIdError):
            validate_trace_id("ptrace-12345")
        with self.assertRaises(InvalidTesterIdError):
            validate_trace_id("ttrace-")

        # Result validation
        with self.assertRaises(InvalidTesterIdError):
            validate_result_id("pres-12345")
        with self.assertRaises(InvalidTesterIdError):
            validate_result_id("tres-")

        # Defect validation
        with self.assertRaises(InvalidTesterIdError):
            validate_defect_id("bad-defect")
        with self.assertRaises(InvalidTesterIdError):
            validate_defect_id("tdef-")

        # Finding validation
        with self.assertRaises(InvalidTesterIdError):
            validate_finding_id("bad-finding")
        with self.assertRaises(InvalidTesterIdError):
            validate_finding_id("tfind-")

        # Evidence validation
        with self.assertRaises(InvalidTesterIdError):
            validate_evidence_id("bad-evidence")
        with self.assertRaises(InvalidTesterIdError):
            validate_evidence_id("tevid-")

        # Blocker validation
        with self.assertRaises(InvalidTesterIdError):
            validate_blocker_id("pblk-12345")
        with self.assertRaises(InvalidTesterIdError):
            validate_blocker_id("tblk-")

    def test_causal_lineage_preservation_golden_path(self):
        """
        Verify unforgeable lineage flow:
        ManagerTask -> TesterWorkOrder -> TesterExecution -> TesterResult
        """
        # 1. Manager authorizes a Task
        manager_task = Task(
            id="task-eval-auth-01",
            project_id="proj-autonomos",
            title="Evaluate OAuth2 Authentication Layer",
            objective="Verify OAuth2 callback flow, error handling, token refresh, and security assertions.",
            metadata={
                "correlation_id": "corr-root-777",
                "test_scope": ["auth.oauth", "auth.tokens"],
                "acceptance_criteria": [
                    "Valid auth code returns 200 and access token",
                    "Expired auth code returns 400 with invalid_grant",
                ],
            },
        )

        # 2. Construct authorized TesterWorkOrder
        work_order = TesterWorkOrder.from_task(manager_task)
        self.assertTrue(work_order.work_order_id.startswith(WORK_ORDER_ID_PREFIX))
        self.assertEqual(work_order.task_id, "task-eval-auth-01")
        self.assertEqual(work_order.manager_task_id, "task-eval-auth-01")
        self.assertEqual(work_order.project_id, "proj-autonomos")
        self.assertEqual(work_order.correlation_id, "corr-root-777")
        self.assertEqual(work_order.objective, manager_task.objective)
        self.assertEqual(work_order.status, TesterWorkOrderStatus.ASSIGNED)
        self.assertEqual(len(work_order.acceptance_criteria), 2)

        # 3. Spawn TesterExecution session
        execution = work_order.create_execution(worker_id="worker.tester")
        self.assertTrue(execution.execution_id.startswith(EXECUTION_ID_PREFIX))
        self.assertEqual(execution.work_order_id, work_order.work_order_id)
        self.assertEqual(execution.task_id, "task-eval-auth-01")
        self.assertEqual(execution.project_id, "proj-autonomos")
        self.assertEqual(execution.correlation_id, "corr-root-777")
        self.assertEqual(execution.worker_id, "worker.tester")
        self.assertEqual(execution.status, TesterExecutionStatus.INITIALIZED)

        # 4. Record execution trace
        trace_init = execution.create_trace(
            action_type=TesterActionType.INITIALIZE,
            action_details={"step": "inspect_test_scope", "target": "auth.oauth"},
        )
        self.assertTrue(trace_init.trace_id.startswith(TRACE_ID_PREFIX))
        self.assertEqual(trace_init.execution_id, execution.execution_id)
        self.assertEqual(trace_init.work_order_id, work_order.work_order_id)
        self.assertEqual(trace_init.task_id, "task-eval-auth-01")
        self.assertEqual(trace_init.project_id, "proj-autonomos")
        self.assertEqual(trace_init.correlation_id, "corr-root-777")
        self.assertTrue(len(trace_init.checksum) == 64)

        # 5. Transition execution to RUNNING and EVALUATING
        execution.transition_to(TesterExecutionStatus.RUNNING)
        self.assertIsNotNone(execution.started_at)

        execution.transition_to(TesterExecutionStatus.EVALUATING)

        # Record evaluation findings & acceptance verification
        execution.record_acceptance_result(
            criterion_id="ac-01",
            description="Valid auth code returns 200 and access token",
            status=AcceptanceCriterionStatus.PASS,
            evidence_ids=[trace_init.trace_id],
            notes="Verified token exchange returns 200 OK with valid bearer token.",
        )

        execution.record_finding(
            category=FindingCategory.UX,
            title="OAuth Consent Screen Load Latency",
            description="Consent redirect latency is ~850ms, slightly above ideal 500ms threshold.",
            recommendation="Consider pre-warming OAuth client sessions.",
        )

        # 6. Complete and produce TesterResult
        execution.transition_to(TesterExecutionStatus.REPORTING)
        execution.transition_to(TesterExecutionStatus.COMPLETED)
        self.assertIsNotNone(execution.completed_at)

        result = execution.create_result(
            status=TesterResultStatus.SUCCESS,
            summary_for_manager="OAuth2 evaluation completed. All acceptance criteria passed.",
            artifacts=["test-reports/oauth2-eval.json"],
        )
        self.assertTrue(result.result_id.startswith(RESULT_ID_PREFIX))
        self.assertEqual(result.execution_id, execution.execution_id)
        self.assertEqual(result.work_order_id, work_order.work_order_id)
        self.assertEqual(result.task_id, "task-eval-auth-01")
        self.assertEqual(result.project_id, "proj-autonomos")
        self.assertEqual(result.correlation_id, "corr-root-777")
        self.assertEqual(result.status, TesterResultStatus.SUCCESS)
        self.assertTrue(result.is_acceptance_fully_verified())
        self.assertFalse(result.has_defects())

        # 7. Validate lineage integrity
        result.validate_lineage(execution=execution, work_order=work_order)

    def test_lineage_tampering_and_mismatch_detection(self):
        """Verify that any tampering, disconnection, or forgery in the causal chain is rejected."""
        # 1. Reject WorkOrder creation from invalid Task
        task_no_id = Task(id="", project_id="proj-1", title="No ID", objective="Obj")
        with self.assertRaises(TesterLineageError):
            TesterWorkOrder.from_task(task_no_id)

        task_no_proj = Task(id="task-1", project_id="", title="No Proj", objective="Obj")
        with self.assertRaises(TesterLineageError):
            TesterWorkOrder.from_task(task_no_proj)

        # 2. Reject WorkOrder missing critical lineage links
        with self.assertRaises(TesterLineageError):
            TesterWorkOrder(
                work_order_id=new_work_order_id(),
                task_id="",
                project_id="proj-1",
                correlation_id="corr-1",
                objective="Objective",
            )

        with self.assertRaises(TesterLineageError):
            TesterWorkOrder(
                work_order_id=new_work_order_id(),
                task_id="task-1",
                project_id="",
                correlation_id="corr-1",
                objective="Objective",
            )

        with self.assertRaises(TesterLineageError):
            TesterWorkOrder(
                work_order_id=new_work_order_id(),
                task_id="task-1",
                project_id="proj-1",
                correlation_id="",
                objective="Objective",
            )

        # Task ID mismatch
        with self.assertRaises(TesterLineageError):
            TesterWorkOrder(
                work_order_id=new_work_order_id(),
                manager_task_id="task-A",
                task_id="task-B",
                project_id="proj-1",
                correlation_id="corr-1",
                objective="Objective",
            )

        # 3. Reject Result with mismatched execution or work order lineage
        wo = TesterWorkOrder(
            work_order_id=new_work_order_id(),
            task_id="task-root",
            project_id="proj-root",
            correlation_id="corr-root",
            objective="Evaluation objective",
        )
        exec_obj = wo.create_execution()

        # Forged execution ID
        forged_exec_result = TesterResult(
            result_id=new_result_id(),
            execution_id=new_execution_id(),  # mismatched execution ID
            work_order_id=wo.work_order_id,
            task_id=wo.task_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
        )
        with self.assertRaises(TesterLineageError):
            forged_exec_result.validate_lineage(execution=exec_obj, work_order=wo)

        # Forged task ID
        forged_task_result = TesterResult(
            result_id=new_result_id(),
            execution_id=exec_obj.execution_id,
            work_order_id=wo.work_order_id,
            task_id="task-forged",  # mismatched task ID
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
        )
        with self.assertRaises(TesterLineageError):
            forged_task_result.validate_lineage(execution=exec_obj, work_order=wo)

        # Forged work order ID
        forged_wo_result = TesterResult(
            result_id=new_result_id(),
            execution_id=exec_obj.execution_id,
            work_order_id=new_work_order_id(),  # mismatched work order ID
            task_id=wo.task_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
        )
        with self.assertRaises(TesterLineageError):
            forged_wo_result.validate_lineage(execution=exec_obj, work_order=wo)

    def test_project_isolation_enforcement(self):
        """Verify that work orders and executions cannot cross project boundaries."""
        wo_proj1 = TesterWorkOrder(
            work_order_id=new_work_order_id(),
            task_id="task-1",
            project_id="proj-alpha",
            correlation_id="corr-1",
            objective="Evaluate Alpha",
        )
        exec_proj1 = wo_proj1.create_execution()
        res_proj1 = exec_proj1.create_result()

        # Attempting to validate lineage against a ManagerTask from a different project
        task_proj2 = Task(id="task-1", project_id="proj-beta", title="Task Beta", objective="Beta")
        with self.assertRaises(TesterLineageError):
            wo_proj1.validate_lineage(task_proj2)

        # Attempting to validate a result with forged project ID
        res_tampered_project = TesterResult(
            result_id=new_result_id(),
            execution_id=exec_proj1.execution_id,
            work_order_id=wo_proj1.work_order_id,
            task_id=wo_proj1.task_id,
            project_id="proj-beta",  # Cross-project violation
            correlation_id=wo_proj1.correlation_id,
        )
        with self.assertRaises(TesterLineageError):
            res_tampered_project.validate_lineage(execution=exec_proj1, work_order=wo_proj1)

    def test_tester_responsibility_boundary_enforcement(self):
        """
        Verify strict enforcement of Tester responsibility boundaries:
        Tester MAY evaluate, observe, record defects, and report findings.
        Tester MUST NOT modify source code, fix defects, deploy, change requirements, or expand scope.
        """
        # Tester MAY: Allowed capabilities are valid
        for cap in TESTER_ALLOWED_CAPABILITIES:
            self.assertIsInstance(cap, TestingCapability)

        # Tester MUST NOT: Prohibited actions are strictly blocked by TesterBoundaryGuard
        for forbidden in TESTER_FORBIDDEN_ACTIONS:
            with self.assertRaises(TesterBoundaryViolationError) as ctx:
                TesterBoundaryGuard.assert_action_permitted(forbidden)
            self.assertEqual(ctx.exception.code, "TESTER_BOUNDARY_VIOLATION")

        # Specific keyword checks
        with self.assertRaises(TesterBoundaryViolationError):
            TesterBoundaryGuard.assert_action_permitted("EDIT_FILE_IN_PLACE")

        with self.assertRaises(TesterBoundaryViolationError):
            TesterBoundaryGuard.assert_action_permitted("FIX_BUG_IN_MODULE")

        with self.assertRaises(TesterBoundaryViolationError):
            TesterBoundaryGuard.assert_action_permitted("DEPLOY_TO_STAGING")

        with self.assertRaises(TesterBoundaryViolationError):
            TesterBoundaryGuard.assert_action_permitted("MERGE_PULL_REQUEST")

        with self.assertRaises(TesterBoundaryViolationError):
            TesterBoundaryGuard.assert_action_permitted("OVERRIDE_DECISION_OF_MANAGER")

        # Source code write protection
        with self.assertRaises(TesterBoundaryViolationError):
            TesterBoundaryGuard.assert_write_access_permitted("src/auth/service.py", is_artifact_or_report=False)

        # Writing test reports/artifacts is permitted
        try:
            TesterBoundaryGuard.assert_write_access_permitted("reports/test_report.json", is_artifact_or_report=True)
        except TesterBoundaryViolationError:
            self.fail("Tester should be permitted to write test reports/artifacts.")

        # Test scope boundary enforcement
        authorized_scopes = ["auth.oauth", "auth.session"]
        TesterBoundaryGuard.assert_scope_bounded("auth.oauth.token", authorized_scopes)  # In scope
        with self.assertRaises(TesterBoundaryViolationError):
            TesterBoundaryGuard.assert_scope_bounded("billing.stripe", authorized_scopes)  # Out of scope

        # Subjective preferences as mandatory defects enforcement
        with self.assertRaises(TesterBoundaryViolationError):
            TesterBoundaryGuard.assert_objective_evaluation(is_subjective=True, treated_as_mandatory=True)

        # Subjective preferences recorded as non-mandatory observations are permitted
        try:
            TesterBoundaryGuard.assert_objective_evaluation(is_subjective=True, treated_as_mandatory=False)
        except TesterBoundaryViolationError:
            self.fail("Subjective preferences recorded non-mandatorily should be permitted.")

    def test_trace_and_cryptographic_provenance(self):
        """Verify that TesterTrace computes deterministic SHA-256 and bridges into RuntimeEvidence."""
        trace = TesterTrace(
            trace_id=new_trace_id(),
            execution_id=new_execution_id(),
            work_order_id=new_work_order_id(),
            task_id="task-trace-01",
            project_id="proj-core",
            correlation_id="corr-trace-01",
            action_type=TesterActionType.EXECUTE_TEST,
            action_details={"suite": "integration/auth", "exit_code": 0, "assertions": 12},
        )
        self.assertTrue(len(trace.checksum) == 64)

        # Check conversion to RuntimeEvidence
        runtime_ev = trace.to_runtime_evidence()
        self.assertIsInstance(runtime_ev, RuntimeEvidence)
        self.assertEqual(runtime_ev.id, trace.trace_id)
        self.assertEqual(runtime_ev.task_id, "task-trace-01")
        self.assertEqual(runtime_ev.evidence_type, "TESTER_EXECUTE_TEST")
        self.assertEqual(runtime_ev.checksum, trace.checksum)
        self.assertIn("integration/auth", runtime_ev.data)

    def test_execution_lifecycle_state_machine(self):
        """Verify valid operational transitions and rejection of illegal state changes."""
        wo = TesterWorkOrder(
            work_order_id=new_work_order_id(),
            task_id="task-sm-01",
            project_id="proj-sm",
            correlation_id="corr-sm",
            objective="Lifecycle test",
        )
        execution = wo.create_execution()
        self.assertEqual(execution.status, TesterExecutionStatus.INITIALIZED)

        # Legal: INITIALIZED (STARTING) -> RUNNING
        execution.transition_to(TesterExecutionStatus.RUNNING)
        self.assertEqual(execution.status, TesterExecutionStatus.RUNNING)
        self.assertIsNotNone(execution.started_at)

        # Legal: RUNNING -> EVALUATING
        execution.transition_to(TesterExecutionStatus.EVALUATING)
        self.assertEqual(execution.status, TesterExecutionStatus.EVALUATING)

        # Legal: EVALUATING -> BLOCKED
        execution.transition_to(TesterExecutionStatus.BLOCKED, reason="Staging endpoint 503 Service Unavailable")
        self.assertEqual(execution.status, TesterExecutionStatus.BLOCKED)
        self.assertEqual(len(execution.active_blockers), 1)

        # Legal: BLOCKED -> unblock -> RUNNING
        execution.unblock(resolution_notes="Staging service restored")
        self.assertEqual(execution.status, TesterExecutionStatus.RUNNING)
        self.assertEqual(len(execution.active_blockers), 0)

        # Legal: RUNNING -> REPORTING -> COMPLETED
        execution.transition_to(TesterExecutionStatus.REPORTING)
        execution.transition_to(TesterExecutionStatus.COMPLETED)
        self.assertEqual(execution.status, TesterExecutionStatus.COMPLETED)
        self.assertIsNotNone(execution.completed_at)

        # Illegal: COMPLETED is terminal, cannot transition back to RUNNING
        with self.assertRaises(InvalidTesterTransitionError) as ctx:
            execution.transition_to(TesterExecutionStatus.RUNNING)
        self.assertEqual(ctx.exception.code, "INVALID_TESTER_TRANSITION")
        self.assertEqual(ctx.exception.current_status, "COMPLETED")
        self.assertEqual(ctx.exception.target_status, "RUNNING")

        # Illegal: REQUESTED cannot jump directly to COMPLETED without execution
        exec2 = TesterExecution(
            execution_id=new_execution_id(),
            work_order_id=wo.work_order_id,
            task_id=wo.task_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            status=TesterExecutionStatus.REQUESTED,
        )
        with self.assertRaises(InvalidTesterTransitionError):
            exec2.transition_to(TesterExecutionStatus.COMPLETED)

    def test_evaluator_identity_defects_and_uncertainty(self):
        """
        Verify core Evaluator identity:
        - Recording concrete, evidence-backed defects (severity, reproduction, actual vs expected)
        - Reporting evidence-backed UX and performance findings
        - Reporting uncertainty explicitly to Manager
        - Reporting recommendations
        """
        wo = TesterWorkOrder(
            work_order_id=new_work_order_id(),
            task_id="task-eval-01",
            project_id="proj-eval",
            correlation_id="corr-eval",
            objective="Evaluate payment retry behavior",
        )
        execution = wo.create_execution()
        execution.transition_to(TesterExecutionStatus.RUNNING)
        execution.transition_to(TesterExecutionStatus.EVALUATING)

        # Record concrete defect
        defect = execution.record_defect(
            title="Duplicate charge on concurrent retry",
            description="When payment retry is triggered concurrently, two transactions are committed.",
            severity=DefectSeverity.CRITICAL,
            defect_type=DefectType.FUNCTIONAL,
            reproduction_steps=[
                "Initiate payment for cart #123",
                "Inject 1500ms network timeout",
                "Trigger retry while initial request is in-flight",
            ],
            expected_behavior="Idempotency key prevents duplicate transaction.",
            actual_behavior="Two charges of $49.99 appear on ledger.",
            evidence_ids=["tevid-charge-1", "tevid-charge-2"],
            affected_components=["billing.service", "payment.gateway"],
            is_regression=True,
        )
        self.assertEqual(defect.severity, DefectSeverity.CRITICAL)
        self.assertTrue(defect.is_regression)

        # Record evidence-backed UX finding
        ux_finding = execution.record_finding(
            category=FindingCategory.UX,
            title="Spinner lacks cancellation option",
            description="When transaction is pending, user is locked without option to cancel.",
            recommendation="Add cancel button after 5 seconds of pending state.",
        )
        self.assertEqual(ux_finding.category, FindingCategory.UX)

        # Record reported uncertainty
        unc_finding = execution.record_finding(
            category=FindingCategory.UNCERTAINTY,
            title="Payment webhook delivery under heavy load unverified",
            description="Staging environment throttled webhook delivery mock; real-world behavior under 1000 req/sec remains uncertain.",
            is_uncertain=True,
            recommendation="Recommend staging load test with dedicated webhook receiver before general release.",
        )
        self.assertTrue(unc_finding.is_uncertain)

        # Complete and produce result
        execution.transition_to(TesterExecutionStatus.REPORTING)
        execution.transition_to(TesterExecutionStatus.COMPLETED)

        result = execution.create_result(
            status=TesterResultStatus.COMPLETED,
            summary_for_manager="Payment evaluation identified 1 critical defect, 1 UX recommendation, and 1 unverified load uncertainty.",
        )

        self.assertTrue(result.has_defects())
        self.assertTrue(result.has_critical_defects())
        self.assertTrue(result.has_uncertainty())
        self.assertEqual(len(result.defects), 1)
        self.assertEqual(len(result.findings), 2)
        self.assertIn(ux_finding.recommendation, result.recommendations)
        self.assertIn(unc_finding.description, result.uncertainties)

        # Verify WorkerOutput bridge for Manager
        output = result.to_worker_output()
        self.assertIsInstance(output, WorkerOutput)
        self.assertTrue(output.success)  # Evaluation completed successfully
        self.assertEqual(output.metadata["critical_defects_count"], 1)
        self.assertEqual(output.metadata["defects_count"], 1)
        self.assertIn(unc_finding.description, output.metadata["uncertainties"])

    def test_serialization_fidelity_roundtrip(self):
        """Verify complete round-trip dictionary serialization for all Tester contracts."""
        # 1. TesterWorkOrder
        wo = TesterWorkOrder(
            work_order_id="two-11112222",
            task_id="task-roundtrip",
            project_id="proj-roundtrip",
            correlation_id="corr-roundtrip",
            objective="Verify roundtrip serialization",
            instructions=["Step 1", "Step 2"],
            test_scope=["auth", "billing"],
            acceptance_criteria=["Criterion 1", "Criterion 2"],
            environment_details={"env": "staging", "port": 8080},
            time_budget=450,
            iteration_budget=8,
            status=TesterWorkOrderStatus.IN_PROGRESS,
            metadata={"priority": "HIGH"},
        )
        d_wo = wo.to_dict()
        restored_wo = TesterWorkOrder.from_dict(d_wo)
        self.assertEqual(restored_wo.work_order_id, wo.work_order_id)
        self.assertEqual(restored_wo.task_id, wo.task_id)
        self.assertEqual(restored_wo.project_id, wo.project_id)
        self.assertEqual(restored_wo.correlation_id, wo.correlation_id)
        self.assertEqual(restored_wo.objective, wo.objective)
        self.assertEqual(restored_wo.test_scope, wo.test_scope)
        self.assertEqual(len(restored_wo.acceptance_criteria), 2)
        self.assertEqual(restored_wo.time_budget, 450)
        self.assertEqual(restored_wo.iteration_budget, 8)
        self.assertEqual(restored_wo.status, TesterWorkOrderStatus.IN_PROGRESS)
        self.assertEqual(restored_wo.metadata["priority"], "HIGH")

        # 2. TesterTrace
        trace = TesterTrace(
            trace_id="ttrace-12345678",
            execution_id="texec-12345678",
            work_order_id="two-12345678",
            task_id="task-roundtrip",
            project_id="proj-roundtrip",
            correlation_id="corr-roundtrip",
            action_type=TesterActionType.OBSERVE,
            action_details={"metric": "latency", "value_ms": 120},
        )
        d_trace = trace.to_dict()
        restored_trace = TesterTrace.from_dict(d_trace)
        self.assertEqual(restored_trace.trace_id, trace.trace_id)
        self.assertEqual(restored_trace.action_type, TesterActionType.OBSERVE)
        self.assertEqual(restored_trace.checksum, trace.checksum)
        self.assertEqual(restored_trace.action_details["value_ms"], 120)

        # 3. TesterEvidence
        evid = TesterEvidence(
            evidence_id="tevid-12345678",
            evidence_type="HTTP_RESPONSE",
            data='{"status": 200, "token": "jwt-token-xyz"}',
            metadata={"source": "curl"},
        )
        d_evid = evid.to_dict()
        restored_evid = TesterEvidence.from_dict(d_evid)
        self.assertEqual(restored_evid.evidence_id, evid.evidence_id)
        self.assertEqual(restored_evid.evidence_type, "HTTP_RESPONSE")
        self.assertEqual(restored_evid.checksum, evid.checksum)

        # 4. AcceptanceCriterionResult
        ac = AcceptanceCriterionResult(
            criterion_id="ac-001",
            description="Check token exchange",
            status=AcceptanceCriterionStatus.PASS,
            evidence_ids=["tevid-12345678"],
            notes="Passed on staging",
        )
        d_ac = ac.to_dict()
        restored_ac = AcceptanceCriterionResult.from_dict(d_ac)
        self.assertEqual(restored_ac.criterion_id, "ac-001")
        self.assertEqual(restored_ac.status, AcceptanceCriterionStatus.PASS)
        self.assertEqual(restored_ac.evidence_ids, ["tevid-12345678"])

        # 5. TesterDefect
        defect = TesterDefect(
            defect_id="tdef-12345678",
            work_order_id="two-12345678",
            title="Memory leak in worker loop",
            description="Process memory grows unbounded during 100 consecutive requests.",
            severity=DefectSeverity.HIGH,
            defect_type=DefectType.PERFORMANCE,
            execution_id="texec-12345678",
            reproduction_steps=["Run 100 requests", "Observe heap"],
            expected_behavior="Stable heap < 200MB",
            actual_behavior="Heap reaches 1.2GB",
            evidence_ids=["tevid-heap-log"],
            affected_components=["worker.runtime"],
            is_regression=False,
        )
        d_defect = defect.to_dict()
        restored_defect = TesterDefect.from_dict(d_defect)
        self.assertEqual(restored_defect.defect_id, defect.defect_id)
        self.assertEqual(restored_defect.severity, DefectSeverity.HIGH)
        self.assertEqual(restored_defect.defect_type, DefectType.PERFORMANCE)
        self.assertEqual(restored_defect.affected_components, ["worker.runtime"])

        # 6. TesterFinding
        finding = TesterFinding(
            finding_id="tfind-12345678",
            category=FindingCategory.RECOMMENDATION,
            title="Cache response headers",
            description="Static assets do not include Cache-Control headers.",
            recommendation="Add Cache-Control: max-age=3600 for static assets.",
        )
        d_finding = finding.to_dict()
        restored_finding = TesterFinding.from_dict(d_finding)
        self.assertEqual(restored_finding.finding_id, finding.finding_id)
        self.assertEqual(restored_finding.category, FindingCategory.RECOMMENDATION)
        self.assertEqual(restored_finding.recommendation, finding.recommendation)

        # 7. TesterBlocker
        blocker = TesterBlocker(
            blocker_id="tblk-12345678",
            work_order_id="two-12345678",
            category=TesterBlockerCategory.ENVIRONMENT,
            description="Database credentials invalid",
            severity=TesterBlockerSeverity.CRITICAL,
            required_decision="Provide updated test credentials",
        )
        d_blocker = blocker.to_dict()
        restored_blocker = TesterBlocker.from_dict(d_blocker)
        self.assertEqual(restored_blocker.blocker_id, blocker.blocker_id)
        self.assertEqual(restored_blocker.category, TesterBlockerCategory.ENVIRONMENT)
        self.assertEqual(restored_blocker.severity, TesterBlockerSeverity.CRITICAL)

        # 8. TesterExecution
        execution = TesterExecution(
            execution_id="texec-12345678",
            work_order_id="two-12345678",
            task_id="task-roundtrip",
            project_id="proj-roundtrip",
            correlation_id="corr-roundtrip",
            worker_id="worker.tester",
            status=TesterExecutionStatus.RUNNING,
            traces=[trace],
            defects=[defect],
            findings=[finding],
            acceptance_results=[ac],
            blockers=[blocker],
            metadata={"test_run": 42},
        )
        d_exec = execution.to_dict()
        restored_exec = TesterExecution.from_dict(d_exec)
        self.assertEqual(restored_exec.execution_id, execution.execution_id)
        self.assertEqual(restored_exec.status, TesterExecutionStatus.RUNNING)
        self.assertEqual(len(restored_exec.traces), 1)
        self.assertEqual(len(restored_exec.defects), 1)
        self.assertEqual(len(restored_exec.findings), 1)
        self.assertEqual(len(restored_exec.acceptance_results), 1)
        self.assertEqual(len(restored_exec.blockers), 1)

        # 9. TesterResult
        result = TesterResult(
            result_id="tres-12345678",
            execution_id="texec-12345678",
            work_order_id="two-12345678",
            task_id="task-roundtrip",
            project_id="proj-roundtrip",
            correlation_id="corr-roundtrip",
            status=TesterResultStatus.COMPLETED,
            summary_for_manager="Serialization test completed successfully.",
            defects=[defect],
            findings=[finding],
            acceptance_results=[ac],
            recommendations=["Add caching"],
            uncertainties=["Concurrency unverified"],
            blockers=["DB credentials invalid"],
            artifacts=["reports/test-summary.json"],
        )
        d_res = result.to_dict()
        restored_res = TesterResult.from_dict(d_res)
        self.assertEqual(restored_res.result_id, result.result_id)
        self.assertEqual(restored_res.status, TesterResultStatus.COMPLETED)
        self.assertEqual(len(restored_res.defects), 1)
        self.assertEqual(len(restored_res.findings), 1)
        self.assertEqual(len(restored_res.acceptance_results), 1)
        self.assertEqual(restored_res.recommendations, ["Add caching"])
        self.assertEqual(restored_res.uncertainties, ["Concurrency unverified"])
        self.assertEqual(restored_res.blockers, ["DB credentials invalid"])

    def test_compatibility_with_existing_worker_conventions(self):
        """Verify interoperability with AutonomOS base errors, Task, and WorkerOutput."""
        # 1. Error hierarchy inherits from AutonomOSError
        err = InvalidTesterIdError("execution_id", "bad-id", "texec-")
        self.assertIsInstance(err, TesterError)
        self.assertIsInstance(err, AutonomOSError)
        d_err = err.to_dict()
        self.assertEqual(d_err["error"], "INVALID_TESTER_ID")
        self.assertIn("bad-id", d_err["message"])

        # Boundary violation error inherits from AutonomOSError
        boundary_err = TesterBoundaryViolationError("MODIFY_SOURCE_CODE", "Tester is an evaluator.")
        self.assertIsInstance(boundary_err, TesterError)
        self.assertIsInstance(boundary_err, AutonomOSError)
        self.assertEqual(boundary_err.to_dict()["error"], "TESTER_BOUNDARY_VIOLATION")

        # 2. WorkerOutput conforms to runtime expectations
        res = TesterResult(
            result_id="tres-99999999",
            execution_id="texec-99999999",
            work_order_id="two-99999999",
            task_id="task-99",
            project_id="proj-99",
            correlation_id="corr-99",
            status=TesterResultStatus.SUCCESS,
            summary_for_manager="QA evaluation passed with zero defects.",
        )
        worker_output = res.to_worker_output()
        self.assertIsInstance(worker_output, WorkerOutput)
        self.assertEqual(worker_output.status, "COMPLETED")
        self.assertTrue(worker_output.success)
        self.assertEqual(worker_output.result["tester_status"], "SUCCESS")
        self.assertEqual(worker_output.result["defects_count"], 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from datetime import datetime, timezone
import os
import shutil
import tempfile
from typing import Any, Callable, Optional
import unittest
from unittest.mock import MagicMock

from core.enums import RiskLevel, TaskStatus
from core.models import Task, WorkerOutput
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.acceptance_evaluator import AcceptanceCriteriaEvaluator
from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.blocker import ProgrammerBlocker
from core.programmer.contracts.cancellation import ProgrammerCancellation
from core.programmer.contracts.cancellation_handler import ProgrammerCancellationHandler
from core.programmer.contracts.coding_agent import (
    CodingAgentBackend,
    CodingAgentCancellationRequest,
    CodingAgentCancellationResult,
    CodingAgentEvent,
    CodingAgentExecutionStatus,
    CodingAgentRequest,
    CodingAgentResult,
    MockCodingAgentBackend,
)
from core.programmer.contracts.command_record import CommandExecutionRecord
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.evidence import ProgrammerEvidence
from core.programmer.contracts.correction_loop import (
    BoundedCorrectionLoop,
    CorrectionIterationRecord,
    CorrectionLoopResult,
    CorrectionPromptBuilder,
)
from core.programmer.contracts.diff_verifier import (
    DiffScopeVerifier,
    DiffVerification,
    UnauthorizedChange,
)
from core.programmer.contracts.escalation import (
    EscalationCoordinator,
    EscalationStatus,
    ManagerEscalationResponse,
    ManagerEscalationResponseAction,
    ProgrammerEscalation,
    ProgrammerEscalationCategory,
)
from core.programmer.contracts.evidence_aggregator import VerificationEvidenceAggregator
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.executor import (
    ControlledExecutionOutcome,
    ControlledProgrammerExecutor,
)
from core.programmer.contracts.failure import (
    ProgrammerFailure,
    determine_default_disposition,
)
from core.programmer.contracts.identifiers import (
    new_diff_verification_id,
    new_escalation_id,
    new_execution_id,
    new_failure_id,
    new_recovery_decision_id,
    new_result_id,
    new_retry_attempt_id,
    new_verification_check_id,
    new_verification_evidence_id,
    new_work_order_id,
    validate_escalation_id,
    validate_execution_id,
    validate_failure_id,
    validate_retry_attempt_id,
    validate_work_order_id,
)
from core.programmer.contracts.manager_bridge import ProgrammerManagerBridge
from core.programmer.contracts.programmer import Programmer
from core.programmer.contracts.provisioner import WorkspaceProvisioner
from core.programmer.contracts.recovery import (
    FailureRecoveryController,
    RecoveryDecision,
    RecoveryPolicy,
)
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.retry import (
    RetryAttemptRecord,
    RetryCoordinator,
    RetryPolicy,
)
from core.programmer.contracts.verification import (
    AcceptanceResult,
    VerificationCheck,
    VerificationEvidence,
    VerificationSummary,
)
from core.programmer.contracts.verification_runner import (
    VerificationRunner,
    VerificationRunnerResult,
)
from core.programmer.contracts.watchdog import (
    ExecutionMonitoringRecord,
    ExecutionWatchdog,
    SimulatedClock,
    WatchdogHeartbeatConfig,
    WatchdogTimeoutConfig,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import (
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    AcceptanceCriterionType,
    AcceptanceStatus,
    ExecutionMonitoringState,
    FailureSourceType,
    ProgrammerActionType,
    ProgrammerBlockerCategory,
    ProgrammerBlockerSeverity,
    ProgrammerEvidenceType,
    ProgrammerExecutionStatus,
    ProgrammerFailureCategory,
    ProgrammerFailureSeverity,
    ProgrammerResultStatus,
    ProgrammerWorkOrderStatus,
    RecoveryDisposition,
    VerificationCheckType,
    VerificationEvidenceSourceType,
    VerificationStatus,
    VerificationSummaryStatus,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ==============================================================================
# Controllable Mock Coding Agent Backend
# ==============================================================================


class SupervisedMockBackend(MockCodingAgentBackend):
    """
    Configurable fake Cline runtime client for deterministic supervision and recovery testing.
    """

    def __init__(
        self,
        startup_failures_remaining: int = 0,
        crashes_remaining: int = 0,
        fail_with_category: Optional[ProgrammerFailureCategory] = None,
        custom_output: str = "Implementation complete",
    ) -> None:
        super().__init__()
        self.startup_failures_remaining = startup_failures_remaining
        self.crashes_remaining = crashes_remaining
        self.fail_with_category = fail_with_category
        self.custom_output = custom_output
        self.attempts_seen = 0
        self.cancellation_requested = False
        self.last_request: Optional[CodingAgentRequest] = None

    def execute(
        self,
        request: CodingAgentRequest,
        event_handler: Optional[Callable[[CodingAgentEvent], None]] = None,
    ) -> CodingAgentResult:
        self.attempts_seen += 1
        self.last_request = request

        if self.cancellation_requested:
            return CodingAgentResult(
                execution_id=request.execution_id,
                work_order_id=request.work_order_id,
                status=CodingAgentExecutionStatus.CANCELLED,
                output_text="Execution cancelled on backend",
            )

        if self.startup_failures_remaining > 0:
            self.startup_failures_remaining -= 1
            return CodingAgentResult(
                execution_id=request.execution_id,
                work_order_id=request.work_order_id,
                status=CodingAgentExecutionStatus.FAILED,
                error_message="Failed to start Cline agent process: address already in use",
            )

        if self.crashes_remaining > 0:
            self.crashes_remaining -= 1
            return CodingAgentResult(
                execution_id=request.execution_id,
                work_order_id=request.work_order_id,
                status=CodingAgentExecutionStatus.FAILED,
                error_message="Cline process terminated unexpectedly: exit code 139 (SIGSEGV)",
            )

        return CodingAgentResult(
            execution_id=request.execution_id,
            work_order_id=request.work_order_id,
            status=CodingAgentExecutionStatus.COMPLETED,
            output_text=self.custom_output,
        )

    def cancel(
        self,
        cancellation_request: CodingAgentCancellationRequest,
    ) -> CodingAgentCancellationResult:
        self.cancellation_requested = True
        return CodingAgentCancellationResult(
            execution_id=cancellation_request.execution_id,
            work_order_id=cancellation_request.work_order_id,
            cancelled=True,
            reason="Cline runtime agent terminated cleanly upon request.",
        )


# ==============================================================================
# Phase 5.7 Integration Test Suite
# ==============================================================================


class TestProgrammerSupervisionAndRecoveryIntegration(unittest.TestCase):
    """
    PROGRAMMER V1 — PHASE 5.7: Failure Recovery & Supervision Integration Tests.
    
    Validates the end-to-end architecture:
    Manager -> ProgrammerWorkOrder -> ExecutionContext -> Cline ->
    execution monitoring -> failure classification -> recovery policy ->
    retry/correction/escalation -> verification -> ProgrammerResult.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="prog_p57_test_")
        self.workspace_root = os.path.join(self.temp_dir, "workspace")
        os.makedirs(os.path.join(self.workspace_root, "src"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_root, "tests"), exist_ok=True)

        self.project_id = "proj-p57"
        self.manager_task_id = "mtask-rec-57"
        self.correlation_id = "corr-rec-57"
        self.bridge = ProgrammerManagerBridge()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_work_order(
        self,
        objective: str = "Implement supervised payment provider",
        allowed_paths: Optional[list[str]] = None,
        writable_paths: Optional[list[str]] = None,
        allowed_commands: Optional[list[AllowedCommand]] = None,
        iteration_budget: int = 3,
        time_budget: int = 120,
        risk_level: RiskLevel = RiskLevel.MEDIUM,
    ) -> ProgrammerWorkOrder:
        return ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            manager_task_id=self.manager_task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective=objective,
            allowed_paths=allowed_paths or ["/workspace/src", "/workspace/tests"],
            writable_paths=writable_paths or ["/workspace/src", "/workspace/tests"],
            allowed_commands=allowed_commands or [
                AllowedCommand(command="pytest", description="Run pytest test suite")
            ],
            acceptance_criteria=[
                AcceptanceCriterion(
                    criterion_id="ac-1",
                    description="Unit tests pass",
                    criterion_type=AcceptanceCriterionType.TEST_PASS,
                )
            ],
            iteration_budget=iteration_budget,
            time_budget=time_budget,
            risk_level=risk_level,
        )

    def _create_execution(self, work_order: ProgrammerWorkOrder) -> ProgrammerExecution:
        return ProgrammerExecution(
            execution_id=new_execution_id(),
            work_order_id=work_order.work_order_id,
            task_id=work_order.manager_task_id,
            project_id=work_order.project_id,
            correlation_id=work_order.correlation_id,
            status=ProgrammerExecutionStatus.RUNNING,
        )

    def _make_mock_diff_verifier(
        self,
        files_changed: Optional[list[str]] = None,
        files_created: Optional[list[str]] = None,
        files_deleted: Optional[list[str]] = None,
        unauthorized_changes: Optional[list[UnauthorizedChange]] = None,
        scope_status: VerificationStatus = VerificationStatus.PASS,
    ):
        """Creates a mock DiffScopeVerifier dynamically matching runtime execution_id and work_order_id."""
        mock = MagicMock()

        def _verify_workspace(workspace, baseline_snapshot=None, work_order=None, execution_id=""):
            wo_id = getattr(work_order, "work_order_id", "") or str(work_order or "")
            return DiffVerification(
                verification_id=new_diff_verification_id(),
                execution_id=execution_id or "pexec-default",
                work_order_id=wo_id,
                files_changed=list(files_changed if files_changed is not None else ["/workspace/src/app.py"]),
                files_created=list(files_created or []),
                files_deleted=list(files_deleted or []),
                unauthorized_changes=list(unauthorized_changes or []),
                scope_status=scope_status,
            )

        mock.verify_workspace.side_effect = _verify_workspace
        return mock

    # -------------------------------------------------------------------------
    # Scenario 1: Cline starts successfully
    # -------------------------------------------------------------------------
    def test_01_cline_starts_successfully(self) -> None:
        """Cline starts cleanly, produces code, verification passes -> COMPLETED."""
        wo = self._create_work_order()
        backend = SupervisedMockBackend()

        def pass_runner_factory(ctx: ProgrammerExecutionContext):
            mock_r = MagicMock()
            ev_id = new_verification_evidence_id()
            ev = VerificationEvidence(
                evidence_id=ev_id,
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                source_type=VerificationEvidenceSourceType.TEST_RUNNER,
                description="Pytest passed",
                data={"command": "pytest", "exit_code": 0},
                is_agent_claim=False,
            )
            c = VerificationCheck(
                check_id=new_verification_check_id(),
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                check_type=VerificationCheckType.TEST,
                command="pytest",
                status=VerificationStatus.PASS,
                exit_code=0,
                evidence=[ev_id],
            )
            mock_r.run.return_value = VerificationRunnerResult(
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                checks=[c],
                evidence=[ev],
            )
            return mock_r

        programmer = Programmer(
            backend=backend,
            bridge=self.bridge,
            verification_runner_factory=pass_runner_factory,
            diff_verifier=self._make_mock_diff_verifier(),
        )

        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        self.assertEqual(result.status, ProgrammerResultStatus.COMPLETED)
        self.assertEqual(backend.attempts_seen, 1)
        self.assertTrue(result.is_success())

    # -------------------------------------------------------------------------
    # Scenario 2: Cline startup fails once and retry succeeds
    # -------------------------------------------------------------------------
    def test_02_cline_startup_fails_once_and_retry_succeeds(self) -> None:
        """Initial attempt fails with AGENT_STARTUP; retry coordinator authorizes retry 2 -> succeeds."""
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)
        backend = SupervisedMockBackend(startup_failures_remaining=1)
        retry_coord = RetryCoordinator()
        retry_policy = RetryPolicy(max_retries=2, initial_delay_seconds=0.01)
        recovery_policy = RecoveryPolicy(max_startup_retries=2)
        controller = FailureRecoveryController(default_policy=recovery_policy)

        # Build context and request
        ws = ProgrammerWorkspace(
            workspace_id="pws-test-02",
            project_id=wo.project_id,
            work_order_id=wo.work_order_id,
            root_path=self.workspace_root,
            allowed_paths=["/workspace/src", "/workspace/tests"],
            writable_paths=["/workspace/src", "/workspace/tests"],
        )
        ctx = ProgrammerExecutionContext(
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            workspace_id=ws.workspace_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            workspace=ws,
            work_order=wo,
            execution=exec_sess,
        )
        req = CodingAgentRequest(
            request_id="cbreq-1",
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            prompt="Implement feature",
            execution_context=ctx,
        )
        res1 = backend.execute(req)
        self.assertEqual(res1.status, CodingAgentExecutionStatus.FAILED)

        # Failure classification
        failure = ProgrammerFailure(
            failure_id=new_failure_id(),
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            category=ProgrammerFailureCategory.AGENT_STARTUP,
            severity=ProgrammerFailureSeverity.HIGH,
            source=FailureSourceType.AGENT,
            message=res1.error_message,
            retryable=True,
        )

        # Recovery decision
        decision = controller.evaluate_recovery(failure, policy=recovery_policy, work_order=wo)
        self.assertEqual(decision.action, RecoveryDisposition.RETRY)

        # Execute backoff & record retry attempt
        delay = retry_coord.execute_backoff(
            attempt_number=1,
            policy=retry_policy,
            sleep_func=lambda s: None,
        )
        rec = retry_coord.record_retry_attempt(
            failure=failure,
            execution=exec_sess,
            work_order=wo,
            delay_seconds=delay,
        )
        self.assertTrue(rec.attempt_id.startswith("pretry-"))
        validate_retry_attempt_id(rec.attempt_id)

        # Attempt 2: Startup succeeds
        res2 = backend.execute(req)
        self.assertEqual(res2.status, CodingAgentExecutionStatus.COMPLETED)
        self.assertEqual(backend.attempts_seen, 2)
        self.assertEqual(retry_coord.get_total_retry_count(exec_sess.execution_id), 1)

    # -------------------------------------------------------------------------
    # Scenario 3: Cline crashes repeatedly and retry budget is exhausted
    # -------------------------------------------------------------------------
    def test_03_cline_crashes_repeatedly_and_retry_budget_exhausted(self) -> None:
        """Cline crashes repeatedly until max_retries reached; controller dispatches FAIL."""
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)
        retry_coord = RetryCoordinator()
        retry_policy = RetryPolicy(max_retries=2, total_retry_budget=2, initial_delay_seconds=0.01)
        recovery_policy = RecoveryPolicy(max_execution_retries=2, max_total_recovery_attempts=2)
        controller = FailureRecoveryController(default_policy=recovery_policy)

        # 2 allowable crashes
        for attempt in [1, 2]:
            failure = ProgrammerFailure(
                failure_id=new_failure_id(),
                execution_id=exec_sess.execution_id,
                work_order_id=wo.work_order_id,
                category=ProgrammerFailureCategory.AGENT_CRASH,
                severity=ProgrammerFailureSeverity.HIGH,
                source=FailureSourceType.AGENT,
                message="Segmentation fault",
                retryable=True,
            )
            decision = controller.evaluate_recovery(failure, policy=recovery_policy, work_order=wo)
            self.assertEqual(decision.action, RecoveryDisposition.RETRY)
            retry_coord.record_retry_attempt(failure, exec_sess, wo, attempt, 0.01)

        # 3rd crash: budget exhausted
        crash3 = ProgrammerFailure(
            failure_id=new_failure_id(),
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            category=ProgrammerFailureCategory.AGENT_CRASH,
            severity=ProgrammerFailureSeverity.HIGH,
            source=FailureSourceType.AGENT,
            message="Segmentation fault again",
            retryable=True,
        )
        can_retry, reason = retry_coord.can_retry(crash3, exec_sess, wo, retry_policy)
        self.assertFalse(can_retry)
        self.assertIn("exhausted", reason.lower())

        decision3 = controller.evaluate_recovery(crash3, policy=recovery_policy, work_order=wo)
        self.assertEqual(decision3.action, RecoveryDisposition.FAIL)

    # -------------------------------------------------------------------------
    # Scenario 4: Cline becomes unresponsive and watchdog detects it
    # -------------------------------------------------------------------------
    def test_04_cline_unresponsive_watchdog_detects(self) -> None:
        """Watchdog detects heartbeat silence exceeding threshold -> emits AGENT_HUNG."""
        clock = SimulatedClock()
        watchdog = ExecutionWatchdog(clock=clock.now)
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)

        # Start monitoring
        record = watchdog.start_monitoring(
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            timeout_config=WatchdogTimeoutConfig(execution_timeout_seconds=300),
            heartbeat_config=WatchdogHeartbeatConfig(inactivity_threshold_seconds=10, hung_threshold_seconds=30),
        )
        self.assertEqual(record.current_state, ExecutionMonitoringState.ACTIVE)

        # Advance clock by 35 seconds without heartbeat or activity
        clock.advance(35.0)
        failures = watchdog.check_health(execution_id=exec_sess.execution_id)

        rec = watchdog.get_record(exec_sess.execution_id)
        self.assertEqual(rec.current_state, ExecutionMonitoringState.HUNG_SUSPECTED)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].category, ProgrammerFailureCategory.AGENT_HUNG)
        validate_failure_id(failures[0].failure_id)

    # -------------------------------------------------------------------------
    # Scenario 5: Execution timeout occurs
    # -------------------------------------------------------------------------
    def test_05_execution_timeout_occurs(self) -> None:
        """Execution duration exceeds time_budget -> watchdog detects TIMED_OUT & handler acts."""
        clock = SimulatedClock()
        watchdog = ExecutionWatchdog(clock=clock.now)
        wo = self._create_work_order(time_budget=60)
        exec_sess = self._create_execution(wo)

        watchdog.start_monitoring(
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            timeout_config=WatchdogTimeoutConfig(execution_timeout_seconds=60),
        )
        clock.advance(65.0)

        failures = watchdog.check_health(execution_id=exec_sess.execution_id)
        rec = watchdog.get_record(exec_sess.execution_id)
        self.assertEqual(rec.current_state, ExecutionMonitoringState.TIMED_OUT)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].category, ProgrammerFailureCategory.TIMEOUT)

        # Process timeout via cancellation handler
        handler = ProgrammerCancellationHandler()
        timeout_fail = handler.handle_timeout(exec_sess, timeout_seconds=60.0)
        self.assertEqual(timeout_fail.category, ProgrammerFailureCategory.TIMEOUT)
        self.assertEqual(exec_sess.status, ProgrammerExecutionStatus.FAILED)

    # -------------------------------------------------------------------------
    # Scenario 6: Manager cancellation propagates correctly
    # -------------------------------------------------------------------------
    def test_06_manager_cancellation_propagates_correctly(self) -> None:
        """Manager cancellation propagates through handler to Cline backend with provenance."""
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)
        backend = SupervisedMockBackend()
        handler = ProgrammerCancellationHandler()

        cancellation = handler.cancel_execution(
            execution=exec_sess,
            requested_by="manager",
            reason="Priority shifted to hotfix",
            backend=backend,
        )

        self.assertTrue(cancellation.confirmed)
        self.assertTrue(backend.cancellation_requested)
        self.assertEqual(exec_sess.status, ProgrammerExecutionStatus.CANCELLED)
        self.assertEqual(exec_sess.cancellation.requested_by, "manager")
        self.assertIn("Priority shifted", exec_sess.cancellation.reason)

    # -------------------------------------------------------------------------
    # Scenario 7: Verification fails and bounded correction succeeds
    # -------------------------------------------------------------------------
    def test_07_verification_fails_and_bounded_correction_succeeds(self) -> None:
        """Implementation fails verification on turn 1, bounded correction fixes on turn 2 -> COMPLETED."""
        wo = self._create_work_order(iteration_budget=3)
        turn_counter = {"turn": 0}

        def dynamic_runner_factory(ctx: ProgrammerExecutionContext):
            mock_r = MagicMock()
            turn_counter["turn"] += 1
            if turn_counter["turn"] == 1:
                ev_id = new_verification_evidence_id()
                ev = VerificationEvidence(
                    evidence_id=ev_id,
                    execution_id=ctx.execution_id,
                    work_order_id=ctx.work_order_id,
                    source_type=VerificationEvidenceSourceType.TEST_RUNNER,
                    description="Pytest failed",
                    data={"command": "pytest", "exit_code": 1},
                    is_agent_claim=False,
                )
                chk = VerificationCheck(
                    check_id=new_verification_check_id(),
                    execution_id=ctx.execution_id,
                    work_order_id=ctx.work_order_id,
                    check_type=VerificationCheckType.TEST,
                    command="pytest",
                    status=VerificationStatus.FAIL,
                    exit_code=1,
                    output_snippet="AssertionError: expected 200 got 500",
                    evidence=[ev_id],
                )
                evidence_list = [ev]
            else:
                ev_id = new_verification_evidence_id()
                ev = VerificationEvidence(
                    evidence_id=ev_id,
                    execution_id=ctx.execution_id,
                    work_order_id=ctx.work_order_id,
                    source_type=VerificationEvidenceSourceType.TEST_RUNNER,
                    description="Pytest passed",
                    data={"command": "pytest", "exit_code": 0},
                    is_agent_claim=False,
                )
                chk = VerificationCheck(
                    check_id=new_verification_check_id(),
                    execution_id=ctx.execution_id,
                    work_order_id=ctx.work_order_id,
                    check_type=VerificationCheckType.TEST,
                    command="pytest",
                    status=VerificationStatus.PASS,
                    exit_code=0,
                    evidence=[ev_id],
                )
                evidence_list = [ev]
            mock_r.run.return_value = VerificationRunnerResult(
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                checks=[chk],
                evidence=evidence_list,
            )
            return mock_r

        programmer = Programmer(
            backend=SupervisedMockBackend(),
            bridge=self.bridge,
            verification_runner_factory=dynamic_runner_factory,
            diff_verifier=self._make_mock_diff_verifier(),
        )

        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        self.assertEqual(result.status, ProgrammerResultStatus.COMPLETED)
        self.assertEqual(turn_counter["turn"], 2)
        self.assertTrue(result.is_success())

    # -------------------------------------------------------------------------
    # Scenario 8: Verification repeatedly fails and correction budget is exhausted
    # -------------------------------------------------------------------------
    def test_08_verification_fails_repeatedly_and_correction_budget_exhausted(self) -> None:
        """Verification fails consistently across iteration budget -> terminates as FAILED."""
        wo = self._create_work_order(iteration_budget=2)

        def failing_runner_factory(ctx: ProgrammerExecutionContext):
            mock_r = MagicMock()
            ev_id = new_verification_evidence_id()
            ev = VerificationEvidence(
                evidence_id=ev_id,
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                source_type=VerificationEvidenceSourceType.TEST_RUNNER,
                description="Pytest failed",
                data={"command": "pytest", "exit_code": 1},
                is_agent_claim=False,
            )
            chk = VerificationCheck(
                check_id=new_verification_check_id(),
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                check_type=VerificationCheckType.TEST,
                command="pytest",
                status=VerificationStatus.FAIL,
                exit_code=1,
                evidence=[ev_id],
            )
            mock_r.run.return_value = VerificationRunnerResult(
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                checks=[chk],
                evidence=[ev],
            )
            return mock_r

        programmer = Programmer(
            backend=SupervisedMockBackend(),
            bridge=self.bridge,
            verification_runner_factory=failing_runner_factory,
            diff_verifier=self._make_mock_diff_verifier(),
        )

        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        self.assertEqual(result.status, ProgrammerResultStatus.FAILED)
        self.assertFalse(result.is_success())

    # -------------------------------------------------------------------------
    # Scenario 9: Permission denial causes escalation
    # -------------------------------------------------------------------------
    def test_09_permission_denial_causes_escalation(self) -> None:
        """Permission violation is non-retryable; dispatches ESCALATE and blocks execution."""
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)
        controller = FailureRecoveryController()
        escalation_coord = EscalationCoordinator()

        perm_failure = ProgrammerFailure(
            failure_id=new_failure_id(),
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            category=ProgrammerFailureCategory.PERMISSION,
            severity=ProgrammerFailureSeverity.HIGH,
            source=FailureSourceType.COMMAND,
            message="Operation not permitted: sudo apt-get install",
            retryable=False,
        )

        decision = controller.evaluate_recovery(perm_failure, work_order=wo)
        self.assertEqual(decision.action, RecoveryDisposition.ESCALATE)

        escalation = escalation_coord.create_escalation(
            execution=exec_sess,
            work_order=wo,
            category=ProgrammerEscalationCategory.PERMISSION,
            requested_decision="Require root privileges to install system package.",
        )
        self.assertEqual(exec_sess.status, ProgrammerExecutionStatus.BLOCKED)
        self.assertEqual(escalation.status, EscalationStatus.PENDING)

    # -------------------------------------------------------------------------
    # Scenario 10: Scope violation causes escalation/blocking
    # -------------------------------------------------------------------------
    def test_10_scope_violation_causes_escalation_and_blocking(self) -> None:
        """File modified outside authorized scope -> SCOPE failure -> ESCALATE / BLOCK."""
        wo = self._create_work_order(allowed_paths=["/workspace/src"])
        exec_sess = self._create_execution(wo)
        controller = FailureRecoveryController()
        escalation_coord = EscalationCoordinator()

        scope_failure = ProgrammerFailure(
            failure_id=new_failure_id(),
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            category=ProgrammerFailureCategory.SCOPE,
            severity=ProgrammerFailureSeverity.HIGH,
            source=FailureSourceType.WORKSPACE,
            message="Unauthorized modification to /etc/hosts",
            retryable=False,
        )

        decision = controller.evaluate_recovery(scope_failure, work_order=wo)
        self.assertIn(decision.action, (RecoveryDisposition.ESCALATE, RecoveryDisposition.BLOCK))

        escalation = escalation_coord.create_escalation(
            execution=exec_sess,
            work_order=wo,
            category=ProgrammerEscalationCategory.SCOPE,
            requested_decision="Expand writable paths to include /etc/hosts.",
        )
        self.assertEqual(exec_sess.status, ProgrammerExecutionStatus.BLOCKED)
        self.assertEqual(escalation.category, ProgrammerEscalationCategory.SCOPE)

    # -------------------------------------------------------------------------
    # Scenario 11: Required dependency is unavailable
    # -------------------------------------------------------------------------
    def test_11_required_dependency_unavailable(self) -> None:
        """Missing library/binary triggers DEPENDENCY failure and escalates to Manager."""
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)
        controller = FailureRecoveryController()
        escalation_coord = EscalationCoordinator()

        dep_failure = ProgrammerFailure(
            failure_id=new_failure_id(),
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            category=ProgrammerFailureCategory.DEPENDENCY,
            severity=ProgrammerFailureSeverity.HIGH,
            source=FailureSourceType.COMMAND,
            message="Binary 'protoc' not found on PATH",
            retryable=False,
        )

        decision = controller.evaluate_recovery(dep_failure, work_order=wo)
        self.assertIn(decision.action, (RecoveryDisposition.BLOCK, RecoveryDisposition.ESCALATE))

        escalation = escalation_coord.create_escalation(
            execution=exec_sess,
            work_order=wo,
            category=ProgrammerEscalationCategory.DEPENDENCY,
            requested_decision="Need protobuf compiler pre-installed.",
            suggested_options=["Install protoc via brew/apt", "Use pre-compiled python stubs"],
        )
        self.assertEqual(escalation.category, ProgrammerEscalationCategory.DEPENDENCY)
        self.assertEqual(len(escalation.suggested_options), 2)
        self.assertEqual(exec_sess.status, ProgrammerExecutionStatus.BLOCKED)

    # -------------------------------------------------------------------------
    # Scenario 12: Missing context causes escalation
    # -------------------------------------------------------------------------
    def test_12_missing_context_causes_escalation(self) -> None:
        """Missing domain details trigger MISSING_CONTEXT and resolve upon context provision."""
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)
        escalation_coord = EscalationCoordinator()

        escalation = escalation_coord.create_escalation(
            execution=exec_sess,
            work_order=wo,
            category=ProgrammerEscalationCategory.MISSING_CONTEXT,
            requested_decision="Need Stripe webhook signing secret format.",
        )
        self.assertEqual(exec_sess.status, ProgrammerExecutionStatus.BLOCKED)

        # Manager responds with context
        response = ManagerEscalationResponse(
            response_id="mresp-ctx-1",
            escalation_id=escalation.escalation_id,
            action=ManagerEscalationResponseAction.PROVIDE_CONTEXT,
            responder="manager",
            reason="Provided webhook signing format",
            additional_context={"signing_format": "whsec_..."},
        )
        status, revised_wo = escalation_coord.apply_response(escalation, response, exec_sess, wo)
        self.assertEqual(status, EscalationStatus.RESOLVED)
        self.assertEqual(exec_sess.status, ProgrammerExecutionStatus.RUNNING)
        self.assertIn("signing_format", wo.context)

    # -------------------------------------------------------------------------
    # Scenario 13: Resource failure retries only when policy permits
    # -------------------------------------------------------------------------
    def test_13_resource_failure_retries_only_when_policy_permits(self) -> None:
        """RESOURCE failure is retried under default policy, but rejected under strict policy."""
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)
        retry_coord = RetryCoordinator()

        res_failure = ProgrammerFailure(
            failure_id=new_failure_id(),
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            category=ProgrammerFailureCategory.RESOURCE,
            severity=ProgrammerFailureSeverity.HIGH,
            source=FailureSourceType.SYSTEM,
            message="Port 5432 already in use",
            retryable=True,
        )

        # Policy A: RESOURCE is retryable
        permissive_policy = RetryPolicy(
            retryable_categories=(
                ProgrammerFailureCategory.AGENT_STARTUP,
                ProgrammerFailureCategory.AGENT_CRASH,
                ProgrammerFailureCategory.RESOURCE,
            )
        )
        can_retry, _ = retry_coord.can_retry(res_failure, exec_sess, wo, permissive_policy)
        self.assertTrue(can_retry)

        # Policy B: Strict policy without RESOURCE
        strict_policy = RetryPolicy(
            retryable_categories=(
                ProgrammerFailureCategory.AGENT_STARTUP,
                ProgrammerFailureCategory.AGENT_CRASH,
            )
        )
        can_retry_strict, reason = retry_coord.can_retry(res_failure, exec_sess, wo, strict_policy)
        self.assertFalse(can_retry_strict)
        self.assertIn("non-retryable", reason.lower())

    # -------------------------------------------------------------------------
    # Scenario 14: Retry budget exhaustion produces deterministic failure/escalation
    # -------------------------------------------------------------------------
    def test_14_retry_budget_exhaustion_produces_deterministic_failure(self) -> None:
        """Budget exhaustion deterministically transitions to FAIL or ESCALATE."""
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)
        retry_coord = RetryCoordinator()
        retry_policy = RetryPolicy(max_retries=1, total_retry_budget=1)
        recovery_policy = RecoveryPolicy(max_execution_retries=1, max_total_recovery_attempts=1)
        controller = FailureRecoveryController(default_policy=recovery_policy)

        fail1 = ProgrammerFailure(
            failure_id=new_failure_id(),
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            category=ProgrammerFailureCategory.AGENT_CRASH,
            severity=ProgrammerFailureSeverity.HIGH,
            source=FailureSourceType.AGENT,
            message="Crash",
            retryable=True,
        )
        decision1 = controller.evaluate_recovery(fail1, policy=recovery_policy, work_order=wo)
        self.assertEqual(decision1.action, RecoveryDisposition.RETRY)
        retry_coord.record_retry_attempt(fail1, exec_sess, wo, 0.01)

        # 2nd failure exceeds max_retries=1
        fail2 = ProgrammerFailure(
            failure_id=new_failure_id(),
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            category=ProgrammerFailureCategory.AGENT_CRASH,
            severity=ProgrammerFailureSeverity.HIGH,
            source=FailureSourceType.AGENT,
            message="Crash again",
            retryable=True,
        )
        can_retry, reason = retry_coord.can_retry(fail2, exec_sess, wo, retry_policy)
        self.assertFalse(can_retry)
        self.assertIn("exhausted", reason.lower())

        decision2 = controller.evaluate_recovery(fail2, policy=recovery_policy, work_order=wo)
        self.assertEqual(decision2.action, RecoveryDisposition.FAIL)

    # -------------------------------------------------------------------------
    # Scenario 15: Cancellation during retry backoff
    # -------------------------------------------------------------------------
    def test_15_cancellation_during_retry_backoff(self) -> None:
        """Cancellation signaled before/during backoff aborts immediately without delay."""
        retry_coord = RetryCoordinator()
        policy = RetryPolicy(max_delay_seconds=30.0)

        with self.assertRaises(ProgrammerError) as ctx:
            retry_coord.execute_backoff(
                attempt_number=2,
                policy=policy,
                is_cancelled=lambda: True,
            )
        self.assertIn("cancell", str(ctx.exception).lower())

    # -------------------------------------------------------------------------
    # Scenario 16: Cancellation during Cline execution
    # -------------------------------------------------------------------------
    def test_16_cancellation_during_cline_execution(self) -> None:
        """Active Cline execution terminates upon cancellation signal."""
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)
        backend = SupervisedMockBackend()
        handler = ProgrammerCancellationHandler()

        # Signal cancellation while agent is simulated running
        cancellation = handler.cancel_execution(
            execution=exec_sess,
            requested_by="manager",
            reason="User abort",
            backend=backend,
        )
        self.assertTrue(cancellation.confirmed)
        self.assertTrue(cancellation.agent_terminated)
        self.assertEqual(exec_sess.status, ProgrammerExecutionStatus.CANCELLED)

    # -------------------------------------------------------------------------
    # Scenario 17: Completion races with cancellation
    # -------------------------------------------------------------------------
    def test_17_completion_races_with_cancellation(self) -> None:
        """Cancellation arriving after execution COMPLETED is rejected; status preserved."""
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)
        exec_sess.status = ProgrammerExecutionStatus.COMPLETED

        handler = ProgrammerCancellationHandler()
        cancellation = handler.cancel_execution(
            execution=exec_sess,
            requested_by="manager",
            reason="Late cancellation",
        )
        self.assertFalse(cancellation.confirmed)
        self.assertIn("already completed", cancellation.confirmation_error.lower())
        self.assertEqual(exec_sess.status, ProgrammerExecutionStatus.COMPLETED)

    # -------------------------------------------------------------------------
    # Scenario 18: Recovery never expands authority
    # -------------------------------------------------------------------------
    def test_18_recovery_never_expands_authority(self) -> None:
        """Neither retry coordinator nor recovery controller can alter WorkOrder authority envelope."""
        wo = self._create_work_order(
            allowed_paths=["/workspace/src"],
            writable_paths=["/workspace/src"],
        )
        exec_sess = self._create_execution(wo)
        recovery_policy = RecoveryPolicy()
        controller = FailureRecoveryController(default_policy=recovery_policy)
        retry_coord = RetryCoordinator()
        policy = RetryPolicy()

        failure = ProgrammerFailure(
            failure_id=new_failure_id(),
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            category=ProgrammerFailureCategory.AGENT_STARTUP,
            severity=ProgrammerFailureSeverity.HIGH,
            source=FailureSourceType.AGENT,
            message="Startup failed",
            retryable=True,
        )

        controller.evaluate_recovery(failure, policy=recovery_policy, work_order=wo)
        retry_coord.record_retry_attempt(failure, exec_sess, wo, 1, 0.01)

        # Invariant: authority envelope is identical
        self.assertEqual(wo.allowed_paths, ["/workspace/src"])
        self.assertEqual(wo.writable_paths, ["/workspace/src"])
        self.assertEqual(len(wo.allowed_commands), 1)

    # -------------------------------------------------------------------------
    # Scenario 19: Recovery never resets budgets silently
    # -------------------------------------------------------------------------
    def test_19_recovery_never_resets_budgets_silently(self) -> None:
        """Retry counts and correction counts are isolated and strictly non-decreasing."""
        retry_coord = RetryCoordinator()
        exec_id = new_execution_id()
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)

        fail = ProgrammerFailure(
            failure_id=new_failure_id(),
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            category=ProgrammerFailureCategory.AGENT_CRASH,
            severity=ProgrammerFailureSeverity.HIGH,
            source=FailureSourceType.AGENT,
            message="Crash",
            retryable=True,
        )

        # Retry recorded
        retry_coord.record_retry_attempt(fail, exec_sess, wo, 1, 0.01)
        self.assertEqual(retry_coord.get_total_retry_count(exec_sess.execution_id), 1)

        # Correction turn recorded
        retry_coord.record_correction_turn(exec_sess.execution_id)
        self.assertEqual(retry_coord.get_correction_count(exec_sess.execution_id), 1)

        # Retry count remains 1 (not reset)
        self.assertEqual(retry_coord.get_total_retry_count(exec_sess.execution_id), 1)

    # -------------------------------------------------------------------------
    # Scenario 20: Every attempt preserves lineage
    # -------------------------------------------------------------------------
    def test_20_every_attempt_preserves_lineage(self) -> None:
        """Every failure, watchdog record, retry attempt, and escalation retains complete lineage."""
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)
        retry_coord = RetryCoordinator()
        escalation_coord = EscalationCoordinator()

        fail = ProgrammerFailure(
            failure_id=new_failure_id(),
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            category=ProgrammerFailureCategory.AGENT_CRASH,
            severity=ProgrammerFailureSeverity.HIGH,
            source=FailureSourceType.AGENT,
            message="Lineage test failure",
            retryable=True,
        )
        rec = retry_coord.record_retry_attempt(fail, exec_sess, wo, 1, 0.01)
        esc = escalation_coord.create_escalation(
            execution=exec_sess,
            work_order=wo,
            category=ProgrammerEscalationCategory.OTHER,
            requested_decision="Lineage decision",
        )

        # Assert bidirectional lineage
        self.assertEqual(rec.execution_id, exec_sess.execution_id)
        self.assertEqual(rec.work_order_id, wo.work_order_id)
        self.assertEqual(rec.failure_id, fail.failure_id)

        self.assertEqual(esc.execution_id, exec_sess.execution_id)
        self.assertEqual(esc.work_order_id, wo.work_order_id)
        self.assertEqual(esc.trace["task_id"], wo.manager_task_id)
        self.assertEqual(esc.trace["project_id"], wo.project_id)

    # -------------------------------------------------------------------------
    # Scenario 21: Every failure has evidence/provenance
    # -------------------------------------------------------------------------
    def test_21_every_failure_has_evidence_and_provenance(self) -> None:
        """ProgrammerFailure contains failure_id, execution_id, category, source, evidence."""
        fail = ProgrammerFailure(
            failure_id=new_failure_id(),
            execution_id=new_execution_id(),
            work_order_id=new_work_order_id(),
            category=ProgrammerFailureCategory.COMMAND_FAILURE,
            severity=ProgrammerFailureSeverity.HIGH,
            source=FailureSourceType.COMMAND,
            message="gcc returned exit code 1",
            evidence=["fatal error: stdio.h not found"],
            trace={"exit_code": 1, "cwd": "/workspace"},
        )
        validate_failure_id(fail.failure_id)
        self.assertEqual(len(fail.evidence), 1)
        self.assertEqual(fail.trace["exit_code"], 1)

    # -------------------------------------------------------------------------
    # Scenario 22: Final ProgrammerResult accurately reflects what happened
    # -------------------------------------------------------------------------
    def test_22_final_programmer_result_accurately_reflects_execution(self) -> None:
        """ProgrammerResult captures execution status, files, commands, checks, and evidence."""
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)

        cmd_rec = CommandExecutionRecord(
            command="pytest",
            status="SUCCESS",
            exit_code=0,
            duration_ms=45.0,
            output_snippet="1 passed",
        )
        ev = ProgrammerEvidence(
            evidence_id="pevid-001",
            evidence_type=ProgrammerEvidenceType.TEST_RESULT,
            source="pytest",
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            summary="Pytest passed",
        )
        res = ProgrammerResult(
            result_id=new_result_id(),
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            task_id=wo.manager_task_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            status=ProgrammerResultStatus.COMPLETED,
            summary="All tests pass",
            files_changed=["/workspace/src/app.py"],
            files_created=["/workspace/tests/test_app.py"],
            files_deleted=[],
            commands_executed=[cmd_rec],
            evidence=[ev],
        )

        d = res.to_dict()
        self.assertEqual(d["status"], "COMPLETED")
        self.assertEqual(d["files_changed"], ["/workspace/src/app.py"])
        self.assertEqual(d["files_created"], ["/workspace/tests/test_app.py"])
        self.assertEqual(len(d["commands_executed"]), 1)
        self.assertEqual(len(d["evidence"]), 1)

    # =========================================================================
    # Explicit Invariant Tests
    # =========================================================================

    def test_invariant_no_infinite_retries(self) -> None:
        """Retry policy strictly limits attempts to max_retries and halts."""
        policy = RetryPolicy(max_retries=3, total_retry_budget=3)
        coord = RetryCoordinator()
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)

        fail = ProgrammerFailure(
            failure_id=new_failure_id(),
            execution_id=exec_sess.execution_id,
            work_order_id=wo.work_order_id,
            category=ProgrammerFailureCategory.AGENT_CRASH,
            severity=ProgrammerFailureSeverity.HIGH,
            source=FailureSourceType.AGENT,
            message="Crash",
            retryable=True,
        )

        for attempt in range(1, 4):
            can_retry, _ = coord.can_retry(fail, exec_sess, wo, policy)
            self.assertTrue(can_retry)
            coord.record_retry_attempt(fail, exec_sess, wo, 0.01)

        # 4th attempt must be rejected
        can_retry_4, reason = coord.can_retry(fail, exec_sess, wo, policy)
        self.assertFalse(can_retry_4)
        self.assertIn("exhausted", reason.lower())

    def test_invariant_no_infinite_corrections(self) -> None:
        """BoundedCorrectionLoop strictly respects iteration_budget and never loops infinitely."""
        wo = self._create_work_order(iteration_budget=2)

        def infinite_fail_factory(ctx: ProgrammerExecutionContext):
            mock_r = MagicMock()
            chk = VerificationCheck(
                check_id=new_verification_check_id(),
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                check_type=VerificationCheckType.TEST,
                command="pytest",
                status=VerificationStatus.FAIL,
            )
            mock_r.run.return_value = VerificationRunnerResult(
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                checks=[chk],
                evidence=[],
            )
            return mock_r

        programmer = Programmer(
            backend=SupervisedMockBackend(),
            verification_runner_factory=infinite_fail_factory,
            diff_verifier=self._make_mock_diff_verifier(),
        )

        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        self.assertEqual(result.status, ProgrammerResultStatus.FAILED)
        self.assertLessEqual(programmer.last_loop_result.total_iterations, 2)

    def test_invariant_no_automatic_permission_escalation(self) -> None:
        """PERMISSION failures are strictly non-retryable and never automatically elevate."""
        policy = RetryPolicy()
        self.assertIn(ProgrammerFailureCategory.PERMISSION, policy.non_retryable_categories)
        self.assertNotIn(ProgrammerFailureCategory.PERMISSION, policy.retryable_categories)

        # Attempting to construct a policy with PERMISSION as retryable must raise error
        with self.assertRaises(ProgrammerValidationError):
            RetryPolicy(retryable_categories=(ProgrammerFailureCategory.PERMISSION,))

    def test_invariant_no_automatic_scope_expansion(self) -> None:
        """SCOPE violations are strictly non-retryable and never expand paths automatically."""
        policy = RetryPolicy()
        self.assertIn(ProgrammerFailureCategory.SCOPE, policy.non_retryable_categories)
        with self.assertRaises(ProgrammerValidationError):
            RetryPolicy(retryable_categories=(ProgrammerFailureCategory.SCOPE,))

    def test_invariant_no_self_approval_by_programmer_or_cline(self) -> None:
        """Programmer and Cline cannot self-approve or answer escalations."""
        with self.assertRaises(ProgrammerValidationError) as ctx:
            ManagerEscalationResponse(
                response_id="mresp-self-1",
                escalation_id=new_escalation_id(),
                action=ManagerEscalationResponseAction.APPROVE,
                responder="programmer",
                reason="Self approving",
            )
        self.assertIn("Unauthorized escalation responder", str(ctx.exception))

        with self.assertRaises(ProgrammerValidationError) as ctx2:
            ManagerEscalationResponse(
                response_id="mresp-self-2",
                escalation_id=new_escalation_id(),
                action=ManagerEscalationResponseAction.APPROVE,
                responder="cline",
                reason="Cline approving",
            )
        self.assertIn("Unauthorized escalation responder", str(ctx2.exception))

    def test_invariant_no_mutation_of_historical_evidence(self) -> None:
        """WorkOrder revision creates a new object with incremented revision, preserving original."""
        wo = self._create_work_order(allowed_paths=["/workspace/src"], writable_paths=["/workspace/src"])
        revised = wo.create_revision(
            modifications={
                "allowed_paths": ["/workspace/src", "/workspace/extra"],
                "writable_paths": ["/workspace/src", "/workspace/extra"],
            },
            reason="Manager expanded paths",
        )

        self.assertEqual(wo.revision_number, 1)
        self.assertEqual(wo.allowed_paths, ["/workspace/src"])

        self.assertEqual(revised.revision_number, 2)
        self.assertEqual(revised.parent_work_order_id, wo.work_order_id)
        self.assertEqual(revised.allowed_paths, ["/workspace/src", "/workspace/extra"])

    def test_invariant_cancellation_eventually_terminates(self) -> None:
        """Cancellation transitions active execution to CANCELLED and marks agent terminated."""
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)
        backend = SupervisedMockBackend()
        handler = ProgrammerCancellationHandler()

        cancellation = handler.cancel_execution(
            execution=exec_sess,
            requested_by="manager",
            reason="Terminate now",
            backend=backend,
        )
        self.assertTrue(cancellation.confirmed)
        self.assertTrue(cancellation.agent_terminated)
        self.assertEqual(exec_sess.status, ProgrammerExecutionStatus.CANCELLED)

    def test_invariant_manager_remains_authority(self) -> None:
        """Only Manager or User can approve, deny, or modify WorkOrders on escalation."""
        wo = self._create_work_order()
        exec_sess = self._create_execution(wo)
        coord = EscalationCoordinator()

        esc = coord.create_escalation(
            execution=exec_sess,
            work_order=wo,
            category=ProgrammerEscalationCategory.SCOPE,
            requested_decision="Need access to /data",
        )
        self.assertEqual(exec_sess.status, ProgrammerExecutionStatus.BLOCKED)

        # Legitimate Manager response
        resp = ManagerEscalationResponse(
            response_id="mresp-mgr-1",
            escalation_id=esc.escalation_id,
            action=ManagerEscalationResponseAction.APPROVE,
            responder="manager",
            reason="Authorized by manager",
        )
        status, _ = coord.apply_response(esc, resp, exec_sess, wo)
        self.assertEqual(status, EscalationStatus.RESOLVED)
        self.assertEqual(exec_sess.status, ProgrammerExecutionStatus.RUNNING)

    def test_invariant_cline_remains_implementation_capability(self) -> None:
        """Cline output does not dictate final status; AutonomOS verification determines outcome."""
        wo = self._create_work_order()

        # Cline says it succeeded, but verification fails
        class BoastfulBackend(MockCodingAgentBackend):
            def execute(self, req, event_handler=None):
                return CodingAgentResult(
                    execution_id=req.execution_id,
                    work_order_id=req.work_order_id,
                    status=CodingAgentExecutionStatus.COMPLETED,
                    output_text="I have 100% completed everything perfectly!",
                )

            def cancel(self, req):
                return CodingAgentCancellationResult(
                    execution_id=req.execution_id,
                    work_order_id=req.work_order_id,
                    cancelled=True,
                    reason="Cancelled",
                )

        def failing_runner_factory(ctx: ProgrammerExecutionContext):
            mock_r = MagicMock()
            chk = VerificationCheck(
                check_id=new_verification_check_id(),
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                check_type=VerificationCheckType.TEST,
                command="pytest",
                status=VerificationStatus.FAIL,
            )
            mock_r.run.return_value = VerificationRunnerResult(
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                checks=[chk],
                evidence=[],
            )
            return mock_r

        programmer = Programmer(
            backend=BoastfulBackend(),
            verification_runner_factory=failing_runner_factory,
            diff_verifier=self._make_mock_diff_verifier(),
        )

        # AutonomOS overrides Cline's self-reported success because verification failed
        result = programmer.execute(work_order=wo, root_path_override=self.workspace_root)
        self.assertEqual(result.status, ProgrammerResultStatus.FAILED)
        self.assertFalse(result.is_success())


if __name__ == "__main__":
    unittest.main()

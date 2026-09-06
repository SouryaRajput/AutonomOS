"""
End-to-End Integration Tests for Programmer V1 Phase 4.7:
Final Verification & ProgrammerResult Integration.

Validates the complete execution flow:
Manager -> WorkOrder -> ExecutionContext -> Programmer -> Fake Cline implementation ->
Repository changes -> Verification -> Correction -> Final ProgrammerResult.

Mandated Test Scenarios:
1. implementation succeeds and verification passes
2. implementation fails verification then correction succeeds
3. correction budget exhausted
4. unauthorized file modification
5. required check fails
6. acceptance criterion remains unverified
7. cancellation
8. Cline failure
9. invalid WorkOrder
10. complete lineage/provenance
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any, Callable, Optional, Sequence
import unittest
from unittest.mock import MagicMock

from core.enums import RiskLevel, TaskStatus
from core.events.types import EventSource, EventType
from core.models import Task, WorkerOutput
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.acceptance_evaluator import AcceptanceCriteriaEvaluator
from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.blocker import ProgrammerBlocker
from core.programmer.contracts.coding_agent import (
    CodingAgentBackend,
    CodingAgentEvent,
    CodingAgentExecutionStatus,
    CodingAgentRequest,
    CodingAgentResult,
    MockCodingAgentBackend,
)
from core.programmer.contracts.command_record import CommandExecutionRecord
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.correction_loop import (
    BoundedCorrectionLoop,
    CorrectionLoopResult,
    CorrectionPromptBuilder,
)
from core.programmer.contracts.diff_verifier import (
    DiffScopeVerifier,
    DiffVerification,
    UnauthorizedChange,
)
from core.programmer.contracts.evidence_aggregator import VerificationEvidenceAggregator
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.executor import (
    ControlledExecutionOutcome,
    ControlledProgrammerExecutor,
)
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_result_id,
    new_verification_check_id,
    new_verification_evidence_id,
    new_work_order_id,
)
from core.programmer.contracts.manager_bridge import ProgrammerManagerBridge
from core.programmer.contracts.programmer import Programmer
from core.programmer.contracts.provisioner import WorkspaceProvisioner
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.test_record import TestResultRecord
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
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    AcceptanceCriterionType,
    AcceptanceStatus,
    PathBoundaryScope,
    ProgrammerBlockerCategory,
    ProgrammerBlockerSeverity,
    ProgrammerEvidenceType,
    ProgrammerExecutionStatus,
    ProgrammerResultStatus,
    VerificationCheckType,
    VerificationEvidenceSourceType,
    VerificationStatus,
    VerificationSummaryStatus,
)


class TestProgrammerFinalVerificationIntegration(unittest.TestCase):
    """Integration test suite for Phase 4.7 Final Verification & ProgrammerResult Integration."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="prog_p47_test_")
        self.workspace_root = os.path.join(self.temp_dir, "workspace")
        os.makedirs(os.path.join(self.workspace_root, "src"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_root, "tests"), exist_ok=True)

        # Baseline files
        self.auth_file = os.path.join(self.workspace_root, "src", "auth.py")
        with open(self.auth_file, "w") as f:
            f.write("# Initial auth\ndef verify(): return False\n")

        self.test_file = os.path.join(self.workspace_root, "tests", "test_auth.py")
        with open(self.test_file, "w") as f:
            f.write("def test_verify(): assert False\n")

        self.project_id = "proj-p47"
        self.bridge = ProgrammerManagerBridge()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_manager_task(
        self,
        task_id: str = "task-auth-47",
        title: str = "Implement token authentication",
    ) -> Task:
        return Task(
            id=task_id,
            project_id=self.project_id,
            title=title,
            objective="Implement token verification with pytest coverage.",
            assigned_worker="worker.programmer",
            status=TaskStatus.RUNNING,
            risk=RiskLevel.MEDIUM,
            metadata={
                "correlation_id": "corr-auth-47",
                "writable_paths": ["src/auth.py", "tests/test_auth.py"],
                "read_only_paths": ["config/app.json"],
                "forbidden_paths": [".env", ".git"],
                "allowed_commands": ["pytest tests/test_auth.py -v", "pytest"],
                "acceptance_criteria": [
                    {
                        "criterion_id": "ac-auth-1",
                        "description": "All auth tests pass with exit code 0",
                        "criterion_type": "TEST_PASS",
                    }
                ],
                "iteration_budget": 3,
                "time_budget": 300,
            },
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
                verification_id="vdiff-dyn",
                execution_id=execution_id,
                work_order_id=wo_id,
                files_changed=list(files_changed if files_changed is not None else ["src/auth.py"]),
                files_created=list(files_created or []),
                files_deleted=list(files_deleted or []),
                unauthorized_changes=list(unauthorized_changes or []),
                scope_status=scope_status,
            )

        mock.verify_workspace.side_effect = _verify_workspace
        return mock

    def _make_runner_factory(
        self,
        checks_fn: Optional[Callable[[ProgrammerExecutionContext], tuple[list[VerificationCheck], list[VerificationEvidence]]]] = None,
    ):
        """Creates a verification_runner_factory dynamically generating checks matching runtime execution_id."""
        def _factory(ctx: ProgrammerExecutionContext):
            mock_r = MagicMock()

            def _run(checks=None):
                c_list: list[VerificationCheck] = []
                e_list: list[VerificationEvidence] = []
                if checks_fn:
                    c_list, e_list = checks_fn(ctx)
                return VerificationRunnerResult(
                    execution_id=ctx.execution_id,
                    work_order_id=ctx.work_order_id,
                    checks=c_list,
                    evidence=e_list,
                )

            mock_r.run.side_effect = _run
            return mock_r

        return _factory

    # -------------------------------------------------------------------------
    # 1. Implementation Succeeds and Verification Passes
    # -------------------------------------------------------------------------

    def test_implementation_succeeds_and_verification_passes(self):
        """
        Scenario 1: Manager -> WorkOrder -> Programmer -> Fake Cline ->
        repository changes -> verification passes on attempt 1 -> COMPLETED result.
        """
        task = self._create_manager_task()
        work_order = self.bridge.issue_work_order(task)

        # Fake Cline writes passing implementation on attempt 1
        class PassingBackend(MockCodingAgentBackend):
            def execute(self, request, event_handler=None):
                return CodingAgentResult(
                    execution_id=request.execution_id,
                    work_order_id=request.work_order_id,
                    status=CodingAgentExecutionStatus.COMPLETED,
                    output_text="Implementation completed: fixed auth verification",
                )

        def pass_checks(ctx: ProgrammerExecutionContext):
            ev_id = new_verification_evidence_id()
            mock_ev = VerificationEvidence(
                evidence_id=ev_id,
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                source_type=VerificationEvidenceSourceType.TEST_RUNNER,
                description="Test run passed with exit code 0",
                data={"command": "pytest tests/test_auth.py -v", "exit_code": 0},
            )
            mock_check = VerificationCheck(
                check_id=new_verification_check_id(),
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                check_type=VerificationCheckType.TEST,
                command="pytest tests/test_auth.py -v",
                status=VerificationStatus.PASS,
                exit_code=0,
                duration_ms=120.0,
                output_snippet="1 passed in 0.12s",
                evidence=[ev_id],
            )
            return [mock_check], [mock_ev]

        mock_diff_verifier = self._make_mock_diff_verifier(files_changed=["src/auth.py"])

        programmer = Programmer(
            backend=PassingBackend(),
            bridge=self.bridge,
            verification_runner_factory=self._make_runner_factory(pass_checks),
            diff_verifier=mock_diff_verifier,
        )

        execution, result, worker_output = programmer.execute_work_order(
            work_order=work_order,
            root_path_override=self.workspace_root,
        )

        # 1. Lifecycle verification: must reach COMPLETED
        self.assertEqual(execution.status, ProgrammerExecutionStatus.COMPLETED)
        self.assertTrue(execution.is_terminal)

        # 2. Result contract verification
        self.assertEqual(result.status, ProgrammerResultStatus.COMPLETED)
        self.assertTrue(result.is_success())
        self.assertEqual(result.files_changed, ["src/auth.py"])
        self.assertTrue(len(result.commands_executed) >= 1)
        self.assertEqual(result.commands_executed[0].command, "pytest tests/test_auth.py -v")
        self.assertEqual(result.commands_executed[0].status, "SUCCESS")
        self.assertEqual(len(result.acceptance_results), 1)
        self.assertEqual(result.acceptance_results[0].status, AcceptanceStatus.PASS)
        self.assertTrue(len(result.evidence) >= 1)
        self.assertTrue(result.is_acceptance_fully_verified())

        # 3. WorkerOutput verification
        self.assertIsNotNone(worker_output)
        self.assertTrue(worker_output.success)
        self.assertEqual(worker_output.metadata["programmer_status"], "COMPLETED")

        # 4. Domain events emitted
        event_types = [e.event_type for e in self.bridge.events]
        self.assertIn(EventType.PROGRAMMER_REQUESTED, event_types)
        self.assertIn(EventType.PROGRAMMER_STARTED, event_types)
        self.assertIn(EventType.PROGRAMMER_COMPLETED, event_types)

    # -------------------------------------------------------------------------
    # 2. Implementation Fails Verification Then Correction Succeeds
    # -------------------------------------------------------------------------

    def test_implementation_fails_verification_then_correction_succeeds(self):
        """
        Scenario 2: Attempt 1 fails verification -> feedback prompt dispatched ->
        Attempt 2 correction succeeds -> final ProgrammerResult is COMPLETED with 2 iterations.
        """
        task = self._create_manager_task()
        work_order = self.bridge.issue_work_order(task)

        turn_count = 0
        prompts_received = []

        class MultiTurnBackend(MockCodingAgentBackend):
            def execute(self, request, event_handler=None):
                nonlocal turn_count
                turn_count += 1
                prompts_received.append(request.prompt)
                return CodingAgentResult(
                    execution_id=request.execution_id,
                    work_order_id=request.work_order_id,
                    status=CodingAgentExecutionStatus.COMPLETED,
                    output_text=f"Turn {turn_count} finished",
                )

        mock_diff_verifier = self._make_mock_diff_verifier(files_changed=["src/auth.py"])

        # Runner fails on attempt 1, passes on attempt 2
        def alternating_checks(ctx: ProgrammerExecutionContext):
            if turn_count == 1:
                # Attempt 1: failure
                ev_id = "vevid-fail"
                ev = VerificationEvidence(
                    evidence_id=ev_id,
                    execution_id=ctx.execution_id,
                    work_order_id=ctx.work_order_id,
                    source_type=VerificationEvidenceSourceType.TEST_RUNNER,
                    description="Test failed with exit code 1",
                    data={"command": "pytest tests/test_auth.py -v", "exit_code": 1},
                )
                check = VerificationCheck(
                    check_id="vchk-fail",
                    execution_id=ctx.execution_id,
                    work_order_id=ctx.work_order_id,
                    check_type=VerificationCheckType.TEST,
                    command="pytest tests/test_auth.py -v",
                    status=VerificationStatus.FAIL,
                    exit_code=1,
                    duration_ms=100.0,
                    output_snippet="AssertionError: verify() returned False",
                    evidence=[ev_id],
                )
                return [check], [ev]
            else:
                # Attempt 2: pass
                ev_id = "vevid-pass"
                ev = VerificationEvidence(
                    evidence_id=ev_id,
                    execution_id=ctx.execution_id,
                    work_order_id=ctx.work_order_id,
                    source_type=VerificationEvidenceSourceType.TEST_RUNNER,
                    description="Test passed on attempt 2",
                    data={"command": "pytest tests/test_auth.py -v", "exit_code": 0},
                )
                check = VerificationCheck(
                    check_id="vchk-pass",
                    execution_id=ctx.execution_id,
                    work_order_id=ctx.work_order_id,
                    check_type=VerificationCheckType.TEST,
                    command="pytest tests/test_auth.py -v",
                    status=VerificationStatus.PASS,
                    exit_code=0,
                    duration_ms=90.0,
                    output_snippet="1 passed",
                    evidence=[ev_id],
                )
                return [check], [ev]

        programmer = Programmer(
            backend=MultiTurnBackend(),
            bridge=self.bridge,
            verification_runner_factory=self._make_runner_factory(alternating_checks),
            diff_verifier=mock_diff_verifier,
        )

        execution, result, worker_output = programmer.execute_work_order(
            work_order=work_order,
            root_path_override=self.workspace_root,
        )

        self.assertEqual(execution.status, ProgrammerExecutionStatus.COMPLETED)
        self.assertEqual(result.status, ProgrammerResultStatus.COMPLETED)
        self.assertEqual(programmer.last_loop_result.total_iterations, 2)
        self.assertEqual(len(programmer.last_loop_result.iterations), 2)
        self.assertIn("OBSERVED VERIFICATION FACTS", prompts_received[1])
        self.assertIn("AssertionError", prompts_received[1])

    # -------------------------------------------------------------------------
    # 3. Correction Budget Exhausted
    # -------------------------------------------------------------------------

    def test_correction_budget_exhausted(self):
        """
        Scenario 3: Checks fail consistently -> stops strictly at budget ->
        result status is FAILED with budget_exhausted metadata.
        """
        task = self._create_manager_task()
        work_order = self.bridge.issue_work_order(task)
        work_order.iteration_budget = 2

        backend = MockCodingAgentBackend()
        mock_diff_verifier = self._make_mock_diff_verifier(files_changed=["src/auth.py"])

        def persistent_fail_checks(ctx: ProgrammerExecutionContext):
            fail_check = VerificationCheck(
                check_id="vchk-cons-fail",
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                command="pytest tests/test_auth.py -v",
                status=VerificationStatus.FAIL,
                exit_code=1,
                output_snippet="Persistent failure",
            )
            fail_ev = VerificationEvidence(
                evidence_id="vevid-cons-fail",
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
                description="Persistent failure across attempts",
            )
            return [fail_check], [fail_ev]

        programmer = Programmer(
            backend=backend,
            bridge=self.bridge,
            verification_runner_factory=self._make_runner_factory(persistent_fail_checks),
            diff_verifier=mock_diff_verifier,
        )

        execution, result, worker_output = programmer.execute_work_order(
            work_order=work_order,
            root_path_override=self.workspace_root,
        )

        self.assertEqual(execution.status, ProgrammerExecutionStatus.FAILED)
        self.assertEqual(result.status, ProgrammerResultStatus.FAILED)
        self.assertTrue(result.metadata.get("budget_exhausted"))
        self.assertEqual(programmer.last_loop_result.total_iterations, 2)
        self.assertTrue(programmer.last_loop_result.is_exhausted)

    # -------------------------------------------------------------------------
    # 4. Unauthorized File Modification
    # -------------------------------------------------------------------------

    def test_unauthorized_file_modification_halts_and_blocks(self):
        """
        Scenario 4: Unauthorized modifications outside writable scope ->
        diff verifier flags violation -> loop halts immediately with BLOCKED.
        """
        task = self._create_manager_task()
        work_order = self.bridge.issue_work_order(task)

        backend = MockCodingAgentBackend()

        mock_diff_verifier = self._make_mock_diff_verifier(
            files_changed=["src/auth.py", "README.md"],
            unauthorized_changes=[
                UnauthorizedChange(
                    path="README.md",
                    change_type="MODIFIED",
                    scope=PathBoundaryScope.OUTSIDE_BOUNDARY,
                    reason="Path not in writable_paths",
                )
            ],
            scope_status=VerificationStatus.FAIL,
        )

        programmer = Programmer(
            backend=backend,
            bridge=self.bridge,
            verification_runner_factory=self._make_runner_factory(lambda ctx: ([], [])),
            diff_verifier=mock_diff_verifier,
        )

        execution, result, worker_output = programmer.execute_work_order(
            work_order=work_order,
            root_path_override=self.workspace_root,
        )

        self.assertEqual(execution.status, ProgrammerExecutionStatus.BLOCKED)
        self.assertEqual(result.status, ProgrammerResultStatus.BLOCKED)
        self.assertTrue(len(result.blockers) >= 1)
        self.assertIn("README.md", result.blockers[0])
        self.assertEqual(programmer.last_loop_result.total_iterations, 1)

    # -------------------------------------------------------------------------
    # 5. Required Check Fails
    # -------------------------------------------------------------------------

    def test_required_check_fails(self):
        """
        Scenario 5: Required verification check fails with non-zero exit code ->
        status FAILED and diagnostic risk recorded.
        """
        task = self._create_manager_task()
        work_order = self.bridge.issue_work_order(task)
        work_order.iteration_budget = 1

        backend = MockCodingAgentBackend()

        def req_fail_checks(ctx: ProgrammerExecutionContext):
            check = VerificationCheck(
                check_id="vchk-req-fail",
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                command="pytest tests/test_auth.py -v",
                status=VerificationStatus.FAIL,
                exit_code=2,
                output_snippet="pytest: error: unrecognized arguments",
            )
            ev = VerificationEvidence(
                evidence_id="vevid-req-fail",
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                source_type=VerificationEvidenceSourceType.COMMAND_OUTPUT,
                description="Required check failed with exit code 2",
            )
            return [check], [ev]

        mock_diff_verifier = self._make_mock_diff_verifier(files_changed=["src/auth.py"])

        programmer = Programmer(
            backend=backend,
            bridge=self.bridge,
            verification_runner_factory=self._make_runner_factory(req_fail_checks),
            diff_verifier=mock_diff_verifier,
        )

        execution, result, worker_output = programmer.execute_work_order(
            work_order=work_order,
            root_path_override=self.workspace_root,
        )

        self.assertEqual(execution.status, ProgrammerExecutionStatus.FAILED)
        self.assertEqual(result.status, ProgrammerResultStatus.FAILED)
        self.assertTrue(any("pytest" in str(r) or "exit code" in str(r).lower() for r in result.risks))
        self.assertFalse(result.test_results[0].passed)
        self.assertEqual(result.test_results[0].exit_code, 2)

    # -------------------------------------------------------------------------
    # 6. Acceptance Criterion Remains Unverified
    # -------------------------------------------------------------------------

    def test_acceptance_criterion_remains_unverified(self):
        """
        Scenario 6: Acceptance criterion without executable evidence remains NOT_VERIFIED ->
        inconclusive UNVERIFIED summary halts loop with BLOCKED instead of blind retries.
        """
        task = self._create_manager_task()
        work_order = self.bridge.issue_work_order(task)
        work_order.acceptance_criteria.append(
            AcceptanceCriterion(
                criterion_id="ac-perf-unverified",
                description="Latency must stay under 5ms (no benchmark check specified)",
                criterion_type=AcceptanceCriterionType.CUSTOM,
            )
        )

        backend = MockCodingAgentBackend()
        mock_diff_verifier = self._make_mock_diff_verifier(files_changed=["src/auth.py"])

        programmer = Programmer(
            backend=backend,
            bridge=self.bridge,
            verification_runner_factory=self._make_runner_factory(lambda ctx: ([], [])),
            diff_verifier=mock_diff_verifier,
        )

        execution, result, worker_output = programmer.execute_work_order(
            work_order=work_order,
            root_path_override=self.workspace_root,
        )

        self.assertEqual(execution.status, ProgrammerExecutionStatus.BLOCKED)
        self.assertEqual(result.status, ProgrammerResultStatus.BLOCKED)
        self.assertEqual(programmer.last_loop_result.final_status, VerificationSummaryStatus.UNVERIFIED)
        self.assertTrue(len(result.blockers) >= 1)
        self.assertEqual(programmer.last_loop_result.total_iterations, 1)

    # -------------------------------------------------------------------------
    # 7. Cancellation
    # -------------------------------------------------------------------------

    def test_cancellation(self):
        """
        Scenario 7: Explicit cancellation halts execution cleanly -> CANCELLED status.
        """
        task = self._create_manager_task()
        work_order = self.bridge.issue_work_order(task)

        programmer = Programmer(
            backend=MockCodingAgentBackend(),
            bridge=self.bridge,
        )
        programmer.cancel(reason="User requested cancellation before start.")

        execution, result, worker_output = programmer.execute_work_order(
            work_order=work_order,
            root_path_override=self.workspace_root,
        )

        self.assertEqual(execution.status, ProgrammerExecutionStatus.CANCELLED)
        self.assertEqual(result.status, ProgrammerResultStatus.CANCELLED)
        self.assertTrue(programmer.last_loop_result.is_cancelled)

    # -------------------------------------------------------------------------
    # 8. Cline Failure
    # -------------------------------------------------------------------------

    def test_cline_failure(self):
        """
        Scenario 8: Coding agent backend raises fatal failure ->
        execution transitions immediately to FAILED.
        """
        task = self._create_manager_task()
        work_order = self.bridge.issue_work_order(task)

        class CrashingBackend(MockCodingAgentBackend):
            def execute(self, request, event_handler=None):
                return CodingAgentResult(
                    execution_id=request.execution_id,
                    work_order_id=request.work_order_id,
                    status=CodingAgentExecutionStatus.FAILED,
                    error_message="Fatal: Out of context window memory",
                )

        programmer = Programmer(
            backend=CrashingBackend(),
            bridge=self.bridge,
        )

        execution, result, worker_output = programmer.execute_work_order(
            work_order=work_order,
            root_path_override=self.workspace_root,
        )

        self.assertEqual(execution.status, ProgrammerExecutionStatus.FAILED)
        self.assertEqual(result.status, ProgrammerResultStatus.FAILED)
        self.assertIn("Out of context window", result.summary)

    # -------------------------------------------------------------------------
    # 9. Invalid WorkOrder
    # -------------------------------------------------------------------------

    def test_invalid_work_order_rejected_fail_closed(self):
        """
        Scenario 9: Invalid WorkOrder with missing objective or illegal budget ->
        rejected before any execution starts with ProgrammerValidationError.
        """
        programmer = Programmer(
            backend=MockCodingAgentBackend(),
            bridge=self.bridge,
        )

        # 1. Missing objective
        invalid_wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            task_id="task-bad",
            project_id=self.project_id,
            correlation_id="corr-bad",
            objective="",  # empty objective
        )
        with self.assertRaises(ProgrammerValidationError):
            programmer.execute(invalid_wo)

        # 2. Illegal budget
        invalid_budget_wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            task_id="task-bad-budget",
            project_id=self.project_id,
            correlation_id="corr-bad-budget",
            objective="Valid objective",
            iteration_budget=0,  # non-positive budget rejected
        )
        with self.assertRaises(ProgrammerValidationError):
            programmer.execute(invalid_budget_wo)

    # -------------------------------------------------------------------------
    # 10. Complete Lineage & Provenance
    # -------------------------------------------------------------------------

    def test_complete_lineage_and_provenance(self):
        """
        Scenario 10: Complete causal lineage verification:
        ManagerTask.id -> WorkOrder.work_order_id -> Execution.execution_id ->
        Result.execution_id -> WorkerOutput. All evidence and checks maintain identical IDs.
        """
        task = self._create_manager_task(task_id="task-lineage-100")
        work_order = self.bridge.issue_work_order(task)

        def pass_checks(ctx: ProgrammerExecutionContext):
            ev_id = new_verification_evidence_id()
            ev = VerificationEvidence(
                evidence_id=ev_id,
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                source_type=VerificationEvidenceSourceType.TEST_RUNNER,
                description="Authoritative pytest output",
                data={"command": "pytest tests/test_auth.py -v", "exit_code": 0},
            )
            chk = VerificationCheck(
                check_id=new_verification_check_id(),
                execution_id=ctx.execution_id,
                work_order_id=ctx.work_order_id,
                check_type=VerificationCheckType.TEST,
                command="pytest tests/test_auth.py -v",
                status=VerificationStatus.PASS,
                exit_code=0,
                duration_ms=50.0,
                output_snippet="all tests passed",
                evidence=[ev_id],
            )
            return [chk], [ev]

        mock_diff_verifier = self._make_mock_diff_verifier(files_changed=["src/auth.py"])

        programmer = Programmer(
            backend=MockCodingAgentBackend(),
            bridge=self.bridge,
            verification_runner_factory=self._make_runner_factory(pass_checks),
            diff_verifier=mock_diff_verifier,
        )

        execution, result, worker_output = programmer.execute_work_order(
            work_order=work_order,
            root_path_override=self.workspace_root,
        )

        # Verify lineage alignment
        self.assertEqual(execution.task_id, task.id)
        self.assertEqual(execution.work_order_id, work_order.work_order_id)
        self.assertEqual(result.execution_id, execution.execution_id)
        self.assertEqual(result.work_order_id, work_order.work_order_id)
        self.assertEqual(result.task_id, task.id)
        self.assertEqual(result.project_id, task.project_id)
        self.assertEqual(result.correlation_id, "corr-auth-47")

        # Validate result lineage
        result.validate_lineage(execution=execution, work_order=work_order)

        # Verify WorkerOutput provenance
        self.assertEqual(worker_output.metadata["task_id"], task.id)
        self.assertEqual(worker_output.metadata["project_id"], task.project_id)
        self.assertEqual(worker_output.metadata["correlation_id"], "corr-auth-47")
        self.assertEqual(worker_output.metadata["result_id"], result.result_id)
        self.assertEqual(worker_output.metadata["execution_id"], execution.execution_id)
        self.assertEqual(worker_output.metadata["work_order_id"], work_order.work_order_id)


if __name__ == "__main__":
    unittest.main()

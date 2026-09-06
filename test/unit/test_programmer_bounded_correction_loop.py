import os
import tempfile
import unittest
from unittest.mock import MagicMock

from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.acceptance_evaluator import AcceptanceCriteriaEvaluator
from core.programmer.contracts.coding_agent import (
    CodingAgentBackend,
    CodingAgentEvent,
    CodingAgentRequest,
    CodingAgentResult,
    MockCodingAgentBackend,
)
from core.programmer.contracts.command_record import CommandExecutionRecord
from core.programmer.contracts.correction_loop import (
    BoundedCorrectionLoop,
    CorrectionIterationRecord,
    CorrectionLoopResult,
    CorrectionPromptBuilder,
)
from core.programmer.contracts.diff_verifier import DiffScopeVerifier, DiffVerification
from core.programmer.contracts.evidence_aggregator import VerificationEvidenceAggregator
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.executor import (
    ControlledExecutionOutcome,
    ControlledProgrammerExecutor,
)
from core.programmer.contracts.identifiers import (
    ITERATION_ID_PREFIX,
    is_programmer_id,
    new_execution_id,
    new_iteration_id,
    new_verification_check_id,
    new_verification_evidence_id,
    new_work_order_id,
    validate_iteration_id,
)
from core.programmer.contracts.result import ProgrammerResult
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
    InvalidProgrammerIdError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    AcceptanceCriterionType,
    CodingAgentBackendType,
    CodingAgentExecutionStatus,
    PathBoundaryScope,
    ProgrammerBlockerCategory,
    ProgrammerExecutionStatus,
    ProgrammerResultStatus,
    VerificationCheckType,
    VerificationEvidenceSourceType,
    VerificationStatus,
    VerificationSummaryStatus,
)


class TestProgrammerBoundedCorrectionLoop(unittest.TestCase):
    """
    Comprehensive unit tests for Programmer V1 Phase 4.6: Bounded Self-Correction Loop.
    Validates loop ownership by AutonomOS, iteration budget enforcement, structured factual
    feedback formulation, immutability of authority, escalation to BLOCKED, and failure guards.
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = self.tmp_dir.name
        self.work_order_id = new_work_order_id()
        self.task_id = "task-oauth-01"
        self.project_id = "proj-autonomos"
        self.correlation_id = "corr-oauth-01"

        # Create base workspace files
        os.makedirs(os.path.join(self.workspace_root, "src", "auth"), exist_ok=True)
        with open(os.path.join(self.workspace_root, "src", "auth", "login.py"), "w") as f:
            f.write("# login implementation\n")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _create_work_order(
        self,
        iteration_budget: int = 3,
        objective: str = "Implement token validation",
        writable_paths: tuple[str, ...] = ("src/auth",),
        read_only_paths: tuple[str, ...] = ("config/",),
        forbidden_paths: tuple[str, ...] = (".env", "secrets/"),
        allowed_commands: tuple[str, ...] = ("pytest", "python -m pytest"),
        constraints: tuple[str, ...] = ("No external network access",),
        required_checks: tuple[str, ...] = ("pytest test/test_auth.py",),
    ) -> ProgrammerWorkOrder:
        return ProgrammerWorkOrder(
            work_order_id=self.work_order_id,
            task_id=self.task_id,
            project_id=self.project_id,
            correlation_id=self.correlation_id,
            objective=objective,
            iteration_budget=iteration_budget,
            writable_paths=writable_paths,
            read_only_paths=read_only_paths,
            forbidden_paths=forbidden_paths,
            allowed_commands=allowed_commands,
            constraints=list(constraints),
            required_checks=required_checks,
            acceptance_criteria=(
                AcceptanceCriterion(
                    criterion_id="crit-auth-1",
                    description="Token validation must return 200 on valid token",
                    criterion_type=AcceptanceCriterionType.TEST_PASS,
                    is_mandatory=True,
                ),
            ),
        )

    def _create_passing_runner_result(self, exec_id: str, wo_id: str) -> VerificationRunnerResult:
        ev = VerificationEvidence(
            evidence_id=new_verification_evidence_id(),
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.TEST_RUNNER,
            source_reference="pytest test/test_auth.py",
            description="Test run completed with exit code 0",
            is_agent_claim=False,
            data={"command": "pytest test/test_auth.py", "exit_code": 0},
        )
        chk = VerificationCheck(
            check_id=new_verification_check_id(),
            execution_id=exec_id,
            work_order_id=wo_id,
            check_type=VerificationCheckType.TEST,
            command="pytest test/test_auth.py",
            status=VerificationStatus.PASS,
            exit_code=0,
            duration_ms=15.0,
            evidence=[ev.evidence_id],
        )
        return VerificationRunnerResult(checks=[chk], evidence=[ev])

    def _create_failing_runner_result(self, exec_id: str, wo_id: str, snippet: str = "AssertionError: Expected 200, got 500") -> VerificationRunnerResult:
        ev = VerificationEvidence(
            evidence_id=new_verification_evidence_id(),
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.TEST_RUNNER,
            source_reference="pytest test/test_auth.py",
            description=f"Test run failed: {snippet}",
            is_agent_claim=False,
            data={"command": "pytest test/test_auth.py", "exit_code": 1},
        )
        chk = VerificationCheck(
            check_id=new_verification_check_id(),
            execution_id=exec_id,
            work_order_id=wo_id,
            check_type=VerificationCheckType.TEST,
            command="pytest test/test_auth.py",
            status=VerificationStatus.FAIL,
            exit_code=1,
            duration_ms=15.0,
            output_snippet=snippet,
            evidence=[ev.evidence_id],
        )
        return VerificationRunnerResult(checks=[chk], evidence=[ev])

    # -------------------------------------------------------------------------
    # 1. Identifier Generation & Validation Tests
    # -------------------------------------------------------------------------

    def test_iteration_identifier_generation_and_validation(self):
        """Verify generation, prefix constraints, and validation for iteration IDs."""
        iter_id = new_iteration_id()
        self.assertTrue(iter_id.startswith(ITERATION_ID_PREFIX))
        self.assertTrue(is_programmer_id(iter_id))

        # Should validate without error
        validate_iteration_id(iter_id)

        # Invalid IDs
        with self.assertRaises(InvalidProgrammerIdError):
            validate_iteration_id("wrongprefix-123")
        with self.assertRaises(InvalidProgrammerIdError):
            validate_iteration_id("piter-")
        with self.assertRaises(InvalidProgrammerIdError):
            validate_iteration_id("piter-invalid@char")
        with self.assertRaises(InvalidProgrammerIdError):
            validate_iteration_id(None)  # type: ignore

    # -------------------------------------------------------------------------
    # 2. Iteration Record & Loop Result Serialization Tests
    # -------------------------------------------------------------------------

    def test_iteration_record_serialization_roundtrip(self):
        """Verify round-trip serialization fidelity of CorrectionIterationRecord."""
        wo = self._create_work_order()
        exec_id = new_execution_id()
        iter_id = new_iteration_id()

        summary = VerificationSummary(
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            overall_status=VerificationSummaryStatus.FAILED,
            verification_checks=[],
            acceptance_results=[],
        )

        record = CorrectionIterationRecord(
            iteration_id=iter_id,
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            attempt_number=1,
            started_at="2026-09-06T12:00:00+00:00",
            completed_at="2026-09-06T12:01:00+00:00",
            verification_summary=summary,
            correction_reason="Verification failed",
            feedback_prompt="Check tests",
            trace={"step": "attempt_1"},
            metadata={"source": "test"},
        )

        record_dict = record.to_dict()
        rehydrated = CorrectionIterationRecord.from_dict(record_dict)

        self.assertEqual(rehydrated.iteration_id, iter_id)
        self.assertEqual(rehydrated.execution_id, exec_id)
        self.assertEqual(rehydrated.attempt_number, 1)
        self.assertEqual(rehydrated.verification_summary.overall_status, VerificationSummaryStatus.FAILED)
        self.assertEqual(rehydrated.correction_reason, "Verification failed")
        self.assertEqual(rehydrated.trace, {"step": "attempt_1"})

    def test_correction_loop_result_properties(self):
        """Verify convenience properties on CorrectionLoopResult."""
        wo = self._create_work_order()
        exec_id = new_execution_id()

        # Success case
        res_success = CorrectionLoopResult(
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            final_status=VerificationSummaryStatus.VERIFIED,
            total_iterations=2,
            budget=3,
        )
        self.assertTrue(res_success.is_success)
        self.assertFalse(res_success.is_exhausted)
        self.assertFalse(res_success.is_blocked)
        self.assertFalse(res_success.is_failed)

        # Budget exhausted case
        res_exhausted = CorrectionLoopResult(
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            final_status=VerificationSummaryStatus.FAILED,
            total_iterations=3,
            budget=3,
        )
        self.assertFalse(res_exhausted.is_success)
        self.assertTrue(res_exhausted.is_exhausted)
        self.assertFalse(res_exhausted.is_blocked)

        # Blocked case
        blocked_prog_res = ProgrammerResult(
            result_id="pres-001",
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            task_id=wo.task_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            status=ProgrammerResultStatus.BLOCKED,
            summary="Blocked",
            summary_for_manager="Blocked",
        )
        res_blocked = CorrectionLoopResult(
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            final_status=VerificationSummaryStatus.FAILED,
            total_iterations=1,
            budget=3,
            result=blocked_prog_res,
        )
        self.assertTrue(res_blocked.is_blocked)
        self.assertFalse(res_blocked.is_exhausted)

    # -------------------------------------------------------------------------
    # 3. Correction Prompt Builder Formulation Tests
    # -------------------------------------------------------------------------

    def test_correction_prompt_builder_factual_structure(self):
        """Verify that the prompt builder formulates structured factual feedback without fabrication."""
        wo = self._create_work_order(iteration_budget=3)
        exec_id = new_execution_id()

        failed_check = VerificationCheck(
            check_id=new_verification_check_id(),
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            check_type=VerificationCheckType.TEST,
            command="pytest test/test_auth.py",
            status=VerificationStatus.FAIL,
            exit_code=1,
            output_snippet="AssertionError: Expected 200, got 401",
        )

        failed_acceptance = AcceptanceResult(
            criterion_id="crit-auth-1",
            status=VerificationStatus.FAIL,
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            explanation="Token endpoint returned 401",
            evidence=["AssertionError in pytest test/test_auth.py"],
        )

        summary = VerificationSummary(
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            overall_status=VerificationSummaryStatus.FAILED,
            verification_checks=[failed_check],
            acceptance_results=[failed_acceptance],
        )

        prompt = CorrectionPromptBuilder.build_correction_prompt(
            work_order=wo,
            summary=summary,
            attempt=1,
            max_attempts=3,
        )

        # Invariant checks:
        # 1. Mentions attempt count
        self.assertIn("ATTEMPT 2 OF 3", prompt)
        # 2. Section 1: Observed Verification Facts
        self.assertIn("## 1. OBSERVED VERIFICATION FACTS", prompt)
        self.assertIn("pytest test/test_auth.py", prompt)
        self.assertIn("Exit Code: `1`", prompt)
        self.assertIn("AssertionError: Expected 200, got 401", prompt)
        self.assertIn("Token endpoint returned 401", prompt)
        # 3. Section 2: Expected Behavior
        self.assertIn("## 2. EXPECTED BEHAVIOR", prompt)
        self.assertIn(wo.objective, prompt)
        # 4. Section 3: Acceptance Criteria
        self.assertIn("## 3. ACCEPTANCE CRITERIA CATALOG", prompt)
        self.assertIn("crit-auth-1", prompt)
        # 5. Section 4: Boundary Constraints
        self.assertIn("## 4. BOUNDARY CONSTRAINTS (STRICTLY ENFORCED)", prompt)
        self.assertIn("src/auth", prompt)
        self.assertIn("Your authority CANNOT expand during correction", prompt)

    # -------------------------------------------------------------------------
    # 4. Bounded Loop: First Attempt Passes
    # -------------------------------------------------------------------------

    def test_loop_terminates_immediately_when_first_attempt_passes(self):
        """Verify that when the initial implementation passes verification, loop completes in 1 turn."""
        wo = self._create_work_order(iteration_budget=3)
        exec_id = new_execution_id()

        execution = ProgrammerExecution(
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            task_id=wo.task_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            status=ProgrammerExecutionStatus.REQUESTED,
        )

        # Mock runner that always passes
        mock_runner = MagicMock()
        mock_runner.run.return_value = self._create_passing_runner_result(exec_id, wo.work_order_id)

        # Mock executor that creates clean initial implementation
        mock_executor = MagicMock()
        mock_context = MagicMock()
        mock_context.workspace = ProgrammerWorkspace(
            workspace_id="pws-test",
            project_id=self.project_id,
            root_path=self.workspace_root,
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            writable_paths=["src/auth"],
        )
        mock_context.status = "ACTIVE"
        mock_executor.execute.return_value = ControlledExecutionOutcome(
            execution=execution,
            context=mock_context,
            result=ProgrammerResult(
                result_id="pres-001",
                execution_id=exec_id,
                work_order_id=wo.work_order_id,
                task_id=wo.task_id,
                project_id=wo.project_id,
                correlation_id=wo.correlation_id,
                status=ProgrammerResultStatus.PARTIAL,
                summary="Initial agent execution finished",
                summary_for_manager="Initial turn completed",
            ),
        )

        loop = BoundedCorrectionLoop(
            executor=mock_executor,
            verification_runner_factory=lambda ctx: mock_runner,
        )

        loop_result = loop.run(
            work_order=wo,
            execution=execution,
        )

        self.assertTrue(loop_result.is_success)
        self.assertEqual(loop_result.total_iterations, 1)
        self.assertEqual(loop_result.final_status, VerificationSummaryStatus.VERIFIED)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.COMPLETED)
        self.assertEqual(loop_result.result.status, ProgrammerResultStatus.COMPLETED)
        # Verify execute_correction was NOT called
        mock_executor.execute_correction.assert_not_called()

    # -------------------------------------------------------------------------
    # 5. Bounded Loop: Fails First, Passes on Second Attempt
    # -------------------------------------------------------------------------

    def test_loop_corrects_failure_and_succeeds_on_second_attempt(self):
        """Verify that a failed first attempt triggers a correction turn and succeeds on second attempt."""
        wo = self._create_work_order(iteration_budget=3)
        exec_id = new_execution_id()

        execution = ProgrammerExecution(
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            task_id=wo.task_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            status=ProgrammerExecutionStatus.REQUESTED,
        )

        mock_context = MagicMock()
        mock_context.workspace = ProgrammerWorkspace(
            workspace_id="pws-test",
            project_id=self.project_id,
            root_path=self.workspace_root,
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            writable_paths=["src/auth"],
        )
        mock_context.status = "ACTIVE"

        # Verification Runner: fails on attempt 1, passes on attempt 2
        attempt_call_count = [0]

        def runner_run_mock(*args, **kwargs):
            attempt_call_count[0] += 1
            if attempt_call_count[0] == 1:
                return self._create_failing_runner_result(exec_id, wo.work_order_id, "AssertionError: Expected 200, got 500")
            else:
                return self._create_passing_runner_result(exec_id, wo.work_order_id)

        mock_runner = MagicMock()
        mock_runner.run.side_effect = runner_run_mock

        mock_executor = MagicMock()
        mock_executor.execute.return_value = ControlledExecutionOutcome(
            execution=execution,
            context=mock_context,
            result=ProgrammerResult(
                result_id="pres-001",
                execution_id=exec_id,
                work_order_id=wo.work_order_id,
                task_id=wo.task_id,
                project_id=wo.project_id,
                correlation_id=wo.correlation_id,
                status=ProgrammerResultStatus.PARTIAL,
                summary="Turn 1 done",
                summary_for_manager="Turn 1 done",
            ),
        )

        mock_executor.execute_correction.return_value = ControlledExecutionOutcome(
            execution=execution,
            context=mock_context,
            result=ProgrammerResult(
                result_id="pres-002",
                execution_id=exec_id,
                work_order_id=wo.work_order_id,
                task_id=wo.task_id,
                project_id=wo.project_id,
                correlation_id=wo.correlation_id,
                status=ProgrammerResultStatus.PARTIAL,
                summary="Turn 2 done",
                summary_for_manager="Turn 2 done",
            ),
        )

        loop = BoundedCorrectionLoop(
            executor=mock_executor,
            verification_runner_factory=lambda ctx: mock_runner,
        )

        loop_result = loop.run(
            work_order=wo,
            execution=execution,
        )

        self.assertTrue(loop_result.is_success)
        self.assertEqual(loop_result.total_iterations, 2)
        self.assertEqual(loop_result.final_status, VerificationSummaryStatus.VERIFIED)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.COMPLETED)
        mock_executor.execute_correction.assert_called_once()
        # Verify the feedback prompt provided to attempt 2
        self.assertIn("AssertionError: Expected 200, got 500", loop_result.iterations[0].feedback_prompt)

    # -------------------------------------------------------------------------
    # 6. Bounded Loop: Budget Exhaustion
    # -------------------------------------------------------------------------

    def test_loop_halts_when_budget_is_exhausted(self):
        """Verify that loop strictly terminates when iteration_budget is reached."""
        wo = self._create_work_order(iteration_budget=2)
        exec_id = new_execution_id()

        execution = ProgrammerExecution(
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            task_id=wo.task_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            status=ProgrammerExecutionStatus.REQUESTED,
        )

        mock_context = MagicMock()
        mock_context.workspace = ProgrammerWorkspace(
            workspace_id="pws-test",
            project_id=self.project_id,
            root_path=self.workspace_root,
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            writable_paths=["src/auth"],
        )
        mock_context.status = "ACTIVE"

        # Verification always fails
        mock_runner = MagicMock()
        mock_runner.run.return_value = self._create_failing_runner_result(exec_id, wo.work_order_id, "AssertionError: Persistent failure")

        mock_executor = MagicMock()
        mock_executor.execute.return_value = ControlledExecutionOutcome(
            execution=execution,
            context=mock_context,
            result=ProgrammerResult(
                result_id="pres-001",
                execution_id=exec_id,
                work_order_id=wo.work_order_id,
                task_id=wo.task_id,
                project_id=wo.project_id,
                correlation_id=wo.correlation_id,
                status=ProgrammerResultStatus.PARTIAL,
                summary="Turn 1 done",
                summary_for_manager="Turn 1 done",
            ),
        )
        mock_executor.execute_correction.return_value = ControlledExecutionOutcome(
            execution=execution,
            context=mock_context,
            result=ProgrammerResult(
                result_id="pres-002",
                execution_id=exec_id,
                work_order_id=wo.work_order_id,
                task_id=wo.task_id,
                project_id=wo.project_id,
                correlation_id=wo.correlation_id,
                status=ProgrammerResultStatus.PARTIAL,
                summary="Turn 2 done",
                summary_for_manager="Turn 2 done",
            ),
        )

        loop = BoundedCorrectionLoop(
            executor=mock_executor,
            verification_runner_factory=lambda ctx: mock_runner,
        )

        loop_result = loop.run(
            work_order=wo,
            execution=execution,
        )

        self.assertFalse(loop_result.is_success)
        self.assertTrue(loop_result.is_exhausted)
        self.assertEqual(loop_result.total_iterations, 2)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.FAILED)
        self.assertEqual(loop_result.result.status, ProgrammerResultStatus.FAILED)
        # Ensure only 1 correction was attempted (total 2 attempts = initial + 1 correction)
        self.assertEqual(mock_executor.execute_correction.call_count, 1)

    # -------------------------------------------------------------------------
    # 7. Out-of-Scope Modification Halts and Blocks
    # -------------------------------------------------------------------------

    def test_unauthorized_scope_modification_halts_and_blocks(self):
        """Verify that repository changes outside writable_paths halt the loop and transition to BLOCKED."""
        wo = self._create_work_order(iteration_budget=3, writable_paths=("src/auth",))
        exec_id = new_execution_id()

        execution = ProgrammerExecution(
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            task_id=wo.task_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            status=ProgrammerExecutionStatus.REQUESTED,
        )

        mock_context = MagicMock()
        mock_context.workspace = ProgrammerWorkspace(
            workspace_id="pws-test",
            project_id=self.project_id,
            root_path=self.workspace_root,
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            writable_paths=["src/auth"],
        )
        mock_context.status = "ACTIVE"

        mock_runner = MagicMock()
        mock_runner.run.return_value = self._create_passing_runner_result(exec_id, wo.work_order_id)

        # Diff verifier reports unauthorized change in README.md
        mock_diff_verifier = MagicMock()
        mock_diff_verifier.verify_workspace.return_value = DiffVerification(
            verification_id="vdiff-001",
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            files_changed=["README.md"],
            unauthorized_changes=[{"path": "README.md", "reason": "Not writable"}],
            scope_status=VerificationStatus.FAIL,
        )

        mock_executor = MagicMock()
        mock_executor.execute.return_value = ControlledExecutionOutcome(
            execution=execution,
            context=mock_context,
            result=ProgrammerResult(
                result_id="pres-001",
                execution_id=exec_id,
                work_order_id=wo.work_order_id,
                task_id=wo.task_id,
                project_id=wo.project_id,
                correlation_id=wo.correlation_id,
                status=ProgrammerResultStatus.PARTIAL,
                summary="Turn 1 done",
                summary_for_manager="Turn 1 done",
            ),
        )

        loop = BoundedCorrectionLoop(
            executor=mock_executor,
            verification_runner_factory=lambda ctx: mock_runner,
            diff_verifier=mock_diff_verifier,
        )

        loop_result = loop.run(
            work_order=wo,
            execution=execution,
        )

        self.assertFalse(loop_result.is_success)
        self.assertTrue(loop_result.is_blocked)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.BLOCKED)
        self.assertEqual(loop_result.result.status, ProgrammerResultStatus.BLOCKED)
        self.assertTrue(len(loop_result.blockers) > 0)
        self.assertEqual(loop_result.blockers[0].category, ProgrammerBlockerCategory.SCOPE)
        # Invariant: loop MUST NOT continue or attempt another turn
        mock_executor.execute_correction.assert_not_called()

    # -------------------------------------------------------------------------
    # 8. Inconclusive UNVERIFIED Halts Without Blind Retries
    # -------------------------------------------------------------------------

    def test_inconclusive_unverified_halts_without_blind_retries(self):
        """Verify that UNVERIFIED verification halts and escalates to BLOCKED without burning retries."""
        wo = self._create_work_order(iteration_budget=3)
        exec_id = new_execution_id()

        execution = ProgrammerExecution(
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            task_id=wo.task_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            status=ProgrammerExecutionStatus.REQUESTED,
        )

        mock_context = MagicMock()
        mock_context.workspace = ProgrammerWorkspace(
            workspace_id="pws-test",
            project_id=self.project_id,
            root_path=self.workspace_root,
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            writable_paths=["src/auth"],
        )
        mock_context.status = "ACTIVE"

        # Runner returns no executable checks
        mock_runner = MagicMock()
        mock_runner.run.return_value = VerificationRunnerResult(
            checks=[],
            evidence=[],
        )

        # Aggregator returns UNVERIFIED
        mock_aggregator = MagicMock()
        mock_aggregator.aggregate.return_value = VerificationSummary(
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            overall_status=VerificationSummaryStatus.UNVERIFIED,
            verification_checks=[],
            acceptance_results=[],
        )

        mock_executor = MagicMock()
        mock_executor.execute.return_value = ControlledExecutionOutcome(
            execution=execution,
            context=mock_context,
            result=ProgrammerResult(
                result_id="pres-001",
                execution_id=exec_id,
                work_order_id=wo.work_order_id,
                task_id=wo.task_id,
                project_id=wo.project_id,
                correlation_id=wo.correlation_id,
                status=ProgrammerResultStatus.PARTIAL,
                summary="Turn 1 done",
                summary_for_manager="Turn 1 done",
            ),
        )

        loop = BoundedCorrectionLoop(
            executor=mock_executor,
            verification_runner_factory=lambda ctx: mock_runner,
            evidence_aggregator=mock_aggregator,
        )

        loop_result = loop.run(
            work_order=wo,
            execution=execution,
        )

        self.assertFalse(loop_result.is_success)
        self.assertTrue(loop_result.is_blocked)
        self.assertEqual(loop_result.final_status, VerificationSummaryStatus.UNVERIFIED)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.BLOCKED)
        self.assertEqual(loop_result.result.status, ProgrammerResultStatus.BLOCKED)
        self.assertEqual(loop_result.blockers[0].category, ProgrammerBlockerCategory.VERIFICATION)
        # Must not retry blindly
        mock_executor.execute_correction.assert_not_called()

    # -------------------------------------------------------------------------
    # 9. Fatal Agent Crash Halts Immediately
    # -------------------------------------------------------------------------

    def test_fatal_agent_crash_during_correction_halts_immediately(self):
        """Verify that if the agent backend fatally crashes during correction, loop halts immediately."""
        wo = self._create_work_order(iteration_budget=3)
        exec_id = new_execution_id()

        execution = ProgrammerExecution(
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            task_id=wo.task_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            status=ProgrammerExecutionStatus.REQUESTED,
        )

        mock_context = MagicMock()
        mock_context.workspace = ProgrammerWorkspace(
            workspace_id="pws-test",
            project_id=self.project_id,
            root_path=self.workspace_root,
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            writable_paths=["src/auth"],
        )
        mock_context.status = "ACTIVE"

        mock_runner = MagicMock()
        mock_runner.run.return_value = self._create_failing_runner_result(exec_id, wo.work_order_id, "Failure on attempt 1")

        mock_executor = MagicMock()
        mock_executor.execute.return_value = ControlledExecutionOutcome(
            execution=execution,
            context=mock_context,
            result=ProgrammerResult(
                result_id="pres-001",
                execution_id=exec_id,
                work_order_id=wo.work_order_id,
                task_id=wo.task_id,
                project_id=wo.project_id,
                correlation_id=wo.correlation_id,
                status=ProgrammerResultStatus.PARTIAL,
                summary="Turn 1 done",
                summary_for_manager="Turn 1 done",
            ),
        )

        # Fatal backend failure on correction
        crash_result = ProgrammerResult(
            result_id="pres-crash",
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            task_id=wo.task_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            status=ProgrammerResultStatus.FAILED,
            summary="Backend process crashed with SIGSEGV",
            summary_for_manager="Agent crash",
        )
        mock_executor.execute_correction.return_value = ControlledExecutionOutcome(
            execution=execution,
            context=mock_context,
            result=crash_result,
            error_message="Backend process crashed with SIGSEGV",
        )

        loop = BoundedCorrectionLoop(
            executor=mock_executor,
            verification_runner_factory=lambda ctx: mock_runner,
        )

        loop_result = loop.run(
            work_order=wo,
            execution=execution,
        )

        self.assertFalse(loop_result.is_success)
        self.assertTrue(loop_result.is_failed)
        self.assertEqual(loop_result.result.status, ProgrammerResultStatus.FAILED)
        self.assertIn("Backend process crashed with SIGSEGV", loop_result.error_message)
        # Should halt immediately and not attempt turn 3
        self.assertEqual(mock_executor.execute_correction.call_count, 1)

    # -------------------------------------------------------------------------
    # 10. Cancellation Handling
    # -------------------------------------------------------------------------

    def test_cancellation_halts_loop_cleanly(self):
        """Verify that loop.cancel() halts execution cleanly with CANCELLED status."""
        wo = self._create_work_order(iteration_budget=3)
        exec_id = new_execution_id()

        execution = ProgrammerExecution(
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            task_id=wo.task_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            status=ProgrammerExecutionStatus.REQUESTED,
        )

        loop = BoundedCorrectionLoop()
        loop.cancel()

        loop_result = loop.run(
            work_order=wo,
            execution=execution,
        )

        self.assertTrue(loop_result.is_cancelled)
        self.assertEqual(loop_result.result.status, ProgrammerResultStatus.CANCELLED)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.CANCELLED)

    # -------------------------------------------------------------------------
    # 11. End-to-End Controlled Execution Integration
    # -------------------------------------------------------------------------

    def test_end_to_end_controlled_executor_with_backend_mock(self):
        """
        Verify end-to-end integration of ControlledProgrammerExecutor.execute_correction
        using a mock backend with file modification capabilities.
        """
        wo = self._create_work_order(iteration_budget=2)
        exec_id = new_execution_id()

        execution = ProgrammerExecution(
            execution_id=exec_id,
            work_order_id=wo.work_order_id,
            task_id=wo.task_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            status=ProgrammerExecutionStatus.REQUESTED,
        )

        backend = MockCodingAgentBackend()
        executor = ControlledProgrammerExecutor(backend=backend)

        # 1. Execute initial turn
        outcome1 = executor.execute(
            work_order=wo,
            execution=execution,
            root_path_override=self.workspace_root,
        )
        self.assertTrue(outcome1.is_success)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.COMPLETING)

        # 2. Execute correction turn
        outcome2 = executor.execute_correction(
            work_order=wo,
            execution=execution,
            context=outcome1.context,
            correction_prompt="Fix the assertion error in test_auth.py",
            backend=backend,
        )
        self.assertTrue(outcome2.is_success)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.COMPLETING)
        self.assertEqual(outcome2.result.metadata.get("implementation_status"), "COMPLETED")


if __name__ == "__main__":
    unittest.main()

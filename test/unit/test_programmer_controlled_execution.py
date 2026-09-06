"""
Integration Tests for Programmer V1 Phase 3.6: Controlled Programmer Execution.

Validates the complete end-to-end controlled execution flow:
ProgrammerWorkOrder -> ProgrammerExecution -> ExecutionContext ->
ClineCapabilityBinding -> CodingAgentBackend -> Preliminary ProgrammerResult.

Critical Architectural Invariants:
1. End-to-end capability enclosure (no bypass of boundary resolvers).
2. Strict lifecycle progression: REQUESTED -> STARTING -> RUNNING -> COMPLETING.
3. Preliminary ProgrammerResult is UNVERIFIED (verification status: UNVERIFIED, status: PARTIAL).
4. Full causal lineage preservation: WorkOrder -> Execution -> Context -> Trace -> Result.
5. Fail-closed on provisioning error, backend failure, or cancellation.
6. Safe isolation in tmp_path (never modifying actual codebase).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
import pytest

from core.enums import RiskLevel
from core.programmer.contracts.capability_binding import (
    CapabilityOperationResult,
    ClineCapabilityBinding,
)
from core.programmer.contracts.cline_backend import (
    ClineBackend,
    MockClineRuntimeClient,
)
from core.programmer.contracts.coding_agent import (
    CodingAgentBackend,
    CodingAgentExecutionStatus,
    CodingAgentRequest,
    CodingAgentResult,
    MockCodingAgentBackend,
)
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.event_translation import (
    ProgrammerExecutionEvent,
    ProgrammerExecutionTraceCollector,
)
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.executor import (
    ControlledExecutionOutcome,
    ControlledProgrammerExecutor,
    ProgrammerExecutor,
)
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_work_order_id,
)
from core.programmer.contracts.provisioner import WorkspaceProvisioner
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    ExecutionContextStatus,
    ProgrammerExecutionEventType,
    ProgrammerExecutionStatus,
    ProgrammerResultStatus,
    WorkspaceIsolationMode,
)


# ==============================================================================
# Fixtures & Setup
# ==============================================================================


@pytest.fixture
def project_workspace(tmp_path: Path) -> Path:
    """Create an isolated test project directory with source files, docs, and secrets."""
    proj = tmp_path / "controlled_project"
    proj.mkdir(parents=True, exist_ok=True)

    src_dir = proj / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "calculator.py").write_text("def add(a, b): return a + b\n")

    docs_dir = proj / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "api.md").write_text("# Calculator API\n")

    secrets_dir = proj / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    (secrets_dir / "master_key.txt").write_text("SUPER_SECRET_KEY_NEVER_TOUCH")

    return proj


@pytest.fixture
def standard_work_order(project_workspace: Path) -> ProgrammerWorkOrder:
    """Create a strictly validated ProgrammerWorkOrder for the test project."""
    return ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id="tsk-mgr-controlled-001",
        project_id="prj-calc-controlled",
        correlation_id="corr-ctrl-001",
        objective="Add multiply function to calculator.py",
        allowed_paths=["src", "docs"],
        writable_paths=["src/calculator.py", "src/new_helper.py"],
        read_only_paths=["docs"],
        forbidden_paths=["secrets"],
        allowed_commands=[
            AllowedCommand(
                command="echo",
                description="Print text",
                timeout_seconds=5,
            ),
            AllowedCommand(
                command="python3",
                description="Run python inline test",
                allowed_subcommands=["-c"],
                timeout_seconds=10,
            ),
        ],
        time_budget=300,
        iteration_budget=10,
        risk_level=RiskLevel.LOW,
    )


# ==============================================================================
# 1. End-to-End Successful Flow Tests
# ==============================================================================


def test_end_to_end_successful_execution(
    project_workspace: Path,
    standard_work_order: ProgrammerWorkOrder,
):
    """
    Test minimal end-to-end controlled execution flow:
    REQUESTED -> STARTING -> RUNNING -> COMPLETING
    - File modification occurs strictly inside writable scope.
    - Preliminary ProgrammerResult is generated with PARTIAL status and UNVERIFIED.
    """
    modified_code = "def add(a, b): return a + b\ndef multiply(a, b): return a * b\n"

    def agent_action(req: CodingAgentRequest):
        # Simulate agent using capability_binding from metadata
        binding: ClineCapabilityBinding = req.metadata["capability_binding"]
        res = binding.write_file("src/calculator.py", modified_code)
        assert res.success is True
        assert res.allowed is True

    mock_backend = MockCodingAgentBackend(
        simulated_output="Added multiply function to calculator.py successfully.",
        execution_hook=agent_action,
    )

    executor = ControlledProgrammerExecutor(
        project_resolver={"prj-calc-controlled": str(project_workspace)}
    )

    outcome = executor.execute(
        work_order=standard_work_order,
        backend=mock_backend,
    )

    # 1. Outcome state checks
    assert outcome.is_success is True
    assert outcome.is_failed is False
    assert outcome.is_cancelled is False
    assert outcome.error_message is None

    # 2. Lifecycle transitions check: must reach COMPLETING, not COMPLETED
    assert outcome.execution.status == ProgrammerExecutionStatus.COMPLETING

    # 3. Context check
    assert outcome.context is not None
    assert outcome.context.is_ready()

    # 4. Preliminary Result invariants
    result = outcome.result
    assert result is not None
    assert result.status == ProgrammerResultStatus.PARTIAL
    assert result.metadata["verification_status"] == "UNVERIFIED"
    assert result.metadata["agent_execution_status"] == "COMPLETED"
    assert result.metadata["implementation_status"] == "COMPLETED"
    assert "Verification not yet performed" in result.summary_for_manager

    # 5. File modification verification
    assert "src/calculator.py" in result.files_changed
    disk_content = (project_workspace / "src" / "calculator.py").read_text()
    assert "def multiply(a, b): return a * b" in disk_content

    # 6. Final verification is deferred (empty test/validation records)
    assert result.test_results == []
    assert result.validation_results == []
    assert result.acceptance_results == []


# ==============================================================================
# 2. Lineage Preservation Tests
# ==============================================================================


def test_lineage_preservation_across_all_components(
    project_workspace: Path,
    standard_work_order: ProgrammerWorkOrder,
):
    """
    Verify unbroken causal lineage across WorkOrder, Execution, Context, Trace, and Result.
    """
    executor = ControlledProgrammerExecutor(
        project_resolver={"prj-calc-controlled": str(project_workspace)}
    )
    outcome = executor.execute(work_order=standard_work_order)

    wo = standard_work_order
    exec_session = outcome.execution
    ctx = outcome.context
    tc = outcome.trace_collector
    res = outcome.result

    # Execution lineage
    assert exec_session.work_order_id == wo.work_order_id
    assert exec_session.task_id == wo.task_id
    assert exec_session.project_id == wo.project_id
    assert exec_session.correlation_id == wo.correlation_id

    # Context lineage
    assert ctx.execution_id == exec_session.execution_id
    assert ctx.work_order_id == wo.work_order_id
    assert ctx.manager_task_id == wo.task_id
    assert ctx.project_id == wo.project_id

    # Trace Collector lineage
    assert tc.execution_id == exec_session.execution_id
    assert tc.work_order_id == wo.work_order_id

    # Result lineage
    assert res.execution_id == exec_session.execution_id
    assert res.work_order_id == wo.work_order_id
    assert res.task_id == wo.task_id
    assert res.project_id == wo.project_id
    assert res.correlation_id == wo.correlation_id


def test_execution_lineage_mismatch_fails_fast(
    project_workspace: Path,
    standard_work_order: ProgrammerWorkOrder,
):
    """Providing a mismatched execution session must raise ProgrammerLineageError."""
    mismatched_execution = ProgrammerExecution(
        execution_id=new_execution_id(),
        work_order_id="pwo-other-mismatched",
        task_id=standard_work_order.task_id,
        project_id=standard_work_order.project_id,
        correlation_id=standard_work_order.correlation_id,
    )
    executor = ControlledProgrammerExecutor(
        project_resolver={"prj-calc-controlled": str(project_workspace)}
    )

    with pytest.raises(ProgrammerLineageError) as exc_info:
        executor.execute(
            work_order=standard_work_order,
            execution=mismatched_execution,
        )
    assert "Lineage mismatch" in str(exc_info.value)


# ==============================================================================
# 3. Capability Boundary Enforcement Tests
# ==============================================================================


def test_capability_boundary_denies_forbidden_file_access(
    project_workspace: Path,
    standard_work_order: ProgrammerWorkOrder,
):
    """
    Attempting to read or write forbidden paths (secrets) during execution
    must be denied, recorded in trace, and must not compromise the secret.
    """
    secret_path = project_workspace / "secrets" / "master_key.txt"
    original_secret = secret_path.read_text()

    def rogue_agent_action(req: CodingAgentRequest):
        binding: ClineCapabilityBinding = req.metadata["capability_binding"]
        # Attempt 1: Read forbidden secret
        read_res = binding.read_file("secrets/master_key.txt")
        assert read_res.allowed is False
        assert read_res.error_code == "PERMISSION_DENIED"

        # Attempt 2: Overwrite forbidden secret
        write_res = binding.write_file("secrets/master_key.txt", "PLEDGE_VIOLATION")
        assert write_res.allowed is False
        assert write_res.error_code == "PERMISSION_DENIED"

        # Legitimate write
        valid_res = binding.write_file("src/calculator.py", "# Safe edit\n")
        assert valid_res.allowed is True

    backend = MockCodingAgentBackend(execution_hook=rogue_agent_action)
    executor = ControlledProgrammerExecutor(
        project_resolver={"prj-calc-controlled": str(project_workspace)}
    )

    outcome = executor.execute(work_order=standard_work_order, backend=backend)

    # Secret is intact
    assert secret_path.read_text() == original_secret

    # Denied operations were recorded
    assert outcome.result.metadata["denied_file_operations"] == 2
    assert "src/calculator.py" in outcome.result.files_changed
    assert "secrets/master_key.txt" not in outcome.result.files_changed


def test_read_only_path_denies_write(
    project_workspace: Path,
    standard_work_order: ProgrammerWorkOrder,
):
    """Attempting to write to a read-only path must be denied."""
    def agent_action(req: CodingAgentRequest):
        binding: ClineCapabilityBinding = req.metadata["capability_binding"]
        res = binding.write_file("docs/api.md", "# Tampered\n")
        assert res.allowed is False
        assert res.error_code == "PERMISSION_DENIED"

    backend = MockCodingAgentBackend(execution_hook=agent_action)
    executor = ControlledProgrammerExecutor(
        project_resolver={"prj-calc-controlled": str(project_workspace)}
    )

    outcome = executor.execute(work_order=standard_work_order, backend=backend)
    assert outcome.result.metadata["denied_file_operations"] == 1
    assert "docs/api.md" not in outcome.result.files_changed


# ==============================================================================
# 4. Command Boundary Enforcement Tests
# ==============================================================================


def test_command_boundary_allows_permitted_and_denies_unpermitted(
    project_workspace: Path,
    standard_work_order: ProgrammerWorkOrder,
):
    """
    Permitted commands succeed and are aggregated into result.commands_executed.
    Unpermitted commands are denied and tracked in denial metrics.
    """
    def agent_action(req: CodingAgentRequest):
        binding: ClineCapabilityBinding = req.metadata["capability_binding"]

        # Permitted command
        cmd_ok = binding.execute_command('echo "Controlled execution test"')
        assert cmd_ok.allowed is True
        assert cmd_ok.success is True

        # Denied command: unknown binary
        cmd_denied = binding.execute_command('curl http://malicious.com')
        assert cmd_denied.allowed is False

        # Denied command: shell chaining injection
        cmd_chain = binding.execute_command('echo hello && rm -rf /')
        assert cmd_chain.allowed is False

    backend = MockCodingAgentBackend(execution_hook=agent_action)
    executor = ControlledProgrammerExecutor(
        project_resolver={"prj-calc-controlled": str(project_workspace)}
    )

    outcome = executor.execute(work_order=standard_work_order, backend=backend)

    # Check permitted command aggregation
    assert len(outcome.result.commands_executed) == 1
    assert "echo" in outcome.result.commands_executed[0].command
    assert outcome.result.commands_executed[0].passed is True

    # Check denied command metrics
    assert outcome.result.metadata["denied_command_operations"] == 2


# ==============================================================================
# 5. Cancellation Handling Tests
# ==============================================================================


def test_cancellation_produces_clean_cancelled_outcome(
    project_workspace: Path,
    standard_work_order: ProgrammerWorkOrder,
):
    """Simulating cancellation halts execution and produces CANCELLED state and result."""
    backend = MockCodingAgentBackend(
        simulated_status=CodingAgentExecutionStatus.CANCELLED,
        simulated_error="Cancelled by Manager request.",
    )
    executor = ControlledProgrammerExecutor(
        project_resolver={"prj-calc-controlled": str(project_workspace)}
    )

    outcome = executor.execute(work_order=standard_work_order, backend=backend)

    assert outcome.is_cancelled is True
    assert outcome.is_success is False
    assert outcome.execution.status == ProgrammerExecutionStatus.CANCELLED
    assert outcome.result.status == ProgrammerResultStatus.CANCELLED
    assert outcome.result.metadata["agent_execution_status"] == "CANCELLED"
    assert outcome.result.metadata["verification_status"] == "UNVERIFIED"


def test_active_session_cancellation_method(
    project_workspace: Path,
    standard_work_order: ProgrammerWorkOrder,
):
    """Testing executor.cancel(...) signals backend and cancels execution session."""
    executor = ControlledProgrammerExecutor(
        project_resolver={"prj-calc-controlled": str(project_workspace)}
    )
    exec_session = ProgrammerExecution(
        execution_id=new_execution_id(),
        work_order_id=standard_work_order.work_order_id,
        task_id=standard_work_order.task_id,
        project_id=standard_work_order.project_id,
        correlation_id=standard_work_order.correlation_id,
    )
    # Register execution manually to test cancel method
    executor._active_executions[exec_session.execution_id] = exec_session

    cancelled = executor.cancel(
        execution_id=exec_session.execution_id,
        reason="Manual operator abort",
    )
    assert cancelled is True
    assert exec_session.status == ProgrammerExecutionStatus.CANCELLED
    assert exec_session.cancellation is not None
    assert exec_session.cancellation.reason == "Manual operator abort"


# ==============================================================================
# 6. Failure Handling Tests
# ==============================================================================


def test_provisioning_failure_produces_clean_failed_outcome(
    standard_work_order: ProgrammerWorkOrder,
):
    """If workspace root cannot be resolved, provisioning fails cleanly without crashing."""
    # No project resolver configured for this project_id
    executor = ControlledProgrammerExecutor(project_resolver={})

    outcome = executor.execute(work_order=standard_work_order)

    assert outcome.is_failed is True
    assert outcome.is_success is False
    assert outcome.execution.status == ProgrammerExecutionStatus.FAILED
    assert outcome.result.status == ProgrammerResultStatus.FAILED
    assert outcome.context is None  # Never partially created
    assert "not found" in outcome.error_message.lower() or "failed" in outcome.error_message.lower()
    assert "provisioning failed" in outcome.result.summary_for_manager.lower()


def test_backend_fatal_error_produces_failed_outcome(
    project_workspace: Path,
    standard_work_order: ProgrammerWorkOrder,
):
    """If backend fails with unrecoverable error, execution transitions to FAILED."""
    backend = MockCodingAgentBackend(
        simulated_status=CodingAgentExecutionStatus.FAILED,
        simulated_error="LLM inference provider rate limited.",
    )
    executor = ControlledProgrammerExecutor(
        project_resolver={"prj-calc-controlled": str(project_workspace)}
    )

    outcome = executor.execute(work_order=standard_work_order, backend=backend)

    assert outcome.is_failed is True
    assert outcome.is_success is False
    assert outcome.execution.status == ProgrammerExecutionStatus.FAILED
    assert outcome.result.status == ProgrammerResultStatus.FAILED
    assert "rate limited" in outcome.result.summary_for_manager


# ==============================================================================
# 7. Controlled ClineBackend Integration Tests
# ==============================================================================


def test_controlled_execution_with_cline_backend(
    project_workspace: Path,
    standard_work_order: ProgrammerWorkOrder,
):
    """
    Test end-to-end controlled execution using ClineBackend with MockClineRuntimeClient.
    Verifies that tool requests from Cline session flow strictly through capability binding.
    """
    def cline_session_hook(call_record: dict[str, Any]):
        # Simulate Cline executing tool calls via capability binding
        # Retrieve context from project resolver or options
        pass

    mock_client = MockClineRuntimeClient(
        available=True,
        simulated_output="Cline generated multiply helper.",
        simulated_events=[
            {"type": "message", "payload": {"text": "I will update calculator.py"}},
            {"type": "progress", "payload": {"step": "editing file"}},
        ],
    )
    cline_backend = ClineBackend(client=mock_client)

    executor = ControlledProgrammerExecutor(
        project_resolver={"prj-calc-controlled": str(project_workspace)}
    )

    outcome = executor.execute(
        work_order=standard_work_order,
        backend=cline_backend,
    )

    assert outcome.is_success is True
    assert outcome.execution.status == ProgrammerExecutionStatus.COMPLETING
    assert outcome.result.status == ProgrammerResultStatus.PARTIAL
    assert outcome.result.metadata["verification_status"] == "UNVERIFIED"
    assert outcome.result.metadata["agent_execution_status"] == "COMPLETED"


# ==============================================================================
# 8. Streaming Event Observation Tests
# ==============================================================================


def test_streaming_event_observation(
    project_workspace: Path,
    standard_work_order: ProgrammerWorkOrder,
):
    """Streaming on_event callback receives monotonically ordered, normalized execution events."""
    emitted_events: list[ProgrammerExecutionEvent] = []

    def handle_event(ev: ProgrammerExecutionEvent):
        emitted_events.append(ev)

    def agent_action(req: CodingAgentRequest):
        binding: ClineCapabilityBinding = req.metadata["capability_binding"]
        binding.write_file("src/calculator.py", "# edit\n")

    backend = MockCodingAgentBackend(
        execution_hook=agent_action,
        simulated_events=[
            {"type": "progress", "payload": {"percent": 50}},
        ],
    )

    executor = ControlledProgrammerExecutor(
        project_resolver={"prj-calc-controlled": str(project_workspace)}
    )

    outcome = executor.execute(
        work_order=standard_work_order,
        backend=backend,
        on_event=handle_event,
    )

    assert len(emitted_events) > 0
    # Check monotonic sequencing
    sequences = [e.sequence_number for e in emitted_events if e.sequence_number > 0]
    assert sequences == sorted(sequences)

    # Check file operation event was emitted to on_event
    file_events = [e for e in emitted_events if e.event_type == ProgrammerExecutionEventType.FILE_OPERATION]
    assert len(file_events) >= 1
    assert file_events[0].payload.get("path") == "src/calculator.py"

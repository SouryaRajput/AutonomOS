"""
Unit Tests for Programmer V1 Phase 3.1: Coding Agent Backend Interface.

Tests the generic backend abstraction through which Programmer interacts with external coding agents:
- Valid backend request creation, validation, and lineage binding
- Invalid execution identity and non-ready context rejection
- Event correlation (execution_id, work_order_id, monotonic sequence, streaming listener)
- Cancellation contract (handshake, reason, state transition, result check)
- Structured result contract (metrics, token counts, success/failure determination)
- Full round-trip serialization and deserialization
- Backend interface invariants (abstract method enforcement, multi-engine support)
"""

from pathlib import Path
import pytest

from core.programmer.contracts.coding_agent import (
    CodingAgentBackend,
    CodingAgentCancellationRequest,
    CodingAgentCancellationResult,
    CodingAgentEvent,
    CodingAgentRequest,
    CodingAgentResult,
    MockCodingAgentBackend,
)
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.identifiers import (
    new_backend_event_id,
    new_backend_request_id,
    new_backend_result_id,
    new_execution_id,
    new_work_order_id,
    validate_backend_request_id,
)
from core.programmer.contracts.provisioner import WorkspaceProvisioner
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import (
    InvalidProgrammerIdError,
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    CodingAgentBackendType,
    CodingAgentEventType,
    CodingAgentExecutionStatus,
    ExecutionContextStatus,
    ProgrammerExecutionStatus,
)


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "test_project"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "src").mkdir(parents=True, exist_ok=True)
    (ws / "src" / "main.py").write_text("print('hello')")
    return ws


@pytest.fixture
def valid_work_order() -> ProgrammerWorkOrder:
    return ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id="tsk-mgr-777",
        project_id="prj-omega",
        correlation_id="corr-777",
        objective="Implement token validator module",
        allowed_paths=["src"],
        writable_paths=["src/main.py"],
        allowed_commands=[AllowedCommand(command="pytest")],
    )


@pytest.fixture
def valid_execution(valid_work_order: ProgrammerWorkOrder) -> ProgrammerExecution:
    return ProgrammerExecution(
        execution_id=new_execution_id(),
        work_order_id=valid_work_order.work_order_id,
        task_id=valid_work_order.manager_task_id,
        project_id=valid_work_order.project_id,
        correlation_id=valid_work_order.correlation_id,
        status=ProgrammerExecutionStatus.STARTING,
    )


@pytest.fixture
def ready_context(
    valid_work_order: ProgrammerWorkOrder,
    valid_execution: ProgrammerExecution,
    workspace_dir: Path,
) -> ProgrammerExecutionContext:
    provisioner = WorkspaceProvisioner(
        project_resolver={valid_work_order.project_id: str(workspace_dir)}
    )
    result = provisioner.provision(
        work_order=valid_work_order,
        execution=valid_execution,
    )
    assert result.is_ready() is True
    assert result.execution_context is not None
    return result.execution_context


# ==============================================================================
# 1. Valid Backend Request Contract
# ==============================================================================


def test_valid_backend_request_creation_and_validation(
    ready_context: ProgrammerExecutionContext,
    valid_execution: ProgrammerExecution,
    valid_work_order: ProgrammerWorkOrder,
) -> None:
    """A valid CodingAgentRequest binds prompt, context, and lineage cleanly."""
    request = CodingAgentRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        prompt="Implement the token validator in src/main.py",
        execution_context=ready_context,
        backend_type=CodingAgentBackendType.MOCK,
        timeout_seconds=300,
        max_iterations=10,
    )

    # Validate does not raise
    request.validate()

    assert request.request_id.startswith("cbreq-")
    validate_backend_request_id(request.request_id)
    assert request.execution_id == valid_execution.execution_id
    assert request.work_order_id == valid_work_order.work_order_id
    assert request.backend_type == CodingAgentBackendType.MOCK
    assert request.trace["execution_id"] == valid_execution.execution_id
    assert request.trace["work_order_id"] == valid_work_order.work_order_id
    assert request.trace["workspace_id"] == ready_context.workspace_id
    assert request.trace["project_id"] == valid_work_order.project_id
    assert request.trace["manager_task_id"] == valid_work_order.manager_task_id


def test_backend_request_string_backend_type_coercion(
    ready_context: ProgrammerExecutionContext,
    valid_execution: ProgrammerExecution,
    valid_work_order: ProgrammerWorkOrder,
) -> None:
    """String backend types coerce safely to enum."""
    req_cline = CodingAgentRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        prompt="Write code",
        execution_context=ready_context,
        backend_type="CLINE",  # type: ignore[arg-type]
    )
    assert req_cline.backend_type == CodingAgentBackendType.CLINE

    req_custom = CodingAgentRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        prompt="Write code",
        execution_context=ready_context,
        backend_type="UNKNOWN_ENGINE",  # type: ignore[arg-type]
    )
    assert req_custom.backend_type == CodingAgentBackendType.CUSTOM


# ==============================================================================
# 2. Invalid Execution Identity & Lineage Rejection
# ==============================================================================


def test_request_rejects_malformed_execution_id(
    ready_context: ProgrammerExecutionContext,
    valid_work_order: ProgrammerWorkOrder,
) -> None:
    """Execution IDs not adhering to 'pexec-' format are strictly rejected."""
    request = CodingAgentRequest(
        execution_id="invalid-id-format",
        work_order_id=valid_work_order.work_order_id,
        prompt="Do work",
        execution_context=ready_context,
    )
    with pytest.raises(InvalidProgrammerIdError) as exc:
        request.validate()
    assert "execution_id" in str(exc.value)


def test_request_rejects_malformed_work_order_id(
    ready_context: ProgrammerExecutionContext,
    valid_execution: ProgrammerExecution,
) -> None:
    """Work order IDs not adhering to 'pwo-' format are strictly rejected."""
    request = CodingAgentRequest(
        execution_id=valid_execution.execution_id,
        work_order_id="bad-work-order",
        prompt="Do work",
        execution_context=ready_context,
    )
    with pytest.raises(InvalidProgrammerIdError) as exc:
        request.validate()
    assert "work_order_id" in str(exc.value)


def test_request_rejects_empty_prompt(
    ready_context: ProgrammerExecutionContext,
    valid_execution: ProgrammerExecution,
    valid_work_order: ProgrammerWorkOrder,
) -> None:
    """Empty or whitespace prompts are rejected."""
    request = CodingAgentRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        prompt="   ",
        execution_context=ready_context,
    )
    with pytest.raises(ProgrammerValidationError) as exc:
        request.validate()
    assert "non-empty prompt" in str(exc.value)


def test_request_rejects_lineage_mismatch_with_context(
    ready_context: ProgrammerExecutionContext,
    valid_execution: ProgrammerExecution,
    valid_work_order: ProgrammerWorkOrder,
) -> None:
    """Request referencing different execution or work order than the context is rejected."""
    # Mismatched execution ID
    req1 = CodingAgentRequest(
        execution_id=new_execution_id(),  # Different from ready_context.execution_id
        work_order_id=valid_work_order.work_order_id,
        prompt="Do work",
        execution_context=ready_context,
    )
    with pytest.raises(ProgrammerLineageError) as exc1:
        req1.validate()
    assert "Lineage mismatch" in str(exc1.value)

    # Mismatched work order ID
    req2 = CodingAgentRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=new_work_order_id(),  # Different from ready_context.work_order_id
        prompt="Do work",
        execution_context=ready_context,
    )
    with pytest.raises(ProgrammerLineageError) as exc2:
        req2.validate()
    assert "Lineage mismatch" in str(exc2.value)


def test_request_rejects_non_ready_execution_context(
    valid_execution: ProgrammerExecution,
    valid_work_order: ProgrammerWorkOrder,
    workspace_dir: Path,
) -> None:
    """A context not in READY status strictly cannot be passed to a backend."""
    ws = ProgrammerWorkspace.from_work_order(valid_work_order, str(workspace_dir))
    failed_ctx = ProgrammerExecutionContext(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        workspace_id=ws.workspace_id,
        project_id=valid_work_order.project_id,
        correlation_id=valid_work_order.correlation_id,
        workspace=ws,
        status=ExecutionContextStatus.FAILED,
        error_message="Provisioning failed previously",
    )

    request = CodingAgentRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        prompt="Do work",
        execution_context=failed_ctx,
    )
    with pytest.raises(ProgrammerError) as exc:
        request.validate()
    assert exc.value.code == "FAILED_CONTEXT_ERROR"
    assert "cannot be used for agent execution" in str(exc.value)


def test_request_rejects_non_positive_budgets(
    ready_context: ProgrammerExecutionContext,
    valid_execution: ProgrammerExecution,
    valid_work_order: ProgrammerWorkOrder,
) -> None:
    """Non-positive timeout or iteration budgets fail validation."""
    req_bad_timeout = CodingAgentRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        prompt="Do work",
        execution_context=ready_context,
        timeout_seconds=0,
    )
    with pytest.raises(ProgrammerValidationError) as exc1:
        req_bad_timeout.validate()
    assert "timeout_seconds must be positive" in str(exc1.value)

    req_bad_iter = CodingAgentRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        prompt="Do work",
        execution_context=ready_context,
        max_iterations=-5,
    )
    with pytest.raises(ProgrammerValidationError) as exc2:
        req_bad_iter.validate()
    assert "max_iterations must be positive" in str(exc2.value)


# ==============================================================================
# 3. Event Correlation & Streaming Listener
# ==============================================================================


def test_event_correlation_and_streaming_listener(
    ready_context: ProgrammerExecutionContext,
    valid_execution: ProgrammerExecution,
    valid_work_order: ProgrammerWorkOrder,
) -> None:
    """Events emitted during execution preserve lineage and monotonic sequence numbers."""
    backend = MockCodingAgentBackend(
        simulated_output="File edited cleanly.",
        simulated_events=[
            {"type": "THINKING", "payload": {"thought": "Inspecting codebase structure"}},
            {"type": "TOOL_CALL_REQUESTED", "payload": {"tool": "read_file", "path": "src/main.py"}},
            {"type": "TOOL_CALL_COMPLETED", "payload": {"tool": "read_file", "status": "SUCCESS"}},
            {"type": "PROGRESS_REPORTED", "payload": {"step": "Code patched", "progress_pct": 80}},
        ],
    )

    received_events: list[CodingAgentEvent] = []

    def on_event(ev: CodingAgentEvent) -> None:
        received_events.append(ev)

    request = CodingAgentRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        prompt="Update main.py",
        execution_context=ready_context,
    )

    result = backend.execute(request, event_handler=on_event)

    assert result.is_successful() is True
    # Initial AGENT_STARTED + 4 simulated events + 1 EXECUTION_COMPLETED = 6 events
    assert len(received_events) == 6

    # Verify event correlation and ordering
    prev_seq = 0
    for ev in received_events:
        assert ev.execution_id == valid_execution.execution_id
        assert ev.work_order_id == valid_work_order.work_order_id
        assert ev.trace["execution_id"] == valid_execution.execution_id
        assert ev.sequence_number > prev_seq
        prev_seq = ev.sequence_number

    assert received_events[0].event_type == CodingAgentEventType.AGENT_STARTED
    assert received_events[1].event_type == CodingAgentEventType.THINKING
    assert received_events[2].event_type == CodingAgentEventType.TOOL_CALL_REQUESTED
    assert received_events[3].event_type == CodingAgentEventType.TOOL_CALL_COMPLETED
    assert received_events[4].event_type == CodingAgentEventType.PROGRESS_REPORTED
    assert received_events[5].event_type == CodingAgentEventType.EXECUTION_COMPLETED


# ==============================================================================
# 4. Cancellation Contract
# ==============================================================================


def test_cancellation_contract_and_state_transition(
    ready_context: ProgrammerExecutionContext,
    valid_execution: ProgrammerExecution,
    valid_work_order: ProgrammerWorkOrder,
) -> None:
    """Cancellation handshake updates status and records reason."""
    backend = MockCodingAgentBackend()

    cancel_req = CodingAgentCancellationRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        reason="Task cancelled by user request",
    )
    cancel_req.validate()

    cancel_res = backend.cancel(cancel_req)

    assert cancel_res.cancelled is True
    assert cancel_res.execution_id == valid_execution.execution_id
    assert cancel_res.work_order_id == valid_work_order.work_order_id
    assert cancel_res.reason == "Task cancelled by user request"
    assert backend.get_status(valid_execution.execution_id) == CodingAgentExecutionStatus.CANCELLED

    # Executing now yields a cancelled result
    req = CodingAgentRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        prompt="Proceed with task",
        execution_context=ready_context,
    )
    result = backend.execute(req)

    assert result.status == CodingAgentExecutionStatus.CANCELLED
    assert result.is_cancelled() is True
    assert result.is_successful() is False
    assert "cancelled" in (result.error_message or "").lower()


def test_cancellation_request_validation() -> None:
    """Cancellation request requires valid IDs and non-empty reason."""
    with pytest.raises(InvalidProgrammerIdError):
        CodingAgentCancellationRequest(
            execution_id="invalid-exec-id",
            work_order_id="pwo-valid123",
            reason="Stop",
        ).validate()

    with pytest.raises(ProgrammerValidationError):
        CodingAgentCancellationRequest(
            execution_id=new_execution_id(),
            work_order_id=new_work_order_id(),
            reason="   ",
        ).validate()


# ==============================================================================
# 5. Structured Result Contract
# ==============================================================================


def test_structured_result_contract_success(
    ready_context: ProgrammerExecutionContext,
    valid_execution: ProgrammerExecution,
    valid_work_order: ProgrammerWorkOrder,
) -> None:
    """Successful agent execution yields COMPLETED status with metrics."""
    backend = MockCodingAgentBackend(
        simulated_output="Created auth token handler and tests.",
    )
    request = CodingAgentRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        prompt="Implement auth token handler",
        execution_context=ready_context,
    )

    result = backend.execute(request)

    assert result.result_id.startswith("cbres-")
    assert result.execution_id == valid_execution.execution_id
    assert result.work_order_id == valid_work_order.work_order_id
    assert result.status == CodingAgentExecutionStatus.COMPLETED
    assert result.is_successful() is True
    assert result.is_failed() is False
    assert result.is_cancelled() is False
    assert result.output_text == "Created auth token handler and tests."
    assert result.tokens_used["total_tokens"] > 0
    assert result.started_at is not None
    assert result.completed_at is not None


def test_structured_result_contract_failure(
    ready_context: ProgrammerExecutionContext,
    valid_execution: ProgrammerExecution,
    valid_work_order: ProgrammerWorkOrder,
) -> None:
    """Failure during agent execution yields FAILED status and error details."""
    backend = MockCodingAgentBackend(
        simulated_status=CodingAgentExecutionStatus.FAILED,
        simulated_error="Model context length exceeded during reasoning.",
    )
    request = CodingAgentRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        prompt="Implement auth token handler",
        execution_context=ready_context,
    )

    result = backend.execute(request)

    assert result.status == CodingAgentExecutionStatus.FAILED
    assert result.is_successful() is False
    assert result.is_failed() is True
    assert result.is_cancelled() is False
    assert result.error_message == "Model context length exceeded during reasoning."
    assert result.output_text == ""


def test_structured_result_contract_timed_out(
    valid_execution: ProgrammerExecution,
    valid_work_order: ProgrammerWorkOrder,
) -> None:
    """TIMED_OUT result status is classified as failed."""
    res = CodingAgentResult(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        status=CodingAgentExecutionStatus.TIMED_OUT,
        error_message="Execution exceeded 300s timeout.",
    )
    assert res.is_failed() is True
    assert res.is_successful() is False
    assert res.is_cancelled() is False


# ==============================================================================
# 6. Full Round-trip Serialization & Deserialization
# ==============================================================================


def test_serialization_and_deserialization_roundtrip(
    ready_context: ProgrammerExecutionContext,
    valid_execution: ProgrammerExecution,
    valid_work_order: ProgrammerWorkOrder,
) -> None:
    """All models support complete lossless to_dict, to_json, and from_dict roundtrip."""
    # 1. Request
    req = CodingAgentRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        prompt="Run unit tests and fix session bug",
        execution_context=ready_context,
        backend_type=CodingAgentBackendType.CLINE,
        system_prompt="You are an autonomous coding assistant",
        model_name="claude-3-7-sonnet",
        timeout_seconds=600,
        max_iterations=15,
        metadata={"priority": "HIGH"},
    )
    req_dict = req.to_dict()
    req_json = req.to_json()
    req_restored = CodingAgentRequest.from_dict(req_dict, execution_context=ready_context)
    assert req_restored.request_id == req.request_id
    assert req_restored.execution_id == req.execution_id
    assert req_restored.work_order_id == req.work_order_id
    assert req_restored.backend_type == CodingAgentBackendType.CLINE
    assert req_restored.timeout_seconds == 600
    assert req_restored.max_iterations == 15
    assert req_restored.metadata["priority"] == "HIGH"
    assert '"backend_type": "CLINE"' in req_json

    # 2. Event
    ev = CodingAgentEvent(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        event_type=CodingAgentEventType.TOOL_CALL_REQUESTED,
        payload={"tool": "edit_file", "path": "src/main.py"},
        sequence_number=4,
    )
    ev_dict = ev.to_dict()
    ev_json = ev.to_json()
    ev_restored = CodingAgentEvent.from_dict(ev_dict)
    assert ev_restored.event_id == ev.event_id
    assert ev_restored.event_type == CodingAgentEventType.TOOL_CALL_REQUESTED
    assert ev_restored.sequence_number == 4
    assert ev_restored.payload["tool"] == "edit_file"
    assert '"event_type": "TOOL_CALL_REQUESTED"' in ev_json

    # 3. Result
    res = CodingAgentResult(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        status=CodingAgentExecutionStatus.COMPLETED,
        output_text="Successfully patched session vulnerability.",
        tool_calls_count=5,
        iterations_count=2,
        tokens_used={"prompt_tokens": 500, "completion_tokens": 120, "total_tokens": 620},
    )
    res_dict = res.to_dict()
    res_json = res.to_json()
    res_restored = CodingAgentResult.from_dict(res_dict)
    assert res_restored.result_id == res.result_id
    assert res_restored.status == CodingAgentExecutionStatus.COMPLETED
    assert res_restored.output_text == res.output_text
    assert res_restored.tokens_used["total_tokens"] == 620
    assert '"status": "COMPLETED"' in res_json

    # 4. Cancellation Request & Result
    creq = CodingAgentCancellationRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        reason="Timed out waiting for tool response",
    )
    creq_dict = creq.to_dict()
    creq_restored = CodingAgentCancellationRequest.from_dict(creq_dict)
    assert creq_restored.reason == creq.reason

    cres = CodingAgentCancellationResult(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        cancelled=True,
        reason="Cancelled cleanly",
    )
    cres_dict = cres.to_dict()
    cres_restored = CodingAgentCancellationResult.from_dict(cres_dict)
    assert cres_restored.cancelled is True
    assert cres_restored.reason == "Cancelled cleanly"


# ==============================================================================
# 7. Backend Interface Invariants & Multi-Engine Support
# ==============================================================================


def test_abstract_backend_cannot_be_instantiated_directly() -> None:
    """CodingAgentBackend is an abstract base class and cannot be instantiated directly."""
    with pytest.raises(TypeError) as exc:
        CodingAgentBackend()  # type: ignore[abstract]
    assert "Can't instantiate abstract class" in str(exc.value)


def test_multi_engine_backend_type_support() -> None:
    """Backend interface natively supports multiple engines."""
    engines = [
        CodingAgentBackendType.CLINE,
        CodingAgentBackendType.CODEX,
        CodingAgentBackendType.CLAUDE_CODE,
        CodingAgentBackendType.CUSTOM,
        CodingAgentBackendType.MOCK,
    ]

    for engine in engines:
        backend = MockCodingAgentBackend(backend_type=engine)
        assert backend.backend_type == engine

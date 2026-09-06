"""
Unit Tests for Programmer V1 Phase 3.2: Cline Runtime Adapter.

Tests the concrete ClineBackend implementation:
- Cline startup and session initialization
- Prompt delivery and context boundary metadata encapsulation
- Execution correlation (execution_id, work_order_id, trace)
- Event translation (mapping native Cline event types to AutonomOS CodingAgentEventType)
- Successful completion handling and token metrics
- Failure handling and error reporting
- Graceful cancellation and session state tracking
- Unavailable Cline runtime detection and deterministic failure
- Malformed Cline events recovery and defensive handling
- Strict authority boundary enforcement (unready context, non-ready execution rejection)
"""

from pathlib import Path
import pytest

from core.programmer.contracts.cline_backend import (
    ClineBackend,
    ClineRuntimeClient,
    ClineSession,
    MockClineRuntimeClient,
)
from core.programmer.contracts.coding_agent import (
    CodingAgentCancellationRequest,
    CodingAgentEvent,
    CodingAgentRequest,
    CodingAgentResult,
)
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_work_order_id,
)
from core.programmer.contracts.provisioner import WorkspaceProvisioner
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import ProgrammerError, ProgrammerLineageError
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
    ws = tmp_path / "cline_project"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "src").mkdir(parents=True, exist_ok=True)
    (ws / "src" / "app.py").write_text("print('AutonomOS Cline Integration')")
    (ws / "tests").mkdir(parents=True, exist_ok=True)
    (ws / "secrets").mkdir(parents=True, exist_ok=True)
    (ws / "secrets" / "key.txt").write_text("TOP_SECRET")
    return ws


@pytest.fixture
def valid_work_order() -> ProgrammerWorkOrder:
    return ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id="tsk-mgr-888",
        project_id="prj-cline-test",
        correlation_id="corr-888",
        objective="Integrate authentication middleware",
        allowed_paths=["src", "tests"],
        writable_paths=["src/app.py"],
        read_only_paths=["tests"],
        forbidden_paths=["secrets"],
        allowed_commands=[AllowedCommand(command="pytest", allow_args=True)],
        iteration_budget=8,
        time_budget=450,
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
    res = provisioner.provision(valid_work_order, valid_execution)
    assert res.is_ready() is True
    assert res.execution_context is not None
    return res.execution_context


@pytest.fixture
def valid_request(
    ready_context: ProgrammerExecutionContext,
    valid_execution: ProgrammerExecution,
    valid_work_order: ProgrammerWorkOrder,
) -> CodingAgentRequest:
    return CodingAgentRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        prompt="Implement authentication middleware in src/app.py",
        execution_context=ready_context,
        backend_type=CodingAgentBackendType.CLINE,
        system_prompt="You are a senior systems engineer.",
        model_name="claude-3-7-sonnet",
        timeout_seconds=450,
        max_iterations=8,
    )


# ==============================================================================
# 1. Cline Startup & Initialization
# ==============================================================================


def test_cline_startup_and_initialization(
    valid_request: CodingAgentRequest,
) -> None:
    """ClineBackend initializes cleanly and starts a session via client."""
    client = MockClineRuntimeClient(available=True)
    backend = ClineBackend(client=client)

    assert backend.backend_type == CodingAgentBackendType.CLINE
    assert backend.is_runtime_available() is True

    result = backend.execute(valid_request)

    assert len(client.started_calls) == 1
    call = client.started_calls[0]
    assert call["execution_id"] == valid_request.execution_id
    assert call["work_order_id"] == valid_request.work_order_id
    assert result.is_successful() is True
    assert backend.get_status(valid_request.execution_id) == CodingAgentExecutionStatus.COMPLETED


# ==============================================================================
# 2. Prompt & Boundary Context Delivery
# ==============================================================================


def test_prompt_and_boundary_context_delivery(
    valid_request: CodingAgentRequest,
    ready_context: ProgrammerExecutionContext,
) -> None:
    """Verifies that prompt, system prompt, and context boundary metadata are delivered intact."""
    client = MockClineRuntimeClient(available=True)
    backend = ClineBackend(client=client)

    backend.execute(valid_request)

    assert len(client.started_calls) == 1
    call = client.started_calls[0]

    # Verify prompts
    assert call["prompt"] == "Implement authentication middleware in src/app.py"
    assert call["system_prompt"] == "You are a senior systems engineer."

    # Verify boundary encapsulation
    meta = call["context_metadata"]
    assert meta["workspace_root"] == ready_context.workspace.root_path
    assert "src" in meta["allowed_paths"]
    assert "src/app.py" in meta["writable_paths"]
    assert "secrets" in meta["forbidden_paths"]
    assert "pytest" in meta["allowed_commands"]
    assert meta["iteration_budget"] == 8
    assert meta["time_budget"] == 450
    assert meta["manager_task_id"] == "tsk-mgr-888"

    # Verify options
    opts = call["options"]
    assert opts["model_name"] == "claude-3-7-sonnet"
    assert opts["timeout_seconds"] == 450
    assert opts["max_iterations"] == 8
    assert opts["auto_approve_policy"] == "STRICT_BOUNDARY_INTERCEPTION"


# ==============================================================================
# 3. Execution Correlation & Lineage
# ==============================================================================


def test_execution_correlation_and_lineage(
    valid_request: CodingAgentRequest,
) -> None:
    """All events and results carry the authorizing execution_id, work_order_id, and trace."""
    client = MockClineRuntimeClient(
        available=True,
        simulated_events=[
            {"type": "assistant_thought", "payload": {"thought": "Analyzing request"}},
            {"type": "tool_request", "payload": {"tool": "read_file", "path": "src/app.py"}},
        ],
    )
    backend = ClineBackend(client=client)

    captured_events: list[CodingAgentEvent] = []

    def on_event(ev: CodingAgentEvent) -> None:
        captured_events.append(ev)

    result = backend.execute(valid_request, event_handler=on_event)

    # 1 start + 2 simulated + 1 complete = 4 events
    assert len(captured_events) == 4
    for ev in captured_events:
        assert ev.execution_id == valid_request.execution_id
        assert ev.work_order_id == valid_request.work_order_id
        assert ev.trace["execution_id"] == valid_request.execution_id
        assert ev.trace["work_order_id"] == valid_request.work_order_id

    assert result.execution_id == valid_request.execution_id
    assert result.work_order_id == valid_request.work_order_id
    assert result.trace["execution_id"] == valid_request.execution_id


# ==============================================================================
# 4. Native Event Translation
# ==============================================================================


def test_native_event_translation(
    valid_request: CodingAgentRequest,
) -> None:
    """Cline-native event types map deterministically to AutonomOS CodingAgentEventType."""
    client = MockClineRuntimeClient(
        available=True,
        simulated_events=[
            {"type": "assistant_thought", "payload": {"thought": "Step 1: Check routes"}},
            {"type": "tool_request", "payload": {"tool": "read_file", "path": "src/app.py"}},
            {"type": "tool_result", "payload": {"tool": "read_file", "content": "..."}},
            {"type": "progress", "payload": {"pct": 50}},
            {"type": "message", "payload": {"text": "Updated app.py"}},
            {"type": "checkpoint", "payload": {"hash": "abc1234"}},
        ],
    )
    backend = ClineBackend(client=client)

    events: list[CodingAgentEvent] = []
    backend.execute(valid_request, event_handler=events.append)

    # Expected order: AGENT_STARTED, THINKING, TOOL_CALL_REQUESTED, TOOL_CALL_COMPLETED,
    # PROGRESS_REPORTED, MESSAGE_EMITTED, CHECKPOINT_SAVED, EXECUTION_COMPLETED
    assert events[0].event_type == CodingAgentEventType.AGENT_STARTED
    assert events[1].event_type == CodingAgentEventType.THINKING
    assert events[2].event_type == CodingAgentEventType.TOOL_CALL_REQUESTED
    assert events[3].event_type == CodingAgentEventType.TOOL_CALL_COMPLETED
    assert events[4].event_type == CodingAgentEventType.PROGRESS_REPORTED
    assert events[5].event_type == CodingAgentEventType.MESSAGE_EMITTED
    assert events[6].event_type == CodingAgentEventType.CHECKPOINT_SAVED
    assert events[7].event_type == CodingAgentEventType.EXECUTION_COMPLETED


# ==============================================================================
# 5. Completion & Failure Handling
# ==============================================================================


def test_completion_handling(
    valid_request: CodingAgentRequest,
) -> None:
    """Successful session returns COMPLETED result with output and metrics."""
    client = MockClineRuntimeClient(
        available=True,
        simulated_status="COMPLETED",
        simulated_output="Authentication middleware added successfully.",
        tokens_used={"prompt_tokens": 300, "completion_tokens": 150, "total_tokens": 450},
    )
    backend = ClineBackend(client=client)

    result = backend.execute(valid_request)

    assert result.status == CodingAgentExecutionStatus.COMPLETED
    assert result.is_successful() is True
    assert result.output_text == "Authentication middleware added successfully."
    assert result.tokens_used["total_tokens"] == 450
    assert result.error_message is None


def test_failure_handling(
    valid_request: CodingAgentRequest,
) -> None:
    """Runtime failure returns FAILED result with error details."""
    client = MockClineRuntimeClient(
        available=True,
        simulated_status="FAILED",
        simulated_error="Cline subprocess encountered an unexpected exit code 1.",
    )
    backend = ClineBackend(client=client)

    result = backend.execute(valid_request)

    assert result.status == CodingAgentExecutionStatus.FAILED
    assert result.is_successful() is False
    assert result.is_failed() is True
    assert "unexpected exit code 1" in (result.error_message or "")


# ==============================================================================
# 6. Cancellation Contract
# ==============================================================================


def test_cancellation_dispatches_to_cline_session(
    valid_request: CodingAgentRequest,
) -> None:
    """Cancellation request dispatches to active Cline session and records state."""
    client = MockClineRuntimeClient(
        available=True,
        simulated_status="CANCELLED",
    )
    backend = ClineBackend(client=client)

    # Start session
    result = backend.execute(valid_request)
    assert result.status == CodingAgentExecutionStatus.CANCELLED

    # Cancel explicitly
    cancel_req = CodingAgentCancellationRequest(
        execution_id=valid_request.execution_id,
        work_order_id=valid_request.work_order_id,
        reason="Aborted by operator",
    )
    cancel_res = backend.cancel(cancel_req)

    assert cancel_res.cancelled is True
    assert cancel_res.reason == "Aborted by operator"
    assert backend.get_status(valid_request.execution_id) == CodingAgentExecutionStatus.CANCELLED
    assert len(client.cancelled_calls) == 1
    assert client.cancelled_calls[0]["reason"] == "Aborted by operator"


# ==============================================================================
# 7. Unavailable Cline Runtime Handling
# ==============================================================================


def test_unavailable_cline_runtime_fails_deterministically(
    valid_request: CodingAgentRequest,
) -> None:
    """When Cline runtime is not available, fail deterministically without crashing."""
    # Client explicitly configured as unavailable
    client = MockClineRuntimeClient(available=False)
    backend = ClineBackend(client=client)

    assert backend.is_runtime_available() is False

    emitted_events: list[CodingAgentEvent] = []
    result = backend.execute(valid_request, event_handler=emitted_events.append)

    assert result.status == CodingAgentExecutionStatus.FAILED
    assert result.is_successful() is False
    assert result.is_failed() is True
    assert "unavailable in the current environment" in (result.error_message or "")

    # Verifies failure event was emitted
    assert len(emitted_events) == 1
    assert emitted_events[0].event_type == CodingAgentEventType.EXECUTION_FAILED
    assert emitted_events[0].payload["code"] == "CLINE_RUNTIME_UNAVAILABLE"


def test_auto_detect_runtime_when_cline_not_in_path(
    valid_request: CodingAgentRequest,
) -> None:
    """When no client is provided and 'cline' is not in PATH, report unavailable."""
    backend = ClineBackend(client=None, cline_path="/non_existent_binary_xyz123")
    assert backend.is_runtime_available() is False

    result = backend.execute(valid_request)
    assert result.status == CodingAgentExecutionStatus.FAILED
    assert "unavailable in the current environment" in (result.error_message or "")


# ==============================================================================
# 8. Malformed Cline Events Recovery
# ==============================================================================


def test_malformed_cline_events_recovery(
    valid_request: CodingAgentRequest,
) -> None:
    """Defensively recovers from malformed/corrupted events without unhandled exceptions."""
    client = MockClineRuntimeClient(
        available=True,
        simulated_events=[
            # Case A: Non-dict string event
            "RAW_STRING_OUTPUT_FROM_STDOUT",
            # Case B: Event with missing 'payload' key
            {"type": "unknown_cline_event", "extra_field": "val"},
            # Case C: Event with non-dict payload
            {"type": "progress", "payload": "50% done"},
            # Case D: Raw number
            12345,
        ],
    )
    backend = ClineBackend(client=client)

    events: list[CodingAgentEvent] = []
    result = backend.execute(valid_request, event_handler=events.append)

    assert result.is_successful() is True
    # Initial start + 4 simulated + 1 complete = 6 events
    assert len(events) == 6

    # Verify that malformed events were converted safely
    raw_str_ev = events[1]
    assert raw_str_ev.event_type == CodingAgentEventType.MESSAGE_EMITTED
    assert raw_str_ev.payload.get("malformed") is True

    unknown_ev = events[2]
    assert unknown_ev.event_type == CodingAgentEventType.MESSAGE_EMITTED

    scalar_payload_ev = events[3]
    assert scalar_payload_ev.event_type == CodingAgentEventType.PROGRESS_REPORTED
    assert scalar_payload_ev.payload["data"] == "50% done"


# ==============================================================================
# 9. Authority Boundary Enforcement
# ==============================================================================


def test_unready_context_rejected_before_cline_startup(
    valid_work_order: ProgrammerWorkOrder,
    valid_execution: ProgrammerExecution,
    workspace_dir: Path,
) -> None:
    """Passing an unready context raises FAILED_CONTEXT_ERROR before Cline is invoked."""
    ws = ProgrammerWorkspace.from_work_order(valid_work_order, str(workspace_dir))
    unready_ctx = ProgrammerExecutionContext(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        workspace_id=ws.workspace_id,
        project_id=valid_work_order.project_id,
        correlation_id=valid_work_order.correlation_id,
        workspace=ws,
        status=ExecutionContextStatus.CREATING,  # Not READY!
    )

    client = MockClineRuntimeClient(available=True)
    backend = ClineBackend(client=client)

    req = CodingAgentRequest(
        execution_id=valid_execution.execution_id,
        work_order_id=valid_work_order.work_order_id,
        prompt="Do work",
        execution_context=unready_ctx,
    )

    with pytest.raises(ProgrammerError) as exc:
        backend.execute(req)
    assert exc.value.code == "FAILED_CONTEXT_ERROR"
    assert len(client.started_calls) == 0


def test_lineage_mismatch_rejected_before_cline_startup(
    ready_context: ProgrammerExecutionContext,
    valid_work_order: ProgrammerWorkOrder,
) -> None:
    """Passing a request with mismatched execution ID raises lineage error before Cline runs."""
    client = MockClineRuntimeClient(available=True)
    backend = ClineBackend(client=client)

    alien_execution_id = new_execution_id()
    req = CodingAgentRequest(
        execution_id=alien_execution_id,  # Mismatched!
        work_order_id=valid_work_order.work_order_id,
        prompt="Do work",
        execution_context=ready_context,
    )

    with pytest.raises(ProgrammerLineageError):
        backend.execute(req)
    assert len(client.started_calls) == 0

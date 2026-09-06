"""
Unit Tests for Programmer V1 Phase 2.5: Unified Programmer Execution Context.

Tests the runtime context binding:
WorkOrder + Execution + Workspace + Filesystem Policy + Command Policy + Budgets
- Valid context lifecycle (CREATING -> VALIDATING -> READY)
- Invalid workspace prevents READY
- Invalid command policy prevents READY
- Invalid filesystem policy prevents READY
- Lineage preservation (ManagerTask -> WorkOrder -> Execution -> Context)
- Rejection of cross-WorkOrder references
- Rejection of cross-Project references
- Rejection of cross-Execution references
- Usability guard: failed context strictly cannot be used
- Immutability enforcement after activation (READY)
- Runtime capability queries for future Cline adapter (may_read_file, may_write_file, may_delete_file, may_execute_command, resolve_working_directory)
- Serialization and deserialization roundtrip fidelity
"""

import os
from pathlib import Path
import pytest

from core.programmer.contracts.command_resolver import CommandBoundaryResolver, CommandRequest
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.filesystem_resolver import FilesystemBoundaryResolver
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_work_order_id,
    new_workspace_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import (
    InvalidPathScopeError,
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    CommandDecisionType,
    ExecutionContextStatus,
    PathBoundaryScope,
    ProgrammerExecutionStatus,
    WorkspaceIsolationMode,
)


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "src").mkdir(parents=True, exist_ok=True)
    (ws / "src" / "auth").mkdir(parents=True, exist_ok=True)
    (ws / "tests").mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture
def valid_work_order() -> ProgrammerWorkOrder:
    return ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id="tsk-manager-500",
        project_id="prj-alpha",
        correlation_id="corr-500",
        objective="Implement authentication session handler",
        allowed_paths=["src", "tests"],
        writable_paths=["src/auth/session.py", "tests/test_session.py"],
        read_only_paths=["src/auth/config.json"],
        forbidden_paths=["src/secrets.env"],
        allowed_commands=[
            AllowedCommand(command="pytest", allow_args=True),
            AllowedCommand(command="git", allowed_subcommands=["status", "diff"]),
        ],
        iteration_budget=10,
        time_budget=600,
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
def valid_workspace(
    valid_work_order: ProgrammerWorkOrder,
    valid_execution: ProgrammerExecution,
    workspace_dir: Path,
) -> ProgrammerWorkspace:
    return ProgrammerWorkspace(
        workspace_id=new_workspace_id(),
        project_id=valid_work_order.project_id,
        work_order_id=valid_work_order.work_order_id,
        execution_id=valid_execution.execution_id,
        root_path=str(workspace_dir),
        allowed_paths=list(valid_work_order.allowed_paths),
        writable_paths=list(valid_work_order.writable_paths),
        read_only_paths=list(valid_work_order.read_only_paths),
        forbidden_paths=list(valid_work_order.forbidden_paths),
        isolation_mode=WorkspaceIsolationMode.SHARED,
    )


# ==============================================================================
# 1. Valid Context Becomes READY
# ==============================================================================


def test_valid_context_becomes_ready(
    valid_work_order: ProgrammerWorkOrder,
    valid_execution: ProgrammerExecution,
    valid_workspace: ProgrammerWorkspace,
) -> None:
    """Valid components transition cleanly to READY status."""
    context = ProgrammerExecutionContext.build(
        work_order=valid_work_order,
        execution=valid_execution,
        workspace=valid_workspace,
    )

    assert context.status == ExecutionContextStatus.READY
    assert context.is_ready() is True
    assert context.is_failed() is False
    assert context.error_message is None

    # Verify budgets extracted from work order
    assert context.budgets["iteration_budget"] == 10
    assert context.budgets["time_budget"] == 600

    # Verify bound policies
    assert isinstance(context.filesystem_policy, FilesystemBoundaryResolver)
    assert isinstance(context.command_policy, CommandBoundaryResolver)


# ==============================================================================
# 2. Invalid Workspace Prevents READY
# ==============================================================================


def test_invalid_workspace_prevents_ready(
    valid_work_order: ProgrammerWorkOrder,
    valid_execution: ProgrammerExecution,
    workspace_dir: Path,
) -> None:
    """Corrupt workspace boundaries prevent READY status."""
    # Attempt build with workspace where writable path overlaps forbidden
    corrupt_ws = ProgrammerWorkspace.__new__(ProgrammerWorkspace)
    corrupt_ws.workspace_id = new_workspace_id()
    corrupt_ws.project_id = valid_work_order.project_id
    corrupt_ws.work_order_id = valid_work_order.work_order_id
    corrupt_ws.execution_id = valid_execution.execution_id
    corrupt_ws.root_path = str(workspace_dir)
    corrupt_ws.allowed_paths = ["src"]
    corrupt_ws.writable_paths = ["src/forbidden_file.txt"]
    corrupt_ws.read_only_paths = []
    corrupt_ws.forbidden_paths = ["src/forbidden_file.txt"]
    corrupt_ws.isolation_mode = WorkspaceIsolationMode.SHARED
    corrupt_ws.created_at = "2026-09-06T00:00:00Z"
    corrupt_ws.trace = {}
    corrupt_ws.metadata = {}

    with pytest.raises(InvalidPathScopeError):
        ProgrammerExecutionContext.build(
            work_order=valid_work_order,
            execution=valid_execution,
            workspace=corrupt_ws,
            raise_on_failure=True,
        )

    # With raise_on_failure=False, returns a FAILED context
    failed_ctx = ProgrammerExecutionContext.build(
        work_order=valid_work_order,
        execution=valid_execution,
        workspace=corrupt_ws,
        raise_on_failure=False,
    )
    assert failed_ctx.status == ExecutionContextStatus.FAILED
    assert failed_ctx.is_ready() is False
    assert failed_ctx.is_failed() is True
    assert failed_ctx.error_message is not None


# ==============================================================================
# 3. Invalid Work Order Prevents READY
# ==============================================================================


def test_invalid_work_order_prevents_ready(
    valid_execution: ProgrammerExecution,
    valid_workspace: ProgrammerWorkspace,
) -> None:
    """Invalid work order objective or policies prevent READY status."""
    invalid_wo = ProgrammerWorkOrder(
        work_order_id=valid_workspace.work_order_id,
        manager_task_id=valid_execution.task_id,
        project_id=valid_workspace.project_id,
        correlation_id=valid_execution.correlation_id,
        objective="",  # Empty objective fails validation
    )

    with pytest.raises(ProgrammerValidationError):
        ProgrammerExecutionContext.build(
            work_order=invalid_wo,
            execution=valid_execution,
            workspace=valid_workspace,
            raise_on_failure=True,
        )


# ==============================================================================
# 4. Lineage Preservation
# ==============================================================================


def test_lineage_preservation_across_all_levels(
    valid_work_order: ProgrammerWorkOrder,
    valid_execution: ProgrammerExecution,
    valid_workspace: ProgrammerWorkspace,
) -> None:
    """Full causal lineage is preserved: ManagerTask -> WorkOrder -> Execution -> Context."""
    context = ProgrammerExecutionContext.build(
        work_order=valid_work_order,
        execution=valid_execution,
        workspace=valid_workspace,
    )

    assert context.manager_task_id == valid_work_order.manager_task_id
    assert context.manager_task_id == valid_execution.task_id
    assert context.work_order_id == valid_work_order.work_order_id
    assert context.execution_id == valid_execution.execution_id
    assert context.project_id == valid_work_order.project_id
    assert context.correlation_id == valid_work_order.correlation_id


# ==============================================================================
# 5. Cross-Entity Mismatch Rejections (WorkOrder, Project, Execution)
# ==============================================================================


def test_context_cannot_reference_another_work_order(
    valid_work_order: ProgrammerWorkOrder,
    valid_execution: ProgrammerExecution,
    valid_workspace: ProgrammerWorkspace,
) -> None:
    """Mismatched work_order_id between execution and context is rejected."""
    mismatched_exec = ProgrammerExecution(
        execution_id=valid_execution.execution_id,
        work_order_id="pwo-different-999",
        task_id=valid_execution.task_id,
        project_id=valid_execution.project_id,
        correlation_id=valid_execution.correlation_id,
    )

    with pytest.raises(ProgrammerLineageError) as exc:
        ProgrammerExecutionContext.build(
            work_order=valid_work_order,
            execution=mismatched_exec,
            workspace=valid_workspace,
        )
    assert "Work order mismatch" in str(exc.value)


def test_context_cannot_reference_another_project(
    valid_work_order: ProgrammerWorkOrder,
    valid_execution: ProgrammerExecution,
    valid_workspace: ProgrammerWorkspace,
) -> None:
    """Mismatched project_id between workspace and work order is rejected."""
    valid_workspace.project_id = "prj-alien"

    with pytest.raises(ProgrammerLineageError) as exc:
        ProgrammerExecutionContext.build(
            work_order=valid_work_order,
            execution=valid_execution,
            workspace=valid_workspace,
        )
    assert "Project mismatch" in str(exc.value)


# ==============================================================================
# 6. Usability Guard: FAILED Context Cannot Be Used
# ==============================================================================


def test_failed_context_cannot_be_used(
    valid_work_order: ProgrammerWorkOrder,
    valid_execution: ProgrammerExecution,
    valid_workspace: ProgrammerWorkspace,
) -> None:
    """A context in FAILED status strictly refuses all capability queries."""
    # Build a failed context
    valid_workspace.project_id = "prj-mismatched"
    failed_ctx = ProgrammerExecutionContext.build(
        work_order=valid_work_order,
        execution=valid_execution,
        workspace=valid_workspace,
        raise_on_failure=False,
    )

    assert failed_ctx.is_failed() is True
    assert failed_ctx.is_ready() is False

    # Calling may_read_file must raise ProgrammerError
    with pytest.raises(ProgrammerError) as e1:
        failed_ctx.may_read_file("src/auth/session.py")
    assert "cannot be used for execution" in str(e1.value)

    # Calling may_write_file must raise ProgrammerError
    with pytest.raises(ProgrammerError) as e2:
        failed_ctx.may_write_file("src/auth/session.py")
    assert "cannot be used for execution" in str(e2.value)

    # Calling may_execute_command must raise ProgrammerError
    with pytest.raises(ProgrammerError) as e3:
        failed_ctx.may_execute_command("pytest")
    assert "cannot be used for execution" in str(e3.value)

    # Calling resolve_working_directory must raise ProgrammerError
    with pytest.raises(ProgrammerError) as e4:
        failed_ctx.resolve_working_directory("src")
    assert "cannot be used for execution" in str(e4.value)


# ==============================================================================
# 7. Immutability After Activation (READY)
# ==============================================================================


def test_context_is_immutable_after_activation(
    valid_work_order: ProgrammerWorkOrder,
    valid_execution: ProgrammerExecution,
    valid_workspace: ProgrammerWorkspace,
) -> None:
    """Attribute mutation attempts on a READY context must be rejected."""
    context = ProgrammerExecutionContext.build(
        work_order=valid_work_order,
        execution=valid_execution,
        workspace=valid_workspace,
    )

    assert context.is_ready() is True

    # Reassigning work_order_id must fail
    with pytest.raises(ProgrammerError) as exc1:
        context.work_order_id = "pwo-tampered"
    assert "immutable after activation" in str(exc1.value)

    # Reassigning workspace must fail
    with pytest.raises(ProgrammerError) as exc2:
        context.workspace = valid_workspace
    assert "immutable after activation" in str(exc2.value)

    # Reassigning status must fail
    with pytest.raises(ProgrammerError) as exc3:
        context.status = ExecutionContextStatus.FAILED
    assert "immutable after activation" in str(exc3.value)


# ==============================================================================
# 8. Runtime Capability Queries (for future Cline adapter)
# ==============================================================================


def test_runtime_capability_queries_delegate_correctly(
    valid_work_order: ProgrammerWorkOrder,
    valid_execution: ProgrammerExecution,
    valid_workspace: ProgrammerWorkspace,
) -> None:
    """Verify all capability query methods produce structured decisions accurately."""
    context = ProgrammerExecutionContext.build(
        work_order=valid_work_order,
        execution=valid_execution,
        workspace=valid_workspace,
    )

    # 1. may_read_file
    read_dec = context.may_read_file("src/auth/session.py")
    assert read_dec.allowed is True

    # 2. may_write_file on writable target
    write_dec1 = context.may_write_file("src/auth/session.py")
    assert write_dec1.allowed is True

    # 3. may_write_file on read-only target
    write_dec2 = context.may_write_file("src/auth/config.json")
    assert write_dec2.allowed is False
    assert write_dec2.scope == PathBoundaryScope.READ_ONLY

    # 4. may_delete_file on forbidden target
    del_dec = context.may_delete_file("src/secrets.env")
    assert del_dec.allowed is False
    assert del_dec.scope == PathBoundaryScope.FORBIDDEN

    # 5. may_rename_file across boundaries
    rename_dec = context.may_rename_file(
        source_path="src/auth/session.py",
        destination_path="tests/test_session.py",
    )
    assert rename_dec.allowed is True

    # 6. may_execute_command authorized
    cmd_dec1 = context.may_execute_command("pytest -v")
    assert cmd_dec1.allowed is True
    assert cmd_dec1.decision == CommandDecisionType.ALLOW

    # 7. may_execute_command unauthorized binary
    cmd_dec2 = context.may_execute_command("rm -rf src/")
    assert cmd_dec2.allowed is False
    assert cmd_dec2.decision == CommandDecisionType.DENY

    # 8. resolve_working_directory
    wd = context.resolve_working_directory("src/auth")
    assert wd.endswith(os.path.join("src", "auth"))


# ==============================================================================
# 9. Serialization Roundtrip Fidelity
# ==============================================================================


def test_execution_context_serialization_roundtrip(
    valid_work_order: ProgrammerWorkOrder,
    valid_execution: ProgrammerExecution,
    valid_workspace: ProgrammerWorkspace,
) -> None:
    """Verify to_dict(), to_json(), and from_dict() roundtrip preservation."""
    context = ProgrammerExecutionContext.build(
        work_order=valid_work_order,
        execution=valid_execution,
        workspace=valid_workspace,
    )

    data = context.to_dict()
    json_str = context.to_json()

    restored = ProgrammerExecutionContext.from_dict(
        data,
        workspace=valid_workspace,
        work_order=valid_work_order,
        execution=valid_execution,
    )

    assert restored.execution_id == context.execution_id
    assert restored.work_order_id == context.work_order_id
    assert restored.workspace_id == context.workspace_id
    assert restored.project_id == context.project_id
    assert restored.status == ExecutionContextStatus.READY
    assert restored.budgets == context.budgets

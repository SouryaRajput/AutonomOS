"""
Unit Tests for Programmer V1 Phase 2.1: Workspace Model & Execution Context.

Tests the deterministic domain models representing the workspace in which
a Programmer execution is authorized to operate:
- ProgrammerWorkspace & Workspace alias
- WorkspaceIsolationMode (SHARED, ISOLATED)
- Deterministic path normalization and traversal prevention
- All 10 workspace validation invariants:
  1. root_path is valid
  2. workspace belongs to the correct project
  3. workspace belongs to the correct WorkOrder
  4. workspace belongs to the correct execution
  5. writable paths are within allowed paths
  6. read-only paths are not writable
  7. forbidden paths are not writable
  8. forbidden paths cannot overlap the writable scope
  9. path definitions are normalized consistently
  10. malformed workspace context cannot become executable
- ProgrammerExecutionContext binding and cross-entity lineage
- Boundary query methods (is_path_allowed, is_path_writable, is_path_read_only, is_path_forbidden)
- Serialization / deserialization roundtrip fidelity
"""

import pytest

from core.enums import RiskLevel
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.identifiers import (
    WORKSPACE_ID_PREFIX,
    new_execution_id,
    new_work_order_id,
    new_workspace_id,
    validate_workspace_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import (
    ProgrammerWorkspace,
    Workspace,
    normalize_workspace_path,
)
from core.programmer.errors import (
    InvalidPathScopeError,
    InvalidProgrammerIdError,
    InvalidWorkspaceError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    ProgrammerExecutionStatus,
    WorkspaceIsolationMode,
)


@pytest.fixture
def sample_work_order() -> ProgrammerWorkOrder:
    return ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id="tsk-100",
        project_id="prj-alpha",
        correlation_id="corr-100",
        objective="Implement authentication session handling",
        allowed_paths=["src/auth", "tests/auth"],
        writable_paths=["src/auth/session.py", "tests/auth/test_session.py"],
        read_only_paths=["src/auth/config.py"],
        forbidden_paths=["config/secrets.env", ".git"],
    )


@pytest.fixture
def sample_execution(sample_work_order: ProgrammerWorkOrder) -> ProgrammerExecution:
    return ProgrammerExecution(
        execution_id=new_execution_id(),
        work_order_id=sample_work_order.work_order_id,
        task_id=sample_work_order.manager_task_id,
        project_id=sample_work_order.project_id,
        correlation_id=sample_work_order.correlation_id,
        status=ProgrammerExecutionStatus.STARTING,
    )


# ==============================================================================
# 1. Path Normalization Tests
# ==============================================================================


def test_path_normalization_clean() -> None:
    """Test consistent POSIX normalization across diverse path formats."""
    assert normalize_workspace_path("  src/auth/tokens.py  ") == "src/auth/tokens.py"
    assert normalize_workspace_path("src\\auth\\tokens.py") == "src/auth/tokens.py"
    assert normalize_workspace_path("src//auth///tokens.py") == "src/auth/tokens.py"
    assert normalize_workspace_path("src/./auth/session.py") == "src/auth/session.py"
    assert normalize_workspace_path("src/auth/../auth/tokens.py") == "src/auth/tokens.py"
    assert normalize_workspace_path("src/auth/") == "src/auth"
    assert normalize_workspace_path("./src/auth") == "src/auth"
    assert normalize_workspace_path(".") == "."
    assert normalize_workspace_path("") == "."


def test_path_traversal_rejection() -> None:
    """Test that path traversal escaping root is strictly rejected."""
    with pytest.raises(InvalidPathScopeError, match="Path traversal escape forbidden"):
        normalize_workspace_path("../outside.py")

    with pytest.raises(InvalidPathScopeError, match="Path traversal escape forbidden"):
        normalize_workspace_path("src/../../etc/passwd")

    with pytest.raises(InvalidPathScopeError, match="Path traversal escape forbidden"):
        normalize_workspace_path("..")


def test_null_byte_rejection() -> None:
    """Test that null bytes in paths are strictly rejected."""
    with pytest.raises(InvalidPathScopeError, match="Null bytes"):
        normalize_workspace_path("src/auth\0/tokens.py")


# ==============================================================================
# 2. Workspace Construction & Invariant Validation Tests
# ==============================================================================


def test_valid_workspace_construction() -> None:
    """Test standard ProgrammerWorkspace instantiation and Workspace alias."""
    ws = ProgrammerWorkspace(
        workspace_id=new_workspace_id(),
        project_id="prj-alpha",
        work_order_id=new_work_order_id(),
        root_path="/tmp/projects/alpha",
        allowed_paths=["src/auth", "tests/auth"],
        writable_paths=["src/auth/tokens.py"],
        read_only_paths=["src/auth/config.py"],
        forbidden_paths=["config/secrets.env"],
        isolation_mode=WorkspaceIsolationMode.SHARED,
    )

    assert ws.workspace_id.startswith(WORKSPACE_ID_PREFIX)
    assert ws.project_id == "prj-alpha"
    assert ws.root_path == "/tmp/projects/alpha"
    assert ws.isolation_mode == WorkspaceIsolationMode.SHARED
    assert ws.is_path_writable("src/auth/tokens.py") is True
    assert ws.is_path_writable("src/auth/config.py") is False
    assert ws.is_path_read_only("src/auth/config.py") is True
    assert ws.is_path_forbidden("config/secrets.env") is True
    assert ws.is_path_allowed("src/auth/tokens.py") is True
    assert ws.is_path_allowed("other/dir/file.py") is False

    # Check canonical alias
    assert Workspace is ProgrammerWorkspace


def test_workspace_from_work_order(sample_work_order: ProgrammerWorkOrder) -> None:
    """Test workspace factory method from_work_order."""
    ws = ProgrammerWorkspace.from_work_order(
        work_order=sample_work_order,
        root_path="/var/workspaces/proj1",
        isolation_mode=WorkspaceIsolationMode.ISOLATED,
    )

    assert ws.project_id == sample_work_order.project_id
    assert ws.work_order_id == sample_work_order.work_order_id
    assert ws.root_path == "/var/workspaces/proj1"
    assert ws.allowed_paths == sample_work_order.allowed_paths
    assert ws.writable_paths == sample_work_order.writable_paths
    assert ws.read_only_paths == sample_work_order.read_only_paths
    assert ws.forbidden_paths == sample_work_order.forbidden_paths
    assert ws.isolation_mode == WorkspaceIsolationMode.ISOLATED
    assert ws.trace["created_from_work_order"] == sample_work_order.work_order_id


def test_invalid_root_path() -> None:
    """Invariant 1: root_path must be valid non-empty string without null bytes or navigation tokens."""
    # Empty root
    with pytest.raises(InvalidWorkspaceError, match="Workspace root_path must be a valid"):
        ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id="prj-alpha",
            work_order_id=new_work_order_id(),
            root_path="",
        )

    # Whitespace root
    with pytest.raises(InvalidWorkspaceError, match="Workspace root_path must be a valid"):
        ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id="prj-alpha",
            work_order_id=new_work_order_id(),
            root_path="   ",
        )

    # Null byte root
    with pytest.raises(InvalidWorkspaceError, match="null bytes"):
        ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id="prj-alpha",
            work_order_id=new_work_order_id(),
            root_path="/tmp/root\0dir",
        )

    # Relative navigation token root
    with pytest.raises(InvalidWorkspaceError, match="relative navigation token"):
        ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id="prj-alpha",
            work_order_id=new_work_order_id(),
            root_path=".",
        )


def test_invalid_ownership_and_lineage() -> None:
    """Invariants 2, 3, 4: workspace must belong to correct project, work order, and execution."""
    # Invalid workspace ID
    with pytest.raises(InvalidProgrammerIdError):
        ProgrammerWorkspace(
            workspace_id="invalid-ws-1",
            project_id="prj-alpha",
            work_order_id=new_work_order_id(),
            root_path="/tmp/root",
        )

    # Missing project_id
    with pytest.raises(ProgrammerLineageError, match="non-empty project_id"):
        ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id="",
            work_order_id=new_work_order_id(),
            root_path="/tmp/root",
        )

    # Invalid work_order_id
    with pytest.raises(InvalidProgrammerIdError):
        ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id="prj-alpha",
            work_order_id="bad-wo-1",
            root_path="/tmp/root",
        )

    # Invalid execution_id format
    with pytest.raises(InvalidProgrammerIdError):
        ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id="prj-alpha",
            work_order_id=new_work_order_id(),
            execution_id="bad-exec-id",
            root_path="/tmp/root",
        )

    # validate_lineage method check
    ws = ProgrammerWorkspace(
        workspace_id=new_workspace_id(),
        project_id="prj-alpha",
        work_order_id="pwo-target-1",
        execution_id="pexec-exec-1",
        root_path="/tmp/root",
    )
    ws.validate_lineage(project_id="prj-alpha", work_order_id="pwo-target-1", execution_id="pexec-exec-1")

    with pytest.raises(ProgrammerLineageError, match="project mismatch"):
        ws.validate_lineage(project_id="prj-different")

    with pytest.raises(ProgrammerLineageError, match="work_order mismatch"):
        ws.validate_lineage(work_order_id="pwo-other")

    with pytest.raises(ProgrammerLineageError, match="execution mismatch"):
        ws.validate_lineage(execution_id="pexec-other")


def test_writable_paths_outside_allowed_paths() -> None:
    """Invariant 5: writable paths must be within allowed paths."""
    with pytest.raises(InvalidPathScopeError, match="exceeds permitted allowed_paths"):
        ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id="prj-alpha",
            work_order_id=new_work_order_id(),
            root_path="/tmp/root",
            allowed_paths=["src/auth"],
            writable_paths=["src/database/schema.py"],
        )


def test_read_only_and_writable_conflicts() -> None:
    """Invariant 6: read-only paths cannot be writable (exact or subpath overlap)."""
    # Exact overlap
    with pytest.raises(InvalidPathScopeError, match="overlaps read-only path"):
        ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id="prj-alpha",
            work_order_id=new_work_order_id(),
            root_path="/tmp/root",
            allowed_paths=["src/auth"],
            writable_paths=["src/auth/config.py"],
            read_only_paths=["src/auth/config.py"],
        )

    # Subpath overlap
    with pytest.raises(InvalidPathScopeError, match="overlaps read-only path"):
        ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id="prj-alpha",
            work_order_id=new_work_order_id(),
            root_path="/tmp/root",
            allowed_paths=["src"],
            writable_paths=["src/auth/config.py"],
            read_only_paths=["src/auth"],
        )


def test_forbidden_and_writable_conflicts() -> None:
    """Invariants 7 & 8: forbidden paths cannot be writable or overlap writable scope."""
    # Direct overlap
    with pytest.raises(InvalidPathScopeError, match="overlaps forbidden path"):
        ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id="prj-alpha",
            work_order_id=new_work_order_id(),
            root_path="/tmp/root",
            allowed_paths=["src"],
            writable_paths=["src/secret.key"],
            forbidden_paths=["src/secret.key"],
        )

    # Writable directory containing forbidden file
    with pytest.raises(InvalidPathScopeError, match="overlaps forbidden path"):
        ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id="prj-alpha",
            work_order_id=new_work_order_id(),
            root_path="/tmp/root",
            allowed_paths=["src"],
            writable_paths=["src"],
            forbidden_paths=["src/passwords.txt"],
        )


def test_isolation_mode_validation() -> None:
    """Test validation of isolation modes."""
    # Shared mode
    ws_shared = ProgrammerWorkspace(
        workspace_id=new_workspace_id(),
        project_id="prj-alpha",
        work_order_id=new_work_order_id(),
        root_path="/tmp/root",
        isolation_mode=WorkspaceIsolationMode.SHARED,
    )
    assert ws_shared.isolation_mode == WorkspaceIsolationMode.SHARED

    # Isolated mode
    ws_isolated = ProgrammerWorkspace(
        workspace_id=new_workspace_id(),
        project_id="prj-alpha",
        work_order_id=new_work_order_id(),
        root_path="/tmp/root",
        isolation_mode="isolated",  # test string coercion
    )
    assert ws_isolated.isolation_mode == WorkspaceIsolationMode.ISOLATED

    # Invalid mode
    with pytest.raises(InvalidWorkspaceError, match="Invalid workspace isolation mode"):
        ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            project_id="prj-alpha",
            work_order_id=new_work_order_id(),
            root_path="/tmp/root",
            isolation_mode="DOCKER_CONTAINER",
        )


def test_resolve_path_within_root() -> None:
    """Test absolute path resolution within workspace root."""
    ws = ProgrammerWorkspace(
        workspace_id=new_workspace_id(),
        project_id="prj-alpha",
        work_order_id=new_work_order_id(),
        root_path="/tmp/workspace/root",
    )

    resolved = ws.resolve_path("src/auth/session.py")
    assert resolved == "/tmp/workspace/root/src/auth/session.py"

    resolved_root = ws.resolve_path(".")
    assert resolved_root == "/tmp/workspace/root"


def test_serialization_and_deserialization_fidelity() -> None:
    """Test to_dict, to_json, and from_dict roundtrip."""
    ws = ProgrammerWorkspace(
        workspace_id=new_workspace_id(),
        project_id="prj-alpha",
        work_order_id=new_work_order_id(),
        execution_id=new_execution_id(),
        root_path="/tmp/root/app",
        allowed_paths=["src", "tests"],
        writable_paths=["src/app.py"],
        read_only_paths=["src/constants.py"],
        forbidden_paths=[".git"],
        isolation_mode=WorkspaceIsolationMode.ISOLATED,
        trace={"key": "val"},
        metadata={"author": "manager"},
    )

    d = ws.to_dict()
    assert d["workspace_id"] == ws.workspace_id
    assert d["isolation_mode"] == "ISOLATED"
    assert d["writable_paths"] == ["src/app.py"]

    json_str = ws.to_json()
    assert ws.workspace_id in json_str

    restored = ProgrammerWorkspace.from_dict(d)
    assert restored.workspace_id == ws.workspace_id
    assert restored.isolation_mode == WorkspaceIsolationMode.ISOLATED
    assert restored.root_path == ws.root_path
    assert restored.allowed_paths == ws.allowed_paths
    assert restored.writable_paths == ws.writable_paths
    assert restored.trace == ws.trace
    assert restored.metadata == ws.metadata


# ==============================================================================
# 3. ProgrammerExecutionContext Binding Tests
# ==============================================================================


def test_execution_context_binding_and_validation(
    sample_work_order: ProgrammerWorkOrder,
    sample_execution: ProgrammerExecution,
) -> None:
    """
    Test ProgrammerExecutionContext creation, boundary delegation,
    and strict lineage enforcement.
    """
    ws = ProgrammerWorkspace.from_work_order(
        work_order=sample_work_order,
        root_path="/tmp/repo",
        execution_id=sample_execution.execution_id,
    )

    ctx = ProgrammerExecutionContext.create(
        workspace=ws,
        execution=sample_execution,
        work_order=sample_work_order,
    )

    # Invariants and lineage
    assert ctx.execution_id == sample_execution.execution_id
    assert ctx.work_order_id == sample_work_order.work_order_id
    assert ctx.workspace_id == ws.workspace_id
    assert ctx.project_id == sample_work_order.project_id
    assert ctx.correlation_id == sample_work_order.correlation_id

    # Boundary queries delegate to workspace
    assert ctx.is_path_writable("src/auth/session.py") is True
    assert ctx.is_path_writable("src/auth/config.py") is False
    assert ctx.is_path_read_only("src/auth/config.py") is True
    assert ctx.is_path_forbidden("config/secrets.env") is True
    assert ctx.is_path_allowed("src/auth/session.py") is True

    # Serialization
    d = ctx.to_dict()
    assert d["execution_id"] == sample_execution.execution_id
    assert d["workspace"]["workspace_id"] == ws.workspace_id

    ctx_restored = ProgrammerExecutionContext.from_dict(d)
    assert ctx_restored.execution_id == ctx.execution_id
    assert ctx_restored.workspace.workspace_id == ws.workspace_id


def test_execution_context_mismatch_rejection(
    sample_work_order: ProgrammerWorkOrder,
    sample_execution: ProgrammerExecution,
) -> None:
    """
    Invariant 10: malformed workspace context cannot become executable.
    Cross-entity lineage mismatches must be caught immediately.
    """
    ws = ProgrammerWorkspace.from_work_order(
        work_order=sample_work_order,
        root_path="/tmp/repo",
        execution_id=sample_execution.execution_id,
    )

    # 1. Project ID mismatch
    with pytest.raises(ProgrammerLineageError, match="Project mismatch"):
        ProgrammerExecutionContext(
            execution_id=sample_execution.execution_id,
            work_order_id=sample_work_order.work_order_id,
            workspace_id=ws.workspace_id,
            project_id="prj-foreign",
            correlation_id="corr-1",
            workspace=ws,
        )

    # 2. WorkOrder ID mismatch
    with pytest.raises(ProgrammerLineageError, match="Work order mismatch"):
        ProgrammerExecutionContext(
            execution_id=sample_execution.execution_id,
            work_order_id="pwo-different-999",
            workspace_id=ws.workspace_id,
            project_id=sample_work_order.project_id,
            correlation_id="corr-1",
            workspace=ws,
        )

    # 3. Execution ID mismatch
    with pytest.raises(ProgrammerLineageError, match="Execution mismatch"):
        ProgrammerExecutionContext(
            execution_id="pexec-different-888",
            work_order_id=sample_work_order.work_order_id,
            workspace_id=ws.workspace_id,
            project_id=sample_work_order.project_id,
            correlation_id="corr-1",
            workspace=ws,
        )

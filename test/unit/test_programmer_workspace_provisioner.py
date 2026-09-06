"""
Unit Tests for Programmer V1 Phase 2.2: Workspace Provisioning.

Tests the deterministic WorkspaceProvisioner and WorkspaceProvisioningResult contracts:
- Successful provisioning in SHARED mode (logical path boundary capability)
- Successful provisioning in ISOLATED mode (local staging directory capability)
- Clean up of provisioned staging workspaces
- Repeated provisioning idempotency
- Deterministic failure when work order is invalid (INVALID_WORKSPACE)
- Deterministic failure when project is unavailable (PROJECT_NOT_FOUND)
- Deterministic failure on invalid root path (PATH_INVALID / WORKSPACE_UNAVAILABLE)
- Deterministic failure when strict isolation requested (ISOLATION_UNAVAILABLE)
- Invariant verification that a failed provision NEVER produces an executable READY context
- Cross-entity lineage preservation across WorkOrder -> Workspace -> ExecutionContext
- Integration with existing AutonomOS ProjectRegistry and Store
- Serialization and deserialization fidelity
"""

import os
from pathlib import Path
import pytest
import tempfile

from core.models import Project, utc_now
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
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
from core.programmer.errors import WorkspaceProvisioningError
from core.programmer.types import (
    ProgrammerExecutionStatus,
    WorkspaceIsolationMode,
    WorkspaceProvisioningErrorCode,
    WorkspaceProvisioningStatus,
)
from core.runtime.project_registry import ProjectRegistry
from core.storage.memory_store import MemoryStore


@pytest.fixture
def temp_project_dir(tmp_path: Path) -> Path:
    proj_dir = tmp_path / "test_project"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "src").mkdir(parents=True, exist_ok=True)
    (proj_dir / "src" / "auth").mkdir(parents=True, exist_ok=True)
    (proj_dir / "tests").mkdir(parents=True, exist_ok=True)
    return proj_dir


@pytest.fixture
def sample_work_order() -> ProgrammerWorkOrder:
    return ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id="tsk-100",
        project_id="prj-alpha",
        correlation_id="corr-100",
        objective="Implement authentication session handling",
        allowed_paths=["src/auth", "tests"],
        writable_paths=["src/auth/session.py", "tests/test_session.py"],
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
# 1. Successful Provisioning in SHARED Mode
# ==============================================================================


def test_successful_provisioning_shared_mode(
    temp_project_dir: Path,
    sample_work_order: ProgrammerWorkOrder,
    sample_execution: ProgrammerExecution,
) -> None:
    """Test standard provisioning in SHARED mode with verified lineage and logical boundary."""
    provisioner = WorkspaceProvisioner(
        project_resolver={sample_work_order.project_id: str(temp_project_dir)}
    )

    result = provisioner.provision(
        work_order=sample_work_order,
        execution=sample_execution,
        isolation_mode=WorkspaceIsolationMode.SHARED,
    )

    assert result.status == WorkspaceProvisioningStatus.READY
    assert result.is_ready() is True
    assert result.error_code is None
    assert result.error_message is None
    assert result.isolation_capability == "LOGICAL_PATH_BOUNDARY"

    # Verify workspace properties
    assert result.workspace is not None
    assert result.workspace.root_path == str(temp_project_dir)
    assert result.workspace.isolation_mode == WorkspaceIsolationMode.SHARED
    assert result.workspace.work_order_id == sample_work_order.work_order_id
    assert result.workspace.project_id == sample_work_order.project_id
    assert result.workspace.execution_id == sample_execution.execution_id

    # Verify execution context binding
    assert result.execution_context is not None
    assert result.execution_context.execution_id == sample_execution.execution_id
    assert result.execution_context.workspace_id == result.workspace.workspace_id
    assert result.execution_context.work_order_id == sample_work_order.work_order_id

    # Verify provisioner registry lookup
    assert provisioner.is_provisioned(result.workspace.workspace_id) is True
    assert provisioner.get_workspace(result.workspace.workspace_id) is result.workspace
    assert provisioner.get_result(result.workspace.workspace_id) is result


# ==============================================================================
# 2. Successful Provisioning in ISOLATED Mode & Cleanup
# ==============================================================================


def test_successful_provisioning_isolated_mode(
    temp_project_dir: Path,
    sample_work_order: ProgrammerWorkOrder,
    sample_execution: ProgrammerExecution,
    tmp_path: Path,
) -> None:
    """Test provisioning in ISOLATED mode creates a dedicated local staging directory."""
    staging_base = tmp_path / "staging"
    provisioner = WorkspaceProvisioner(
        project_resolver={sample_work_order.project_id: str(temp_project_dir)},
        staging_base_dir=str(staging_base),
        strict_isolation=False,
    )

    result = provisioner.provision(
        work_order=sample_work_order,
        execution=sample_execution,
        isolation_mode=WorkspaceIsolationMode.ISOLATED,
    )

    assert result.status == WorkspaceProvisioningStatus.READY
    assert result.is_ready() is True
    assert result.isolation_capability == "LOCAL_STAGING_DIRECTORY"

    assert result.workspace is not None
    # Staging directory is distinct from the project directory
    assert result.workspace.root_path != str(temp_project_dir)
    assert os.path.exists(result.workspace.root_path)
    assert os.path.isdir(result.workspace.root_path)
    assert result.workspace.isolation_mode == WorkspaceIsolationMode.ISOLATED

    staging_path = result.workspace.root_path
    ws_id = result.workspace.workspace_id

    # Test clean_workspace
    cleaned = provisioner.clean_workspace(ws_id)
    assert cleaned is True
    assert not os.path.exists(staging_path)
    assert provisioner.is_provisioned(ws_id) is False
    assert provisioner.get_workspace(ws_id) is None


# ==============================================================================
# 3. Repeated Provisioning Idempotency
# ==============================================================================


def test_repeated_provisioning_idempotency(
    temp_project_dir: Path,
    sample_work_order: ProgrammerWorkOrder,
    sample_execution: ProgrammerExecution,
) -> None:
    """Repeated calls with identical work order and execution return identical ready result."""
    provisioner = WorkspaceProvisioner(
        project_resolver={sample_work_order.project_id: str(temp_project_dir)}
    )

    res1 = provisioner.provision(sample_work_order, sample_execution)
    res2 = provisioner.provision(sample_work_order, sample_execution)

    assert res1.is_ready() is True
    assert res2.is_ready() is True
    assert res1 is res2
    assert res1.workspace.workspace_id == res2.workspace.workspace_id


# ==============================================================================
# 4. Invalid Work Order Fails Provisioning
# ==============================================================================


def test_invalid_work_order_fails_provisioning(
    temp_project_dir: Path,
    sample_execution: ProgrammerExecution,
) -> None:
    """A malformed or inconsistent work order fails deterministically."""
    provisioner = WorkspaceProvisioner(
        project_resolver={"prj-alpha": str(temp_project_dir)}
    )

    # Pass non-work order
    bad_result = provisioner.provision(
        work_order="not-a-work-order",  # type: ignore
        execution=sample_execution,
    )
    assert bad_result.status == WorkspaceProvisioningStatus.FAILED
    assert bad_result.error_code == WorkspaceProvisioningErrorCode.INVALID_WORKSPACE
    assert bad_result.workspace is None
    assert bad_result.execution_context is None
    assert bad_result.is_ready() is False


# ==============================================================================
# 5. Unavailable Project Fails Provisioning
# ==============================================================================


def test_unavailable_project_fails_provisioning(
    sample_work_order: ProgrammerWorkOrder,
    sample_execution: ProgrammerExecution,
) -> None:
    """When the project cannot be resolved, fail deterministically with PROJECT_NOT_FOUND."""
    # Resolver has no entry for sample_work_order.project_id
    provisioner = WorkspaceProvisioner(project_resolver={})

    result = provisioner.provision(sample_work_order, sample_execution)

    assert result.status == WorkspaceProvisioningStatus.FAILED
    assert result.error_code == WorkspaceProvisioningErrorCode.PROJECT_NOT_FOUND
    assert result.workspace is None
    assert result.execution_context is None
    assert result.is_ready() is False
    assert "not found" in (result.error_message or "").lower()


# ==============================================================================
# 6. Invalid Path Fails Provisioning
# ==============================================================================


def test_invalid_path_fails_provisioning(
    sample_work_order: ProgrammerWorkOrder,
    sample_execution: ProgrammerExecution,
    tmp_path: Path,
) -> None:
    """Non-existent or malformed root path fails deterministically."""
    provisioner = WorkspaceProvisioner()

    # Case A: Non-existent directory override
    non_existent = str(tmp_path / "does_not_exist_xyz")
    res1 = provisioner.provision(
        sample_work_order,
        sample_execution,
        root_path_override=non_existent,
    )
    assert res1.status == WorkspaceProvisioningStatus.FAILED
    assert res1.error_code == WorkspaceProvisioningErrorCode.WORKSPACE_UNAVAILABLE
    assert res1.workspace is None

    # Case B: Traversal navigation token override
    res2 = provisioner.provision(
        sample_work_order,
        sample_execution,
        root_path_override="..",
    )
    assert res2.status == WorkspaceProvisioningStatus.FAILED
    assert res2.error_code == WorkspaceProvisioningErrorCode.PATH_INVALID
    assert res2.workspace is None

    # Case C: Path pointing to regular file instead of directory
    file_path = tmp_path / "somefile.txt"
    file_path.write_text("content")
    res3 = provisioner.provision(
        sample_work_order,
        sample_execution,
        root_path_override=str(file_path),
    )
    assert res3.status == WorkspaceProvisioningStatus.FAILED
    assert res3.error_code == WorkspaceProvisioningErrorCode.PATH_INVALID
    assert res3.workspace is None


# ==============================================================================
# 7. Strict Isolation Fails When Unavailable
# ==============================================================================


def test_strict_isolation_fails_when_unavailable(
    temp_project_dir: Path,
    sample_work_order: ProgrammerWorkOrder,
    sample_execution: ProgrammerExecution,
) -> None:
    """When strict container isolation is required but unavailable, fail deterministically."""
    provisioner = WorkspaceProvisioner(
        project_resolver={sample_work_order.project_id: str(temp_project_dir)},
        strict_isolation=True,
    )

    result = provisioner.provision(
        work_order=sample_work_order,
        execution=sample_execution,
        isolation_mode=WorkspaceIsolationMode.ISOLATED,
    )

    assert result.status == WorkspaceProvisioningStatus.FAILED
    assert result.error_code == WorkspaceProvisioningErrorCode.ISOLATION_UNAVAILABLE
    assert result.isolation_capability == "UNAVAILABLE"
    assert result.workspace is None
    assert result.execution_context is None
    assert result.is_ready() is False
    assert "Strict container/OS isolation is requested but unavailable" in (result.error_message or "")


# ==============================================================================
# 8. Invariant: No False READY State After Failure
# ==============================================================================


def test_no_false_ready_state_after_failure(
    temp_project_dir: Path,
    sample_work_order: ProgrammerWorkOrder,
) -> None:
    """Directly asserts the invariant: a failed provision cannot hold an active workspace or context."""
    ws = ProgrammerWorkspace.from_work_order(
        work_order=sample_work_order,
        root_path=str(temp_project_dir),
    )

    # Invariant 1: FAILED cannot hold workspace
    with pytest.raises(WorkspaceProvisioningError) as exc1:
        WorkspaceProvisioningResult(
            status=WorkspaceProvisioningStatus.FAILED,
            workspace=ws,
            error_code=WorkspaceProvisioningErrorCode.INVALID_WORKSPACE,
        )
    assert "cannot contain an active workspace" in str(exc1.value)

    # Invariant 2: READY cannot have None workspace
    with pytest.raises(WorkspaceProvisioningError) as exc2:
        WorkspaceProvisioningResult(
            status=WorkspaceProvisioningStatus.READY,
            workspace=None,
        )
    assert "must contain an active workspace" in str(exc2.value)


# ==============================================================================
# 9. Lineage Preservation Across Provisioning
# ==============================================================================


def test_lineage_preservation_across_provisioning(
    temp_project_dir: Path,
    sample_work_order: ProgrammerWorkOrder,
) -> None:
    """Execution with mismatched work_order_id or project_id is rejected."""
    provisioner = WorkspaceProvisioner(
        project_resolver={sample_work_order.project_id: str(temp_project_dir)}
    )

    # Execution with mismatched work_order_id
    mismatched_exec = ProgrammerExecution(
        execution_id=new_execution_id(),
        work_order_id="pwo-other-999",
        task_id=sample_work_order.manager_task_id,
        project_id=sample_work_order.project_id,
        correlation_id=sample_work_order.correlation_id,
    )

    result = provisioner.provision(sample_work_order, mismatched_exec)
    assert result.status == WorkspaceProvisioningStatus.FAILED
    assert result.error_code == WorkspaceProvisioningErrorCode.INVALID_WORKSPACE
    assert "mismatch" in (result.error_message or "").lower()
    assert result.workspace is None


def test_auto_creation_of_execution_preserves_lineage(
    temp_project_dir: Path,
    sample_work_order: ProgrammerWorkOrder,
) -> None:
    """When execution is omitted, an execution session is automatically created preserving lineage."""
    provisioner = WorkspaceProvisioner(
        project_resolver={sample_work_order.project_id: str(temp_project_dir)}
    )

    result = provisioner.provision(sample_work_order, execution=None)

    assert result.is_ready() is True
    assert result.execution_context is not None
    assert result.execution_context.work_order_id == sample_work_order.work_order_id
    assert result.execution_context.project_id == sample_work_order.project_id
    assert result.workspace.execution_id == result.execution_context.execution_id


# ==============================================================================
# 10. Integration with AutonomOS ProjectRegistry & Store
# ==============================================================================


def test_integration_with_project_registry_and_store(
    tmp_path: Path,
    sample_work_order: ProgrammerWorkOrder,
) -> None:
    """Verify seamless resolution when using ProjectRegistry and MemoryStore."""
    store = MemoryStore()
    registry = ProjectRegistry(store)

    project_root = tmp_path / "registry_project"
    project_root.mkdir(parents=True, exist_ok=True)

    project = registry.create_project(
        name="Test Registry Project",
        root_path=str(project_root),
        project_id=sample_work_order.project_id,
    )

    provisioner = WorkspaceProvisioner(project_resolver=registry)
    result = provisioner.provision(sample_work_order)

    assert result.is_ready() is True
    assert result.workspace.root_path == str(project_root.resolve())
    assert result.workspace.project_id == project.id


# ==============================================================================
# 11. Serialization Roundtrip Fidelity
# ==============================================================================


def test_provisioning_result_serialization_roundtrip(
    temp_project_dir: Path,
    sample_work_order: ProgrammerWorkOrder,
    sample_execution: ProgrammerExecution,
) -> None:
    """Verify JSON/dict serialization roundtrip fidelity for both READY and FAILED results."""
    provisioner = WorkspaceProvisioner(
        project_resolver={sample_work_order.project_id: str(temp_project_dir)}
    )

    # READY result serialization
    ready_result = provisioner.provision(sample_work_order, sample_execution)
    data = ready_result.to_dict()
    json_str = ready_result.to_json()

    restored = WorkspaceProvisioningResult.from_dict(data)
    assert restored.is_ready() is True
    assert restored.workspace.workspace_id == ready_result.workspace.workspace_id
    assert restored.execution_context.execution_id == ready_result.execution_context.execution_id
    assert restored.isolation_capability == "LOGICAL_PATH_BOUNDARY"

    # FAILED result serialization
    failed_result = WorkspaceProvisioningResult(
        status=WorkspaceProvisioningStatus.FAILED,
        workspace=None,
        execution_context=None,
        error_code=WorkspaceProvisioningErrorCode.PROJECT_NOT_FOUND,
        error_message="Project missing",
        isolation_capability="UNAVAILABLE",
    )
    failed_data = failed_result.to_dict()
    restored_failed = WorkspaceProvisioningResult.from_dict(failed_data)
    assert restored_failed.status == WorkspaceProvisioningStatus.FAILED
    assert restored_failed.is_ready() is False
    assert restored_failed.workspace is None
    assert restored_failed.error_code == WorkspaceProvisioningErrorCode.PROJECT_NOT_FOUND

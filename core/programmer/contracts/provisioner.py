from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable, Optional, Union

from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.identifiers import new_execution_id
from core.programmer.contracts.workspace import ProgrammerWorkspace, normalize_workspace_path
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    InvalidPathScopeError,
    InvalidWorkspaceError,
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
    WorkspaceProvisioningError,
)
from core.programmer.types import (
    WorkspaceIsolationMode,
    WorkspaceProvisioningErrorCode,
    WorkspaceProvisioningStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkspaceProvisioningResult:
    """
    Structured outcome of a workspace provisioning attempt.
    
    Guarantees:
    - If status == READY, workspace and execution_context are valid and active.
    - If status == FAILED, workspace and execution_context are strictly None.
    - An executable READY context can never be produced from a failed attempt.
    """
    status: WorkspaceProvisioningStatus
    workspace: Optional[ProgrammerWorkspace] = None
    execution_context: Optional[ProgrammerExecutionContext] = None
    error_code: Optional[WorkspaceProvisioningErrorCode] = None
    error_message: Optional[str] = None
    isolation_capability: str = "LOGICAL_PATH_BOUNDARY"
    provisioned_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = WorkspaceProvisioningStatus(self.status.upper())
            except (ValueError, TypeError):
                self.status = WorkspaceProvisioningStatus.FAILED

        if isinstance(self.error_code, str):
            try:
                self.error_code = WorkspaceProvisioningErrorCode(self.error_code.upper())
            except (ValueError, TypeError):
                self.error_code = WorkspaceProvisioningErrorCode.UNKNOWN

        # Invariant enforcement: a failed provision cannot hold active workspace/context
        if self.status == WorkspaceProvisioningStatus.FAILED:
            if self.workspace is not None or self.execution_context is not None:
                raise WorkspaceProvisioningError(
                    "A failed provisioning result cannot contain an active workspace or execution_context.",
                    error_code=WorkspaceProvisioningErrorCode.INVALID_WORKSPACE,
                )

        # Invariant enforcement: a READY provision must have both workspace and execution_context
        if self.status == WorkspaceProvisioningStatus.READY:
            if self.workspace is None or self.execution_context is None:
                raise WorkspaceProvisioningError(
                    "A READY provisioning result must contain an active workspace and execution_context.",
                    error_code=WorkspaceProvisioningErrorCode.INVALID_WORKSPACE,
                )

    def is_ready(self) -> bool:
        """Check whether the workspace provisioning succeeded and is executable."""
        return (
            self.status == WorkspaceProvisioningStatus.READY
            and self.workspace is not None
            and self.execution_context is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value if isinstance(self.status, WorkspaceProvisioningStatus) else str(self.status),
            "workspace": self.workspace.to_dict() if self.workspace else None,
            "execution_context": self.execution_context.to_dict() if self.execution_context else None,
            "error_code": (
                self.error_code.value
                if isinstance(self.error_code, WorkspaceProvisioningErrorCode)
                else (str(self.error_code) if self.error_code else None)
            ),
            "error_message": self.error_message,
            "isolation_capability": self.isolation_capability,
            "provisioned_at": self.provisioned_at,
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceProvisioningResult:
        st_raw = data.get("status", WorkspaceProvisioningStatus.FAILED.value)
        try:
            status = WorkspaceProvisioningStatus(str(st_raw).upper())
        except (ValueError, TypeError):
            status = WorkspaceProvisioningStatus.FAILED

        err_code_raw = data.get("error_code")
        error_code = None
        if err_code_raw:
            try:
                error_code = WorkspaceProvisioningErrorCode(str(err_code_raw).upper())
            except (ValueError, TypeError):
                error_code = WorkspaceProvisioningErrorCode.UNKNOWN

        workspace = None
        if data.get("workspace"):
            workspace = ProgrammerWorkspace.from_dict(data["workspace"])

        execution_context = None
        if data.get("execution_context"):
            execution_context = ProgrammerExecutionContext.from_dict(
                data["execution_context"],
                workspace=workspace,
            )

        return cls(
            status=status,
            workspace=workspace,
            execution_context=execution_context,
            error_code=error_code,
            error_message=data.get("error_message"),
            isolation_capability=data.get("isolation_capability", "LOGICAL_PATH_BOUNDARY"),
            provisioned_at=data.get("provisioned_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )


class WorkspaceProvisioner:
    """
    Deterministic workspace provisioner for the Programmer subsystem.
    
    Core Architectural Principle:
    - Programmer owns execution authority.
    - WorkspaceProvisioner owns workspace preparation.
    - Cline provides coding capabilities later, but does NOT own workspace authority.
    
    Responsibilities:
    - Receive a validated ProgrammerWorkOrder and optional ProgrammerExecution.
    - Resolve the project workspace root path via ProjectRegistry, Store, resolver callable, or directory structure.
    - Prepare the execution workspace based on isolation_mode:
      * SHARED: Uses the project workspace with strictly declared logical path boundaries;
                isolation_capability is explicitly recorded as "LOGICAL_PATH_BOUNDARY".
      * ISOLATED: Prepares a dedicated local staging directory (e.g., .autonomos/isolated_workspaces/<execution_id>),
                  explicitly tagged as isolation_capability="LOCAL_STAGING_DIRECTORY".
                  Never misrepresents logical scoping or directory staging as OS-level sandbox isolation.
                  If strict_isolation is requested and unsupported/unavailable, fails deterministically with ISOLATION_UNAVAILABLE.
    - Build and validate the ProgrammerWorkspace ensuring all 10 invariants hold.
    - Bind ProgrammerExecutionContext preserving full causal lineage:
      WorkOrder -> Workspace -> ExecutionContext.
    - Track provisioned workspaces and ensure idempotency.
    - Guarantee deterministic failure states: on any failure, status is FAILED, workspace is None,
      and execution_context is None (no half-ready or falsely executable states).
    """

    def __init__(
        self,
        project_resolver: Optional[Any] = None,
        default_projects_dir: Optional[str] = None,
        staging_base_dir: Optional[str] = None,
        strict_isolation: bool = False,
    ):
        self.project_resolver = project_resolver
        self.default_projects_dir = default_projects_dir
        self.staging_base_dir = staging_base_dir
        self.strict_isolation = strict_isolation

        # Workspace tracking
        self._workspaces_by_id: dict[str, ProgrammerWorkspace] = {}
        self._results_by_key: dict[tuple[str, str], WorkspaceProvisioningResult] = {}
        self._results_by_ws_id: dict[str, WorkspaceProvisioningResult] = {}
        self._staging_dirs_by_ws_id: dict[str, str] = {}

    def _resolve_project_root(
        self,
        project_id: str,
        root_path_override: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[WorkspaceProvisioningErrorCode], Optional[str]]:
        """
        Deterministically resolve the project root directory.
        Returns: (resolved_root_path, error_code, error_message)
        """
        # 1. Direct path override
        if root_path_override is not None:
            if not isinstance(root_path_override, str) or not root_path_override.strip() or "\0" in root_path_override:
                return (
                    None,
                    WorkspaceProvisioningErrorCode.PATH_INVALID,
                    "Invalid root_path_override: path is empty or contains invalid characters.",
                )
            clean_override = root_path_override.strip()
            if clean_override in {".", ".."}:
                return (
                    None,
                    WorkspaceProvisioningErrorCode.PATH_INVALID,
                    f"Invalid root_path_override: relative token '{clean_override}' not allowed.",
                )
            abs_path = os.path.abspath(clean_override)
            if not os.path.exists(abs_path):
                return (
                    None,
                    WorkspaceProvisioningErrorCode.WORKSPACE_UNAVAILABLE,
                    f"Project root directory does not exist: '{abs_path}'",
                )
            if not os.path.isdir(abs_path):
                return (
                    None,
                    WorkspaceProvisioningErrorCode.PATH_INVALID,
                    f"Project root path is not a directory: '{abs_path}'",
                )
            if not os.access(abs_path, os.R_OK):
                return (
                    None,
                    WorkspaceProvisioningErrorCode.PERMISSION_DENIED,
                    f"Project root path is not readable: '{abs_path}'",
                )
            return abs_path, None, None

        # 2. Project resolver (ProjectRegistry, Store, dict, or Callable)
        if self.project_resolver is not None:
            project = None
            try:
                if hasattr(self.project_resolver, "get_project") and callable(self.project_resolver.get_project):
                    project = self.project_resolver.get_project(project_id)
                elif callable(self.project_resolver):
                    project = self.project_resolver(project_id)
                elif isinstance(self.project_resolver, dict):
                    project = self.project_resolver.get(project_id)
            except Exception as e:
                return (
                    None,
                    WorkspaceProvisioningErrorCode.PROJECT_NOT_FOUND,
                    f"Failed resolving project '{project_id}': {e}",
                )

            if project is None:
                return (
                    None,
                    WorkspaceProvisioningErrorCode.PROJECT_NOT_FOUND,
                    f"Project '{project_id}' not found.",
                )

            raw_root = None
            if hasattr(project, "root_path"):
                raw_root = project.root_path
            elif isinstance(project, dict) and "root_path" in project:
                raw_root = project["root_path"]
            elif isinstance(project, str):
                raw_root = project

            if not raw_root or not isinstance(raw_root, str):
                return (
                    None,
                    WorkspaceProvisioningErrorCode.WORKSPACE_UNAVAILABLE,
                    f"Project '{project_id}' has no valid root_path configured.",
                )

            abs_path = os.path.abspath(raw_root.strip())
            if not os.path.exists(abs_path):
                return (
                    None,
                    WorkspaceProvisioningErrorCode.WORKSPACE_UNAVAILABLE,
                    f"Project root directory does not exist: '{abs_path}'",
                )
            if not os.path.isdir(abs_path):
                return (
                    None,
                    WorkspaceProvisioningErrorCode.PATH_INVALID,
                    f"Project root path is not a directory: '{abs_path}'",
                )
            if not os.access(abs_path, os.R_OK):
                return (
                    None,
                    WorkspaceProvisioningErrorCode.PERMISSION_DENIED,
                    f"Project root path is not readable: '{abs_path}'",
                )
            return abs_path, None, None

        # 3. Default projects directory lookup
        if self.default_projects_dir is not None:
            candidate = os.path.abspath(os.path.join(self.default_projects_dir, project_id))
            if os.path.exists(candidate):
                if not os.path.isdir(candidate):
                    return (
                        None,
                        WorkspaceProvisioningErrorCode.PATH_INVALID,
                        f"Candidate project path is not a directory: '{candidate}'",
                    )
                if not os.access(candidate, os.R_OK):
                    return (
                        None,
                        WorkspaceProvisioningErrorCode.PERMISSION_DENIED,
                        f"Candidate project path is not readable: '{candidate}'",
                    )
                return candidate, None, None
            return (
                None,
                WorkspaceProvisioningErrorCode.PROJECT_NOT_FOUND,
                f"Project '{project_id}' directory not found in '{self.default_projects_dir}'.",
            )

        return (
            None,
            WorkspaceProvisioningErrorCode.PROJECT_NOT_FOUND,
            f"No resolution mechanism available for project '{project_id}'.",
        )

    def provision(
        self,
        work_order: ProgrammerWorkOrder,
        execution: Optional[ProgrammerExecution] = None,
        isolation_mode: Optional[Union[WorkspaceIsolationMode, str]] = None,
        root_path_override: Optional[str] = None,
        strict_isolation: Optional[bool] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> WorkspaceProvisioningResult:
        """
        Provision the workspace and operational execution context for a Programmer execution.
        """
        # Step 1: Validate work order
        if not isinstance(work_order, ProgrammerWorkOrder):
            return WorkspaceProvisioningResult(
                status=WorkspaceProvisioningStatus.FAILED,
                workspace=None,
                execution_context=None,
                error_code=WorkspaceProvisioningErrorCode.INVALID_WORKSPACE,
                error_message=f"Expected ProgrammerWorkOrder instance, got {type(work_order).__name__}.",
            )

        try:
            work_order.validate()
        except (ProgrammerValidationError, ProgrammerError) as err:
            return WorkspaceProvisioningResult(
                status=WorkspaceProvisioningStatus.FAILED,
                workspace=None,
                execution_context=None,
                error_code=WorkspaceProvisioningErrorCode.INVALID_WORKSPACE,
                error_message=f"Work order failed validation: {err}",
            )

        # Step 2: Validate or construct execution
        if execution is not None:
            if not isinstance(execution, ProgrammerExecution):
                return WorkspaceProvisioningResult(
                    status=WorkspaceProvisioningStatus.FAILED,
                    workspace=None,
                    execution_context=None,
                    error_code=WorkspaceProvisioningErrorCode.INVALID_WORKSPACE,
                    error_message=f"Expected ProgrammerExecution instance, got {type(execution).__name__}.",
                )
            if execution.work_order_id != work_order.work_order_id:
                return WorkspaceProvisioningResult(
                    status=WorkspaceProvisioningStatus.FAILED,
                    workspace=None,
                    execution_context=None,
                    error_code=WorkspaceProvisioningErrorCode.INVALID_WORKSPACE,
                    error_message=(
                        f"Execution work_order_id mismatch: execution belongs to '{execution.work_order_id}', "
                        f"work order is '{work_order.work_order_id}'."
                    ),
                )
            if execution.project_id != work_order.project_id:
                return WorkspaceProvisioningResult(
                    status=WorkspaceProvisioningStatus.FAILED,
                    workspace=None,
                    execution_context=None,
                    error_code=WorkspaceProvisioningErrorCode.INVALID_WORKSPACE,
                    error_message=(
                        f"Execution project_id mismatch: execution belongs to '{execution.project_id}', "
                        f"work order is '{work_order.project_id}'."
                    ),
                )
            if execution.task_id != work_order.manager_task_id:
                return WorkspaceProvisioningResult(
                    status=WorkspaceProvisioningStatus.FAILED,
                    workspace=None,
                    execution_context=None,
                    error_code=WorkspaceProvisioningErrorCode.INVALID_WORKSPACE,
                    error_message=(
                        f"Execution task_id mismatch: execution belongs to task '{execution.task_id}', "
                        f"work order belongs to task '{work_order.manager_task_id}'."
                    ),
                )
        else:
            execution = ProgrammerExecution(
                execution_id=new_execution_id(),
                work_order_id=work_order.work_order_id,
                task_id=work_order.manager_task_id,
                project_id=work_order.project_id,
                correlation_id=work_order.correlation_id,
            )

        # Step 3: Idempotency check
        idempotency_key = (work_order.work_order_id, execution.execution_id)
        if idempotency_key in self._results_by_key:
            existing = self._results_by_key[idempotency_key]
            if existing.is_ready():
                return existing

        # Step 4: Resolve isolation mode
        iso = isolation_mode or getattr(work_order, "isolation_mode", None) or WorkspaceIsolationMode.SHARED
        if isinstance(iso, str):
            try:
                iso = WorkspaceIsolationMode(iso.upper())
            except (ValueError, TypeError):
                return WorkspaceProvisioningResult(
                    status=WorkspaceProvisioningStatus.FAILED,
                    workspace=None,
                    execution_context=None,
                    error_code=WorkspaceProvisioningErrorCode.INVALID_WORKSPACE,
                    error_message=f"Invalid workspace isolation mode '{iso}'.",
                )

        # Step 5: Strict isolation check
        is_strict = self.strict_isolation if strict_isolation is None else strict_isolation
        if iso == WorkspaceIsolationMode.ISOLATED and is_strict:
            return WorkspaceProvisioningResult(
                status=WorkspaceProvisioningStatus.FAILED,
                workspace=None,
                execution_context=None,
                error_code=WorkspaceProvisioningErrorCode.ISOLATION_UNAVAILABLE,
                error_message="Strict container/OS isolation is requested but unavailable in this runtime environment.",
                isolation_capability="UNAVAILABLE",
            )

        # Step 6: Project root resolution
        resolved_root, err_code, err_msg = self._resolve_project_root(
            project_id=work_order.project_id,
            root_path_override=root_path_override,
        )
        if err_code or not resolved_root:
            return WorkspaceProvisioningResult(
                status=WorkspaceProvisioningStatus.FAILED,
                workspace=None,
                execution_context=None,
                error_code=err_code or WorkspaceProvisioningErrorCode.WORKSPACE_UNAVAILABLE,
                error_message=err_msg or "Failed to resolve project root path.",
            )

        # Step 7: Prepare execution workspace directory
        staging_dir: Optional[str] = None
        if iso == WorkspaceIsolationMode.SHARED:
            workspace_root = resolved_root
            isolation_capability = "LOGICAL_PATH_BOUNDARY"
        else:  # ISOLATED
            staging_base = self.staging_base_dir or os.path.join(tempfile.gettempdir(), "autonomos_staging")
            try:
                os.makedirs(staging_base, exist_ok=True)
                staging_dir = os.path.join(staging_base, execution.execution_id)
                os.makedirs(staging_dir, exist_ok=True)
                workspace_root = staging_dir
                isolation_capability = "LOCAL_STAGING_DIRECTORY"
            except (PermissionError, OSError) as e:
                return WorkspaceProvisioningResult(
                    status=WorkspaceProvisioningStatus.FAILED,
                    workspace=None,
                    execution_context=None,
                    error_code=WorkspaceProvisioningErrorCode.RESOURCE_FAILURE,
                    error_message=f"Failed to create isolated staging directory: {e}",
                    isolation_capability="UNAVAILABLE",
                )

        # Step 8: Build ProgrammerWorkspace
        try:
            workspace = ProgrammerWorkspace.from_work_order(
                work_order=work_order,
                root_path=workspace_root,
                execution_id=execution.execution_id,
                isolation_mode=iso,
                metadata={
                    "isolation_capability": isolation_capability,
                    "resolved_project_root": resolved_root,
                },
            )
        except (InvalidWorkspaceError, InvalidPathScopeError, ProgrammerLineageError, ProgrammerValidationError) as e:
            if staging_dir and os.path.exists(staging_dir):
                shutil.rmtree(staging_dir, ignore_errors=True)
            return WorkspaceProvisioningResult(
                status=WorkspaceProvisioningStatus.FAILED,
                workspace=None,
                execution_context=None,
                error_code=WorkspaceProvisioningErrorCode.INVALID_WORKSPACE,
                error_message=str(e),
                isolation_capability=isolation_capability,
            )
        except Exception as e:
            if staging_dir and os.path.exists(staging_dir):
                shutil.rmtree(staging_dir, ignore_errors=True)
            return WorkspaceProvisioningResult(
                status=WorkspaceProvisioningStatus.FAILED,
                workspace=None,
                execution_context=None,
                error_code=WorkspaceProvisioningErrorCode.RESOURCE_FAILURE,
                error_message=f"Unexpected failure constructing workspace: {e}",
                isolation_capability=isolation_capability,
            )

        # Step 9: Build ProgrammerExecutionContext
        try:
            execution_context = ProgrammerExecutionContext.create(
                workspace=workspace,
                execution=execution,
                work_order=work_order,
                metadata={
                    "isolation_capability": isolation_capability,
                    "project_root": resolved_root,
                },
            )
        except (ProgrammerLineageError, ProgrammerValidationError) as e:
            if staging_dir and os.path.exists(staging_dir):
                shutil.rmtree(staging_dir, ignore_errors=True)
            return WorkspaceProvisioningResult(
                status=WorkspaceProvisioningStatus.FAILED,
                workspace=None,
                execution_context=None,
                error_code=WorkspaceProvisioningErrorCode.INVALID_WORKSPACE,
                error_message=f"Lineage or execution context binding error: {e}",
                isolation_capability=isolation_capability,
            )

        # Step 10: Construct READY result & register
        result = WorkspaceProvisioningResult(
            status=WorkspaceProvisioningStatus.READY,
            workspace=workspace,
            execution_context=execution_context,
            error_code=None,
            error_message=None,
            isolation_capability=isolation_capability,
            provisioned_at=utc_now(),
            metadata=dict(metadata or {}),
        )

        self._workspaces_by_id[workspace.workspace_id] = workspace
        self._results_by_key[idempotency_key] = result
        self._results_by_ws_id[workspace.workspace_id] = result
        if staging_dir:
            self._staging_dirs_by_ws_id[workspace.workspace_id] = staging_dir

        return result

    def is_provisioned(self, workspace_id: str) -> bool:
        """Check whether a workspace is active and in READY provisioning state."""
        res = self._results_by_ws_id.get(workspace_id)
        return res is not None and res.is_ready()

    def get_workspace(self, workspace_id: str) -> Optional[ProgrammerWorkspace]:
        """Retrieve a provisioned workspace by its identifier."""
        return self._workspaces_by_id.get(workspace_id)

    def get_result(self, workspace_id: str) -> Optional[WorkspaceProvisioningResult]:
        """Retrieve a provisioning result by workspace identifier."""
        return self._results_by_ws_id.get(workspace_id)

    def clean_workspace(self, workspace_id: str) -> bool:
        """
        Clean up resources associated with a provisioned workspace.
        Cleans isolated staging directory if created and unregisters tracking records.
        """
        staging_dir = self._staging_dirs_by_ws_id.pop(workspace_id, None)
        if staging_dir and os.path.exists(staging_dir):
            try:
                shutil.rmtree(staging_dir, ignore_errors=True)
            except Exception:
                pass

        ws = self._workspaces_by_id.pop(workspace_id, None)
        res = self._results_by_ws_id.pop(workspace_id, None)
        if res and res.execution_context:
            key = (res.execution_context.work_order_id, res.execution_context.execution_id)
            self._results_by_key.pop(key, None)

        return ws is not None

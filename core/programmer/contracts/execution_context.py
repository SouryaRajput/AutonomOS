from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from typing import Any, Optional, Union

from core.programmer.contracts.command_resolver import (
    CommandBoundaryResolver,
    CommandDecision,
    CommandRequest,
)
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.filesystem_resolver import (
    FilesystemBoundaryResolver,
    FilesystemDecision,
)
from core.programmer.contracts.identifiers import (
    validate_execution_id,
    validate_work_order_id,
    validate_workspace_id,
)
from core.programmer.contracts.workspace import ProgrammerWorkspace
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    ExecutionContextStatus,
    FilesystemOperation,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProgrammerExecutionContext:
    """
    Unified runtime execution context binding together:
    WorkOrder + Execution + Workspace + Filesystem Policy + Command Policy + Budgets.
    
    This is the authoritative capability envelope that the future Cline adapter will receive.
    
    Architectural Guarantees:
    - Cline receives capabilities through this context.
    - Cline does NOT receive unrestricted access to the project.
    - The context is immutable after activation (READY).
    - Preserves causal lineage: ManagerTask -> ProgrammerWorkOrder -> ProgrammerExecution -> ExecutionContext.
    - A FAILED context strictly refuses all execution capability queries.
    """
    execution_id: str
    work_order_id: str
    workspace_id: str
    project_id: str
    correlation_id: str
    workspace: ProgrammerWorkspace
    work_order: Optional[ProgrammerWorkOrder] = None
    execution: Optional[ProgrammerExecution] = None
    filesystem_policy: FilesystemBoundaryResolver = field(default_factory=FilesystemBoundaryResolver)
    command_policy: CommandBoundaryResolver = field(default_factory=CommandBoundaryResolver)
    budgets: dict[str, Any] = field(default_factory=dict)
    status: ExecutionContextStatus = ExecutionContextStatus.READY
    error_message: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    _frozen: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = ExecutionContextStatus(self.status.upper())
            except (ValueError, TypeError):
                self.status = ExecutionContextStatus.FAILED

        if self.work_order and not self.budgets:
            b = {}
            if hasattr(self.work_order, "iteration_budget"):
                b["iteration_budget"] = self.work_order.iteration_budget
            if hasattr(self.work_order, "time_budget"):
                b["time_budget"] = self.work_order.time_budget
            if hasattr(self.work_order, "budgets"):
                b.update(getattr(self.work_order, "budgets", {}))
            self.budgets = b

        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        validate_workspace_id(self.workspace_id)

        # Automatically validate if instantiated directly into READY state
        if self.status == ExecutionContextStatus.READY:
            self.validate()
            self._frozen = True

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False) and name != "_frozen":
            raise ProgrammerError(
                f"ProgrammerExecutionContext is immutable after activation. Cannot modify attribute '{name}'.",
                code="IMMUTABLE_CONTEXT_ERROR",
            )
        super().__setattr__(name, value)

    def validate(self) -> None:
        """
        Validate cross-entity lineage and ensure malformed workspace context cannot become executable.
        Raises ProgrammerLineageError or ProgrammerValidationError if boundaries or ownership do not align.
        """
        if not self.project_id or not self.project_id.strip():
            raise ProgrammerLineageError("ProgrammerExecutionContext must specify a valid project_id.")
        if not self.correlation_id or not self.correlation_id.strip():
            raise ProgrammerLineageError("ProgrammerExecutionContext must specify a valid correlation_id.")

        # Lineage with Workspace
        if self.workspace.workspace_id != self.workspace_id:
            raise ProgrammerLineageError(
                f"Workspace ID mismatch: context has '{self.workspace_id}', but workspace object is '{self.workspace.workspace_id}'."
            )
        if self.workspace.work_order_id != self.work_order_id:
            raise ProgrammerLineageError(
                f"Work order mismatch: context belongs to '{self.work_order_id}', but workspace belongs to '{self.workspace.work_order_id}'."
            )
        if self.workspace.project_id != self.project_id:
            raise ProgrammerLineageError(
                f"Project mismatch: context belongs to '{self.project_id}', but workspace belongs to '{self.workspace.project_id}'."
            )
        if self.workspace.execution_id and self.workspace.execution_id != self.execution_id:
            raise ProgrammerLineageError(
                f"Execution mismatch: context is '{self.execution_id}', but workspace is bound to '{self.workspace.execution_id}'."
            )

        # Validate underlying workspace
        self.workspace.validate()

        # Lineage with WorkOrder (if attached)
        if self.work_order:
            if self.work_order.work_order_id != self.work_order_id:
                raise ProgrammerLineageError(
                    f"Work order mismatch: context has '{self.work_order_id}', attached work order is '{self.work_order.work_order_id}'."
                )
            if self.work_order.project_id != self.project_id:
                raise ProgrammerLineageError(
                    f"Project mismatch: context is '{self.project_id}', attached work order is '{self.work_order.project_id}'."
                )
            self.work_order.validate()

        # Lineage with Execution (if attached)
        if self.execution:
            if self.execution.execution_id != self.execution_id:
                raise ProgrammerLineageError(
                    f"Execution mismatch: context has '{self.execution_id}', attached execution is '{self.execution.execution_id}'."
                )
            if self.execution.work_order_id != self.work_order_id:
                raise ProgrammerLineageError(
                    f"Work order mismatch: context has '{self.work_order_id}', attached execution belongs to '{self.execution.work_order_id}'."
                )
            if self.execution.project_id != self.project_id:
                raise ProgrammerLineageError(
                    f"Project mismatch: context is '{self.project_id}', attached execution belongs to '{self.execution.project_id}'."
                )
            if self.work_order and self.execution.task_id != self.work_order.manager_task_id:
                raise ProgrammerLineageError(
                    f"Manager task mismatch: execution belongs to task '{self.execution.task_id}', attached work order belongs to '{self.work_order.manager_task_id}'."
                )

    def assert_ready(self) -> None:
        """Enforce that the context is in READY status and usable for capability execution."""
        if self.status != ExecutionContextStatus.READY:
            err_details = f": {self.error_message}" if self.error_message else ""
            raise ProgrammerError(
                f"ProgrammerExecutionContext is in '{self.status.value}' state and cannot be used for execution{err_details}.",
                code="FAILED_CONTEXT_ERROR",
            )

    def is_ready(self) -> bool:
        """Check whether the context is activated and ready for operational queries."""
        return self.status == ExecutionContextStatus.READY and not self.error_message

    def is_failed(self) -> bool:
        """Check whether the context is in a failure state."""
        return self.status == ExecutionContextStatus.FAILED

    def _enrich_trace(self, trace_dict: dict[str, Any]) -> dict[str, Any]:
        """Enrich an authorization decision trace with execution context lineage."""
        trace_dict["execution_id"] = self.execution_id
        trace_dict["work_order_id"] = self.work_order_id
        trace_dict["manager_task_id"] = self.manager_task_id
        trace_dict["workspace_id"] = self.workspace_id
        trace_dict["project_id"] = self.project_id
        return trace_dict

    # ==========================================================================
    # Runtime Capability Query API for future Cline adapter
    # ==========================================================================

    def may_read_file(self, path: str) -> FilesystemDecision:
        """Evaluate whether reading the specified path is authorized by policy."""
        self.assert_ready()
        decision = self.filesystem_policy.resolve(self.workspace, path, FilesystemOperation.READ)
        self._enrich_trace(decision.trace)
        return decision

    def may_write_file(self, path: str) -> FilesystemDecision:
        """Evaluate whether modifying/writing the specified path is authorized by policy."""
        self.assert_ready()
        decision = self.filesystem_policy.resolve(self.workspace, path, FilesystemOperation.WRITE)
        self._enrich_trace(decision.trace)
        return decision

    def may_create_file(self, path: str) -> FilesystemDecision:
        """Evaluate whether creating a file at the specified path is authorized by policy."""
        self.assert_ready()
        decision = self.filesystem_policy.resolve(self.workspace, path, FilesystemOperation.CREATE)
        self._enrich_trace(decision.trace)
        return decision

    def may_delete_file(self, path: str) -> FilesystemDecision:
        """Evaluate whether deleting the specified path is authorized by policy."""
        self.assert_ready()
        decision = self.filesystem_policy.resolve(self.workspace, path, FilesystemOperation.DELETE)
        self._enrich_trace(decision.trace)
        return decision

    def may_rename_file(self, source_path: str, destination_path: str) -> FilesystemDecision:
        """Evaluate whether renaming a file from source to destination is authorized by policy."""
        self.assert_ready()
        decision = self.filesystem_policy.resolve(
            self.workspace,
            source_path,
            FilesystemOperation.RENAME,
            destination_path=destination_path,
        )
        self._enrich_trace(decision.trace)
        return decision

    def may_execute_command(
        self,
        request: Union[CommandRequest, str],
        working_directory: Optional[str] = None,
    ) -> CommandDecision:
        """Evaluate whether executing the specified command is authorized by policy."""
        self.assert_ready()
        if not self.work_order:
            raise ProgrammerError("Cannot evaluate command authorization: work_order is not attached to context.")
        decision = self.command_policy.resolve(
            self.work_order,
            request,
            workspace=self.workspace,
            working_directory=working_directory,
        )
        self._enrich_trace(decision.trace)
        return decision

    def resolve_working_directory(self, rel_path: Optional[str] = None) -> str:
        """Resolve a confined working directory within the workspace root."""
        self.assert_ready()
        if not rel_path or rel_path == ".":
            return os.path.abspath(self.workspace.root_path)
        return self.workspace.resolve_path(rel_path)

    # ==========================================================================
    # Direct boundary inspection queries
    # ==========================================================================

    def is_path_writable(self, path: str) -> bool:
        """Check whether a relative path is authorized for write operations in the bound workspace."""
        self.assert_ready()
        return self.workspace.is_path_writable(path)

    def is_path_allowed(self, path: str) -> bool:
        """Check whether a relative path is within allowed read/inspection scope in the bound workspace."""
        self.assert_ready()
        return self.workspace.is_path_allowed(path)

    def is_path_read_only(self, path: str) -> bool:
        """Check whether a relative path is designated read-only in the bound workspace."""
        self.assert_ready()
        return self.workspace.is_path_read_only(path)

    def is_path_forbidden(self, path: str) -> bool:
        """Check whether a relative path is forbidden in the bound workspace."""
        self.assert_ready()
        return self.workspace.is_path_forbidden(path)

    def resolve_path(self, rel_path: str) -> str:
        """Resolve a relative workspace path to an absolute path within the workspace root."""
        self.assert_ready()
        return self.workspace.resolve_path(rel_path)

    @property
    def manager_task_id(self) -> str:
        """Lineage reference to the originating Manager task."""
        if self.work_order:
            return self.work_order.manager_task_id
        if self.execution:
            return self.execution.task_id
        return ""

    @property
    def working_directory(self) -> str:
        """Default workspace root working directory."""
        return os.path.abspath(self.workspace.root_path)

    # ==========================================================================
    # Factories & Serialization
    # ==========================================================================

    @classmethod
    def build(
        cls,
        work_order: ProgrammerWorkOrder,
        execution: ProgrammerExecution,
        workspace: ProgrammerWorkspace,
        filesystem_policy: Optional[FilesystemBoundaryResolver] = None,
        command_policy: Optional[CommandBoundaryResolver] = None,
        metadata: Optional[dict[str, Any]] = None,
        raise_on_failure: bool = True,
    ) -> ProgrammerExecutionContext:
        """
        Deterministic factory managing the full lifecycle:
        CREATING -> VALIDATING -> READY (or FAILED).
        """
        correlation_id = (
            execution.correlation_id
            or work_order.correlation_id
            or workspace.trace.get("correlation_id", "")
            or execution.task_id
        )

        # Bind execution_id to workspace if not yet bound
        if not workspace.execution_id:
            workspace.execution_id = execution.execution_id

        fs_policy = filesystem_policy or FilesystemBoundaryResolver()
        cmd_policy = command_policy or CommandBoundaryResolver()

        budgets = {}
        if hasattr(work_order, "iteration_budget"):
            budgets["iteration_budget"] = work_order.iteration_budget
        if hasattr(work_order, "time_budget"):
            budgets["time_budget"] = work_order.time_budget
        if hasattr(work_order, "budgets"):
            budgets.update(getattr(work_order, "budgets", {}))

        context = cls(
            execution_id=execution.execution_id,
            work_order_id=work_order.work_order_id,
            workspace_id=workspace.workspace_id,
            project_id=work_order.project_id,
            correlation_id=correlation_id,
            workspace=workspace,
            work_order=work_order,
            execution=execution,
            filesystem_policy=fs_policy,
            command_policy=cmd_policy,
            budgets=budgets,
            status=ExecutionContextStatus.CREATING,
            metadata=dict(metadata or {}),
            _frozen=False,
        )

        # Step 2: VALIDATING
        context.status = ExecutionContextStatus.VALIDATING
        try:
            context.validate()
            # Step 3: READY
            context.status = ExecutionContextStatus.READY
            context._frozen = True
            return context
        except (ProgrammerLineageError, ProgrammerValidationError, ProgrammerError) as err:
            context.status = ExecutionContextStatus.FAILED
            context.error_message = str(err)
            context._frozen = True
            if raise_on_failure:
                raise
            return context
        except Exception as err:
            context.status = ExecutionContextStatus.FAILED
            context.error_message = f"Unexpected validation failure: {err}"
            context._frozen = True
            if raise_on_failure:
                raise ProgrammerValidationError(f"ExecutionContext build failed: {err}")
            return context

    @classmethod
    def create(
        cls,
        workspace: ProgrammerWorkspace,
        execution: ProgrammerExecution,
        work_order: Optional[ProgrammerWorkOrder] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ProgrammerExecutionContext:
        """
        Backward-compatible factory to bind an active ProgrammerExecution to its authorized ProgrammerWorkspace.
        """
        if work_order is not None:
            return cls.build(
                work_order=work_order,
                execution=execution,
                workspace=workspace,
                metadata=metadata,
            )

        correlation_id = (
            execution.correlation_id
            or workspace.trace.get("correlation_id", "")
            or execution.task_id
        )

        if not workspace.execution_id:
            workspace.execution_id = execution.execution_id

        context = cls(
            execution_id=execution.execution_id,
            work_order_id=execution.work_order_id,
            workspace_id=workspace.workspace_id,
            project_id=execution.project_id,
            correlation_id=correlation_id,
            workspace=workspace,
            work_order=work_order,
            execution=execution,
            status=ExecutionContextStatus.READY,
            metadata=dict(metadata or {}),
        )
        return context

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "status": self.status.value if isinstance(self.status, ExecutionContextStatus) else str(self.status),
            "error_message": self.error_message,
            "workspace": self.workspace.to_dict(),
            "work_order_id_ref": self.work_order.work_order_id if self.work_order else None,
            "execution_id_ref": self.execution.execution_id if self.execution else None,
            "budgets": dict(self.budgets),
            "created_at": self.created_at,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        workspace: Optional[ProgrammerWorkspace] = None,
        work_order: Optional[ProgrammerWorkOrder] = None,
        execution: Optional[ProgrammerExecution] = None,
    ) -> ProgrammerExecutionContext:
        ws = workspace or ProgrammerWorkspace.from_dict(data["workspace"])
        st_raw = data.get("status", ExecutionContextStatus.READY.value)
        try:
            status = ExecutionContextStatus(str(st_raw).upper())
        except (ValueError, TypeError):
            status = ExecutionContextStatus.FAILED

        return cls(
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            workspace_id=data["workspace_id"],
            project_id=data["project_id"],
            correlation_id=data.get("correlation_id", ""),
            workspace=ws,
            work_order=work_order,
            execution=execution,
            budgets=dict(data.get("budgets", {})),
            status=status,
            error_message=data.get("error_message"),
            created_at=data.get("created_at", utc_now()),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )

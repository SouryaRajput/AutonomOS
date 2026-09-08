from __future__ import annotations

from typing import Any, Optional

from core.errors import AutonomOSError


class ProgrammerError(AutonomOSError):
    """Base exception for all Programmer subsystem errors."""

    def __init__(
        self,
        message: str,
        code: str = "PROGRAMMER_ERROR",
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message=message, code=code, details=details)


class InvalidProgrammerIdError(ProgrammerError):
    """Raised when an identifier does not conform to the Programmer subsystem prefix or format."""

    def __init__(self, identifier_type: str, identifier_value: str, expected_prefix: str):
        super().__init__(
            message=f"Invalid {identifier_type} identifier '{identifier_value}'. Expected prefix '{expected_prefix}'.",
            code="INVALID_PROGRAMMER_ID",
            details={
                "identifier_type": identifier_type,
                "identifier_value": identifier_value,
                "expected_prefix": expected_prefix,
            },
        )
        self.identifier_type = identifier_type
        self.identifier_value = identifier_value
        self.expected_prefix = expected_prefix


class ProgrammerLineageError(ProgrammerError):
    """Raised when an entity's parent lineage or correlation reference is missing, broken, or tampered with."""

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(
            message=message,
            code="PROGRAMMER_LINEAGE_ERROR",
            details=details,
        )


class InvalidProgrammerTransitionError(ProgrammerError):
    """Raised when an illegal lifecycle transition is attempted on a Programmer execution or work order."""

    def __init__(
        self,
        entity_id: str,
        current_status: str,
        target_status: str,
        reason: str = "",
    ):
        msg = f"Cannot transition '{entity_id}' from '{current_status}' to '{target_status}'"
        if reason:
            msg += f": {reason}"
        super().__init__(
            message=msg,
            code="INVALID_PROGRAMMER_TRANSITION",
            details={
                "entity_id": entity_id,
                "current_status": current_status,
                "target_status": target_status,
                "reason": reason,
            },
        )
        self.entity_id = entity_id
        self.current_status = current_status
        self.target_status = target_status


class ProgrammerBlockerError(ProgrammerError):
    """Raised when a material blocker prevents execution from proceeding, requiring escalation to Manager."""

    def __init__(self, work_order_id: str, blocker: str, details: Optional[dict[str, Any]] = None):
        d = {"work_order_id": work_order_id, "blocker": blocker}
        if details:
            d.update(details)
        super().__init__(
            message=f"Execution blocked for work order '{work_order_id}': {blocker}",
            code="PROGRAMMER_MATERIAL_BLOCKER",
            details=d,
        )
        self.work_order_id = work_order_id
        self.blocker = blocker


class ProgrammerValidationError(ProgrammerError):
    """Raised when a Programmer contract fails schema, boundary, or sanity validation."""

    def __init__(
        self,
        message: str,
        field_name: Optional[str] = None,
        code: str = "PROGRAMMER_VALIDATION_ERROR",
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        if field_name:
            d["field_name"] = field_name
        super().__init__(message=message, code=code, details=d)
        self.field_name = field_name


class InvalidPathScopeError(ProgrammerValidationError):
    """Raised when path scopes are malformed, contradictory, or violate confinement policies."""

    def __init__(self, message: str, path: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if path:
            d["path"] = path
        super().__init__(
            message=message,
            field_name="paths",
            code="INVALID_PATH_SCOPE",
            details=d,
        )
        self.path = path


class InvalidCommandScopeError(ProgrammerValidationError):
    """Raised when allowed commands are malformed or invalid."""

    def __init__(self, message: str, command: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if command:
            d["command"] = command
        super().__init__(
            message=message,
            field_name="allowed_commands",
            code="INVALID_COMMAND_SCOPE",
            details=d,
        )
        self.command = command


class InvalidBudgetError(ProgrammerValidationError):
    """Raised when execution budgets are non-positive or malformed."""

    def __init__(self, message: str, budget_type: Optional[str] = None, value: Any = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if budget_type:
            d["budget_type"] = budget_type
        if value is not None:
            d["value"] = value
        super().__init__(
            message=message,
            field_name="budgets",
            code="INVALID_BUDGET",
            details=d,
        )
        self.budget_type = budget_type
        self.value = value


class InvalidWorkspaceError(ProgrammerValidationError):
    """Raised when a workspace fails boundary, ownership, or isolation validation."""

    def __init__(self, message: str, workspace_id: Optional[str] = None, details: Optional[dict[str, Any]] = None):
        d = dict(details or {})
        if workspace_id:
            d["workspace_id"] = workspace_id
        super().__init__(
            message=message,
            field_name="workspace",
            code="INVALID_WORKSPACE",
            details=d,
        )
        self.workspace_id = workspace_id


class WorkspaceProvisioningError(ProgrammerError):
    """Raised when workspace provisioning fails deterministically."""

    def __init__(
        self,
        message: str,
        error_code: str = "UNKNOWN",
        workspace_id: Optional[str] = None,
        project_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        code_str = error_code.value if hasattr(error_code, "value") else str(error_code)
        d["error_code"] = code_str
        if workspace_id:
            d["workspace_id"] = workspace_id
        if project_id:
            d["project_id"] = project_id
        super().__init__(
            message=message,
            code=f"WORKSPACE_PROVISIONING_{code_str}",
            details=d,
        )
        self.error_code = error_code
        self.workspace_id = workspace_id
        self.project_id = project_id


class GitWorktreeError(ProgrammerError):
    """Raised when Git worktree provisioning, lifecycle transition, or cleanup fails."""

    def __init__(
        self,
        message: str,
        error_code: str = "UNKNOWN",
        worktree_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        repository_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        d = dict(details or {})
        code_str = error_code.value if hasattr(error_code, "value") else str(error_code)
        d["error_code"] = code_str
        if worktree_id:
            d["worktree_id"] = worktree_id
        if execution_id:
            d["execution_id"] = execution_id
        if repository_id:
            d["repository_id"] = repository_id
        super().__init__(
            message=message,
            code=f"GIT_WORKTREE_{code_str}",
            details=d,
        )
        self.error_code = error_code
        self.worktree_id = worktree_id
        self.execution_id = execution_id
        self.repository_id = repository_id




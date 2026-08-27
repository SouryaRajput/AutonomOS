from typing import Any, Optional


class AutonomOSError(Exception):
    """Base exception for all AutonomOS runtime errors."""

    def __init__(self, message: str, code: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            "details": self.details,
        }


# Project Errors
class ProjectNotFoundError(AutonomOSError):
    def __init__(self, project_id: str):
        super().__init__(
            f"Project with ID '{project_id}' not found.",
            code="PROJECT_NOT_FOUND",
            details={"project_id": project_id},
        )


class ProjectAlreadyExistsError(AutonomOSError):
    def __init__(self, project_id: str):
        super().__init__(
            f"Project with ID '{project_id}' already exists.",
            code="PROJECT_ALREADY_EXISTS",
            details={"project_id": project_id},
        )


# Worker Errors
class WorkerNotFoundError(AutonomOSError):
    def __init__(self, worker_id: str):
        super().__init__(
            f"Worker with ID '{worker_id}' not found.",
            code="WORKER_NOT_FOUND",
            details={"worker_id": worker_id},
        )


class WorkerAlreadyExistsError(AutonomOSError):
    def __init__(self, worker_id: str):
        super().__init__(
            f"Worker with ID '{worker_id}' is already registered.",
            code="WORKER_ALREADY_EXISTS",
            details={"worker_id": worker_id},
        )


class WorkerNotEligibleError(AutonomOSError):
    def __init__(self, worker_id: str, reason: str):
        super().__init__(
            f"Worker '{worker_id}' is not eligible: {reason}",
            code="WORKER_NOT_ELIGIBLE",
            details={"worker_id": worker_id, "reason": reason},
        )


class WorkerBusyError(AutonomOSError):
    def __init__(self, worker_id: str, active_task_id: str):
        super().__init__(
            f"Worker '{worker_id}' is currently busy executing task '{active_task_id}'.",
            code="WORKER_BUSY",
            details={"worker_id": worker_id, "active_task_id": active_task_id},
        )


class InvalidWorkerTransitionError(AutonomOSError):
    def __init__(self, worker_id: str, from_status: str, to_status: str):
        super().__init__(
            f"Invalid worker state transition from '{from_status}' to '{to_status}' for worker '{worker_id}'.",
            code="INVALID_WORKER_TRANSITION",
            details={"worker_id": worker_id, "from_status": from_status, "to_status": to_status},
        )


# Task Errors
class TaskNotFoundError(AutonomOSError):
    def __init__(self, task_id: str):
        super().__init__(
            f"Task with ID '{task_id}' not found.",
            code="TASK_NOT_FOUND",
            details={"task_id": task_id},
        )


class TaskAlreadyAssignedError(AutonomOSError):
    def __init__(self, task_id: str, assigned_worker: str):
        super().__init__(
            f"Task '{task_id}' is already assigned to worker '{assigned_worker}'.",
            code="TASK_ALREADY_ASSIGNED",
            details={"task_id": task_id, "assigned_worker": assigned_worker},
        )


class TaskAlreadyCompletedError(AutonomOSError):
    def __init__(self, task_id: str):
        super().__init__(
            f"Task '{task_id}' is already completed and cannot be re-executed.",
            code="TASK_ALREADY_COMPLETED",
            details={"task_id": task_id},
        )


class InvalidTaskTransitionError(AutonomOSError):
    def __init__(self, task_id: str, from_status: str, to_status: str, reason: Optional[str] = None):
        msg = f"Invalid task state transition from '{from_status}' to '{to_status}' for task '{task_id}'."
        if reason:
            msg += f" Reason: {reason}"
        super().__init__(
            msg,
            code="INVALID_TASK_TRANSITION",
            details={"task_id": task_id, "from_status": from_status, "to_status": to_status, "reason": reason},
        )


# Dependency Errors
class DependencyNotSatisfiedError(AutonomOSError):
    def __init__(self, task_id: str, unfulfilled_dependencies: list[str]):
        super().__init__(
            f"Cannot execute task '{task_id}': dependencies not satisfied: {unfulfilled_dependencies}",
            code="DEPENDENCY_NOT_SATISFIED",
            details={"task_id": task_id, "unfulfilled_dependencies": unfulfilled_dependencies},
        )


class DependencyCycleError(AutonomOSError):
    def __init__(self, task_id: str, prerequisite_id: str):
        super().__init__(
            f"Adding dependency from '{task_id}' to '{prerequisite_id}' would create a circular dependency cycle.",
            code="DEPENDENCY_CYCLE_DETECTED",
            details={"task_id": task_id, "prerequisite_id": prerequisite_id},
        )


# Execution & Sandbox Errors
class ExecutionFailedError(AutonomOSError):
    def __init__(self, task_id: str, worker_id: str, cause: str):
        super().__init__(
            f"Execution of task '{task_id}' by worker '{worker_id}' failed: {cause}",
            code="EXECUTION_FAILED",
            details={"task_id": task_id, "worker_id": worker_id, "cause": cause},
        )


class PersistenceError(AutonomOSError):
    def __init__(self, operation: str, cause: str):
        super().__init__(
            f"Persistence error during '{operation}': {cause}",
            code="PERSISTENCE_ERROR",
            details={"operation": operation, "cause": cause},
        )


class ArtifactNotFoundError(AutonomOSError):
    def __init__(self, artifact_id: str):
        super().__init__(
            f"Artifact with ID '{artifact_id}' not found.",
            code="ARTIFACT_NOT_FOUND",
            details={"artifact_id": artifact_id},
        )


# Memory Errors (Stage 3)
class MemoryNotFoundError(AutonomOSError):
    def __init__(self, memory_id: str, path: Optional[str] = None):
        msg = f"Memory document '{memory_id}' not found."
        if path:
            msg += f" (Path: {path})"
        super().__init__(
            msg,
            code="MEMORY_NOT_FOUND",
            details={"memory_id": memory_id, "path": path},
        )


class MemoryAlreadyExistsError(AutonomOSError):
    def __init__(self, identifier: str):
        super().__init__(
            f"Memory document '{identifier}' already exists.",
            code="MEMORY_ALREADY_EXISTS",
            details={"identifier": identifier},
        )


class MemoryValidationError(AutonomOSError):
    def __init__(self, memory_id: str, issues: list[str]):
        super().__init__(
            f"Validation failed for memory document '{memory_id}': {issues}",
            code="MEMORY_VALIDATION_ERROR",
            details={"memory_id": memory_id, "issues": issues},
        )


class MemoryConflictError(AutonomOSError):
    def __init__(self, memory_id: str, current_version: int, expected_version: int):
        super().__init__(
            f"Concurrent modification conflict on memory '{memory_id}': current version is {current_version}, expected {expected_version}.",
            code="MEMORY_CONFLICT",
            details={"memory_id": memory_id, "current_version": current_version, "expected_version": expected_version},
        )


# Tool Errors (Stage 5)
class ToolNotFoundError(AutonomOSError):
    def __init__(self, tool_id: str):
        super().__init__(
            f"Tool with ID '{tool_id}' not found in registry.",
            code="TOOL_NOT_FOUND",
            details={"tool_id": tool_id},
        )


class ToolPermissionDeniedError(AutonomOSError):
    def __init__(self, worker_id: str, tool_id: str, required_permissions: list[str]):
        super().__init__(
            f"Permission denied for worker '{worker_id}' to execute tool '{tool_id}'. Required: {required_permissions}",
            code="TOOL_PERMISSION_DENIED",
            details={"worker_id": worker_id, "tool_id": tool_id, "required_permissions": required_permissions},
        )


class ToolArgumentValidationError(AutonomOSError):
    def __init__(self, tool_id: str, errors: list[str]):
        super().__init__(
            f"Argument validation failed for tool '{tool_id}': {errors}",
            code="TOOL_ARGUMENT_VALIDATION_ERROR",
            details={"tool_id": tool_id, "errors": errors},
        )


class ToolExecutionError(AutonomOSError):
    def __init__(self, tool_id: str, message: str, exit_code: Optional[int] = None):
        super().__init__(
            f"Execution of tool '{tool_id}' failed: {message}",
            code="TOOL_EXECUTION_ERROR",
            details={"tool_id": tool_id, "exit_code": exit_code, "cause": message},
        )


class ToolTimeoutError(AutonomOSError):
    def __init__(self, tool_id: str, timeout_seconds: int):
        super().__init__(
            f"Tool '{tool_id}' timed out after {timeout_seconds} seconds.",
            code="TOOL_TIMEOUT",
            details={"tool_id": tool_id, "timeout_seconds": timeout_seconds},
        )


class ToolWorkspaceViolationError(AutonomOSError):
    def __init__(self, attempted_path: str, workspace_root: str):
        super().__init__(
            f"Security violation: Attempted path '{attempted_path}' escapes workspace boundary '{workspace_root}'.",
            code="TOOL_WORKSPACE_VIOLATION",
            details={"attempted_path": attempted_path, "workspace_root": workspace_root},
        )


class ToolOutputLimitError(AutonomOSError):
    def __init__(self, tool_id: str, output_size: int, limit_size: int):
        super().__init__(
            f"Tool '{tool_id}' exceeded output size limit: {output_size} bytes (Limit: {limit_size} bytes).",
            code="TOOL_OUTPUT_LIMIT_EXCEEDED",
            details={"tool_id": tool_id, "output_size": output_size, "limit_size": limit_size},
        )


class ToolUnavailableError(AutonomOSError):
    def __init__(self, tool_id: str, reason: str):
        super().__init__(
            f"Tool '{tool_id}' is currently unavailable: {reason}",
            code="TOOL_UNAVAILABLE",
            details={"tool_id": tool_id, "reason": reason},
        )

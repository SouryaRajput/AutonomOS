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

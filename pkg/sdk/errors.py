from __future__ import annotations

from core.errors import AutonomOSError


class WorkerSDKError(AutonomOSError):
    """Base exception for all Worker SDK level errors."""
    def __init__(self, code: str, message: str):
        super().__init__(code, message)


class WorkerExecutionError(WorkerSDKError):
    """Raised when an unhandled error occurs during worker task execution."""
    def __init__(self, worker_id: str, reason: str):
        super().__init__("WORKER_EXECUTION_ERROR", f"Worker '{worker_id}' execution failed: {reason}")
        self.worker_id = worker_id
        self.reason = reason


class WorkerConfigurationError(WorkerSDKError):
    """Raised when worker configuration is invalid or missing required keys."""
    def __init__(self, worker_id: str, reason: str):
        super().__init__("WORKER_CONFIGURATION_ERROR", f"Worker '{worker_id}' configuration invalid: {reason}")
        self.worker_id = worker_id
        self.reason = reason


class WorkerContextError(WorkerSDKError):
    """Raised when context retrieval fails or returns invalid package."""
    def __init__(self, reason: str):
        super().__init__("WORKER_CONTEXT_ERROR", f"Context Engine operation failed: {reason}")
        self.reason = reason


class WorkerToolError(WorkerSDKError):
    """Raised when a tool requested by a worker fails or is denied."""
    def __init__(self, tool_id: str, reason: str):
        super().__init__("WORKER_TOOL_ERROR", f"Tool '{tool_id}' execution failed: {reason}")
        self.tool_id = tool_id
        self.reason = reason


class WorkerInferenceError(WorkerSDKError):
    """Raised when an inference request fails across all routed providers."""
    def __init__(self, reason: str):
        super().__init__("WORKER_INFERENCE_ERROR", f"Inference Gateway operation failed: {reason}")
        self.reason = reason


class WorkerMemoryError(WorkerSDKError):
    """Raised when project memory access or mutation fails."""
    def __init__(self, reason: str):
        super().__init__("WORKER_MEMORY_ERROR", f"Memory operation failed: {reason}")
        self.reason = reason


class WorkerArtifactError(WorkerSDKError):
    """Raised when artifact registration or retrieval fails."""
    def __init__(self, path: str, reason: str):
        super().__init__("WORKER_ARTIFACT_ERROR", f"Artifact operation on '{path}' failed: {reason}")
        self.path = path
        self.reason = reason


class WorkerVerificationError(WorkerSDKError):
    """Raised when verification request cannot be executed."""
    def __init__(self, reason: str):
        super().__init__("WORKER_VERIFICATION_ERROR", f"Verification request failed: {reason}")
        self.reason = reason


class WorkerCancelledError(WorkerSDKError):
    """Raised when worker execution is halted due to active task cancellation."""
    def __init__(self, task_id: str):
        super().__init__("WORKER_CANCELLED", f"Task '{task_id}' execution was cancelled.")
        self.task_id = task_id


class WorkerTimeoutError(WorkerSDKError):
    """Raised when worker task execution exceeds allowed runtime deadline."""
    def __init__(self, task_id: str, timeout_seconds: float):
        super().__init__("WORKER_TIMEOUT", f"Task '{task_id}' timed out after {timeout_seconds}s.")
        self.task_id = task_id
        self.timeout_seconds = timeout_seconds

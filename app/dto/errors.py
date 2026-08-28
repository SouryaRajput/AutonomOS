"""Normalized application error types — never exposes internal stack traces."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ErrorCode(str, Enum):
    """UI-friendly error classification codes."""
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    CONFLICT = "CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    EMERGENCY_STOPPED = "EMERGENCY_STOPPED"
    WORKER_BUSY = "WORKER_BUSY"
    TASK_ALREADY_COMPLETED = "TASK_ALREADY_COMPLETED"
    INVALID_TRANSITION = "INVALID_TRANSITION"


@dataclass
class AppError:
    """Normalized error envelope for the UI — no stack traces."""
    code: ErrorCode
    message: str
    user_message: str
    field_errors: dict[str, str] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "user_message": self.user_message,
            "field_errors": self.field_errors,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppError:
        return cls(
            code=ErrorCode(data["code"]),
            message=data.get("message", ""),
            user_message=data.get("user_message", ""),
            field_errors=dict(data.get("field_errors", {})),
            details=dict(data.get("details", {})),
        )


class AppException(Exception):
    """Raised by application services with a structured AppError."""
    def __init__(self, error: AppError):
        super().__init__(error.user_message)
        self.error = error


# --- Error Code Mapping ---

_ERROR_CODE_MAP = {
    "PROJECT_NOT_FOUND": ErrorCode.NOT_FOUND,
    "TASK_NOT_FOUND": ErrorCode.NOT_FOUND,
    "WORKER_NOT_FOUND": ErrorCode.NOT_FOUND,
    "PROJECT_ALREADY_EXISTS": ErrorCode.CONFLICT,
    "WORKER_ALREADY_EXISTS": ErrorCode.CONFLICT,
    "TASK_ALREADY_COMPLETED": ErrorCode.TASK_ALREADY_COMPLETED,
    "WORKER_BUSY": ErrorCode.WORKER_BUSY,
    "WORKER_NOT_ELIGIBLE": ErrorCode.PERMISSION_DENIED,
    "INVALID_WORKER_TRANSITION": ErrorCode.INVALID_TRANSITION,
    "INVALID_TASK_TRANSITION": ErrorCode.INVALID_TRANSITION,
    "TOOL_PERMISSION_DENIED": ErrorCode.PERMISSION_DENIED,
    "PERSISTENCE_ERROR": ErrorCode.INTERNAL_ERROR,
    "EXECUTION_FAILED": ErrorCode.INTERNAL_ERROR,
    "VALIDATION_ERROR": ErrorCode.VALIDATION_ERROR,
}

_USER_MESSAGE_MAP = {
    ErrorCode.NOT_FOUND: "The requested resource was not found.",
    ErrorCode.VALIDATION_ERROR: "Invalid input. Please check and try again.",
    ErrorCode.PERMISSION_DENIED: "This action is not permitted.",
    ErrorCode.CONFLICT: "A resource with this identifier already exists.",
    ErrorCode.INTERNAL_ERROR: "An internal error occurred. Please try again.",
    ErrorCode.RATE_LIMITED: "Too many requests. Please wait and try again.",
    ErrorCode.EMERGENCY_STOPPED: "The workforce is currently stopped.",
    ErrorCode.WORKER_BUSY: "The worker is currently busy with another task.",
    ErrorCode.TASK_ALREADY_COMPLETED: "This task has already been completed.",
    ErrorCode.INVALID_TRANSITION: "This state transition is not valid.",
}


def normalize_error(exc: Exception) -> AppError:
    """Convert any exception into a safe, UI-friendly AppError."""
    from core.errors import AutonomOSError

    if isinstance(exc, AppException):
        return exc.error

    if isinstance(exc, AutonomOSError):
        code = _ERROR_CODE_MAP.get(exc.code, ErrorCode.INTERNAL_ERROR)
        return AppError(
            code=code,
            message=exc.message,
            user_message=_USER_MESSAGE_MAP.get(code, exc.message),
            details=exc.details,
        )

    # Generic Python exception — NEVER expose stack trace
    return AppError(
        code=ErrorCode.INTERNAL_ERROR,
        message=str(exc) if str(exc) else type(exc).__name__,
        user_message="An unexpected error occurred. Please try again.",
    )

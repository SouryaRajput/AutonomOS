from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from core.tester.contracts.identifiers import (
    new_action_id,
    validate_action_id,
    validate_execution_id,
    validate_runtime_id,
)
from core.tester.errors import TesterValidationError
from core.tester.types import TestActionStatus, TesterActionType


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TestActionRecord:
    """
    Authoritative record of a discrete interaction or navigation action
    executed by a test runtime subordinate to TesterExecution.
    """
    __test__ = False
    action_id: str
    execution_id: str
    runtime_id: str
    action_type: TesterActionType
    target: str
    started_at: str = field(default_factory=utc_now)
    completed_at: Optional[str] = None
    status: TestActionStatus = TestActionStatus.PENDING
    previous_state: Optional[dict[str, Any]] = None
    resulting_state: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    trace: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.action_id:
            self.action_id = new_action_id()
        validate_action_id(self.action_id)
        validate_execution_id(self.execution_id)
        validate_runtime_id(self.runtime_id)

        if not isinstance(self.action_type, TesterActionType):
            try:
                self.action_type = TesterActionType(str(self.action_type).upper())
            except (ValueError, KeyError):
                raise TesterValidationError(
                    f"Invalid action_type '{self.action_type}'. Must be a valid TesterActionType.",
                    field_name="action_type",
                )

        if not isinstance(self.status, TestActionStatus):
            try:
                self.status = TestActionStatus(str(self.status).upper())
            except (ValueError, KeyError):
                raise TesterValidationError(
                    f"Invalid status '{self.status}'. Must be a valid TestActionStatus.",
                    field_name="status",
                )

        self.previous_state = dict(self.previous_state or {})
        self.resulting_state = dict(self.resulting_state or {})
        self.trace = dict(self.trace or {})

    def complete(
        self,
        status: TestActionStatus,
        resulting_state: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Mark action as completed with final status, resulting state, and error if any."""
        self.status = status
        self.completed_at = utc_now()
        if resulting_state:
            self.resulting_state = dict(resulting_state)
        if error:
            self.error = error

    def duration_ms(self) -> Optional[float]:
        """Compute duration of the action in milliseconds if completed."""
        if not self.completed_at or not self.started_at:
            return None
        try:
            t0 = datetime.fromisoformat(self.started_at)
            t1 = datetime.fromisoformat(self.completed_at)
            return (t1 - t0).total_seconds() * 1000.0
        except Exception:
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "execution_id": self.execution_id,
            "runtime_id": self.runtime_id,
            "action_type": self.action_type.value,
            "target": self.target,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status.value,
            "previous_state": dict(self.previous_state or {}),
            "resulting_state": dict(self.resulting_state or {}),
            "error": self.error,
            "trace": dict(self.trace or {}),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestActionRecord:
        action_type_val = data.get("action_type", TesterActionType.NAVIGATE.value)
        try:
            action_type = TesterActionType(str(action_type_val).upper())
        except (ValueError, KeyError):
            action_type = TesterActionType.NAVIGATE

        status_val = data.get("status", TestActionStatus.PENDING.value)
        try:
            status = TestActionStatus(str(status_val).upper())
        except (ValueError, KeyError):
            status = TestActionStatus.PENDING

        return cls(
            action_id=data.get("action_id", ""),
            execution_id=data.get("execution_id", ""),
            runtime_id=data.get("runtime_id", ""),
            action_type=action_type,
            target=data.get("target", ""),
            started_at=data.get("started_at", utc_now()),
            completed_at=data.get("completed_at"),
            status=status,
            previous_state=data.get("previous_state"),
            resulting_state=data.get("resulting_state"),
            error=data.get("error"),
            trace=data.get("trace") or {},
        )

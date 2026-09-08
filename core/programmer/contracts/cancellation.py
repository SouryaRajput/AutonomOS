from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from core.programmer.errors import ProgrammerValidationError


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProgrammerCancellation:
    """
    Provenance record capturing an authorized cancellation of a Programmer execution or work order.
    Records who requested it, when, why it happened, whether the agent actually terminated,
    and whether cleanup completed.
    """
    requested_by: str
    reason: str
    requested_at: str = field(default_factory=utc_now)
    agent_terminated: bool = False
    cleanup_completed: bool = False
    confirmed: bool = False
    confirmation_error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.requested_by or not self.requested_by.strip():
            raise ProgrammerValidationError(
                "Cancellation requested_by cannot be empty.",
                field_name="requested_by",
            )
        if not self.reason or not self.reason.strip():
            raise ProgrammerValidationError(
                "Cancellation reason cannot be empty.",
                field_name="reason",
            )

    @property
    def is_confirmed(self) -> bool:
        """True if the cancellation has been positively confirmed."""
        return self.confirmed

    @property
    def has_error(self) -> bool:
        """True if cancellation encountered an error or could not be confirmed."""
        return self.confirmation_error is not None or (not self.confirmed and not self.agent_terminated)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_by": self.requested_by,
            "reason": self.reason,
            "requested_at": self.requested_at,
            "agent_terminated": self.agent_terminated,
            "cleanup_completed": self.cleanup_completed,
            "confirmed": self.confirmed,
            "confirmation_error": self.confirmation_error,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammerCancellation:
        return cls(
            requested_by=str(data.get("requested_by", "")),
            reason=str(data.get("reason", "")),
            requested_at=str(data.get("requested_at", utc_now())),
            agent_terminated=bool(data.get("agent_terminated", False)),
            cleanup_completed=bool(data.get("cleanup_completed", False)),
            confirmed=bool(data.get("confirmed", False)),
            confirmation_error=data.get("confirmation_error"),
            metadata=dict(data.get("metadata", {})),
        )

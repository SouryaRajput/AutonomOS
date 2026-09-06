from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.programmer.errors import ProgrammerValidationError


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProgrammerCancellation:
    """
    Provenance record capturing an authorized cancellation of a Programmer execution or work order.
    Records who requested it, when, and the explicit reason/justification.
    """
    requested_by: str
    reason: str
    requested_at: str = field(default_factory=utc_now)
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_by": self.requested_by,
            "reason": self.reason,
            "requested_at": self.requested_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammerCancellation:
        return cls(
            requested_by=str(data.get("requested_by", "")),
            reason=str(data.get("reason", "")),
            requested_at=str(data.get("requested_at", utc_now())),
            metadata=dict(data.get("metadata", {})),
        )

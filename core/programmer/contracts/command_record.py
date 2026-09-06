from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CommandExecutionRecord:
    """
    Structured record of an executed command with execution metadata for auditing.
    Avoids storing massive stdout directly by relying on snippets and output references.
    """
    command: str
    status: str
    exit_code: int
    duration_ms: float
    output_ref: Optional[str] = None
    output_snippet: str = ""
    timestamp: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Returns True if the command exited cleanly with code 0."""
        return self.exit_code == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "output_ref": self.output_ref,
            "output_snippet": self.output_snippet,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandExecutionRecord:
        return cls(
            command=str(data.get("command", "")),
            status=str(data.get("status", "SUCCESS")),
            exit_code=int(data.get("exit_code", 0)),
            duration_ms=float(data.get("duration_ms", 0.0)),
            output_ref=data.get("output_ref"),
            output_snippet=str(data.get("output_snippet", "")),
            timestamp=str(data.get("timestamp", utc_now())),
            metadata=dict(data.get("metadata", {})),
        )

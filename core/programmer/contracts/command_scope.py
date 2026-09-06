from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from core.programmer.errors import InvalidCommandScopeError


@dataclass
class AllowedCommand:
    """
    Explicit authorization contract specifying an allowed shell/runtime command.
    Ensures file modification permissions are strictly separated from command execution.
    """
    command: str
    description: str = ""
    allow_args: bool = True
    allowed_subcommands: list[str] = field(default_factory=list)
    timeout_seconds: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate command specification integrity. Raises InvalidCommandScopeError if malformed."""
        if not self.command or not str(self.command).strip():
            raise InvalidCommandScopeError(
                message="Allowed command must not be empty.",
                command=self.command,
            )

        cmd = str(self.command).strip()
        # Disallow command specs containing shell chain operators or multiple commands
        forbidden_tokens = [";", "&&", "||", "|", "&", "\n", "`", "$("]
        for token in forbidden_tokens:
            if token in cmd:
                raise InvalidCommandScopeError(
                    message=f"Malformed command specification '{cmd}': shell injection or chaining syntax '{token}' is forbidden.",
                    command=cmd,
                )

        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise InvalidCommandScopeError(
                message=f"Command timeout must be positive, got {self.timeout_seconds}s.",
                command=cmd,
                details={"timeout_seconds": self.timeout_seconds},
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "description": self.description,
            "allow_args": self.allow_args,
            "allowed_subcommands": list(self.allowed_subcommands),
            "timeout_seconds": self.timeout_seconds,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AllowedCommand:
        return cls(
            command=str(data.get("command", "")),
            description=str(data.get("description", "")),
            allow_args=bool(data.get("allow_args", True)),
            allowed_subcommands=list(data.get("allowed_subcommands", [])),
            timeout_seconds=data.get("timeout_seconds"),
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def from_str(cls, cmd_str: str, description: str = "") -> AllowedCommand:
        """Convenience factory creating an AllowedCommand from a raw command string."""
        return cls(
            command=str(cmd_str).strip(),
            description=description,
        )

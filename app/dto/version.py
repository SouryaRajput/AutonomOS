"""Version and schema compatibility DTOs."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass
class SystemVersionDTO:
    application_version: str
    api_version: str
    schema_version: int
    min_supported_client_version: str
    is_compatible: bool = True
    compatibility_message: str = "Client and runtime versions are compatible."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SystemVersionDTO:
        return cls(**data)

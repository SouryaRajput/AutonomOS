"""Search DTOs for global project search."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Optional


class SearchResultKind(str, Enum):
    TASK = "TASK"
    ARTIFACT = "ARTIFACT"
    CONVERSATION = "CONVERSATION"
    WORKFLOW = "WORKFLOW"
    REPORT = "REPORT"


@dataclass
class SearchResultDTO:
    id: str
    kind: SearchResultKind
    title: str
    snippet: str
    project_id: str
    target_route: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value if hasattr(self.kind, "value") else str(self.kind)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchResultDTO:
        return cls(
            id=data["id"],
            kind=SearchResultKind(data["kind"]),
            title=data.get("title", ""),
            snippet=data.get("snippet", ""),
            project_id=data.get("project_id", ""),
            target_route=data.get("target_route", ""),
            metadata=data.get("metadata", {}),
        )

"""Storage and project bundle DTOs."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass
class StorageInfoDTO:
    project_id: str
    root_path: str
    database_path: str
    artifact_storage_path: str
    database_size_bytes: int
    artifacts_size_bytes: int
    total_size_bytes: int
    artifact_count: int
    task_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StorageInfoDTO:
        return cls(**data)


@dataclass
class ProjectBundleMetadataDTO:
    version: str
    project_id: str
    project_name: str
    exported_at: str
    task_count: int
    artifact_count: int
    has_secrets: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectBundleMetadataDTO:
        return cls(**data)

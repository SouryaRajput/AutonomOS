from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Optional
import uuid

from core.enums import MemoryType


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def compute_checksum(content: str) -> str:
    """Compute SHA-256 hash of string content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class MemoryDocument:
    """
    Managed persistent memory document in AutonomOS.
    Represents an externalized, human-readable knowledge asset (e.g. project map, architecture, decision).
    """
    id: str
    project_id: str
    memory_type: MemoryType
    title: str
    relative_path: str
    content: str
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)  # Referenced repository file paths or symbols
    related_task_id: Optional[str] = None
    related_worker_id: Optional[str] = None
    version: int = 1
    checksum: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self):
        if not self.checksum:
            self.checksum = compute_checksum(self.content)

    def update_content(self, new_content: str, summary: Optional[str] = None, tags: Optional[list[str]] = None, references: Optional[list[str]] = None) -> None:
        """Update content, increment version, recompute checksum and update timestamp."""
        self.content = new_content
        self.checksum = compute_checksum(new_content)
        if summary is not None:
            self.summary = summary
        if tags is not None:
            self.tags = tags
        if references is not None:
            self.references = references
        self.version += 1
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "memory_type": self.memory_type.value if isinstance(self.memory_type, MemoryType) else self.memory_type,
            "title": self.title,
            "relative_path": self.relative_path,
            "content": self.content,
            "summary": self.summary,
            "tags": self.tags,
            "references": self.references,
            "related_task_id": self.related_task_id,
            "related_worker_id": self.related_worker_id,
            "version": self.version,
            "checksum": self.checksum,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryDocument":
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            memory_type=MemoryType(data["memory_type"]) if isinstance(data["memory_type"], str) else data["memory_type"],
            title=data["title"],
            relative_path=data["relative_path"],
            content=data["content"],
            summary=data.get("summary", ""),
            tags=list(data.get("tags", [])),
            references=list(data.get("references", [])),
            related_task_id=data.get("related_task_id"),
            related_worker_id=data.get("related_worker_id"),
            version=data.get("version", 1),
            checksum=data.get("checksum"),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
        )


@dataclass
class ReferenceIssue:
    """Represents a specific broken or stale reference within a memory document."""
    reference_type: str  # "FILE", "TASK", "WORKER", "ARTIFACT"
    target: str
    description: str


@dataclass
class ValidationReport:
    """Audit report assessing the validity and freshness of a project memory document."""
    memory_id: str
    relative_path: str
    is_valid: bool
    broken_file_references: list[str] = field(default_factory=list)
    broken_task_references: list[str] = field(default_factory=list)
    broken_worker_references: list[str] = field(default_factory=list)
    broken_artifact_references: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_issues_count(self) -> int:
        return (
            len(self.broken_file_references)
            + len(self.broken_task_references)
            + len(self.broken_worker_references)
            + len(self.broken_artifact_references)
        )

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class WorkspaceStatusDTO:
    active_path: str
    is_valid: bool
    is_initialized: bool
    project_name: str
    total_files: int
    last_audited: Optional[str] = None
    tech_stack: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_path": self.active_path,
            "is_valid": self.is_valid,
            "is_initialized": self.is_initialized,
            "project_name": self.project_name,
            "total_files": self.total_files,
            "last_audited": self.last_audited,
            "tech_stack": self.tech_stack,
        }


@dataclass
class SubsystemDTO:
    name: str
    description: str
    file_count: int
    files: List[str]
    total_size: int
    primary_language: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "file_count": self.file_count,
            "files": self.files,
            "total_size": self.total_size,
            "primary_language": self.primary_language,
        }


@dataclass
class FileDetailDTO:
    path: str
    name: str
    category: str
    size: int
    mtime: float
    hash: str
    purpose: str
    symbols: List[str]
    dependencies: List[str]
    dependents: List[str]
    todos: List[str]
    last_audited: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "category": self.category,
            "size": self.size,
            "mtime": self.mtime,
            "hash": self.hash,
            "purpose": self.purpose,
            "symbols": self.symbols,
            "dependencies": self.dependencies,
            "dependents": self.dependents,
            "todos": self.todos,
            "last_audited": self.last_audited,
        }


@dataclass
class AuditHistoryItemDTO:
    audit_id: str
    timestamp: str
    trigger: str
    duration_seconds: float
    files_inspected: int
    files_changed: int
    impacted_files: int
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "timestamp": self.timestamp,
            "trigger": self.trigger,
            "duration_seconds": self.duration_seconds,
            "files_inspected": self.files_inspected,
            "files_changed": self.files_changed,
            "impacted_files": self.impacted_files,
            "summary": self.summary,
        }

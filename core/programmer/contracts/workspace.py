from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Optional, Union
import uuid

from core.programmer.contracts.identifiers import (
    WORKSPACE_ID_PREFIX,
    new_workspace_id,
    validate_execution_id,
    validate_work_order_id,
    validate_workspace_id,
)
from core.programmer.contracts.validator import is_path_in_scope
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    InvalidPathScopeError,
    InvalidWorkspaceError,
    ProgrammerLineageError,
)
from core.programmer.types import WorkspaceIsolationMode


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def normalize_workspace_path(path: str) -> str:
    """
    Deterministically normalize a relative workspace path to POSIX format.
    
    Invariants enforced:
    1. Strip leading and trailing whitespace.
    2. Null bytes are strictly rejected.
    3. Backslashes are converted to forward slashes.
    4. Leading './' or duplicate slashes are stripped.
    5. Path traversal escapes ('..' navigating out of root) are strictly rejected.
    6. Trailing slashes are stripped.
    7. Empty path or '.' normalizes to '.'.
    """
    if not isinstance(path, str):
        raise InvalidPathScopeError(
            f"Workspace path must be a string, got {type(path).__name__}.",
            path=str(path),
        )

    clean = path.strip()
    if "\0" in clean:
        raise InvalidPathScopeError(
            "Null bytes in workspace path are strictly forbidden.",
            path=clean,
        )

    if not clean or clean == ".":
        return "."

    # Standardize to POSIX slashes
    clean = clean.replace("\\", "/")

    # Check for direct path traversal tricks
    # Parse parts via PurePosixPath
    parts: list[str] = []
    # If path starts with '/', strip leading slash to treat as workspace-relative
    clean_relative = clean.lstrip("/")
    
    for segment in clean_relative.split("/"):
        if segment == "" or segment == ".":
            continue
        elif segment == "..":
            if not parts:
                raise InvalidPathScopeError(
                    f"Path traversal escape forbidden: '{clean}' attempts to navigate outside workspace root.",
                    path=clean,
                )
            parts.pop()
        else:
            parts.append(segment)

    if not parts:
        return "."

    return "/".join(parts)


@dataclass
class ProgrammerWorkspace:
    """
    Minimal deterministic domain model representing the workspace envelope
    in which a Programmer execution is authorized to operate.
    
    Architectural boundary rule:
    - Programmer owns execution authority.
    - Cline provides coding implementation capabilities later, but does NOT own workspace authority.
    - The Workspace model specifies WHERE the Programmer is allowed to operate:
      * root_path: physical or virtual root directory
      * allowed_paths: boundaries of readable / inspectable scope
      * writable_paths: strictly authorized file modification targets
      * read_only_paths: immutable reference files
      * forbidden_paths: strictly excluded locations
      * isolation_mode: SHARED or ISOLATED
    """
    workspace_id: str
    project_id: str
    work_order_id: str
    root_path: str
    allowed_paths: list[str] = field(default_factory=list)
    writable_paths: list[str] = field(default_factory=list)
    read_only_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    execution_id: Optional[str] = None
    isolation_mode: WorkspaceIsolationMode = WorkspaceIsolationMode.SHARED
    created_at: str = field(default_factory=utc_now)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validate ID format
        validate_workspace_id(self.workspace_id)
        validate_work_order_id(self.work_order_id)
        if self.execution_id:
            validate_execution_id(self.execution_id)

        # Normalize isolation mode
        if isinstance(self.isolation_mode, str):
            try:
                self.isolation_mode = WorkspaceIsolationMode(self.isolation_mode.upper())
            except (ValueError, TypeError):
                raise InvalidWorkspaceError(
                    f"Invalid workspace isolation mode '{self.isolation_mode}'. Expected 'SHARED' or 'ISOLATED'.",
                    workspace_id=self.workspace_id,
                )

        # Normalize paths upon ingestion
        self.allowed_paths = [normalize_workspace_path(p) for p in self.allowed_paths]
        self.writable_paths = [normalize_workspace_path(p) for p in self.writable_paths]
        self.read_only_paths = [normalize_workspace_path(p) for p in self.read_only_paths]
        self.forbidden_paths = [normalize_workspace_path(p) for p in self.forbidden_paths]

        # Automatic validation on post-init
        self.validate()

    def validate(self) -> None:
        """
        Execute deterministic validation across all 10 workspace invariants:
        1. root_path is valid
        2. workspace belongs to the correct project
        3. workspace belongs to the correct WorkOrder
        4. workspace belongs to the correct execution (if set)
        5. writable paths are within allowed paths
        6. read-only paths are not writable
        7. forbidden paths are not writable
        8. forbidden paths cannot overlap the writable scope
        9. path definitions are normalized consistently
        10. malformed workspace context cannot become executable
        """
        # 1. root_path validation
        if not self.root_path or not isinstance(self.root_path, str) or not self.root_path.strip():
            raise InvalidWorkspaceError(
                "Workspace root_path must be a valid, non-empty path string.",
                workspace_id=self.workspace_id,
            )
        if "\0" in self.root_path:
            raise InvalidWorkspaceError(
                "Workspace root_path cannot contain null bytes.",
                workspace_id=self.workspace_id,
            )
        clean_root = self.root_path.strip()
        if clean_root == "." or clean_root == "..":
            raise InvalidWorkspaceError(
                f"Workspace root_path cannot be a relative navigation token '{clean_root}'.",
                workspace_id=self.workspace_id,
            )

        # 2. project_id validation
        if not self.project_id or not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ProgrammerLineageError(
                f"Workspace '{self.workspace_id}' must belong to a non-empty project_id."
            )

        # 3. work_order_id validation
        if not self.work_order_id or not isinstance(self.work_order_id, str):
            raise ProgrammerLineageError(
                f"Workspace '{self.workspace_id}' must belong to a valid work_order_id."
            )
        validate_work_order_id(self.work_order_id)

        # 4. execution_id validation (if present)
        if self.execution_id:
            validate_execution_id(self.execution_id)

        # 5. Writable paths within allowed paths
        if self.allowed_paths:
            for w in self.writable_paths:
                if not any(is_path_in_scope(w, a) for a in self.allowed_paths):
                    raise InvalidPathScopeError(
                        f"Workspace writable path '{w}' exceeds permitted allowed_paths scope {self.allowed_paths}.",
                        path=w,
                    )

        # 6. Read-only paths cannot be writable
        for w in self.writable_paths:
            for r in self.read_only_paths:
                if is_path_in_scope(w, r) or is_path_in_scope(r, w):
                    raise InvalidPathScopeError(
                        f"Workspace path conflict: path '{w}' is declared writable but overlaps read-only path '{r}'.",
                        path=w,
                    )

        # 7 & 8. Forbidden paths cannot be writable or overlap writable scope
        for w in self.writable_paths:
            for f in self.forbidden_paths:
                if is_path_in_scope(w, f) or is_path_in_scope(f, w):
                    raise InvalidPathScopeError(
                        f"Workspace path conflict: path '{w}' is declared writable but overlaps forbidden path '{f}'.",
                        path=w,
                    )

    def validate_lineage(
        self,
        project_id: Optional[str] = None,
        work_order_id: Optional[str] = None,
        execution_id: Optional[str] = None,
    ) -> None:
        """
        Verify that workspace ownership matches expected runtime parent entities.
        Raises ProgrammerLineageError on mismatch.
        """
        if project_id and self.project_id != project_id:
            raise ProgrammerLineageError(
                f"Workspace project mismatch: belongs to project '{self.project_id}', expected '{project_id}'."
            )
        if work_order_id and self.work_order_id != work_order_id:
            raise ProgrammerLineageError(
                f"Workspace work_order mismatch: belongs to work order '{self.work_order_id}', expected '{work_order_id}'."
            )
        if execution_id and self.execution_id and self.execution_id != execution_id:
            raise ProgrammerLineageError(
                f"Workspace execution mismatch: belongs to execution '{self.execution_id}', expected '{execution_id}'."
            )

    @property
    def workspace_root(self) -> str:
        """Convenience property returning root_path."""
        return self.root_path

    @property
    def is_isolated(self) -> bool:
        """Check whether workspace is isolated from the main repository."""
        return self.isolation_mode == WorkspaceIsolationMode.ISOLATED

    def is_path_allowed(self, path: str) -> bool:
        """Check whether a relative path is within allowed read/inspection scope."""
        norm = normalize_workspace_path(path)
        if not self.allowed_paths:
            return True
        return any(is_path_in_scope(norm, a) for a in self.allowed_paths)

    def is_path_writable(self, path: str) -> bool:
        """Check whether a relative path is authorized for write operations."""
        norm = normalize_workspace_path(path)
        if self.is_path_forbidden(norm):
            return False
        if not self.writable_paths:
            return False
        return any(is_path_in_scope(norm, w) for w in self.writable_paths)

    def is_path_read_only(self, path: str) -> bool:
        """Check whether a relative path is designated as read-only."""
        norm = normalize_workspace_path(path)
        return any(is_path_in_scope(norm, r) for r in self.read_only_paths)

    def is_path_forbidden(self, path: str) -> bool:
        """Check whether a relative path is forbidden/quarantined."""
        norm = normalize_workspace_path(path)
        return any(is_path_in_scope(norm, f) for f in self.forbidden_paths)

    def resolve_path(self, rel_path: str) -> str:
        """
        Resolve a relative workspace path to an absolute path within root_path.
        Guarantees that path traversal cannot escape root_path.
        """
        norm = normalize_workspace_path(rel_path)
        root = os.path.abspath(self.root_path)
        if norm == ".":
            return root
        target = os.path.abspath(os.path.join(root, norm))
        # Confinement check
        if not target.startswith(root + os.sep) and target != root:
            raise InvalidPathScopeError(
                f"Path resolution escaped workspace root: '{rel_path}' resolves to '{target}' outside '{root}'.",
                path=rel_path,
            )
        return target

    @classmethod
    def from_work_order(
        cls,
        work_order: ProgrammerWorkOrder,
        root_path: str,
        execution_id: Optional[str] = None,
        isolation_mode: WorkspaceIsolationMode = WorkspaceIsolationMode.SHARED,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ProgrammerWorkspace:
        """
        Factory to construct and validate a ProgrammerWorkspace directly from an authorized ProgrammerWorkOrder.
        """
        meta = dict(metadata or {})
        meta.setdefault("correlation_id", work_order.correlation_id)

        workspace = cls(
            workspace_id=new_workspace_id(),
            project_id=work_order.project_id,
            work_order_id=work_order.work_order_id,
            execution_id=execution_id,
            root_path=root_path,
            allowed_paths=list(work_order.allowed_paths),
            writable_paths=list(work_order.writable_paths),
            read_only_paths=list(work_order.read_only_paths),
            forbidden_paths=list(work_order.forbidden_paths),
            isolation_mode=isolation_mode,
            trace={
                "created_from_work_order": work_order.work_order_id,
                "correlation_id": work_order.correlation_id,
            },
            metadata=meta,
        )
        return workspace

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "work_order_id": self.work_order_id,
            "execution_id": self.execution_id,
            "root_path": self.root_path,
            "allowed_paths": list(self.allowed_paths),
            "writable_paths": list(self.writable_paths),
            "read_only_paths": list(self.read_only_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "isolation_mode": self.isolation_mode.value if isinstance(self.isolation_mode, WorkspaceIsolationMode) else str(self.isolation_mode),
            "created_at": self.created_at,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammerWorkspace:
        iso_raw = data.get("isolation_mode", WorkspaceIsolationMode.SHARED.value)
        try:
            isolation_mode = WorkspaceIsolationMode(str(iso_raw).upper())
        except (ValueError, TypeError):
            isolation_mode = WorkspaceIsolationMode.SHARED

        return cls(
            workspace_id=data["workspace_id"],
            project_id=data["project_id"],
            work_order_id=data["work_order_id"],
            root_path=data["root_path"],
            allowed_paths=list(data.get("allowed_paths", [])),
            writable_paths=list(data.get("writable_paths", [])),
            read_only_paths=list(data.get("read_only_paths", [])),
            forbidden_paths=list(data.get("forbidden_paths", [])),
            execution_id=data.get("execution_id"),
            isolation_mode=isolation_mode,
            created_at=data.get("created_at", utc_now()),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )


# Canonical alias
Workspace = ProgrammerWorkspace

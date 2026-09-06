from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Optional, Union

from core.programmer.contracts.workspace import ProgrammerWorkspace, normalize_workspace_path
from core.programmer.errors import InvalidPathScopeError
from core.programmer.types import FilesystemOperation, PathBoundaryScope


SYMLINK_SECURITY_LIMITATION: str = (
    "Static boundary resolution checks existing filesystem symlinks. "
    "Runtime TOCTOU symlink swaps or dynamic symlink creations require "
    "kernel-level filesystem sandboxing or execution-time containment."
)


def is_subpath_or_equal(child: str, parent: str) -> bool:
    """
    Check if child path is equal to or a subpath of parent path using POSIX path parts.
    
    Guarantees:
    - Exact component boundary matching.
    - Sibling directories with similar names (e.g. 'src/auth_evil' vs 'src/auth')
      are never falsely matched.
    """
    if parent in (".", "*", ""):
        return True
    c_parts = PurePosixPath(child).parts
    p_parts = PurePosixPath(parent).parts
    if len(c_parts) < len(p_parts):
        return False
    return c_parts[: len(p_parts)] == p_parts


@dataclass
class FilesystemDecision:
    """
    Structured authorization decision returned by FilesystemBoundaryResolver.
    
    Represents the deterministic policy verdict on whether a specific filesystem
    operation is permitted on the requested path(s) within the given workspace.
    """
    allowed: bool
    normalized_path: str
    operation: FilesystemOperation
    scope: PathBoundaryScope
    reason: str
    policy_rule: str
    destination_path: Optional[str] = None
    normalized_destination_path: Optional[str] = None
    destination_scope: Optional[PathBoundaryScope] = None
    symlink_status: str = "RESOLVED_SAFE"
    symlink_limitation: str = SYMLINK_SECURITY_LIMITATION
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.operation, str):
            try:
                self.operation = FilesystemOperation(self.operation.upper())
            except (ValueError, TypeError):
                pass
        if isinstance(self.scope, str):
            try:
                self.scope = PathBoundaryScope(self.scope.upper())
            except (ValueError, TypeError):
                pass
        if self.destination_scope is not None and isinstance(self.destination_scope, str):
            try:
                self.destination_scope = PathBoundaryScope(self.destination_scope.upper())
            except (ValueError, TypeError):
                pass

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "normalized_path": self.normalized_path,
            "operation": (
                self.operation.value
                if isinstance(self.operation, FilesystemOperation)
                else str(self.operation)
            ),
            "scope": (
                self.scope.value
                if isinstance(self.scope, PathBoundaryScope)
                else str(self.scope)
            ),
            "reason": self.reason,
            "policy_rule": self.policy_rule,
            "destination_path": self.destination_path,
            "normalized_destination_path": self.normalized_destination_path,
            "destination_scope": (
                self.destination_scope.value
                if isinstance(self.destination_scope, PathBoundaryScope)
                else (str(self.destination_scope) if self.destination_scope else None)
            ),
            "symlink_status": self.symlink_status,
            "symlink_limitation": self.symlink_limitation,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FilesystemDecision:
        op_raw = data.get("operation", FilesystemOperation.READ.value)
        try:
            operation = FilesystemOperation(str(op_raw).upper())
        except (ValueError, TypeError):
            operation = FilesystemOperation.READ

        scope_raw = data.get("scope", PathBoundaryScope.OUTSIDE_BOUNDARY.value)
        try:
            scope = PathBoundaryScope(str(scope_raw).upper())
        except (ValueError, TypeError):
            scope = PathBoundaryScope.OUTSIDE_BOUNDARY

        dst_scope = None
        if data.get("destination_scope"):
            try:
                dst_scope = PathBoundaryScope(str(data["destination_scope"]).upper())
            except (ValueError, TypeError):
                dst_scope = PathBoundaryScope.OUTSIDE_BOUNDARY

        return cls(
            allowed=bool(data.get("allowed", False)),
            normalized_path=str(data.get("normalized_path", "")),
            operation=operation,
            scope=scope,
            reason=str(data.get("reason", "")),
            policy_rule=str(data.get("policy_rule", "")),
            destination_path=data.get("destination_path"),
            normalized_destination_path=data.get("normalized_destination_path"),
            destination_scope=dst_scope,
            symlink_status=str(data.get("symlink_status", "RESOLVED_SAFE")),
            symlink_limitation=str(data.get("symlink_limitation", SYMLINK_SECURITY_LIMITATION)),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )


class FilesystemBoundaryResolver:
    """
    Deterministic boundary policy resolver for the Programmer subsystem.
    
    Answers the question:
    "Is this path inside the Programmer's authorized filesystem boundary,
     and what operations are permitted?"
     
    Architectural Constraints:
    - This component is STRICT POLICY.
    - It does NOT perform file reads, writes, deletes, renames, or shell execution.
    - Cline does NOT own boundary authority.
    - Evaluates operations against declared Workspace path scopes:
      * READ: permitted in allowed_paths; forbidden always denies; outside boundary denies.
      * WRITE / CREATE: permitted only within writable_paths; read-only, forbidden, outside boundary deny.
      * DELETE: permitted only on explicitly writable paths; forbidden, read-only, outside boundary deny.
      * RENAME: source and destination must independently satisfy write requirements.
    - Defends against path traversal (../), absolute escapes, sibling directory prefix tricks, and null bytes.
    - Explicitly evaluates existing symlink targets and discloses runtime TOCTOU limitations.
    """

    def normalize_path(
        self,
        workspace: ProgrammerWorkspace,
        raw_path: str,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Deterministically sanitize and normalize a requested path relative to workspace.root_path.
        
        Returns:
            (normalized_relative_path, error_reason)
            If normalization fails or attempts boundary escape, normalized_path is None.
        """
        if raw_path is None or not isinstance(raw_path, str):
            return None, f"Path must be a non-empty string, got {type(raw_path).__name__}."

        clean = raw_path.strip()
        if not clean:
            return None, "Path cannot be empty or whitespace only."

        if "\0" in clean:
            return None, "Null bytes in path are strictly forbidden."

        # Handle absolute paths
        if os.path.isabs(clean):
            abs_candidate = os.path.abspath(clean)
            ws_root = os.path.abspath(workspace.root_path)

            if abs_candidate == ws_root:
                return ".", None

            if abs_candidate.startswith(ws_root + os.sep):
                rel = os.path.relpath(abs_candidate, ws_root)
                try:
                    norm = normalize_workspace_path(rel)
                    return norm, None
                except InvalidPathScopeError as err:
                    return None, f"Path normalization failed: {err}"
            else:
                return None, f"Absolute path '{clean}' escapes workspace root '{ws_root}'."

        # Handle relative paths
        try:
            norm = normalize_workspace_path(clean)
            return norm, None
        except InvalidPathScopeError as err:
            return None, f"Path traversal escape detected: {err}"
        except Exception as err:
            return None, f"Malformed path: {err}"

    def check_symlink_safety(
        self,
        workspace: ProgrammerWorkspace,
        norm_path: str,
    ) -> tuple[bool, str, Optional[str]]:
        """
        Check if the path or an existing ancestor resolves outside the workspace root via symlink.
        
        Returns:
            (is_safe, symlink_status, error_message)
        """
        ws_root = os.path.abspath(workspace.root_path)
        real_root = os.path.realpath(ws_root)
        target_abs = os.path.abspath(os.path.join(ws_root, norm_path))

        # Case 1: Target exists on disk
        if os.path.exists(target_abs) or os.path.islink(target_abs):
            try:
                real_target = os.path.realpath(target_abs)
                if real_target != real_root and not real_target.startswith(real_root + os.sep):
                    return (
                        False,
                        "ESCAPES_BOUNDARY",
                        f"Path '{norm_path}' resolves outside workspace root via symlink to '{real_target}'.",
                    )
                return True, "RESOLVED_SAFE", None
            except Exception as e:
                return False, "EVALUATION_ERROR", f"Failed to evaluate symlink safety: {e}"

        # Case 2: Target does not exist yet (e.g. for CREATE / WRITE)
        # Check nearest existing parent directory
        parent = os.path.dirname(target_abs)
        while parent and not os.path.exists(parent) and parent != os.path.dirname(parent):
            parent = os.path.dirname(parent)

        if parent and os.path.exists(parent):
            try:
                real_parent = os.path.realpath(parent)
                if real_parent != real_root and not real_parent.startswith(real_root + os.sep):
                    return (
                        False,
                        "ESCAPES_BOUNDARY",
                        f"Parent directory of '{norm_path}' resolves outside workspace root via symlink to '{real_parent}'.",
                    )
                return True, "RESOLVED_SAFE", None
            except Exception as e:
                return False, "EVALUATION_ERROR", f"Failed to evaluate parent symlink safety: {e}"

        return True, "UNRESOLVED_NONEXISTENT", None

    def classify_scope(
        self,
        workspace: ProgrammerWorkspace,
        norm_path: str,
    ) -> tuple[PathBoundaryScope, str, str]:
        """
        Determine the scope of a normalized relative path against workspace policy rules.
        
        Precedence:
        1. FORBIDDEN: exact or subpath match in forbidden_paths (always highest priority).
        2. READ_ONLY: exact or subpath match in read_only_paths.
        3. WRITABLE: exact or subpath match in writable_paths.
        4. ALLOWED READ: in allowed_paths (permitted to read, not writable).
        5. OUTSIDE_BOUNDARY: not in allowed_paths.
        
        Returns:
            (PathBoundaryScope, policy_rule_name, reason)
        """
        # 1. Check forbidden paths (absolute veto)
        for f in workspace.forbidden_paths:
            if is_subpath_or_equal(norm_path, f):
                return (
                    PathBoundaryScope.FORBIDDEN,
                    "FORBIDDEN_SCOPE",
                    f"Path '{norm_path}' is in forbidden scope '{f}'.",
                )

        # 2. Check explicitly designated read-only paths
        for r in workspace.read_only_paths:
            if is_subpath_or_equal(norm_path, r):
                return (
                    PathBoundaryScope.READ_ONLY,
                    "READ_ONLY_SCOPE",
                    f"Path '{norm_path}' is designated read-only in '{r}'.",
                )

        # 3. Check explicitly designated writable paths
        for w in workspace.writable_paths:
            if is_subpath_or_equal(norm_path, w):
                return (
                    PathBoundaryScope.WRITABLE,
                    "WRITABLE_SCOPE",
                    f"Path '{norm_path}' is in writable scope '{w}'.",
                )

        # 4. Check general allowed paths
        if workspace.allowed_paths:
            in_allowed = any(is_subpath_or_equal(norm_path, a) for a in workspace.allowed_paths)
            if in_allowed:
                return (
                    PathBoundaryScope.READ_ONLY,
                    "ALLOWED_READ_SCOPE",
                    f"Path '{norm_path}' is within allowed read scope, but not declared writable.",
                )
            else:
                return (
                    PathBoundaryScope.OUTSIDE_BOUNDARY,
                    "OUTSIDE_ALLOWED_SCOPE",
                    f"Path '{norm_path}' is outside the authorized allowed_paths scope.",
                )

        # 5. Default when allowed_paths is unspecified
        return (
            PathBoundaryScope.READ_ONLY,
            "WORKSPACE_ROOT_DEFAULT",
            f"Path '{norm_path}' is within workspace root (read-only by default).",
        )

    def resolve(
        self,
        workspace: ProgrammerWorkspace,
        requested_path: str,
        operation: Union[FilesystemOperation, str],
        destination_path: Optional[str] = None,
        check_symlinks: bool = True,
        metadata: Optional[dict[str, Any]] = None,
    ) -> FilesystemDecision:
        """
        Resolve and authorize a requested filesystem operation against the workspace envelope.
        """
        # Parse operation
        op = operation
        if isinstance(op, str):
            try:
                op = FilesystemOperation(op.upper())
            except (ValueError, TypeError):
                return FilesystemDecision(
                    allowed=False,
                    normalized_path=str(requested_path or ""),
                    operation=FilesystemOperation.READ,
                    scope=PathBoundaryScope.OUTSIDE_BOUNDARY,
                    reason=f"Unknown filesystem operation '{operation}'.",
                    policy_rule="INVALID_OPERATION",
                    symlink_status="UNCHECKED",
                    trace={"requested_operation": str(operation)},
                    metadata=dict(metadata or {}),
                )

        # Normalize requested path
        norm_path, norm_err = self.normalize_path(workspace, requested_path)
        if norm_err or norm_path is None:
            return FilesystemDecision(
                allowed=False,
                normalized_path=str(requested_path or ""),
                operation=op,
                scope=PathBoundaryScope.OUTSIDE_BOUNDARY,
                reason=norm_err or "Invalid path.",
                policy_rule="PATH_TRAVERSAL_OR_INVALID",
                symlink_status="UNCHECKED",
                trace={"requested_path": str(requested_path)},
                metadata=dict(metadata or {}),
            )

        # Symlink check
        sym_status = "RESOLVED_SAFE"
        if check_symlinks:
            is_safe, sym_status, sym_err = self.check_symlink_safety(workspace, norm_path)
            if not is_safe:
                return FilesystemDecision(
                    allowed=False,
                    normalized_path=norm_path,
                    operation=op,
                    scope=PathBoundaryScope.OUTSIDE_BOUNDARY,
                    reason=sym_err or "Symlink escapes workspace boundary.",
                    policy_rule="SYMLINK_ESCAPES_WORKSPACE",
                    symlink_status=sym_status,
                    trace={
                        "requested_path": requested_path,
                        "normalized_path": norm_path,
                        "symlink_status": sym_status,
                    },
                    metadata=dict(metadata or {}),
                )

        # Classify primary path scope
        scope, scope_rule, scope_reason = self.classify_scope(workspace, norm_path)

        # Evaluate RENAME operation
        if op == FilesystemOperation.RENAME:
            if destination_path is None or not isinstance(destination_path, str) or not destination_path.strip():
                return FilesystemDecision(
                    allowed=False,
                    normalized_path=norm_path,
                    operation=op,
                    scope=scope,
                    destination_path=destination_path,
                    reason="Rename operation requires a valid non-empty destination_path.",
                    policy_rule="MISSING_RENAME_DESTINATION",
                    symlink_status=sym_status,
                    trace={"requested_path": requested_path, "destination_path": destination_path},
                    metadata=dict(metadata or {}),
                )

            norm_dst, dst_err = self.normalize_path(workspace, destination_path)
            if dst_err or norm_dst is None:
                return FilesystemDecision(
                    allowed=False,
                    normalized_path=norm_path,
                    operation=op,
                    scope=scope,
                    destination_path=destination_path,
                    normalized_destination_path=str(destination_path),
                    destination_scope=PathBoundaryScope.OUTSIDE_BOUNDARY,
                    reason=f"Invalid rename destination: {dst_err}",
                    policy_rule="INVALID_RENAME_DESTINATION",
                    symlink_status=sym_status,
                    trace={"requested_path": requested_path, "destination_path": destination_path},
                    metadata=dict(metadata or {}),
                )

            dst_sym_status = "RESOLVED_SAFE"
            if check_symlinks:
                dst_safe, dst_sym_status, dst_sym_err = self.check_symlink_safety(workspace, norm_dst)
                if not dst_safe:
                    return FilesystemDecision(
                        allowed=False,
                        normalized_path=norm_path,
                        operation=op,
                        scope=scope,
                        destination_path=destination_path,
                        normalized_destination_path=norm_dst,
                        destination_scope=PathBoundaryScope.OUTSIDE_BOUNDARY,
                        reason=dst_sym_err or "Rename destination symlink escapes workspace root.",
                        policy_rule="SYMLINK_ESCAPES_WORKSPACE",
                        symlink_status=dst_sym_status,
                        trace={"destination_path": destination_path, "symlink_status": dst_sym_status},
                        metadata=dict(metadata or {}),
                    )

            dst_scope, dst_rule, dst_reason = self.classify_scope(workspace, norm_dst)

            # Both source and destination must be WRITABLE
            if scope != PathBoundaryScope.WRITABLE:
                return FilesystemDecision(
                    allowed=False,
                    normalized_path=norm_path,
                    operation=op,
                    scope=scope,
                    destination_path=destination_path,
                    normalized_destination_path=norm_dst,
                    destination_scope=dst_scope,
                    reason=f"Rename denied: source path '{norm_path}' is {scope.value} (must be WRITABLE).",
                    policy_rule="RENAME_SOURCE_DENIED",
                    symlink_status=sym_status,
                    trace={
                        "source": {"path": norm_path, "scope": scope.value},
                        "destination": {"path": norm_dst, "scope": dst_scope.value},
                    },
                    metadata=dict(metadata or {}),
                )

            if dst_scope != PathBoundaryScope.WRITABLE:
                return FilesystemDecision(
                    allowed=False,
                    normalized_path=norm_path,
                    operation=op,
                    scope=scope,
                    destination_path=destination_path,
                    normalized_destination_path=norm_dst,
                    destination_scope=dst_scope,
                    reason=f"Rename denied: destination path '{norm_dst}' is {dst_scope.value} (must be WRITABLE).",
                    policy_rule="RENAME_DESTINATION_DENIED",
                    symlink_status=sym_status,
                    trace={
                        "source": {"path": norm_path, "scope": scope.value},
                        "destination": {"path": norm_dst, "scope": dst_scope.value},
                    },
                    metadata=dict(metadata or {}),
                )

            return FilesystemDecision(
                allowed=True,
                normalized_path=norm_path,
                operation=op,
                scope=scope,
                destination_path=destination_path,
                normalized_destination_path=norm_dst,
                destination_scope=dst_scope,
                reason=f"Rename permitted from '{norm_path}' to '{norm_dst}'.",
                policy_rule="RENAME_PERMITTED",
                symlink_status=sym_status,
                trace={
                    "source": {"path": norm_path, "scope": scope.value},
                    "destination": {"path": norm_dst, "scope": dst_scope.value},
                },
                metadata=dict(metadata or {}),
            )

        # Evaluate READ operation
        if op == FilesystemOperation.READ:
            if scope in (PathBoundaryScope.WRITABLE, PathBoundaryScope.READ_ONLY):
                return FilesystemDecision(
                    allowed=True,
                    normalized_path=norm_path,
                    operation=op,
                    scope=scope,
                    reason=f"Read permitted for path '{norm_path}' in {scope.value} scope.",
                    policy_rule="READ_PERMITTED",
                    symlink_status=sym_status,
                    trace={"normalized_path": norm_path, "scope": scope.value},
                    metadata=dict(metadata or {}),
                )
            else:
                return FilesystemDecision(
                    allowed=False,
                    normalized_path=norm_path,
                    operation=op,
                    scope=scope,
                    reason=f"Read denied: path '{norm_path}' is {scope.value}.",
                    policy_rule=f"READ_DENIED_{scope.value}",
                    symlink_status=sym_status,
                    trace={"normalized_path": norm_path, "scope": scope.value},
                    metadata=dict(metadata or {}),
                )

        # Evaluate WRITE / CREATE operations
        if op in (FilesystemOperation.WRITE, FilesystemOperation.CREATE):
            if scope == PathBoundaryScope.WRITABLE:
                return FilesystemDecision(
                    allowed=True,
                    normalized_path=norm_path,
                    operation=op,
                    scope=scope,
                    reason=f"{op.value} permitted on writable path '{norm_path}'.",
                    policy_rule=f"{op.value}_PERMITTED",
                    symlink_status=sym_status,
                    trace={"normalized_path": norm_path, "scope": scope.value},
                    metadata=dict(metadata or {}),
                )
            else:
                return FilesystemDecision(
                    allowed=False,
                    normalized_path=norm_path,
                    operation=op,
                    scope=scope,
                    reason=f"{op.value} denied: path '{norm_path}' is {scope.value} (must be WRITABLE).",
                    policy_rule=f"{op.value}_DENIED_{scope.value}",
                    symlink_status=sym_status,
                    trace={"normalized_path": norm_path, "scope": scope.value},
                    metadata=dict(metadata or {}),
                )

        # Evaluate DELETE operation
        if op == FilesystemOperation.DELETE:
            if scope == PathBoundaryScope.WRITABLE:
                return FilesystemDecision(
                    allowed=True,
                    normalized_path=norm_path,
                    operation=op,
                    scope=scope,
                    reason=f"Delete permitted on writable path '{norm_path}'.",
                    policy_rule="DELETE_PERMITTED",
                    symlink_status=sym_status,
                    trace={"normalized_path": norm_path, "scope": scope.value},
                    metadata=dict(metadata or {}),
                )
            else:
                return FilesystemDecision(
                    allowed=False,
                    normalized_path=norm_path,
                    operation=op,
                    scope=scope,
                    reason=f"Delete denied: path '{norm_path}' is {scope.value} (must be explicitly WRITABLE).",
                    policy_rule=f"DELETE_DENIED_{scope.value}",
                    symlink_status=sym_status,
                    trace={"normalized_path": norm_path, "scope": scope.value},
                    metadata=dict(metadata or {}),
                )

        # Fallback denial
        return FilesystemDecision(
            allowed=False,
            normalized_path=norm_path,
            operation=op,
            scope=scope,
            reason=f"Unsupported operation '{op}' denied.",
            policy_rule="OPERATION_DENIED_BY_DEFAULT",
            symlink_status=sym_status,
            trace={"normalized_path": norm_path, "operation": str(op)},
            metadata=dict(metadata or {}),
        )

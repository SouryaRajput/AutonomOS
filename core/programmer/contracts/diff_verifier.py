from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Optional, Sequence

from core.programmer.contracts.filesystem_resolver import (
    FilesystemBoundaryResolver,
    is_subpath_or_equal,
)
from core.programmer.contracts.identifiers import (
    new_diff_verification_id,
    new_execution_id,
    new_verification_evidence_id,
    validate_diff_verification_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.programmer.contracts.verification import (
    VerificationCheck,
    VerificationEvidence,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.contracts.workspace import (
    ProgrammerWorkspace,
    normalize_workspace_path,
)
from core.programmer.errors import (
    InvalidPathScopeError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    PathBoundaryScope,
    VerificationCheckType,
    VerificationEvidenceSourceType,
    VerificationStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def compute_sha256(content: str | bytes) -> str:
    """Compute SHA-256 hex digest for cryptographic integrity."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


def compute_file_sha256(file_path: str) -> str:
    """Compute SHA-256 hex digest of a file on disk."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


# Default paths and directory patterns to ignore during filesystem walks
DEFAULT_IGNORED_PATTERNS = frozenset({
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    ".DS_Store",
})


@dataclass
class RenamedFile:
    """Represents a detectable file rename from old_path to new_path."""
    old_path: str
    new_path: str

    def __post_init__(self) -> None:
        if isinstance(self.old_path, str):
            try:
                self.old_path = normalize_workspace_path(self.old_path)
            except Exception:
                self.old_path = self.old_path.strip().replace("\\", "/")
        if isinstance(self.new_path, str):
            try:
                self.new_path = normalize_workspace_path(self.new_path)
            except Exception:
                self.new_path = self.new_path.strip().replace("\\", "/")

    def to_dict(self) -> dict[str, str]:
        return {
            "old_path": self.old_path,
            "new_path": self.new_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RenamedFile:
        return cls(
            old_path=str(data.get("old_path", "")),
            new_path=str(data.get("new_path", "")),
        )

    def __str__(self) -> str:
        return f"{self.old_path} -> {self.new_path}"


@dataclass
class UnauthorizedChange:
    """
    Diagnostic record of a repository modification that violated authorized scope.
    """
    path: str
    change_type: str  # "MODIFIED", "CREATED", "DELETED", "RENAMED_SRC", "RENAMED_DST"
    scope: PathBoundaryScope
    reason: str

    def __post_init__(self) -> None:
        if isinstance(self.scope, str):
            try:
                self.scope = PathBoundaryScope(self.scope.upper())
            except (ValueError, TypeError):
                self.scope = PathBoundaryScope.OUTSIDE_BOUNDARY

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "change_type": self.change_type,
            "scope": self.scope.value if isinstance(self.scope, PathBoundaryScope) else str(self.scope),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnauthorizedChange:
        scope_raw = data.get("scope", PathBoundaryScope.OUTSIDE_BOUNDARY.value)
        try:
            scope = PathBoundaryScope(str(scope_raw).upper())
        except (ValueError, TypeError):
            scope = PathBoundaryScope.OUTSIDE_BOUNDARY

        return cls(
            path=str(data.get("path", "")),
            change_type=str(data.get("change_type", "MODIFIED")),
            scope=scope,
            reason=str(data.get("reason", "")),
        )


@dataclass
class ObservedDiff:
    """
    Raw observed repository changes captured through physical inspection.
    """
    files_changed: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    files_deleted: list[str] = field(default_factory=list)
    files_renamed: list[RenamedFile] = field(default_factory=list)
    inspection_method: str = "SNAPSHOT"  # "SNAPSHOT", "GIT", "HYBRID", "NONE"
    is_valid: bool = True
    error_message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_changed": list(self.files_changed),
            "files_created": list(self.files_created),
            "files_deleted": list(self.files_deleted),
            "files_renamed": [r.to_dict() for r in self.files_renamed],
            "inspection_method": self.inspection_method,
            "is_valid": self.is_valid,
            "error_message": self.error_message,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObservedDiff:
        return cls(
            files_changed=list(data.get("files_changed", [])),
            files_created=list(data.get("files_created", [])),
            files_deleted=list(data.get("files_deleted", [])),
            files_renamed=[RenamedFile.from_dict(r) for r in data.get("files_renamed", [])],
            inspection_method=str(data.get("inspection_method", "SNAPSHOT")),
            is_valid=bool(data.get("is_valid", True)),
            error_message=data.get("error_message"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class DiffVerification:
    """
    Structured outcome of verifying observed repository diffs against authorized scope.
    """
    verification_id: str = field(default_factory=new_diff_verification_id)
    execution_id: str = ""
    work_order_id: str = ""
    files_changed: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    files_deleted: list[str] = field(default_factory=list)
    files_renamed: list[RenamedFile] = field(default_factory=list)
    unauthorized_changes: list[UnauthorizedChange] = field(default_factory=list)
    scope_status: VerificationStatus = VerificationStatus.NOT_VERIFIED
    evidence: list[VerificationEvidence] = field(default_factory=list)
    trace: Optional[Any] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.scope_status, str):
            try:
                self.scope_status = VerificationStatus(self.scope_status)
            except ValueError:
                self.scope_status = VerificationStatus.NOT_VERIFIED

        # Rehydrate RenamedFile if passed as raw dicts
        normalized_renamed: list[RenamedFile] = []
        for r in self.files_renamed:
            if isinstance(r, dict):
                normalized_renamed.append(RenamedFile.from_dict(r))
            elif isinstance(r, RenamedFile):
                normalized_renamed.append(r)
        self.files_renamed = normalized_renamed

        # Rehydrate UnauthorizedChange if passed as raw dicts
        normalized_unauth: list[UnauthorizedChange] = []
        for u in self.unauthorized_changes:
            if isinstance(u, dict):
                normalized_unauth.append(UnauthorizedChange.from_dict(u))
            elif isinstance(u, UnauthorizedChange):
                normalized_unauth.append(u)
        self.unauthorized_changes = normalized_unauth

        # Rehydrate VerificationEvidence if passed as raw dicts
        normalized_ev: list[VerificationEvidence] = []
        for e in self.evidence:
            if isinstance(e, dict):
                normalized_ev.append(VerificationEvidence.from_dict(e))
            elif isinstance(e, VerificationEvidence):
                normalized_ev.append(e)
        self.evidence = normalized_ev

    @property
    def is_authorized(self) -> bool:
        """True if scope_status is PASS with zero unauthorized modifications."""
        return self.scope_status == VerificationStatus.PASS and len(self.unauthorized_changes) == 0

    @property
    def has_unauthorized_changes(self) -> bool:
        """True if any unauthorized modification was observed."""
        return len(self.unauthorized_changes) > 0

    @property
    def unauthorized_paths(self) -> list[str]:
        """Unique list of file paths that suffered unauthorized modifications."""
        seen: set[str] = set()
        paths: list[str] = []
        for u in self.unauthorized_changes:
            if u.path not in seen:
                seen.add(u.path)
                paths.append(u.path)
        return paths

    @property
    def total_changes_count(self) -> int:
        """Total count of modified, created, deleted, and renamed files."""
        return (
            len(self.files_changed)
            + len(self.files_created)
            + len(self.files_deleted)
            + len(self.files_renamed)
        )

    def validate(self) -> None:
        """Validate identifier syntax, causal lineage, and status integrity."""
        validate_diff_verification_id(self.verification_id)
        if not self.execution_id:
            raise ProgrammerLineageError("DiffVerification must have a non-empty execution_id.")
        validate_execution_id(self.execution_id)
        if not self.work_order_id:
            raise ProgrammerLineageError("DiffVerification must have a non-empty work_order_id.")
        validate_work_order_id(self.work_order_id)

        # Invariant: PASS status cannot have unauthorized changes
        if self.scope_status == VerificationStatus.PASS:
            if self.unauthorized_changes:
                raise ProgrammerValidationError(
                    f"DiffVerification '{self.verification_id}' marked PASS cannot contain unauthorized changes {self.unauthorized_paths}."
                )
            # Must have at least one authoritative evidence item
            authoritative_ev = [e for e in self.evidence if e.is_authoritative()]
            if not authoritative_ev:
                raise ProgrammerValidationError(
                    f"DiffVerification '{self.verification_id}' marked PASS requires at least one authoritative VerificationEvidence."
                )

        # Invariant: Evidence lineage matching
        for ev in self.evidence:
            ev.validate()
            if ev.execution_id != self.execution_id:
                raise ProgrammerLineageError(
                    f"VerificationEvidence '{ev.evidence_id}' execution_id '{ev.execution_id}' does not match DiffVerification execution_id '{self.execution_id}'."
                )
            if ev.work_order_id != self.work_order_id:
                raise ProgrammerLineageError(
                    f"VerificationEvidence '{ev.evidence_id}' work_order_id '{ev.work_order_id}' does not match DiffVerification work_order_id '{self.work_order_id}'."
                )

    def to_verification_check(self) -> VerificationCheck:
        """Convert this DiffVerification into an authoritative VerificationCheck."""
        output_snippet = (
            f"Scope Status: {self.scope_status.value}. "
            f"Changed: {len(self.files_changed)}, Created: {len(self.files_created)}, "
            f"Deleted: {len(self.files_deleted)}, Renamed: {len(self.files_renamed)}, "
            f"Unauthorized: {len(self.unauthorized_changes)}."
        )
        return VerificationCheck(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            check_type=VerificationCheckType.STATIC_ANALYSIS,
            command="diff_scope_verifier",
            status=self.scope_status,
            exit_code=0 if self.scope_status == VerificationStatus.PASS else 1,
            output_snippet=output_snippet,
            evidence=[e.evidence_id for e in self.evidence],
            trace=self.trace,
            metadata={"verification_id": self.verification_id, **self.metadata},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verification_id": self.verification_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "files_changed": list(self.files_changed),
            "files_created": list(self.files_created),
            "files_deleted": list(self.files_deleted),
            "files_renamed": [r.to_dict() for r in self.files_renamed],
            "unauthorized_changes": [u.to_dict() for u in self.unauthorized_changes],
            "scope_status": self.scope_status.value if isinstance(self.scope_status, VerificationStatus) else str(self.scope_status),
            "evidence": [e.to_dict() for e in self.evidence],
            "trace": self.trace.to_dict() if hasattr(self.trace, "to_dict") else (dict(self.trace) if isinstance(self.trace, dict) else self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiffVerification:
        st_raw = data.get("scope_status", VerificationStatus.NOT_VERIFIED.value)
        try:
            scope_status = VerificationStatus(st_raw)
        except (ValueError, TypeError):
            scope_status = VerificationStatus.NOT_VERIFIED

        return cls(
            verification_id=str(data.get("verification_id", "")),
            execution_id=str(data.get("execution_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            files_changed=list(data.get("files_changed", [])),
            files_created=list(data.get("files_created", [])),
            files_deleted=list(data.get("files_deleted", [])),
            files_renamed=[RenamedFile.from_dict(r) for r in data.get("files_renamed", [])],
            unauthorized_changes=[UnauthorizedChange.from_dict(u) for u in data.get("unauthorized_changes", [])],
            scope_status=scope_status,
            evidence=[VerificationEvidence.from_dict(e) for e in data.get("evidence", [])],
            trace=data.get("trace"),
            metadata=dict(data.get("metadata", {})),
            created_at=str(data.get("created_at", utc_now())),
        )


class WorkspaceDiffInspector:
    """
    Deterministic workspace inspector capturing repository diffs without modifying files.
    
    Supports:
    1. Snapshot-based diffing: baseline file manifest vs current filesystem state.
    2. Git-based diffing: non-modifying git status --porcelain=v1 inspection.
    3. Resilient fallback when git is unavailable or workspace is an isolated filesystem.
    """

    def capture_snapshot(
        self,
        workspace: ProgrammerWorkspace,
        ignored_patterns: Optional[Sequence[str]] = None,
    ) -> dict[str, str]:
        """
        Capture a baseline snapshot of workspace files mapping relative normalized paths to content SHA-256 hashes.
        """
        root = os.path.abspath(workspace.root_path)
        if not os.path.exists(root) or not os.path.isdir(root):
            return {}

        ignores = set(DEFAULT_IGNORED_PATTERNS)
        if ignored_patterns:
            ignores.update(ignored_patterns)

        snapshot: dict[str, str] = {}
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune ignored directories in-place
            dirnames[:] = [d for d in dirnames if d not in ignores and not d.startswith(".")]

            for fname in filenames:
                if fname in ignores or (fname.startswith(".") and fname not in {".gitignore", ".env.example"}):
                    continue

                full_path = os.path.join(dirpath, fname)
                try:
                    rel_path = os.path.relpath(full_path, root)
                    norm_path = normalize_workspace_path(rel_path)
                    digest = compute_file_sha256(full_path)
                    snapshot[norm_path] = digest
                except Exception:
                    # Unreadable file or broken symlink: skip or record sentinel
                    continue

        return snapshot

    def inspect_filesystem_diff(
        self,
        workspace: ProgrammerWorkspace,
        baseline_snapshot: dict[str, str],
        ignored_patterns: Optional[Sequence[str]] = None,
    ) -> ObservedDiff:
        """
        Compare current filesystem state against a baseline snapshot.
        """
        root = os.path.abspath(workspace.root_path)
        if not os.path.exists(root) or not os.path.isdir(root):
            return ObservedDiff(
                is_valid=False,
                error_message=f"Workspace root '{workspace.root_path}' does not exist or is not a directory.",
                inspection_method="SNAPSHOT",
            )

        current_snapshot = self.capture_snapshot(workspace, ignored_patterns=ignored_patterns)

        base_keys = set(baseline_snapshot.keys())
        curr_keys = set(current_snapshot.keys())

        created_candidates = sorted(curr_keys - base_keys)
        deleted_candidates = sorted(base_keys - curr_keys)
        common_keys = sorted(base_keys & curr_keys)

        changed_files: list[str] = []
        for k in common_keys:
            if baseline_snapshot[k] != current_snapshot[k]:
                changed_files.append(k)

        # Detect renames where a deleted file's hash matches a created file's hash
        renamed_files: list[RenamedFile] = []
        matched_created: set[str] = set()
        matched_deleted: set[str] = set()

        # Inverted index of created content hashes for fast matching
        created_by_hash: dict[str, list[str]] = {}
        for c in created_candidates:
            h = current_snapshot[c]
            created_by_hash.setdefault(h, []).append(c)

        for d in deleted_candidates:
            h = baseline_snapshot[d]
            if h in created_by_hash and created_by_hash[h]:
                matched_c = created_by_hash[h].pop(0)
                renamed_files.append(RenamedFile(old_path=d, new_path=matched_c))
                matched_deleted.add(d)
                matched_created.add(matched_c)

        remaining_created = [c for c in created_candidates if c not in matched_created]
        remaining_deleted = [d for d in deleted_candidates if d not in matched_deleted]

        return ObservedDiff(
            files_changed=sorted(changed_files),
            files_created=sorted(remaining_created),
            files_deleted=sorted(remaining_deleted),
            files_renamed=sorted(renamed_files, key=lambda r: (r.old_path, r.new_path)),
            inspection_method="SNAPSHOT",
            is_valid=True,
        )

    def inspect_git_diff(self, workspace: ProgrammerWorkspace) -> ObservedDiff:
        """
        Inspect git repository status using read-only 'git status --porcelain=v1 -uall'.
        """
        root = os.path.abspath(workspace.root_path)
        if not os.path.exists(root) or not os.path.isdir(root):
            return ObservedDiff(
                is_valid=False,
                error_message=f"Workspace root '{workspace.root_path}' does not exist or is not a directory.",
                inspection_method="GIT",
            )

        git_dir = os.path.join(root, ".git")
        if not os.path.exists(git_dir):
            return ObservedDiff(
                is_valid=False,
                error_message=f"Workspace root '{workspace.root_path}' is not a git repository (.git missing).",
                inspection_method="GIT",
            )

        try:
            res = subprocess.run(
                ["git", "status", "--porcelain=v1", "-uall"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except Exception as e:
            return ObservedDiff(
                is_valid=False,
                error_message=f"Failed to execute git status: {e}",
                inspection_method="GIT",
            )

        if res.returncode != 0:
            return ObservedDiff(
                is_valid=False,
                error_message=f"git status returned code {res.returncode}: {res.stderr.strip()}",
                inspection_method="GIT",
            )

        changed_set: set[str] = set()
        created_set: set[str] = set()
        deleted_set: set[str] = set()
        renamed_list: list[RenamedFile] = []

        for line in res.stdout.splitlines():
            if not line or len(line) < 3:
                continue

            status_code = line[:2]
            path_part = line[3:].strip()

            # Strip enclosing quotes if git formatted path
            if path_part.startswith('"') and path_part.endswith('"'):
                path_part = path_part[1:-1]

            # Handle rename 'R  old -> new' or 'RM old -> new'
            if "->" in path_part:
                parts = path_part.split("->")
                old_p = parts[0].strip().strip('"')
                new_p = parts[1].strip().strip('"')
                try:
                    norm_old = normalize_workspace_path(old_p)
                    norm_new = normalize_workspace_path(new_p)
                    renamed_list.append(RenamedFile(norm_old, norm_new))
                except Exception:
                    renamed_list.append(RenamedFile(old_p, new_p))
                continue

            try:
                norm_p = normalize_workspace_path(path_part)
            except Exception:
                norm_p = path_part.replace("\\", "/").strip()

            x, y = status_code[0], status_code[1]

            if status_code == "??" or x == "A":
                created_set.add(norm_p)
            elif x == "D" or y == "D":
                deleted_set.add(norm_p)
            elif x == "M" or y == "M" or x == "T" or y == "T":
                changed_set.add(norm_p)
            elif status_code == "!!":
                continue
            else:
                changed_set.add(norm_p)

        return ObservedDiff(
            files_changed=sorted(changed_set),
            files_created=sorted(created_set),
            files_deleted=sorted(deleted_set),
            files_renamed=sorted(renamed_list, key=lambda r: (r.old_path, r.new_path)),
            inspection_method="GIT",
            is_valid=True,
        )

    def inspect(
        self,
        workspace: ProgrammerWorkspace,
        baseline_snapshot: Optional[dict[str, str]] = None,
    ) -> ObservedDiff:
        """
        Deterministically inspect repository diff using baseline snapshot if provided,
        or git status if available.
        """
        if baseline_snapshot is not None:
            return self.inspect_filesystem_diff(workspace, baseline_snapshot)

        # If baseline is not provided, try git
        root = os.path.abspath(workspace.root_path)
        git_dir = os.path.join(root, ".git")
        if os.path.exists(git_dir):
            git_diff = self.inspect_git_diff(workspace)
            if git_diff.is_valid:
                return git_diff

        # If neither baseline snapshot nor git is usable, state cannot be reliably determined
        return ObservedDiff(
            is_valid=False,
            error_message="Repository state cannot be reliably determined: no baseline snapshot was provided and workspace is not a valid git repository.",
            inspection_method="NONE",
        )


class DiffScopeVerifier:
    """
    Deterministic scope verifier inspecting actual repository changes produced
    during Programmer execution against authorized WorkOrder scope boundaries.
    
    Principles:
    1. Inspects actual repository/workspace state; NEVER relies on agent claims.
    2. Modification is authorized ONLY if within writable_paths.
    3. Read-only and forbidden paths are strictly unauthorized for modification/creation/deletion/rename.
    4. Evaluates renames across boundaries: both source and destination must be writable.
    5. Detects path traversals (../) and attempts to escape workspace root.
    6. Does NOT revert unauthorized changes; reports them as verification failures.
    7. Creates authoritative VerificationEvidence linking observed diff and scope verdict.
    """

    def __init__(
        self,
        resolver: Optional[FilesystemBoundaryResolver] = None,
        inspector: Optional[WorkspaceDiffInspector] = None,
    ) -> None:
        self.resolver = resolver or FilesystemBoundaryResolver()
        self.inspector = inspector or WorkspaceDiffInspector()

    def _classify_change(
        self,
        workspace: ProgrammerWorkspace,
        raw_path: str,
        change_type: str,
    ) -> tuple[str, Optional[UnauthorizedChange]]:
        """
        Normalize path and evaluate whether the change is authorized in workspace.
        Returns (normalized_path, Optional[UnauthorizedChange]).
        """
        # Step 1: Normalize path and detect traversal / null bytes
        norm_path, norm_err = self.resolver.normalize_path(workspace, raw_path)
        if norm_path is None or norm_err:
            return raw_path, UnauthorizedChange(
                path=raw_path,
                change_type=change_type,
                scope=PathBoundaryScope.OUTSIDE_BOUNDARY,
                reason=f"Path normalization / escape violation: {norm_err}",
            )

        # Step 2: Classify scope against workspace policy
        scope, policy_rule, reason = self.resolver.classify_scope(workspace, norm_path)

        # Only WRITABLE is authorized for modification / creation / deletion
        if scope != PathBoundaryScope.WRITABLE:
            return norm_path, UnauthorizedChange(
                path=norm_path,
                change_type=change_type,
                scope=scope,
                reason=f"Operation not authorized in scope '{scope.value}': {reason}",
            )

        return norm_path, None

    def verify_diff(
        self,
        workspace: ProgrammerWorkspace,
        observed_diff: ObservedDiff,
        work_order: Optional[ProgrammerWorkOrder] = None,
        execution_id: Optional[str] = None,
        trace: Optional[Any] = None,
    ) -> DiffVerification:
        """
        Evaluate an ObservedDiff against workspace and work order scope boundaries.
        """
        exec_id = execution_id or workspace.execution_id or new_execution_id()
        wo_id = getattr(work_order, "work_order_id", None) or workspace.work_order_id or ""

        # Handle invalid diff observation (e.g. unreadable workspace or git error)
        if not observed_diff.is_valid:
            status = VerificationStatus.ERROR
            error_msg = observed_diff.error_message or "Observed diff could not be reliably determined."
            ev = VerificationEvidence(
                execution_id=exec_id,
                work_order_id=wo_id,
                source_type=VerificationEvidenceSourceType.FILESYSTEM,
                source_reference=f"diff_verifier:{observed_diff.inspection_method}",
                description=f"Scope verification failed: {error_msg}",
                is_agent_claim=False,
                data={
                    "scope_status": status.value,
                    "error": error_msg,
                    "inspection_method": observed_diff.inspection_method,
                },
            )
            return DiffVerification(
                execution_id=exec_id,
                work_order_id=wo_id,
                scope_status=status,
                evidence=[ev],
                trace=trace,
                metadata={"error": error_msg, "inspection_method": observed_diff.inspection_method},
            )

        unauthorized_changes: list[UnauthorizedChange] = []
        normalized_changed: list[str] = []
        normalized_created: list[str] = []
        normalized_deleted: list[str] = []
        normalized_renamed: list[RenamedFile] = []

        # 1. Evaluate modified files
        for p in observed_diff.files_changed:
            norm_p, unauth = self._classify_change(workspace, p, "MODIFIED")
            normalized_changed.append(norm_p)
            if unauth:
                unauthorized_changes.append(unauth)

        # 2. Evaluate created files
        for p in observed_diff.files_created:
            norm_p, unauth = self._classify_change(workspace, p, "CREATED")
            normalized_created.append(norm_p)
            if unauth:
                unauthorized_changes.append(unauth)

        # 3. Evaluate deleted files
        for p in observed_diff.files_deleted:
            norm_p, unauth = self._classify_change(workspace, p, "DELETED")
            normalized_deleted.append(norm_p)
            if unauth:
                unauthorized_changes.append(unauth)

        # 4. Evaluate renamed files (both old and new must be in writable scope)
        for r in observed_diff.files_renamed:
            norm_old, unauth_old = self._classify_change(workspace, r.old_path, "RENAMED_SRC")
            norm_new, unauth_new = self._classify_change(workspace, r.new_path, "RENAMED_DST")
            normalized_renamed.append(RenamedFile(norm_old, norm_new))

            if unauth_old:
                unauthorized_changes.append(unauth_old)
            if unauth_new:
                unauthorized_changes.append(unauth_new)

        # Determine overall scope status: PASS or FAIL
        if unauthorized_changes:
            scope_status = VerificationStatus.FAIL
        else:
            scope_status = VerificationStatus.PASS

        # Generate authoritative VerificationEvidence
        evidence_data = {
            "scope_status": scope_status.value,
            "inspection_method": observed_diff.inspection_method,
            "files_changed": normalized_changed,
            "files_created": normalized_created,
            "files_deleted": normalized_deleted,
            "files_renamed": [r.to_dict() for r in normalized_renamed],
            "unauthorized_changes": [u.to_dict() for u in unauthorized_changes],
            "writable_paths": list(workspace.writable_paths),
            "read_only_paths": list(workspace.read_only_paths),
            "forbidden_paths": list(workspace.forbidden_paths),
            "allowed_paths": list(workspace.allowed_paths),
        }

        desc = (
            f"Scope verification verdict: {scope_status.value}. "
            f"Observed {len(normalized_changed)} modified, {len(normalized_created)} created, "
            f"{len(normalized_deleted)} deleted, {len(normalized_renamed)} renamed, "
            f"{len(unauthorized_changes)} unauthorized changes."
        )

        ev = VerificationEvidence(
            execution_id=exec_id,
            work_order_id=wo_id,
            source_type=VerificationEvidenceSourceType.FILESYSTEM,
            source_reference=f"diff_verifier:{observed_diff.inspection_method}",
            description=desc,
            is_agent_claim=False,
            data=evidence_data,
        )

        result = DiffVerification(
            execution_id=exec_id,
            work_order_id=wo_id,
            files_changed=sorted(normalized_changed),
            files_created=sorted(normalized_created),
            files_deleted=sorted(normalized_deleted),
            files_renamed=sorted(normalized_renamed, key=lambda r: (r.old_path, r.new_path)),
            unauthorized_changes=unauthorized_changes,
            scope_status=scope_status,
            evidence=[ev],
            trace=trace,
            metadata={
                "inspection_method": observed_diff.inspection_method,
                "total_changes": len(normalized_changed) + len(normalized_created) + len(normalized_deleted) + len(normalized_renamed),
                "unauthorized_count": len(unauthorized_changes),
            },
        )
        result.validate()
        return result

    def verify_workspace(
        self,
        workspace: ProgrammerWorkspace,
        baseline_snapshot: Optional[dict[str, str]] = None,
        work_order: Optional[ProgrammerWorkOrder] = None,
        execution_id: Optional[str] = None,
        trace: Optional[Any] = None,
    ) -> DiffVerification:
        """
        Inspect workspace and verify repository changes against authorized scope.
        """
        root = os.path.abspath(workspace.root_path)
        if not os.path.exists(root) or not os.path.isdir(root):
            exec_id = execution_id or workspace.execution_id or new_execution_id()
            wo_id = getattr(work_order, "work_order_id", None) or workspace.work_order_id or ""
            status = VerificationStatus.ERROR
            err_msg = f"Workspace root '{workspace.root_path}' does not exist or is inaccessible."
            ev = VerificationEvidence(
                execution_id=exec_id,
                work_order_id=wo_id,
                source_type=VerificationEvidenceSourceType.FILESYSTEM,
                source_reference="diff_verifier:workspace_check",
                description=err_msg,
                is_agent_claim=False,
                data={"error": err_msg, "scope_status": status.value},
            )
            return DiffVerification(
                execution_id=exec_id,
                work_order_id=wo_id,
                scope_status=status,
                evidence=[ev],
                trace=trace,
                metadata={"error": err_msg},
            )

        observed = self.inspector.inspect(workspace, baseline_snapshot=baseline_snapshot)
        return self.verify_diff(
            workspace=workspace,
            observed_diff=observed,
            work_order=work_order,
            execution_id=execution_id,
            trace=trace,
        )

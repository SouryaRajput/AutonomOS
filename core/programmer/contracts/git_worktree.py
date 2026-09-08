from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from typing import Any, Optional, Protocol, runtime_checkable

from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.git_model import (
    GitExecutionContext,
    GitRepository,
    GitRevision,
    validate_commit_hash,
)
from core.programmer.contracts.identifiers import (
    GIT_WORKTREE_ID_PREFIX,
    new_git_revision_id,
    new_git_worktree_id,
    validate_execution_id,
    validate_git_repository_id,
    validate_git_worktree_id,
    validate_work_order_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    GitWorktreeError,
    InvalidProgrammerTransitionError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    GitIsolationMode,
    GitRepositoryState,
    GitWorktreeErrorCode,
    GitWorktreeStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# =============================================================================
# 1. Git Operations Interface & Fake Implementation
# =============================================================================

@runtime_checkable
class GitOperationsProtocol(Protocol):
    """
    Protocol for underlying Git interactions required by GitWorktreeProvisioner.
    Enables pluggable implementations (CLI, LibGit2, or in-memory fakes for unit tests).
    """
    def is_git_repository(self, repo_path: str) -> bool:
        ...

    def resolve_revision(self, repo_path: str, reference: Optional[str] = None) -> GitRevision:
        ...

    def create_worktree(self, repo_path: str, worktree_path: str, branch: str, base_commit: str) -> None:
        ...

    def remove_worktree(self, repo_path: str, worktree_path: str, force: bool = True) -> None:
        ...

    def delete_branch(self, repo_path: str, branch: str, force: bool = True) -> None:
        ...

    def is_worktree_active(self, worktree_path: str) -> bool:
        ...

    def supports_worktrees(self, repo_path: str) -> bool:
        ...

    def inspect_status(self, repo_or_worktree_path: str) -> dict[str, Any]:
        ...

    def commit(
        self,
        repo_or_worktree_path: str,
        message: str,
        author: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> GitRevision:
        ...

    def get_commit_history(
        self,
        repo_or_worktree_path: str,
        base_commit: Optional[str] = None,
    ) -> list[GitRevision]:
        ...


class FakeGitOperations:
    """
    Deterministic, in-memory fake Git backend for unit testing GitWorktreeProvisioner.
    Simulates repositories, branches, commits, and worktree operations with fault injection.
    """
    def __init__(self) -> None:
        self.repositories: dict[str, dict[str, Any]] = {}
        self.branches: dict[str, dict[str, str]] = {}  # repo_path -> {branch: commit}
        self.worktrees: dict[str, dict[str, Any]] = {}  # worktree_path -> info
        self.commits: dict[str, list[GitRevision]] = {}  # repo_path -> list[GitRevision]
        self.working_tree_files: dict[str, dict[str, Any]] = {}  # path -> {files_changed: [], files_created: [], files_deleted: []}
        self.commits_by_path: dict[str, list[GitRevision]] = {}  # path -> list of created GitRevision

        # Fault injection hooks
        self.fail_is_repo: bool = False
        self.fail_resolve_revision: bool = False
        self.fail_create_worktree: bool = False
        self.fail_remove_worktree: bool = False
        self.fail_commit: bool = False
        self.fail_status: bool = False
        self.worktrees_supported: bool = True
        self.custom_error_message: Optional[str] = None

    def add_repository(
        self,
        repo_path: str,
        default_branch: str = "main",
        initial_commit_sha: Optional[str] = None,
    ) -> GitRevision:
        """Register a repository in the fake environment with an initial revision."""
        norm_path = os.path.abspath(repo_path)
        sha = initial_commit_sha or "0123456789abcdef0123456789abcdef01234567"
        rev = GitRevision(
            revision_id=new_git_revision_id(),
            commit_hash=sha,
            branch=default_branch,
        )
        self.repositories[norm_path] = {
            "default_branch": default_branch,
            "current_revision": rev,
        }
        self.branches.setdefault(norm_path, {})[default_branch] = sha
        self.commits.setdefault(norm_path, []).append(rev)
        return rev

    def is_git_repository(self, repo_path: str) -> bool:
        if self.fail_is_repo:
            return False
        return os.path.abspath(repo_path) in self.repositories

    def supports_worktrees(self, repo_path: str) -> bool:
        return self.worktrees_supported

    def resolve_revision(self, repo_path: str, reference: Optional[str] = None) -> GitRevision:
        if self.fail_resolve_revision:
            raise GitWorktreeError(
                self.custom_error_message or f"Failed resolving revision for '{reference}'.",
                error_code=GitWorktreeErrorCode.BASE_REVISION_UNRESOLVED,
            )
        norm_path = os.path.abspath(repo_path)
        actual_repo_path = norm_path
        wt_info = None

        if norm_path in self.worktrees:
            wt_info = self.worktrees[norm_path]
            actual_repo_path = wt_info["repo_path"]

        if actual_repo_path not in self.repositories:
            raise GitWorktreeError(
                f"Path '{repo_path}' is not a registered Git repository or worktree.",
                error_code=GitWorktreeErrorCode.REPOSITORY_INVALID,
            )

        repo_info = self.repositories[actual_repo_path]
        target_ref = reference or (wt_info["branch"] if wt_info else repo_info["default_branch"])

        # Check if reference is a direct commit hash matching base_commit
        if wt_info and (target_ref == wt_info.get("base_commit") or target_ref == "BASE"):
            return GitRevision(
                revision_id=new_git_revision_id(),
                commit_hash=wt_info["base_commit"],
                branch=wt_info["branch"],
            )

        # Check worktree local commits
        wt_commits = self.commits_by_path.get(norm_path, [])
        if target_ref in ("HEAD", None) and wt_commits:
            return wt_commits[-1]

        for c in wt_commits:
            if c.commit_hash == target_ref:
                return c

        # Check if reference is in branches
        branches = self.branches.get(actual_repo_path, {})
        if target_ref in branches:
            sha = branches[target_ref]
            return GitRevision(
                revision_id=new_git_revision_id(),
                commit_hash=sha,
                branch=target_ref,
            )

        # Check repo commits
        repo_commits = self.commits.get(actual_repo_path, [])
        for c in repo_commits:
            if c.commit_hash == target_ref:
                return c

        if wt_info and wt_info.get("base_commit"):
            return GitRevision(
                revision_id=new_git_revision_id(),
                commit_hash=wt_info["base_commit"],
                branch=wt_info["branch"],
            )

        # Fallback to current revision
        return repo_info["current_revision"]

    def create_worktree(self, repo_path: str, worktree_path: str, branch: str, base_commit: str) -> None:
        if not self.worktrees_supported:
            raise GitWorktreeError(
                "Git worktree isolation is unsupported by this environment.",
                error_code=GitWorktreeErrorCode.ISOLATION_UNAVAILABLE,
            )
        if self.fail_create_worktree:
            raise GitWorktreeError(
                self.custom_error_message or f"Failed to create worktree at '{worktree_path}'.",
                error_code=GitWorktreeErrorCode.PROVISIONING_FAILED,
            )
        norm_repo = os.path.abspath(repo_path)
        norm_wt = os.path.abspath(worktree_path)

        if norm_wt in self.worktrees:
            raise GitWorktreeError(
                f"Worktree path '{worktree_path}' already exists.",
                error_code=GitWorktreeErrorCode.WORKTREE_EXISTS,
            )

        # Check branch collision
        repo_branches = self.branches.setdefault(norm_repo, {})
        if branch in repo_branches:
            raise GitWorktreeError(
                f"Branch '{branch}' already exists in repository '{repo_path}'.",
                error_code=GitWorktreeErrorCode.BRANCH_COLLISION,
            )

        repo_branches[branch] = base_commit
        self.worktrees[norm_wt] = {
            "repo_path": norm_repo,
            "branch": branch,
            "base_commit": base_commit,
            "created_at": utc_now(),
        }

    def remove_worktree(self, repo_path: str, worktree_path: str, force: bool = True) -> None:
        if self.fail_remove_worktree:
            raise GitWorktreeError(
                self.custom_error_message or f"Failed to remove worktree at '{worktree_path}'.",
                error_code=GitWorktreeErrorCode.CLEANUP_FAILED,
            )
        norm_wt = os.path.abspath(worktree_path)
        if norm_wt in self.worktrees:
            del self.worktrees[norm_wt]

    def delete_branch(self, repo_path: str, branch: str, force: bool = True) -> None:
        norm_repo = os.path.abspath(repo_path)
        repo_branches = self.branches.get(norm_repo, {})
        if branch in repo_branches:
            del repo_branches[branch]

    def is_worktree_active(self, worktree_path: str) -> bool:
        return os.path.abspath(worktree_path) in self.worktrees

    def stage_change(
        self,
        repo_or_worktree_path: str,
        file_path: str,
        change_type: str = "MODIFIED",
    ) -> None:
        """Helper to simulate working tree modifications for testing."""
        norm_path = os.path.abspath(repo_or_worktree_path)
        wt_state = self.working_tree_files.setdefault(
            norm_path,
            {"files_changed": [], "files_created": [], "files_deleted": [], "files_renamed": [], "diff_summary": {}},
        )
        ct = change_type.upper()
        if ct == "CREATED":
            if file_path not in wt_state["files_created"]:
                wt_state["files_created"].append(file_path)
        elif ct == "DELETED":
            if file_path not in wt_state["files_deleted"]:
                wt_state["files_deleted"].append(file_path)
        else:
            if file_path not in wt_state["files_changed"]:
                wt_state["files_changed"].append(file_path)

        total_files = (
            len(wt_state["files_changed"])
            + len(wt_state["files_created"])
            + len(wt_state["files_deleted"])
            + len(wt_state.get("files_renamed", []))
        )
        wt_state["diff_summary"] = {
            "total_files": total_files,
            "changed_count": len(wt_state["files_changed"]),
            "created_count": len(wt_state["files_created"]),
            "deleted_count": len(wt_state["files_deleted"]),
            "renamed_count": len(wt_state.get("files_renamed", [])),
        }

    def stage_rename(
        self,
        repo_or_worktree_path: str,
        old_path: str,
        new_path: str,
    ) -> None:
        """Helper to simulate file renames for testing."""
        norm_path = os.path.abspath(repo_or_worktree_path)
        wt_state = self.working_tree_files.setdefault(
            norm_path,
            {"files_changed": [], "files_created": [], "files_deleted": [], "files_renamed": [], "diff_summary": {}},
        )
        renamed_list = wt_state.setdefault("files_renamed", [])
        renamed_list.append({"old_path": old_path, "new_path": new_path})

        total_files = (
            len(wt_state["files_changed"])
            + len(wt_state["files_created"])
            + len(wt_state["files_deleted"])
            + len(renamed_list)
        )
        wt_state["diff_summary"] = {
            "total_files": total_files,
            "changed_count": len(wt_state["files_changed"]),
            "created_count": len(wt_state["files_created"]),
            "deleted_count": len(wt_state["files_deleted"]),
            "renamed_count": len(renamed_list),
        }

    def inspect_status(self, repo_or_worktree_path: str) -> dict[str, Any]:
        """Inspect working tree status for the specified path."""
        if self.fail_status:
            raise GitWorktreeError(
                self.custom_error_message or f"Failed to inspect Git status for '{repo_or_worktree_path}'.",
                error_code=GitWorktreeErrorCode.UNKNOWN,
            )
        norm_path = os.path.abspath(repo_or_worktree_path)
        if norm_path not in self.repositories and norm_path not in self.worktrees:
            raise GitWorktreeError(
                f"Path '{repo_or_worktree_path}' is neither a registered Git repository nor a worktree.",
                error_code=GitWorktreeErrorCode.REPOSITORY_INVALID,
            )
        data = self.working_tree_files.get(
            norm_path,
            {"files_changed": [], "files_created": [], "files_deleted": [], "files_renamed": [], "diff_summary": {"total_files": 0}},
        )
        return {
            "files_changed": list(data.get("files_changed", [])),
            "files_created": list(data.get("files_created", [])),
            "files_deleted": list(data.get("files_deleted", [])),
            "files_renamed": list(data.get("files_renamed", [])),
            "diff_summary": dict(data.get("diff_summary", {})),
        }

    def commit(
        self,
        repo_or_worktree_path: str,
        message: str,
        author: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> GitRevision:
        """Create a local Git commit in the specified repository or worktree."""
        if self.fail_commit:
            raise GitWorktreeError(
                self.custom_error_message or f"Failed to commit in '{repo_or_worktree_path}'.",
                error_code=GitWorktreeErrorCode.PROVISIONING_FAILED,
            )
        norm_path = os.path.abspath(repo_or_worktree_path)
        if norm_path not in self.repositories and norm_path not in self.worktrees:
            raise GitWorktreeError(
                f"Path '{repo_or_worktree_path}' is neither a registered Git repository nor a worktree.",
                error_code=GitWorktreeErrorCode.REPOSITORY_INVALID,
            )

        branch = "main"
        if norm_path in self.worktrees:
            branch = self.worktrees[norm_path].get("branch", "main")
        elif norm_path in self.repositories:
            branch = self.repositories[norm_path].get("default_branch", "main")

        current_wt = self.working_tree_files.get(norm_path, {})
        committed_files = {
            "files_changed": list(current_wt.get("files_changed", [])),
            "files_created": list(current_wt.get("files_created", [])),
            "files_deleted": list(current_wt.get("files_deleted", [])),
            "files_renamed": list(current_wt.get("files_renamed", [])),
            "diff_summary": dict(current_wt.get("diff_summary", {})),
        }
        meta = dict(metadata or {})
        if "committed_files" not in meta:
            meta["committed_files"] = committed_files

        import uuid
        sha = uuid.uuid4().hex
        rev = GitRevision(
            revision_id=new_git_revision_id(),
            commit_hash=sha,
            branch=branch,
            trace={"message": message, "author": author or "programmer"},
            metadata=meta,
        )

        self.commits_by_path.setdefault(norm_path, []).append(rev)

        # Clear working tree changes since they are now committed
        self.working_tree_files[norm_path] = {
            "files_changed": [],
            "files_created": [],
            "files_deleted": [],
            "files_renamed": [],
            "diff_summary": {"total_files": 0},
        }
        return rev

    def get_commit_history(
        self,
        repo_or_worktree_path: str,
        base_commit: Optional[str] = None,
    ) -> list[GitRevision]:
        """Return commits created in this repository/worktree since base_commit."""
        norm_path = os.path.abspath(repo_or_worktree_path)
        commits = list(self.commits_by_path.get(norm_path, []))
        if base_commit:
            # Filter commits after base_commit if found
            return [c for c in commits if c.commit_hash != base_commit]
        return commits


# =============================================================================
# 2. GitWorktree Domain Model
# =============================================================================

@dataclass
class GitWorktree:
    """
    Deterministic domain model representing an isolated Git worktree dedicated to a specific ProgrammerExecution.
    Tracks worktree lifecycle:
        PROVISIONING -> READY -> ACTIVE -> CLEANUP_PENDING -> CLEANED
        (Any state -> FAILED)
    """
    worktree_id: str
    repository_id: str
    execution_id: str
    work_order_id: str
    project_id: str
    worktree_path: str
    branch: str
    base_revision: GitRevision
    status: GitWorktreeStatus = GitWorktreeStatus.PROVISIONING
    cleanup_reason: Optional[str] = None
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    cleaned_at: Optional[str] = None

    ALLOWED_TRANSITIONS: dict[GitWorktreeStatus, set[GitWorktreeStatus]] = field(
        default_factory=lambda: {
            GitWorktreeStatus.PROVISIONING: {GitWorktreeStatus.READY, GitWorktreeStatus.FAILED},
            GitWorktreeStatus.READY: {GitWorktreeStatus.ACTIVE, GitWorktreeStatus.CLEANUP_PENDING, GitWorktreeStatus.FAILED},
            GitWorktreeStatus.ACTIVE: {GitWorktreeStatus.CLEANUP_PENDING, GitWorktreeStatus.FAILED},
            GitWorktreeStatus.CLEANUP_PENDING: {GitWorktreeStatus.CLEANED, GitWorktreeStatus.FAILED},
            GitWorktreeStatus.CLEANED: set(),
            GitWorktreeStatus.FAILED: {GitWorktreeStatus.CLEANUP_PENDING, GitWorktreeStatus.CLEANED},
        },
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = GitWorktreeStatus(self.status.upper())
            except (ValueError, TypeError):
                self.status = GitWorktreeStatus.FAILED

        if isinstance(self.base_revision, dict):
            self.base_revision = GitRevision.from_dict(self.base_revision)

        self.validate()

    def validate(self) -> None:
        validate_git_worktree_id(self.worktree_id)
        validate_git_repository_id(self.repository_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)

        if not self.project_id or not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ProgrammerValidationError("GitWorktree requires a non-empty project_id.", field_name="project_id")

        if not self.worktree_path or not isinstance(self.worktree_path, str) or not self.worktree_path.strip():
            raise ProgrammerValidationError("GitWorktree requires a non-empty worktree_path.", field_name="worktree_path")

        if not self.branch or not isinstance(self.branch, str) or not self.branch.strip():
            raise ProgrammerValidationError("GitWorktree requires a non-empty branch.", field_name="branch")

        if self.base_revision is None or not isinstance(self.base_revision, GitRevision):
            raise ProgrammerValidationError("GitWorktree requires a valid base_revision GitRevision.", field_name="base_revision")
        self.base_revision.validate()

    def transition_to(self, target_status: GitWorktreeStatus, reason: str = "") -> None:
        """
        Transition worktree lifecycle to target_status verifying allowed state transitions.
        Raises InvalidProgrammerTransitionError on invalid transition.
        """
        if isinstance(target_status, str):
            target_status = GitWorktreeStatus(target_status.upper())

        allowed = self.ALLOWED_TRANSITIONS.get(self.status, set())
        if target_status not in allowed:
            raise InvalidProgrammerTransitionError(
                entity_id=self.worktree_id,
                current_status=self.status.value,
                target_status=target_status.value,
                reason=reason or f"Transition from {self.status.value} to {target_status.value} is not permitted.",
            )

        self.status = target_status
        if reason:
            self.cleanup_reason = reason
        if target_status == GitWorktreeStatus.CLEANED:
            self.cleaned_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "worktree_id": self.worktree_id,
            "repository_id": self.repository_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "project_id": self.project_id,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "base_revision": self.base_revision.to_dict(),
            "status": self.status.value,
            "cleanup_reason": self.cleanup_reason,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "cleaned_at": self.cleaned_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GitWorktree:
        return cls(
            worktree_id=data["worktree_id"],
            repository_id=data["repository_id"],
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            project_id=data["project_id"],
            worktree_path=data["worktree_path"],
            branch=data["branch"],
            base_revision=GitRevision.from_dict(data["base_revision"]),
            status=GitWorktreeStatus(data.get("status", GitWorktreeStatus.PROVISIONING.value)),
            cleanup_reason=data.get("cleanup_reason"),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
            cleaned_at=data.get("cleaned_at"),
        )


# =============================================================================
# 3. Worktree Provisioning Result
# =============================================================================

@dataclass
class GitWorktreeProvisioningResult:
    """
    Structured outcome of a Git worktree provisioning attempt.
    Guarantees that a READY result always contains an active worktree and GitExecutionContext.
    """
    status: GitWorktreeStatus
    worktree: Optional[GitWorktree] = None
    execution_context: Optional[GitExecutionContext] = None
    error_code: Optional[GitWorktreeErrorCode] = None
    error_message: Optional[str] = None
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = GitWorktreeStatus(self.status.upper())
            except (ValueError, TypeError):
                self.status = GitWorktreeStatus.FAILED

        if isinstance(self.error_code, str):
            try:
                self.error_code = GitWorktreeErrorCode(self.error_code.upper())
            except (ValueError, TypeError):
                self.error_code = GitWorktreeErrorCode.UNKNOWN

        # Invariant enforcement: a failed provision cannot produce an active ready context
        if self.status == GitWorktreeStatus.FAILED:
            if self.is_ready():
                raise GitWorktreeError(
                    "A failed provisioning result cannot produce an active READY execution context.",
                    error_code=GitWorktreeErrorCode.PROVISIONING_FAILED,
                )

        # Invariant enforcement: READY status must have both worktree and context
        if self.status == GitWorktreeStatus.READY:
            if self.worktree is None or self.execution_context is None:
                raise GitWorktreeError(
                    "A READY provisioning result must contain both worktree and GitExecutionContext.",
                    error_code=GitWorktreeErrorCode.PROVISIONING_FAILED,
                )

    def is_ready(self) -> bool:
        """Check whether the isolated worktree is fully provisioned and ready for execution."""
        return (
            self.status == GitWorktreeStatus.READY
            and self.worktree is not None
            and self.worktree.status == GitWorktreeStatus.READY
            and self.execution_context is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "worktree": self.worktree.to_dict() if self.worktree else None,
            "execution_context": self.execution_context.to_dict() if self.execution_context else None,
            "error_code": self.error_code.value if self.error_code else None,
            "error_message": self.error_message,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GitWorktreeProvisioningResult:
        err_code = None
        if data.get("error_code"):
            try:
                err_code = GitWorktreeErrorCode(str(data["error_code"]).upper())
            except (ValueError, TypeError):
                err_code = GitWorktreeErrorCode.UNKNOWN

        wt = GitWorktree.from_dict(data["worktree"]) if data.get("worktree") else None
        ctx = GitExecutionContext.from_dict(data["execution_context"]) if data.get("execution_context") else None

        return cls(
            status=GitWorktreeStatus(data.get("status", GitWorktreeStatus.FAILED.value)),
            worktree=wt,
            execution_context=ctx,
            error_code=err_code,
            error_message=data.get("error_message"),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )


# =============================================================================
# 4. GitWorktreeProvisioner
# =============================================================================

class GitWorktreeProvisioner:
    """
    Deterministic provisioner for isolated Git workspaces using Git worktrees.

    Architecture Lineage:
        Repository -> Base Revision -> GitWorktreeProvisioner -> Execution Worktree -> Programmer Execution -> Cline

    Core Invariants:
    1. Validate repository integrity and project ownership prior to worktree creation.
    2. Authoritative base revision determination.
    3. Strict isolation mode: never silently degrades from ISOLATED to SHARED mode.
    4. Deterministic, unique worktree path and branch naming; prevents active worktree collisions.
    5. Worktree is marked READY strictly after successful creation and context validation.
    6. Safe cleanup policies protecting repository root and unrelated workspaces.
    """

    def __init__(
        self,
        git_ops: Optional[GitOperationsProtocol] = None,
        worktree_base_dir: Optional[str] = None,
        supported_modes: Optional[list[GitIsolationMode]] = None,
        strict_isolation: bool = True,
    ) -> None:
        self.git_ops = git_ops or FakeGitOperations()
        self.worktree_base_dir = worktree_base_dir
        self.supported_modes = supported_modes or [GitIsolationMode.WORKTREE]
        self.strict_isolation = strict_isolation

        # State tracking
        self._active_worktrees_by_execution: dict[str, GitWorktree] = {}
        self._worktrees_by_id: dict[str, GitWorktree] = {}
        self._worktrees_by_path: dict[str, GitWorktree] = {}

    def provision_isolated_workspace(
        self,
        repository: GitRepository,
        work_order: ProgrammerWorkOrder,
        execution: ProgrammerExecution,
        base_revision: Optional[GitRevision] = None,
        branch_override: Optional[str] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> GitWorktreeProvisioningResult:
        """
        Provision an isolated execution worktree for a Programmer execution.
        """
        tr = dict(trace or {})

        # Step 1: Validate repository contracts
        try:
            repository.validate()
        except Exception as e:
            return GitWorktreeProvisioningResult(
                status=GitWorktreeStatus.FAILED,
                error_code=GitWorktreeErrorCode.REPOSITORY_INVALID,
                error_message=f"Repository validation failed: {e}",
                trace=tr,
            )

        if not self.git_ops.is_git_repository(repository.root_path):
            return GitWorktreeProvisioningResult(
                status=GitWorktreeStatus.FAILED,
                error_code=GitWorktreeErrorCode.REPOSITORY_INVALID,
                error_message=f"Root path '{repository.root_path}' is not a valid Git repository.",
                trace=tr,
            )

        # Cross-project ownership check
        if repository.project_id != work_order.project_id:
            return GitWorktreeProvisioningResult(
                status=GitWorktreeStatus.FAILED,
                error_code=GitWorktreeErrorCode.REPOSITORY_INVALID,
                error_message=(
                    f"Cross-project forbidden: repository project '{repository.project_id}' "
                    f"does not match work order project '{work_order.project_id}'."
                ),
                trace=tr,
            )

        if repository.project_id != execution.project_id:
            return GitWorktreeProvisioningResult(
                status=GitWorktreeStatus.FAILED,
                error_code=GitWorktreeErrorCode.REPOSITORY_INVALID,
                error_message=(
                    f"Cross-project forbidden: repository project '{repository.project_id}' "
                    f"does not match execution project '{execution.project_id}'."
                ),
                trace=tr,
            )

        if execution.work_order_id != work_order.work_order_id:
            return GitWorktreeProvisioningResult(
                status=GitWorktreeStatus.FAILED,
                error_code=GitWorktreeErrorCode.REPOSITORY_INVALID,
                error_message=(
                    f"Lineage mismatch: execution work_order_id '{execution.work_order_id}' "
                    f"does not match work order '{work_order.work_order_id}'."
                ),
                trace=tr,
            )

        # Step 2: Check isolation availability (never fallback silently to SHARED)
        if (
            GitIsolationMode.WORKTREE not in self.supported_modes
            or not self.git_ops.supports_worktrees(repository.root_path)
        ):
            return GitWorktreeProvisioningResult(
                status=GitWorktreeStatus.FAILED,
                error_code=GitWorktreeErrorCode.ISOLATION_UNAVAILABLE,
                error_message="Git worktree isolation is unavailable in this environment. Silent fallback to SHARED is forbidden.",
                trace=tr,
            )

        # Step 3: Determine base revision
        resolved_base: Optional[GitRevision] = None
        if base_revision is not None:
            try:
                base_revision.validate()
                resolved_base = base_revision
            except Exception as e:
                return GitWorktreeProvisioningResult(
                    status=GitWorktreeStatus.FAILED,
                    error_code=GitWorktreeErrorCode.BASE_REVISION_UNRESOLVED,
                    error_message=f"Provided base_revision is invalid: {e}",
                    trace=tr,
                )
        else:
            try:
                resolved_base = self.git_ops.resolve_revision(
                    repository.root_path,
                    reference=repository.default_branch,
                )
                resolved_base.validate()
            except Exception as e:
                return GitWorktreeProvisioningResult(
                    status=GitWorktreeStatus.FAILED,
                    error_code=GitWorktreeErrorCode.BASE_REVISION_UNRESOLVED,
                    error_message=f"Failed to resolve base revision from repository: {e}",
                    trace=tr,
                )

        # Step 4: Check uniqueness / prevent collision
        if execution.execution_id in self._active_worktrees_by_execution:
            return GitWorktreeProvisioningResult(
                status=GitWorktreeStatus.FAILED,
                error_code=GitWorktreeErrorCode.WORKTREE_EXISTS,
                error_message=f"Execution '{execution.execution_id}' already has an active worktree.",
                trace=tr,
            )

        worktree_id = new_git_worktree_id()
        branch = branch_override or f"autonomos/{work_order.work_order_id}/{execution.execution_id}"
        
        base_dir = self.worktree_base_dir or os.path.join(repository.root_path, ".autonomos", "worktrees")
        worktree_path = os.path.abspath(os.path.join(base_dir, f"{repository.repository_id}_{execution.execution_id}"))

        if worktree_path in self._worktrees_by_path:
            existing_wt = self._worktrees_by_path[worktree_path]
            if existing_wt.status in {GitWorktreeStatus.PROVISIONING, GitWorktreeStatus.READY, GitWorktreeStatus.ACTIVE}:
                return GitWorktreeProvisioningResult(
                    status=GitWorktreeStatus.FAILED,
                    error_code=GitWorktreeErrorCode.WORKTREE_EXISTS,
                    error_message=f"Worktree path '{worktree_path}' is currently occupied by active execution '{existing_wt.execution_id}'.",
                    trace=tr,
                )

        # Step 5: Construct worktree record in PROVISIONING status
        worktree = GitWorktree(
            worktree_id=worktree_id,
            repository_id=repository.repository_id,
            execution_id=execution.execution_id,
            work_order_id=work_order.work_order_id,
            project_id=repository.project_id,
            worktree_path=worktree_path,
            branch=branch,
            base_revision=resolved_base,
            status=GitWorktreeStatus.PROVISIONING,
            trace=tr,
        )
        self._worktrees_by_id[worktree_id] = worktree
        self._worktrees_by_path[worktree_path] = worktree

        # Step 6: Create execution worktree via git_ops
        try:
            self.git_ops.create_worktree(
                repo_path=repository.root_path,
                worktree_path=worktree_path,
                branch=branch,
                base_commit=resolved_base.commit_hash,
            )
        except Exception as e:
            worktree.transition_to(GitWorktreeStatus.FAILED, reason=str(e))
            return GitWorktreeProvisioningResult(
                status=GitWorktreeStatus.FAILED,
                worktree=None,
                execution_context=None,
                error_code=GitWorktreeErrorCode.PROVISIONING_FAILED,
                error_message=f"Underlying worktree creation failed: {e}",
                trace=tr,
            )

        # Step 7: Produce and cross-validate GitExecutionContext
        try:
            git_context = GitExecutionContext(
                repository_id=repository.repository_id,
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
                project_id=repository.project_id,
                base_revision=resolved_base,
                workspace_path=worktree_path,
                isolation_mode=GitIsolationMode.WORKTREE,
                branch=branch,
                reference=f"refs/heads/{branch}",
                trace=tr,
            )
            git_context.validate(
                repository=repository,
                execution=execution,
                work_order=work_order,
            )
        except Exception as e:
            worktree.transition_to(GitWorktreeStatus.FAILED, reason=f"Context validation failed: {e}")
            # Roll back created worktree
            self._safe_remove_worktree_dir(repository.root_path, worktree_path)
            return GitWorktreeProvisioningResult(
                status=GitWorktreeStatus.FAILED,
                worktree=None,
                execution_context=None,
                error_code=GitWorktreeErrorCode.PROVISIONING_FAILED,
                error_message=f"GitExecutionContext validation failed: {e}",
                trace=tr,
            )

        # Step 8: Mark workspace READY
        worktree.transition_to(GitWorktreeStatus.READY)
        self._active_worktrees_by_execution[execution.execution_id] = worktree

        return GitWorktreeProvisioningResult(
            status=GitWorktreeStatus.READY,
            worktree=worktree,
            execution_context=git_context,
            trace=tr,
        )

    def mark_active(self, execution_id: str) -> None:
        """Mark a READY worktree as ACTIVE when execution begins."""
        worktree = self._active_worktrees_by_execution.get(execution_id)
        if not worktree:
            raise GitWorktreeError(
                f"No active worktree found for execution '{execution_id}'.",
                error_code=GitWorktreeErrorCode.REPOSITORY_INVALID,
                execution_id=execution_id,
            )
        worktree.transition_to(GitWorktreeStatus.ACTIVE)

    def cleanup_worktree(
        self,
        execution_id: str,
        repository: Optional[GitRepository] = None,
        reason: str = "execution_completed",
        delete_branch: bool = False,
    ) -> bool:
        """
        Clean up an isolated worktree for a completed, failed, or cancelled execution.
        Safely prunes the worktree directory without modifying the main repository or unrelated worktrees.
        Idempotent: repeated calls succeed without error.
        """
        worktree = self._active_worktrees_by_execution.get(execution_id)
        if worktree is None:
            # Check if it was already registered and cleaned
            for wt in self._worktrees_by_id.values():
                if wt.execution_id == execution_id and wt.status == GitWorktreeStatus.CLEANED:
                    return True
            return True

        if worktree.status == GitWorktreeStatus.CLEANED:
            return True

        # Transition to CLEANUP_PENDING
        try:
            worktree.transition_to(GitWorktreeStatus.CLEANUP_PENDING, reason=reason)
        except InvalidProgrammerTransitionError:
            pass  # If already FAILED, it can still transition to CLEANED below

        # Safety invariant: Never delete repository root
        repo_root = repository.root_path if repository else None
        if repo_root and os.path.abspath(worktree.worktree_path) == os.path.abspath(repo_root):
            raise GitWorktreeError(
                f"Catastrophic safety violation: attempted to clean up repository root '{repo_root}'.",
                error_code=GitWorktreeErrorCode.CLEANUP_FAILED,
                worktree_id=worktree.worktree_id,
                execution_id=execution_id,
            )

        # Call underlying git operations to remove worktree
        repo_path_to_use = repo_root or os.path.dirname(worktree.worktree_path)
        self._safe_remove_worktree_dir(repo_path_to_use, worktree.worktree_path)

        if delete_branch:
            try:
                self.git_ops.delete_branch(repo_path_to_use, worktree.branch, force=True)
            except Exception:
                pass

        # Transition to CLEANED
        worktree.transition_to(GitWorktreeStatus.CLEANED, reason=reason)

        # Remove from active tracking
        self._active_worktrees_by_execution.pop(execution_id, None)
        return True

    def _safe_remove_worktree_dir(self, repo_path: str, worktree_path: str) -> None:
        """Safely invoke git_ops.remove_worktree."""
        try:
            self.git_ops.remove_worktree(repo_path, worktree_path, force=True)
        except Exception as e:
            # Record warning / continue cleanup
            pass

    def get_worktree(self, execution_id: str) -> Optional[GitWorktree]:
        """Get the GitWorktree for a given execution_id."""
        return self._active_worktrees_by_execution.get(execution_id)

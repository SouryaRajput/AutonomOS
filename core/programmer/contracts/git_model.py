from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Optional

from core.programmer.contracts.identifiers import (
    GIT_REPOSITORY_ID_PREFIX,
    GIT_REVISION_ID_PREFIX,
    validate_execution_id,
    validate_git_repository_id,
    validate_git_revision_id,
    validate_work_order_id,
)
from core.programmer.errors import (
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    GitIsolationMode,
    GitRepositoryState,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


_COMMIT_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")


def validate_commit_hash(commit_hash: str) -> None:
    """
    Validate that a commit hash is a valid 7 to 40 character hexadecimal string.
    Raises ProgrammerValidationError if invalid.
    """
    if not isinstance(commit_hash, str) or not commit_hash.strip():
        raise ProgrammerValidationError("Commit hash must be a non-empty string.", field_name="commit_hash")
    clean = commit_hash.strip()
    if not _COMMIT_HASH_PATTERN.match(clean):
        raise ProgrammerValidationError(
            f"Invalid commit hash '{commit_hash}'. Expected a 7-40 character hexadecimal SHA.",
            field_name="commit_hash",
        )


@dataclass
class GitRevision:
    """
    Deterministic domain model representing a concrete Git revision (commit).
    Preserves commit hash, associated branch (if any), timestamp, and provenance trace.
    """
    revision_id: str
    commit_hash: str
    branch: Optional[str] = None
    timestamp: str = field(default_factory=utc_now)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.revision_id:
            raise ProgrammerValidationError("GitRevision requires a non-empty revision_id.", field_name="revision_id")
        validate_git_revision_id(self.revision_id)

        validate_commit_hash(self.commit_hash)

        if not self.timestamp:
            raise ProgrammerValidationError("GitRevision requires a non-empty timestamp.", field_name="timestamp")

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "commit_hash": self.commit_hash,
            "branch": self.branch,
            "timestamp": self.timestamp,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GitRevision:
        return cls(
            revision_id=data["revision_id"],
            commit_hash=data["commit_hash"],
            branch=data.get("branch"),
            timestamp=data.get("timestamp", utc_now()),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class GitRepository:
    """
    Deterministic domain model representing an authorized Git repository boundary for a project.
    Binds the repository identity to a project, local root path, default branch, and remote reference.
    """
    repository_id: str
    project_id: str
    root_path: str
    default_branch: str = "main"
    remote_reference: Optional[str] = None
    repository_state: GitRepositoryState = GitRepositoryState.CLEAN
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.repository_state, str):
            try:
                self.repository_state = GitRepositoryState(self.repository_state.upper())
            except (ValueError, TypeError):
                self.repository_state = GitRepositoryState.UNKNOWN
        self.validate()

    def validate(self) -> None:
        if not self.repository_id:
            raise ProgrammerValidationError("GitRepository requires a non-empty repository_id.", field_name="repository_id")
        validate_git_repository_id(self.repository_id)

        if not self.project_id or not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ProgrammerValidationError("GitRepository requires a non-empty project_id.", field_name="project_id")

        if not self.root_path or not isinstance(self.root_path, str) or not self.root_path.strip():
            raise ProgrammerValidationError("GitRepository requires a non-empty root_path.", field_name="root_path")

        if not self.default_branch or not isinstance(self.default_branch, str) or not self.default_branch.strip():
            raise ProgrammerValidationError("GitRepository requires a non-empty default_branch.", field_name="default_branch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository_id": self.repository_id,
            "project_id": self.project_id,
            "root_path": self.root_path,
            "default_branch": self.default_branch,
            "remote_reference": self.remote_reference,
            "repository_state": self.repository_state.value if isinstance(self.repository_state, GitRepositoryState) else str(self.repository_state),
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GitRepository:
        st_raw = data.get("repository_state", GitRepositoryState.CLEAN.value)
        try:
            st = GitRepositoryState(st_raw.upper()) if isinstance(st_raw, str) else st_raw
        except (ValueError, TypeError):
            st = GitRepositoryState.UNKNOWN

        return cls(
            repository_id=data["repository_id"],
            project_id=data["project_id"],
            root_path=data["root_path"],
            default_branch=data.get("default_branch", "main"),
            remote_reference=data.get("remote_reference"),
            repository_state=st,
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )


@dataclass
class GitExecutionContext:
    """
    Deterministic domain model binding an active Programmer execution to a Git repository,
    workspace directory, isolation mode, and revision lineage.

    Architectural Lineage:
        ManagerTask -> ProgrammerWorkOrder -> ProgrammerExecution -> GitExecutionContext

    Invariants:
    1. Distinguishes repository identity, execution workspace path, base revision, and resulting revision.
    2. Strictly forbids cross-project references: cannot attach to another project's repository.
    3. Strictly forbids cross-execution references: cannot attach to another execution attempt.
    4. Enforces authoritative base revision existence prior to execution.
    """
    repository_id: str
    execution_id: str
    work_order_id: str
    base_revision: GitRevision
    workspace_path: str
    isolation_mode: GitIsolationMode = GitIsolationMode.BRANCH
    branch: Optional[str] = None
    reference: Optional[str] = None
    resulting_revision: Optional[GitRevision] = None
    project_id: str = ""
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.isolation_mode, str):
            try:
                self.isolation_mode = GitIsolationMode(self.isolation_mode.upper())
            except (ValueError, TypeError):
                self.isolation_mode = GitIsolationMode.BRANCH

        if isinstance(self.base_revision, dict):
            self.base_revision = GitRevision.from_dict(self.base_revision)
        if isinstance(self.resulting_revision, dict):
            self.resulting_revision = GitRevision.from_dict(self.resulting_revision)

        self.validate()

    def validate(
        self,
        repository: Optional[GitRepository] = None,
        execution: Optional[Any] = None,
        work_order: Optional[Any] = None,
    ) -> None:
        """
        Validate internal consistency and optionally cross-verify lineage against
        provided GitRepository, ProgrammerExecution, or ProgrammerWorkOrder instances.
        """
        if not self.repository_id:
            raise ProgrammerLineageError("GitExecutionContext requires a non-empty repository_id.")
        validate_git_repository_id(self.repository_id)

        if not self.execution_id:
            raise ProgrammerLineageError("GitExecutionContext requires a non-empty execution_id.")
        validate_execution_id(self.execution_id)

        if not self.work_order_id:
            raise ProgrammerLineageError("GitExecutionContext requires a non-empty work_order_id.")
        validate_work_order_id(self.work_order_id)

        if not self.workspace_path or not isinstance(self.workspace_path, str) or not self.workspace_path.strip():
            raise ProgrammerValidationError("GitExecutionContext requires a non-empty workspace_path.", field_name="workspace_path")

        if self.base_revision is None:
            raise ProgrammerValidationError("GitExecutionContext requires an authoritative base_revision.", field_name="base_revision")
        if not isinstance(self.base_revision, GitRevision):
            raise ProgrammerValidationError("base_revision must be a GitRevision instance.", field_name="base_revision")
        self.base_revision.validate()

        if self.resulting_revision is not None:
            if not isinstance(self.resulting_revision, GitRevision):
                raise ProgrammerValidationError("resulting_revision must be a GitRevision instance.", field_name="resulting_revision")
            self.resulting_revision.validate()

        # Repository cross-project and ID validation
        if repository is not None:
            if repository.repository_id != self.repository_id:
                raise ProgrammerLineageError(
                    f"Lineage mismatch: GitExecutionContext repository_id '{self.repository_id}' "
                    f"does not match repository '{repository.repository_id}'."
                )
            if self.project_id and repository.project_id != self.project_id:
                raise ProgrammerLineageError(
                    f"Cross-project forbidden: GitExecutionContext project_id '{self.project_id}' "
                    f"does not match repository project_id '{repository.project_id}'."
                )

        # Execution lineage and project validation
        if execution is not None:
            exec_id = getattr(execution, "execution_id", None)
            if exec_id and exec_id != self.execution_id:
                raise ProgrammerLineageError(
                    f"Lineage mismatch: GitExecutionContext execution_id '{self.execution_id}' "
                    f"does not match execution '{exec_id}'."
                )
            exec_wo = getattr(execution, "work_order_id", None)
            if exec_wo and exec_wo != self.work_order_id:
                raise ProgrammerLineageError(
                    f"Lineage mismatch: GitExecutionContext work_order_id '{self.work_order_id}' "
                    f"does not match execution work_order_id '{exec_wo}'."
                )
            exec_proj = getattr(execution, "project_id", None)
            if self.project_id and exec_proj and exec_proj != self.project_id:
                raise ProgrammerLineageError(
                    f"Cross-project forbidden: GitExecutionContext project_id '{self.project_id}' "
                    f"does not match execution project_id '{exec_proj}'."
                )

        # WorkOrder lineage and project validation
        if work_order is not None:
            wo_id = getattr(work_order, "work_order_id", None)
            if wo_id and wo_id != self.work_order_id:
                raise ProgrammerLineageError(
                    f"Lineage mismatch: GitExecutionContext work_order_id '{self.work_order_id}' "
                    f"does not match work_order '{wo_id}'."
                )
            wo_proj = getattr(work_order, "project_id", None)
            if self.project_id and wo_proj and wo_proj != self.project_id:
                raise ProgrammerLineageError(
                    f"Cross-project forbidden: GitExecutionContext project_id '{self.project_id}' "
                    f"does not match work order project_id '{wo_proj}'."
                )

        # Pairwise cross-validation between provided entities
        if repository is not None and execution is not None:
            exec_proj = getattr(execution, "project_id", None)
            if repository.project_id and exec_proj and repository.project_id != exec_proj:
                raise ProgrammerLineageError(
                    f"Cross-project forbidden: repository project_id '{repository.project_id}' "
                    f"does not match execution project_id '{exec_proj}'."
                )
        if repository is not None and work_order is not None:
            wo_proj = getattr(work_order, "project_id", None)
            if repository.project_id and wo_proj and repository.project_id != wo_proj:
                raise ProgrammerLineageError(
                    f"Cross-project forbidden: repository project_id '{repository.project_id}' "
                    f"does not match work order project_id '{wo_proj}'."
                )
        if execution is not None and work_order is not None:
            exec_wo = getattr(execution, "work_order_id", None)
            wo_id = getattr(work_order, "work_order_id", None)
            if exec_wo and wo_id and exec_wo != wo_id:
                raise ProgrammerLineageError(
                    f"Lineage mismatch: execution work_order_id '{exec_wo}' does not match work order '{wo_id}'."
                )
            exec_proj = getattr(execution, "project_id", None)
            wo_proj = getattr(work_order, "project_id", None)
            if exec_proj and wo_proj and exec_proj != wo_proj:
                raise ProgrammerLineageError(
                    f"Cross-project forbidden: execution project_id '{exec_proj}' does not match work order '{wo_proj}'."
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository_id": self.repository_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "project_id": self.project_id,
            "base_revision": self.base_revision.to_dict(),
            "resulting_revision": self.resulting_revision.to_dict() if self.resulting_revision else None,
            "workspace_path": self.workspace_path,
            "isolation_mode": self.isolation_mode.value if isinstance(self.isolation_mode, GitIsolationMode) else str(self.isolation_mode),
            "branch": self.branch,
            "reference": self.reference,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GitExecutionContext:
        mode_raw = data.get("isolation_mode", GitIsolationMode.BRANCH.value)
        try:
            mode = GitIsolationMode(mode_raw.upper()) if isinstance(mode_raw, str) else mode_raw
        except (ValueError, TypeError):
            mode = GitIsolationMode.BRANCH

        base_rev_data = data["base_revision"]
        base_rev = GitRevision.from_dict(base_rev_data) if isinstance(base_rev_data, dict) else base_rev_data

        res_rev_data = data.get("resulting_revision")
        res_rev = GitRevision.from_dict(res_rev_data) if isinstance(res_rev_data, dict) else res_rev_data

        return cls(
            repository_id=data["repository_id"],
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            project_id=data.get("project_id", ""),
            base_revision=base_rev,
            resulting_revision=res_rev,
            workspace_path=data["workspace_path"],
            isolation_mode=mode,
            branch=data.get("branch"),
            reference=data.get("reference"),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )

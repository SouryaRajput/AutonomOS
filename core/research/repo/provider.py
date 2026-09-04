"""
Repository Provider Abstraction (Phase 1 / Part 5 / Step 2).

Decouples repository inspection, tree navigation, and file retrieval from concrete hosting
backends (GitHub, GitLab, local filesystem, in-memory fixtures).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging
from typing import Any, Callable, Optional

from core.research.errors import (
    RepositoryCancelledError,
    RepositoryError,
    RepositoryProviderError,
    RepositoryResourceLimitError,
    RepositoryValidationError,
)
from core.research.repo.models import (
    LineRange,
    RepositoryIdentity,
    RepositoryProviderType,
    RepositoryRevision,
    RepositorySourceMaterial,
    RepositoryTree,
)

logger = logging.getLogger("AutonomOS.Research.RepositoryProvider")


@dataclass(frozen=True)
class RepositoryFetchLimits:
    """
    Configurable resource and bounded execution limits for repository operations.
    """
    max_file_bytes: int = 1_000_000         # 1 MB per file content
    max_tree_depth: int = 10                # Maximum directory hierarchy depth
    max_tree_files: int = 1000              # Maximum file count returned in a single tree query
    timeout_seconds: float = 30.0           # Default per-operation timeout in seconds

    def __post_init__(self):
        if self.max_file_bytes <= 0:
            raise RepositoryValidationError("max_file_bytes", "max_file_bytes must be > 0.")
        if self.max_tree_depth <= 0:
            raise RepositoryValidationError("max_tree_depth", "max_tree_depth must be > 0.")
        if self.max_tree_files <= 0:
            raise RepositoryValidationError("max_tree_files", "max_tree_files must be > 0.")
        if self.timeout_seconds <= 0:
            raise RepositoryValidationError("timeout_seconds", "timeout_seconds must be > 0.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_file_bytes": self.max_file_bytes,
            "max_tree_depth": self.max_tree_depth,
            "max_tree_files": self.max_tree_files,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryFetchLimits:
        return cls(
            max_file_bytes=int(data.get("max_file_bytes", 1_000_000)),
            max_tree_depth=int(data.get("max_tree_depth", 10)),
            max_tree_files=int(data.get("max_tree_files", 1000)),
            timeout_seconds=float(data.get("timeout_seconds", 30.0)),
        )


@dataclass(frozen=True)
class RepositoryTreeParams:
    """
    Explicit request parameters for retrieving a bounded repository directory tree.
    """
    repo: RepositoryIdentity | str
    revision: Optional[str] = None
    subpath: Optional[str] = None
    max_depth: Optional[int] = None
    max_files: Optional[int] = None
    include_file_metadata: bool = True
    timeout_seconds: Optional[float] = None

    def __post_init__(self):
        if not self.repo:
            raise RepositoryValidationError("repo", "Repository identity or target string is required.")
        if isinstance(self.repo, str) and not self.repo.strip():
            raise RepositoryValidationError("repo", "Repository target string cannot be empty.")
        if self.max_depth is not None and self.max_depth <= 0:
            raise RepositoryValidationError("max_depth", "max_depth must be > 0.")
        if self.max_files is not None and self.max_files <= 0:
            raise RepositoryValidationError("max_files", "max_files must be > 0.")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise RepositoryValidationError("timeout_seconds", "timeout_seconds must be > 0.")


@dataclass(frozen=True)
class RepositoryFileParams:
    """
    Explicit request parameters for retrieving file content from a repository.
    """
    repo: RepositoryIdentity | str
    file_path: str
    revision: Optional[str] = None
    line_range: Optional[LineRange] = None
    max_bytes: Optional[int] = None
    timeout_seconds: Optional[float] = None

    def __post_init__(self):
        if not self.repo:
            raise RepositoryValidationError("repo", "Repository identity or target string is required.")
        if isinstance(self.repo, str) and not self.repo.strip():
            raise RepositoryValidationError("repo", "Repository target string cannot be empty.")
        if not self.file_path or not isinstance(self.file_path, str) or not self.file_path.strip():
            raise RepositoryValidationError("file_path", "File path cannot be empty.")
        if self.max_bytes is not None and self.max_bytes <= 0:
            raise RepositoryValidationError("max_bytes", "max_bytes must be > 0.")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise RepositoryValidationError("timeout_seconds", "timeout_seconds must be > 0.")


class RepositoryProvider(ABC):
    """
    Abstract Base Class for Repository Data Access Providers.

    Encapsulates backend communication, revision resolution, tree structure queries,
    and file content retrieval without coupling to specific Git services or execution models.
    """

    def __init__(
        self,
        provider_id: str,
        provider_type: RepositoryProviderType,
        name: str,
        limits: Optional[RepositoryFetchLimits] = None,
    ):
        if not provider_id or not provider_id.strip():
            raise RepositoryValidationError("provider_id", "Provider identifier cannot be empty.")
        if not name or not name.strip():
            raise RepositoryValidationError("name", "Provider name cannot be empty.")

        self._provider_id = provider_id.strip()
        self._provider_type = provider_type
        self._name = name.strip()
        self._limits = limits or RepositoryFetchLimits()

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def provider_type(self) -> RepositoryProviderType:
        return self._provider_type

    @property
    def name(self) -> str:
        return self._name

    @property
    def limits(self) -> RepositoryFetchLimits:
        return self._limits

    def check_cancellation(
        self,
        is_cancelled: Optional[Callable[[], bool]],
        repo_target: str,
        operation: str,
    ) -> None:
        """
        Check cancellation predicate and raise RepositoryCancelledError if triggered.
        """
        if is_cancelled is not None and is_cancelled():
            logger.info(f"Repository operation '{operation}' for '{repo_target}' cancelled by caller.")
            raise RepositoryCancelledError(repo_target=repo_target, operation=operation)

    def extract_repo_target(self, repo: RepositoryIdentity | str) -> str:
        """
        Helper extracting repository identifier or URL string from repo parameter.
        """
        if isinstance(repo, RepositoryIdentity):
            return repo.url or repo.repo_id
        elif isinstance(repo, str) and repo.strip():
            return repo.strip()
        raise RepositoryValidationError("repo", "Must provide a valid RepositoryIdentity or non-empty string.")

    @abstractmethod
    def resolve_identity(
        self,
        target: str,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> RepositoryIdentity:
        """
        Resolve a repository URL, shorthand, or identifier into a canonical RepositoryIdentity.

        Raises:
            RepositoryNotFoundError: If the target repository cannot be found or resolved.
            RepositoryTimeoutError: If the operation times out.
            RepositoryCancelledError: If cancelled by caller.
            RepositoryProviderError: On general provider failure.
        """
        pass

    @abstractmethod
    def get_metadata(
        self,
        repo: RepositoryIdentity | str,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> RepositoryIdentity:
        """
        Obtain detailed repository metadata (description, default branch, primary language, etc.).

        Raises:
            RepositoryNotFoundError: If the repository does not exist.
            RepositoryTimeoutError: If the operation times out.
            RepositoryCancelledError: If cancelled by caller.
            RepositoryProviderError: On general provider failure.
        """
        pass

    @abstractmethod
    def get_revision(
        self,
        repo: RepositoryIdentity | str,
        revision: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> RepositoryRevision:
        """
        Obtain repository revision context (commit SHA, branch, tag, commit author, date).
        If revision is omitted, resolves against the default branch / latest HEAD.

        Raises:
            RepositoryNotFoundError: If the repository does not exist.
            RepositoryRevisionNotFoundError: If the requested revision/branch/tag does not exist.
            RepositoryTimeoutError: If the operation times out.
            RepositoryCancelledError: If cancelled by caller.
            RepositoryProviderError: On general provider failure.
        """
        pass

    @abstractmethod
    def get_tree(
        self,
        repo: RepositoryIdentity | str,
        revision: Optional[str] = None,
        subpath: Optional[str] = None,
        max_depth: Optional[int] = None,
        max_files: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> RepositoryTree:
        """
        Obtain bounded repository directory tree structure for a given revision and optional subpath.

        Raises:
            RepositoryNotFoundError: If the repository does not exist.
            RepositoryRevisionNotFoundError: If the requested revision does not exist.
            RepositoryResourceLimitError: If bounds are exceeded.
            RepositoryTimeoutError: If the operation times out.
            RepositoryCancelledError: If cancelled by caller.
            RepositoryProviderError: On general provider failure.
        """
        pass

    @abstractmethod
    def get_file_content(
        self,
        repo: RepositoryIdentity | str,
        file_path: str,
        revision: Optional[str] = None,
        line_range: Optional[LineRange] = None,
        max_bytes: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> RepositorySourceMaterial:
        """
        Retrieve file content by path and revision, optionally slicing to a LineRange and bounded by max_bytes.

        Raises:
            RepositoryNotFoundError: If the repository does not exist.
            RepositoryRevisionNotFoundError: If the requested revision does not exist.
            RepositoryFileNotFoundError: If the file path does not exist at that revision.
            RepositoryResourceLimitError: If the requested file size exceeds max_bytes.
            RepositoryTimeoutError: If the operation times out.
            RepositoryCancelledError: If cancelled by caller.
            RepositoryProviderError: On general provider failure.
        """
        pass

"""
Project Workspace Provider Abstraction (Phase 1 / Part 8 / Step 2).

Defines the provider protocol and bounded operation interface giving ProjectContextCrawler
controlled, READ-ONLY access to the current project workspace.
Enforces project-root containment, bounded resource limits, credential isolation, and zero-execution safety.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import logging
import os
import posixpath
import re
import time
from typing import Any, Callable, Optional

from core.research.errors import (
    ProjectAccessError,
    ProjectCancelledError,
    ProjectFileNotFoundError,
    ProjectMalformedFileError,
    ProjectProviderError,
    ProjectResourceLimitError,
    ProjectSecurityError,
    ProjectTimeoutError,
    ProjectValidationError,
)
from core.research.project.models import (
    ProjectDirectory,
    ProjectFile,
    ProjectIdentity,
    ProjectSourceMaterial,
    ProjectStructure,
    ProjectVCSContext,
    normalize_project_path,
)
from core.research.repo.models import LineRange

logger = logging.getLogger("AutonomOS.Research.ProjectWorkspaceProvider")

IGNORED_PROJECT_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".bzr",
    ".DS_Store",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    "env",
    ".env",
    ".tox",
    "dist",
    "build",
}

SENSITIVE_FILE_PATTERNS = [
    re.compile(r"^\.env(?:\..*)?$", re.IGNORECASE),
    re.compile(r"^.*(?:id_rsa|id_dsa|id_ecdsa|id_ed25519)(?:\.pub)?$", re.IGNORECASE),
    re.compile(r"^.*(?:\.pem|\.key|\.pfx|\.p12|\.pkcs12|\.der)$", re.IGNORECASE),
    re.compile(r"^.*(?:credentials|secrets)\.(?:json|yaml|yml|toml)$", re.IGNORECASE),
    re.compile(r"^\.?(?:git-credentials|npmrc|pypirc)$", re.IGNORECASE),
    re.compile(r"^.*service-account.*\.json$", re.IGNORECASE),
    re.compile(r"^.*gcp.*\.json$", re.IGNORECASE),
    re.compile(r"^azureProfile\.json$", re.IGNORECASE),
    re.compile(r"^(?:passwd|shadow)$", re.IGNORECASE),
    re.compile(r"^(?:token|api_key|password|secret).*\.txt$", re.IGNORECASE),
]

# Sensitive path segment patterns (e.g. .aws/credentials)
SENSITIVE_DIR_SEGMENT_PATTERNS = [
    re.compile(r"^\.aws$", re.IGNORECASE),
    re.compile(r"^\.ssh$", re.IGNORECASE),
    re.compile(r"^\.gnupg$", re.IGNORECASE),
]


def is_sensitive_project_path(path: str) -> bool:
    """
    Check if a relative project path references a known sensitive credential, key, or secret file.
    Checks both filename patterns and enclosing sensitive directory segments (e.g. .aws/, .ssh/).
    """
    if not path:
        return False
    parts = path.replace("\\", "/").strip("/").split("/")
    # Check directory components
    for dir_part in parts[:-1]:
        for dir_pat in SENSITIVE_DIR_SEGMENT_PATTERNS:
            if dir_pat.match(dir_part):
                return True

    # Check filename / individual parts
    for part in parts:
        if part.startswith(".env"):
            return True
        for pat in SENSITIVE_FILE_PATTERNS:
            if pat.match(part):
                return True
    return False


INLINE_SECRET_PATTERNS = [
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA[REDACTED]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{36,}\b"), "ghp_[REDACTED]"),
    (re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----(?:.|\n)*?-----END [A-Z ]+PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9_\-\.]{25,}\b"), r"\g<1>[REDACTED]"),
    (re.compile(r"(?i)(password|passwd|secret|api_key|apikey|auth_token)\s*([:=])\s*['\"][^'\"]{6,}['\"]"), r'\1\2"[REDACTED]"'),
]


def sanitize_content_secrets(content: str) -> str:
    """
    Scrub recognized secrets, tokens, and private keys from string content.
    Prevents inadvertent leakage of sensitive data in extracted text and snippets.
    """
    if not content:
        return ""
    scrubbed = content
    for pattern, replacement in INLINE_SECRET_PATTERNS:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed


def sanitize_project_error(err: Exception | str) -> str:
    """
    Sanitize error message to prevent leaking raw secret tokens, passwords, or sensitive paths.
    """
    msg = str(err) if not isinstance(err, str) else err
    if not msg:
        return ""
    # Redact URL credentials
    msg = re.sub(r"://([^:@\s]+):([^@\s]+)@", r"://\1:[REDACTED]@", msg)
    # Redact inline secrets
    msg = sanitize_content_secrets(msg)
    return msg



@dataclass(frozen=True)
class ProjectFetchLimits:
    """
    Configurable resource and safety limits for project workspace inspection.
    """
    max_file_bytes: int = 1_000_000         # 1 MB per file content limit
    max_total_bytes: int = 10_000_000       # 10 MB total across queries
    max_tree_depth: int = 15                # Maximum directory hierarchy depth
    max_tree_files: int = 2000              # Maximum file count returned in a single directory query
    max_file_size: int = 5_000_000          # 5 MB hard ceiling on individual file size allowed to open
    timeout_seconds: float = 30.0           # Default per-operation timeout in seconds

    def __post_init__(self):
        if self.max_file_bytes <= 0:
            raise ProjectValidationError("max_file_bytes", "max_file_bytes must be > 0.")
        if self.max_total_bytes <= 0:
            raise ProjectValidationError("max_total_bytes", "max_total_bytes must be > 0.")
        if self.max_tree_depth <= 0:
            raise ProjectValidationError("max_tree_depth", "max_tree_depth must be > 0.")
        if self.max_tree_files <= 0:
            raise ProjectValidationError("max_tree_files", "max_tree_files must be > 0.")
        if self.max_file_size <= 0:
            raise ProjectValidationError("max_file_size", "max_file_size must be > 0.")
        if self.timeout_seconds <= 0:
            raise ProjectValidationError("timeout_seconds", "timeout_seconds must be > 0.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_file_bytes": self.max_file_bytes,
            "max_total_bytes": self.max_total_bytes,
            "max_tree_depth": self.max_tree_depth,
            "max_tree_files": self.max_tree_files,
            "max_file_size": self.max_file_size,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectFetchLimits:
        return cls(
            max_file_bytes=int(data.get("max_file_bytes", 1_000_000)),
            max_total_bytes=int(data.get("max_total_bytes", 10_000_000)),
            max_tree_depth=int(data.get("max_tree_depth", 15)),
            max_tree_files=int(data.get("max_tree_files", 2000)),
            max_file_size=int(data.get("max_file_size", 5_000_000)),
            timeout_seconds=float(data.get("timeout_seconds", 30.0)),
        )


@dataclass(frozen=True)
class ProjectTreeParams:
    """
    Explicit request parameters for retrieving a bounded project directory structure.
    """
    subpath: str = ""
    max_depth: Optional[int] = None
    max_files: Optional[int] = None
    timeout_seconds: Optional[float] = None

    def __post_init__(self):
        if self.max_depth is not None and self.max_depth <= 0:
            raise ProjectValidationError("max_depth", "max_depth must be > 0.")
        if self.max_files is not None and self.max_files <= 0:
            raise ProjectValidationError("max_files", "max_files must be > 0.")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ProjectValidationError("timeout_seconds", "timeout_seconds must be > 0.")


@dataclass(frozen=True)
class ProjectFileParams:
    """
    Explicit request parameters for retrieving project file content.
    """
    file_path: str
    line_range: Optional[LineRange] = None
    max_bytes: Optional[int] = None
    timeout_seconds: Optional[float] = None

    def __post_init__(self):
        if not self.file_path or not isinstance(self.file_path, str) or not self.file_path.strip():
            raise ProjectValidationError("file_path", "file_path cannot be empty.")
        if self.max_bytes is not None and self.max_bytes <= 0:
            raise ProjectValidationError("max_bytes", "max_bytes must be > 0.")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ProjectValidationError("timeout_seconds", "timeout_seconds must be > 0.")


class ProjectWorkspaceProvider(ABC):
    """
    Abstract Base Class for Project Workspace Data Access Providers.

    Enforces:
    - Zero execution: strictly read-only filesystem or fixture operations.
    - Project-root containment: rejects relative or absolute traversals escaping root.
    - Credential isolation: never exposes environment variables, process memory, or secret files.
    - Resource limits: file count, depth, file size, byte ceilings, timeout, and cancellation.
    """

    def __init__(
        self,
        provider_id: str,
        name: str,
        root_path: str = "/",
        limits: Optional[ProjectFetchLimits] = None,
    ):
        if not provider_id or not provider_id.strip():
            raise ProjectValidationError("provider_id", "Provider identifier cannot be empty.")
        if not name or not name.strip():
            raise ProjectValidationError("name", "Provider name cannot be empty.")

        self._provider_id = provider_id.strip()
        self._name = name.strip()
        self._root_path = root_path.strip() or "/"
        self._limits = limits or ProjectFetchLimits()

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def root_path(self) -> str:
        return self._root_path

    @property
    def limits(self) -> ProjectFetchLimits:
        return self._limits

    def check_cancellation(
        self,
        is_cancelled: Optional[Callable[[], bool]],
        operation: str,
        target: str = "",
    ) -> None:
        """
        Check cancellation predicate and raise ProjectCancelledError if triggered.
        """
        if is_cancelled is not None and is_cancelled():
            logger.info(f"Project operation '{operation}' for target '{target}' cancelled by caller.")
            raise ProjectCancelledError(operation=operation, target=target)

    def check_timeout(
        self,
        start_time: float,
        timeout_seconds: Optional[float],
        operation: str,
        target: str = "",
    ) -> None:
        """
        Check elapsed time against operation timeout and raise ProjectTimeoutError if exceeded.
        """
        eff_timeout = timeout_seconds if timeout_seconds is not None else self._limits.timeout_seconds
        if eff_timeout > 0:
            elapsed = time.time() - start_time
            if elapsed > eff_timeout:
                logger.warning(f"Project operation '{operation}' for target '{target}' timed out after {elapsed:.2f}s.")
                raise ProjectTimeoutError(operation=operation, timeout_seconds=eff_timeout, target=target)

    def validate_relative_path(self, path: str) -> str:
        """
        Validate and normalize a project path ensuring strict root containment.
        Rejects directory traversals ('..'), null bytes, and paths escaping root.
        """
        if path is None:
            raise ProjectValidationError("path", "Path cannot be None.")

        # If an absolute path is provided, ensure it starts within root_path
        cleaned = path.strip().replace("\\", "/")
        if cleaned.startswith("/") or (len(cleaned) > 2 and cleaned[1] == ":"):
            # Normalize root_path and cleaned path
            norm_root = posixpath.normpath(self._root_path.replace("\\", "/"))
            norm_cleaned = posixpath.normpath(cleaned)
            if norm_cleaned != norm_root and not norm_cleaned.startswith(norm_root.rstrip("/") + "/"):
                raise ProjectSecurityError(path, f"Absolute path '{path}' escapes project root '{self._root_path}'.")
            # Convert to relative path inside root
            rel = posixpath.relpath(norm_cleaned, norm_root)
            return normalize_project_path(rel)

        return normalize_project_path(path)

    @abstractmethod
    def identify_root(
        self,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> ProjectIdentity:
        """
        Identify canonical project identity (id, root path, name, type, languages, version, provenance).
        """
        pass

    @abstractmethod
    def list_dir(
        self,
        subpath: str = "",
        max_depth: Optional[int] = None,
        max_files: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> ProjectStructure:
        """
        Retrieve hierarchical directory and file tree structure starting from subpath.
        Bounded by max_depth and max_files.
        """
        pass

    @abstractmethod
    def get_file_metadata(
        self,
        file_path: str,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> ProjectFile:
        """
        Retrieve metadata for a single relative project file path.
        """
        pass

    @abstractmethod
    def get_file_content(
        self,
        file_path: str,
        line_range: Optional[LineRange] = None,
        max_bytes: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> ProjectSourceMaterial:
        """
        Retrieve file content by relative path, optionally sliced to a LineRange and bounded by max_bytes.
        """
        pass

    @abstractmethod
    def get_project_metadata(
        self,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> dict[str, Any]:
        """
        Retrieve safe project metadata with sensitive fields/secrets masked.
        """
        pass

    @abstractmethod
    def get_vcs_metadata(
        self,
        allow_vcs: bool = False,
        timeout_seconds: Optional[float] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Optional[ProjectVCSContext]:
        """
        Retrieve VCS metadata (branch, commit hash, sanitized remote URL) only when explicitly permitted.
        """
        pass

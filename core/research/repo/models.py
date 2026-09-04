"""
Repository Source and Structural Domain Models (Phase 1 / Part 5 / Step 1).

Establishes provider-neutral, strongly typed domain representations for:
- Repository Identity (URL, provider, owner, name, default branch)
- Repository Revision Context (commit, branch, tag, version context, timestamps)
- Repository Tree & Hierarchy (directories, files, parent/child relationships, bounds)
- Repository File Metadata (path, filename, extension, language, size, lines, binary/text, checksum)
- Repository Source Material (code snippets, line ranges, raw/extracted content, hashes)
- Version & Language Context (explicit tags, semver, localization, language detection)
- Integration with CrawlerReport contracts (RawSourceReference, EvidenceItem, EvidenceProvenance)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import os
import posixpath
import re
from typing import Any, Optional
import urllib.parse
import uuid

from core.research.contracts.crawler_report import RawSourceReference
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.errors import RepositoryValidationError
from core.research.types import FactClassification, ResearchConfidence, SourceType


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def compute_sha256(content: str | bytes) -> str:
    """Compute SHA-256 hex digest for cryptographic integrity."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


# -----------------------------------------------------------------------------
# Language Detection & Binary Extension Classifications
# -----------------------------------------------------------------------------

EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".swift": "swift",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".fs": "fsharp",
    ".dart": "dart",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".fish": "fish",
    ".ps1": "powershell",
    ".sql": "sql",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    ".json": "json",
    ".jsonc": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".md": "markdown",
    ".markdown": "markdown",
    ".rst": "rst",
    ".tex": "latex",
    ".lua": "lua",
    ".r": "r",
    ".pl": "perl",
    ".pm": "perl",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hrl": "erlang",
    ".clj": "clojure",
    ".cljs": "clojure",
    ".hs": "haskell",
    ".lhs": "haskell",
    ".zig": "zig",
    ".nim": "nim",
    ".d": "d",
    ".proto": "protobuf",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".dockerfile": "dockerfile",
    "dockerfile": "dockerfile",
    "makefile": "makefile",
    "cmakelists.txt": "cmake",
}

KNOWN_BINARY_EXTENSIONS: set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".svgz", ".bmp", ".tiff",
    ".pdf", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".zst",
    ".iso", ".dmg", ".pkg", ".rpm", ".deb", ".apk", ".ipa", ".cab",
    ".exe", ".dll", ".so", ".dylib", ".wasm", ".bin", ".dat", ".o", ".a", ".obj", ".lib", ".elf",
    ".pyc", ".pyo", ".pyd", ".class", ".jar", ".war", ".ear",
    ".mp3", ".wav", ".ogg", ".flac", ".aac",
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".db", ".sqlite", ".sqlite3", ".pdb", ".node",
}


def normalize_repo_path(path: str) -> str:
    """
    Normalize repository-relative path with POSIX forward slashes,
    removing leading/trailing slashes and resolving relative dots without escaping root.
    Validates against null bytes, control characters, and encoded traversals.
    """
    if not path or not isinstance(path, str):
        return ""
    if "\x00" in path:
        raise RepositoryValidationError("path", f"Path contains null byte: '{path}'")
    if any(ord(c) < 32 and c not in ("\t",) for c in path):
        raise RepositoryValidationError("path", f"Path contains illegal control characters: '{path}'")
    if "%" in path:
        try:
            decoded = urllib.parse.unquote(path)
            if "\x00" in decoded:
                raise RepositoryValidationError("path", f"Path contains encoded null byte: '{path}'")
            decoded_clean = decoded.replace("\\", "/")
            if "/../" in f"/{decoded_clean}/" or decoded_clean.startswith("../") or decoded_clean == "..":
                raise RepositoryValidationError("path", f"Path contains encoded traversal: '{path}'")
        except RepositoryValidationError:
            raise
        except Exception:
            pass

    cleaned = path.strip().replace("\\", "/")
    # Remove leading slashes
    cleaned = cleaned.lstrip("/")
    # Clean with posixpath
    norm = posixpath.normpath(cleaned)
    if norm in (".", "/"):
        return ""
    # Guard against directory traversal
    if norm.startswith("../") or norm == "..":
        raise RepositoryValidationError("path", f"Path escapes repository root: '{path}'")
    return norm


def detect_file_language(path_or_filename: str) -> Optional[str]:
    """
    Deterministically detect programming or markup language from file extension or base name.
    """
    if not path_or_filename or not isinstance(path_or_filename, str):
        return None
    normalized = path_or_filename.strip().lower()
    base_name = os.path.basename(normalized)

    # Check exact base name match first (e.g. Dockerfile, Makefile)
    if base_name in EXTENSION_LANGUAGE_MAP:
        return EXTENSION_LANGUAGE_MAP[base_name]

    # Check extension
    _, ext = os.path.splitext(normalized)
    if ext and ext in EXTENSION_LANGUAGE_MAP:
        return EXTENSION_LANGUAGE_MAP[ext]

    return None


def is_known_binary_extension(ext_or_path: str) -> bool:
    """
    Check if a file path or extension represents a known binary format.
    """
    if not ext_or_path or not isinstance(ext_or_path, str):
        return False
    normalized = ext_or_path.strip().lower()
    if not normalized.startswith("."):
        _, ext = os.path.splitext(normalized)
    else:
        ext = normalized
    return ext in KNOWN_BINARY_EXTENSIONS


# -----------------------------------------------------------------------------
# Provider & Version Enums
# -----------------------------------------------------------------------------

class RepositoryProviderType(str, Enum):
    """
    Taxonomy of repository host providers and source formats.
    """
    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"
    LOCAL_GIT = "local_git"
    GENERIC_GIT = "generic_git"
    ARCHIVE = "archive"
    UNKNOWN = "unknown"

    @classmethod
    def from_url(cls, url: str) -> RepositoryProviderType:
        """
        Infer repository provider from URL domain or scheme.
        """
        if not url or not isinstance(url, str):
            return cls.UNKNOWN
        lowered = url.strip().lower()

        if lowered.startswith("file://") or (os.path.isabs(url) and not lowered.startswith("http")):
            return cls.LOCAL_GIT
        if "github.com" in lowered:
            return cls.GITHUB
        if "gitlab.com" in lowered or "gitlab" in lowered:
            return cls.GITLAB
        if "bitbucket.org" in lowered or "bitbucket" in lowered:
            return cls.BITBUCKET
        if lowered.endswith(".git") or lowered.startswith("git@"):
            return cls.GENERIC_GIT
        if lowered.endswith((".zip", ".tar.gz", ".tgz", ".tar")):
            return cls.ARCHIVE
        if lowered.startswith(("http://", "https://")):
            return cls.GENERIC_GIT
        return cls.UNKNOWN


class RepoVersionCategory(str, Enum):
    """
    Classification of repository revision version semantics.
    """
    TAG = "tag"
    BRANCH = "branch"
    COMMIT = "commit"
    LATEST = "latest"
    STABLE = "stable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RepoVersionContext:
    """
    Version and revision context for a repository snapshot.
    Never guesses version strings: unconfirmed versions remain UNKNOWN.
    """
    category: RepoVersionCategory = RepoVersionCategory.UNKNOWN
    version_string: Optional[str] = None
    is_default: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value if isinstance(self.category, RepoVersionCategory) else str(self.category),
            "version_string": self.version_string,
            "is_default": self.is_default,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepoVersionContext:
        cat_raw = data.get("category", RepoVersionCategory.UNKNOWN.value)
        try:
            category = RepoVersionCategory(cat_raw)
        except (ValueError, TypeError):
            category = RepoVersionCategory.UNKNOWN

        return cls(
            category=category,
            version_string=data.get("version_string"),
            is_default=bool(data.get("is_default", False)),
        )

    @classmethod
    def unknown(cls) -> RepoVersionContext:
        """Create unknown version context."""
        return cls(category=RepoVersionCategory.UNKNOWN, version_string=None, is_default=False)

    @classmethod
    def latest(cls, is_default: bool = True) -> RepoVersionContext:
        """Create 'latest' repository version context."""
        return cls(category=RepoVersionCategory.LATEST, version_string="latest", is_default=is_default)

    @classmethod
    def stable(cls, version_string: Optional[str] = None, is_default: bool = True) -> RepoVersionContext:
        """Create 'stable' release version context."""
        return cls(category=RepoVersionCategory.STABLE, version_string=version_string or "stable", is_default=is_default)

    @classmethod
    def tag(cls, version_string: str, is_default: bool = False) -> RepoVersionContext:
        """Create explicit release/git tag version context (e.g. 'v1.4.0')."""
        if not version_string or not isinstance(version_string, str) or not version_string.strip():
            raise RepositoryValidationError("version_string", "Tag version string cannot be empty.")
        return cls(category=RepoVersionCategory.TAG, version_string=version_string.strip(), is_default=is_default)

    @classmethod
    def from_tag(cls, version_string: str, is_default: bool = False) -> RepoVersionContext:
        return cls.tag(version_string, is_default=is_default)

    @classmethod
    def branch(cls, branch_name: str, is_default: bool = False) -> RepoVersionContext:
        """Create branch version context (e.g. 'main', 'develop')."""
        if not branch_name or not isinstance(branch_name, str) or not branch_name.strip():
            raise RepositoryValidationError("version_string", "Branch name cannot be empty.")
        return cls(category=RepoVersionCategory.BRANCH, version_string=branch_name.strip(), is_default=is_default)

    @classmethod
    def from_branch(cls, branch_name: str, is_default: bool = False) -> RepoVersionContext:
        return cls.branch(branch_name, is_default=is_default)

    @classmethod
    def commit(cls, sha: str) -> RepoVersionContext:
        """Create commit SHA version context."""
        if not sha or not isinstance(sha, str) or not sha.strip():
            raise RepositoryValidationError("version_string", "Commit SHA cannot be empty.")
        return cls(category=RepoVersionCategory.COMMIT, version_string=sha.strip(), is_default=False)

    @classmethod
    def from_commit(cls, sha: str) -> RepoVersionContext:
        return cls.commit(sha)


# -----------------------------------------------------------------------------
# Repository Identity & Revision
# -----------------------------------------------------------------------------

@dataclass
class RepositoryIdentity:
    """
    Deterministic identity of a code repository source.
    """
    repo_id: str
    url: str
    provider_type: RepositoryProviderType = RepositoryProviderType.UNKNOWN
    owner: Optional[str] = None
    name: Optional[str] = None
    full_name: Optional[str] = None
    default_branch: Optional[str] = None
    is_private: bool = False
    description: Optional[str] = None
    primary_language: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.repo_id or not isinstance(self.repo_id, str) or not self.repo_id.strip():
            raise RepositoryValidationError("repo_id", "Repository identifier cannot be empty.")
        if not self.url or not isinstance(self.url, str) or not self.url.strip():
            raise RepositoryValidationError("url", "Repository URL cannot be empty.")

        # Derive full_name if owner and name exist
        if not self.full_name and self.owner and self.name:
            self.full_name = f"{self.owner}/{self.name}"
        elif self.full_name and not self.owner and "/" in self.full_name:
            parts = self.full_name.split("/", 1)
            self.owner = parts[0]
            if not self.name:
                self.name = parts[1]

        # Auto-detect provider if UNKNOWN
        if self.provider_type == RepositoryProviderType.UNKNOWN:
            self.provider_type = RepositoryProviderType.from_url(self.url)

    @classmethod
    def from_url(
        cls,
        url: str,
        repo_id: Optional[str] = None,
        default_branch: Optional[str] = None,
        is_private: bool = False,
        metadata: Optional[dict[str, Any]] = None,
    ) -> RepositoryIdentity:
        """
        Construct RepositoryIdentity from repository URL, parsing owner and name when available.
        """
        if not url or not isinstance(url, str) or not url.strip():
            raise RepositoryValidationError("url", "Repository URL cannot be empty.")

        raw_url = url.strip()
        provider = RepositoryProviderType.from_url(raw_url)
        owner = None
        name = None

        # Parse owner/name from standard URL structures (https://github.com/owner/repo, git@host:owner/repo.git, nested groups)
        if provider in (RepositoryProviderType.GITHUB, RepositoryProviderType.GITLAB, RepositoryProviderType.BITBUCKET, RepositoryProviderType.GENERIC_GIT, RepositoryProviderType.LOCAL_GIT):
            clean_url = re.sub(r"\.git$", "", raw_url)
            if clean_url.startswith("git@") or (":" in clean_url and not clean_url.startswith(("http://", "https://", "file://", "ssh://"))):
                path_str = clean_url.split(":", 1)[1]
            else:
                parsed = urllib.parse.urlparse(clean_url)
                path_str = parsed.path

            path_parts = [p for p in path_str.strip("/").split("/") if p]
            if provider == RepositoryProviderType.LOCAL_GIT:
                if path_parts:
                    name = path_parts[-1]
            else:
                if len(path_parts) >= 2:
                    owner = "/".join(path_parts[:-1])
                    name = path_parts[-1]
                elif len(path_parts) == 1:
                    name = path_parts[0]

        rid = repo_id or f"repo-{hashlib.sha256(raw_url.encode()).hexdigest()[:8]}"
        full_name = f"{owner}/{name}" if (owner and name) else name

        return cls(
            repo_id=rid,
            url=raw_url,
            provider_type=provider,
            owner=owner,
            name=name,
            full_name=full_name,
            default_branch=default_branch,
            is_private=is_private,
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "url": self.url,
            "provider_type": self.provider_type.value if isinstance(self.provider_type, RepositoryProviderType) else str(self.provider_type),
            "owner": self.owner,
            "name": self.name,
            "full_name": self.full_name,
            "default_branch": self.default_branch,
            "is_private": self.is_private,
            "description": self.description,
            "primary_language": self.primary_language,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryIdentity:
        prov_raw = data.get("provider_type", RepositoryProviderType.UNKNOWN.value)
        try:
            provider = RepositoryProviderType(prov_raw)
        except (ValueError, TypeError):
            provider = RepositoryProviderType.UNKNOWN

        return cls(
            repo_id=data.get("repo_id", f"repo-{uuid.uuid4().hex[:8]}"),
            url=data.get("url", ""),
            provider_type=provider,
            owner=data.get("owner"),
            name=data.get("name"),
            full_name=data.get("full_name"),
            default_branch=data.get("default_branch"),
            is_private=bool(data.get("is_private", False)),
            description=data.get("description"),
            primary_language=data.get("primary_language"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class RepositoryRevision:
    """
    Deterministic snapshot context of a repository revision.
    """
    commit_sha: Optional[str] = None
    branch: Optional[str] = None
    tag: Optional[str] = None
    version_context: RepoVersionContext = field(default_factory=RepoVersionContext.unknown)
    retrieved_at: str = field(default_factory=utc_now)
    is_dirty: bool = False
    author: Optional[str] = None
    commit_message: Optional[str] = None
    commit_date: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Align version context if commit, branch, or tag are provided and version_context is UNKNOWN
        if self.version_context.category == RepoVersionCategory.UNKNOWN:
            if self.tag:
                self.version_context = RepoVersionContext.tag(self.tag)
            elif self.branch:
                self.version_context = RepoVersionContext.branch(self.branch)
            elif self.commit_sha:
                self.version_context = RepoVersionContext.commit(self.commit_sha)

    @classmethod
    def default(cls) -> RepositoryRevision:
        """Create default current snapshot revision."""
        return cls(retrieved_at=utc_now())

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_sha": self.commit_sha,
            "branch": self.branch,
            "tag": self.tag,
            "version_context": self.version_context.to_dict(),
            "retrieved_at": self.retrieved_at,
            "is_dirty": self.is_dirty,
            "author": self.author,
            "commit_message": self.commit_message,
            "commit_date": self.commit_date,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryRevision:
        vc_data = data.get("version_context")
        vc = RepoVersionContext.from_dict(vc_data) if isinstance(vc_data, dict) else RepoVersionContext.unknown()

        return cls(
            commit_sha=data.get("commit_sha"),
            branch=data.get("branch"),
            tag=data.get("tag"),
            version_context=vc,
            retrieved_at=data.get("retrieved_at", utc_now()),
            is_dirty=bool(data.get("is_dirty", False)),
            author=data.get("author"),
            commit_message=data.get("commit_message"),
            commit_date=data.get("commit_date"),
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# Line Range & File/Directory Models
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class LineRange:
    """
    1-indexed, inclusive bounded line span within a repository source file.
    """
    start_line: int
    end_line: int

    def __post_init__(self):
        if not isinstance(self.start_line, int) or self.start_line < 1:
            raise RepositoryValidationError("start_line", f"Start line must be an integer >= 1 (got {self.start_line}).")
        if not isinstance(self.end_line, int) or self.end_line < self.start_line:
            raise RepositoryValidationError("end_line", f"End line ({self.end_line}) cannot be less than start line ({self.start_line}).")

    @property
    def line_count(self) -> int:
        """Total number of lines spanned."""
        return self.end_line - self.start_line + 1

    def contains(self, line_number: int) -> bool:
        """Check if a specific 1-indexed line number is within this range."""
        return self.start_line <= line_number <= self.end_line

    def overlaps(self, other: LineRange) -> bool:
        """Check if this line range overlaps with another line range."""
        return max(self.start_line, other.start_line) <= min(self.end_line, other.end_line)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_line": self.start_line,
            "end_line": self.end_line,
            "line_count": self.line_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LineRange:
        return cls(
            start_line=int(data["start_line"]),
            end_line=int(data["end_line"]),
        )


@dataclass
class RepositoryFile:
    """
    Structured metadata and relationship model for a single file in a repository.
    """
    path: str                                   # Normalized relative path (e.g. 'src/core/engine.ts')
    filename: str = ""                          # Base filename (e.g. 'engine.ts')
    extension: str = ""                         # Extension with leading dot (e.g. '.ts')
    parent_path: Optional[str] = None           # Parent directory path (e.g. 'src/core')
    size_bytes: int = 0
    line_count: Optional[int] = None
    is_binary: bool = False
    language: Optional[str] = None
    content_checksum: Optional[str] = None      # SHA-256 hash of file content when available
    file_references: list[str] = field(default_factory=list) # Structural links/references to other files
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.path or not isinstance(self.path, str) or not self.path.strip():
            raise RepositoryValidationError("path", "File path cannot be empty.")

        self.path = normalize_repo_path(self.path)

        if not self.filename:
            self.filename = posixpath.basename(self.path)

        if not self.extension:
            _, ext = posixpath.splitext(self.filename)
            self.extension = ext.lower()

        if self.parent_path is None:
            dir_name = posixpath.dirname(self.path)
            self.parent_path = dir_name if dir_name else None

        if not self.is_binary:
            self.is_binary = is_known_binary_extension(self.extension or self.filename)

        if not self.language and not self.is_binary:
            self.language = detect_file_language(self.path)

        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise RepositoryValidationError("size_bytes", f"File size must be a non-negative integer (got {self.size_bytes}).")
        if self.line_count is not None and (not isinstance(self.line_count, int) or self.line_count < 0):
            raise RepositoryValidationError("line_count", f"Line count must be a non-negative integer (got {self.line_count}).")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "filename": self.filename,
            "extension": self.extension,
            "parent_path": self.parent_path,
            "size_bytes": self.size_bytes,
            "line_count": self.line_count,
            "is_binary": self.is_binary,
            "language": self.language,
            "content_checksum": self.content_checksum,
            "file_references": list(self.file_references),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryFile:
        return cls(
            path=data.get("path", ""),
            filename=data.get("filename", ""),
            extension=data.get("extension", ""),
            parent_path=data.get("parent_path"),
            size_bytes=int(data.get("size_bytes", 0)),
            line_count=int(data["line_count"]) if data.get("line_count") is not None else None,
            is_binary=bool(data.get("is_binary", False)),
            language=data.get("language"),
            content_checksum=data.get("content_checksum"),
            file_references=list(data.get("file_references", [])),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class RepositoryDirectory:
    """
    Structured directory node in a repository tree.
    """
    path: str                                   # Normalized directory path (e.g. 'src/core')
    name: str = ""                              # Directory base name (e.g. 'core')
    parent_path: Optional[str] = None           # Parent directory path (e.g. 'src')
    child_dir_paths: list[str] = field(default_factory=list)
    child_file_paths: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.path is None or not isinstance(self.path, str):
            raise RepositoryValidationError("path", "Directory path must be a string.")

        self.path = normalize_repo_path(self.path)

        if not self.name:
            self.name = posixpath.basename(self.path) if self.path else ""

        if self.parent_path is None and self.path:
            dir_name = posixpath.dirname(self.path)
            self.parent_path = dir_name if dir_name else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "parent_path": self.parent_path,
            "child_dir_paths": list(self.child_dir_paths),
            "child_file_paths": list(self.child_file_paths),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryDirectory:
        return cls(
            path=data.get("path", ""),
            name=data.get("name", ""),
            parent_path=data.get("parent_path"),
            child_dir_paths=list(data.get("child_dir_paths", [])),
            child_file_paths=list(data.get("child_file_paths", [])),
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# Repository Tree
# -----------------------------------------------------------------------------

@dataclass
class RepositoryTree:
    """
    Bounded, hierarchical representation of repository structure (directories and files).
    """
    root_path: str = ""
    directories: list[RepositoryDirectory] = field(default_factory=list)
    files: list[RepositoryFile] = field(default_factory=list)
    total_files: int = 0
    total_directories: int = 0
    max_depth_reached: int = 0
    is_truncated: bool = False
    truncation_reason: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.root_path = normalize_repo_path(self.root_path)
        if not self.total_files:
            self.total_files = len(self.files)
        if not self.total_directories:
            self.total_directories = len(self.directories)

    def add_file(self, file: RepositoryFile) -> None:
        """Add a file to the repository tree, updating totals and parent relationships."""
        self.files.append(file)
        self.total_files = len(self.files)
        # Update depth
        depth = len(file.path.split("/")) if file.path else 0
        if depth > self.max_depth_reached:
            self.max_depth_reached = depth

    def add_directory(self, dir_node: RepositoryDirectory) -> None:
        """Add a directory to the repository tree, updating totals."""
        self.directories.append(dir_node)
        self.total_directories = len(self.directories)
        depth = len(dir_node.path.split("/")) if dir_node.path else 0
        if depth > self.max_depth_reached:
            self.max_depth_reached = depth

    def get_file(self, path: str) -> Optional[RepositoryFile]:
        """Find a file by normalized repository-relative path."""
        norm = normalize_repo_path(path)
        for f in self.files:
            if f.path == norm:
                return f
        return None

    def get_directory(self, path: str) -> Optional[RepositoryDirectory]:
        """Find a directory by normalized repository-relative path."""
        norm = normalize_repo_path(path)
        for d in self.directories:
            if d.path == norm:
                return d
        return None

    def get_children(self, dir_path: str = "") -> tuple[list[RepositoryDirectory], list[RepositoryFile]]:
        """Return direct child directories and files under the given directory path."""
        norm = normalize_repo_path(dir_path)
        target_parent = norm if norm else None

        child_dirs = [d for d in self.directories if d.parent_path == target_parent and d.path != norm]
        child_files = [f for f in self.files if f.parent_path == target_parent]
        return child_dirs, child_files

    @property
    def depth(self) -> int:
        """Return maximum hierarchy depth reached in tree."""
        return self.max_depth_reached

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_path": self.root_path,
            "directories": [d.to_dict() for d in self.directories],
            "files": [f.to_dict() for f in self.files],
            "total_files": self.total_files,
            "total_directories": self.total_directories,
            "max_depth_reached": self.max_depth_reached,
            "is_truncated": self.is_truncated,
            "truncation_reason": self.truncation_reason,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryTree:
        dirs = [RepositoryDirectory.from_dict(d) for d in data.get("directories", [])]
        fls = [RepositoryFile.from_dict(f) for f in data.get("files", [])]
        return cls(
            root_path=data.get("root_path", ""),
            directories=dirs,
            files=fls,
            total_files=int(data.get("total_files", len(fls))),
            total_directories=int(data.get("total_directories", len(dirs))),
            max_depth_reached=int(data.get("max_depth_reached", 0)),
            is_truncated=bool(data.get("is_truncated", False)),
            truncation_reason=data.get("truncation_reason"),
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# Repository Source Material & Snippet
# -----------------------------------------------------------------------------

@dataclass
class RepositorySourceMaterial:
    """
    Extracted file content or bounded code snippet from a repository source.
    Represents concrete source material without subjective semantic evaluation.
    """
    snippet_id: str
    repository_identity: RepositoryIdentity
    revision: RepositoryRevision
    file_path: str
    line_range: Optional[LineRange] = None
    content: str = ""
    raw_bytes: Optional[bytes] = None
    language: Optional[str] = None
    content_checksum: str = ""
    is_binary: bool = False
    provenance: Optional[EvidenceProvenance] = None
    structure: Optional[Any] = None  # Optional[StructuredCodeFile]
    retrieved_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.snippet_id or not isinstance(self.snippet_id, str) or not self.snippet_id.strip():
            raise RepositoryValidationError("snippet_id", "Snippet ID cannot be empty.")
        if not self.file_path or not isinstance(self.file_path, str) or not self.file_path.strip():
            raise RepositoryValidationError("file_path", "File path cannot be empty.")

        self.file_path = normalize_repo_path(self.file_path)

        if not self.language and not self.is_binary:
            self.language = detect_file_language(self.file_path)

        if not self.content_checksum:
            if self.content:
                self.content_checksum = compute_sha256(self.content)
            elif self.raw_bytes:
                self.content_checksum = compute_sha256(self.raw_bytes)

    @classmethod
    def generate_snippet_id(
        cls,
        repo_id: str,
        revision: RepositoryRevision | str,
        file_path: str,
        line_range: Optional[LineRange] = None,
    ) -> str:
        """
        Deterministically generate unique snippet identifier keyed by repo, revision, path, and line range.
        """
        rev_key: str
        if isinstance(revision, RepositoryRevision):
            rev_key = revision.commit_sha or revision.tag or revision.branch or "HEAD"
        else:
            rev_key = str(revision) or "HEAD"
        lr_str = f"{line_range.start_line}-{line_range.end_line}" if line_range else "full"
        norm_path = normalize_repo_path(file_path)
        seed = f"{repo_id}:{rev_key}:{norm_path}:{lr_str}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
        return f"snip-{digest}"

    @property
    def identity(self) -> RepositoryIdentity:
        """Alias for repository_identity."""
        return self.repository_identity

    @property
    def checksum(self) -> str:
        """Alias for content_checksum."""
        return self.content_checksum

    @property
    def source_ref(self) -> str:
        """Construct canonical URI reference for this repository source snippet."""
        base_url = self.repository_identity.url.rstrip("/") if self.repository_identity.url else f"repo://{self.repository_identity.repo_id}"
        if base_url.endswith(".git"):
            base_url = base_url[:-4]
        rev = self.revision.commit_sha or self.revision.tag or self.revision.branch or "HEAD"
        line_frag = f"#L{self.line_range.start_line}-L{self.line_range.end_line}" if self.line_range else ""
        if base_url.startswith("file://") or "://" not in base_url:
            return f"{base_url}@{rev}/{self.file_path}{line_frag}"
        return f"{base_url}/blob/{rev}/{self.file_path}{line_frag}"

    def to_raw_source_reference(self) -> RawSourceReference:
        """Convert snippet into standard RawSourceReference for CrawlerReport integration."""
        meta: dict[str, Any] = {
            "snippet_id": self.snippet_id,
            "repo_id": self.repository_identity.repo_id,
            "repo_url": self.repository_identity.url,
            "provider_type": self.repository_identity.provider_type.value if hasattr(self.repository_identity.provider_type, "value") else str(self.repository_identity.provider_type),
            "file_path": self.file_path,
            "language": self.language,
            "is_binary": self.is_binary,
            "line_range": self.line_range.to_dict() if self.line_range else None,
            "revision": self.revision.to_dict(),
            "commit_sha": self.revision.commit_sha,
            "branch": self.revision.branch,
            "tag": self.revision.tag,
            "version_context": self.revision.version_context.to_dict() if self.revision.version_context else None,
            "content_checksum": self.content_checksum,
        }
        if self.structure is not None and hasattr(self.structure, "parsing_status"):
            meta["code_structure"] = {
                "parsing_status": self.structure.parsing_status.value if hasattr(self.structure.parsing_status, "value") else str(self.structure.parsing_status),
                "classes_count": len(self.structure.classes) if hasattr(self.structure, "classes") else 0,
                "functions_count": len(self.structure.functions) if hasattr(self.structure, "functions") else 0,
                "symbols": list(self.structure.top_level_symbols[:20]) if hasattr(self.structure, "top_level_symbols") else [],
                "imports_count": len(self.structure.imports) if hasattr(self.structure, "imports") else 0,
                "exports_count": len(self.structure.exports) if hasattr(self.structure, "exports") else 0,
            }

        return RawSourceReference(
            url_or_ref=self.source_ref,
            title=f"{self.repository_identity.full_name or self.repository_identity.name}:{self.file_path}",
            publisher=self.repository_identity.owner or self.repository_identity.provider_type.value,
            source_type=SourceType.REPOSITORY,
            checksum=self.content_checksum,
            bytes_fetched=len(self.raw_bytes) if self.raw_bytes is not None else len(self.content.encode("utf-8")),
            content_snippet=self.content[:2000] if not self.is_binary else f"[Binary content: {self.file_path}]",
            fetched_at=self.retrieved_at,
            metadata=meta,
        )

    def to_evidence_items(
        self,
        request_id: str,
        crawler_task_id: str,
        crawler_id: str,
        question_id: str = "",
        correlation_id: str = "",
    ) -> list[EvidenceItem]:
        """Convert snippet into standard EvidenceItem collection."""
        prov = EvidenceProvenance(
            request_id=request_id,
            crawler_task_id=crawler_task_id,
            crawler_id=crawler_id,
            question_id=question_id,
            source_ref=self.source_ref,
            correlation_id=correlation_id,
            captured_at=self.retrieved_at,
        )

        title_desc = f"Repository file {self.file_path}"
        if self.line_range:
            title_desc += f" (L{self.line_range.start_line}-L{self.line_range.end_line})"

        snippet_text = self.content[:4000] if not self.is_binary else f"[Binary content: {self.file_path}]"

        meta = {
            "snippet_id": self.snippet_id,
            "repo_id": self.repository_identity.repo_id,
            "repo_url": self.repository_identity.url,
            "provider_type": self.repository_identity.provider_type.value if hasattr(self.repository_identity.provider_type, "value") else str(self.repository_identity.provider_type),
            "file_path": self.file_path,
            "language": self.language,
            "is_binary": self.is_binary,
            "line_range": self.line_range.to_dict() if self.line_range else None,
            "revision": self.revision.to_dict(),
            "commit_sha": self.revision.commit_sha,
            "branch": self.revision.branch,
            "tag": self.revision.tag,
            "version_context": self.revision.version_context.to_dict() if self.revision.version_context else None,
            "has_code_structure": self.structure is not None,
            "content_checksum": self.content_checksum,
        }
        if self.structure is not None and hasattr(self.structure, "parsing_status"):
            meta["code_structure"] = {
                "parsing_status": self.structure.parsing_status.value if hasattr(self.structure.parsing_status, "value") else str(self.structure.parsing_status),
                "classes_count": len(self.structure.classes) if hasattr(self.structure, "classes") else 0,
                "functions_count": len(self.structure.functions) if hasattr(self.structure, "functions") else 0,
                "symbols": list(self.structure.top_level_symbols[:20]) if hasattr(self.structure, "top_level_symbols") else [],
                "imports_count": len(self.structure.imports) if hasattr(self.structure, "imports") else 0,
                "exports_count": len(self.structure.exports) if hasattr(self.structure, "exports") else 0,
            }

        evidence = EvidenceItem(
            evidence_id=f"ev-repo-{self.snippet_id}",
            provenance=prov,
            extracted_fact=title_desc,
            content_snippet=snippet_text,
            classification=FactClassification.SOURCE_CLAIM,
            confidence=ResearchConfidence.SUPPORTED,
            reliability_score=0.9,
            source_type=SourceType.REPOSITORY,
            checksum=self.content_checksum,
            metadata=meta,
        )
        return [evidence]

    def to_dict(self) -> dict[str, Any]:
        return {
            "snippet_id": self.snippet_id,
            "repository_identity": self.repository_identity.to_dict(),
            "revision": self.revision.to_dict(),
            "file_path": self.file_path,
            "line_range": self.line_range.to_dict() if self.line_range else None,
            "content": self.content,
            "language": self.language,
            "content_checksum": self.content_checksum,
            "is_binary": self.is_binary,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "structure": self.structure.to_dict() if self.structure is not None and hasattr(self.structure, "to_dict") else None,
            "retrieved_at": self.retrieved_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositorySourceMaterial:
        identity = RepositoryIdentity.from_dict(data["repository_identity"])
        revision = RepositoryRevision.from_dict(data["revision"])
        lr_data = data.get("line_range")
        line_range = LineRange.from_dict(lr_data) if isinstance(lr_data, dict) else None
        prov_data = data.get("provenance")
        provenance = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None

        struct_obj = None
        struct_data = data.get("structure")
        if isinstance(struct_data, dict):
            try:
                from core.research.repo.structure import StructuredCodeFile
                struct_obj = StructuredCodeFile.from_dict(struct_data)
            except Exception:
                struct_obj = None

        return cls(
            snippet_id=data.get("snippet_id", f"snip-{uuid.uuid4().hex[:8]}"),
            repository_identity=identity,
            revision=revision,
            file_path=data.get("file_path", ""),
            line_range=line_range,
            content=data.get("content", ""),
            language=data.get("language"),
            content_checksum=data.get("content_checksum", ""),
            is_binary=bool(data.get("is_binary", False)),
            provenance=provenance,
            structure=struct_obj,
            retrieved_at=data.get("retrieved_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# Repository Root Source Aggregate
# -----------------------------------------------------------------------------

@dataclass
class RepositorySource:
    """
    Root aggregate container representing a full or partial repository inspection snapshot.
    """
    identity: RepositoryIdentity
    revision: RepositoryRevision
    tree: Optional[RepositoryTree] = None
    source_materials: list[RepositorySourceMaterial] = field(default_factory=list)
    provenance: Optional[EvidenceProvenance] = None
    retrieved_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_materials_count(self) -> int:
        return len(self.source_materials)

    @property
    def total_files_count(self) -> int:
        return self.tree.total_files if self.tree else len(self.source_materials)

    def add_source_material(self, material: RepositorySourceMaterial) -> None:
        """Append extracted source material snippet to this repository snapshot."""
        self.source_materials.append(material)

    def to_raw_source_references(self) -> list[RawSourceReference]:
        """Convert all repository materials into standard RawSourceReferences."""
        refs: list[RawSourceReference] = []
        for mat in self.source_materials:
            refs.append(mat.to_raw_source_reference())
        return refs

    def to_evidence_items(
        self,
        request_id: str,
        crawler_task_id: str,
        crawler_id: str,
        question_id: str = "",
        correlation_id: str = "",
    ) -> list[EvidenceItem]:
        """Aggregate evidence items across all repository materials."""
        items: list[EvidenceItem] = []
        for mat in self.source_materials:
            evs = mat.to_evidence_items(
                request_id=request_id,
                crawler_task_id=crawler_task_id,
                crawler_id=crawler_id,
                question_id=question_id,
                correlation_id=correlation_id,
            )
            items.extend(evs)
        return items

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "revision": self.revision.to_dict(),
            "tree": self.tree.to_dict() if self.tree else None,
            "source_materials": [m.to_dict() for m in self.source_materials],
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "retrieved_at": self.retrieved_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositorySource:
        identity = RepositoryIdentity.from_dict(data["identity"])
        revision = RepositoryRevision.from_dict(data["revision"])
        tree_data = data.get("tree")
        tree = RepositoryTree.from_dict(tree_data) if isinstance(tree_data, dict) else None
        materials = [RepositorySourceMaterial.from_dict(m) for m in data.get("source_materials", [])]
        prov_data = data.get("provenance")
        provenance = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None

        return cls(
            identity=identity,
            revision=revision,
            tree=tree,
            source_materials=materials,
            provenance=provenance,
            retrieved_at=data.get("retrieved_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )

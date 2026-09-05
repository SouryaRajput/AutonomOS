"""
Project Context Domain Models (Phase 1 / Part 8 / Step 1).

Establishes provider-neutral, strongly typed domain representations for inspecting
and understanding the current AutonomOS project/workspace:
- ProjectIdentity (id, root path, project type, languages, explicit version, provenance)
- ProjectStructure & ProjectDirectory (hierarchical tree, directories, files, parent/child relationships)
- ProjectFile (relative path, filename, extension, language, size, line count, binary/text, content hash)
- ProjectSourceMaterial (file path, content, bounded LineRanges, structural metadata, content hash)
- ProjectSymbol (symbol name, symbol type, file path, line range, structural relationships)
- ProjectConfigurationMetadata (config path, config type, safe metadata with secret masking)
- ProjectDependencyMetadata (dependency name, version spec/constraint, manifest source, type, ecosystem)
- ProjectVCSContext (branch, revision, working-tree state, author, remote URL sanitization)
- ProjectContext (aggregate container snapshot with CrawlerReport integration)

Invariants:
- Read-only models.
- Reuses existing LineRange, language detection, binary classifications, and provenance contracts.
- Produces standard CrawlerReport contracts (RawSourceReference, EvidenceItem) with zero secrets.
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
from core.research.errors import (
    ProjectSecurityError,
    ProjectValidationError,
)
from core.research.repo.models import (
    LineRange,
    compute_sha256,
    detect_file_language,
    is_known_binary_extension,
    utc_now,
)
from core.research.repo.structure import SymbolKind
from core.research.search.security import REDACTED_STR, sanitize_url
from core.research.types import FactClassification, ResearchConfidence, SourceType


# -----------------------------------------------------------------------------
# Path Normalization & Security Bounds
# -----------------------------------------------------------------------------

def normalize_project_path(path: str) -> str:
    """
    Normalize workspace/project-relative path into POSIX forward-slash format.
    Guarantees:
    - Null bytes and control characters raise ProjectSecurityError.
    - URL-encoded traversals and root-escaping dots ('..') raise ProjectSecurityError.
    - Leading and trailing slashes are stripped.
    - POSIX normalization resolves internal relative dots.
    """
    if path is None or not isinstance(path, str):
        raise ProjectValidationError("path", f"Path must be a string (got {type(path).__name__}).")

    if not path.strip():
        return ""

    if "\x00" in path:
        raise ProjectSecurityError(path, "Path contains null byte.")

    if any(ord(c) < 32 and c not in ("\t",) for c in path):
        raise ProjectSecurityError(path, "Path contains illegal control characters.")

    if "%" in path:
        try:
            decoded = urllib.parse.unquote(path)
            if "\x00" in decoded:
                raise ProjectSecurityError(path, "Path contains encoded null byte.")
            decoded_clean = decoded.replace("\\", "/")
            if "/../" in f"/{decoded_clean}/" or decoded_clean.startswith("../") or decoded_clean == "..":
                raise ProjectSecurityError(path, "Path contains encoded directory traversal.")
        except ProjectSecurityError:
            raise
        except Exception:
            pass

    cleaned = path.strip().replace("\\", "/")
    cleaned = cleaned.lstrip("/")
    norm = posixpath.normpath(cleaned)

    if norm in (".", "/"):
        return ""

    if norm.startswith("../") or norm == "..":
        raise ProjectSecurityError(path, f"Path escapes project root: '{path}'")

    for part in norm.split("/"):
        if re.match(r"^\.{2,}$", part):
            raise ProjectSecurityError(path, f"Path contains invalid directory traversal dots: '{path}'")

    return norm


# -----------------------------------------------------------------------------
# Project Enums
# -----------------------------------------------------------------------------

class ProjectType(str, Enum):
    """Classification of project workspace technology stacks."""
    PYTHON = "python"
    NODE = "node"
    TYPESCRIPT = "typescript"
    RUST = "rust"
    GO = "go"
    DART = "dart"
    JAVA = "java"
    KOTLIN = "kotlin"
    CSHARP = "csharp"
    CPP = "cpp"
    C = "c"
    RUBY = "ruby"
    PHP = "php"
    MULTI_LANGUAGE = "multi_language"
    UNKNOWN = "unknown"

    @classmethod
    def from_string(cls, val: str | ProjectType) -> ProjectType:
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            return cls.UNKNOWN
        low = val.strip().lower()
        for member in cls:
            if member.value == low:
                return member
        return cls.UNKNOWN


class ProjectConfigType(str, Enum):
    """Taxonomy of project configuration and manifest types."""
    PYTHON_PYPROJECT = "python_pyproject"
    PYTHON_SETUPTOOLS = "python_setuptools"
    PYTHON_REQUIREMENTS = "python_requirements"
    PYTHON_FLIT = "python_flit"
    PYTHON_POETRY = "python_poetry"
    PYTHON_PIPENV = "python_pipenv"
    NPM_PACKAGE = "npm_package"
    TYPESCRIPT_TSCONFIG = "typescript_tsconfig"
    RUST_CARGO = "rust_cargo"
    GO_MOD = "go_mod"
    DART_PUBSPEC = "dart_pubspec"
    DOCKERFILE = "dockerfile"
    DOCKER_COMPOSE = "docker_compose"
    GITHUB_ACTIONS = "github_actions"
    GIT_CONFIG = "git_config"
    ENV = "env"
    GENERIC = "generic"

    @classmethod
    def from_string(cls, val: str | ProjectConfigType) -> ProjectConfigType:
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            return cls.GENERIC
        low = val.strip().lower()
        for member in cls:
            if member.value == low:
                return member
        return cls.GENERIC


class ProjectDependencyType(str, Enum):
    """Scope of declared dependencies."""
    RUNTIME = "runtime"
    DEV = "dev"
    BUILD = "build"
    PEER = "peer"
    OPTIONAL = "optional"

    @classmethod
    def from_string(cls, val: str | ProjectDependencyType) -> ProjectDependencyType:
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            return cls.RUNTIME
        low = val.strip().lower()
        for member in cls:
            if member.value == low:
                return member
        return cls.RUNTIME


class ProjectVCSState(str, Enum):
    """Working tree cleanliness status."""
    CLEAN = "clean"
    DIRTY = "dirty"
    UNTRACKED = "untracked"
    UNKNOWN = "unknown"

    @classmethod
    def from_string(cls, val: str | ProjectVCSState) -> ProjectVCSState:
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            return cls.UNKNOWN
        low = val.strip().lower()
        for member in cls:
            if member.value == low:
                return member
        return cls.UNKNOWN


class ProjectContractType(str, Enum):
    """Taxonomy of explicit project code contracts and structural specifications."""
    INTERFACE = "interface"
    PROTOCOL = "protocol"
    TYPED_MODEL = "typed_model"
    LIFECYCLE_ENUM = "lifecycle_enum"
    CONFIG_SCHEMA = "config_schema"
    EVENT_CONTRACT = "event_contract"
    REGISTRY_DEFINITION = "registry_definition"
    GENERIC = "generic"

    @classmethod
    def from_string(cls, val: str | ProjectContractType) -> ProjectContractType:
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            return cls.GENERIC
        low = val.strip().lower()
        for member in cls:
            if member.value == low:
                return member
        return cls.GENERIC



# -----------------------------------------------------------------------------
# 1. ProjectIdentity
# -----------------------------------------------------------------------------

@dataclass
class ProjectIdentity:
    """
    Deterministic identity and top-level classification of an AutonomOS project/workspace.
    Never guesses version strings: unconfirmed versions remain None.
    """
    project_id: str
    project_root: str
    project_type: ProjectType = ProjectType.UNKNOWN
    languages: list[str] = field(default_factory=list)
    version: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    provenance: Optional[EvidenceProvenance] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.project_id or not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ProjectValidationError("project_id", "Project identifier cannot be empty.")
        if not self.project_root or not isinstance(self.project_root, str) or not self.project_root.strip():
            raise ProjectValidationError("project_root", "Project root cannot be empty.")

        self.project_id = self.project_id.strip()
        self.project_root = os.path.normpath(self.project_root.strip())

        if isinstance(self.project_type, str):
            self.project_type = ProjectType.from_string(self.project_type)

        if not self.name:
            base = os.path.basename(self.project_root)
            self.name = base if base else self.project_id

        # Deduplicate languages preserving order
        seen_langs = set()
        clean_langs = []
        for lang in self.languages:
            if isinstance(lang, str) and lang.strip():
                l_clean = lang.strip().lower()
                if l_clean not in seen_langs:
                    seen_langs.add(l_clean)
                    clean_langs.append(l_clean)
        self.languages = clean_langs

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_root": self.project_root,
            "project_type": self.project_type.value,
            "languages": list(self.languages),
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectIdentity:
        if not isinstance(data, dict):
            raise ProjectValidationError("data", f"Expected dict, got {type(data).__name__}.")

        prov_data = data.get("provenance")
        prov = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None

        return cls(
            project_id=str(data.get("project_id", "")),
            project_root=str(data.get("project_root", "")),
            project_type=ProjectType.from_string(data.get("project_type", ProjectType.UNKNOWN.value)),
            languages=list(data.get("languages", [])),
            version=data.get("version"),
            name=data.get("name"),
            description=data.get("description"),
            provenance=prov,
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# 2. ProjectFile
# -----------------------------------------------------------------------------

@dataclass
class ProjectFile:
    """
    Structured metadata for a single file located within the project workspace.
    """
    relative_path: str
    filename: str = ""
    extension: str = ""
    language: Optional[str] = None
    size_bytes: int = 0
    line_count: Optional[int] = None
    is_binary: bool = False
    content_hash: Optional[str] = None
    parent_path: Optional[str] = None
    provenance: Optional[EvidenceProvenance] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.relative_path or not isinstance(self.relative_path, str) or not self.relative_path.strip():
            raise ProjectValidationError("relative_path", "Relative path cannot be empty.")

        self.relative_path = normalize_project_path(self.relative_path)
        if not self.relative_path:
            raise ProjectValidationError("relative_path", "Relative path cannot resolve to empty root.")

        if not self.filename:
            self.filename = posixpath.basename(self.relative_path)

        if not self.extension:
            _, ext = posixpath.splitext(self.filename)
            self.extension = ext.lower()

        if self.parent_path is None:
            dir_name = posixpath.dirname(self.relative_path)
            self.parent_path = dir_name if dir_name else None

        if not self.is_binary:
            self.is_binary = is_known_binary_extension(self.extension or self.filename)

        if not self.language and not self.is_binary:
            self.language = detect_file_language(self.relative_path)

        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ProjectValidationError("size_bytes", f"File size must be a non-negative integer (got {self.size_bytes}).")

        if self.line_count is not None and (not isinstance(self.line_count, int) or self.line_count < 0):
            raise ProjectValidationError("line_count", f"Line count must be a non-negative integer (got {self.line_count}).")

    @property
    def path(self) -> str:
        """Alias for relative_path for path-based ergonomics."""
        return self.relative_path

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "filename": self.filename,
            "extension": self.extension,
            "language": self.language,
            "size_bytes": self.size_bytes,
            "line_count": self.line_count,
            "is_binary": self.is_binary,
            "content_hash": self.content_hash,
            "parent_path": self.parent_path,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectFile:
        if not isinstance(data, dict):
            raise ProjectValidationError("data", f"Expected dict, got {type(data).__name__}.")

        prov_data = data.get("provenance")
        prov = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None

        return cls(
            relative_path=data.get("relative_path", ""),
            filename=data.get("filename", ""),
            extension=data.get("extension", ""),
            language=data.get("language"),
            size_bytes=int(data.get("size_bytes", 0)),
            line_count=int(data["line_count"]) if data.get("line_count") is not None else None,
            is_binary=bool(data.get("is_binary", False)),
            content_hash=data.get("content_hash"),
            parent_path=data.get("parent_path"),
            provenance=prov,
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# 3. ProjectDirectory & ProjectStructure
# -----------------------------------------------------------------------------

@dataclass
class ProjectDirectory:
    """
    Structured directory node representing a folder within the project hierarchy.
    """
    path: str
    name: str = ""
    parent_path: Optional[str] = None
    child_dir_paths: list[str] = field(default_factory=list)
    child_file_paths: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.path is None or not isinstance(self.path, str):
            raise ProjectValidationError("path", "Directory path must be a string.")

        self.path = normalize_project_path(self.path)

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
    def from_dict(cls, data: dict[str, Any]) -> ProjectDirectory:
        if not isinstance(data, dict):
            raise ProjectValidationError("data", f"Expected dict, got {type(data).__name__}.")

        return cls(
            path=data.get("path", ""),
            name=data.get("name", ""),
            parent_path=data.get("parent_path"),
            child_dir_paths=list(data.get("child_dir_paths", [])),
            child_file_paths=list(data.get("child_file_paths", [])),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ProjectStructure:
    """
    Bounded structural representation of the workspace directory tree and files.
    """
    root_path: str = ""
    directories: list[ProjectDirectory] = field(default_factory=list)
    files: list[ProjectFile] = field(default_factory=list)
    total_files: int = 0
    total_directories: int = 0
    max_depth_reached: int = 0
    is_truncated: bool = False
    truncation_reason: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.root_path = normalize_project_path(self.root_path) if self.root_path else ""
        if not self.total_files:
            self.total_files = len(self.files)
        if not self.total_directories:
            self.total_directories = len(self.directories)

    def add_file(self, file: ProjectFile) -> None:
        """Add a file node and update tree metrics."""
        self.files.append(file)
        self.total_files = len(self.files)
        depth = len(file.relative_path.split("/")) if file.relative_path else 0
        if depth > self.max_depth_reached:
            self.max_depth_reached = depth

    def add_directory(self, dir_node: ProjectDirectory) -> None:
        """Add a directory node and update tree metrics."""
        self.directories.append(dir_node)
        self.total_directories = len(self.directories)
        depth = len(dir_node.path.split("/")) if dir_node.path else 0
        if depth > self.max_depth_reached:
            self.max_depth_reached = depth

    def get_file(self, relative_path: str) -> Optional[ProjectFile]:
        """Look up a file by normalized relative path."""
        norm = normalize_project_path(relative_path)
        for f in self.files:
            if f.relative_path == norm:
                return f
        return None

    def get_directory(self, path: str) -> Optional[ProjectDirectory]:
        """Look up a directory by normalized relative path."""
        norm = normalize_project_path(path)
        for d in self.directories:
            if d.path == norm:
                return d
        return None

    def get_children(self, dir_path: str = "") -> tuple[list[ProjectDirectory], list[ProjectFile]]:
        """Return direct child directories and files under the given directory path."""
        norm = normalize_project_path(dir_path)
        target_parent = norm if norm else None

        child_dirs = [d for d in self.directories if d.parent_path == target_parent and d.path != norm]
        child_files = [f for f in self.files if f.parent_path == target_parent]
        return child_dirs, child_files

    def __contains__(self, path: str) -> bool:
        """Check whether a path exists as a file or directory in this structure."""
        return self.get_file(path) is not None or self.get_directory(path) is not None

    @property
    def file_paths(self) -> set[str]:
        """Set of all normalized relative file paths in this structure."""
        return {f.relative_path for f in self.files}

    @property
    def directory_paths(self) -> set[str]:
        """Set of all normalized directory paths in this structure."""
        return {d.path for d in self.directories}

    @property
    def truncated(self) -> bool:
        """Convenience alias for is_truncated."""
        return self.is_truncated

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
    def from_dict(cls, data: dict[str, Any]) -> ProjectStructure:
        if not isinstance(data, dict):
            raise ProjectValidationError("data", f"Expected dict, got {type(data).__name__}.")

        dirs = [ProjectDirectory.from_dict(d) for d in data.get("directories", [])]
        fls = [ProjectFile.from_dict(f) for f in data.get("files", [])]
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
# 4. ProjectSourceMaterial
# -----------------------------------------------------------------------------

@dataclass
class ProjectSourceMaterial:
    """
    Extracted file content or bounded code snippet from within the project.
    Directly converts to standard CrawlerReport contracts (RawSourceReference, EvidenceItem).
    """
    material_id: str
    file_path: str
    content: str = ""
    raw_bytes: Optional[bytes] = None
    line_range: Optional[LineRange] = None
    language: Optional[str] = None
    content_hash: str = ""
    is_binary: bool = False
    structural_metadata: dict[str, Any] = field(default_factory=dict)
    provenance: Optional[EvidenceProvenance] = None
    retrieved_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.material_id or not isinstance(self.material_id, str) or not self.material_id.strip():
            raise ProjectValidationError("material_id", "Material ID cannot be empty.")
        if not self.file_path or not isinstance(self.file_path, str) or not self.file_path.strip():
            raise ProjectValidationError("file_path", "File path cannot be empty.")

        self.file_path = normalize_project_path(self.file_path)

        if not self.language and not self.is_binary:
            self.language = detect_file_language(self.file_path)

        if not self.content_hash:
            if self.content:
                self.content_hash = compute_sha256(self.content)
            elif self.raw_bytes:
                self.content_hash = compute_sha256(self.raw_bytes)

    @classmethod
    def generate_material_id(
        cls,
        project_id: str,
        file_path: str,
        line_range: Optional[LineRange] = None,
    ) -> str:
        """Deterministically generate material ID from project, path, and line range."""
        lr_str = f"{line_range.start_line}-{line_range.end_line}" if line_range else "full"
        norm = normalize_project_path(file_path)
        seed = f"{project_id}:{norm}:{lr_str}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
        return f"mat-{digest}"

    @property
    def source_ref(self) -> str:
        """Canonical URI reference for project source material."""
        line_frag = f"#L{self.line_range.start_line}-L{self.line_range.end_line}" if self.line_range else ""
        return f"project://{self.file_path}{line_frag}"

    @property
    def size_bytes(self) -> int:
        """Byte size of the raw or encoded material content."""
        if self.raw_bytes is not None:
            return len(self.raw_bytes)
        return len(self.content.encode("utf-8"))

    @property
    def line_count(self) -> int:
        """Line count of the text content."""
        return len(self.content.splitlines()) if self.content else 0

    def to_raw_source_reference(self) -> RawSourceReference:
        """Convert into standard RawSourceReference for CrawlerReport integration."""
        meta: dict[str, Any] = {
            "material_id": self.material_id,
            "file_path": self.file_path,
            "language": self.language,
            "is_binary": self.is_binary,
            "line_range": self.line_range.to_dict() if self.line_range else None,
            "content_hash": self.content_hash,
            "structural_metadata": dict(self.structural_metadata),
        }
        meta.update(self.metadata)

        return RawSourceReference(
            url_or_ref=self.source_ref,
            title=f"Project File: {self.file_path}",
            publisher="local_project",
            source_type=SourceType.PRIMARY_SOURCE,
            checksum=self.content_hash,
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
        """Convert into standard EvidenceItem collection."""
        prov = EvidenceProvenance(
            request_id=request_id,
            crawler_task_id=crawler_task_id,
            crawler_id=crawler_id,
            question_id=question_id,
            source_ref=self.source_ref,
            correlation_id=correlation_id,
            captured_at=self.retrieved_at,
        )

        title_desc = f"Project file {self.file_path}"
        if self.line_range:
            title_desc += f" (L{self.line_range.start_line}-L{self.line_range.end_line})"

        snippet_text = self.content[:4000] if not self.is_binary else f"[Binary content: {self.file_path}]"

        meta: dict[str, Any] = {
            "material_id": self.material_id,
            "file_path": self.file_path,
            "language": self.language,
            "is_binary": self.is_binary,
            "line_range": self.line_range.to_dict() if self.line_range else None,
            "content_hash": self.content_hash,
            "structural_metadata": dict(self.structural_metadata),
        }
        meta.update(self.metadata)

        evidence = EvidenceItem(
            evidence_id=f"ev-proj-{self.material_id}",
            provenance=prov,
            extracted_fact=title_desc,
            content_snippet=snippet_text,
            classification=FactClassification.FACT,
            confidence=ResearchConfidence.WELL_SUPPORTED,
            reliability_score=0.95,
            source_type=SourceType.PRIMARY_SOURCE,
            checksum=self.content_hash,
            metadata=meta,
        )
        return [evidence]

    def to_dict(self) -> dict[str, Any]:
        return {
            "material_id": self.material_id,
            "file_path": self.file_path,
            "content": self.content,
            "line_range": self.line_range.to_dict() if self.line_range else None,
            "language": self.language,
            "content_hash": self.content_hash,
            "is_binary": self.is_binary,
            "structural_metadata": dict(self.structural_metadata),
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "retrieved_at": self.retrieved_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectSourceMaterial:
        if not isinstance(data, dict):
            raise ProjectValidationError("data", f"Expected dict, got {type(data).__name__}.")

        lr_data = data.get("line_range")
        line_range = LineRange.from_dict(lr_data) if isinstance(lr_data, dict) else None
        prov_data = data.get("provenance")
        prov = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None

        return cls(
            material_id=data.get("material_id", f"mat-{uuid.uuid4().hex[:8]}"),
            file_path=data.get("file_path", ""),
            content=data.get("content", ""),
            line_range=line_range,
            language=data.get("language"),
            content_hash=data.get("content_hash", ""),
            is_binary=bool(data.get("is_binary", False)),
            structural_metadata=dict(data.get("structural_metadata", {})),
            provenance=prov,
            retrieved_at=data.get("retrieved_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# 5. ProjectSymbol
# -----------------------------------------------------------------------------

@dataclass
class ProjectSymbol:
    """
    Structured representation of a code symbol (class, function, variable, etc.) in the project.
    """
    name: str
    symbol_type: SymbolKind = SymbolKind.UNKNOWN
    file_path: str = ""
    line_range: Optional[LineRange] = None
    parent_symbol: Optional[str] = None
    structural_relationships: list[str] = field(default_factory=list)
    signature: Optional[str] = None
    docstring: Optional[str] = None
    visibility: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.name or not isinstance(self.name, str) or not self.name.strip():
            raise ProjectValidationError("name", "Symbol name cannot be empty.")
        self.name = self.name.strip()

        if self.file_path:
            self.file_path = normalize_project_path(self.file_path)

        if isinstance(self.symbol_type, str):
            try:
                self.symbol_type = SymbolKind(self.symbol_type.lower())
            except (ValueError, TypeError):
                self.symbol_type = SymbolKind.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "symbol_type": self.symbol_type.value if hasattr(self.symbol_type, "value") else str(self.symbol_type),
            "file_path": self.file_path,
            "line_range": self.line_range.to_dict() if self.line_range else None,
            "parent_symbol": self.parent_symbol,
            "structural_relationships": list(self.structural_relationships),
            "signature": self.signature,
            "docstring": self.docstring,
            "visibility": self.visibility,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectSymbol:
        if not isinstance(data, dict):
            raise ProjectValidationError("data", f"Expected dict, got {type(data).__name__}.")

        lr_data = data.get("line_range")
        line_range = LineRange.from_dict(lr_data) if isinstance(lr_data, dict) else None

        st_raw = data.get("symbol_type", SymbolKind.UNKNOWN.value)
        try:
            st = SymbolKind(st_raw)
        except (ValueError, TypeError):
            st = SymbolKind.UNKNOWN

        return cls(
            name=data.get("name", ""),
            symbol_type=st,
            file_path=data.get("file_path", ""),
            line_range=line_range,
            parent_symbol=data.get("parent_symbol"),
            structural_relationships=list(data.get("structural_relationships", [])),
            signature=data.get("signature"),
            docstring=data.get("docstring"),
            visibility=data.get("visibility"),
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# 6. ProjectConfigurationMetadata
# -----------------------------------------------------------------------------

_SENSITIVE_CONFIG_KEY_PATTERNS: Sequence[re.Pattern] = (
    re.compile(r".*(password|secret|token|api_key|private_key|auth|credential).*", re.IGNORECASE),
)


def mask_sensitive_config(data: dict[str, Any]) -> dict[str, Any]:
    """Sanitize configuration metadata by masking secret keys and sensitive values."""
    sanitized: dict[str, Any] = {}
    for k, v in data.items():
        if any(pat.match(str(k)) for pat in _SENSITIVE_CONFIG_KEY_PATTERNS):
            sanitized[k] = REDACTED_STR
        elif isinstance(v, dict):
            sanitized[k] = mask_sensitive_config(v)
        elif isinstance(v, list):
            sanitized[k] = [
                mask_sensitive_config(item) if isinstance(item, dict) else item
                for item in v
            ]
        elif isinstance(v, str) and ("://" in v and "@" in v):
            sanitized[k] = sanitize_url(v)
        else:
            sanitized[k] = v
    return sanitized


@dataclass
class ProjectConfigurationMetadata:
    """
    Project manifest or configuration file metadata.
    Enforces secret masking on all key-value entries.
    """
    config_path: str
    config_type: ProjectConfigType = ProjectConfigType.GENERIC
    safe_metadata: dict[str, Any] = field(default_factory=dict)
    content_hash: Optional[str] = None
    provenance: Optional[EvidenceProvenance] = None
    retrieved_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.config_path or not isinstance(self.config_path, str) or not self.config_path.strip():
            raise ProjectValidationError("config_path", "Configuration path cannot be empty.")

        self.config_path = normalize_project_path(self.config_path)

        if isinstance(self.config_type, str):
            self.config_type = ProjectConfigType.from_string(self.config_type)

        # Enforce secret masking on safe_metadata
        self.safe_metadata = mask_sensitive_config(self.safe_metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_path": self.config_path,
            "config_type": self.config_type.value,
            "safe_metadata": dict(self.safe_metadata),
            "content_hash": self.content_hash,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "retrieved_at": self.retrieved_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectConfigurationMetadata:
        if not isinstance(data, dict):
            raise ProjectValidationError("data", f"Expected dict, got {type(data).__name__}.")

        prov_data = data.get("provenance")
        prov = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None

        return cls(
            config_path=data.get("config_path", ""),
            config_type=ProjectConfigType.from_string(data.get("config_type", ProjectConfigType.GENERIC.value)),
            safe_metadata=dict(data.get("safe_metadata", {})),
            content_hash=data.get("content_hash"),
            provenance=prov,
            retrieved_at=data.get("retrieved_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# 7. ProjectDependencyMetadata
# -----------------------------------------------------------------------------

@dataclass
class ProjectDependencyMetadata:
    """
    Declared external package, library, or toolchain dependency.
    """
    name: str
    version_spec: Optional[str] = None
    manifest_source: str = ""
    dependency_type: ProjectDependencyType = ProjectDependencyType.RUNTIME
    is_dev: bool = False
    ecosystem: Optional[str] = None
    provenance: Optional[EvidenceProvenance] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.name or not isinstance(self.name, str) or not self.name.strip():
            raise ProjectValidationError("name", "Dependency name cannot be empty.")
        self.name = self.name.strip()

        if self.manifest_source:
            self.manifest_source = normalize_project_path(self.manifest_source)

        if isinstance(self.dependency_type, str):
            self.dependency_type = ProjectDependencyType.from_string(self.dependency_type)

        if self.dependency_type == ProjectDependencyType.DEV:
            self.is_dev = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version_spec": self.version_spec,
            "manifest_source": self.manifest_source,
            "dependency_type": self.dependency_type.value,
            "is_dev": self.is_dev,
            "ecosystem": self.ecosystem,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectDependencyMetadata:
        if not isinstance(data, dict):
            raise ProjectValidationError("data", f"Expected dict, got {type(data).__name__}.")

        prov_data = data.get("provenance")
        prov = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None

        return cls(
            name=data.get("name", ""),
            version_spec=data.get("version_spec"),
            manifest_source=data.get("manifest_source", ""),
            dependency_type=ProjectDependencyType.from_string(data.get("dependency_type", ProjectDependencyType.RUNTIME.value)),
            is_dev=bool(data.get("is_dev", False)),
            ecosystem=data.get("ecosystem"),
            provenance=prov,
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# 8. ProjectVCSContext
# -----------------------------------------------------------------------------

@dataclass
class ProjectVCSContext:
    """
    Version control snapshot context for the project workspace.
    Sanitizes remote URLs to ensure no user credentials are leaked.
    Exposes branch, revision, modified/deleted/untracked/staged/unstaged files, and clean/dirty state.
    """
    branch: Optional[str] = None
    revision: Optional[str] = None
    working_tree_state: ProjectVCSState = ProjectVCSState.UNKNOWN
    repository_id: Optional[str] = None
    modified_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)
    staged_files: list[str] = field(default_factory=list)
    unstaged_files: list[str] = field(default_factory=list)
    tag: Optional[str] = None
    commit_date: Optional[str] = None
    author: Optional[str] = None
    remote_url: Optional[str] = None
    vcs_type: str = "git"
    retrieved_at: str = field(default_factory=utc_now)
    provenance: Optional[EvidenceProvenance] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Normalize changed file lists and deduplicate preserving order
        self.modified_files = self._normalize_file_list(self.modified_files)
        self.deleted_files = self._normalize_file_list(self.deleted_files)
        self.untracked_files = self._normalize_file_list(self.untracked_files)
        self.staged_files = self._normalize_file_list(self.staged_files)
        self.unstaged_files = self._normalize_file_list(self.unstaged_files)

        if isinstance(self.working_tree_state, str):
            self.working_tree_state = ProjectVCSState.from_string(self.working_tree_state)
        elif self.working_tree_state == ProjectVCSState.UNKNOWN:
            if self.modified_files or self.deleted_files or self.staged_files:
                self.working_tree_state = ProjectVCSState.DIRTY
            elif self.untracked_files:
                self.working_tree_state = ProjectVCSState.UNTRACKED

        # Sanitize remote URL if basic auth credentials exist
        if self.remote_url and isinstance(self.remote_url, str):
            self.remote_url = sanitize_url(self.remote_url.strip())

    @staticmethod
    def _normalize_file_list(files: Sequence[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for f in files:
            if isinstance(f, str) and f.strip():
                try:
                    norm = normalize_project_path(f.strip())
                    if norm and norm not in seen:
                        seen.add(norm)
                        cleaned.append(norm)
                except Exception:
                    pass
        return cleaned

    @property
    def is_dirty(self) -> bool:
        """Convenience property indicating uncommitted modifications."""
        return (
            self.working_tree_state == ProjectVCSState.DIRTY
            or bool(self.modified_files)
            or bool(self.deleted_files)
            or bool(self.staged_files)
        )

    def to_evidence_item(
        self,
        request_id: str,
        crawler_task_id: str,
        crawler_id: str,
        question_id: str = "",
        correlation_id: str = "",
    ) -> EvidenceItem:
        """Convert VCS snapshot into a standard EvidenceItem."""
        prov = EvidenceProvenance(
            request_id=request_id,
            crawler_task_id=crawler_task_id,
            crawler_id=crawler_id,
            question_id=question_id,
            source_ref=f"vcs://{self.repository_id or 'project'}@{self.branch or 'HEAD'}",
            correlation_id=correlation_id,
            captured_at=self.retrieved_at,
        )
        rev_str = self.revision[:8] if self.revision else "unknown"
        fact = (
            f"VCS state ({self.vcs_type}): branch '{self.branch or 'detached'}', "
            f"rev '{rev_str}', state '{self.working_tree_state.value}' "
            f"(modified: {len(self.modified_files)}, untracked: {len(self.untracked_files)}, deleted: {len(self.deleted_files)})"
        )
        meta: dict[str, Any] = {
            "branch": self.branch,
            "revision": self.revision,
            "working_tree_state": self.working_tree_state.value,
            "is_dirty": self.is_dirty,
            "repository_id": self.repository_id,
            "modified_files": list(self.modified_files),
            "deleted_files": list(self.deleted_files),
            "untracked_files": list(self.untracked_files),
            "staged_files": list(self.staged_files),
            "unstaged_files": list(self.unstaged_files),
            "vcs_type": self.vcs_type,
            "remote_url": self.remote_url,
        }
        meta.update(self.metadata)

        content_seed = f"{self.branch}:{self.revision}:{self.working_tree_state.value}:{','.join(self.modified_files)}"
        return EvidenceItem(
            evidence_id=f"ev-vcs-{compute_sha256(content_seed)[:12]}",
            provenance=prov,
            extracted_fact=fact,
            content_snippet=fact,
            classification=FactClassification.FACT,
            confidence=ResearchConfidence.WELL_SUPPORTED,
            reliability_score=0.95,
            source_type=SourceType.REPOSITORY,
            checksum=compute_sha256(content_seed),
            metadata=meta,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "revision": self.revision,
            "working_tree_state": self.working_tree_state.value,
            "is_dirty": self.is_dirty,
            "repository_id": self.repository_id,
            "modified_files": list(self.modified_files),
            "deleted_files": list(self.deleted_files),
            "untracked_files": list(self.untracked_files),
            "staged_files": list(self.staged_files),
            "unstaged_files": list(self.unstaged_files),
            "tag": self.tag,
            "commit_date": self.commit_date,
            "author": self.author,
            "remote_url": self.remote_url,
            "vcs_type": self.vcs_type,
            "retrieved_at": self.retrieved_at,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectVCSContext:
        if not isinstance(data, dict):
            raise ProjectValidationError("data", f"Expected dict, got {type(data).__name__}.")

        prov_data = data.get("provenance")
        prov = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None

        return cls(
            branch=data.get("branch"),
            revision=data.get("revision"),
            working_tree_state=ProjectVCSState.from_string(data.get("working_tree_state", ProjectVCSState.UNKNOWN.value)),
            repository_id=data.get("repository_id"),
            modified_files=list(data.get("modified_files", [])),
            deleted_files=list(data.get("deleted_files", [])),
            untracked_files=list(data.get("untracked_files", [])),
            staged_files=list(data.get("staged_files", [])),
            unstaged_files=list(data.get("unstaged_files", [])),
            tag=data.get("tag"),
            commit_date=data.get("commit_date"),
            author=data.get("author"),
            remote_url=data.get("remote_url"),
            vcs_type=data.get("vcs_type", "git"),
            retrieved_at=data.get("retrieved_at", utc_now()),
            provenance=prov,
            metadata=dict(data.get("metadata", {})),
        )



# -----------------------------------------------------------------------------
# 8. ProjectContract
# -----------------------------------------------------------------------------

@dataclass
class ProjectContract:
    """
    Explicit project contract definition (interface, protocol, typed model,
    lifecycle enum, config schema, event contract, registry definition).
    """
    contract_id: str
    name: str
    contract_type: ProjectContractType
    file_path: str
    line_range: Optional[LineRange] = None
    members: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    signature: str = ""
    docstring: Optional[str] = None
    content_snippet: str = ""
    content_hash: str = ""
    provenance: Optional[EvidenceProvenance] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.contract_id:
            norm = normalize_project_path(self.file_path) if self.file_path else "unknown"
            self.contract_id = f"contract-{norm.replace('/', '_')}-{self.name}"
        if isinstance(self.contract_type, str):
            self.contract_type = ProjectContractType.from_string(self.contract_type)
        if self.file_path:
            self.file_path = normalize_project_path(self.file_path)
        if not self.content_hash and self.content_snippet:
            self.content_hash = compute_sha256(self.content_snippet)

    @property
    def source_ref(self) -> str:
        line_frag = f"#L{self.line_range.start_line}-L{self.line_range.end_line}" if self.line_range else ""
        return f"project://{self.file_path}{line_frag}"

    def to_evidence_item(
        self,
        request_id: str,
        crawler_task_id: str,
        crawler_id: str,
        question_id: str = "",
        correlation_id: str = "",
    ) -> EvidenceItem:
        prov = EvidenceProvenance(
            request_id=request_id,
            crawler_task_id=crawler_task_id,
            crawler_id=crawler_id,
            question_id=question_id,
            source_ref=self.source_ref,
            correlation_id=correlation_id,
            captured_at=utc_now(),
        )
        fact = f"{self.contract_type.value.capitalize()} contract '{self.name}' defined in {self.file_path}"
        snippet = self.content_snippet or self.docstring or self.signature or f"Contract {self.name}"

        meta: dict[str, Any] = {
            "contract_id": self.contract_id,
            "contract_type": self.contract_type.value,
            "file_path": self.file_path,
            "name": self.name,
            "bases": list(self.bases),
            "members": list(self.members),
            "signature": self.signature,
            "line_range": self.line_range.to_dict() if self.line_range else None,
            "content_hash": self.content_hash,
        }
        meta.update(self.metadata)

        return EvidenceItem(
            evidence_id=f"ev-contract-{self.contract_id}",
            provenance=prov,
            extracted_fact=fact,
            content_snippet=snippet[:2000],
            classification=FactClassification.FACT,
            confidence=ResearchConfidence.WELL_SUPPORTED,
            reliability_score=0.95,
            source_type=SourceType.PRIMARY_SOURCE,
            checksum=self.content_hash,
            metadata=meta,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "name": self.name,
            "contract_type": self.contract_type.value,
            "file_path": self.file_path,
            "line_range": self.line_range.to_dict() if self.line_range else None,
            "members": list(self.members),
            "bases": list(self.bases),
            "signature": self.signature,
            "docstring": self.docstring,
            "content_snippet": self.content_snippet,
            "content_hash": self.content_hash,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectContract:
        if not isinstance(data, dict):
            raise ProjectValidationError("data", f"Expected dict, got {type(data).__name__}.")

        lr_data = data.get("line_range")
        line_range = LineRange.from_dict(lr_data) if isinstance(lr_data, dict) else None

        prov_data = data.get("provenance")
        prov = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None

        return cls(
            contract_id=data.get("contract_id", ""),
            name=data.get("name", ""),
            contract_type=ProjectContractType.from_string(data.get("contract_type", ProjectContractType.GENERIC.value)),
            file_path=data.get("file_path", ""),
            line_range=line_range,
            members=list(data.get("members", [])),
            bases=list(data.get("bases", [])),
            signature=data.get("signature", ""),
            docstring=data.get("docstring"),
            content_snippet=data.get("content_snippet", ""),
            content_hash=data.get("content_hash", ""),
            provenance=prov,
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# 9. ProjectDocumentation
# -----------------------------------------------------------------------------

@dataclass
class ProjectDocSection:
    """
    Structured section of a project documentation file.
    """
    heading: str
    level: int
    heading_path: list[str] = field(default_factory=list)
    content: str = ""
    line_range: Optional[LineRange] = None
    code_blocks: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "heading": self.heading,
            "level": self.level,
            "heading_path": list(self.heading_path),
            "content": self.content,
            "line_range": self.line_range.to_dict() if self.line_range else None,
            "code_blocks": list(self.code_blocks),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectDocSection:
        if not isinstance(data, dict):
            raise ProjectValidationError("data", f"Expected dict, got {type(data).__name__}.")
        lr_data = data.get("line_range")
        line_range = LineRange.from_dict(lr_data) if isinstance(lr_data, dict) else None
        return cls(
            heading=data.get("heading", ""),
            level=int(data.get("level", 1)),
            heading_path=list(data.get("heading_path", [])),
            content=data.get("content", ""),
            line_range=line_range,
            code_blocks=list(data.get("code_blocks", [])),
        )


@dataclass
class ProjectDocumentationMetadata:
    """
    Extracted documentation metadata (README, architecture docs, ADRs, guides).
    """
    file_path: str
    doc_type: str = "general"
    title: str = ""
    sections: list[ProjectDocSection] = field(default_factory=list)
    content_hash: str = ""
    provenance: Optional[EvidenceProvenance] = None
    retrieved_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.file_path:
            self.file_path = normalize_project_path(self.file_path)

    @property
    def source_ref(self) -> str:
        return f"project://{self.file_path}"

    def to_evidence_items(
        self,
        request_id: str,
        crawler_task_id: str,
        crawler_id: str,
        question_id: str = "",
        correlation_id: str = "",
    ) -> list[EvidenceItem]:
        prov = EvidenceProvenance(
            request_id=request_id,
            crawler_task_id=crawler_task_id,
            crawler_id=crawler_id,
            question_id=question_id,
            source_ref=self.source_ref,
            correlation_id=correlation_id,
            captured_at=self.retrieved_at,
        )
        fact = f"Documentation ({self.doc_type}) '{self.title or self.file_path}' in {self.file_path}"
        snippet = "\n".join(s.content for s in self.sections[:3]) if self.sections else f"Doc {self.file_path}"

        meta: dict[str, Any] = {
            "file_path": self.file_path,
            "doc_type": self.doc_type,
            "title": self.title,
            "section_count": len(self.sections),
            "content_hash": self.content_hash,
        }
        meta.update(self.metadata)

        evidence = EvidenceItem(
            evidence_id=f"ev-doc-{self.file_path.replace('/', '_')}",
            provenance=prov,
            extracted_fact=fact,
            content_snippet=snippet[:2000],
            classification=FactClassification.SOURCE_CLAIM,
            confidence=ResearchConfidence.SUPPORTED,
            reliability_score=0.9,
            source_type=SourceType.OFFICIAL_DOCUMENTATION,
            checksum=self.content_hash,
            metadata=meta,
        )
        return [evidence]

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "doc_type": self.doc_type,
            "title": self.title,
            "sections": [s.to_dict() for s in self.sections],
            "content_hash": self.content_hash,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "retrieved_at": self.retrieved_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectDocumentationMetadata:
        if not isinstance(data, dict):
            raise ProjectValidationError("data", f"Expected dict, got {type(data).__name__}.")

        prov_data = data.get("provenance")
        prov = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None

        sections = [ProjectDocSection.from_dict(s) for s in data.get("sections", [])]

        return cls(
            file_path=data.get("file_path", ""),
            doc_type=data.get("doc_type", "general"),
            title=data.get("title", ""),
            sections=sections,
            content_hash=data.get("content_hash", ""),
            provenance=prov,
            retrieved_at=data.get("retrieved_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# Root Aggregate: ProjectContext
# -----------------------------------------------------------------------------

@dataclass
class ProjectContext:
    """
    Consolidated project inspection snapshot container.
    Integrates all extracted project facets and seamlessly converts into
    standard CrawlerReport data (RawSourceReference and EvidenceItem collections).
    """
    identity: ProjectIdentity
    structure: Optional[ProjectStructure] = None
    source_materials: list[ProjectSourceMaterial] = field(default_factory=list)
    symbols: list[ProjectSymbol] = field(default_factory=list)
    contracts: list[ProjectContract] = field(default_factory=list)
    documentation: list[ProjectDocumentationMetadata] = field(default_factory=list)
    configurations: list[ProjectConfigurationMetadata] = field(default_factory=list)
    dependencies: list[ProjectDependencyMetadata] = field(default_factory=list)
    vcs: Optional[ProjectVCSContext] = None
    provenance: Optional[EvidenceProvenance] = None
    retrieved_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_source_material(self, material: ProjectSourceMaterial) -> None:
        """Append extracted source material snippet."""
        self.source_materials.append(material)

    def add_symbol(self, symbol: ProjectSymbol) -> None:
        """Append extracted code symbol."""
        self.symbols.append(symbol)

    def add_contract(self, contract: ProjectContract) -> None:
        """Append extracted explicit project contract."""
        self.contracts.append(contract)

    def add_documentation(self, doc: ProjectDocumentationMetadata) -> None:
        """Append extracted documentation metadata."""
        self.documentation.append(doc)

    def add_configuration(self, config: ProjectConfigurationMetadata) -> None:
        """Append configuration metadata."""
        self.configurations.append(config)

    def add_dependency(self, dep: ProjectDependencyMetadata) -> None:
        """Append declared dependency metadata."""
        self.dependencies.append(dep)

    def to_raw_source_references(self) -> list[RawSourceReference]:
        """Convert all project source materials into standard RawSourceReferences."""
        return [mat.to_raw_source_reference() for mat in self.source_materials]

    def to_evidence_items(
        self,
        request_id: str,
        crawler_task_id: str,
        crawler_id: str,
        question_id: str = "",
        correlation_id: str = "",
        include_vcs: bool = False,
    ) -> list[EvidenceItem]:
        """Aggregate evidence items across all project materials, contracts, and documentation."""
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
        for contract in self.contracts:
            items.append(
                contract.to_evidence_item(
                    request_id=request_id,
                    crawler_task_id=crawler_task_id,
                    crawler_id=crawler_id,
                    question_id=question_id,
                    correlation_id=correlation_id,
                )
            )
        for doc in self.documentation:
            items.extend(
                doc.to_evidence_items(
                    request_id=request_id,
                    crawler_task_id=crawler_task_id,
                    crawler_id=crawler_id,
                    question_id=question_id,
                    correlation_id=correlation_id,
                )
            )
        if include_vcs and self.vcs:
            items.append(
                self.vcs.to_evidence_item(
                    request_id=request_id,
                    crawler_task_id=crawler_task_id,
                    crawler_id=crawler_id,
                    question_id=question_id,
                    correlation_id=correlation_id,
                )
            )
        return items

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "structure": self.structure.to_dict() if self.structure else None,
            "source_materials": [m.to_dict() for m in self.source_materials],
            "symbols": [s.to_dict() for s in self.symbols],
            "contracts": [c.to_dict() for c in self.contracts],
            "documentation": [d.to_dict() for d in self.documentation],
            "configurations": [c.to_dict() for c in self.configurations],
            "dependencies": [d.to_dict() for d in self.dependencies],
            "vcs": self.vcs.to_dict() if self.vcs else None,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "retrieved_at": self.retrieved_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectContext:
        if not isinstance(data, dict):
            raise ProjectValidationError("data", f"Expected dict, got {type(data).__name__}.")

        identity = ProjectIdentity.from_dict(data["identity"])
        struct_data = data.get("structure")
        structure = ProjectStructure.from_dict(struct_data) if isinstance(struct_data, dict) else None

        materials = [ProjectSourceMaterial.from_dict(m) for m in data.get("source_materials", [])]
        symbols = [ProjectSymbol.from_dict(s) for s in data.get("symbols", [])]
        contracts = [ProjectContract.from_dict(c) for c in data.get("contracts", [])]
        documentation = [ProjectDocumentationMetadata.from_dict(d) for d in data.get("documentation", [])]
        configs = [ProjectConfigurationMetadata.from_dict(c) for c in data.get("configurations", [])]
        deps = [ProjectDependencyMetadata.from_dict(d) for d in data.get("dependencies", [])]

        vcs_data = data.get("vcs")
        vcs = ProjectVCSContext.from_dict(vcs_data) if isinstance(vcs_data, dict) else None

        prov_data = data.get("provenance")
        prov = EvidenceProvenance.from_dict(prov_data) if isinstance(prov_data, dict) else None

        return cls(
            identity=identity,
            structure=structure,
            source_materials=materials,
            symbols=symbols,
            contracts=contracts,
            documentation=documentation,
            configurations=configs,
            dependencies=deps,
            vcs=vcs,
            provenance=prov,
            retrieved_at=data.get("retrieved_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )


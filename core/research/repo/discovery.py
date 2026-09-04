"""
Repository Discovery Engine (Phase 1 / Part 5 / Step 3).

Discovers repository identity, default revision, bounded tree hierarchy, detected languages,
README documents, project manifests, and directory structure.
Answers "What does this repository contain?" without executing repository code or making
subjective semantic relevance decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import logging
import os
import posixpath
import re
from typing import Any, Callable, Optional

from core.research.errors import (
    RepositoryCancelledError,
    RepositoryError,
    RepositoryNotFoundError,
    RepositoryProviderError,
    RepositoryResourceLimitError,
    RepositoryRevisionNotFoundError,
    RepositoryTimeoutError,
    RepositoryValidationError,
)
from core.research.repo.mock_provider import MockRepositoryProvider
from core.research.repo.models import (
    RepositoryDirectory,
    RepositoryFile,
    RepositoryIdentity,
    RepositoryProviderType,
    RepositoryRevision,
    RepositorySource,
    RepositoryTree,
    detect_file_language,
    normalize_repo_path,
    utc_now,
)
from core.research.repo.provider import (
    RepositoryFetchLimits,
    RepositoryProvider,
)

logger = logging.getLogger("AutonomOS.Research.RepositoryDiscovery")

# -----------------------------------------------------------------------------
# Well-Known Manifests, Documentation, and Configuration Dictionaries
# -----------------------------------------------------------------------------

KNOWN_MANIFEST_ECOSYSTEM_MAP: dict[str, str] = {
    # JavaScript / TypeScript / Node
    "package.json": "javascript",
    "package-lock.json": "javascript",
    "yarn.lock": "javascript",
    "pnpm-lock.yaml": "javascript",
    "pnpm-workspace.yaml": "javascript",
    "tsconfig.json": "typescript",
    "jsconfig.json": "javascript",
    "deno.json": "typescript",
    "deno.jsonc": "typescript",
    "lerna.json": "javascript",
    "turbo.json": "javascript",
    # Python
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "Pipfile": "python",
    "Pipfile.lock": "python",
    "poetry.lock": "python",
    "environment.yml": "python",
    "tox.ini": "python",
    # Rust
    "Cargo.toml": "rust",
    "Cargo.lock": "rust",
    # Go
    "go.mod": "go",
    "go.sum": "go",
    "Gopkg.toml": "go",
    "Gopkg.lock": "go",
    # Java / Kotlin / JVM
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "kotlin",
    "settings.gradle": "java",
    "settings.gradle.kts": "kotlin",
    "gradlew": "java",
    # C / C++ / Native
    "CMakeLists.txt": "cpp",
    "Makefile": "c",
    "makefile": "c",
    "GNUmakefile": "c",
    "meson.build": "cpp",
    "conanfile.txt": "cpp",
    "conanfile.py": "cpp",
    # Ruby
    "Gemfile": "ruby",
    "Gemfile.lock": "ruby",
    "Rakefile": "ruby",
    # PHP
    "composer.json": "php",
    "composer.lock": "php",
    # .NET / C#
    "nuget.config": "csharp",
    "Directory.Build.props": "csharp",
    # Swift
    "Package.swift": "swift",
    "Package.resolved": "swift",
    # Elixir / Erlang
    "mix.exs": "elixir",
    "mix.lock": "elixir",
    "rebar.config": "erlang",
    # Containers & Infrastructure
    "Dockerfile": "docker",
    "Containerfile": "docker",
    "docker-compose.yml": "docker",
    "docker-compose.yaml": "docker",
    "Vagrantfile": "vagrant",
    "Vagrantfile.local": "vagrant",
    # CI/CD Workflows
    ".gitlab-ci.yml": "ci",
    "azure-pipelines.yml": "ci",
    "Jenkinsfile": "ci",
}

KNOWN_DOCS_DIRS = {"docs", "doc", "documentation", "wiki", "man", "guides", "reference", "manual"}


def is_manifest_file(path_or_name: str) -> bool:
    """Check if a path or filename corresponds to a recognized project manifest or config."""
    if not path_or_name:
        return False
    base = posixpath.basename(path_or_name)
    if base in KNOWN_MANIFEST_ECOSYSTEM_MAP:
        return True
    # Check .NET project files (.csproj, .fsproj, .sln)
    if base.endswith((".csproj", ".fsproj", ".sln", ".vcxproj")):
        return True
    # Check GitHub actions workflow files
    if "/.github/workflows/" in path_or_name and path_or_name.endswith((".yml", ".yaml")):
        return True
    return False


def classify_manifest_ecosystem(path_or_name: str) -> Optional[str]:
    """Return the ecosystem (e.g. 'python', 'rust', 'javascript') for a manifest file."""
    if not path_or_name:
        return None
    base = posixpath.basename(path_or_name)
    if base in KNOWN_MANIFEST_ECOSYSTEM_MAP:
        return KNOWN_MANIFEST_ECOSYSTEM_MAP[base]
    if base.endswith((".csproj", ".fsproj", ".sln")):
        return "csharp"
    if "/.github/workflows/" in path_or_name:
        return "ci"
    return None


def is_readme_file(path_or_name: str) -> bool:
    """Check if a path or filename represents a project README."""
    if not path_or_name:
        return False
    base = posixpath.basename(path_or_name).lower()
    return base.startswith("readme")


def is_documentation_file(path_or_name: str) -> bool:
    """Check if a path or filename is part of repository documentation."""
    if not path_or_name:
        return False
    norm = normalize_repo_path(path_or_name)
    parts = norm.split("/")
    # Check if inside a docs directory
    if any(p.lower() in KNOWN_DOCS_DIRS for p in parts[:-1]):
        return True
    # Check if documentation markup file (and not a README)
    _, ext = posixpath.splitext(parts[-1])
    if ext.lower() in (".md", ".markdown", ".rst", ".adoc", ".asciidoc", ".tex"):
        return True
    return False


# -----------------------------------------------------------------------------
# Discovery Options & Results
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class RepositoryDiscoveryOptions:
    """
    Configuration options and boundary limits for repository discovery.
    """
    max_files: int = 1000
    max_directories: int = 200
    max_depth: int = 10
    max_total_bytes: int = 50_000_000
    timeout_seconds: float = 30.0
    exclude_patterns: tuple[str, ...] = (
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        ".env",
        ".DS_Store",
    )
    include_manifest_contents: bool = False

    def __post_init__(self):
        if self.max_files <= 0:
            raise RepositoryValidationError("max_files", "max_files must be > 0.")
        if self.max_directories <= 0:
            raise RepositoryValidationError("max_directories", "max_directories must be > 0.")
        if self.max_depth <= 0:
            raise RepositoryValidationError("max_depth", "max_depth must be > 0.")
        if self.timeout_seconds <= 0:
            raise RepositoryValidationError("timeout_seconds", "timeout_seconds must be > 0.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_files": self.max_files,
            "max_directories": self.max_directories,
            "max_depth": self.max_depth,
            "max_total_bytes": self.max_total_bytes,
            "timeout_seconds": self.timeout_seconds,
            "exclude_patterns": list(self.exclude_patterns),
            "include_manifest_contents": self.include_manifest_contents,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryDiscoveryOptions:
        return cls(
            max_files=int(data.get("max_files", 1000)),
            max_directories=int(data.get("max_directories", 200)),
            max_depth=int(data.get("max_depth", 10)),
            max_total_bytes=int(data.get("max_total_bytes", 50_000_000)),
            timeout_seconds=float(data.get("timeout_seconds", 30.0)),
            exclude_patterns=tuple(data.get("exclude_patterns", ())),
            include_manifest_contents=bool(data.get("include_manifest_contents", False)),
        )


@dataclass
class DiscoveredRepository:
    """
    Deterministic discovery result establishing the structural inventory of a repository.
    """
    identity: RepositoryIdentity
    revision: RepositoryRevision
    tree: RepositoryTree
    readme_files: list[RepositoryFile] = field(default_factory=list)
    manifest_files: list[RepositoryFile] = field(default_factory=list)
    documentation_files: list[RepositoryFile] = field(default_factory=list)
    primary_language: Optional[str] = None
    detected_languages: dict[str, int] = field(default_factory=dict)
    total_files: int = 0
    total_directories: int = 0
    total_bytes: int = 0
    is_truncated: bool = False
    truncation_reason: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    discovered_at: str = field(default_factory=utc_now)

    def get_primary_readme(self) -> Optional[RepositoryFile]:
        """Return the most authoritative README file (root README preferred)."""
        if not self.readme_files:
            return None
        # Root READMEs have no slashes in path
        for rf in self.readme_files:
            if "/" not in rf.path:
                return rf
        return self.readme_files[0]

    def get_primary_manifest(self) -> Optional[RepositoryFile]:
        """Return the primary root manifest file (e.g. pyproject.toml, package.json, Cargo.toml)."""
        if not self.manifest_files:
            return None
        for mf in self.manifest_files:
            if "/" not in mf.path:
                return mf
        return self.manifest_files[0]

    def get_manifests_by_ecosystem(self, ecosystem: str) -> list[RepositoryFile]:
        """Filter discovered manifest files by language ecosystem (e.g. 'python', 'rust')."""
        target = ecosystem.strip().lower()
        return [
            mf for mf in self.manifest_files
            if classify_manifest_ecosystem(mf.path) == target
        ]

    def get_file(self, path: str) -> Optional[RepositoryFile]:
        """Look up a file in the discovered repository tree."""
        return self.tree.get_file(path)

    def to_source_model(self) -> RepositorySource:
        """Convert discovery result into standard RepositorySource aggregate model."""
        return RepositorySource(
            identity=self.identity,
            revision=self.revision,
            tree=self.tree,
            source_materials=[],
            metadata={
                "discovered_at": self.discovered_at,
                "primary_language": self.primary_language,
                "detected_languages": dict(self.detected_languages),
                "readme_count": len(self.readme_files),
                "manifest_count": len(self.manifest_files),
                "is_truncated": self.is_truncated,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "revision": self.revision.to_dict(),
            "tree": self.tree.to_dict(),
            "readme_files": [f.to_dict() for f in self.readme_files],
            "manifest_files": [f.to_dict() for f in self.manifest_files],
            "documentation_files": [f.to_dict() for f in self.documentation_files],
            "primary_language": self.primary_language,
            "detected_languages": dict(self.detected_languages),
            "total_files": self.total_files,
            "total_directories": self.total_directories,
            "total_bytes": self.total_bytes,
            "is_truncated": self.is_truncated,
            "truncation_reason": self.truncation_reason,
            "metadata": dict(self.metadata),
            "discovered_at": self.discovered_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscoveredRepository:
        ident = RepositoryIdentity.from_dict(data.get("identity", {}))
        rev = RepositoryRevision.from_dict(data.get("revision", {}))
        tree = RepositoryTree.from_dict(data.get("tree", {}))
        readmes = [RepositoryFile.from_dict(f) for f in data.get("readme_files", [])]
        manifests = [RepositoryFile.from_dict(f) for f in data.get("manifest_files", [])]
        docs = [RepositoryFile.from_dict(f) for f in data.get("documentation_files", [])]

        return cls(
            identity=ident,
            revision=rev,
            tree=tree,
            readme_files=readmes,
            manifest_files=manifests,
            documentation_files=docs,
            primary_language=data.get("primary_language"),
            detected_languages=dict(data.get("detected_languages", {})),
            total_files=int(data.get("total_files", 0)),
            total_directories=int(data.get("total_directories", 0)),
            total_bytes=int(data.get("total_bytes", 0)),
            is_truncated=bool(data.get("is_truncated", False)),
            truncation_reason=data.get("truncation_reason"),
            metadata=dict(data.get("metadata", {})),
            discovered_at=data.get("discovered_at", utc_now()),
        )


# -----------------------------------------------------------------------------
# Discovery Engine
# -----------------------------------------------------------------------------

class RepositoryDiscoveryEngine:
    """
    Deterministic structural discovery engine for code repositories.
    Interacts strictly through the RepositoryProvider abstraction.
    """

    def __init__(
        self,
        provider: Optional[RepositoryProvider] = None,
        default_options: Optional[RepositoryDiscoveryOptions] = None,
    ):
        self.provider = provider or MockRepositoryProvider()
        self.default_options = default_options or RepositoryDiscoveryOptions()

    def discover(
        self,
        target: str | RepositoryIdentity,
        revision: Optional[str] = None,
        options: Optional[RepositoryDiscoveryOptions] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> DiscoveredRepository:
        """
        Execute structural repository discovery against the configured provider.

        Args:
            target: Repository URL, path, or RepositoryIdentity.
            revision: Optional target branch, tag, or commit SHA.
            options: Optional override discovery limits and options.
            is_cancelled: Optional cancellation predicate.

        Returns:
            DiscoveredRepository containing identity, revision, tree, manifests, and languages.

        Raises:
            RepositoryNotFoundError: If the repository does not exist.
            RepositoryRevisionNotFoundError: If the requested revision does not exist.
            RepositoryTimeoutError: If the operation exceeds timeout.
            RepositoryCancelledError: If cancelled by caller.
            RepositoryProviderError: On underlying provider failure.
        """
        opts = options or self.default_options
        target_str = self.provider.extract_repo_target(target)

        # 1. Cancellation check
        self.provider.check_cancellation(is_cancelled, target_str, "repository_discovery")

        # 2. Resolve Repository Identity
        if isinstance(target, RepositoryIdentity):
            ident = target
        else:
            ident = self.provider.resolve_identity(
                target_str,
                timeout_seconds=opts.timeout_seconds,
                is_cancelled=is_cancelled,
            )

        # 3. Enrich with metadata if available
        try:
            meta_ident = self.provider.get_metadata(
                ident,
                timeout_seconds=opts.timeout_seconds,
                is_cancelled=is_cancelled,
            )
            if meta_ident and meta_ident.description and not ident.description:
                ident.description = meta_ident.description
            if meta_ident and meta_ident.primary_language and not ident.primary_language:
                ident.primary_language = meta_ident.primary_language
        except Exception as e:
            logger.debug(f"Metadata enrichment skipped for '{target_str}': {e}")

        # 4. Resolve Revision Context
        rev = self.provider.get_revision(
            ident,
            revision=revision,
            timeout_seconds=opts.timeout_seconds,
            is_cancelled=is_cancelled,
        )

        # 5. Retrieve Bounded Repository Tree
        raw_tree = self.provider.get_tree(
            ident,
            revision=rev.commit_sha or rev.branch or revision,
            max_depth=opts.max_depth,
            max_files=opts.max_files,
            timeout_seconds=opts.timeout_seconds,
            is_cancelled=is_cancelled,
        )

        # 6. Normalize paths, eliminate duplicates, and categorize components
        clean_tree = RepositoryTree(root_path=raw_tree.root_path)
        seen_file_paths: set[str] = set()
        seen_dir_paths: set[str] = set()
        is_truncated: bool = bool(raw_tree.is_truncated)
        truncation_reason: Optional[str] = raw_tree.truncation_reason

        total_bytes = 0
        detected_languages: dict[str, int] = {}
        readme_files: list[RepositoryFile] = []
        manifest_files: list[RepositoryFile] = []
        doc_files: list[RepositoryFile] = []

        # Process directories
        for dir_node in sorted(raw_tree.directories, key=lambda d: d.path):
            self.provider.check_cancellation(is_cancelled, target_str, "repository_discovery")
            try:
                norm_d = normalize_repo_path(dir_node.path)
            except RepositoryValidationError:
                continue
            if not norm_d or norm_d in seen_dir_paths:
                continue
            if self._is_excluded(norm_d, opts.exclude_patterns):
                continue
            if len(seen_dir_paths) >= opts.max_directories:
                is_truncated = True
                truncation_reason = f"Directory count reached maximum limit of {opts.max_directories}"
                break
            seen_dir_paths.add(norm_d)
            clean_tree.add_directory(dir_node)

        # Process files
        for file_node in sorted(raw_tree.files, key=lambda f: f.path):
            self.provider.check_cancellation(is_cancelled, target_str, "repository_discovery")
            try:
                norm_f = normalize_repo_path(file_node.path)
            except RepositoryValidationError:
                continue
            if not norm_f or norm_f in seen_file_paths:
                continue
            if self._is_excluded(norm_f, opts.exclude_patterns):
                continue
            if len(seen_file_paths) >= opts.max_files:
                is_truncated = True
                truncation_reason = f"File count reached maximum limit of {opts.max_files}"
                break

            seen_file_paths.add(norm_f)
            clean_tree.add_file(file_node)
            total_bytes += file_node.size_bytes

            # Language tracking
            lang = file_node.language or detect_file_language(norm_f)
            if lang:
                detected_languages[lang] = detected_languages.get(lang, 0) + 1

            # Category classification
            if is_readme_file(norm_f):
                readme_files.append(file_node)
            elif is_manifest_file(norm_f):
                manifest_files.append(file_node)
            elif is_documentation_file(norm_f):
                doc_files.append(file_node)

        # Deterministic sorting for categories (root files first, then alphabetical)
        readme_files.sort(key=lambda f: (f.path.count("/"), f.path))
        manifest_files.sort(key=lambda f: (f.path.count("/"), f.path))
        doc_files.sort(key=lambda f: (f.path.count("/"), f.path))

        # Inferred primary language
        primary_lang = ident.primary_language
        if not primary_lang and detected_languages:
            # Sort by frequency descending
            sorted_langs = sorted(detected_languages.items(), key=lambda kv: kv[1], reverse=True)
            primary_lang = sorted_langs[0][0]

        # Truncation assessment
        if not is_truncated:
            is_truncated = raw_tree.is_truncated
            truncation_reason = raw_tree.truncation_reason
        if clean_tree.total_files >= opts.max_files:
            is_truncated = True
            truncation_reason = f"File count reached maximum limit of {opts.max_files}"
        elif clean_tree.depth >= opts.max_depth:
            is_truncated = True
            truncation_reason = f"Directory depth reached maximum limit of {opts.max_depth}"
        elif total_bytes >= opts.max_total_bytes:
            is_truncated = True
            truncation_reason = f"Total byte size ({total_bytes} bytes) reached maximum limit of {opts.max_total_bytes} bytes"

        return DiscoveredRepository(
            identity=ident,
            revision=rev,
            tree=clean_tree,
            readme_files=readme_files,
            manifest_files=manifest_files,
            documentation_files=doc_files,
            primary_language=primary_lang,
            detected_languages=detected_languages,
            total_files=clean_tree.total_files,
            total_directories=clean_tree.total_directories,
            total_bytes=total_bytes,
            is_truncated=is_truncated,
            truncation_reason=truncation_reason,
            metadata={
                "provider_id": self.provider.provider_id,
                "provider_type": self.provider.provider_type.value,
            },
            discovered_at=utc_now(),
        )

    def _is_excluded(self, path: str, patterns: tuple[str, ...]) -> bool:
        """Check if path segments match exclusion patterns."""
        parts = path.split("/")
        for pattern in patterns:
            if pattern in parts:
                return True
        return False

"""
Project Structure Discovery Engine (Phase 1 / Part 8 / Step 3).

Discovers the physical and structural inventory of an AutonomOS project/workspace:
- Project root, directories, and files
- Source roots, test directories, documentation directories, and configuration directories
- Recognized manifests, lockfiles, and ecosystem mappings
- Detected languages, file types, and binary vs text classifications
- Bounded traversal ceilings (max_depth, max_files, max_directories, max_total_bytes)
- Deterministic lexicographical ordering, deduplication, and symlink containment

Answers: "What is physically/structurally present in the project?"
Does NOT make subjective semantic relevance judgements.
Does NOT execute files or shell commands.
Does NOT expose secrets or load large file contents unnecessarily.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import hashlib
import json
import logging
import os
import posixpath
import re
import time
from typing import Any, Callable, Optional

from core.research.errors import (
    ProjectCancelledError,
    ProjectContextError,
    ProjectFileNotFoundError,
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
    ProjectStructure,
    ProjectType,
    normalize_project_path,
)
from core.research.project.provider import (
    IGNORED_PROJECT_NAMES,
    ProjectWorkspaceProvider,
    is_sensitive_project_path,
)
from core.research.repo.models import (
    detect_file_language,
    is_known_binary_extension,
    utc_now,
)

logger = logging.getLogger("AutonomOS.Research.ProjectStructureDiscovery")

# -----------------------------------------------------------------------------
# Manifest & Ecosystem Classification Maps
# -----------------------------------------------------------------------------

KNOWN_MANIFEST_ECOSYSTEM_MAP: dict[str, str] = {
    # Python
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "requirements-dev.txt": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "Pipfile": "python",
    "Pipfile.lock": "python",
    "poetry.lock": "python",
    "environment.yml": "python",
    "tox.ini": "python",
    "pylintrc": "python",
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
    "bun.lockb": "javascript",
    # Rust
    "Cargo.toml": "rust",
    "Cargo.lock": "rust",
    # Go
    "go.mod": "go",
    "go.sum": "go",
    "go.work": "go",
    "Gopkg.toml": "go",
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
    # Dart / Flutter
    "pubspec.yaml": "dart",
    "pubspec.lock": "dart",
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
    "Directory.Build.targets": "csharp",
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
    # CI/CD Workflows
    ".gitlab-ci.yml": "ci",
    "azure-pipelines.yml": "ci",
    "Jenkinsfile": "ci",
}

KNOWN_TEST_DIRS = {
    "tests",
    "test",
    "spec",
    "specs",
    "__tests__",
    "testing",
    "test_suite",
    "unit_tests",
    "integration_tests",
}

KNOWN_DOCS_DIRS = {
    "docs",
    "doc",
    "documentation",
    "wiki",
    "man",
    "guides",
    "reference",
    "manual",
}

KNOWN_CONFIG_DIRS = {
    "config",
    "configs",
    "conf",
    "settings",
    ".github",
    ".vscode",
    ".idea",
    "etc",
}

KNOWN_SOURCE_ROOT_NAMES = {
    "src",
    "lib",
    "app",
    "core",
    "pkg",
    "packages",
    "sources",
}

DEFAULT_GENERATED_PATTERNS: tuple[str, ...] = (
    "*.min.js",
    "*.min.css",
    "*.bundle.js",
    "*.map",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.o",
    "*.obj",
    "*.so",
    "*.dylib",
    "*.dll",
    "*.class",
    "*.pb.go",
    "*.pb.cc",
    "*.pb.h",
)


# -----------------------------------------------------------------------------
# Classification Helper Functions
# -----------------------------------------------------------------------------

def is_manifest_path(path_or_name: str) -> bool:
    """Check if a path or filename corresponds to a recognized project manifest or lockfile."""
    if not path_or_name:
        return False
    base = posixpath.basename(path_or_name)
    if base in KNOWN_MANIFEST_ECOSYSTEM_MAP:
        return True
    if base.endswith((".csproj", ".fsproj", ".sln", ".vcxproj")):
        return True
    norm = path_or_name.replace("\\", "/").lstrip("/")
    if (norm.startswith(".github/workflows/") or "/.github/workflows/" in norm) and norm.endswith((".yml", ".yaml")):
        return True
    return False


def classify_manifest_ecosystem(path_or_name: str) -> Optional[str]:
    """Return the technology ecosystem for a manifest file."""
    if not path_or_name:
        return None
    base = posixpath.basename(path_or_name)
    if base in KNOWN_MANIFEST_ECOSYSTEM_MAP:
        return KNOWN_MANIFEST_ECOSYSTEM_MAP[base]
    if base.endswith((".csproj", ".fsproj", ".sln", ".vcxproj")):
        return "csharp"
    norm = path_or_name.replace("\\", "/").lstrip("/")
    if (norm.startswith(".github/workflows/") or "/.github/workflows/" in norm) and norm.endswith((".yml", ".yaml")):
        return "ci"
    return None


def is_test_file_path(path_or_name: str) -> bool:
    """Check if a relative path or filename represents a test file."""
    if not path_or_name:
        return False
    norm = path_or_name.replace("\\", "/").strip("/")
    parts = norm.split("/")
    # Directory check
    if any(p.lower() in KNOWN_TEST_DIRS for p in parts[:-1]):
        return True
    # Filename check
    fname = parts[-1].lower()
    if fname.startswith("test_") or fname.endswith(("_test.py", "_test.go", "_test.rs", ".test.js", ".test.ts", ".test.jsx", ".test.tsx", ".spec.js", ".spec.ts", ".spec.jsx", ".spec.tsx", "_spec.rb")):
        return True
    if fname.startswith("test") and fname.endswith((".py", ".js", ".ts", ".java", ".kt", ".go")):
        return True
    return False


def is_documentation_file_path(path_or_name: str) -> bool:
    """Check if a path or filename represents documentation."""
    if not path_or_name:
        return False
    norm = path_or_name.replace("\\", "/").strip("/")
    parts = norm.split("/")
    # Check directory
    if any(p.lower() in KNOWN_DOCS_DIRS for p in parts[:-1]):
        return True
    # Check filename
    fname = parts[-1].lower()
    if fname.startswith(("readme", "contributing", "changelog", "license", "authors", "history", "architecture", "design", "security")):
        return True
    _, ext = posixpath.splitext(fname)
    if ext in (".md", ".markdown", ".rst", ".adoc", ".asciidoc", ".tex"):
        return True
    return False


def is_generated_file_path(
    path_or_name: str,
    patterns: Optional[tuple[str, ...]] = None,
) -> bool:
    """Check if a path matches known generated/minified file patterns."""
    if not path_or_name:
        return False
    fname = posixpath.basename(path_or_name).lower()
    effective_patterns = patterns or DEFAULT_GENERATED_PATTERNS
    for pat in effective_patterns:
        if fnmatch.fnmatch(fname, pat.lower()):
            return True
    return False


def detect_source_roots(directories: list[str], files: list[str]) -> list[str]:
    """
    Detect source root directories dynamically without assuming a fixed project layout.
    """
    roots: set[str] = set()

    # 1. Look for canonical source root names
    for d in directories:
        d_norm = normalize_project_path(d)
        parts = d_norm.split("/")
        if len(parts) == 1 and parts[0].lower() in KNOWN_SOURCE_ROOT_NAMES:
            roots.add(d_norm)
        elif len(parts) == 2 and parts[0] == "packages":
            # Mono-repo pattern: packages/<pkg>/src or packages/<pkg>
            roots.add(d_norm)

    # 2. Check if subdirectories contain standard source directories (e.g. packages/foo/src)
    for d in directories:
        d_norm = normalize_project_path(d)
        if d_norm.endswith("/src") or d_norm.endswith("/lib"):
            roots.add(d_norm)

    # 3. If no standard source roots were found, check if code files exist at project root
    if not roots:
        root_code_files = [
            f for f in files
            if "/" not in f and not is_manifest_path(f) and not is_documentation_file_path(f)
            and not is_test_file_path(f) and detect_file_language(f) is not None
        ]
        if root_code_files:
            roots.add(".")

    # If still no roots, check for any top-level directories containing non-test, non-doc code
    if not roots:
        dir_code_counts: dict[str, int] = {}
        for f in files:
            if "/" in f and not is_test_file_path(f) and not is_documentation_file_path(f):
                top_dir = f.split("/")[0]
                if top_dir not in KNOWN_TEST_DIRS and top_dir not in KNOWN_DOCS_DIRS and top_dir not in KNOWN_CONFIG_DIRS:
                    if detect_file_language(f) is not None:
                        dir_code_counts[top_dir] = dir_code_counts.get(top_dir, 0) + 1
        if dir_code_counts:
            # Pick the top directory with most code files
            best_dir = max(dir_code_counts.items(), key=lambda x: x[1])[0]
            roots.add(best_dir)

    return sorted(roots)


# -----------------------------------------------------------------------------
# Discovery Options & Results
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class ProjectDiscoveryOptions:
    """
    Configurable bounds and policy options for project structure discovery.
    """
    max_depth: int = 15                     # Max directory traversal depth
    max_files: int = 2000                   # Max files collected
    max_directories: int = 500              # Max directories collected
    max_total_bytes: int = 50_000_000       # 50 MB total byte limit across discovered files
    max_file_size: int = 5_000_000          # 5 MB ceiling on individual file size
    timeout_seconds: float = 30.0           # Overall discovery timeout
    concurrency_limit: int = 1              # Sequential deterministic discovery by default
    read_manifest_metadata: bool = True     # Bounded safe inspection of manifests
    ignored_names: tuple[str, ...] = tuple(sorted(IGNORED_PROJECT_NAMES))
    ignored_patterns: tuple[str, ...] = DEFAULT_GENERATED_PATTERNS

    def __post_init__(self):
        if self.max_depth <= 0:
            raise ProjectValidationError("max_depth", "max_depth must be > 0.")
        if self.max_files <= 0:
            raise ProjectValidationError("max_files", "max_files must be > 0.")
        if self.max_directories <= 0:
            raise ProjectValidationError("max_directories", "max_directories must be > 0.")
        if self.max_total_bytes <= 0:
            raise ProjectValidationError("max_total_bytes", "max_total_bytes must be > 0.")
        if self.max_file_size <= 0:
            raise ProjectValidationError("max_file_size", "max_file_size must be > 0.")
        if self.timeout_seconds <= 0:
            raise ProjectValidationError("timeout_seconds", "timeout_seconds must be > 0.")
        if self.concurrency_limit <= 0:
            raise ProjectValidationError("concurrency_limit", "concurrency_limit must be > 0.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_depth": self.max_depth,
            "max_files": self.max_files,
            "max_directories": self.max_directories,
            "max_total_bytes": self.max_total_bytes,
            "max_file_size": self.max_file_size,
            "timeout_seconds": self.timeout_seconds,
            "concurrency_limit": self.concurrency_limit,
            "read_manifest_metadata": self.read_manifest_metadata,
            "ignored_names": list(self.ignored_names),
            "ignored_patterns": list(self.ignored_patterns),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectDiscoveryOptions:
        if not isinstance(data, dict):
            raise ProjectValidationError("data", "Expected dictionary for ProjectDiscoveryOptions.")
        return cls(
            max_depth=int(data.get("max_depth", 15)),
            max_files=int(data.get("max_files", 2000)),
            max_directories=int(data.get("max_directories", 500)),
            max_total_bytes=int(data.get("max_total_bytes", 50_000_000)),
            max_file_size=int(data.get("max_file_size", 5_000_000)),
            timeout_seconds=float(data.get("timeout_seconds", 30.0)),
            concurrency_limit=int(data.get("concurrency_limit", 1)),
            read_manifest_metadata=bool(data.get("read_manifest_metadata", True)),
            ignored_names=tuple(data.get("ignored_names", tuple(sorted(IGNORED_PROJECT_NAMES)))),
            ignored_patterns=tuple(data.get("ignored_patterns", DEFAULT_GENERATED_PATTERNS)),
        )


@dataclass
class DiscoveredProjectStructure:
    """
    Deterministic discovery result establishing the complete physical inventory of a project.
    """
    identity: ProjectIdentity
    structure: ProjectStructure
    source_roots: list[str] = field(default_factory=list)
    test_directories: list[str] = field(default_factory=list)
    documentation_directories: list[str] = field(default_factory=list)
    configuration_directories: list[str] = field(default_factory=list)
    manifest_files: list[ProjectFile] = field(default_factory=list)
    documentation_files: list[ProjectFile] = field(default_factory=list)
    test_files: list[ProjectFile] = field(default_factory=list)
    primary_language: Optional[str] = None
    detected_languages: dict[str, int] = field(default_factory=dict)
    file_types: dict[str, int] = field(default_factory=dict)
    total_files: int = 0
    total_directories: int = 0
    total_bytes: int = 0
    total_lines: int = 0
    is_truncated: bool = False
    truncation_reason: Optional[str] = None
    duration_seconds: float = 0.0
    discovered_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def languages(self) -> dict[str, int]:
        """Convenience alias for detected_languages."""
        return self.detected_languages

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "structure": self.structure.to_dict(),
            "source_roots": list(self.source_roots),
            "test_directories": list(self.test_directories),
            "documentation_directories": list(self.documentation_directories),
            "configuration_directories": list(self.configuration_directories),
            "manifest_files": [f.to_dict() for f in self.manifest_files],
            "documentation_files": [f.to_dict() for f in self.documentation_files],
            "test_files": [f.to_dict() for f in self.test_files],
            "primary_language": self.primary_language,
            "detected_languages": dict(self.detected_languages),
            "file_types": dict(self.file_types),
            "total_files": self.total_files,
            "total_directories": self.total_directories,
            "total_bytes": self.total_bytes,
            "total_lines": self.total_lines,
            "is_truncated": self.is_truncated,
            "truncation_reason": self.truncation_reason,
            "duration_seconds": self.duration_seconds,
            "discovered_at": self.discovered_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscoveredProjectStructure:
        if not isinstance(data, dict):
            raise ProjectValidationError("data", "Expected dictionary for DiscoveredProjectStructure.")

        ident = ProjectIdentity.from_dict(data.get("identity", {}))
        struct = ProjectStructure.from_dict(data.get("structure", {}))
        manifests = [ProjectFile.from_dict(f) for f in data.get("manifest_files", [])]
        docs = [ProjectFile.from_dict(f) for f in data.get("documentation_files", [])]
        tests = [ProjectFile.from_dict(f) for f in data.get("test_files", [])]

        return cls(
            identity=ident,
            structure=struct,
            source_roots=list(data.get("source_roots", [])),
            test_directories=list(data.get("test_directories", [])),
            documentation_directories=list(data.get("documentation_directories", [])),
            configuration_directories=list(data.get("configuration_directories", [])),
            manifest_files=manifests,
            documentation_files=docs,
            test_files=tests,
            primary_language=data.get("primary_language"),
            detected_languages=dict(data.get("detected_languages", {})),
            file_types=dict(data.get("file_types", {})),
            total_files=int(data.get("total_files", 0)),
            total_directories=int(data.get("total_directories", 0)),
            total_bytes=int(data.get("total_bytes", 0)),
            total_lines=int(data.get("total_lines", 0)),
            is_truncated=bool(data.get("is_truncated", False)),
            truncation_reason=data.get("truncation_reason"),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            discovered_at=data.get("discovered_at", utc_now()),
            metadata=dict(data.get("metadata", {})),
        )


# -----------------------------------------------------------------------------
# Discoverer Engine
# -----------------------------------------------------------------------------

class ProjectStructureDiscoverer:
    """
    Coordinates with ProjectWorkspaceProvider to perform bounded, deterministic discovery.
    """

    def __init__(self, options: Optional[ProjectDiscoveryOptions] = None):
        self.options = options or ProjectDiscoveryOptions()

    def discover(
        self,
        provider: ProjectWorkspaceProvider,
        options: Optional[ProjectDiscoveryOptions] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> DiscoveredProjectStructure:
        """
        Execute deterministic discovery of project workspace structure.
        """
        opts = options or self.options
        start_time = time.time()

        provider.check_cancellation(is_cancelled, "discover", provider.root_path)

        # 1. Identify project root
        identity = provider.identify_root(
            timeout_seconds=opts.timeout_seconds,
            is_cancelled=is_cancelled,
        )

        # 2. Query workspace structure with bounds
        provider.check_cancellation(is_cancelled, "discover_list_dir", provider.root_path)
        elapsed_so_far = time.time() - start_time
        remaining_timeout = max(0.1, opts.timeout_seconds - elapsed_so_far)

        try:
            raw_structure = provider.list_dir(
                subpath="",
                max_depth=opts.max_depth,
                timeout_seconds=remaining_timeout,
                is_cancelled=is_cancelled,
            )
        except ProjectResourceLimitError as e:
            # Handle resource limit gracefully by returning a truncated discovery result
            raw_structure = ProjectStructure(
                root_path=provider.root_path,
                is_truncated=True,
                truncation_reason=str(e),
            )

        # 3. Deduplication & filtering against ignored names / patterns
        seen_paths: set[str] = set()
        clean_files: list[ProjectFile] = []
        is_truncated = raw_structure.is_truncated
        truncation_reason = raw_structure.truncation_reason

        total_bytes = 0
        total_lines = 0

        # Sort files deterministically by relative path
        sorted_files = sorted(raw_structure.files, key=lambda f: f.relative_path)

        for f in sorted_files:
            provider.check_cancellation(is_cancelled, "discover_filter_files", f.relative_path)

            if f.relative_path in seen_paths:
                continue
            seen_paths.add(f.relative_path)

            # Check ignored directories and names
            parts = f.relative_path.split("/")
            if any(part in opts.ignored_names for part in parts):
                continue

            # Check sensitive file paths
            if is_sensitive_project_path(f.relative_path):
                continue

            # Check ignored/generated patterns
            if is_generated_file_path(f.relative_path, opts.ignored_patterns):
                continue

            # Check file size limit
            if f.size_bytes > opts.max_file_size:
                continue

            # Check total file count limit
            if len(clean_files) >= opts.max_files:
                is_truncated = True
                truncation_reason = f"File count limit ({opts.max_files}) reached"
                break

            # Check total bytes limit
            if total_bytes + f.size_bytes > opts.max_total_bytes:
                is_truncated = True
                truncation_reason = f"Total byte limit ({opts.max_total_bytes} bytes) reached"
                break

            clean_files.append(f)
            total_bytes += f.size_bytes
            if f.line_count:
                total_lines += f.line_count

        # 4. Filter and sort directories deterministically
        seen_dirs: set[str] = set()
        clean_dirs: list[ProjectDirectory] = []
        sorted_dirs = sorted(raw_structure.directories, key=lambda d: d.path)

        for d in sorted_dirs:
            if d.path in seen_dirs:
                continue
            seen_dirs.add(d.path)

            parts = d.path.split("/")
            if any(part in opts.ignored_names for part in parts):
                continue

            if len(clean_dirs) >= opts.max_directories:
                is_truncated = True
                truncation_reason = f"Directory count limit ({opts.max_directories}) reached"
                break

            clean_dirs.append(d)

        # 5. Build bounded clean structure
        structure = ProjectStructure(
            root_path=raw_structure.root_path or provider.root_path,
            directories=clean_dirs,
            files=clean_files,
            max_depth_reached=raw_structure.max_depth_reached,
            is_truncated=is_truncated,
            truncation_reason=truncation_reason,
            total_files=len(clean_files),
            total_directories=len(clean_dirs),
        )

        # 6. Categorize directories
        source_roots = detect_source_roots(
            [d.path for d in clean_dirs],
            [f.relative_path for f in clean_files],
        )

        test_directories = sorted([
            d.path for d in clean_dirs
            if any(part.lower() in KNOWN_TEST_DIRS for part in d.path.split("/"))
        ])

        documentation_directories = sorted([
            d.path for d in clean_dirs
            if any(part.lower() in KNOWN_DOCS_DIRS for part in d.path.split("/"))
        ])

        configuration_directories = sorted([
            d.path for d in clean_dirs
            if any(part.lower() in KNOWN_CONFIG_DIRS for part in d.path.split("/"))
        ])

        # 7. Categorize files
        manifest_files = [f for f in clean_files if is_manifest_path(f.relative_path)]
        documentation_files = [f for f in clean_files if is_documentation_file_path(f.relative_path)]
        test_files = [f for f in clean_files if is_test_file_path(f.relative_path)]

        # 8. Compute language and file type metrics
        lang_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}

        for f in clean_files:
            # Language
            if f.language:
                lang_counts[f.language] = lang_counts.get(f.language, 0) + 1
            # Extension / File type
            ext = f.extension or "[no_extension]"
            type_counts[ext] = type_counts.get(ext, 0) + 1

        # Deterministic sorting of language and file type maps
        sorted_languages = dict(sorted(lang_counts.items(), key=lambda x: (-x[1], x[0])))
        sorted_file_types = dict(sorted(type_counts.items(), key=lambda x: (-x[1], x[0])))

        primary_lang = next(iter(sorted_languages.keys())) if sorted_languages else None

        # 9. Bounded manifest metadata inspection
        manifest_meta: dict[str, Any] = {}
        if opts.read_manifest_metadata and manifest_files:
            for mf in manifest_files:
                provider.check_cancellation(is_cancelled, "discover_manifest_meta", mf.relative_path)
                # Only read small top-level manifests (e.g. pyproject.toml, package.json <= 64 KB)
                if mf.size_bytes <= 64_000 and "/" not in mf.relative_path:
                    try:
                        mat = provider.get_file_content(
                            mf.relative_path,
                            max_bytes=64_000,
                            timeout_seconds=5.0,
                            is_cancelled=is_cancelled,
                        )
                        ecosystem = classify_manifest_ecosystem(mf.relative_path)
                        manifest_meta[mf.relative_path] = {
                            "ecosystem": ecosystem,
                            "size_bytes": mf.size_bytes,
                            "content_hash": mat.content_hash,
                        }
                    except (ProjectContextError, OSError):
                        pass

        # 10. Update identity languages if discovered languages provide richer detail
        detected_lang_names = list(sorted_languages.keys())
        if detected_lang_names and not identity.languages:
            identity = ProjectIdentity(
                project_id=identity.project_id,
                project_root=identity.project_root,
                project_type=identity.project_type,
                languages=detected_lang_names,
                version=identity.version,
                name=identity.name,
                description=identity.description,
                provenance=identity.provenance,
                metadata=dict(identity.metadata),
            )

        duration = time.time() - start_time

        return DiscoveredProjectStructure(
            identity=identity,
            structure=structure,
            source_roots=source_roots,
            test_directories=test_directories,
            documentation_directories=documentation_directories,
            configuration_directories=configuration_directories,
            manifest_files=manifest_files,
            documentation_files=documentation_files,
            test_files=test_files,
            primary_language=primary_lang,
            detected_languages=sorted_languages,
            file_types=sorted_file_types,
            total_files=len(clean_files),
            total_directories=len(clean_dirs),
            total_bytes=total_bytes,
            total_lines=total_lines,
            is_truncated=is_truncated,
            truncation_reason=truncation_reason,
            duration_seconds=duration,
            metadata={"manifest_metadata": manifest_meta},
        )

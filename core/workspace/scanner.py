from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from core.workspace.filesystem import ControlledWorkspaceFS, WorkspaceSecurityError

logger = logging.getLogger("AutonomOS.RepositoryScanner")


@dataclass
class FileMetadata:
    """Structured deterministic metadata for a single file in the workspace."""
    path: str
    name: str
    extension: str
    size: int
    mtime: float
    hash: str
    category: str  # source | manifest_config | test | documentation | script | asset | other
    symbols: List[str] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    todos: List[str] = field(default_factory=list)
    purpose: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FileMetadata:
        return cls(**data)


@dataclass
class GitState:
    """Deterministic snapshot of workspace git state if repository is version-controlled."""
    is_git_repo: bool = False
    current_branch: str = ""
    head_commit: str = ""
    is_clean: bool = True
    untracked_count: int = 0
    modified_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GitState:
        return cls(**data)


@dataclass
class RepositoryIndex:
    """
    Structured, persistent repository index produced deterministically by the scanner.
    Provides complete project inventory without requiring LLM context dumps.
    """
    workspace_root: str
    scanned_at: str
    total_files: int
    total_directories: int
    total_size_bytes: int
    git_state: GitState
    tech_stack: Dict[str, Any]
    source_directories: List[str]
    test_directories: List[str]
    doc_directories: List[str]
    script_directories: List[str]
    entry_points: List[str]
    manifests: List[str]
    files: Dict[str, FileMetadata] = field(default_factory=dict)
    directories: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_root": self.workspace_root,
            "scanned_at": self.scanned_at,
            "total_files": self.total_files,
            "total_directories": self.total_directories,
            "total_size_bytes": self.total_size_bytes,
            "git_state": self.git_state.to_dict(),
            "tech_stack": self.tech_stack,
            "source_directories": self.source_directories,
            "test_directories": self.test_directories,
            "doc_directories": self.doc_directories,
            "script_directories": self.script_directories,
            "entry_points": self.entry_points,
            "manifests": self.manifests,
            "directories": self.directories,
            "files": {path: f.to_dict() for path, f in self.files.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RepositoryIndex:
        git_state = GitState.from_dict(data.get("git_state", {}))
        files_dict = {
            p: FileMetadata.from_dict(fdata)
            for p, fdata in data.get("files", {}).items()
        }
        return cls(
            workspace_root=data.get("workspace_root", ""),
            scanned_at=data.get("scanned_at", ""),
            total_files=data.get("total_files", 0),
            total_directories=data.get("total_directories", 0),
            total_size_bytes=data.get("total_size_bytes", 0),
            git_state=git_state,
            tech_stack=data.get("tech_stack", {}),
            source_directories=data.get("source_directories", []),
            test_directories=data.get("test_directories", []),
            doc_directories=data.get("doc_directories", []),
            script_directories=data.get("script_directories", []),
            entry_points=data.get("entry_points", []),
            manifests=data.get("manifests", []),
            directories=data.get("directories", []),
            files=files_dict,
        )

    def query_files(
        self,
        category: Optional[str] = None,
        extension: Optional[str] = None,
        path_prefix: Optional[str] = None,
    ) -> List[FileMetadata]:
        """Filters files deterministically matching criteria."""
        results = []
        for path, meta in self.files.items():
            if category and meta.category != category:
                continue
            if extension and meta.extension.lower() != extension.lower():
                continue
            if path_prefix and not path.startswith(path_prefix):
                continue
            results.append(meta)
        return results


class RepositoryScanner:
    """
    Deterministic Codebase Scanner for AutonomOS Manager.
    Inspects and maps project structure, manifests, symbols, and dependencies
    strictly inside the workspace boundary.
    """

    IGNORE_DIRS: Set[str] = {
        ".git",
        "node_modules",
        "build",
        "dist",
        "out",
        ".dart_tool",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".venv",
        "venv",
        "env",
        ".idea",
        ".vscode",
        "target",
        ".gradle",
        "Pods",
        ".gemini",
        ".next",
        ".nuxt",
        "coverage",
        ".turbo",
        ".cache",
    }

    IGNORE_BINARY_EXTENSIONS: Set[str] = {
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
        ".mp4", ".mp3", ".wav",
        ".woff", ".woff2", ".ttf", ".eot",
        ".pdf", ".zip", ".tar", ".gz",
        ".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll", ".exe", ".bin",
        ".lock",
    }

    MANIFEST_MAP: Dict[str, Tuple[str, str]] = {
        "package.json": ("Node.js / TypeScript / JavaScript", "npm/yarn/pnpm"),
        "pubspec.yaml": ("Flutter / Dart", "pub"),
        "pyproject.toml": ("Python", "pip/poetry/flit"),
        "requirements.txt": ("Python", "pip"),
        "Pipfile": ("Python", "pipenv"),
        "Cargo.toml": ("Rust", "cargo"),
        "go.mod": ("Go", "go modules"),
        "pom.xml": ("Java", "maven"),
        "build.gradle": ("Java / Kotlin / Android", "gradle"),
        "build.gradle.kts": ("Kotlin / Gradle", "gradle"),
        "Gemfile": ("Ruby", "bundler"),
        "composer.json": ("PHP", "composer"),
        "Makefile": ("C / C++ / Build Tool", "make"),
        "Dockerfile": ("Docker", "container"),
        "docker-compose.yml": ("Docker", "compose"),
        "docker-compose.yaml": ("Docker", "compose"),
    }

    def __init__(self, fs: ControlledWorkspaceFS, max_parse_size_bytes: int = 250000):
        self.fs = fs
        self.max_parse_size_bytes = max_parse_size_bytes

    def scan_repository(self) -> RepositoryIndex:
        """
        Executes a deterministic scan of the entire workspace.
        Returns a structured RepositoryIndex representing files, types, manifests, symbols, and structure.
        """
        workspace_root = self.fs.workspace_root
        all_files: Dict[str, FileMetadata] = {}
        all_directories: List[str] = []
        total_size_bytes = 0

        source_dirs: Set[str] = set()
        test_dirs: Set[str] = set()
        doc_dirs: Set[str] = set()
        script_dirs: Set[str] = set()
        manifests: List[str] = []
        entry_points: List[str] = []

        # 1. Walk directory tree
        for root, dirs, filenames in os.walk(workspace_root):
            # Prune ignored directories in-place
            dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS and not d.startswith(".git")]

            root_path = Path(root)
            try:
                rel_dir = str(root_path.relative_to(workspace_root))
                if rel_dir != ".":
                    # Skip internal .autonomos directory
                    if rel_dir.startswith(".autonomos"):
                        continue
                    all_directories.append(rel_dir)
            except Exception:
                continue

            for fname in filenames:
                file_path = root_path / fname
                try:
                    rel_path = str(file_path.relative_to(workspace_root))
                    # Skip internal autonomos operational files
                    if rel_path.startswith(".autonomos"):
                        continue

                    ext = os.path.splitext(fname)[1].lower()

                    # Handle unreadable/broken stats gracefully
                    try:
                        st = file_path.stat()
                        fsize = st.st_size
                        fmtime = st.st_mtime
                    except Exception as e:
                        logger.warning(f"Could not stat file '{rel_path}': {e}")
                        continue

                    total_size_bytes += fsize
                    category = self.categorize_file(rel_path)

                    # Track directory purposes
                    parent_dir = str(Path(rel_path).parent)
                    if parent_dir != ".":
                        if category == "source":
                            source_dirs.add(parent_dir)
                        elif category == "test":
                            test_dirs.add(parent_dir)
                        elif category == "documentation":
                            doc_dirs.add(parent_dir)
                        elif category == "script":
                            script_dirs.add(parent_dir)

                    if category == "manifest_config" and fname in self.MANIFEST_MAP:
                        manifests.append(rel_path)

                    # Compute deterministic SHA256 hash safely
                    fhash = ""
                    try:
                        fhash = self.fs.compute_file_hash(rel_path)
                    except Exception:
                        pass

                    # Extract symbols, imports, and purpose safely
                    symbols, imports, todos, purpose = self._extract_metadata(rel_path, category, fsize)

                    all_files[rel_path] = FileMetadata(
                        path=rel_path,
                        name=fname,
                        extension=ext,
                        size=fsize,
                        mtime=fmtime,
                        hash=fhash,
                        category=category,
                        symbols=symbols,
                        imports=imports,
                        todos=todos,
                        purpose=purpose,
                    )

                except Exception as e:
                    # Never crash the entire audit because one file failed
                    logger.warning(f"Error scanning file '{fname}' in '{root}': {e}")
                    continue

        # 2. Tech stack & Entry points detection
        tech_stack = self._detect_tech_stack(all_files, manifests)
        entry_points = tech_stack.get("entry_points", [])

        # 3. Git state detection
        git_state = self._detect_git_state()

        return RepositoryIndex(
            workspace_root=str(workspace_root),
            scanned_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            total_files=len(all_files),
            total_directories=len(all_directories),
            total_size_bytes=total_size_bytes,
            git_state=git_state,
            tech_stack=tech_stack,
            source_directories=sorted(list(source_dirs)),
            test_directories=sorted(list(test_dirs)),
            doc_directories=sorted(list(doc_dirs)),
            script_directories=sorted(list(script_dirs)),
            entry_points=sorted(list(set(entry_points))),
            manifests=sorted(manifests),
            files=all_files,
            directories=sorted(all_directories),
        )

    def categorize_file(self, rel_path: str) -> str:
        """Deterministically categorizes a file based on name, path, and extension."""
        name = os.path.basename(rel_path).lower()
        ext = os.path.splitext(name)[1]

        # 1. Manifests and Configuration
        if name in self.MANIFEST_MAP or name in (
            "tsconfig.json", "vite.config.ts", "webpack.config.js", "setup.py",
            ".eslintrc.json", ".prettierrc", "tailwind.config.js", "tailwind.config.ts",
            "babel.config.js", "CMakeLists.txt", "angular.json"
        ):
            return "manifest_config"

        # 2. Tests
        if "test" in name or "spec" in name or rel_path.startswith("test/") or rel_path.startswith("tests/") or "/test/" in rel_path or "/tests/" in rel_path:
            return "test"

        # 3. Documentation
        if ext in (".md", ".txt", ".rst", ".adoc") or name in ("license", "readme", "contributing", "changelog"):
            return "documentation"

        # 4. Scripts
        if ext in (".sh", ".bash", ".zsh", ".bat", ".cmd", ".ps1") or rel_path.startswith("scripts/") or rel_path.startswith("bin/"):
            return "script"

        # 5. Source Code
        if ext in (
            ".py", ".dart", ".ts", ".tsx", ".js", ".jsx",
            ".go", ".rs", ".java", ".kt", ".swift",
            ".cpp", ".c", ".h", ".hpp", ".cs", ".rb", ".php", ".scala"
        ):
            return "source"

        # 6. Assets & Config
        if ext in (".json", ".yaml", ".yml", ".toml", ".xml", ".css", ".scss", ".html", ".sql", ".graphql", ".env.example"):
            return "asset"

        return "other"

    def _extract_metadata(
        self, rel_path: str, category: str, file_size: int
    ) -> Tuple[List[str], List[str], List[str], str]:
        """
        Safely extracts symbols, imports, and TODOs from source code without failing on syntax or encoding errors.
        """
        symbols: List[str] = []
        imports: List[str] = []
        todos: List[str] = []
        purpose: str = ""

        ext = os.path.splitext(rel_path)[1].lower()

        if category not in ("source", "test", "manifest_config"):
            return symbols, imports, todos, purpose

        if ext in self.IGNORE_BINARY_EXTENSIONS or file_size > self.max_parse_size_bytes:
            return symbols, imports, todos, purpose

        try:
            content = self.fs.read_file(rel_path, max_bytes=self.max_parse_size_bytes)
        except Exception:
            return symbols, imports, todos, purpose

        # 1. Python AST parsing
        if ext == ".py":
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        symbols.append(f"class {node.name}")
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbols.append(f"def {node.name}")
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            imports.append(alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            imports.append(node.module)
            except Exception:
                # Regex fallback for Python with syntax errors
                for m in re.finditer(r'^(?:class|def)\s+([A-Za-z0-9_]+)', content, re.MULTILINE):
                    symbols.append(m.group(0))

        # 2. Regex parsing for Dart, TypeScript, JavaScript, Rust, Go, Java, Kotlin, Swift, C++
        else:
            for m in re.finditer(r'(?:class|interface|struct|enum|trait|protocol)\s+([A-Za-z0-9_]+)', content):
                symbols.append(m.group(0))

            for m in re.finditer(r'(?:function|fn|def|void|Future<[^>]+>|Widget|const)\s+([A-Za-z0-9_]+)\s*\(', content):
                name = m.group(1)
                if name not in ("if", "for", "while", "switch", "catch", "return"):
                    symbols.append(f"fn {name}")

            for m in re.finditer(r'^\s*(?:async\s+)?([A-Za-z0-9_]+)\s*\([^)]*\)\s*(?::\s*[\w<>[\]]+)?\s*\{', content, re.MULTILINE):
                name = m.group(1)
                if name not in ("if", "for", "while", "switch", "catch", "return", "constructor"):
                    symbols.append(f"fn {name}")

            for m in re.finditer(r'''import\s+(?:(?:[\w*\s{},]+)\s+from\s+)?['"]([^'"]+)['"]''', content):
                imports.append(m.group(1))
            for m in re.finditer(r'''require\(['"]([^'"]+)['"]\)''', content):
                imports.append(m.group(1))
            for m in re.finditer(r'''import\(['"]([^'"]+)['"]\)''', content):
                imports.append(m.group(1))

        # Extract TODOs
        for line in content.splitlines():
            sline = line.strip()
            if "TODO" in sline or "FIXME" in sline or "HACK" in sline:
                todos.append(sline[:120])

        # Infer concise purpose
        purpose = self._infer_purpose(rel_path, symbols, content)

        return (
            sorted(list(set(symbols)))[:30],
            sorted(list(set(imports)))[:30],
            todos[:10],
            purpose,
        )

    def _infer_purpose(self, rel_path: str, symbols: List[str], content: str) -> str:
        for line in content.splitlines()[:5]:
            l = line.strip()
            if l.startswith("///") or l.startswith("/*") or l.startswith("#") or l.startswith('"""'):
                clean = l.lstrip("/#*\" ").rstrip("/*\" ")
                if len(clean) > 8:
                    return clean

        name = os.path.basename(rel_path)
        if "test" in name:
            return "Test suite"
        if symbols:
            return f"Implements {symbols[0]}"
        return f"Component module {name}"

    def _detect_tech_stack(
        self, files: Dict[str, FileMetadata], manifests: List[str]
    ) -> Dict[str, Any]:
        languages: Set[str] = set()
        frameworks: Set[str] = set()
        build_tools: Set[str] = set()
        entry_points: List[str] = []

        for rel_path, meta in files.items():
            fname = meta.name
            if fname in self.MANIFEST_MAP:
                lang, btool = self.MANIFEST_MAP[fname]
                languages.add(lang)
                build_tools.add(btool)

                try:
                    content = self.fs.read_file(rel_path, max_bytes=10000)
                    if fname == "package.json":
                        pj = json.loads(content)
                        deps = {**pj.get("dependencies", {}), **pj.get("devDependencies", {})}
                        if "react" in deps: frameworks.add("React")
                        if "next" in deps: frameworks.add("Next.js")
                        if "vue" in deps: frameworks.add("Vue")
                        if "three" in deps or "@types/three" in deps: frameworks.add("Three.js")
                        if "express" in deps: frameworks.add("Express")
                        if "tailwindcss" in deps: frameworks.add("Tailwind CSS")
                        if "main" in pj: entry_points.append(pj["main"])
                    elif fname == "pubspec.yaml":
                        if "flutter:" in content: frameworks.add("Flutter")
                    elif fname in ("pyproject.toml", "requirements.txt"):
                        if "fastapi" in content: frameworks.add("FastAPI")
                        if "flask" in content: frameworks.add("Flask")
                        if "django" in content: frameworks.add("Django")
                except Exception:
                    pass

            # Detect standard entry points
            if fname in (
                "main.py", "app.py", "server.py", "index.ts", "index.js",
                "main.dart", "App.tsx", "main.go", "main.rs", "index.html"
            ):
                entry_points.append(rel_path)

        return {
            "languages": sorted(list(languages)),
            "frameworks": sorted(list(frameworks)),
            "build_tools": sorted(list(build_tools)),
            "entry_points": sorted(list(set(entry_points))),
        }

    def _detect_git_state(self) -> GitState:
        """Safely inspects git status if workspace is a git repository."""
        git_dir = self.fs.workspace_root / ".git"
        if not git_dir.exists():
            return GitState(is_git_repo=False)

        try:
            # Current branch
            branch_out = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(self.fs.workspace_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2,
            )
            branch = branch_out.stdout.strip() if branch_out.returncode == 0 else "unknown"

            # Head commit
            commit_out = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(self.fs.workspace_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2,
            )
            commit = commit_out.stdout.strip() if commit_out.returncode == 0 else ""

            # Porcelain status
            status_out = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.fs.workspace_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2,
            )
            is_clean = True
            untracked = 0
            modified = 0
            if status_out.returncode == 0:
                lines = [l for l in status_out.stdout.splitlines() if l.strip()]
                is_clean = len(lines) == 0
                for l in lines:
                    if l.startswith("??"):
                        untracked += 1
                    else:
                        modified += 1

            return GitState(
                is_git_repo=True,
                current_branch=branch,
                head_commit=commit,
                is_clean=is_clean,
                untracked_count=untracked,
                modified_count=modified,
            )
        except Exception:
            return GitState(is_git_repo=True, current_branch="main")

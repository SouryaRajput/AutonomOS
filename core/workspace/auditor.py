from __future__ import annotations

import ast
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from core.workspace.filesystem import ControlledWorkspaceFS

logger = logging.getLogger("AutonomOS.ProjectAuditor")


class ProjectAuditor:
    """
    Deterministic Codebase Auditor for AutonomOS.
    Performs comprehensive static analysis, symbol extraction, dependency graph mapping,
    and subsystem clustering without requiring massive LLM context dumps.
    """

    IGNORE_DIRS = {
        ".git",
        "node_modules",
        "build",
        "dist",
        ".dart_tool",
        "__pycache__",
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
    }

    IGNORE_EXTENSIONS = {
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
        ".mp4", ".mp3", ".wav",
        ".woff", ".woff2", ".ttf", ".eot",
        ".pdf", ".zip", ".tar", ".gz",
        ".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll", ".exe", ".bin",
        ".lock",
    }

    MANIFEST_MAP = {
        "package.json": ("Node.js / TypeScript / JavaScript", "npm/yarn/pnpm"),
        "pubspec.yaml": ("Flutter / Dart", "pub"),
        "pyproject.toml": ("Python", "pip/poetry/flit"),
        "requirements.txt": ("Python", "pip"),
        "Pipfile": ("Python", "pipenv"),
        "Cargo.toml": ("Rust", "cargo"),
        "go.mod": ("Go", "go modules"),
        "pom.xml": ("Java", "maven"),
        "build.gradle": ("Java / Kotlin / Android", "gradle"),
        "Gemfile": ("Ruby", "bundler"),
        "composer.json": ("PHP", "composer"),
        "Makefile": ("C / C++ / Build Tool", "make"),
        "Dockerfile": ("Docker", "container"),
    }

    def __init__(self, fs: ControlledWorkspaceFS):
        self.fs = fs

    def scan_workspace_files(self) -> List[Dict[str, Any]]:
        """Walks the workspace directory ignoring build and cache folders."""
        files_info = []
        workspace_root = self.fs.workspace_root

        for root, dirs, files in os.walk(workspace_root):
            # Prune ignored directories
            dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS and not d.startswith(".git")]

            root_path = Path(root)
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in self.IGNORE_EXTENSIONS or f.startswith("."):
                    # Check if it's an important config file like .env.example or .gitignore
                    if f not in (".gitignore", ".env.example", ".dockerignore", ".eslintrc.json"):
                        continue

                file_path = root_path / f
                try:
                    rel = str(file_path.relative_to(workspace_root))
                    # Avoid indexing inside .autonomos internal data
                    if rel.startswith(".autonomos/"):
                        continue

                    st = file_path.stat()
                    file_hash = self.fs.compute_file_hash(rel)
                    category = self.categorize_file(rel)

                    files_info.append({
                        "path": rel,
                        "name": f,
                        "extension": ext,
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                        "hash": file_hash,
                        "category": category,
                    })
                except Exception as e:
                    logger.debug(f"Could not stat file '{file_path}': {e}")

        return sorted(files_info, key=lambda x: x["path"])

    def categorize_file(self, rel_path: str) -> str:
        name = os.path.basename(rel_path).lower()
        ext = os.path.splitext(name)[1]

        if name in self.MANIFEST_MAP or name in ("tsconfig.json", "vite.config.ts", "webpack.config.js", "setup.py"):
            return "manifest_config"
        if "test" in name or "spec" in name or rel_path.startswith("test/") or rel_path.startswith("tests/"):
            return "test"
        if ext in (".md", ".txt", ".rst", ".adoc"):
            return "documentation"
        if ext in (".sh", ".bash", ".zsh", ".bat", ".cmd", ".ps1"):
            return "script"
        if ext in (".py", ".dart", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt", ".cpp", ".c", ".h"):
            return "source"
        return "asset_other"

    def detect_tech_stack(self, files: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Detects languages, frameworks, build systems, and entry points from manifests and files."""
        frameworks = set()
        languages = set()
        build_tools = set()
        entry_points = []

        file_names = {f["name"]: f["path"] for f in files}

        for fname, rel_path in file_names.items():
            if fname in self.MANIFEST_MAP:
                lang, btool = self.MANIFEST_MAP[fname]
                languages.add(lang)
                build_tools.add(btool)

                # Inspect manifest content if possible
                try:
                    content = self.fs.read_file(rel_path, max_bytes=10000)
                    if fname == "package.json":
                        pj = json.loads(content)
                        deps = {**pj.get("dependencies", {}), **pj.get("devDependencies", {})}
                        if "react" in deps:
                            frameworks.add("React")
                        if "next" in deps:
                            frameworks.add("Next.js")
                        if "vue" in deps:
                            frameworks.add("Vue")
                        if "three" in deps or "@types/three" in deps:
                            frameworks.add("Three.js")
                        if "express" in deps:
                            frameworks.add("Express")
                        if "tailwindcss" in deps:
                            frameworks.add("Tailwind CSS")
                        if "main" in pj:
                            entry_points.append(pj["main"])
                    elif fname == "pubspec.yaml":
                        if "flutter:" in content:
                            frameworks.add("Flutter")
                    elif fname == "pyproject.toml" or fname == "requirements.txt":
                        if "fastapi" in content:
                            frameworks.add("FastAPI")
                        if "flask" in content:
                            frameworks.add("Flask")
                        if "django" in content:
                            frameworks.add("Django")
                except Exception:
                    pass

            # Detect standard entry points
            if fname in ("main.py", "app.py", "server.py", "index.ts", "index.js", "main.dart", "App.tsx", "main.go", "main.rs"):
                entry_points.append(rel_path)

        return {
            "languages": sorted(list(languages)),
            "frameworks": sorted(list(frameworks)),
            "build_tools": sorted(list(build_tools)),
            "entry_points": sorted(list(set(entry_points))),
        }

    def extract_file_symbols_and_imports(self, rel_path: str, max_bytes: int = 150000) -> Dict[str, Any]:
        """
        Deterministically parses symbols (classes, functions, interfaces, components)
        and imports from source code files.
        """
        ext = os.path.splitext(rel_path)[1].lower()
        if ext not in (".py", ".dart", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"):
            return {"symbols": [], "imports": [], "todos": [], "purpose": ""}

        try:
            content = self.fs.read_file(rel_path, max_bytes=max_bytes)
        except Exception:
            return {"symbols": [], "imports": [], "todos": [], "purpose": ""}

        symbols = []
        imports = []
        todos = []

        # 1. Python AST parser
        if ext == ".py":
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        symbols.append(f"class {node.name}")
                    elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                        symbols.append(f"def {node.name}")
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            imports.append(alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            imports.append(node.module)
            except Exception:
                pass

        # 2. Regex parser for TypeScript, JavaScript, Dart, Go, Rust
        else:
            # Classes and Interfaces
            for m in re.finditer(r'(?:class|interface|struct|enum|trait)\s+([A-Za-z0-9_]+)', content):
                symbols.append(m.group(0))

            # Functions and Methods
            for m in re.finditer(r'(?:function|fn|def|void|Future<[^>]+>|Widget|const)\s+([A-Za-z0-9_]+)\s*\(', content):
                name = m.group(1)
                if name not in ("if", "for", "while", "switch", "catch"):
                    symbols.append(f"fn {name}")

            # Imports
            for m in re.finditer(r'''import\s+(?:\{[^}]+\}\s+from\s+)?['"]([^'"]+)['"]''', content):
                imports.append(m.group(1))
            for m in re.finditer(r'''require\(['"]([^'"]+)['"]\)''', content):
                imports.append(m.group(1))

        # Extract TODOs
        for line in content.splitlines():
            line_str = line.strip()
            if "TODO" in line_str or "FIXME" in line_str or "HACK" in line_str:
                todos.append(line_str[:120])

        # Infer concise purpose from header docstring or class name
        purpose = self._infer_purpose(rel_path, symbols, content)

        return {
            "symbols": sorted(list(set(symbols)))[:30],
            "imports": sorted(list(set(imports)))[:30],
            "todos": todos[:10],
            "purpose": purpose,
        }

    def _infer_purpose(self, rel_path: str, symbols: List[str], content: str) -> str:
        # Check first 5 lines for comments
        for line in content.splitlines()[:5]:
            l = line.strip()
            if l.startswith("///") or l.startswith("/*") or l.startswith("#") or l.startswith('"""'):
                clean = l.lstrip("/#*\" ").rstrip("/*\" ")
                if len(clean) > 8:
                    return clean

        name = os.path.basename(rel_path)
        if "test" in name:
            return "Unit & Integration test suite"
        if symbols:
            return f"Implements {symbols[0]}"
        return f"Component module {name}"

    def build_dependency_graph(
        self, files: List[Dict[str, Any]], parsed_meta: Dict[str, Dict[str, Any]]
    ) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
        """
        Builds forward (dependencies) and reverse (dependents) graphs mapping file-to-file relationships.
        """
        forward_graph: Dict[str, List[str]] = {}
        reverse_graph: Dict[str, List[str]] = {}

        file_paths = [f["path"] for f in files]
        path_set = set(file_paths)

        for p in file_paths:
            forward_graph[p] = []
            reverse_graph[p] = []

        for p, meta in parsed_meta.items():
            raw_imports = meta.get("imports", [])
            for imp in raw_imports:
                # Resolve relative or module imports to internal file paths
                matched_file = self._resolve_import_to_file(p, imp, path_set)
                if matched_file and matched_file != p:
                    if matched_file not in forward_graph[p]:
                        forward_graph[p].append(matched_file)
                    if p not in reverse_graph[matched_file]:
                        reverse_graph[matched_file].append(p)

        return forward_graph, reverse_graph

    def _resolve_import_to_file(self, source_path: str, import_str: str, path_set: Set[str]) -> Optional[str]:
        # Handle relative imports: ./foo or ../foo
        if import_str.startswith("."):
            src_dir = os.path.dirname(source_path)
            cand_base = os.path.normpath(os.path.join(src_dir, import_str))
            for ext in ("", ".ts", ".tsx", ".js", ".jsx", ".dart", ".py"):
                cand = cand_base + ext
                if cand in path_set:
                    return cand
                # Index file check
                cand_idx = os.path.join(cand_base, f"index{ext}")
                if cand_idx in path_set:
                    return cand_idx

        # Handle module name match (e.g. core.models or services/api_client)
        normalized = import_str.replace(".", "/")
        for ext in ("", ".py", ".dart", ".ts", ".js"):
            cand = normalized + ext
            if cand in path_set:
                return cand
            # Check suffix matches
            for p in path_set:
                if p.endswith("/" + cand) or p == cand:
                    return p

        return None

    def cluster_subsystems(
        self, files: List[Dict[str, Any]], parsed_meta: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Clusters files into coherent architectural subsystems based on folder hierarchy and coupling.
        """
        subsystems: Dict[str, Dict[str, Any]] = {}

        for f in files:
            path = f["path"]
            parts = path.split("/")
            if len(parts) > 1:
                # e.g., 'core/runtime', 'client/lib', 'app/api'
                subsystem_name = parts[0]
                if len(parts) > 2 and parts[0] in ("src", "lib", "core", "app", "client"):
                    subsystem_name = f"{parts[0]}/{parts[1]}"
            else:
                subsystem_name = "root"

            if subsystem_name not in subsystems:
                subsystems[subsystem_name] = {
                    "name": subsystem_name,
                    "description": f"Architectural subsystem for {subsystem_name}",
                    "files": [],
                    "total_size": 0,
                    "primary_language": f.get("extension", ""),
                }

            subsystems[subsystem_name]["files"].append(path)
            subsystems[subsystem_name]["total_size"] += f.get("size", 0)

        return subsystems

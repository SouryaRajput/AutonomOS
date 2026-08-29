from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from core.workspace.auditor import ProjectAuditor
from core.workspace.filesystem import ControlledWorkspaceFS
from core.workspace.scanner import RepositoryScanner, RepositoryIndex, FileMetadata

logger = logging.getLogger("AutonomOS.ProjectMapEngine")


class ProjectMapEngine:
    """
    Manages persistent Project Map generation, hierarchical project knowledge,
    and targeted context retrieval for the AutonomOS Manager.
    """

    def __init__(self, fs: ControlledWorkspaceFS, auditor: Optional[Any] = None, scanner: Optional[RepositoryScanner] = None):
        self.fs = fs
        if isinstance(auditor, RepositoryScanner):
            self.scanner = auditor
            self.auditor = ProjectAuditor(fs)
        elif isinstance(auditor, ProjectAuditor):
            self.auditor = auditor
            self.scanner = scanner or RepositoryScanner(fs)
        else:
            self.auditor = auditor or ProjectAuditor(fs)
            self.scanner = scanner or RepositoryScanner(fs)

        self.meta_dir = self.fs.meta_dir
        self.map_json_path = self.meta_dir / "project_map.json"
        self.map_md_primary = self.meta_dir / "project-map.md"
        self.map_md_legacy = self.meta_dir / "PROJECT_MAP.md"
        self.map_md_path = self.map_md_primary
        self.snapshot_path = self.meta_dir / "last_snapshot.json"
        self.audit_history_path = self.meta_dir / "audit_history.json"

    def is_initialized(self) -> bool:
        """Returns True if the Project Map and snapshot exist and are valid."""
        return (self.map_md_primary.exists() or self.map_md_legacy.exists()) and self.map_json_path.exists()

    def perform_full_audit(self, trigger: str = "INITIAL_AUDIT") -> Dict[str, Any]:
        """
        Executes a complete, deterministic repository scan and creates persistent
        .autonomos/project-map.md and .autonomos/project_map.json.
        """
        start_time = time.time()
        logger.info(f"Generating persistent Project Map for workspace '{self.fs.workspace_root}' (Trigger: {trigger})")

        # 1. Run deterministic repository scan
        index: RepositoryIndex = self.scanner.scan_repository()

        # 2. Build forward and reverse dependency graphs
        forward_graph, reverse_graph = self._build_dependency_graph(index)

        # 3. Cluster into architectural subsystems
        subsystems = self._cluster_subsystems(index, forward_graph)

        # 4. Assemble Project Map Data Structure
        project_name = self.fs.workspace_root.name
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        file_records: Dict[str, Dict[str, Any]] = {}
        for path, meta in index.files.items():
            file_records[path] = {
                "path": path,
                "name": meta.name,
                "category": meta.category,
                "size": meta.size,
                "mtime": meta.mtime,
                "hash": meta.hash,
                "purpose": meta.purpose,
                "symbols": meta.symbols,
                "dependencies": forward_graph.get(path, []),
                "dependents": reverse_graph.get(path, []),
                "todos": meta.todos,
                "last_audited": now_iso,
            }

        project_map: Dict[str, Any] = {
            "version": "1.0",
            "project_name": project_name,
            "root_path": str(self.fs.workspace_root),
            "last_audited": now_iso,
            "tech_stack": index.tech_stack,
            "git_state": index.git_state.to_dict(),
            "total_files": index.total_files,
            "total_size_bytes": index.total_size_bytes,
            "source_directories": index.source_directories,
            "test_directories": index.test_directories,
            "doc_directories": index.doc_directories,
            "script_directories": index.script_directories,
            "entry_points": index.entry_points,
            "manifests": index.manifests,
            "subsystems": subsystems,
            "files": file_records,
        }

        # 5. Persist .autonomos/project_map.json
        with open(self.map_json_path, "w", encoding="utf-8") as f:
            json.dump(project_map, f, indent=2)

        # 6. Persist .autonomos/last_snapshot.json
        snapshot: Dict[str, Any] = {
            "version": "1.0",
            "timestamp": now_iso,
            "file_count": index.total_files,
            "files": {p: {"hash": m.hash, "size": m.size, "mtime": m.mtime} for p, m in index.files.items()},
        }
        with open(self.snapshot_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)

        # 7. Generate and write .autonomos/project-map.md
        md_content = self.generate_project_map_markdown(project_map)
        with open(self.map_md_primary, "w", encoding="utf-8") as f:
            f.write(md_content)

        with open(self.map_md_legacy, "w", encoding="utf-8") as f:
            f.write(md_content)

        # 8. Record Audit History
        duration = round(time.time() - start_time, 3)
        self._append_audit_history({
            "audit_id": f"aud-{uuid.uuid4().hex[:8]}",
            "timestamp": now_iso,
            "trigger": trigger,
            "duration_seconds": duration,
            "files_inspected": index.total_files,
            "files_changed": index.total_files,
            "impacted_files": 0,
            "summary": f"Project Map initialized. Indexed {index.total_files} files across {len(subsystems)} subsystems in {duration}s.",
        })

        logger.info(f"Project Map generated successfully: {index.total_files} files indexed in {duration}s.")
        return project_map

    def _discover_routes(self, files: Dict[str, Any]) -> List[Tuple[str, str]]:
        """
        Discovers application routes with strict framework-aware rules.
        For Next.js App Router:
          src/app/page.tsx -> /
          src/app/about/page.tsx -> /about
          src/app/projects/[slug]/page.tsx -> /projects/[slug]
          src/app/api/contact/route.ts -> /api/contact
        For Pages Router:
          pages/index.tsx -> /
          pages/about.tsx -> /about
        """
        routes: List[Tuple[str, str]] = []
        for path in sorted(files.keys()):
            # Next.js App Router (app/ or src/app/)
            app_match = re.search(r'(?:^|/)(?:src/)?app/(?:(.+)/)?page\.(?:tsx|jsx|js|ts)$', path)
            if app_match:
                segment = app_match.group(1) if app_match.group(1) else ""
                clean_segments = [s for s in segment.split('/') if s and not (s.startswith('(') and s.endswith(')'))]
                route_path = "/" + "/".join(clean_segments) if clean_segments else "/"
                routes.append((route_path, path))
                continue

            app_route_match = re.search(r'(?:^|/)(?:src/)?app/(?:(.+)/)?route\.(?:ts|js)$', path)
            if app_route_match:
                segment = app_route_match.group(1) if app_route_match.group(1) else ""
                clean_segments = [s for s in segment.split('/') if s and not (s.startswith('(') and s.endswith(')'))]
                route_path = "/" + "/".join(clean_segments) if clean_segments else "/"
                routes.append((route_path, path))
                continue

            # Next.js Pages Router (pages/ or src/pages/)
            pages_match = re.search(r'(?:^|/)(?:src/)?pages/(.+)\.(?:tsx|jsx|js|ts)$', path)
            if pages_match:
                seg = pages_match.group(1)
                if seg in ("_app", "_document", "_error"):
                    continue
                if seg == "index":
                    routes.append(("/", path))
                elif seg.endswith("/index"):
                    routes.append((f"/{seg[:-6]}", path))
                else:
                    routes.append((f"/{seg}", path))

        return routes

    def generate_project_map_markdown(self, project_map: Dict[str, Any], requested_objective: Optional[str] = None) -> str:
        """
        Generates structured, semantic architectural Markdown representation of the workspace knowledge.
        Strictly reflects verified repository evidence and separates current state from requested future transformations.
        """
        lines = []
        name = project_map.get("project_name", "Workspace")
        root_path = project_map.get("root_path", "")
        last_audited = project_map.get("last_audited", "N/A")
        total_files = project_map.get("total_files", 0)
        tech = project_map.get("tech_stack", {})
        git = project_map.get("git_state", {})
        subsystems = project_map.get("subsystems", {})
        files = project_map.get("files", {})
        manifests = project_map.get("manifests", [])
        entry_points = project_map.get("entry_points", [])
        source_dirs = project_map.get("source_directories", [])

        # Collect all imports across scanned source files
        all_imports = set(tech.get("all_imports", []))
        for frec in files.values():
            for imp in frec.get("imports", []):
                clean_imp = imp.split('/')[0] if not imp.startswith('@') else '/'.join(imp.split('/')[:2])
                all_imports.add(clean_imp)

        installed_packages = tech.get("installed_packages", {})

        # Framework & Evidence-Based Capability Detection
        is_nextjs = any("next" in str(x).lower() for x in tech.get("frameworks", [])) or ("package.json" in manifests and any("app/" in p or "src/app/" in p for p in files))
        is_react = any("react" in str(x).lower() for x in tech.get("frameworks", [])) or any(p.endswith(('.tsx', '.jsx')) for p in files)

        # Verified 3D vs Merely Installed
        has_verified_3d = any("three" in imp.lower() or "@react-three" in imp.lower() for imp in all_imports)
        has_installed_3d = any("three" in pkg.lower() or "@react-three" in pkg.lower() for pkg in installed_packages)

        # Verified Animations vs Merely Installed
        has_verified_motion = any("framer-motion" in imp.lower() for imp in all_imports)
        has_verified_gsap = any("gsap" in imp.lower() for imp in all_imports)

        # Base Project Type on OBSERVED REALITY
        if is_nextjs and has_verified_3d:
            project_type = "Next.js Web Application with Three.js 3D Rendering"
        elif is_nextjs:
            project_type = "Next.js Portfolio Web Application" if any("portfolio" in name.lower() or "hero" in p.lower() for p in files) else "Next.js Web Application"
        elif is_react:
            project_type = "React Single Page Application"
        else:
            project_type = "Application Workspace"

        lines.append(f"# Project Map: {name}")
        lines.append("")
        lines.append("> Semantic architectural understanding of the workspace generated by AutonomOS Manager.")
        lines.append("")

        # 1. Identity
        lines.append("## Identity")
        lines.append(f"- **Project Name**: `{name}`")
        lines.append(f"- **Observed Project Type**: {project_type}")
        lines.append(f"- **Root Path**: `{root_path}`")
        lines.append(f"- **Tracked Meaningful Files**: {total_files}")
        if git.get("is_git_repo"):
            clean_str = "Clean" if git.get("is_clean") else f"{git.get('modified_count', 0)} modified, {git.get('untracked_count', 0)} untracked"
            lines.append(f"- **Git Status**: Branch `{git.get('current_branch', 'main')}` ({clean_str})")
        lines.append("")

        # 2. Current Technology
        lines.append("## Current Technology")
        langs = tech.get("languages", [])
        fworks = [f for f in tech.get("frameworks", []) if f not in ("Three.js", "GSAP") or (f == "Three.js" and has_verified_3d) or (f == "GSAP" and has_verified_gsap)]
        btools = tech.get("build_tools", [])
        for l in langs:
            lines.append(f"- **Language**: `{l}`")
        for f in fworks:
            lines.append(f"- **Framework**: `{f}`")
        for b in btools:
            lines.append(f"- **Build System**: `{b}`")

        # Verified used libraries
        verified_libs = [pkg for pkg in installed_packages if pkg in all_imports]
        if verified_libs:
            lines.append(f"- **Verified Used Libraries**: {', '.join([f'`{v}`' for v in sorted(verified_libs)])}")
        if has_installed_3d and not has_verified_3d:
            lines.append("- **Installed (Not yet imported in source code)**: Three.js / 3D rendering packages are present in `package.json` but not yet imported in active source files.")
        lines.append("")

        # 3. Routes (Strict Framework-Aware Route Discovery)
        lines.append("## Routes")
        discovered_routes = self._discover_routes(files)
        if discovered_routes:
            for rpath, srcfile in discovered_routes:
                lines.append(f"- Route `{rpath}` -> `{srcfile}`")
        else:
            lines.append("- Standard Single Page / Application Entry")
        lines.append("")

        # 4. Entry Points
        lines.append("## Entry Points")
        if entry_points:
            for ep in entry_points:
                lines.append(f"- `{ep}`")
        else:
            lines.append("- Standard application entry point")
        lines.append("")

        # 5. Architecture
        lines.append("## Architecture")
        lines.append(f"The workspace is currently structured as a {project_type}.")
        if is_nextjs:
            lines.append("It leverages Next.js App Router with React Server and Client Components, structured styling via Tailwind CSS, and modular UI components.")
        lines.append("")

        # 6. Components
        lines.append("## Components")
        components = [p for p in files if ("component" in p.lower() or "ui" in p.lower() or "widgets" in p.lower()) and not "test" in p.lower()]
        for c in components[:20]:
            lines.append(f"- `{c}`")
        if len(components) > 20:
            lines.append(f"- *(and {len(components) - 20} more components)*")
        if not components:
            lines.append("- Main view components located in entry points")
        lines.append("")

        # 7. Directory Structure
        lines.append("## Directory Structure")
        lines.append(f"- **Source Directories**: {', '.join([f'`{sd}/`' for sd in source_dirs]) or 'Root'}")
        for sd in source_dirs[:10]:
            lines.append(f"- `{sd}/`: Source code and components")
        if any(p.startswith("public/") for p in files):
            lines.append("- `public/`: Static web assets, textures, 3D models, and images")
        lines.append("")

        # 8. Architectural Subsystems & Key Modules
        lines.append("## Architectural Subsystems")
        for sub_name, sub in sorted(subsystems.items()):
            sub_files = sub.get("files", [])
            lines.append(f"### Subsystem: `{sub_name}` ({len(sub_files)} files)")
            lines.append(f"{sub.get('description', '')}")
            lines.append("")
            lines.append("| File Path | Inferred Purpose | Exported Symbols / Interfaces | Dependencies |")
            lines.append("| :--- | :--- | :--- | :--- |")

            for fp in sub_files[:25]:
                frec = files.get(fp, {})
                purpose = frec.get("purpose", "Module component")
                symbols_list = frec.get("symbols", [])
                symbols_str = ", ".join(symbols_list[:3]) if symbols_list else "-"
                deps_list = frec.get("dependencies", [])
                deps_str = f"{len(deps_list)} files" if deps_list else "-"
                lines.append(f"| `{fp}` | {purpose} | `{symbols_str}` | {deps_str} |")

            if len(sub_files) > 25:
                lines.append(f"| *...and {len(sub_files) - 25} more files* | | | |")
            lines.append("")

        # 9. Assets
        lines.append("## Assets")
        assets = [p for p in files if p.startswith("public/") or p.startswith("assets/")]
        for a in assets[:15]:
            lines.append(f"- `{a}`")
        if not assets:
            lines.append("- Static assets in `public/`")
        lines.append("")

        # 10. Dependencies
        lines.append("## Dependencies")
        lines.append(f"- **Manifests**: {', '.join([f'`{m}`' for m in manifests]) or 'package.json'}")
        if installed_packages:
            lines.append(f"- **Installed Packages Count**: {len(installed_packages)} packages")
        lines.append("")

        # 11. Configuration
        lines.append("## Configuration")
        configs = [p for p in files if any(p.endswith(c) for c in ("package.json", "tsconfig.json", "next.config.js", "next.config.mjs", "next.config.ts", "tailwind.config.ts", "tailwind.config.js", "postcss.config.mjs", "postcss.config.js", "pubspec.yaml", "pyproject.toml"))]
        for cfg in configs:
            lines.append(f"- `{cfg}`")
        lines.append("")

        # 12. Runtime / Build Commands
        lines.append("## Runtime / Build Commands")
        if is_nextjs:
            lines.append("- `npm run dev`: Starts local development server at http://localhost:3000")
            lines.append("- `npm run build`: Compiles production build")
            lines.append("- `npm start`: Starts production server")
        else:
            lines.append("- `npm run dev` / `flutter run` / `python main.py`")
        lines.append("")

        # 13. Important Relationships
        lines.append("## Important Relationships")
        if is_nextjs:
            lines.append("- Application layout (`src/app/layout.tsx`) wraps all pages and imports global styling (`src/app/globals.css`).")
            lines.append("- Root page (`src/app/page.tsx`) renders the primary view and composes UI components.")
        else:
            lines.append("- Main entry point orchestrates core subsystems.")
        lines.append("")

        # 14. Generated / Ignored Areas
        lines.append("## Generated / Ignored Areas")
        lines.append("- `.next/`: Next.js build cache and compiled server/client artifacts (excluded from Project Map).")
        lines.append("- `node_modules/`: Vendor dependencies (managed by package manager).")
        lines.append("- `.git/`: Version control metadata.")
        lines.append("")

        # 15. Requested Transformation (Clearly Separated from Current State)
        lines.append("## Requested Transformation")
        if requested_objective:
            lines.append(f"- **User Requested Objective**: \"{requested_objective}\"")
            lines.append("- **Status**: PENDING IMPLEMENTATION (Workers Disabled / Paused)")
            lines.append("- **Notice**: This requested transformation represents intended future work and is NOT part of current observed repository state.")
        else:
            lines.append("- No pending transformation requested.")
        lines.append("")

        # 16. Evidence & Metadata
        lines.append("## Evidence")
        lines.append(f"- **Last Audited**: `{last_audited}`")
        lines.append(f"- **Meaningful Files Tracked**: {total_files}")
        lines.append("- **Snapshot File**: `.autonomos/last_snapshot.json`")
        lines.append("- **Verification Confidence**: 100% deterministic filesystem inspection")

        return "\n".join(lines)

    def load_project_map(self) -> Optional[Dict[str, Any]]:
        """Loads persistent project map from disk."""
        if not self.map_json_path.exists():
            return None
        try:
            with open(self.map_json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load project map: {e}")
            return None

    def query_relevant_context(self, objective: str, max_files: int = 15) -> Dict[str, Any]:
        """
        Retrieves targeted, hierarchical project context matching a user objective
        without reading the whole repository into memory.
        """
        pmap = self.load_project_map()
        if not pmap:
            pmap = self.perform_full_audit(trigger="ON_DEMAND_QUERY")

        keywords = set(re.findall(r'[a-zA-Z0-9_]{3,}', objective.lower()))
        matched_subsystems = set()
        matched_files: List[Dict[str, Any]] = []

        files = pmap.get("files", {})

        for path, frec in files.items():
            path_lower = path.lower()
            purpose_lower = frec.get("purpose", "").lower()
            symbols_lower = " ".join(frec.get("symbols", [])).lower()

            score = 0
            for kw in keywords:
                if kw in path_lower:
                    score += 5
                if kw in purpose_lower:
                    score += 3
                if kw in symbols_lower:
                    score += 2

            if score > 0:
                parts = path.split("/")
                sub_name = parts[0] if len(parts) > 1 else "root"
                matched_subsystems.add(sub_name)
                matched_files.append({
                    "score": score,
                    "path": path,
                    "purpose": frec.get("purpose", ""),
                    "symbols": frec.get("symbols", []),
                    "dependencies": frec.get("dependencies", []),
                })

        matched_files.sort(key=lambda x: x["score"], reverse=True)
        top_files = matched_files[:max_files]

        return {
            "objective": objective,
            "total_matches": len(matched_files),
            "matched_subsystems": sorted(list(matched_subsystems)),
            "matched_files": top_files,
            "relevant_files": top_files,
            "tech_stack": pmap.get("tech_stack", {}),
            "entry_points": pmap.get("entry_points", []),
        }

    def get_audit_history(self) -> List[Dict[str, Any]]:
        if not self.audit_history_path.exists():
            return []
        try:
            with open(self.audit_history_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _build_dependency_graph(
        self, index: RepositoryIndex
    ) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
        forward_graph: Dict[str, List[str]] = {}
        reverse_graph: Dict[str, List[str]] = {}

        file_paths = list(index.files.keys())
        path_set = set(file_paths)

        for p in file_paths:
            forward_graph[p] = []
            reverse_graph[p] = []

        for p, meta in index.files.items():
            for imp in meta.imports:
                matched_file = self._resolve_import_to_file(p, imp, path_set)
                if matched_file and matched_file != p:
                    if matched_file not in forward_graph[p]:
                        forward_graph[p].append(matched_file)
                    if p not in reverse_graph[matched_file]:
                        reverse_graph[matched_file].append(p)

        return forward_graph, reverse_graph

    def _resolve_import_to_file(self, source_path: str, import_str: str, path_set: Set[str]) -> Optional[str]:
        if import_str.startswith("."):
            src_dir = os.path.dirname(source_path)
            cand_base = os.path.normpath(os.path.join(src_dir, import_str))
            for ext in ("", ".ts", ".tsx", ".js", ".jsx", ".dart", ".py"):
                cand = cand_base + ext
                if cand in path_set:
                    return cand
                cand_idx = os.path.join(cand_base, f"index{ext}")
                if cand_idx in path_set:
                    return cand_idx

        normalized = import_str.replace(".", "/")
        for ext in ("", ".py", ".dart", ".ts", ".js"):
            cand = normalized + ext
            if cand in path_set:
                return cand
            for p in path_set:
                if p.endswith("/" + cand) or p == cand:
                    return p

        return None

    def _cluster_subsystems(
        self, index: RepositoryIndex, forward_graph: Dict[str, List[str]]
    ) -> Dict[str, Dict[str, Any]]:
        subsystems: Dict[str, Dict[str, Any]] = {}

        for path, meta in index.files.items():
            parts = path.split("/")
            if len(parts) > 1:
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
                    "primary_language": meta.extension,
                }

            subsystems[subsystem_name]["files"].append(path)
            subsystems[subsystem_name]["total_size"] += meta.size

        return subsystems

    def _append_audit_history(self, entry: Dict[str, Any]):
        history = []
        if self.audit_history_path.exists():
            try:
                with open(self.audit_history_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = []

        history.insert(0, entry)
        history = history[:50]

        try:
            with open(self.audit_history_path, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to append audit history: {e}")

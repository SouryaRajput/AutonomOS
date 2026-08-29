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

    def generate_project_map_markdown(self, project_map: Dict[str, Any]) -> str:
        """
        Generates structured Markdown representation of the workspace knowledge.
        Provides compressed understanding without copying raw source code.
        """
        lines = []
        name = project_map.get("project_name", "Workspace")
        tech = project_map.get("tech_stack", {})
        git = project_map.get("git_state", {})
        subsystems = project_map.get("subsystems", {})
        files = project_map.get("files", {})
        manifests = project_map.get("manifests", [])
        entry_points = project_map.get("entry_points", [])
        source_dirs = project_map.get("source_directories", [])
        test_dirs = project_map.get("test_directories", [])
        doc_dirs = project_map.get("doc_directories", [])
        script_dirs = project_map.get("script_directories", [])

        lines.append(f"# Project Map: {name}")
        lines.append(f"**Root Path**: `{project_map.get('root_path', '')}`  ")
        lines.append(f"**Last Audited**: {project_map.get('last_audited', 'N/A')}  ")
        lines.append(f"**Total Tracked Files**: {project_map.get('total_files', 0)} files ({round(project_map.get('total_size_bytes', 0) / 1024, 1)} KB)  ")
        if git.get("is_git_repo"):
            clean_str = "Clean" if git.get("is_clean") else f"{git.get('modified_count', 0)} modified, {git.get('untracked_count', 0)} untracked"
            lines.append(f"**Git Status**: Branch `{git.get('current_branch', 'main')}` (Commit `{git.get('head_commit', 'HEAD')}`, {clean_str})  ")
        lines.append("")

        # 1. Tech Stack
        lines.append("## 1. Technology Stack & Frameworks")
        langs = ", ".join(tech.get("languages", [])) or "Detected generic"
        fworks = ", ".join(tech.get("frameworks", [])) or "Standard libraries"
        btools = ", ".join(tech.get("build_tools", [])) or "None"
        lines.append(f"- **Languages**: {langs}")
        lines.append(f"- **Frameworks / UI / Server**: {fworks}")
        lines.append(f"- **Build / Package Tools**: {btools}")
        lines.append("")

        # 2. Entry Points & Configurations
        lines.append("## 2. Entry Points & Configuration Manifests")
        if entry_points:
            lines.append(f"- **Primary Entry Points**: {', '.join([f'`{ep}`' for ep in entry_points])}")
        else:
            lines.append("- **Primary Entry Points**: None detected")
        if manifests:
            lines.append(f"- **Dependency Manifests**: {', '.join([f'`{m}`' for m in manifests])}")
        lines.append("")

        # 3. Project Structure
        lines.append("## 3. Project Structure & Directory Organization")
        lines.append(f"- **Source Directories**: {', '.join([f'`{d}/`' for d in source_dirs]) or 'Root'}")
        lines.append(f"- **Test Directories**: {', '.join([f'`{d}/`' for d in test_dirs]) or 'None'}")
        lines.append(f"- **Documentation Directories**: {', '.join([f'`{d}/`' for d in doc_dirs]) or 'Root'}")
        lines.append(f"- **Script Directories**: {', '.join([f'`{d}/`' for d in script_dirs]) or 'None'}")
        lines.append("")

        # 4. Major Subsystems & Architecture
        lines.append("## 4. Architectural Subsystems")
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

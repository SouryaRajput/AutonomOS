from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Set
import uuid

from core.workspace.auditor import ProjectAuditor
from core.workspace.filesystem import ControlledWorkspaceFS

logger = logging.getLogger("AutonomOS.ProjectMapEngine")


class ProjectMapEngine:
    """
    Manages the persistent Project Map, hierarchical project knowledge,
    and targeted context retrieval for the AutonomOS Manager.
    """

    def __init__(self, fs: ControlledWorkspaceFS, auditor: Optional[ProjectAuditor] = None):
        self.fs = fs
        self.auditor = auditor or ProjectAuditor(fs)

        self.map_json_path = self.fs.meta_dir / "project_map.json"
        self.map_md_path = self.fs.meta_dir / "PROJECT_MAP.md"
        self.snapshot_path = self.fs.meta_dir / "last_snapshot.json"
        self.audit_history_path = self.fs.meta_dir / "audit_history.json"

    def is_initialized(self) -> bool:
        """Returns True if the Project Map and snapshot exist and are valid."""
        return self.map_json_path.exists() and self.snapshot_path.exists()

    def perform_full_audit(self, trigger: str = "INITIAL_AUDIT") -> Dict[str, Any]:
        """
        Executes a complete, deterministic initial repository audit,
        builds the Project Map, generates Markdown documentation, and saves the snapshot.
        """
        start_time = time.time()
        logger.info(f"Starting full project audit for workspace '{self.fs.workspace_root}' (Trigger: {trigger})")

        # 1. Scan files
        files = self.auditor.scan_workspace_files()

        # 2. Detect tech stack
        tech_stack = self.auditor.detect_tech_stack(files)

        # 3. Extract symbols, imports, and purposes for each file
        parsed_meta: Dict[str, Dict[str, Any]] = {}
        for f in files:
            p = f["path"]
            if f["category"] in ("source", "test", "manifest_config"):
                meta = self.auditor.extract_file_symbols_and_imports(p)
                parsed_meta[p] = meta
            else:
                parsed_meta[p] = {
                    "symbols": [],
                    "imports": [],
                    "todos": [],
                    "purpose": f"Asset / {f['category']}",
                }

        # 4. Build forward and reverse dependency graphs
        forward_graph, reverse_graph = self.auditor.build_dependency_graph(files, parsed_meta)

        # 5. Cluster into subsystems
        subsystems = self.auditor.cluster_subsystems(files, parsed_meta)

        # 6. Build Project Map Data Structure
        project_name = self.fs.workspace_root.name
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        file_records: Dict[str, Dict[str, Any]] = {}
        for f in files:
            p = f["path"]
            pm = parsed_meta.get(p, {})
            file_records[p] = {
                "path": p,
                "name": f["name"],
                "category": f["category"],
                "size": f["size"],
                "mtime": f["mtime"],
                "hash": f["hash"],
                "purpose": pm.get("purpose", ""),
                "symbols": pm.get("symbols", []),
                "dependencies": forward_graph.get(p, []),
                "dependents": reverse_graph.get(p, []),
                "todos": pm.get("todos", []),
                "last_audited": now_iso,
            }

        project_map: Dict[str, Any] = {
            "version": "1.0",
            "project_name": project_name,
            "root_path": str(self.fs.workspace_root),
            "last_audited": now_iso,
            "tech_stack": tech_stack,
            "total_files": len(files),
            "subsystems": subsystems,
            "files": file_records,
        }

        # 7. Persist project_map.json
        with open(self.map_json_path, "w", encoding="utf-8") as f:
            json.dump(project_map, f, indent=2)

        # 8. Persist last_snapshot.json
        snapshot: Dict[str, Any] = {
            "version": "1.0",
            "timestamp": now_iso,
            "file_count": len(files),
            "files": {f["path"]: {"hash": f["hash"], "size": f["size"], "mtime": f["mtime"]} for f in files},
        }
        with open(self.snapshot_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)

        # 9. Generate PROJECT_MAP.md
        md_content = self.generate_project_map_markdown(project_map)
        with open(self.map_md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        # 10. Record Audit History
        duration = round(time.time() - start_time, 3)
        self._append_audit_history({
            "audit_id": f"aud-{uuid.uuid4().hex[:8]}",
            "timestamp": now_iso,
            "trigger": trigger,
            "duration_seconds": duration,
            "files_inspected": len(files),
            "files_changed": len(files),
            "impacted_files": 0,
            "summary": f"Full audit completed. Indexed {len(files)} files across {len(subsystems)} subsystems in {duration}s.",
        })

        logger.info(f"Full project audit completed: {len(files)} files indexed in {duration}s.")
        return project_map

    def generate_project_map_markdown(self, project_map: Dict[str, Any]) -> str:
        """Generates high-density structured Markdown map of the workspace."""
        lines = []
        name = project_map.get("project_name", "Workspace")
        tech = project_map.get("tech_stack", {})
        subsystems = project_map.get("subsystems", {})
        files = project_map.get("files", {})

        lines.append(f"# Project Map: {name}")
        lines.append(f"**Last Audited**: {project_map.get('last_audited', 'N/A')}  ")
        lines.append(f"**Total Tracked Files**: {project_map.get('total_files', 0)}  ")
        lines.append("")

        # Technology Stack
        lines.append("## Technology Stack & Architecture")
        langs = ", ".join(tech.get("languages", [])) or "Detected generic"
        fworks = ", ".join(tech.get("frameworks", [])) or "Standard modules"
        btools = ", ".join(tech.get("build_tools", [])) or "None"
        entries = ", ".join(tech.get("entry_points", [])) or "None detected"

        lines.append(f"- **Languages**: {langs}")
        lines.append(f"- **Frameworks / Libraries**: {fworks}")
        lines.append(f"- **Build / Package Tools**: {btools}")
        lines.append(f"- **Primary Entry Points**: `{entries}`")
        lines.append("")

        # Subsystems
        lines.append("## Architectural Subsystems")
        for sub_name, sub in sorted(subsystems.items()):
            file_count = len(sub.get("files", []))
            lines.append(f"### Subsystem: `{sub_name}` ({file_count} files)")
            lines.append(f"{sub.get('description', '')}")
            lines.append("")
            lines.append("| File | Purpose | Symbols / Exports | Dependencies |")
            lines.append("| :--- | :--- | :--- | :--- |")

            for fp in sub.get("files", [])[:20]:
                frec = files.get(fp, {})
                purpose = frec.get("purpose", "Module component")
                symbols_list = frec.get("symbols", [])
                symbols_str = ", ".join(symbols_list[:3]) if symbols_list else "-"
                deps_list = frec.get("dependencies", [])
                deps_str = f"{len(deps_list)} files" if deps_list else "-"
                lines.append(f"| `{fp}` | {purpose} | `{symbols_str}` | {deps_str} |")

            if len(sub.get("files", [])) > 20:
                lines.append(f"| *...and {len(sub.get('files', [])) - 20} more files* | | | |")
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

    def get_subsystem_info(self, subsystem_name: str) -> Optional[Dict[str, Any]]:
        pmap = self.load_project_map()
        if not pmap:
            return None
        return pmap.get("subsystems", {}).get(subsystem_name)

    def get_file_info(self, rel_path: str) -> Optional[Dict[str, Any]]:
        pmap = self.load_project_map()
        if not pmap:
            return None
        return pmap.get("files", {}).get(rel_path)

    def query_relevant_context(self, objective: str, max_files: int = 15) -> Dict[str, Any]:
        """
        Retrieves targeted, hierarchical project context matching a user objective
        without reading the whole repository into memory.
        """
        pmap = self.load_project_map()
        if not pmap:
            # Audit on the fly if needed
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
                    "dependents": frec.get("dependents", []),
                })

        matched_files.sort(key=lambda x: x["score"], reverse=True)

        return {
            "project_name": pmap.get("project_name"),
            "tech_stack": pmap.get("tech_stack"),
            "matched_subsystems": sorted(list(matched_subsystems)),
            "relevant_files": matched_files[:max_files],
            "total_matches": len(matched_files),
        }

    def _append_audit_history(self, entry: Dict[str, Any]):
        history = []
        if self.audit_history_path.exists():
            try:
                with open(self.audit_history_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = []

        history.insert(0, entry)
        # Keep last 50 audit entries
        history = history[:50]

        try:
            with open(self.audit_history_path, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save audit history: {e}")

    def get_audit_history(self) -> List[Dict[str, Any]]:
        if not self.audit_history_path.exists():
            return []
        try:
            with open(self.audit_history_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

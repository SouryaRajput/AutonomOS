from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from core.workspace.auditor import ProjectAuditor
from core.workspace.filesystem import ControlledWorkspaceFS
from core.workspace.project_map import ProjectMapEngine

logger = logging.getLogger("AutonomOS.IncrementalAuditEngine")


class IncrementalAuditEngine:
    """
    Incremental audit and change impact analysis engine for AutonomOS.
    Detects changes between the current filesystem state and the last snapshot,
    computes the impact surface using reverse dependency graphs, and audits ONLY
    affected files to maintain maximum context efficiency.
    """

    def __init__(self, fs: ControlledWorkspaceFS, map_engine: Optional[ProjectMapEngine] = None):
        self.fs = fs
        self.map_engine = map_engine or ProjectMapEngine(fs)
        self.auditor = self.map_engine.auditor

    def check_and_update(self, trigger: str = "SESSION_START") -> Dict[str, Any]:
        """
        Main entry point for session checks and post-operation verification:
        1. If not initialized -> run full initial audit.
        2. If initialized -> detect diff with last snapshot.
        3. If changes detected -> run targeted impact audit and update Project Map.
        4. If no changes -> return clean status immediately without reading unchanged code.
        """
        if not self.map_engine.is_initialized():
            return {
                "status": "INITIAL_AUDIT_PERFORMED",
                "changes_detected": True,
                "project_map": self.map_engine.perform_full_audit(trigger=trigger),
            }

        # 1. Detect Changes
        changes = self.detect_changes()
        if not changes["has_changes"]:
            logger.info(f"Incremental audit check: No filesystem changes detected (Trigger: {trigger}).")
            return {
                "status": "UP_TO_DATE",
                "changes_detected": False,
                "changes": changes,
                "project_map": self.map_engine.load_project_map(),
            }

        # 2. Compute Change Impact Surface
        impact = self.analyze_change_impact(changes)

        # 3. Perform Targeted Audit on Affected Files
        updated_map = self.execute_targeted_audit(changes, impact, trigger=trigger)

        return {
            "status": "INCREMENTAL_AUDIT_COMPLETED",
            "changes_detected": True,
            "changes": changes,
            "impact": impact,
            "project_map": updated_map,
        }

    def detect_changes(self) -> Dict[str, Any]:
        """
        Compares current workspace file state against `last_snapshot.json`.
        Categorizes files as added, modified, or deleted.
        """
        snapshot_path = self.map_engine.snapshot_path
        if not snapshot_path.exists():
            return {"has_changes": True, "added": [], "modified": [], "deleted": []}

        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                last_snapshot = json.load(f)
        except Exception:
            return {"has_changes": True, "added": [], "modified": [], "deleted": []}

        old_files: Dict[str, Dict[str, Any]] = last_snapshot.get("files", {})
        current_files = self.auditor.scan_workspace_files()
        curr_map = {f["path"]: f for f in current_files}

        added = []
        modified = []
        deleted = []

        # Check for added and modified files
        for p, cur_info in curr_map.items():
            if p not in old_files:
                added.append(p)
            else:
                old_info = old_files[p]
                # Fast check mtime & size first, then hash if size differs
                if cur_info["size"] != old_info.get("size") or cur_info["hash"] != old_info.get("hash"):
                    modified.append(p)

        # Check for deleted files
        for p in old_files:
            if p not in curr_map:
                deleted.append(p)

        has_changes = bool(added or modified or deleted)

        return {
            "has_changes": has_changes,
            "added": added,
            "modified": modified,
            "deleted": deleted,
            "total_changed_count": len(added) + len(modified) + len(deleted),
        }

    def analyze_change_impact(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traverses reverse dependency graph to identify direct dependents,
        related test suites, and coupled subsystems.
        """
        pmap = self.map_engine.load_project_map() or {}
        file_records: Dict[str, Dict[str, Any]] = pmap.get("files", {})

        changed_set = set(changes.get("added", []) + changes.get("modified", []) + changes.get("deleted", []))
        impacted_dependents = set()
        impacted_tests = set()
        impacted_subsystems = set()

        for p in changed_set:
            parts = p.split("/")
            if len(parts) > 1:
                impacted_subsystems.add(parts[0])

            frec = file_records.get(p, {})
            # Look up who depends on this changed file
            dependents = frec.get("dependents", [])
            for dep in dependents:
                if dep not in changed_set:
                    impacted_dependents.add(dep)
                    if "test" in dep.lower() or "spec" in dep.lower():
                        impacted_tests.add(dep)

        return {
            "changed_files": sorted(list(changed_set)),
            "impacted_dependents": sorted(list(impacted_dependents)),
            "impacted_tests": sorted(list(impacted_tests)),
            "impacted_subsystems": sorted(list(impacted_subsystems)),
            "total_impact_surface": len(changed_set) + len(impacted_dependents),
        }

    def execute_targeted_audit(
        self, changes: Dict[str, Any], impact: Dict[str, Any], trigger: str = "INCREMENTAL_AUDIT"
    ) -> Dict[str, Any]:
        """
        Re-audits ONLY changed + impacted files, updates Project Map entries,
        refreshes Markdown, and saves the new snapshot.
        """
        start_time = time.time()
        pmap = self.map_engine.load_project_map() or self.map_engine.perform_full_audit(trigger="FALLBACK_REINIT")
        file_records: Dict[str, Dict[str, Any]] = pmap.get("files", {})

        files_to_reanalyze = set(changes.get("added", []) + changes.get("modified", []) + impact.get("impacted_dependents", []))
        deleted_files = set(changes.get("deleted", []))

        # 1. Remove deleted files from Project Map
        for p in deleted_files:
            if p in file_records:
                del file_records[p]

        # 2. Re-analyze only changed and impacted files
        current_scan = {f["path"]: f for f in self.auditor.scan_workspace_files()}
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        for p in files_to_reanalyze:
            if p in current_scan:
                f_info = current_scan[p]
                meta = self.auditor.extract_file_symbols_and_imports(p)
                file_records[p] = {
                    "path": p,
                    "name": f_info["name"],
                    "category": f_info["category"],
                    "size": f_info["size"],
                    "mtime": f_info["mtime"],
                    "hash": f_info["hash"],
                    "purpose": meta.get("purpose", ""),
                    "symbols": meta.get("symbols", []),
                    "dependencies": [],  # Will be recalculated below
                    "dependents": file_records.get(p, {}).get("dependents", []),
                    "todos": meta.get("todos", []),
                    "last_audited": now_iso,
                }

        # 3. Update forward and reverse dependencies for affected files
        all_paths = set(file_records.keys())
        for p in files_to_reanalyze:
            if p in file_records:
                meta = self.auditor.extract_file_symbols_and_imports(p)
                raw_imports = meta.get("imports", [])
                deps = []
                for imp in raw_imports:
                    target = self.auditor._resolve_import_to_file(p, imp, all_paths)
                    if target and target != p and target not in deps:
                        deps.append(target)
                        # Add reverse dependency
                        if target in file_records and p not in file_records[target].get("dependents", []):
                            file_records[target].setdefault("dependents", []).append(p)
                file_records[p]["dependencies"] = deps

        # 4. Refresh subsystems
        subsystems = self.auditor.cluster_subsystems(list(current_scan.values()), {})
        pmap["total_files"] = len(file_records)
        pmap["last_audited"] = now_iso
        pmap["subsystems"] = subsystems
        pmap["files"] = file_records

        # 5. Persist updated project_map.json
        with open(self.map_engine.map_json_path, "w", encoding="utf-8") as f:
            json.dump(pmap, f, indent=2)

        # 6. Persist new snapshot
        snapshot: Dict[str, Any] = {
            "version": "1.0",
            "timestamp": now_iso,
            "file_count": len(current_scan),
            "files": {f["path"]: {"hash": f["hash"], "size": f["size"], "mtime": f["mtime"]} for f in current_scan.values()},
        }
        with open(self.map_engine.snapshot_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)

        # 7. Refresh PROJECT_MAP.md
        md_content = self.map_engine.generate_project_map_markdown(pmap)
        with open(self.map_engine.map_md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        # 8. Record in audit history
        duration = round(time.time() - start_time, 3)
        self.map_engine._append_audit_history({
            "audit_id": f"aud-{uuid.uuid4().hex[:8]}",
            "timestamp": now_iso,
            "trigger": trigger,
            "duration_seconds": duration,
            "files_inspected": len(files_to_reanalyze),
            "files_changed": changes.get("total_changed_count", 0),
            "impacted_files": len(impact.get("impacted_dependents", [])),
            "summary": f"Incremental audit: {changes.get('total_changed_count', 0)} files changed, {len(impact.get('impacted_dependents', []))} dependents updated in {duration}s.",
        })

        logger.info(f"Targeted incremental audit finished in {duration}s for {len(files_to_reanalyze)} files.")
        return pmap

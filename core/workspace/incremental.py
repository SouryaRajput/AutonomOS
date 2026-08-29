from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from core.workspace.filesystem import ControlledWorkspaceFS
from core.workspace.project_map import ProjectMapEngine
from core.workspace.scanner import RepositoryScanner, FileMetadata
from core.workspace.snapshot import SnapshotEngine, ProjectSnapshot, WorkspaceChanges

logger = logging.getLogger("AutonomOS.IncrementalAuditEngine")


class IncrementalAuditEngine:
    """
    Deterministic Incremental Audit Engine for AutonomOS Manager.
    Audits ONLY changed files and their directly affected dependents/subsystems,
    preserving context efficiency and eliminating redundant repository audits.
    """

    def __init__(
        self,
        fs: ControlledWorkspaceFS,
        map_engine: Optional[ProjectMapEngine] = None,
        snapshot_engine: Optional[SnapshotEngine] = None,
        scanner: Optional[RepositoryScanner] = None,
    ):
        self.fs = fs
        self.map_engine = map_engine or ProjectMapEngine(fs)
        self.scanner = scanner or RepositoryScanner(fs)
        self.snapshot_engine = snapshot_engine or SnapshotEngine(fs, scanner=self.scanner)
        self.auditor = self.map_engine.auditor

    def detect_changes(self) -> Dict[str, Any]:
        """Convenience method returning structured change dictionary."""
        return self.snapshot_engine.detect_changes().to_dict()

    def should_perform_full_audit(self) -> Tuple[bool, str]:
        """
        Determines whether a full audit is strictly necessary.
        Full audit is triggered only when:
        1. Project Map does not exist.
        2. Project Map JSON is unparseable or corrupted.
        3. Previous snapshot does not exist.
        4. Snapshot file is corrupted.
        """
        if not self.map_engine.is_initialized():
            return True, "INITIAL_PROJECT_INITIALIZATION"

        if not self.map_engine.map_json_path.exists():
            return True, "MISSING_PROJECT_MAP_JSON"

        pmap = self.map_engine.load_project_map()
        if pmap is None or not isinstance(pmap, dict) or "files" not in pmap:
            return True, "CORRUPTED_PROJECT_MAP"

        snapshot = self.snapshot_engine.load_last_snapshot()
        if snapshot is None:
            return True, "MISSING_SNAPSHOT"

        return False, ""

    def check_and_update(self, trigger: str = "SESSION_START", force_full: bool = False) -> Dict[str, Any]:
        """
        Main entry point for session verification and post-edit synchronization:
        1. Evaluates if full rebuild is required.
        2. If up-to-date, detects changes against last snapshot.
        3. If changes exist, computes affected impact surface and re-audits only affected files.
        4. Updates Project Map, writes project-map.md, updates snapshot, and records audit history.
        """
        start_time = time.time()

        needs_full, reason = self.should_perform_full_audit()
        if force_full or needs_full:
            full_trigger = reason if needs_full else f"FORCED_FULL_{trigger}"
            logger.info(f"Performing full repository audit. Reason: {full_trigger}")
            pmap = self.map_engine.perform_full_audit(trigger=full_trigger)
            return {
                "status": "FULL_AUDIT_PERFORMED",
                "trigger": trigger,
                "reason": full_trigger,
                "changes_detected": True,
                "files_audited": pmap.get("total_files", 0),
                "impacted_files": 0,
                "project_map": pmap,
            }

        # 1. Detect Changes via Snapshot Engine
        changes = self.snapshot_engine.detect_changes()
        if not changes.has_changes:
            logger.info(f"Incremental audit check: Zero changes detected (Trigger: {trigger}). No re-audit needed.")
            return {
                "status": "UP_TO_DATE",
                "trigger": trigger,
                "changes_detected": False,
                "changes": changes.to_dict(),
                "files_audited": 0,
                "impacted_files": 0,
                "project_map": self.map_engine.load_project_map(),
            }

        # 2. Compute Affected Impact Surface (Dependents & Subsystems)
        impact = self.analyze_change_impact(changes)

        # 3. Targeted Audit: Re-audit only changed + directly affected files
        updated_map = self.execute_targeted_audit(changes, impact, trigger=trigger, start_time=start_time)

        return {
            "status": "INCREMENTAL_AUDIT_COMPLETED",
            "trigger": trigger,
            "changes_detected": True,
            "changes": changes.to_dict(),
            "impact": impact,
            "files_audited": len(impact["files_to_reanalyze"]),
            "impacted_files": len(impact["impacted_dependents"]),
            "project_map": updated_map,
        }

    def analyze_change_impact(self, changes: WorkspaceChanges | Dict[str, Any]) -> Dict[str, Any]:
        """
        Traverses reverse dependency graph to identify direct dependents,
        related test suites, and coupled subsystems.
        """
        pmap = self.map_engine.load_project_map() or {}
        file_records: Dict[str, Dict[str, Any]] = pmap.get("files", {})

        added = changes.get("added", []) if isinstance(changes, dict) else changes.added
        modified = changes.get("modified", []) if isinstance(changes, dict) else changes.modified
        deleted = changes.get("deleted", []) if isinstance(changes, dict) else changes.deleted
        renamed = changes.get("renamed", []) if isinstance(changes, dict) else changes.renamed

        changed_set = set(added + modified + deleted)
        for ren in renamed:
            changed_set.add(ren["from"])
            changed_set.add(ren["to"])

        impacted_dependents: Set[str] = set()
        impacted_tests: Set[str] = set()
        impacted_subsystems: Set[str] = set()

        for p in changed_set:
            parts = p.split("/")
            if len(parts) > 1:
                impacted_subsystems.add(parts[0])

            frec = file_records.get(p, {})
            dependents = frec.get("dependents", [])
            for dep in dependents:
                if dep not in changed_set:
                    impacted_dependents.add(dep)
                    if "test" in dep.lower() or "spec" in dep.lower():
                        impacted_tests.add(dep)

        files_to_reanalyze = sorted(list(
            (set(added + modified) | impacted_dependents) - set(deleted)
        ))

        return {
            "changed_files": sorted(list(changed_set)),
            "impacted_dependents": sorted(list(impacted_dependents)),
            "impacted_tests": sorted(list(impacted_tests)),
            "impacted_subsystems": sorted(list(impacted_subsystems)),
            "files_to_reanalyze": files_to_reanalyze,
            "total_impact_surface": len(files_to_reanalyze) + len(deleted),
        }

    def execute_targeted_audit(
        self,
        changes: WorkspaceChanges | Dict[str, Any],
        impact: Dict[str, Any],
        trigger: str = "INCREMENTAL_AUDIT",
        start_time: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Re-audits ONLY changed and directly impacted files without reprocessing
        unaffected parts of the repository. Updates project map and snapshot.
        """
        t0 = start_time or time.time()
        pmap = self.map_engine.load_project_map() or self.map_engine.perform_full_audit(trigger="FALLBACK_REINIT")
        file_records: Dict[str, Dict[str, Any]] = pmap.get("files", {})

        files_to_reanalyze = impact["files_to_reanalyze"]
        deleted = changes.get("deleted", []) if isinstance(changes, dict) else changes.deleted
        renamed = changes.get("renamed", []) if isinstance(changes, dict) else changes.renamed
        added = changes.get("added", []) if isinstance(changes, dict) else changes.added
        modified = changes.get("modified", []) if isinstance(changes, dict) else changes.modified

        deleted_files = set(deleted)
        for ren in renamed:
            deleted_files.add(ren["from"])

        # 1. Remove deleted files from Project Map records
        for p in deleted_files:
            if p in file_records:
                del file_records[p]

        # Clean reverse dependencies pointing to deleted files
        for p, rec in file_records.items():
            rec["dependencies"] = [dep for dep in rec.get("dependencies", []) if dep not in deleted_files]
            rec["dependents"] = [dep for dep in rec.get("dependents", []) if dep not in deleted_files]

        # 2. Rescan and re-analyze ONLY changed + impacted files
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        for p in files_to_reanalyze:
            if not self.fs.exists(p):
                continue

            try:
                st = self.fs.stat_file(p)
                fsize = st["size"]
                fmtime = st["mtime"]
            except Exception:
                fsize = 0
                fmtime = 0.0

            fhash = self.fs.compute_file_hash(p)
            category = self.scanner.categorize_file(p)
            symbols, imports, todos, purpose = self.scanner._extract_metadata(p, category, fsize)

            file_records[p] = {
                "path": p,
                "name": os.path.basename(p),
                "category": category,
                "size": fsize,
                "mtime": fmtime,
                "hash": fhash,
                "purpose": purpose,
                "symbols": symbols,
                "dependencies": [],  # Computed in step 3
                "dependents": file_records.get(p, {}).get("dependents", []),
                "todos": todos,
                "last_audited": now_iso,
            }

        # 3. Update forward and reverse dependencies for re-analyzed files
        all_paths = set(file_records.keys())
        for p in files_to_reanalyze:
            if p in file_records:
                symbols, imports, _, _ = self.scanner._extract_metadata(p, file_records[p]["category"], file_records[p]["size"])
                deps = []
                for imp in imports:
                    target = self.map_engine._resolve_import_to_file(p, imp, all_paths)
                    if target and target != p and target not in deps:
                        deps.append(target)
                        # Add reverse dependency
                        if target in file_records and p not in file_records[target].get("dependents", []):
                            file_records[target].setdefault("dependents", []).append(p)
                file_records[p]["dependencies"] = deps

        # 4. Refresh architectural subsystems
        subsystems: Dict[str, Dict[str, Any]] = {}
        for p, rec in file_records.items():
            parts = p.split("/")
            sub_name = parts[0] if len(parts) > 1 else "root"
            if len(parts) > 2 and parts[0] in ("src", "lib", "core", "app", "client"):
                sub_name = f"{parts[0]}/{parts[1]}"

            if sub_name not in subsystems:
                subsystems[sub_name] = {
                    "name": sub_name,
                    "description": f"Architectural subsystem for {sub_name}",
                    "files": [],
                    "total_size": 0,
                    "primary_language": os.path.splitext(p)[1],
                }
            subsystems[sub_name]["files"].append(p)
            subsystems[sub_name]["total_size"] += rec.get("size", 0)

        # 5. Update Project Map Metadata
        pmap["total_files"] = len(file_records)
        pmap["last_audited"] = now_iso
        pmap["subsystems"] = subsystems
        pmap["files"] = file_records

        # Write updated project_map.json
        with open(self.map_engine.map_json_path, "w", encoding="utf-8") as f:
            json.dump(pmap, f, indent=2)

        # Write updated project-map.md and PROJECT_MAP.md
        md_content = self.map_engine.generate_project_map_markdown(pmap)
        with open(self.map_engine.map_md_primary, "w", encoding="utf-8") as f:
            f.write(md_content)
        with open(self.map_engine.map_md_legacy, "w", encoding="utf-8") as f:
            f.write(md_content)

        # 6. Capture and persist new Snapshot
        fresh_snapshot = self.snapshot_engine.capture_current_snapshot()
        self.snapshot_engine.save_snapshot(fresh_snapshot)

        # 7. Record structured audit history
        duration = round(time.time() - t0, 3)
        updated_sections = ["files", "subsystems", "project-map.md", "snapshot"]
        if impact["impacted_dependents"]:
            updated_sections.append("dependencies")

        self.map_engine._append_audit_history({
            "audit_id": f"aud-{uuid.uuid4().hex[:8]}",
            "timestamp": now_iso,
            "trigger": trigger,
            "duration_seconds": duration,
            "files_inspected": len(files_to_reanalyze),
            "files_changed": len(changes.added) + len(changes.modified) + len(changes.deleted) + len(changes.renamed),
            "impacted_files": len(impact["impacted_dependents"]),
            "changed_files": changes.added + changes.modified + changes.deleted,
            "affected_files": impact["impacted_dependents"],
            "updated_map_sections": updated_sections,
            "snapshot_created": fresh_snapshot.snapshot_id,
            "summary": f"Targeted incremental audit: {len(files_to_reanalyze)} files updated ({len(impact['impacted_dependents'])} dependents) in {duration}s.",
        })

        logger.info(f"Targeted incremental audit completed in {duration}s for {len(files_to_reanalyze)} files.")
        return pmap

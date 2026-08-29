from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.dto.workspace import AuditHistoryItemDTO, FileDetailDTO, SubsystemDTO, WorkspaceStatusDTO
from core.runtime.workforce_runtime import WorkforceRuntime
from core.workspace.auditor import ProjectAuditor
from core.workspace.filesystem import ControlledWorkspaceFS
from core.workspace.incremental import IncrementalAuditEngine
from core.workspace.project_map import ProjectMapEngine

logger = logging.getLogger("AutonomOS.WorkspaceService")


class WorkspaceService:
    """
    Application service managing the active workspace, project maps,
    incremental audits, and workspace filesystem operations.
    """

    def __init__(self, runtime: WorkforceRuntime):
        self.runtime = runtime
        self._active_folder = os.getcwd()
        self._init_engines(self._active_folder)

    def _init_engines(self, folder_path: str):
        self._active_folder = folder_path
        self.fs = ControlledWorkspaceFS(folder_path)
        self.auditor = ProjectAuditor(self.fs)
        self.map_engine = ProjectMapEngine(self.fs, self.auditor)
        self.incremental_engine = IncrementalAuditEngine(self.fs, self.map_engine)

    def set_active_folder(self, folder_path: str) -> WorkspaceStatusDTO:
        resolved = Path(folder_path).resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"Folder path '{folder_path}' does not exist.")
        if not resolved.is_dir():
            raise NotADirectoryError(f"Path '{folder_path}' is not a directory.")

        self._init_engines(str(resolved))
        return self.get_status()

    def get_status(self) -> WorkspaceStatusDTO:
        is_init = self.map_engine.is_initialized()
        pmap = self.map_engine.load_project_map() if is_init else None

        return WorkspaceStatusDTO(
            active_path=self._active_folder,
            is_valid=Path(self._active_folder).exists(),
            is_initialized=is_init,
            project_name=Path(self._active_folder).name,
            total_files=pmap.get("total_files", 0) if pmap else 0,
            last_audited=pmap.get("last_audited") if pmap else None,
            tech_stack=pmap.get("tech_stack", {}) if pmap else {},
        )

    def run_audit(self, full: bool = False, trigger: str = "MANUAL_TRIGGER") -> Dict[str, Any]:
        if full or not self.map_engine.is_initialized():
            pmap = self.map_engine.perform_full_audit(trigger=trigger)
            return {
                "status": "FULL_AUDIT_COMPLETED",
                "project_map": pmap,
            }
        else:
            return self.incremental_engine.check_and_update(trigger=trigger)

    def get_project_map_json(self) -> Optional[Dict[str, Any]]:
        return self.map_engine.load_project_map()

    def get_project_map_markdown(self) -> str:
        if self.map_engine.map_md_path.exists():
            return self.map_engine.map_md_path.read_text(encoding="utf-8")
        pmap = self.map_engine.load_project_map()
        if pmap:
            return self.map_engine.generate_project_map_markdown(pmap)
        return "# Project Map Not Initialized\n\nRun an initial audit to generate the project map."

    def list_subsystems(self) -> List[Dict[str, Any]]:
        pmap = self.map_engine.load_project_map()
        if not pmap:
            return []
        subsystems = pmap.get("subsystems", {})
        results = []
        for name, data in subsystems.items():
            results.append(SubsystemDTO(
                name=name,
                description=data.get("description", ""),
                file_count=len(data.get("files", [])),
                files=data.get("files", []),
                total_size=data.get("total_size", 0),
                primary_language=data.get("primary_language", ""),
            ).to_dict())
        return results

    def get_file_detail(self, rel_path: str) -> Optional[Dict[str, Any]]:
        pmap = self.map_engine.load_project_map()
        if not pmap:
            return None
        frec = pmap.get("files", {}).get(rel_path)
        if not frec:
            return None
        return FileDetailDTO(
            path=frec["path"],
            name=frec["name"],
            category=frec.get("category", ""),
            size=frec.get("size", 0),
            mtime=frec.get("mtime", 0.0),
            hash=frec.get("hash", ""),
            purpose=frec.get("purpose", ""),
            symbols=frec.get("symbols", []),
            dependencies=frec.get("dependencies", []),
            dependents=frec.get("dependents", []),
            todos=frec.get("todos", []),
            last_audited=frec.get("last_audited", ""),
        ).to_dict()

    def get_audit_history(self) -> List[Dict[str, Any]]:
        return self.map_engine.get_audit_history()

    def query_context(self, objective: str) -> Dict[str, Any]:
        return self.map_engine.query_relevant_context(objective)

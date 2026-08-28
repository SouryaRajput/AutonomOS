"""Storage inspection service — safe metadata and disk usage metrics."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Optional

from app.dto.errors import AppException, normalize_error
from app.dto.storage import StorageInfoDTO

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime


class StorageService:
    """Thin facade for inspecting project storage, database, and artifact sizes."""

    def __init__(self, runtime: WorkforceRuntime):
        self._runtime = runtime

    def get_storage_info(self, project_id: str) -> dict[str, Any]:
        """Compute disk usage and storage locations for a project."""
        try:
            proj = self._runtime.projects.get_project(project_id)
            root_path = proj.root_path

            # Determine DB path if SQLite
            db_path = getattr(self._runtime.store, "db_path", ":memory:")
            db_size = 0
            if db_path != ":memory:" and os.path.exists(db_path):
                db_size = os.path.getsize(db_path)

            # Artifacts inspection
            artifacts_dir = os.path.join(root_path, ".autonomos", "artifacts")
            artifacts = self._runtime.store.list_artifacts_for_project(project_id)
            art_count = len(artifacts)
            art_size = 0
            for a in artifacts:
                if a.path and os.path.exists(a.path):
                    art_size += os.path.getsize(a.path)

            tasks = self._runtime.tasks.list_tasks(project_id)

            info = StorageInfoDTO(
                project_id=project_id,
                root_path=root_path,
                database_path=db_path,
                artifact_storage_path=artifacts_dir,
                database_size_bytes=db_size,
                artifacts_size_bytes=art_size,
                total_size_bytes=db_size + art_size,
                artifact_count=art_count,
                task_count=len(tasks),
            )
            return info.to_dict()
        except Exception as e:
            raise AppException(normalize_error(e)) from e

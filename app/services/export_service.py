"""Export and import service for portable, secure project bundles."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import TYPE_CHECKING, Any, Optional
import zipfile

from app.dto.errors import AppException, normalize_error
from app.dto.storage import ProjectBundleMetadataDTO
from core.enums import ArtifactType, TaskStatus
from core.errors import ValidationError
from core.models import Artifact, Task

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime


class ExportImportService:
    """Handles safe serialization, packaging, and validation of project bundles."""

    def __init__(self, runtime: WorkforceRuntime):
        self._runtime = runtime

    def export_project_bundle(self, project_id: str, target_zip_path: str) -> dict[str, Any]:
        """Export project tasks, workflows, artifacts, and metadata to a portable zip bundle (excluding secrets)."""
        try:
            proj = self._runtime.projects.get_project(project_id)
            tasks = self._runtime.tasks.list_tasks(project_id)
            workflows = self._runtime.workflows.list_workflows(project_id)
            artifacts = self._runtime.store.list_artifacts_for_project(project_id)

            metadata = {
                "bundle_version": "1.0.0",
                "schema_version": 16,
                "project": proj.to_dict(),
                "tasks": [t.to_dict() for t in tasks],
                "workflows": [w.to_dict() for w in workflows],
                "artifacts": [a.to_dict() for a in artifacts],
                "has_secrets": False,  # Secrets are strictly excluded
            }

            os.makedirs(os.path.dirname(os.path.abspath(target_zip_path)), exist_ok=True)

            with zipfile.ZipFile(target_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                # 1. Write manifest.json
                zf.writestr("manifest.json", json.dumps(metadata, indent=2))

                # 2. Write artifact contents
                for a in artifacts:
                    content = None
                    if a.path and os.path.exists(a.path):
                        try:
                            with open(a.path, "r", encoding="utf-8", errors="replace") as f:
                                content = f.read()
                        except Exception:
                            pass
                    if content is not None:
                        try:
                            rel = os.path.relpath(os.path.realpath(a.path), os.path.realpath(proj.root_path))
                            safe_rel = os.path.normpath(rel).lstrip("/\\")
                        except Exception:
                            safe_rel = os.path.basename(a.path)
                        if not safe_rel.startswith(".."):
                            zf.writestr(f"artifacts/{safe_rel}", content)

            return {
                "exported": True,
                "target_path": target_zip_path,
                "task_count": len(tasks),
                "workflow_count": len(workflows),
                "artifact_count": len(artifacts),
            }
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def import_project_bundle(self, bundle_zip_path: str, target_root_path: str) -> dict[str, Any]:
        """Validate and import a project bundle into the runtime."""
        try:
            if not os.path.exists(bundle_zip_path):
                raise ValidationError(f"Bundle file '{bundle_zip_path}' not found.")

            with zipfile.ZipFile(bundle_zip_path, "r") as zf:
                namelist = zf.namelist()
                if "manifest.json" not in namelist:
                    raise ValidationError("Invalid project bundle: missing manifest.json")

                manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))

                # Validate bundle schema
                if manifest_data.get("bundle_version") != "1.0.0":
                    raise ValidationError(f"Unsupported bundle version: {manifest_data.get('bundle_version')}")

                proj_data = manifest_data.get("project", {})
                new_proj_id = proj_data.get("id")

                # Create or register project
                os.makedirs(target_root_path, exist_ok=True)
                proj = self._runtime.projects.create_project(
                    name=proj_data.get("name", "Imported Project"),
                    root_path=target_root_path,
                    description=proj_data.get("description", "Imported from bundle"),
                    configuration=proj_data.get("configuration", {}),
                )

                # Import Tasks
                tasks_imported = 0
                for t_dict in manifest_data.get("tasks", []):
                    try:
                        self._runtime.tasks.create_task(
                            project_id=proj.id,
                            title=t_dict.get("title", "Task"),
                            objective=t_dict.get("objective", ""),
                            priority=t_dict.get("priority", "NORMAL"),
                            risk=t_dict.get("risk", "LOW"),
                        )
                        tasks_imported += 1
                    except Exception:
                        pass

                # Import Artifacts and files
                artifacts_imported = 0
                imported_tasks = self._runtime.tasks.list_tasks(proj.id)
                fallback_task_id = imported_tasks[0].id if imported_tasks else f"task-import-{proj.id[:6]}"
                fallback_worker_id = "worker.programmer"

                for a_dict in manifest_data.get("artifacts", []):
                    rel_path = a_dict.get("path") or a_dict.get("relative_path", "")
                    safe_rel = os.path.normpath(rel_path).lstrip("/\\")
                    if safe_rel and not safe_rel.startswith(".."):
                        zip_art_path = f"artifacts/{safe_rel}"
                        content = ""
                        if zip_art_path in namelist:
                            content = zf.read(zip_art_path).decode("utf-8", errors="replace")

                        t_id = a_dict.get("task_id") or fallback_task_id
                        w_id = a_dict.get("worker_id") or fallback_worker_id

                        self._runtime.artifacts.register_artifact(
                            project_id=proj.id,
                            task_id=t_id,
                            worker_id=w_id,
                            artifact_type=ArtifactType(a_dict.get("type", "REPORT")),
                            relative_path=safe_rel,
                            description=a_dict.get("description", ""),
                            content=content,
                            base_dir=target_root_path,
                        )
                        artifacts_imported += 1

                return {
                    "imported": True,
                    "project_id": proj.id,
                    "project_name": proj.name,
                    "tasks_imported": tasks_imported,
                    "artifacts_imported": artifacts_imported,
                }
        except Exception as e:
            raise AppException(normalize_error(e)) from e

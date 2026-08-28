"""Project service — delegates to WorkforceRuntime for project operations."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from app.dto.errors import AppError, AppException, ErrorCode, normalize_error

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime


class ProjectService:
    """Thin facade for project operations."""

    def __init__(self, runtime: WorkforceRuntime):
        self._runtime = runtime

    def create_project(
        self,
        name: str,
        root_path: str,
        description: str = "",
        configuration: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Create a new project. Returns project as dict."""
        try:
            if not name or not name.strip():
                raise AppException(AppError(
                    code=ErrorCode.VALIDATION_ERROR,
                    message="Project name is required",
                    user_message="Please enter a project name.",
                    field_errors={"name": "Required"},
                ))
            project = self._runtime.create_project(
                name=name.strip(),
                root_path=root_path,
                description=description,
                configuration=configuration,
            )
            tasks = self._runtime.store.list_tasks(project_id=project.id)
            workers = self._runtime.store.list_workers()
            return {
                **project.to_dict(),
                "task_count": len(tasks),
                "worker_count": len(workers),
            }
        except AppException:
            raise
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def get_project(self, project_id: str) -> dict[str, Any]:
        """Get project by ID. Returns project as dict."""
        try:
            project = self._runtime.projects.get_project(project_id)
            tasks = self._runtime.store.list_tasks(project_id=project.id)
            workers = self._runtime.store.list_workers()
            return {
                **project.to_dict(),
                "task_count": len(tasks),
                "worker_count": len(workers),
            }
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def list_projects(self) -> list[dict[str, Any]]:
        """List all projects as summary dicts."""
        try:
            projects = self._runtime.store.list_projects()
            result = []
            for p in projects:
                tasks = self._runtime.store.list_tasks(project_id=p.id)
                result.append({
                    "id": p.id,
                    "name": p.name,
                    "status": p.status.value if hasattr(p.status, 'value') else p.status,
                    "task_count": len(tasks),
                    "description": p.description,
                    "created_at": p.created_at,
                })
            return result
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def update_project(self, project_id: str, name: Optional[str] = None, description: Optional[str] = None) -> dict[str, Any]:
        """Update project name or description."""
        try:
            project = self._runtime.projects.get_project(project_id)
            if name is not None:
                project.name = name.strip()
            if description is not None:
                project.description = description
            from core.models import utc_now
            project.updated_at = utc_now()
            self._runtime.store.save_project(project)
            return self.get_project(project_id)
        except AppException:
            raise
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def archive_project(self, project_id: str) -> dict[str, Any]:
        """Archive a project (safe — does not delete)."""
        try:
            from core.enums import ProjectStatus
            project = self._runtime.projects.get_project(project_id)
            project.status = ProjectStatus.ARCHIVED
            from core.models import utc_now
            project.updated_at = utc_now()
            self._runtime.store.save_project(project)
            return self.get_project(project_id)
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def get_project_activity(self, project_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent activity for a project."""
        try:
            items = self._runtime.get_project_activity(project_id, limit=limit)
            return [item.to_dict() for item in items]
        except Exception as e:
            raise AppException(normalize_error(e)) from e

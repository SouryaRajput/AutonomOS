from pathlib import Path
from typing import Any, Optional

from core.enums import ProjectStatus
from core.errors import ProjectAlreadyExistsError, ProjectNotFoundError
from core.models import Project, new_id, utc_now
from core.storage.base import Store


class ProjectRegistry:
    """Manages project workspaces and metadata."""

    def __init__(self, store: Store):
        self.store = store

    def create_project(
        self,
        name: str,
        root_path: str,
        description: str = "",
        project_id: Optional[str] = None,
        configuration: Optional[dict[str, Any]] = None,
    ) -> Project:
        pid = project_id or new_id()
        existing = self.store.get_project(pid)
        if existing:
            raise ProjectAlreadyExistsError(pid)

        # Ensure directory exists on filesystem
        resolved_path = str(Path(root_path).resolve())
        Path(resolved_path).mkdir(parents=True, exist_ok=True)

        project = Project(
            id=pid,
            name=name,
            description=description,
            root_path=resolved_path,
            status=ProjectStatus.ACTIVE,
            configuration=configuration or {},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.store.save_project(project)
        return project

    def get_project(self, project_id: str) -> Project:
        project = self.store.get_project(project_id)
        if not project:
            raise ProjectNotFoundError(project_id)
        return project

    def list_projects(self) -> list[Project]:
        return self.store.list_projects()

    def update_project(
        self,
        project_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[ProjectStatus] = None,
        configuration: Optional[dict[str, Any]] = None,
    ) -> Project:
        project = self.get_project(project_id)
        if name is not None:
            project.name = name
        if description is not None:
            project.description = description
        if status is not None:
            project.status = status
        if configuration is not None:
            project.configuration = configuration
        project.updated_at = utc_now()
        self.store.save_project(project)
        return project

    def delete_project(self, project_id: str) -> bool:
        # Check exists
        self.get_project(project_id)
        return self.store.delete_project(project_id)

"""Artifact service — delegates to WorkforceRuntime for artifact/evidence queries."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from app.dto.errors import AppException, normalize_error

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime


class ArtifactService:
    """Thin facade for artifact and evidence queries."""

    def __init__(self, runtime: WorkforceRuntime):
        self._runtime = runtime

    def list_artifacts(self, project_id: Optional[str] = None, task_id: Optional[str] = None) -> list[dict[str, Any]]:
        try:
            if task_id:
                artifacts = self._runtime.store.list_artifacts_for_task(task_id)
            elif project_id:
                artifacts = self._runtime.store.list_artifacts_for_project(project_id)
            else:
                artifacts = []
            return [a.to_dict() for a in artifacts]
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        try:
            a = self._runtime.store.get_artifact(artifact_id)
            if not a:
                from app.dto.errors import AppError, ErrorCode
                raise AppException(AppError(
                    code=ErrorCode.NOT_FOUND,
                    message=f"Artifact {artifact_id} not found",
                    user_message="Artifact not found.",
                ))
            return a.to_dict()
        except AppException:
            raise
        except Exception as e:
            raise AppException(normalize_error(e)) from e

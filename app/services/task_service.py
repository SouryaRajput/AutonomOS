"""Task service — delegates to WorkforceRuntime for task operations."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from app.dto.errors import AppException, normalize_error

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime


class TaskService:
    """Thin facade for task operations."""

    def __init__(self, runtime: WorkforceRuntime):
        self._runtime = runtime

    def create_task(
        self,
        project_id: str,
        title: str,
        objective: str = "",
        priority: int = 1,
        risk: str = "LOW",
        dependencies: Optional[list[str]] = None,
        max_attempts: int = 3,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        try:
            from core.enums import RiskLevel
            task = self._runtime.create_task(
                project_id=project_id,
                title=title,
                objective=objective,
                priority=priority,
                risk=RiskLevel(risk),
                dependencies=dependencies,
                max_attempts=max_attempts,
                metadata=metadata,
            )
            return self._task_to_dict(task)
        except AppException:
            raise
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def get_task(self, task_id: str) -> dict[str, Any]:
        try:
            task = self._runtime.tasks.get_task(task_id)
            return self._task_to_dict(task)
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def list_tasks(self, project_id: Optional[str] = None, status: Optional[str] = None) -> list[dict[str, Any]]:
        try:
            from core.enums import TaskStatus
            status_filter = TaskStatus(status) if status else None
            tasks = self._runtime.store.list_tasks(project_id=project_id, status=status_filter)
            return [self._task_to_dict(t) for t in tasks]
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def get_task_timeline(self, task_id: str) -> list[dict[str, Any]]:
        try:
            events = self._runtime.get_task_timeline(task_id)
            return [e.to_dict() for e in events]
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def cancel_task(self, task_id: str, reason: str = "User cancellation") -> dict[str, Any]:
        try:
            task = self._runtime.tasks.cancel_task(task_id, reason=reason)
            return self._task_to_dict(task)
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def _task_to_dict(self, task) -> dict[str, Any]:
        worker_name = None
        if task.assigned_worker:
            try:
                w = self._runtime.workers.get_worker(task.assigned_worker)
                worker_name = w.name
            except Exception:
                worker_name = task.assigned_worker
        d = task.to_dict()
        d["assigned_worker_name"] = worker_name
        return d

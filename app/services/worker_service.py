"""Worker service — delegates to WorkforceRuntime for worker operations."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.dto.errors import AppException, normalize_error

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime


class WorkerService:
    """Thin facade for worker operations."""

    def __init__(self, runtime: WorkforceRuntime):
        self._runtime = runtime

    def list_workers(self) -> list[dict[str, Any]]:
        try:
            workers = self._runtime.store.list_workers()
            return [self._worker_to_dict(w) for w in workers]
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def get_worker(self, worker_id: str) -> dict[str, Any]:
        try:
            w = self._runtime.workers.get_worker(worker_id)
            return self._worker_to_dict(w)
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def _worker_to_dict(self, manifest) -> dict[str, Any]:
        task_title = None
        if manifest.active_task_id:
            try:
                t = self._runtime.tasks.get_task(manifest.active_task_id)
                task_title = t.title
            except Exception:
                pass
        d = manifest.to_dict()
        d["active_task_title"] = task_title
        return d

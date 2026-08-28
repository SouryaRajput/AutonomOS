"""Event service — delegates to WorkforceRuntime for event queries."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

from app.dto.errors import AppException, normalize_error

if TYPE_CHECKING:
    from core.events.model import Event
    from core.runtime.workforce_runtime import WorkforceRuntime


class EventService:
    """Thin facade for event and activity feed queries."""

    def __init__(self, runtime: WorkforceRuntime):
        self._runtime = runtime

    def get_events(
        self,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        since_sequence: Optional[int] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        try:
            events = self._runtime.get_events(
                project_id=project_id,
                task_id=task_id,
                worker_id=worker_id,
                since_sequence=since_sequence,
                limit=limit,
            )
            return [e.to_dict() for e in events]
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def get_activity_feed(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            items = self._runtime.get_activity_feed(limit=limit)
            return [item.to_dict() for item in items]
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def get_causal_chain(self, event_id: str) -> list[dict[str, Any]]:
        try:
            events = self._runtime.get_causal_chain(event_id)
            return [e.to_dict() for e in events]
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def subscribe(self, callback: Callable[[Event], None]) -> Callable[[], None]:
        """Subscribe to real-time events. Returns unsubscribe function."""
        self._runtime.subscribe_events(callback)
        # Note: current runtime doesn't support unsubscribe, so this is a no-op
        def _unsubscribe():
            pass
        return _unsubscribe

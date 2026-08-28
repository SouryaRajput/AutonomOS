"""Policy service — delegates to AutonomyService for policy and emergency stop."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from app.dto.errors import AppException, normalize_error

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime


class PolicyService:
    """Thin facade for autonomy policy and emergency stop operations."""

    def __init__(self, runtime: WorkforceRuntime):
        self._runtime = runtime

    def get_policy(self, project_id: str) -> dict[str, Any]:
        try:
            policy = self._runtime.autonomy.get_policy_for_project(project_id)
            return policy.to_dict()
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def update_policy(self, project_id: str, autonomy_level: Optional[str] = None, **kwargs) -> dict[str, Any]:
        try:
            from core.autonomy.types import AutonomyLevel
            policy = self._runtime.autonomy.get_policy_for_project(project_id)
            if autonomy_level:
                policy.autonomy_level = AutonomyLevel(autonomy_level)
            from core.autonomy.model import utc_now
            policy.updated_at = utc_now()
            policy.version += 1
            if hasattr(self._runtime.store, "save_autonomy_policy"):
                self._runtime.store.save_autonomy_policy(policy)
            return policy.to_dict()
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def emergency_stop(self, project_id: str, reason: str = "User initiated") -> dict[str, Any]:
        try:
            self._runtime.autonomy.set_emergency_stop(reason=reason, actor="user")
            return {"stopped": True, "reason": reason}
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def clear_emergency_stop(self, project_id: str) -> dict[str, Any]:
        try:
            self._runtime.autonomy.clear_emergency_stop(actor="user")
            return {"stopped": False}
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def is_emergency_stopped(self) -> bool:
        return self._runtime.autonomy.is_emergency_stopped

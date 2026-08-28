"""Approval service — delegates to AutonomyService for approval and user input operations."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from app.dto.errors import AppException, normalize_error

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime


class ApprovalService:
    """Thin facade for approval, user-input, and decision operations."""

    def __init__(self, runtime: WorkforceRuntime):
        self._runtime = runtime

    def list_pending_approvals(self, project_id: str) -> list[dict[str, Any]]:
        try:
            reqs = self._runtime.autonomy.list_pending_approvals(project_id)
            return [r.to_dict() for r in reqs]
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def grant_approval(self, approval_id: str, decided_by: str = "user") -> dict[str, Any]:
        try:
            req = self._runtime.autonomy.grant_approval(approval_id, decided_by=decided_by)
            return req.to_dict()
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def reject_approval(self, approval_id: str, reason: str = "", decided_by: str = "user") -> dict[str, Any]:
        try:
            req = self._runtime.autonomy.reject_approval(approval_id, reason=reason, decided_by=decided_by)
            return req.to_dict()
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def list_pending_user_inputs(self, project_id: str) -> list[dict[str, Any]]:
        try:
            reqs = self._runtime.autonomy.list_pending_user_inputs(project_id)
            return [r.to_dict() for r in reqs]
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def respond_to_user_input(self, input_id: str, answer: str) -> dict[str, Any]:
        try:
            req = self._runtime.autonomy.respond_to_user_input(input_id, answer=answer)
            return req.to_dict()
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def list_pending_decisions(self, project_id: str) -> list[dict[str, Any]]:
        try:
            reqs = self._runtime.autonomy.list_pending_decisions(project_id)
            return [r.to_dict() for r in reqs]
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def respond_to_decision(self, decision_id: str, chosen_option: str, rationale: str = "") -> dict[str, Any]:
        try:
            req = self._runtime.autonomy.respond_to_decision(decision_id, chosen_option=chosen_option, rationale=rationale)
            return req.to_dict()
        except Exception as e:
            raise AppException(normalize_error(e)) from e

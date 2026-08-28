"""Workflow service — delegates to WorkforceRuntime for workflow and manager operations."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from app.dto.errors import AppException, normalize_error

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime


class WorkflowService:
    """Thin facade for workflow and manager orchestration operations."""

    def __init__(self, runtime: WorkforceRuntime):
        self._runtime = runtime

    def step_manager(self, project_id: str, feedback: Optional[str] = None) -> dict[str, Any]:
        """Execute one Manager reasoning cycle."""
        try:
            result = self._runtime.step_manager(project_id, feedback=feedback)
            return result.to_dict()
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def run_orchestration(self, project_id: str, max_cycles: int = 15) -> dict[str, Any]:
        """Run automated orchestration loop."""
        try:
            result = self._runtime.run_orchestration(project_id, max_cycles=max_cycles)
            return result.to_dict()
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def get_manager_status(self, project_id: str) -> dict[str, Any]:
        try:
            status = self._runtime.get_manager_status(project_id)
            return status.to_dict()
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def get_active_plan(self, project_id: str) -> Optional[dict[str, Any]]:
        try:
            plan = self._runtime.get_active_plan(project_id)
            return plan.to_dict() if plan else None
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def list_workflows(self, project_id: str) -> list[dict[str, Any]]:
        try:
            workflows = self._runtime.workflows.list_workflows(project_id)
            return [w.to_dict() for w in workflows]
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        try:
            wf = self._runtime.workflows.get_workflow(workflow_id)
            return wf.to_dict()
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def pause_workflow(self, workflow_id: str, reason: str = "User requested pause") -> dict[str, Any]:
        try:
            wf = self._runtime.workflows.pause_workflow(workflow_id, reason=reason)
            return wf.to_dict()
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def resume_workflow(self, workflow_id: str, reason: str = "User requested resume") -> dict[str, Any]:
        try:
            wf = self._runtime.workflows.resume_workflow(workflow_id, reason=reason)
            return wf.to_dict()
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def cancel_workflow(self, workflow_id: str, reason: str = "User requested cancellation") -> dict[str, Any]:
        try:
            wf = self._runtime.workflows.cancel_workflow(workflow_id, reason=reason)
            return wf.to_dict()
        except Exception as e:
            raise AppException(normalize_error(e)) from e

from __future__ import annotations

from typing import Any, Optional
import uuid

from core.errors import ValidationError
from core.models import Task
from core.workflow.model import WorkerHandoff
from core.workflow.types import HandoffType
from pkg.sdk.worker import WorkerOutput


class HandoffManager:
    """
    Manages structured, validated, and compressed work product handoffs between workers.
    """

    @classmethod
    def create_handoff(
        cls,
        project_id: str,
        source_worker: str,
        source_task: Task,
        output: WorkerOutput,
        destination_worker: Optional[str] = None,
        destination_task_id: Optional[str] = None,
        handoff_type: HandoffType = HandoffType.GENERAL,
        workflow_id: Optional[str] = None,
    ) -> WorkerHandoff:
        handoff_id = f"hoff-{uuid.uuid4().hex[:8]}"

        # Context compression: extract concise summaries and artifact references
        summary = output.summary or f"Work completed by {source_worker} for task '{source_task.title}'."
        artifacts = list(output.artifacts)
        
        evidence_ids = []
        if output.metadata and "evidence_ids" in output.metadata:
            evidence_ids = list(output.metadata["evidence_ids"])

        warnings = []
        if not output.success:
            warnings.append(f"Task reported non-success: {summary}")

        return WorkerHandoff(
            id=handoff_id,
            project_id=project_id,
            source_worker=source_worker,
            source_task_id=source_task.id,
            destination_worker=destination_worker,
            destination_task_id=destination_task_id,
            handoff_type=handoff_type,
            artifacts=artifacts,
            evidence=evidence_ids,
            summary=summary,
            requirements=list(source_task.success_criteria or []),
            warnings=warnings,
            workflow_id=workflow_id,
            metadata=dict(output.metadata or {}),
        )

    @classmethod
    def validate_handoff(cls, handoff: WorkerHandoff, target_project_id: str) -> None:
        """Enforces cross-project isolation and validation boundaries."""
        if handoff.project_id != target_project_id:
            raise ValidationError(
                f"Cross-project handoff violation: handoff project '{handoff.project_id}' "
                f"does not match destination project '{target_project_id}'."
            )
        if not handoff.source_task_id:
            raise ValidationError("Invalid handoff: source_task_id is missing.")
        if not handoff.source_worker:
            raise ValidationError("Invalid handoff: source_worker is missing.")

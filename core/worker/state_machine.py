from typing import Optional

from core.enums import WorkerStatus
from core.errors import InvalidWorkerTransitionError
from core.models import WorkerManifest, utc_now


class WorkerStateMachine:
    """Deterministic state machine governing Worker lifecycle transitions."""

    # Map of allowed transitions: { CurrentStatus: set(AllowedNextStatuses) }
    ALLOWED_TRANSITIONS: dict[WorkerStatus, set[WorkerStatus]] = {
        WorkerStatus.REGISTERED: {
            WorkerStatus.IDLE,
            WorkerStatus.TERMINATED,
        },
        WorkerStatus.IDLE: {
            WorkerStatus.ASSIGNED,
            WorkerStatus.TERMINATED,
            WorkerStatus.ERROR,
        },
        WorkerStatus.ASSIGNED: {
            WorkerStatus.RUNNING,
            WorkerStatus.IDLE,       # Task unassigned / cancelled before start
            WorkerStatus.FAILED,
            WorkerStatus.ERROR,
        },
        WorkerStatus.RUNNING: {
            WorkerStatus.REPORTING,
            WorkerStatus.IDLE,
            WorkerStatus.FAILED,
            WorkerStatus.ERROR,
            WorkerStatus.TERMINATED,
        },
        WorkerStatus.REPORTING: {
            WorkerStatus.IDLE,
            WorkerStatus.FAILED,
            WorkerStatus.ERROR,
        },
        WorkerStatus.FAILED: {
            WorkerStatus.IDLE,       # Reset to idle after handling failure
            WorkerStatus.RUNNING,    # Immediate retry
            WorkerStatus.TERMINATED,
        },
        WorkerStatus.ERROR: {
            WorkerStatus.IDLE,       # Recovered from error
            WorkerStatus.TERMINATED,
        },
        WorkerStatus.TERMINATED: set(),  # Terminal state
    }

    @classmethod
    def can_transition(cls, current_status: WorkerStatus, target_status: WorkerStatus) -> bool:
        """Check if a worker transition from current_status to target_status is valid."""
        allowed = cls.ALLOWED_TRANSITIONS.get(current_status, set())
        return target_status in allowed

    @classmethod
    def validate_and_transition(
        cls,
        worker: WorkerManifest,
        target_status: WorkerStatus,
        active_task_id: Optional[str] = None,
    ) -> None:
        """
        Validate and apply a lifecycle state transition to a WorkerManifest object.
        Raises InvalidWorkerTransitionError if transition is illegal.
        """
        current_status = worker.status if isinstance(worker.status, WorkerStatus) else WorkerStatus(worker.status)
        target = target_status if isinstance(target_status, WorkerStatus) else WorkerStatus(target_status)

        if current_status == target:
            # Update active task if changed
            worker.active_task_id = active_task_id
            return

        if not cls.can_transition(current_status, target):
            raise InvalidWorkerTransitionError(
                worker_id=worker.id,
                from_status=current_status.value,
                to_status=target.value,
            )

        worker.status = target
        if target in (WorkerStatus.IDLE, WorkerStatus.TERMINATED):
            worker.active_task_id = None
        elif active_task_id is not None:
            worker.active_task_id = active_task_id
        worker.updated_at = utc_now()

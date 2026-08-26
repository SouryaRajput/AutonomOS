from typing import Optional

from core.enums import TaskStatus
from core.errors import InvalidTaskTransitionError
from core.models import Task, utc_now


class TaskStateMachine:
    """Deterministic state machine governing Task lifecycle transitions."""

    # Map of allowed transitions: { CurrentStatus: set(AllowedNextStatuses) }
    ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
        TaskStatus.PENDING: {
            TaskStatus.READY,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.READY: {
            TaskStatus.ASSIGNED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.ASSIGNED: {
            TaskStatus.RUNNING,
            TaskStatus.READY,       # Unassign
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.RUNNING: {
            TaskStatus.VERIFYING,
            TaskStatus.AWAITING_APPROVAL,
            TaskStatus.COMPLETED,
            TaskStatus.BLOCKED,
            TaskStatus.RETRYING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.BLOCKED: {
            TaskStatus.READY,
            TaskStatus.PENDING,
            TaskStatus.CANCELLED,
        },
        TaskStatus.VERIFYING: {
            TaskStatus.COMPLETED,
            TaskStatus.AWAITING_APPROVAL,
            TaskStatus.RETRYING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.AWAITING_APPROVAL: {
            TaskStatus.COMPLETED,
            TaskStatus.RUNNING,
            TaskStatus.RETRYING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.RETRYING: {
            TaskStatus.READY,
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.COMPLETED: set(),  # Terminal state (Rule 5: cannot silently return to running)
        TaskStatus.FAILED: {
            TaskStatus.RETRYING,      # Can be explicitly retried
        },
        TaskStatus.CANCELLED: set(),  # Terminal state
    }

    @classmethod
    def can_transition(cls, current_status: TaskStatus, target_status: TaskStatus) -> bool:
        """Check if a transition from current_status to target_status is valid."""
        allowed = cls.ALLOWED_TRANSITIONS.get(current_status, set())
        return target_status in allowed

    @classmethod
    def validate_and_transition(
        cls,
        task: Task,
        target_status: TaskStatus,
        reason: Optional[str] = None,
    ) -> None:
        """
        Validate and apply a state transition to a Task object in-place.
        Raises InvalidTaskTransitionError if the transition violates rules.
        """
        current_status = task.status if isinstance(task.status, TaskStatus) else TaskStatus(task.status)
        target = target_status if isinstance(target_status, TaskStatus) else TaskStatus(target_status)

        if current_status == target:
            return  # No-op

        if not cls.can_transition(current_status, target):
            raise InvalidTaskTransitionError(
                task_id=task.id,
                from_status=current_status.value,
                to_status=target.value,
                reason=reason or f"Transition from {current_status.value} to {target.value} is not permitted.",
            )

        # Apply state change & timestamps
        task.status = target

        now = utc_now()
        if target == TaskStatus.RUNNING and not task.started_at:
            task.started_at = now
        elif target in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            task.completed_at = now

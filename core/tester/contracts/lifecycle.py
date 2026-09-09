from __future__ import annotations

from typing import Union

from core.tester.errors import InvalidTesterTransitionError
from core.tester.types import TesterExecutionStatus


class TesterLifecycle:
    """
    Deterministic state machine governing Tester execution lifecycle transitions.
    Reuses existing AutonomOS lifecycle conventions (WorkerStateMachine, TaskStateMachine)
    to provide strict, verified transition boundaries.
    """
    __test__ = False

    ALLOWED_TRANSITIONS: dict[TesterExecutionStatus, set[TesterExecutionStatus]] = {
        TesterExecutionStatus.REQUESTED: {
            TesterExecutionStatus.STARTING,
            TesterExecutionStatus.CANCELLED,
        },
        TesterExecutionStatus.STARTING: {
            TesterExecutionStatus.RUNNING,
            TesterExecutionStatus.BLOCKED,
            TesterExecutionStatus.FAILED,
            TesterExecutionStatus.CANCELLED,
        },
        TesterExecutionStatus.RUNNING: {
            TesterExecutionStatus.EVALUATING,
            TesterExecutionStatus.REPORTING,
            TesterExecutionStatus.BLOCKED,
            TesterExecutionStatus.FAILED,
            TesterExecutionStatus.CANCELLED,
        },
        TesterExecutionStatus.EVALUATING: {
            TesterExecutionStatus.REPORTING,
            TesterExecutionStatus.RUNNING,        # Additional test execution needed for isolated observation
            TesterExecutionStatus.BLOCKED,
            TesterExecutionStatus.FAILED,
            TesterExecutionStatus.CANCELLED,
        },
        TesterExecutionStatus.REPORTING: {
            TesterExecutionStatus.COMPLETED,
            TesterExecutionStatus.FAILED,
            TesterExecutionStatus.CANCELLED,
        },
        TesterExecutionStatus.BLOCKED: {
            TesterExecutionStatus.STARTING,       # Re-initializing upon environment fix
            TesterExecutionStatus.RUNNING,        # Resumed upon Manager decision or context provision
            TesterExecutionStatus.CANCELLED,      # Cancelled while blocked
            TesterExecutionStatus.FAILED,         # Unresolvable blocker deemed fatal
        },
        TesterExecutionStatus.COMPLETED: set(),   # Terminal state
        TesterExecutionStatus.FAILED: set(),      # Terminal state
        TesterExecutionStatus.CANCELLED: set(),   # Terminal state
    }

    TERMINAL_STATUSES: set[TesterExecutionStatus] = {
        TesterExecutionStatus.COMPLETED,
        TesterExecutionStatus.FAILED,
        TesterExecutionStatus.CANCELLED,
    }

    ACTIVE_STATUSES: set[TesterExecutionStatus] = {
        TesterExecutionStatus.STARTING,
        TesterExecutionStatus.RUNNING,
        TesterExecutionStatus.EVALUATING,
        TesterExecutionStatus.REPORTING,
        TesterExecutionStatus.BLOCKED,
    }

    @classmethod
    def to_status(cls, status: Union[TesterExecutionStatus, str]) -> TesterExecutionStatus:
        """Convert string or enum safely to TesterExecutionStatus."""
        if isinstance(status, TesterExecutionStatus):
            return status
        try:
            return TesterExecutionStatus(str(status).upper())
        except (ValueError, KeyError):
            return TesterExecutionStatus.REQUESTED

    @classmethod
    def can_transition(
        cls,
        current_status: Union[TesterExecutionStatus, str],
        target_status: Union[TesterExecutionStatus, str],
    ) -> bool:
        """Check whether a transition from current_status to target_status is permitted."""
        current = cls.to_status(current_status)
        target = cls.to_status(target_status)
        if current == target:
            return True
        allowed = cls.ALLOWED_TRANSITIONS.get(current, set())
        return target in allowed

    @classmethod
    def validate_transition(
        cls,
        entity_id: str,
        current_status: Union[TesterExecutionStatus, str],
        target_status: Union[TesterExecutionStatus, str],
        reason: str = "",
    ) -> None:
        """
        Enforce valid state transition.
        Raises InvalidTesterTransitionError if transition is disallowed.
        """
        current = cls.to_status(current_status)
        target = cls.to_status(target_status)
        if not cls.can_transition(current, target):
            raise InvalidTesterTransitionError(
                entity_id=entity_id,
                current_status=current.value,
                target_status=target.value,
                reason=reason or f"Transition from '{current.value}' to '{target.value}' is not permitted.",
            )

    @classmethod
    def is_terminal(cls, status: Union[TesterExecutionStatus, str]) -> bool:
        """Check whether status is a terminal state."""
        return cls.to_status(status) in cls.TERMINAL_STATUSES

    @classmethod
    def is_active(cls, status: Union[TesterExecutionStatus, str]) -> bool:
        """Check whether status represents an active execution state."""
        return cls.to_status(status) in cls.ACTIVE_STATUSES

    @classmethod
    def get_allowed_transitions(cls, status: Union[TesterExecutionStatus, str]) -> set[TesterExecutionStatus]:
        """Return the set of valid target statuses from the given status."""
        curr = cls.to_status(status)
        return set(cls.ALLOWED_TRANSITIONS.get(curr, set()))

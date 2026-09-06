from __future__ import annotations

from typing import Union

from core.programmer.errors import InvalidProgrammerTransitionError
from core.programmer.types import ProgrammerExecutionStatus


class ProgrammerLifecycle:
    """
    Deterministic state machine governing Programmer execution lifecycle transitions.
    Reuses existing AutonomOS lifecycle conventions (e.g. WorkerStateMachine) to provide
    strict transition boundaries without external framework overhead.
    """

    ALLOWED_TRANSITIONS: dict[ProgrammerExecutionStatus, set[ProgrammerExecutionStatus]] = {
        ProgrammerExecutionStatus.REQUESTED: {
            ProgrammerExecutionStatus.STARTING,
            ProgrammerExecutionStatus.CANCELLED,
        },
        ProgrammerExecutionStatus.STARTING: {
            ProgrammerExecutionStatus.RUNNING,
            ProgrammerExecutionStatus.BLOCKED,
            ProgrammerExecutionStatus.FAILED,
            ProgrammerExecutionStatus.CANCELLED,
        },
        ProgrammerExecutionStatus.RUNNING: {
            ProgrammerExecutionStatus.VERIFYING,
            ProgrammerExecutionStatus.COMPLETING,
            ProgrammerExecutionStatus.COMPLETED,
            ProgrammerExecutionStatus.BLOCKED,
            ProgrammerExecutionStatus.FAILED,
            ProgrammerExecutionStatus.CANCELLED,
        },
        ProgrammerExecutionStatus.VERIFYING: {
            ProgrammerExecutionStatus.COMPLETING,
            ProgrammerExecutionStatus.COMPLETED,
            ProgrammerExecutionStatus.RUNNING,      # Iterative fix loop when verification reveals defects
            ProgrammerExecutionStatus.BLOCKED,
            ProgrammerExecutionStatus.FAILED,
            ProgrammerExecutionStatus.CANCELLED,
        },
        ProgrammerExecutionStatus.COMPLETING: {
            ProgrammerExecutionStatus.COMPLETED,
            ProgrammerExecutionStatus.FAILED,
            ProgrammerExecutionStatus.CANCELLED,
            ProgrammerExecutionStatus.VERIFYING,
            ProgrammerExecutionStatus.RUNNING,
        },
        ProgrammerExecutionStatus.BLOCKED: {
            ProgrammerExecutionStatus.RUNNING,      # Resumed upon Manager decision/clarification
            ProgrammerExecutionStatus.STARTING,     # Re-initializing upon permission/credentials grant
            ProgrammerExecutionStatus.VERIFYING,    # Resumed verification upon unblocking
            ProgrammerExecutionStatus.FAILED,       # Unresolvable blocker deemed fatal
            ProgrammerExecutionStatus.CANCELLED,    # Cancelled while blocked
        },
        ProgrammerExecutionStatus.COMPLETED: set(),  # Terminal state
        ProgrammerExecutionStatus.FAILED: set(),     # Terminal state (no retry without explicit reset)
        ProgrammerExecutionStatus.CANCELLED: set(),  # Terminal state
    }

    TERMINAL_STATUSES: set[ProgrammerExecutionStatus] = {
        ProgrammerExecutionStatus.COMPLETED,
        ProgrammerExecutionStatus.FAILED,
        ProgrammerExecutionStatus.CANCELLED,
    }

    ACTIVE_STATUSES: set[ProgrammerExecutionStatus] = {
        ProgrammerExecutionStatus.STARTING,
        ProgrammerExecutionStatus.RUNNING,
        ProgrammerExecutionStatus.VERIFYING,
        ProgrammerExecutionStatus.COMPLETING,
        ProgrammerExecutionStatus.BLOCKED,
    }

    @classmethod
    def to_status(cls, status: Union[ProgrammerExecutionStatus, str]) -> ProgrammerExecutionStatus:
        """Convert string or enum safely to ProgrammerExecutionStatus."""
        if isinstance(status, ProgrammerExecutionStatus):
            return status
        try:
            return ProgrammerExecutionStatus(str(status).upper())
        except (ValueError, KeyError):
            return ProgrammerExecutionStatus.REQUESTED

    @classmethod
    def can_transition(
        cls,
        current_status: Union[ProgrammerExecutionStatus, str],
        target_status: Union[ProgrammerExecutionStatus, str],
    ) -> bool:
        """Check whether a transition from current_status to target_status is permitted."""
        current = cls.to_status(current_status)
        target = cls.to_status(target_status)
        if current == target:
            return True
        allowed = cls.ALLOWED_TRANSITIONS.get(current, set())
        return target in allowed

    @classmethod
    def is_terminal(cls, status: Union[ProgrammerExecutionStatus, str]) -> bool:
        """Check whether the given status is a terminal execution state."""
        return cls.to_status(status) in cls.TERMINAL_STATUSES

    @classmethod
    def is_active(cls, status: Union[ProgrammerExecutionStatus, str]) -> bool:
        """Check whether the given status is an active (in-flight or suspended) execution state."""
        return cls.to_status(status) in cls.ACTIVE_STATUSES

    @classmethod
    def validate_transition(
        cls,
        entity_id: str,
        current_status: Union[ProgrammerExecutionStatus, str],
        target_status: Union[ProgrammerExecutionStatus, str],
        reason: str = "",
    ) -> None:
        """
        Validate transition. Raises InvalidProgrammerTransitionError if invalid.
        """
        current = cls.to_status(current_status)
        target = cls.to_status(target_status)

        if current == target:
            return

        if not cls.can_transition(current, target):
            raise InvalidProgrammerTransitionError(
                entity_id=entity_id,
                current_status=current.value,
                target_status=target.value,
                reason=reason or f"Transition from {current.value} to {target.value} is forbidden.",
            )

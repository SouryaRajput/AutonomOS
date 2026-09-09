from __future__ import annotations

from typing import Optional, Set

from core.tester.errors import InvalidTesterTransitionError
from core.tester.types import TestRuntimeStatus


class TestRuntimeLifecycle:
    """
    Deterministic lifecycle state machine for the TestRuntime.
    
    Guarantees:
    - Predefined deterministic transitions only.
    - Protected terminal state (STOPPED).
    - Failure states (FAILED) can only transition to clean cleanup (STOPPED).
    """
    __test__ = False

    ALLOWED_TRANSITIONS: dict[TestRuntimeStatus, Set[TestRuntimeStatus]] = {
        TestRuntimeStatus.CREATED: {
            TestRuntimeStatus.STARTING,
            TestRuntimeStatus.STOPPED,
            TestRuntimeStatus.FAILED,
        },
        TestRuntimeStatus.STARTING: {
            TestRuntimeStatus.READY,
            TestRuntimeStatus.FAILED,
            TestRuntimeStatus.STOPPED,
        },
        TestRuntimeStatus.READY: {
            TestRuntimeStatus.RUNNING,
            TestRuntimeStatus.STOPPING,
            TestRuntimeStatus.FAILED,
            TestRuntimeStatus.STOPPED,
        },
        TestRuntimeStatus.RUNNING: {
            TestRuntimeStatus.STOPPING,
            TestRuntimeStatus.FAILED,
            TestRuntimeStatus.STOPPED,
        },
        TestRuntimeStatus.STOPPING: {
            TestRuntimeStatus.STOPPED,
            TestRuntimeStatus.FAILED,
        },
        TestRuntimeStatus.STOPPED: set(),  # Terminal state: cannot transition out
        TestRuntimeStatus.FAILED: {
            TestRuntimeStatus.STOPPED,  # Allowed cleanup transition
        },
    }

    TERMINAL_STATUSES: Set[TestRuntimeStatus] = {
        TestRuntimeStatus.STOPPED,
    }

    ACTIVE_STATUSES: Set[TestRuntimeStatus] = {
        TestRuntimeStatus.STARTING,
        TestRuntimeStatus.READY,
        TestRuntimeStatus.RUNNING,
        TestRuntimeStatus.STOPPING,
    }

    @classmethod
    def to_status(cls, val: str | TestRuntimeStatus) -> TestRuntimeStatus:
        """Convert a string or enum to a canonical TestRuntimeStatus."""
        if isinstance(val, TestRuntimeStatus):
            return val
        try:
            return TestRuntimeStatus(str(val).upper())
        except (ValueError, KeyError):
            raise InvalidTesterTransitionError(
                entity_id="unknown",
                current_status="UNKNOWN",
                target_status=str(val),
                reason=f"Unknown runtime status value '{val}'",
            )

    @classmethod
    def is_terminal(cls, status: str | TestRuntimeStatus) -> bool:
        """Check whether status is terminal."""
        s = cls.to_status(status)
        return s in cls.TERMINAL_STATUSES

    @classmethod
    def is_active(cls, status: str | TestRuntimeStatus) -> bool:
        """Check whether status is an active non-terminal state."""
        s = cls.to_status(status)
        return s in cls.ACTIVE_STATUSES

    @classmethod
    def get_allowed_transitions(cls, status: str | TestRuntimeStatus) -> Set[TestRuntimeStatus]:
        """Return the set of valid next states from the given status."""
        s = cls.to_status(status)
        return cls.ALLOWED_TRANSITIONS.get(s, set()).copy()

    @classmethod
    def is_transition_allowed(
        cls,
        current_status: str | TestRuntimeStatus,
        target_status: str | TestRuntimeStatus,
    ) -> bool:
        """Return True if the transition from current_status to target_status is permitted."""
        curr = cls.to_status(current_status)
        target = cls.to_status(target_status)
        return target in cls.ALLOWED_TRANSITIONS.get(curr, set())

    @classmethod
    def validate_transition(
        cls,
        entity_id: str,
        current_status: str | TestRuntimeStatus,
        target_status: str | TestRuntimeStatus,
        reason: str = "",
    ) -> None:
        """
        Validate whether a transition is allowed.
        Raises InvalidTesterTransitionError if illegal.
        """
        curr = cls.to_status(current_status)
        target = cls.to_status(target_status)

        if not cls.is_transition_allowed(curr, target):
            allowed = [s.value for s in cls.ALLOWED_TRANSITIONS.get(curr, set())]
            raise InvalidTesterTransitionError(
                entity_id=entity_id,
                current_status=curr.value,
                target_status=target.value,
                reason=f"Transition {curr.value} -> {target.value} is not allowed. Permitted transitions: {allowed}. Details: {reason}",
            )

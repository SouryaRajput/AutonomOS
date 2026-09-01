from __future__ import annotations

import logging
from typing import ClassVar

from core.research.errors import InvalidStateTransitionError
from core.research.types import CrawlerStatus

logger = logging.getLogger("AutonomOS.Research.CrawlerLifecycle")


class CrawlerStateMachine:
    """
    Deterministic state machine governing the operational lifecycle of a Crawler worker.
    CREATED -> QUEUED -> RUNNING -> COMPLETED / FAILED / CANCELLED -> TERMINATED.
    """

    ALLOWED_TRANSITIONS: ClassVar[dict[CrawlerStatus, set[CrawlerStatus]]] = {
        CrawlerStatus.CREATED: {
            CrawlerStatus.QUEUED,
            CrawlerStatus.TERMINATED,
            CrawlerStatus.FAILED,
        },
        CrawlerStatus.QUEUED: {
            CrawlerStatus.RUNNING,
            CrawlerStatus.CANCELLED,
            CrawlerStatus.TERMINATED,
            CrawlerStatus.FAILED,
        },
        CrawlerStatus.RUNNING: {
            CrawlerStatus.COMPLETED,
            CrawlerStatus.FAILED,
            CrawlerStatus.CANCELLED,
            CrawlerStatus.QUEUED,
        },
        CrawlerStatus.COMPLETED: {
            CrawlerStatus.QUEUED,
            CrawlerStatus.TERMINATED,
        },
        CrawlerStatus.FAILED: {
            CrawlerStatus.QUEUED,
            CrawlerStatus.TERMINATED,
        },
        CrawlerStatus.CANCELLED: {
            CrawlerStatus.QUEUED,
            CrawlerStatus.TERMINATED,
        },
        CrawlerStatus.TERMINATED: set(),
    }

    TERMINAL_STATES: ClassVar[set[CrawlerStatus]] = {
        CrawlerStatus.TERMINATED,
    }

    @classmethod
    def is_valid_transition(cls, current: CrawlerStatus, target: CrawlerStatus) -> bool:
        """Check whether a transition between two crawler statuses is allowed."""
        if current == target:
            return True
        allowed = cls.ALLOWED_TRANSITIONS.get(current, set())
        return target in allowed

    @classmethod
    def validate_transition(cls, current: CrawlerStatus, target: CrawlerStatus, reason: str = "") -> None:
        """
        Validate crawler state transition. Raises InvalidStateTransitionError if invalid.
        """
        if not cls.is_valid_transition(current, target):
            logger.error(
                f"Invalid crawler status transition rejected: {current.value} -> {target.value} ({reason})"
            )
            raise InvalidStateTransitionError(
                current_state=current.value,
                target_state=target.value,
                reason=reason or f"Allowed targets from '{current.value}' are: {[s.value for s in cls.ALLOWED_TRANSITIONS.get(current, set())]}",
            )
        logger.debug(f"Crawler status transition approved: {current.value} -> {target.value} ({reason})")

    @classmethod
    def is_terminal(cls, status: CrawlerStatus) -> bool:
        """Check if the crawler status is terminal."""
        return status in cls.TERMINAL_STATES

from __future__ import annotations

import logging
from typing import ClassVar

from core.research.errors import InvalidStateTransitionError
from core.research.types import ResearchLifecycleState

logger = logging.getLogger("AutonomOS.Research.Lifecycle")


class ResearchStateMachine:
    """
    Deterministic state machine governing the formal research lifecycle:
    RESEARCH_REQUESTED -> UNDERSTANDING -> PLANNING -> CRAWLER_ALLOCATION ->
    CRAWLERS_RUNNING -> EVIDENCE_COLLECTION -> EVIDENCE_EVALUATION ->
    COVERAGE_CHECK -> CONTRADICTION_CHECK -> SYNTHESIS ->
    RESEARCH_VERIFIED -> RESULT_DELIVERED -> COMPLETE
    
    Plus deterministic branches for RETRYING_CRAWLERS, INSUFFICIENT_EVIDENCE, FAILED, CANCELLED.
    """

    ALLOWED_TRANSITIONS: ClassVar[dict[ResearchLifecycleState, set[ResearchLifecycleState]]] = {
        ResearchLifecycleState.RESEARCH_REQUESTED: {
            ResearchLifecycleState.UNDERSTANDING,
            ResearchLifecycleState.FAILED,
            ResearchLifecycleState.CANCELLED,
        },
        ResearchLifecycleState.UNDERSTANDING: {
            ResearchLifecycleState.PLANNING,
            ResearchLifecycleState.FAILED,
            ResearchLifecycleState.CANCELLED,
        },
        ResearchLifecycleState.PLANNING: {
            ResearchLifecycleState.CRAWLER_ALLOCATION,
            ResearchLifecycleState.FAILED,
            ResearchLifecycleState.CANCELLED,
        },
        ResearchLifecycleState.CRAWLER_ALLOCATION: {
            ResearchLifecycleState.CRAWLERS_RUNNING,
            ResearchLifecycleState.FAILED,
            ResearchLifecycleState.CANCELLED,
        },
        ResearchLifecycleState.CRAWLERS_RUNNING: {
            ResearchLifecycleState.EVIDENCE_COLLECTION,
            ResearchLifecycleState.RETRYING_CRAWLERS,
            ResearchLifecycleState.FAILED,
            ResearchLifecycleState.CANCELLED,
        },
        ResearchLifecycleState.EVIDENCE_COLLECTION: {
            ResearchLifecycleState.EVIDENCE_EVALUATION,
            ResearchLifecycleState.FAILED,
            ResearchLifecycleState.CANCELLED,
        },
        ResearchLifecycleState.EVIDENCE_EVALUATION: {
            ResearchLifecycleState.COVERAGE_CHECK,
            ResearchLifecycleState.FAILED,
            ResearchLifecycleState.CANCELLED,
        },
        ResearchLifecycleState.COVERAGE_CHECK: {
            ResearchLifecycleState.CONTRADICTION_CHECK,
            ResearchLifecycleState.RETRYING_CRAWLERS,
            ResearchLifecycleState.INSUFFICIENT_EVIDENCE,
            ResearchLifecycleState.SYNTHESIS,
            ResearchLifecycleState.FAILED,
            ResearchLifecycleState.CANCELLED,
        },
        ResearchLifecycleState.CONTRADICTION_CHECK: {
            ResearchLifecycleState.SYNTHESIS,
            ResearchLifecycleState.INSUFFICIENT_EVIDENCE,
            ResearchLifecycleState.FAILED,
            ResearchLifecycleState.CANCELLED,
        },
        ResearchLifecycleState.INSUFFICIENT_EVIDENCE: {
            ResearchLifecycleState.SYNTHESIS,
            ResearchLifecycleState.RETRYING_CRAWLERS,
            ResearchLifecycleState.FAILED,
            ResearchLifecycleState.CANCELLED,
        },
        ResearchLifecycleState.RETRYING_CRAWLERS: {
            ResearchLifecycleState.CRAWLER_ALLOCATION,
            ResearchLifecycleState.CRAWLERS_RUNNING,
            ResearchLifecycleState.INSUFFICIENT_EVIDENCE,
            ResearchLifecycleState.FAILED,
            ResearchLifecycleState.CANCELLED,
        },
        ResearchLifecycleState.SYNTHESIS: {
            ResearchLifecycleState.RESEARCH_VERIFIED,
            ResearchLifecycleState.FAILED,
            ResearchLifecycleState.CANCELLED,
        },
        ResearchLifecycleState.RESEARCH_VERIFIED: {
            ResearchLifecycleState.RESULT_DELIVERED,
            ResearchLifecycleState.FAILED,
            ResearchLifecycleState.CANCELLED,
        },
        ResearchLifecycleState.RESULT_DELIVERED: {
            ResearchLifecycleState.COMPLETE,
            ResearchLifecycleState.FAILED,
        },
        # Terminal states
        ResearchLifecycleState.COMPLETE: set(),
        ResearchLifecycleState.FAILED: set(),
        ResearchLifecycleState.CANCELLED: set(),
        ResearchLifecycleState.STAGNATED: {
            ResearchLifecycleState.FAILED,
            ResearchLifecycleState.CANCELLED,
        },
    }

    TERMINAL_STATES: ClassVar[set[ResearchLifecycleState]] = {
        ResearchLifecycleState.COMPLETE,
        ResearchLifecycleState.FAILED,
        ResearchLifecycleState.CANCELLED,
    }

    @classmethod
    def is_valid_transition(
        cls,
        current: ResearchLifecycleState,
        target: ResearchLifecycleState,
    ) -> bool:
        """Check whether a transition between two research states is allowed."""
        if current == target:
            return True
        allowed = cls.ALLOWED_TRANSITIONS.get(current, set())
        return target in allowed

    @classmethod
    def validate_transition(
        cls,
        current: ResearchLifecycleState,
        target: ResearchLifecycleState,
        reason: str = "",
    ) -> None:
        """
        Validate state transition. Raises InvalidStateTransitionError if invalid.
        """
        if not cls.is_valid_transition(current, target):
            logger.error(
                f"Invalid research lifecycle transition rejected: {current.value} -> {target.value} ({reason})"
            )
            raise InvalidStateTransitionError(
                current_state=current.value,
                target_state=target.value,
                reason=reason or f"Allowed targets from '{current.value}' are: {[s.value for s in cls.ALLOWED_TRANSITIONS.get(current, set())]}",
            )
        logger.info(f"Research state transition approved: {current.value} -> {target.value} ({reason})")

    @classmethod
    def is_terminal(cls, state: ResearchLifecycleState) -> bool:
        """Return True if the state is terminal (no further transitions possible)."""
        return state in cls.TERMINAL_STATES

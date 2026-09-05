from __future__ import annotations

from enum import Enum


class DecisionType(str, Enum):
    """
    Bounded taxonomy of high-impact decision categories requiring human-in-the-loop clarification.
    Restricted to decisions that materially affect scope, criteria, architecture, or direction.
    """
    SCOPE = "SCOPE"
    COMPARISON_CRITERIA = "COMPARISON_CRITERIA"
    TECHNICAL_DIRECTION = "TECHNICAL_DIRECTION"
    ARCHITECTURAL_CONSTRAINT = "ARCHITECTURAL_CONSTRAINT"
    PERFORMANCE_PRIORITY = "PERFORMANCE_PRIORITY"
    COST_PRIORITY = "COST_PRIORITY"
    SECURITY_PRIORITY = "SECURITY_PRIORITY"
    FRESHNESS = "FRESHNESS"
    VERSION = "VERSION"
    ENVIRONMENT = "ENVIRONMENT"
    OUTPUT_EXPECTATION = "OUTPUT_EXPECTATION"
    AMBIGUITY_RESOLUTION = "AMBIGUITY_RESOLUTION"
    OTHER_HIGH_IMPACT = "OTHER_HIGH_IMPACT"


class QuestionImportance(str, Enum):
    """
    Structured importance tiering for research decision questions.
    Enforces deterministic policy: only HIGH or CRITICAL questions become user-facing.
    """
    LOW = "LOW"             # Internal resolution only; never presented to user
    MEDIUM = "MEDIUM"       # Usually resolved internally unless explicitly configured
    HIGH = "HIGH"           # Eligible for user-facing decision prompt
    CRITICAL = "CRITICAL"   # Must be resolved by user before research planning proceeds


class QuestionState(str, Enum):
    """
    Operational lifecycle state of a Human-In-The-Loop research decision question.
    QUESTION_PENDING -> ANSWERED (or SKIPPED, EXPIRED, CANCELLED) -> RESOLVED.
    """
    NO_QUESTION = "NO_QUESTION"
    QUESTION_PENDING = "QUESTION_PENDING"
    USER_ANSWERED = "USER_ANSWERED"
    USER_SKIPPED = "USER_SKIPPED"
    ANSWERED = "ANSWERED"
    SKIPPED = "SKIPPED"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class QuestionLifecycleState(str, Enum):
    """
    Deterministic session state governing Researcher pause/resume:
    NO_QUESTION -> QUESTION_PENDING (execution paused) -> ANSWERED / SKIPPED / EXPIRED / CANCELLED (resumed).
    """
    NO_QUESTION = "NO_QUESTION"
    QUESTION_PENDING = "QUESTION_PENDING"
    ANSWERED = "ANSWERED"
    SKIPPED = "SKIPPED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class AnswerType(str, Enum):
    """Classification of the mode of answer provided."""
    OPTION_SELECTED = "OPTION_SELECTED"
    CUSTOM_ANSWER = "CUSTOM_ANSWER"
    SKIPPED = "SKIPPED"


class AnswerSource(str, Enum):
    """Provenance origin of the research answer."""
    USER = "USER"
    DEFAULT_RECOMMENDATION = "DEFAULT_RECOMMENDATION"
    SYSTEM_TIMEOUT = "SYSTEM_TIMEOUT"
    POLICY = "POLICY"


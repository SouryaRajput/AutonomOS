from __future__ import annotations

from enum import Enum


class AutonomyLevel(str, Enum):
    """
    Autonomy tier configured for the Manager orchestrator.
    - SUPERVISED: Requires user confirmation for major task creations, reassignments, and rollbacks.
    - BALANCED: Autonomous execution of standard tasks; pauses/requests confirmation for high-risk actions.
    - AUTONOMOUS: Full execution within configured hard safety limits and retry budgets.
    """
    SUPERVISED = "SUPERVISED"
    BALANCED = "BALANCED"
    AUTONOMOUS = "AUTONOMOUS"


class ManagerActionType(str, Enum):
    """Authoritative action vocabulary that the Manager LLM can propose to the Controller."""
    CREATE_TASK = "CREATE_TASK"
    UPDATE_TASK = "UPDATE_TASK"
    ASSIGN_TASK = "ASSIGN_TASK"
    REPRIORITIZE_TASK = "REPRIORITIZE_TASK"
    BLOCK_TASK = "BLOCK_TASK"
    UNBLOCK_TASK = "UNBLOCK_TASK"
    REQUEST_VERIFICATION = "REQUEST_VERIFICATION"
    REQUEST_RETRY = "REQUEST_RETRY"
    REQUEST_ROLLBACK = "REQUEST_ROLLBACK"
    UPDATE_MEMORY = "UPDATE_MEMORY"
    REQUEST_USER_INPUT = "REQUEST_USER_INPUT"
    WAIT = "WAIT"
    ESCALATE = "ESCALATE"
    COMPLETE_PROJECT = "COMPLETE_PROJECT"


class ConfidenceLevel(str, Enum):
    """Structured uncertainty indicator for Manager decisions (replacing fake numeric scores)."""
    CERTAIN = "CERTAIN"
    LOW_UNCERTAINTY = "LOW_UNCERTAINTY"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    HIGH_RISK_UNCERTAINTY = "HIGH_RISK_UNCERTAINTY"


class PlanStatus(str, Enum):
    """Lifecycle status of a project plan."""
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    REVISED = "REVISED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


class ManagerExecutionMode(str, Enum):
    """Execution step mode for the Manager loop."""
    SINGLE_STEP = "SINGLE_STEP"
    CONTINUOUS = "CONTINUOUS"


class UserIntentType(str, Enum):
    """Classification of user inputs for intent-aware routing."""
    QUESTION = "QUESTION"
    EXECUTION_REQUEST = "EXECUTION_REQUEST"
    STATUS_QUERY = "STATUS_QUERY"
    CLARIFICATION_RESPONSE = "CLARIFICATION_RESPONSE"
    CONVERSATIONAL = "CONVERSATIONAL"


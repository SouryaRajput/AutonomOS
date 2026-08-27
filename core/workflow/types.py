from __future__ import annotations

from enum import Enum


class WorkflowStatus(str, Enum):
    """Lifecycle status of a multi-worker workflow."""
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    PAUSED = "PAUSED"
    NEEDS_USER_INPUT = "NEEDS_USER_INPUT"
    EMERGENCY_STOPPED = "EMERGENCY_STOPPED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class HandoffType(str, Enum):
    """Semantic type of cross-worker work product transfer."""
    RESEARCH_TO_PROGRAMMING = "RESEARCH_TO_PROGRAMMING"
    PROGRAMMING_TO_TESTING = "PROGRAMMING_TO_TESTING"
    TESTING_TO_MANAGER = "TESTING_TO_MANAGER"
    DEFECT_TO_PROGRAMMING = "DEFECT_TO_PROGRAMMING"
    GENERAL = "GENERAL"


class ReassignmentReason(str, Enum):
    """Reason why a task or step was reassigned to another worker."""
    WORKER_FAILED = "WORKER_FAILED"
    WORKER_BUSY = "WORKER_BUSY"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    USER_REQUESTED = "USER_REQUESTED"
    STAGNATION = "STAGNATION"


class ApprovalStatus(str, Enum):
    """Status of a human approval request within a workflow."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class WorkflowPriority(str, Enum):
    """Priority level of a workforce workflow."""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"

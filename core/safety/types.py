from enum import Enum


class SafetyAction(str, Enum):
    """Action outcome from a safety policy evaluation."""
    ALLOW = "ALLOW"
    ALLOW_WITH_CHECKPOINT = "ALLOW_WITH_CHECKPOINT"
    DENY = "DENY"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"
    ESCALATE = "ESCALATE"


class CheckpointType(str, Enum):
    """Underlying mechanism used to record project state."""
    GIT_COMMIT = "GIT_COMMIT"
    GIT_WORKING_TREE = "GIT_WORKING_TREE"
    FILESYSTEM_SNAPSHOT = "FILESYSTEM_SNAPSHOT"
    MEMORY_SNAPSHOT = "MEMORY_SNAPSHOT"


class CheckpointStatus(str, Enum):
    """Lifecycle status of a project checkpoint."""
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    EXPIRED = "EXPIRED"


class RollbackStatus(str, Enum):
    """Outcome status of a rollback operation."""
    SUCCESS = "SUCCESS"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILED = "FAILED"
    BLOCKED_DIRTY_USER_STATE = "BLOCKED_DIRTY_USER_STATE"


class ScopeDeviationType(str, Enum):
    """Types of detected scope deviations."""
    UNEXPECTED_FILE_MODIFIED = "UNEXPECTED_FILE_MODIFIED"
    UNEXPECTED_FILE_DELETED = "UNEXPECTED_FILE_DELETED"
    PROTECTED_PATH_MODIFIED = "PROTECTED_PATH_MODIFIED"
    MAX_FILES_EXCEEDED = "MAX_FILES_EXCEEDED"
    MAX_DIFF_EXCEEDED = "MAX_DIFF_EXCEEDED"

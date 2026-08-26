from enum import Enum


class ProjectStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class WorkerStatus(str, Enum):
    REGISTERED = "REGISTERED"
    IDLE = "IDLE"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    REPORTING = "REPORTING"
    FAILED = "FAILED"
    ERROR = "ERROR"
    TERMINATED = "TERMINATED"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    VERIFYING = "VERIFYING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RETRYING = "RETRYING"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ArtifactType(str, Enum):
    FILE = "FILE"
    REPORT = "REPORT"
    PATCH = "PATCH"
    OUTPUT = "OUTPUT"
    RESOURCE = "RESOURCE"


class DependencyType(str, Enum):
    STRICT_SUCCESS = "STRICT_SUCCESS"
    COMPLETION_ANY = "COMPLETION_ANY"


class MemoryType(str, Enum):
    """Categories of persistent project memory documents."""
    PROJECT_MAP = "PROJECT_MAP"        # project-map.md (High-level component & navigation map)
    ARCHITECTURE = "ARCHITECTURE"      # architecture.md (Stable invariants, boundaries & constraints)
    CURRENT_STATE = "CURRENT_STATE"    # current-state.md (Active milestones, stages & known limitations)
    DECISION = "DECISION"              # decisions/*.md (Architectural Decision Records)
    TASK_MEMORY = "TASK_MEMORY"        # tasks/*.md (Readable task summaries & findings)
    REPORT = "REPORT"                  # reports/*/*.md (Worker & verification execution reports)
    ISSUE = "ISSUE"                    # issues/*.md (Known problems & limitations)
    NOTE = "NOTE"                      # notes/*.md (General persistent project notes)


class IssueSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IssueStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"

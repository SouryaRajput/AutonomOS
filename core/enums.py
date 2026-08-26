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

from __future__ import annotations

from enum import Enum


class ProgrammerWorkOrderStatus(str, Enum):
    """Lifecycle status of a ProgrammerWorkOrder authorized by Manager."""
    CREATED = "CREATED"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProgrammerExecutionStatus(str, Enum):
    """Operational lifecycle status of an active Programmer execution attempt."""
    REQUESTED = "REQUESTED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETING = "COMPLETING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    # Backward compatibility aliases
    INITIALIZED = "STARTING"
    PENDING = "REQUESTED"

    @classmethod
    def _missing_(cls, value: object):
        if str(value).upper() in ("INITIALIZED", "INIT"):
            return cls.STARTING
        if str(value).upper() in ("PENDING",):
            return cls.REQUESTED
        return None


class ProgrammerBlockerCategory(str, Enum):
    """Categorization of material impediments requiring Manager intervention."""
    SCOPE = "SCOPE"
    PERMISSION = "PERMISSION"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    ARCHITECTURAL = "ARCHITECTURAL"
    DEPENDENCY = "DEPENDENCY"
    VERIFICATION = "VERIFICATION"
    RESOURCE = "RESOURCE"
    OTHER = "OTHER"


class ProgrammerBlockerSeverity(str, Enum):
    """Severity classification of a ProgrammerBlocker."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ProgrammerResultStatus(str, Enum):
    """Operational outcome status returned by the Programmer to the Manager."""
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    # Backward-compatible aliases
    SUCCESS = "SUCCESS"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"


class AcceptanceStatus(str, Enum):
    """Verification status of an individual acceptance criterion."""
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_VERIFIED = "NOT_VERIFIED"


class ProgrammerEvidenceType(str, Enum):
    """Categorization of concrete evidence records captured during implementation."""
    TEST_RESULT = "TEST_RESULT"
    TYPECHECK_RESULT = "TYPECHECK_RESULT"
    LINT_RESULT = "LINT_RESULT"
    COMMAND_EXECUTION = "COMMAND_EXECUTION"
    DIFF = "DIFF"
    FILE_CHANGE = "FILE_CHANGE"
    ACCEPTANCE_CHECK = "ACCEPTANCE_CHECK"
    ARTIFACT = "ARTIFACT"
    CUSTOM = "CUSTOM"



class ProgrammerActionType(str, Enum):
    """Categorization of discrete actions captured during Programmer execution."""
    INITIALIZE = "INITIALIZE"
    INSPECT = "INSPECT"
    EXECUTE = "EXECUTE"
    VALIDATE = "VALIDATE"
    REPORT = "REPORT"
    ESCALATE = "ESCALATE"


class AcceptanceCriterionType(str, Enum):
    """Categorization of machine-evaluable acceptance criteria for verification."""
    TEST_PASS = "TEST_PASS"
    BUILD_PASS = "BUILD_PASS"
    TYPECHECK_PASS = "TYPECHECK_PASS"
    FEATURE_EXISTS = "FEATURE_EXISTS"
    API_BEHAVIOR_PRESERVED = "API_BEHAVIOR_PRESERVED"
    FILE_CHANGED = "FILE_CHANGED"
    NO_DEPENDENCY_ADDED = "NO_DEPENDENCY_ADDED"
    BEHAVIOR_DEMONSTRATED = "BEHAVIOR_DEMONSTRATED"
    CUSTOM = "CUSTOM"


class WorkspaceIsolationMode(str, Enum):
    """Execution isolation mode for a Programmer workspace."""
    SHARED = "SHARED"
    ISOLATED = "ISOLATED"


class WorkspaceProvisioningStatus(str, Enum):
    """Explicit lifecycle status of a workspace provisioning attempt."""
    PROVISIONING = "PROVISIONING"
    READY = "READY"
    FAILED = "FAILED"


class WorkspaceProvisioningErrorCode(str, Enum):
    """Structured error codes for workspace provisioning failures."""
    INVALID_WORKSPACE = "INVALID_WORKSPACE"
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    WORKSPACE_UNAVAILABLE = "WORKSPACE_UNAVAILABLE"
    PATH_INVALID = "PATH_INVALID"
    ISOLATION_UNAVAILABLE = "ISOLATION_UNAVAILABLE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RESOURCE_FAILURE = "RESOURCE_FAILURE"
    UNKNOWN = "UNKNOWN"


class FilesystemOperation(str, Enum):
    """Filesystem operations subject to Programmer boundary authorization."""
    READ = "READ"
    WRITE = "WRITE"
    CREATE = "CREATE"
    DELETE = "DELETE"
    RENAME = "RENAME"


class PathBoundaryScope(str, Enum):
    """Classification of a path relative to the authorized Programmer workspace boundary."""
    WRITABLE = "WRITABLE"
    READ_ONLY = "READ_ONLY"
    FORBIDDEN = "FORBIDDEN"
    OUTSIDE_BOUNDARY = "OUTSIDE_BOUNDARY"


class CommandDecisionType(str, Enum):
    """Explicit decision verdict for a command authorization evaluation."""
    ALLOW = "ALLOW"
    DENY = "DENY"
    INVALID = "INVALID"


class ExecutionContextStatus(str, Enum):
    """Lifecycle status of a Programmer execution context."""
    CREATING = "CREATING"
    VALIDATING = "VALIDATING"
    READY = "READY"
    FAILED = "FAILED"


class CodingAgentBackendType(str, Enum):
    """Supported or pluggable coding agent backend implementations."""
    CLINE = "CLINE"
    CODEX = "CODEX"
    CLAUDE_CODE = "CLAUDE_CODE"
    CUSTOM = "CUSTOM"
    MOCK = "MOCK"


class CodingAgentExecutionStatus(str, Enum):
    """Operational status of a coding agent backend execution session."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class CodingAgentEventType(str, Enum):
    """Authoritative taxonomy of events emitted during coding agent execution."""
    AGENT_STARTED = "AGENT_STARTED"
    THINKING = "THINKING"
    TOOL_CALL_REQUESTED = "TOOL_CALL_REQUESTED"
    TOOL_CALL_COMPLETED = "TOOL_CALL_COMPLETED"
    MESSAGE_EMITTED = "MESSAGE_EMITTED"
    PROGRESS_REPORTED = "PROGRESS_REPORTED"
    CHECKPOINT_SAVED = "CHECKPOINT_SAVED"
    EXECUTION_COMPLETED = "EXECUTION_COMPLETED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXECUTION_CANCELLED = "EXECUTION_CANCELLED"


class ProgrammerExecutionEventType(str, Enum):
    """Normalized taxonomy of execution events emitted during a Programmer execution."""
    EXECUTION_STARTED = "EXECUTION_STARTED"
    AGENT_MESSAGE = "AGENT_MESSAGE"
    FILE_OPERATION = "FILE_OPERATION"
    COMMAND_OPERATION = "COMMAND_OPERATION"
    PROGRESS = "PROGRESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    EXECUTION_COMPLETED = "EXECUTION_COMPLETED"
    EXECUTION_CANCELLED = "EXECUTION_CANCELLED"


class VerificationStatus(str, Enum):
    """
    Authoritative 5-state verification outcome for AutonomOS verification checks and summaries.
    These states are strictly distinct and non-collapsible.
    """
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"
    NOT_VERIFIED = "NOT_VERIFIED"
    ERROR = "ERROR"


class VerificationCheckType(str, Enum):
    """Categorization of concrete verification checks executed or evaluated by AutonomOS."""
    TEST = "TEST"
    COMMAND = "COMMAND"
    LINT = "LINT"
    TYPECHECK = "TYPECHECK"
    BUILD = "BUILD"
    STATIC_ANALYSIS = "STATIC_ANALYSIS"
    CUSTOM = "CUSTOM"


class VerificationEvidenceSourceType(str, Enum):
    """Source classification of captured verification evidence, separating actual execution observation from agent claims."""
    COMMAND_OUTPUT = "COMMAND_OUTPUT"
    TEST_RUNNER = "TEST_RUNNER"
    FILESYSTEM = "FILESYSTEM"
    PROCESS_EXIT = "PROCESS_EXIT"
    STATIC_ANALYSIS = "STATIC_ANALYSIS"
    EXTERNAL_EVALUATION = "EXTERNAL_EVALUATION"
    AGENT_CLAIM = "AGENT_CLAIM"


class VerificationSummaryStatus(str, Enum):
    """
    Authoritative 4-state overall verification outcome produced by VerificationEvidenceAggregator.
    Deterministic, auditable, and non-collapsible.
    """
    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    FAILED = "FAILED"
    UNVERIFIED = "UNVERIFIED"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        if val_str in ("PASS", "SUCCESS", "VERIFIED"):
            return cls.VERIFIED
        if val_str in ("FAIL", "FAILURE", "ERROR", "FAILED"):
            return cls.FAILED
        if val_str in ("PARTIAL", "PARTIALLY_PASSED", "PARTIALLY_VERIFIED"):
            return cls.PARTIALLY_VERIFIED
        if val_str in ("NOT_VERIFIED", "UNVERIFIED", "NOT_RUN", "UNKNOWN"):
            return cls.UNVERIFIED
        return None










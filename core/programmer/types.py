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
    GIT = "GIT"
    CODEBASE_EXPLORATION = "CODEBASE_EXPLORATION"
    IMPACT_ANALYSIS = "IMPACT_ANALYSIS"
    RISK_ASSESSMENT = "RISK_ASSESSMENT"
    PLAN_VALIDATION = "PLAN_VALIDATION"
    EXECUTION_SUPERVISION = "EXECUTION_SUPERVISION"


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


class ProgrammerFailureCategory(str, Enum):
    """
    Deterministic failure category taxonomy for the Programmer subsystem.
    Differentiates between agent lifecycle defects, command outcomes,
    boundary violations, and verification verdicts.
    """
    AGENT_STARTUP = "AGENT_STARTUP"
    AGENT_CRASH = "AGENT_CRASH"
    AGENT_HUNG = "AGENT_HUNG"
    COMMAND_FAILURE = "COMMAND_FAILURE"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    TIMEOUT = "TIMEOUT"
    CANCELLATION = "CANCELLATION"
    PERMISSION = "PERMISSION"
    SCOPE = "SCOPE"
    RESOURCE = "RESOURCE"
    BUDGET = "BUDGET"
    DEPENDENCY = "DEPENDENCY"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    INTERNAL = "INTERNAL"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.UNKNOWN


class RecoveryDisposition(str, Enum):
    """
    Deterministic recovery disposition assigned to a Programmer execution failure.
    Directs the high-level recovery action without executing it autonomously.
    """
    RETRY = "RETRY"          # Transient failure: retry attempt with backoff/budget check
    CORRECT = "CORRECT"      # Verification failed but budget remains: bounded self-correction turn
    ESCALATE = "ESCALATE"    # Requires higher-level Manager intervention or policy review
    BLOCK = "BLOCK"          # Requires Manager decision, permission grant, or missing context
    FAIL = "FAIL"            # Permanent/unrecoverable defect: terminate execution as failed
    CANCEL = "CANCEL"        # Cancellation requested: clean termination without further actions

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.FAIL


class ProgrammerFailureSeverity(str, Enum):
    """Severity classification of a ProgrammerFailure."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.HIGH


class FailureSourceType(str, Enum):
    """Origin classification of the failure event or observation."""
    AGENT = "AGENT"
    COMMAND = "COMMAND"
    VERIFICATION = "VERIFICATION"
    WORKSPACE = "WORKSPACE"
    POLICY = "POLICY"
    SYSTEM = "SYSTEM"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.UNKNOWN


class ExecutionMonitoringState(str, Enum):
    """
    Deterministic monitoring states for an active Programmer execution.
    Transitions through: ACTIVE -> IDLE -> HUNG_SUSPECTED or terminal states.
    """
    ACTIVE = "ACTIVE"                  # Normal execution with recent activity or heartbeat
    IDLE = "IDLE"                      # Inactivity exceeded threshold, but not yet suspected hung
    HUNG_SUSPECTED = "HUNG_SUSPECTED"  # Silence exceeded hung threshold; candidate failure emitted
    TERMINATED = "TERMINATED"          # External process or container terminated
    TIMED_OUT = "TIMED_OUT"            # Execution exceeded total time allowance
    CANCELLED = "CANCELLED"            # Execution cancelled upon request

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.ACTIVE


class WatchdogStatus(str, Enum):
    """Lifecycle status of the ExecutionWatchdog monitoring session."""
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.RUNNING


class GitRepositoryState(str, Enum):
    """Deterministic working-tree state of a Git repository."""
    CLEAN = "CLEAN"
    DIRTY = "DIRTY"
    DETACHED = "DETACHED"
    READY = "READY"
    UNINITIALIZED = "UNINITIALIZED"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.UNKNOWN


class GitIsolationMode(str, Enum):
    """Change isolation mode for a Git execution environment."""
    BRANCH = "BRANCH"
    WORKTREE = "WORKTREE"
    SHARED_CLONE = "SHARED_CLONE"
    ISOLATED_CLONE = "ISOLATED_CLONE"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.BRANCH


class GitWorktreeStatus(str, Enum):
    """Deterministic lifecycle states for an execution-specific Git worktree."""
    PROVISIONING = "PROVISIONING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    CLEANUP_PENDING = "CLEANUP_PENDING"
    CLEANED = "CLEANED"
    FAILED = "FAILED"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.FAILED


class GitWorktreeErrorCode(str, Enum):
    """Structured error codes for Git worktree provisioning, isolation, or cleanup failures."""
    ISOLATION_UNAVAILABLE = "ISOLATION_UNAVAILABLE"
    REPOSITORY_INVALID = "REPOSITORY_INVALID"
    BASE_REVISION_UNRESOLVED = "BASE_REVISION_UNRESOLVED"
    WORKTREE_EXISTS = "WORKTREE_EXISTS"
    BRANCH_COLLISION = "BRANCH_COLLISION"
    PROVISIONING_FAILED = "PROVISIONING_FAILED"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.UNKNOWN


class GitOperationCategory(str, Enum):
    """Categorization of Git operations under Programmer governance."""
    READ = "READ"
    LOCAL_CHANGE_MANAGEMENT = "LOCAL_CHANGE_MANAGEMENT"
    DESTRUCTIVE = "DESTRUCTIVE"
    REMOTE = "REMOTE"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.READ


class GitOperationType(str, Enum):
    """Deterministic taxonomy of concrete Git operations."""
    # READ
    STATUS = "status"
    DIFF = "diff"
    LOG = "log"
    SHOW = "show"
    BRANCH_INFO = "branch_info"

    # LOCAL CHANGE MANAGEMENT
    ADD = "add"
    COMMIT = "commit"
    BRANCH_CREATE = "branch_create"

    # DESTRUCTIVE
    RESET = "reset"
    CLEAN = "clean"
    CHECKOUT_DISCARD = "checkout_discard"
    BRANCH_DELETE = "branch_delete"

    # REMOTE
    FETCH = "fetch"
    PUSH = "push"
    PULL = "pull"

    # INVALID / MALFORMED
    INVALID = "invalid"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).lower()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.INVALID


class ChangeSetStatus(str, Enum):
    """Deterministic lifecycle and outcome states for a Git change set."""
    UNCOMMITTED = "UNCOMMITTED"
    COMMITTED = "COMMITTED"
    EMPTY = "EMPTY"
    INVALID = "INVALID"
    FAILED = "FAILED"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.INVALID


class RepositoryAnomalyType(str, Enum):
    """
    Deterministic classification of repository anomalies and suspicious state detected
    during repository state verification.
    """
    UNAUTHORIZED_CHANGES = "UNAUTHORIZED_CHANGES"
    UNEXPECTED_UNCOMMITTED_CHANGES = "UNEXPECTED_UNCOMMITTED_CHANGES"
    CHANGES_DISAPPEARED = "CHANGES_DISAPPEARED"
    BASE_REVISION_MISMATCH = "BASE_REVISION_MISMATCH"
    WORKSPACE_MISMATCH = "WORKSPACE_MISMATCH"
    CROSS_EXECUTION_CONTAMINATION = "CROSS_EXECUTION_CONTAMINATION"
    SUSPICIOUS_STATE = "SUSPICIOUS_STATE"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.SUSPICIOUS_STATE


class ManagerDisposition(str, Enum):
    """
    Possible Manager evaluation outcomes and decisions for a delivered Programmer package.
    Programmer may recommend a disposition, but Manager has authoritative decision power.
    """
    ACCEPT = "ACCEPT"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"
    CANCEL = "CANCEL"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.REJECT


class UnderstandingConfidence(str, Enum):
    """
    Epistemic classification for repository understanding insights.
    Enforces strict distinction between observed repository facts, reasonable inferences,
    and unknown/unverified elements. Never represent inference as fact.
    """
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.UNKNOWN


class ImpactLevel(str, Enum):
    """
    Epistemic classification for implementation impact analysis.
    Directly distinguishes direct targets, indirect dependents, potential side effects,
    and unknown/unresolvable impacts. Never claim certainty without evidence.
    """
    DIRECT = "DIRECT"
    INDIRECT = "INDIRECT"
    POTENTIAL = "POTENTIAL"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.UNKNOWN


class EngineeringRiskCategory(str, Enum):
    """
    Taxonomy of material engineering risks detected in an implementation plan
    prior to code execution.
    """
    SECURITY = "SECURITY"
    DATA_LOSS = "DATA_LOSS"
    DATABASE_MIGRATION = "DATABASE_MIGRATION"
    PUBLIC_API_CHANGE = "PUBLIC_API_CHANGE"
    BREAKING_CHANGE = "BREAKING_CHANGE"
    DEPENDENCY_CHANGE = "DEPENDENCY_CHANGE"
    CONFIGURATION_CHANGE = "CONFIGURATION_CHANGE"
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    LARGE_SCOPE = "LARGE_SCOPE"
    ARCHITECTURAL_CHANGE = "ARCHITECTURAL_CHANGE"
    DESTRUCTIVE_OPERATION = "DESTRUCTIVE_OPERATION"
    INSUFFICIENT_TEST_COVERAGE = "INSUFFICIENT_TEST_COVERAGE"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.UNKNOWN


class PlanValidationStatus(str, Enum):
    """
    Validation decision status for a Programmer implementation plan.
    - APPROVED: Plan is valid, compliant, and ready to execute within existing authority.
    - APPROVED_WITH_WARNINGS: Plan is executable within authority, but carries non-blocking advisories.
    - REQUIRES_ESCALATION: Plan requires Manager intervention (scope, permission, architecture, risk).
    - INVALID: Plan violates foundational invariants, contains cycles, or has broken syntax/commands.
    """
    APPROVED = "APPROVED"
    APPROVED_WITH_WARNINGS = "APPROVED_WITH_WARNINGS"
    REQUIRES_ESCALATION = "REQUIRES_ESCALATION"
    INVALID = "INVALID"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.INVALID


class DeviationClassification(str, Enum):
    """
    Four-level taxonomy of execution deviations from planned expectations:
    - EXPECTED: Anticipated variations (e.g. read operations, ancillary inspections); proceeds autonomously.
    - MINOR: Low-impact variances that may continue autonomously (e.g. non-harmful allowed command variant).
    - MATERIAL: Significant plan drift surfaced to Programmer policy and correction/recovery context.
    - BLOCKING: Out-of-bounds action requiring Manager authority outside WorkOrder (triggers escalation/blocking).
    """
    EXPECTED = "EXPECTED"
    MINOR = "MINOR"
    MATERIAL = "MATERIAL"
    BLOCKING = "BLOCKING"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.MINOR


class PlanDeviationCategory(str, Enum):
    """Specific categories of deviations detected during implementation plan supervision."""
    UNEXPECTED_FILE = "UNEXPECTED_FILE"
    UNEXPECTED_COMMAND = "UNEXPECTED_COMMAND"
    STEP_DEPENDENCY_VIOLATION = "STEP_DEPENDENCY_VIOLATION"
    REPEATED_FAILURE = "REPEATED_FAILURE"
    SCOPE_EXPANSION = "SCOPE_EXPANSION"
    EMERGING_RISK = "EMERGING_RISK"
    MISSING_VERIFICATION = "MISSING_VERIFICATION"
    ANOMALOUS_STATE = "ANOMALOUS_STATE"
    OTHER = "OTHER"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.OTHER


class ExecutionSupervisionStatus(str, Enum):
    """Lifecycle monitoring state of execution plan supervision."""
    SUPERVISING = "SUPERVISING"
    BLOCKED = "BLOCKED"
    ESCALATED = "ESCALATED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.SUPERVISING


class EngineeringHandoffType(str, Enum):
    """Authoritative taxonomy of structured cross-worker handoffs involving Programmer."""
    RESEARCH_TO_PROGRAMMER = "RESEARCH_TO_PROGRAMMER"
    PROGRAMMER_TO_DESIGNER = "PROGRAMMER_TO_DESIGNER"
    PROGRAMMER_TO_TESTER = "PROGRAMMER_TO_TESTER"
    TESTER_TO_PROGRAMMER = "TESTER_TO_PROGRAMMER"
    DESIGNER_TO_PROGRAMMER = "DESIGNER_TO_PROGRAMMER"
    PROGRAMMER_TO_DEPLOYMENT = "PROGRAMMER_TO_DEPLOYMENT"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return None


class HandoffPriority(str, Enum):
    """Priority level assigned to an engineering handoff."""
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.NORMAL


class EpistemicContextType(str, Enum):
    """Authoritative epistemic taxonomy for context elements attached to engineering contracts."""
    OBSERVED_RESEARCH_EVIDENCE = "OBSERVED_RESEARCH_EVIDENCE"
    INFERRED_REQUIREMENTS = "INFERRED_REQUIREMENTS"
    MANAGER_DEFINED_REQUIREMENTS = "MANAGER_DEFINED_REQUIREMENTS"
    PROGRAMMER_ENGINEERING_ASSUMPTIONS = "PROGRAMMER_ENGINEERING_ASSUMPTIONS"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.OBSERVED_RESEARCH_EVIDENCE


class DesignerContextClassification(str, Enum):
    """Classification distinguishing binding technical requirements from non-binding design advice."""
    TECHNICAL_REQUIREMENT = "TECHNICAL_REQUIREMENT"
    DESIGN_RECOMMENDATION = "DESIGN_RECOMMENDATION"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.TECHNICAL_REQUIREMENT


class VerificationDomain(str, Enum):
    """Explicit distinction between internal developer verification and independent QA verification."""
    PROGRAMMER_VERIFICATION = "PROGRAMMER_VERIFICATION"
    TESTER_VERIFICATION = "TESTER_VERIFICATION"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.PROGRAMMER_VERIFICATION


class ApiChangeType(str, Enum):
    """Categorization of an API change communicated in an engineering handoff."""
    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    DEPRECATED = "DEPRECATED"
    REMOVED = "REMOVED"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.MODIFIED


class FeedbackIssueType(str, Enum):
    """Taxonomy of issue categories reported in cross-worker feedback."""
    BUG = "BUG"
    REGRESSION = "REGRESSION"
    UX_INTEGRATION = "UX_INTEGRATION"
    API_CONTRACT = "API_CONTRACT"
    PERFORMANCE = "PERFORMANCE"
    SECURITY = "SECURITY"
    REQUIREMENT_MISMATCH = "REQUIREMENT_MISMATCH"
    OTHER = "OTHER"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.OTHER


class FeedbackSeverity(str, Enum):
    """Severity classification of cross-worker feedback issues."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.MEDIUM


class FeedbackConfidence(str, Enum):
    """Degree of epistemic reproducibility and confidence in the feedback."""
    PROBABLE = "PROBABLE"
    CONFIRMED = "CONFIRMED"
    REPRODUCED = "REPRODUCED"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.REPRODUCED


class ManagerIterationDecision(str, Enum):
    """
    Authoritative decision vocabulary available to Manager when evaluating
    engineering outcomes and peer worker feedback during iteration.
    """
    REQUEST_FIX = "REQUEST_FIX"
    REQUEST_DESIGN_CHANGE = "REQUEST_DESIGN_CHANGE"
    REQUEST_RESEARCH = "REQUEST_RESEARCH"
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    CANCEL = "CANCEL"
    ESCALATE = "ESCALATE"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.ESCALATE


class ProductArtifactType(str, Enum):
    """
    Taxonomy of software product artifacts produced by a Programmer execution.
    Clearly distinguishes source code, build output, deployable package,
    configuration, and documentation.
    """
    SOURCE = "SOURCE"
    BUILD = "BUILD"
    PACKAGE = "PACKAGE"
    CONTAINER = "CONTAINER"
    DOCUMENTATION = "DOCUMENTATION"
    CONFIGURATION = "CONFIGURATION"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.SOURCE


class DeployabilityStatus(str, Enum):
    """
    Assessment status of an artifact's deployability.
    Do not imply that an artifact is deployable merely because it exists.
    """
    NOT_DEPLOYABLE = "NOT_DEPLOYABLE"
    UNDEFINED = "UNDEFINED"
    INELIGIBLE = "INELIGIBLE"
    QUALIFIED = "QUALIFIED"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.UNDEFINED


class BuildPackagingStatus(str, Enum):
    """
    Execution and verification status of a build and packaging operation.
    """
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNAUTHORIZED = "UNAUTHORIZED"
    ESCALATED = "ESCALATED"
    MISMATCH = "MISMATCH"
    MISSING_ARTIFACT = "MISSING_ARTIFACT"
    MISSING_COMMAND = "MISSING_COMMAND"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.FAILED


class ConfigurationFieldType(str, Enum):
    """
    Data types supported for software runtime configuration fields.
    Configuration is data, not executable code.
    """
    STRING = "STRING"
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    BOOLEAN = "BOOLEAN"
    LIST = "LIST"
    DICT = "DICT"
    ENUM = "ENUM"
    SECRET_REF = "SECRET_REF"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.STRING


class ConfigurationValidationStatus(str, Enum):
    """
    Status of runtime configuration validation against its schema.
    """
    VALID = "VALID"
    INVALID = "INVALID"
    PENDING = "PENDING"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
class DeploymentReadinessStatus(str, Enum):
    """
    Authoritative readiness status of a ProductArtifact evaluated for deployment.
    """
    READY = "READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    NOT_READY = "NOT_READY"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.UNKNOWN


class EnvironmentRequirementType(str, Enum):
    """Data type of an environment requirement or variable."""
    STRING = "STRING"
    INTEGER = "INTEGER"
    BOOLEAN = "BOOLEAN"
    URL = "URL"
    SECRET_REF = "SECRET_REF"
    PATH = "PATH"
    JSON = "JSON"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.STRING


class EnvironmentRequirementSource(str, Enum):
    """Source provenance of an identified environment requirement."""
    INFERRED = "INFERRED"
    MANIFEST = "MANIFEST"
    DECLARED = "DECLARED"
    MANAGER = "MANAGER"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.DECLARED


class DeploymentHandoffStatus(str, Enum):
    """
    Status of a structured product deployment handoff.
    """
    READY = "READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    NOT_READY = "NOT_READY"
    BLOCKED = "BLOCKED"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.NOT_READY


class ProductLifecycleState(str, Enum):
    """
    Deterministic lifecycle state of a software product artifact.
    Explicitly distinguishes build completion, verification, deployment readiness,
    and actual deployment. Programmer may reach DEPLOYMENT_READY, but cannot autonomously
    declare DEPLOYED.
    """
    BUILD_COMPLETE = "BUILD_COMPLETE"
    VERIFIED = "VERIFIED"
    DEPLOYMENT_READY = "DEPLOYMENT_READY"
    DEPLOYED = "DEPLOYED"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.BUILD_COMPLETE


class DeploymentRecommendation(str, Enum):
    """
    Programmer's advisory recommendation for Manager regarding deployment action.
    Programmer recommends, but Manager remains the authoritative orchestrator.
    """
    DEPLOY = "DEPLOY"
    FIX = "FIX"
    INVESTIGATE = "INVESTIGATE"
    CONFIGURE = "CONFIGURE"

    @classmethod
    def _missing_(cls, value: object):
        val_str = str(value).upper()
        for member in cls:
            if member.value == val_str:
                return member
        return cls.INVESTIGATE



















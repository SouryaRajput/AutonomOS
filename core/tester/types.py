from __future__ import annotations

from enum import Enum

class WorkerType(str, Enum):
    """Worker types recognized within AutonomOS orchestration."""
    __test__ = False
    TESTER = "TESTER"
    PROGRAMMER = "PROGRAMMER"
    RESEARCHER = "RESEARCHER"


class TesterWorkOrderStatus(str, Enum):
    """Lifecycle status of a TesterWorkOrder authorized by Manager."""
    __test__ = False
    CREATED = "CREATED"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TesterExecutionStatus(str, Enum):
    """Operational lifecycle status of an active Tester execution attempt."""
    __test__ = False
    REQUESTED = "REQUESTED"
    STARTING = "STARTING"
    PLANNING = "PLANNING"
    PLAN_VALIDATION = "PLAN_VALIDATION"
    PLAN_FROZEN = "PLAN_FROZEN"
    RUNNING = "RUNNING"
    EVALUATING = "EVALUATING"
    REPORTING = "REPORTING"
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


class TesterExecutionPhase(str, Enum):
    """Descriptive operational phase of an active Tester execution."""
    __test__ = False
    PREPARING = "PREPARING"
    PLANNING = "PLANNING"
    PLAN_VALIDATION = "PLAN_VALIDATION"
    PLAN_FROZEN = "PLAN_FROZEN"
    EXECUTING = "EXECUTING"
    EVALUATING = "EVALUATING"
    REPORTING = "REPORTING"


class TesterResultStatus(str, Enum):
    """Authoritative outcome status returned by the Tester to the Manager."""
    __test__ = False
    COMPLETED = "COMPLETED"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DefectSeverity(str, Enum):
    """Severity classification of an identified concrete defect."""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class DefectType(str, Enum):
    """Taxonomy of defects identified during product evaluation."""
    FUNCTIONAL = "FUNCTIONAL"
    REGRESSION = "REGRESSION"
    CRASH = "CRASH"
    RUNTIME = "RUNTIME"
    API = "API"
    NAVIGATION = "NAVIGATION"
    RESOURCE = "RESOURCE"
    BUILD = "BUILD"
    INTEGRATION = "INTEGRATION"
    SECURITY = "SECURITY"
    PERFORMANCE = "PERFORMANCE"
    UX = "UX"
    SPEC_VIOLATION = "SPEC_VIOLATION"
    VISUAL = "VISUAL"
    LAYOUT = "LAYOUT"
    SCROLL = "SCROLL"
    OVERFLOW = "OVERFLOW"
    RESPONSIVE = "RESPONSIVE"
    TYPOGRAPHY = "TYPOGRAPHY"
    ANIMATION = "ANIMATION"
    STABILITY = "STABILITY"
    OTHER = "OTHER"


class FindingCategory(str, Enum):
    """Classification of evaluative findings reported by Tester."""
    DEFECT = "DEFECT"
    UX = "UX"
    PERFORMANCE = "PERFORMANCE"
    OBSERVATION = "OBSERVATION"
    RECOMMENDATION = "RECOMMENDATION"
    UNCERTAINTY = "UNCERTAINTY"


class AcceptanceCriterionStatus(str, Enum):
    """Verification status of an explicit acceptance criterion."""
    __test__ = False
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_VERIFIED = "NOT_VERIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNCERTAIN = "UNCERTAIN"
    NOT_EVALUATED = "NOT_EVALUATED"


class TestCaseStatus(str, Enum):
    """Operational evaluation status for a discrete test case."""
    __test__ = False
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"
    NOT_VERIFIED = "NOT_VERIFIED"


class EvidenceType(str, Enum):
    """Taxonomy of evidence records collected or produced during testing."""
    __test__ = False
    SCREENSHOT = "SCREENSHOT"
    VIDEO = "VIDEO"
    OCR = "OCR"
    METRIC = "METRIC"
    LOG = "LOG"
    TEST_OUTPUT = "TEST_OUTPUT"
    OBSERVATION = "OBSERVATION"
    OTHER = "OTHER"


class ShipRecommendation(str, Enum):
    """Advisory release disposition recommendation reported by Tester to Manager."""
    __test__ = False
    SHIP = "SHIP"
    SHIP_WITH_WARNINGS = "SHIP_WITH_WARNINGS"
    DO_NOT_SHIP = "DO_NOT_SHIP"
    NOT_VERIFIED = "NOT_VERIFIED"


class RecommendationPriority(str, Enum):
    """Priority level for advisory recommendations reported to Manager."""
    __test__ = False
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class AcceptanceSummaryStatus(str, Enum):
    """Aggregate evaluation status of acceptance criteria for a product."""
    __test__ = False
    ALL_PASSED = "ALL_PASSED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    NOT_VERIFIED = "NOT_VERIFIED"


class TesterActionType(str, Enum):
    """Discrete auditable actions performed during Tester execution."""
    __test__ = False
    INITIALIZE = "INITIALIZE"
    EXECUTE_TEST = "EXECUTE_TEST"
    OBSERVE = "OBSERVE"
    RECORD_OBSERVATION = "RECORD_OBSERVATION"
    CAPTURE_EVIDENCE = "CAPTURE_EVIDENCE"
    EVALUATE_CRITERIA = "EVALUATE_CRITERIA"
    IDENTIFY_DEFECT = "IDENTIFY_DEFECT"
    REPORT_FINDING = "REPORT_FINDING"
    REPORT_RECOMMENDATION = "REPORT_RECOMMENDATION"
    REPORT_UNCERTAINTY = "REPORT_UNCERTAINTY"
    NAVIGATE = "NAVIGATE"
    CLICK = "CLICK"
    DOUBLE_CLICK = "DOUBLE_CLICK"
    TYPE = "TYPE"
    PRESS_KEY = "PRESS_KEY"
    SCROLL = "SCROLL"
    HOVER = "HOVER"
    DRAG = "DRAG"
    MOVE_CURSOR = "MOVE_CURSOR"
    WAIT = "WAIT"
    CAPTURE_SCREENSHOT = "CAPTURE_SCREENSHOT"
    START_RECORDING = "START_RECORDING"
    STOP_RECORDING = "STOP_RECORDING"
    BUILD_CONTEXT = "BUILD_CONTEXT"
    CLASSIFY_APPLICABILITY = "CLASSIFY_APPLICABILITY"
    GENERATE_PLAN = "GENERATE_PLAN"
    EVALUATE_COVERAGE = "EVALUATE_COVERAGE"
    VALIDATE_PLAN = "VALIDATE_PLAN"
    FREEZE_PLAN = "FREEZE_PLAN"
    PREFLIGHT_CHECK = "PREFLIGHT_CHECK"
    EVALUATE_RUNTIME = "EVALUATE_RUNTIME"


class TestingCapability(str, Enum):
    """Authorized evaluative capabilities permitted for Tester (Tester MAY)."""
    __test__ = False
    # General evaluation capabilities
    TEST_EXECUTION = "TEST_EXECUTION"
    BEHAVIOR_OBSERVATION = "BEHAVIOR_OBSERVATION"
    EVIDENCE_COLLECTION = "EVIDENCE_COLLECTION"
    ACCEPTANCE_EVALUATION = "ACCEPTANCE_EVALUATION"
    DEFECT_IDENTIFICATION = "DEFECT_IDENTIFICATION"
    UX_PERFORMANCE_ANALYSIS = "UX_PERFORMANCE_ANALYSIS"
    RECOMMENDATION_REPORTING = "RECOMMENDATION_REPORTING"
    UNCERTAINTY_REPORTING = "UNCERTAINTY_REPORTING"

    # Specific interaction & observation capabilities
    NAVIGATE = "NAVIGATE"
    CLICK = "CLICK"
    TYPE = "TYPE"
    SCROLL = "SCROLL"
    HOVER = "HOVER"
    DRAG = "DRAG"
    KEYBOARD_INPUT = "KEYBOARD_INPUT"
    MOVE_CURSOR = "MOVE_CURSOR"
    WAIT = "WAIT"
    SCREENSHOT = "SCREENSHOT"
    SCREEN_RECORDING = "SCREEN_RECORDING"
    OCR = "OCR"
    PERFORMANCE_MEASUREMENT = "PERFORMANCE_MEASUREMENT"


class TestCategory(str, Enum):
    """Taxonomy of test and evaluation categories requested by Manager."""
    __test__ = False
    FUNCTIONAL = "FUNCTIONAL"
    INTEGRATION = "INTEGRATION"
    E2E = "E2E"
    SMOKE = "SMOKE"
    REGRESSION = "REGRESSION"
    SECURITY = "SECURITY"
    UI = "UI"
    UI_INTERACTION = "UI_INTERACTION"
    UX = "UX"
    PERFORMANCE = "PERFORMANCE"
    STABILITY = "STABILITY"
    TYPOGRAPHY = "TYPOGRAPHY"
    ANIMATION = "ANIMATION"
    RESPONSIVENESS = "RESPONSIVENESS"
    RESPONSIVE = "RESPONSIVE"
    VISUAL = "VISUAL"
    NAVIGATION = "NAVIGATION"
    OCR = "OCR"
    VIDEO = "VIDEO"
    ONBOARDING = "ONBOARDING"
    ACCESSIBILITY = "ACCESSIBILITY"
    OTHER = "OTHER"


class ForbiddenTesterAction(str, Enum):
    """Explicit taxonomy of strictly forbidden operations (Tester MUST NOT)."""
    MODIFY_SOURCE_CODE = "MODIFY_SOURCE_CODE"
    FIX_DEFECT = "FIX_DEFECT"
    CHANGE_REQUIREMENTS = "CHANGE_REQUIREMENTS"
    EXPAND_TEST_SCOPE = "EXPAND_TEST_SCOPE"
    GRANT_PERMISSIONS = "GRANT_PERMISSIONS"
    DEPLOY_PRODUCT = "DEPLOY_PRODUCT"
    MERGE_CODE = "MERGE_CODE"
    OVERRIDE_MANAGER_DECISION = "OVERRIDE_MANAGER_DECISION"
    SUBJECTIVE_DEFECT_ENFORCEMENT = "SUBJECTIVE_DEFECT_ENFORCEMENT"
    INFINITE_IMPROVEMENT_LOOP = "INFINITE_IMPROVEMENT_LOOP"


class TesterBlockerCategory(str, Enum):
    """Categorization of material impediments requiring Manager intervention."""
    __test__ = False
    ENVIRONMENT = "ENVIRONMENT"
    PERMISSION = "PERMISSION"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    SPECIFICATION_AMBIGUITY = "SPECIFICATION_AMBIGUITY"
    PRODUCT_UNAVAILABLE = "PRODUCT_UNAVAILABLE"
    RESOURCE = "RESOURCE"
    OTHER = "OTHER"


class TesterBlockerSeverity(str, Enum):
    """Severity classification of an operational blocker."""
    __test__ = False
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EnvironmentType(str, Enum):
    """Supported product execution environment types for testing."""
    __test__ = False
    BROWSER = "BROWSER"
    LOCAL_APP = "LOCAL_APP"
    OTHER = "OTHER"


class TestRuntimeStatus(str, Enum):
    """Operational lifecycle status of an active test execution runtime."""
    __test__ = False
    CREATED = "CREATED"
    STARTING = "STARTING"
    READY = "READY"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class TestActionStatus(str, Enum):
    """Execution status of an individual testing interaction action."""
    __test__ = False
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    DENIED = "DENIED"
    TIMED_OUT = "TIMED_OUT"


class InteractionStatus(str, Enum):
    """Result status of a physical product interaction action."""
    __test__ = False
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    DENIED = "DENIED"
    TIMEOUT = "TIMEOUT"
    NOT_SUPPORTED = "NOT_SUPPORTED"


class ScreenshotCaptureReason(str, Enum):
    """Authoritative rationale for capturing a visual screenshot artifact."""
    __test__ = False
    MANUAL_REQUEST = "MANUAL_REQUEST"
    BEFORE_ACTION = "BEFORE_ACTION"
    AFTER_ACTION = "AFTER_ACTION"
    STATE_CHANGE = "STATE_CHANGE"
    FAILURE = "FAILURE"
    CHECKPOINT = "CHECKPOINT"


class ScreenshotCaptureStatus(str, Enum):
    """Outcome status of a visual screenshot capture attempt."""
    __test__ = False
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    DENIED = "DENIED"
    TIMEOUT = "TIMEOUT"
    NOT_SUPPORTED = "NOT_SUPPORTED"


class ScreenRecordingStatus(str, Enum):
    """Lifecycle and outcome status of a screen recording attempt."""
    __test__ = False
    IDLE = "IDLE"
    STARTING = "STARTING"
    RECORDING = "RECORDING"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DENIED = "DENIED"
    NOT_SUPPORTED = "NOT_SUPPORTED"


class RecordingCaptureReason(str, Enum):
    """Authoritative rationale for capturing a screen recording video artifact."""
    __test__ = False
    MANUAL_REQUEST = "MANUAL_REQUEST"
    TEST_FLOW = "TEST_FLOW"
    FAILURE = "FAILURE"
    CHECKPOINT = "CHECKPOINT"
    INTERACTION_SEQUENCE = "INTERACTION_SEQUENCE"


# ---------------------------------------------------------------------------
# Phase 3.1 Test Context & Change Understanding Enums
# ---------------------------------------------------------------------------

class FactStatus(str, Enum):
    """
    Epistemic classification of a context assertion or property.
    Strictly distinguishes directly supported facts from inferred or unknown data.
    """
    __test__ = False
    FACT = "FACT"
    INFERENCE = "INFERENCE"
    UNKNOWN = "UNKNOWN"


class PresenceStatus(str, Enum):
    """Presence determination of an architectural layer or component."""
    __test__ = False
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


class ChangeCategory(str, Enum):
    """
    Deterministic categorization of detected code, configuration, or environment changes.
    A single change may belong to multiple categories.
    """
    __test__ = False
    FRONTEND = "FRONTEND"
    BACKEND = "BACKEND"
    API = "API"
    DATABASE = "DATABASE"
    CONFIGURATION = "CONFIGURATION"
    BUILD = "BUILD"
    DEPENDENCY = "DEPENDENCY"
    DOCUMENTATION = "DOCUMENTATION"
    TEST_ONLY = "TEST_ONLY"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    UNKNOWN = "UNKNOWN"


class TestSurface(str, Enum):
    """
    Surfaces of a product under test that may be applicable for testing.
    Records existence/presence without deciding execution applicability in Phase 3.1.
    """
    __test__ = False
    UI = "UI"
    NAVIGATION = "NAVIGATION"
    USER_INTERACTION = "USER_INTERACTION"
    API = "API"
    BUSINESS_LOGIC = "BUSINESS_LOGIC"
    DATA_FLOW = "DATA_FLOW"
    INTEGRATION = "INTEGRATION"
    RESPONSIVE_LAYOUT = "RESPONSIVE_LAYOUT"
    ANIMATION = "ANIMATION"
    TYPOGRAPHY = "TYPOGRAPHY"
    VISUAL_LAYOUT = "VISUAL_LAYOUT"
    PERFORMANCE = "PERFORMANCE"
    VIDEO = "VIDEO"
    OCR = "OCR"
    ACCESSIBILITY = "ACCESSIBILITY"


# ---------------------------------------------------------------------------
# Phase 3.2 Test Applicability Classification Enums
# ---------------------------------------------------------------------------

class ApplicabilityLevel(str, Enum):
    """
    Classification of applicability for a testing category.
    Strictly distinguishes REQUIRED, OPTIONAL, NOT_APPLICABLE, and UNKNOWN.
    """
    __test__ = False
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class ApplicableTestCategory(str, Enum):
    """
    Core V1 taxonomy of testing categories evaluated for applicability in Tester V1.
    """
    __test__ = False
    FUNCTIONAL = "FUNCTIONAL"
    UI_INTERACTION = "UI_INTERACTION"
    NAVIGATION = "NAVIGATION"
    VISUAL = "VISUAL"
    RESPONSIVE = "RESPONSIVE"
    ANIMATION = "ANIMATION"
    OCR = "OCR"
    VIDEO = "VIDEO"
    PERFORMANCE = "PERFORMANCE"
    LOAD = "LOAD"
    INTERACTION = "INTERACTION"
    NETWORK = "NETWORK"
    RESOURCE = "RESOURCE"
    STABILITY = "STABILITY"
    INTEGRATION = "INTEGRATION"
    TYPOGRAPHY = "TYPOGRAPHY"
    UX = "UX"


# ---------------------------------------------------------------------------
# Phase 3.3 Test Plan Generation Enums
# ---------------------------------------------------------------------------

class TestPlanStatus(str, Enum):
    """Lifecycle status of a finite TestPlan."""
    __test__ = False
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    FROZEN = "FROZEN"
    INVALID = "INVALID"


class TestPriority(str, Enum):
    """Priority level for an individual TestCase."""
    __test__ = False
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class CoverageState(str, Enum):
    """
    Coverage state of an acceptance criterion, changed surface, or category.
    Strictly distinguishes between 'we did not test this' (UNCOVERED)
    and 'this does not apply' (NOT_APPLICABLE).
    """
    __test__ = False
    COVERED = "COVERED"
    PARTIALLY_COVERED = "PARTIALLY_COVERED"
    UNCOVERED = "UNCOVERED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class CoverageGapReason(str, Enum):
    """Factual root cause for a coverage gap."""
    __test__ = False
    MISSING_TEST = "MISSING_TEST"
    UNAVAILABLE_CAPABILITY = "UNAVAILABLE_CAPABILITY"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
    AUTHORIZATION_LIMITATION = "AUTHORIZATION_LIMITATION"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CoverageGapSeverity(str, Enum):
    """Severity of an uncovered criterion or surface."""
    __test__ = False
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ---------------------------------------------------------------------------
# Phase 3.5 Test Plan Validation Enums
# ---------------------------------------------------------------------------

class PlanValidationCode(str, Enum):
    """
    Structured reason codes for TestPlan validation results.
    Guarantees machine-parsable, deterministic failure reporting.
    """
    __test__ = False
    INVALID_LINEAGE = "INVALID_LINEAGE"
    UNAUTHORIZED_CATEGORY = "UNAUTHORIZED_CATEGORY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    MISSING_ACCEPTANCE_REFERENCE = "MISSING_ACCEPTANCE_REFERENCE"
    INVALID_TEST_CASE = "INVALID_TEST_CASE"
    MISSING_RUNTIME_CAPABILITY = "MISSING_RUNTIME_CAPABILITY"
    MISSING_ENVIRONMENT = "MISSING_ENVIRONMENT"
    UNBOUNDED_PLAN = "UNBOUNDED_PLAN"
    INVALID_EVIDENCE_REQUIREMENT = "INVALID_EVIDENCE_REQUIREMENT"
    FORBIDDEN_CAPABILITY = "FORBIDDEN_CAPABILITY"
    DUPLICATE_TEST_CASE = "DUPLICATE_TEST_CASE"
    RECURSIVE_TEST_DEFINITION = "RECURSIVE_TEST_DEFINITION"
    EMPTY_TEST_OBJECTIVE = "EMPTY_TEST_OBJECTIVE"
    EMPTY_EXPECTED_OUTCOME = "EMPTY_EXPECTED_OUTCOME"
    PLAN_STATE_ERROR = "PLAN_STATE_ERROR"
    NO_APPLICABLE_TESTS = "NO_APPLICABLE_TESTS"


class ValidationIssueSeverity(str, Enum):
    """Severity of an issue found during TestPlan validation."""
    __test__ = False
    ERROR = "ERROR"
    WARNING = "WARNING"


# ---------------------------------------------------------------------------
# Phase 4.1 Observation Model Enums
# ---------------------------------------------------------------------------

class ObservationType(str, Enum):
    """
    Categorization of structured observations recorded during test execution.
    Purely descriptive; does NOT represent evaluation, defects, or pass/fail verdict.
    """
    __test__ = False
    SCREEN = "SCREEN"
    TEXT = "TEXT"
    GEOMETRY = "GEOMETRY"
    VIDEO_FRAME = "VIDEO_FRAME"
    UI_STATE = "UI_STATE"
    RUNTIME_STATE = "RUNTIME_STATE"
    METRIC = "METRIC"
    OTHER = "OTHER"


class ObservationConfidence(str, Enum):
    """
    Confidence tier for an observation source.
    Represents certainty/uncertainty explicitly without converting uncertain claims into facts.
    """
    __test__ = False
    CERTAIN = "CERTAIN"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNCERTAIN = "UNCERTAIN"


# ---------------------------------------------------------------------------
# Phase 4.2 OCR Engine Enums
# ---------------------------------------------------------------------------

class OCRStatus(str, Enum):
    """
    Execution status of an OCR text extraction operation.
    Bounded and descriptive; does NOT represent test pass/fail verdict.
    """
    __test__ = False
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    UNSUPPORTED = "UNSUPPORTED"


# ---------------------------------------------------------------------------
# Phase 4.3 Visual Geometry & Layout Observation Enums
# ---------------------------------------------------------------------------

class CoordinateSystem(str, Enum):
    """Reference coordinate frame for spatial and geometric measurements."""
    __test__ = False
    VIEWPORT = "VIEWPORT"
    PAGE = "PAGE"
    NORMALIZED = "NORMALIZED"
    CONTAINER_RELATIVE = "CONTAINER_RELATIVE"


class VisibilityState(str, Enum):
    """Deterministically observable visual presence state of a geometric surface."""
    __test__ = False
    VISIBLE = "VISIBLE"
    HIDDEN = "HIDDEN"
    PARTIALLY_VISIBLE = "PARTIALLY_VISIBLE"
    CLIPPED = "CLIPPED"
    OFFSCREEN = "OFFSCREEN"
    UNKNOWN = "UNKNOWN"


class GeometryStatus(str, Enum):
    """Availability status of spatial and geometric observation measurements."""
    __test__ = False
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    INVALID = "INVALID"


# ---------------------------------------------------------------------------
# Phase 4.4 Video & Frame Observation Enums
# ---------------------------------------------------------------------------

class FrameExtractionStatus(str, Enum):
    """Execution status of video frame observation and extraction."""
    __test__ = False
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"
    TIMEOUT = "TIMEOUT"


class FrameSelectionStrategy(str, Enum):
    """Bounded strategy for sampling or selecting frames from video evidence."""
    __test__ = False
    TIMESTAMP = "TIMESTAMP"
    FRAME_INDEX = "FRAME_INDEX"
    INTERVAL = "INTERVAL"
    SAMPLE = "SAMPLE"


# ---------------------------------------------------------------------------
# Phase 4.5 Observation Aggregation Enums
# ---------------------------------------------------------------------------

class ObservationCompleteness(str, Enum):
    """Completeness state of an aggregated observation set."""
    __test__ = False
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    EMPTY = "EMPTY"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Phase 5.1 Test Preflight & Application Health Enums
# ---------------------------------------------------------------------------

class PreflightStatus(str, Enum):
    """Execution status of application preflight and health verification."""
    __test__ = False
    PASS = "PASS"
    WARNINGS = "WARNINGS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    NOT_VERIFIED = "NOT_VERIFIED"


class PreflightDecision(str, Enum):
    """Authoritative decision on whether planned test execution can proceed."""
    __test__ = False
    READY = "READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class ApplicationHealthStatus(str, Enum):
    """Evaluated runtime operational health of the application under test."""
    __test__ = False
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNREACHABLE = "UNREACHABLE"
    STARTUP_FAILED = "STARTUP_FAILED"
    UNKNOWN = "UNKNOWN"


class BuildStatus(str, Enum):
    """Verification status of authorized build or compilation steps."""
    __test__ = False
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


# ---------------------------------------------------------------------------
# Phase 5.3 Runtime & Request Evaluation Enums
# ---------------------------------------------------------------------------

class RuntimeEventType(str, Enum):
    """Taxonomy of runtime events observed during application execution."""
    __test__ = False
    HTTP_REQUEST = "HTTP_REQUEST"
    HTTP_RESPONSE = "HTTP_RESPONSE"
    CONSOLE_ERROR = "CONSOLE_ERROR"
    CONSOLE_WARNING = "CONSOLE_WARNING"
    PROCESS_ERROR = "PROCESS_ERROR"
    APPLICATION_CRASH = "APPLICATION_CRASH"
    SERVER_TERMINATION = "SERVER_TERMINATION"
    BROWSER_CRASH = "BROWSER_CRASH"
    UNHANDLED_EXCEPTION = "UNHANDLED_EXCEPTION"
    UNREACHABLE = "UNREACHABLE"
    NAVIGATION_FAILURE = "NAVIGATION_FAILURE"
    RESOURCE_FAILURE = "RESOURCE_FAILURE"
    API_FAILURE = "API_FAILURE"


class RuntimeEventSeverity(str, Enum):
    """
    Technical seriousness classification of an observed runtime event.
    Does not itself determine final defect severity.
    """
    __test__ = False
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Phase 6.1 Visual Assertion & Geometry Evaluation Enums
# ---------------------------------------------------------------------------

class VisualCheckType(str, Enum):
    """Taxonomy of deterministic visual and geometric checks evaluated in Tester V1."""
    __test__ = False
    OVERLAP = "OVERLAP"
    CLIPPING = "CLIPPING"
    CONTAINER_BOUNDS = "CONTAINER_BOUNDS"
    VIEWPORT_OVERFLOW = "VIEWPORT_OVERFLOW"
    VISIBILITY = "VISIBILITY"
    GEOMETRY_ASSERTION = "GEOMETRY_ASSERTION"


class VisualAssertionStatus(str, Enum):
    """Evaluation status of a discrete visual assertion or geometry check."""
    __test__ = False
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    UNVERIFIED = "UNVERIFIED"


# ---------------------------------------------------------------------------
# Phase 6.2 Scrolling & Overflow Evaluation Enums
# ---------------------------------------------------------------------------

class ScrollDirection(str, Enum):
    """Supported directional vectors for scrolling evaluations."""
    __test__ = False
    VERTICAL = "VERTICAL"
    HORIZONTAL = "HORIZONTAL"
    UP = "UP"
    DOWN = "DOWN"
    LEFT = "LEFT"
    RIGHT = "RIGHT"


class ScrollEvaluationStatus(str, Enum):
    """Operational evaluation status for a discrete scrolling check."""
    __test__ = False
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    UNVERIFIED = "UNVERIFIED"
    SKIPPED = "SKIPPED"


# ---------------------------------------------------------------------------
# Phase 6.3 Responsive Layout Evaluation Enums
# ---------------------------------------------------------------------------

class DeviceCategory(str, Enum):
    """Categorization of display viewport profile for responsive evaluation."""
    __test__ = False
    MOBILE = "MOBILE"
    TABLET = "TABLET"
    DESKTOP = "DESKTOP"
    CUSTOM = "CUSTOM"


class ResponsiveCheckType(str, Enum):
    """Taxonomy of deterministic responsive layout checks evaluated in Tester V1."""
    __test__ = False
    CLIPPING = "CLIPPING"
    HORIZONTAL_OVERFLOW = "HORIZONTAL_OVERFLOW"
    INACCESSIBLE_CONTROL = "INACCESSIBLE_CONTROL"
    OVERLAP = "OVERLAP"
    NAVIGATION_ADAPTATION = "NAVIGATION_ADAPTATION"
    CONTENT_VISIBILITY = "CONTENT_VISIBILITY"
    LAYOUT_COLLAPSE = "LAYOUT_COLLAPSE"


class ResponsiveEvaluationStatus(str, Enum):
    """Operational evaluation status for a discrete responsive layout check."""
    __test__ = False
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    UNVERIFIED = "UNVERIFIED"


# ---------------------------------------------------------------------------
# Phase 6.4 Typography & Text Presentation Evaluation Enums
# ---------------------------------------------------------------------------

class TypographyCheckType(str, Enum):
    """Taxonomy of deterministic typography and text presentation checks in Tester V1."""
    __test__ = False
    REQUIRED_TEXT_MISSING = "REQUIRED_TEXT_MISSING"
    TEXT_CLIPPING = "TEXT_CLIPPING"
    TEXT_OVERLAP = "TEXT_OVERLAP"
    TEXT_OUTSIDE_EXPECTED_REGION = "TEXT_OUTSIDE_EXPECTED_REGION"
    SEVERE_WRAPPING = "SEVERE_WRAPPING"
    UNREADABLE_TEXT = "UNREADABLE_TEXT"
    SUBJECTIVE_AESTHETIC = "SUBJECTIVE_AESTHETIC"


class TypographyEvaluationStatus(str, Enum):
    """Evaluation status for a discrete typography or text presentation check."""
    __test__ = False
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_VERIFIED = "NOT_VERIFIED"
    SKIPPED = "SKIPPED"


# ---------------------------------------------------------------------------
# Phase 6.5 Animation & Transition Evaluation Enums
# ---------------------------------------------------------------------------

class AnimationCheckType(str, Enum):
    """Taxonomy of deterministic animation and transition checks in Tester V1."""
    __test__ = False
    START_ON_TRIGGER = "START_ON_TRIGGER"
    REACH_EXPECTED_STATE = "REACH_EXPECTED_STATE"
    TRANSITION_COMPLETION = "TRANSITION_COMPLETION"
    ELEMENT_APPEARANCE = "ELEMENT_APPEARANCE"
    ELEMENT_DISAPPEARANCE = "ELEMENT_DISAPPEARANCE"
    VISUAL_JUMP_OR_COLLAPSE = "VISUAL_JUMP_OR_COLLAPSE"
    STUCK_ANIMATION = "STUCK_ANIMATION"
    INCOMPLETE_FINAL_STATE = "INCOMPLETE_FINAL_STATE"
    SUBJECTIVE_MOTION = "SUBJECTIVE_MOTION"


class AnimationEvaluationStatus(str, Enum):
    """Operational evaluation status for an animation or transition check."""
    __test__ = False
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_VERIFIED = "NOT_VERIFIED"
    SKIPPED = "SKIPPED"


# ---------------------------------------------------------------------------
# Phase 6.6 UX Flow & Usability Evaluation Enums
# ---------------------------------------------------------------------------

class UXCheckType(str, Enum):
    """Taxonomy of concrete UX flow and usability checks in Tester V1."""
    __test__ = False
    REQUIRED_ACTION_BLOCKED = "REQUIRED_ACTION_BLOCKED"
    INACCESSIBLE_CONTROL = "INACCESSIBLE_CONTROL"
    CONFUSING_NAVIGATION = "CONFUSING_NAVIGATION"
    UNEXPECTED_STEP = "UNEXPECTED_STEP"
    MISSING_FEEDBACK = "MISSING_FEEDBACK"
    UNRECOVERABLE_ERROR = "UNRECOVERABLE_ERROR"
    ONBOARDING_FAILURE = "ONBOARDING_FAILURE"
    MISSING_NEXT_ACTION = "MISSING_NEXT_ACTION"
    SUBJECTIVE_PREFERENCE = "SUBJECTIVE_PREFERENCE"
    VAGUE_CRITIQUE = "VAGUE_CRITIQUE"


class UXEvaluationStatus(str, Enum):
    """Operational evaluation status for a UX flow check."""
    __test__ = False
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_VERIFIED = "NOT_VERIFIED"
    SKIPPED = "SKIPPED"
    DISCARDED = "DISCARDED"


# ---------------------------------------------------------------------------
# Phase 7.1 Performance Context & Measurement Enums
# ---------------------------------------------------------------------------

class PerformanceMetricType(str, Enum):
    """Taxonomy of measurable runtime performance metrics in Tester V1."""
    __test__ = False
    NAVIGATION_DURATION = "navigation_duration"
    PAGE_LOAD_DURATION = "page_load_duration"
    APPLICATION_STARTUP_DURATION = "application_startup_duration"
    FIRST_CONTENTFUL_PAINT = "first_contentful_paint"
    LARGEST_CONTENTFUL_PAINT = "largest_contentful_paint"
    DOM_CONTENT_LOADED = "dom_content_loaded"
    LOAD_EVENT_DURATION = "load_event_duration"
    INTERACTION_DURATION = "interaction_duration"
    ACTION_EXECUTION_DURATION = "action_execution_duration"
    STATE_READINESS_DURATION = "state_readiness_duration"
    ROUTE_TRANSITION_DURATION = "route_transition_duration"
    PAGE_READINESS_DURATION = "page_readiness_duration"
    REQUEST_DURATION = "request_duration"
    TOTAL_REQUESTS = "total_requests"
    FAILED_REQUESTS = "failed_requests"
    RESOURCE_COUNT = "resource_count"
    TRANSFER_SIZE = "transfer_size"
    CPU_USAGE = "cpu_usage"
    MEMORY_USAGE = "memory_usage"
    CRASH_COUNT = "crash_count"
    ERROR_COUNT = "error_count"

    @classmethod
    def from_str(cls, value: str) -> PerformanceMetricType:
        """Resolve metric from case-insensitive string or alias."""
        normalized = value.strip().lower()
        if normalized in ("dom_content_loaded", "domcontentloaded", "dom_content_loaded_duration"):
            return cls.DOM_CONTENT_LOADED
        if normalized in ("application_startup_duration", "app_startup_duration", "startup_duration"):
            return cls.APPLICATION_STARTUP_DURATION
        if normalized in ("route_transition_duration", "route_transition", "transition_duration"):
            return cls.ROUTE_TRANSITION_DURATION
        if normalized in ("page_readiness_duration", "readiness_duration", "page_readiness"):
            return cls.PAGE_READINESS_DURATION
        if normalized in ("action_execution_duration", "action_duration", "execution_duration"):
            return cls.ACTION_EXECUTION_DURATION
        if normalized in ("state_readiness_duration", "state_duration", "transition_readiness_duration"):
            return cls.STATE_READINESS_DURATION
        if normalized in ("interaction_duration", "total_interaction_duration", "ux_duration"):
            return cls.INTERACTION_DURATION
        if normalized in ("transfer_size", "transfer_size_bytes", "bytes_transferred", "payload_size"):
            return cls.TRANSFER_SIZE
        if normalized in ("cpu_usage", "cpu", "cpu_percent", "cpu_utilization"):
            return cls.CPU_USAGE
        if normalized in ("memory_usage", "memory", "ram", "memory_bytes", "rss"):
            return cls.MEMORY_USAGE
        if normalized in ("crash_count", "crashes", "crash_events"):
            return cls.CRASH_COUNT
        if normalized in ("error_count", "errors", "runtime_errors"):
            return cls.ERROR_COUNT
        for item in cls:
            if item.value.lower() == normalized or item.name.lower() == normalized:
                return item
        raise ValueError(f"Unknown performance metric: '{value}'")


class PerformanceMetricUnit(str, Enum):
    """Units of measurement for performance metrics."""
    __test__ = False
    MILLISECONDS = "ms"
    SECONDS = "s"
    COUNT = "count"
    BYTES = "bytes"
    PERCENTAGE = "%"


class PerformanceMeasurementStatus(str, Enum):
    """Operational status of a performance measurement."""
    __test__ = False
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"


# ---------------------------------------------------------------------------
# Phase 7.2 Load & Navigation Performance Enums
# ---------------------------------------------------------------------------

class PerformanceInitialState(str, Enum):
    """Clean initial state specification for a load or navigation performance test."""
    __test__ = False
    FRESH_PAGE = "FRESH_PAGE"
    FRESH_CONTEXT = "FRESH_CONTEXT"
    EXISTING_SESSION = "EXISTING_SESSION"


class NavigationPerformanceStatus(str, Enum):
    """Operational outcome status for a load or navigation performance test."""
    __test__ = False
    SUCCESS = "SUCCESS"
    APPLICATION_LOAD_FAILED = "APPLICATION_LOAD_FAILED"
    NAVIGATION_FAILED = "NAVIGATION_FAILED"
    MEASUREMENT_UNAVAILABLE = "MEASUREMENT_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"


# ---------------------------------------------------------------------------
# Phase 7.3 Interaction Performance Enums
# ---------------------------------------------------------------------------

class InteractionPerformanceStatus(str, Enum):
    """Operational outcome status for an interaction responsiveness performance test."""
    __test__ = False
    SUCCESS = "SUCCESS"
    TIMEOUT = "TIMEOUT"
    MISSING_STATE_TRANSITION = "MISSING_STATE_TRANSITION"
    INTERACTION_FAILED = "INTERACTION_FAILED"
    MEASUREMENT_UNAVAILABLE = "MEASUREMENT_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Phase 7.4 Network & Resource Performance Enums
# ---------------------------------------------------------------------------

class ResourceType(str, Enum):
    """Taxonomy of standard resource categories in Tester V1 network performance."""
    __test__ = False
    DOCUMENT = "DOCUMENT"
    SCRIPT = "SCRIPT"
    STYLESHEET = "STYLESHEET"
    IMAGE = "IMAGE"
    FONT = "FONT"
    API = "API"
    MEDIA = "MEDIA"
    OTHER = "OTHER"

    @classmethod
    def from_str(cls, value: str) -> ResourceType:
        normalized = value.strip().upper()
        for item in cls:
            if item.value == normalized or item.name == normalized:
                return item
        if normalized in ("XHR", "FETCH", "JSON", "GRAPHQL"):
            return cls.API
        if normalized in ("CSS",):
            return cls.STYLESHEET
        if normalized in ("JS", "JAVASCRIPT"):
            return cls.SCRIPT
        if normalized in ("HTML", "DOC"):
            return cls.DOCUMENT
        if normalized in ("IMG", "SVG", "PNG", "JPEG", "JPG", "WEBP", "GIF"):
            return cls.IMAGE
        if normalized in ("WOFF", "WOFF2", "TTF", "OTF"):
            return cls.FONT
        if normalized in ("VIDEO", "AUDIO", "MP4", "WEBM"):
            return cls.MEDIA
        return cls.OTHER


class ResourceImportance(str, Enum):
    """Criticality / requirement level of an individual network resource."""
    __test__ = False
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"


class NetworkPerformanceStatus(str, Enum):
    """Operational outcome status for a network and resource performance evaluation."""
    __test__ = False
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    DEGRADED = "DEGRADED"
    EXCESSIVE_REQUESTS = "EXCESSIVE_REQUESTS"
    MEASUREMENT_UNAVAILABLE = "MEASUREMENT_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Phase 7.5 Stability & Runtime Health Enums
# ---------------------------------------------------------------------------

class ProcessState(str, Enum):
    """Operating lifecycle state of a tested application or server process."""
    __test__ = False
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    TERMINATED = "TERMINATED"
    CRASHED = "CRASHED"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_str(cls, value: str) -> ProcessState:
        normalized = value.strip().upper()
        for item in cls:
            if item.value == normalized or item.name == normalized:
                return item
        if normalized in ("DEAD", "KILLED", "EXITED"):
            return cls.TERMINATED
        if normalized in ("CRASH", "FATAL", "OOM"):
            return cls.CRASHED
        if normalized in ("ACTIVE", "LIVE", "ALIVE"):
            return cls.RUNNING
        return cls.UNKNOWN


class StabilityStatus(str, Enum):
    """Aggregate operational stability status of the application under test."""
    __test__ = False
    STABLE = "STABLE"
    DEGRADED = "DEGRADED"
    UNSTABLE = "UNSTABLE"
    CRITICAL_FAILURE = "CRITICAL_FAILURE"
    RECOVERY_EXHAUSTED = "RECOVERY_EXHAUSTED"


class StabilityFailureType(str, Enum):
    """Taxonomy of meaningful stability and runtime health failures."""
    __test__ = False
    APPLICATION_CRASH = "APPLICATION_CRASH"
    SERVER_TERMINATION = "SERVER_TERMINATION"
    BROWSER_CRASH = "BROWSER_CRASH"
    UNREACHABLE = "UNREACHABLE"
    REPEATED_EXCEPTION = "REPEATED_EXCEPTION"
    PERSISTENT_REQUEST_FAILURE = "PERSISTENT_REQUEST_FAILURE"
    SEVERE_DEGRADATION = "SEVERE_DEGRADATION"
    TESTER_INFRASTRUCTURE_FAILURE = "TESTER_INFRASTRUCTURE_FAILURE"




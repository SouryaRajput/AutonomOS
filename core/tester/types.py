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
    SECURITY = "SECURITY"
    PERFORMANCE = "PERFORMANCE"
    UX = "UX"
    SPEC_VIOLATION = "SPEC_VIOLATION"
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
    UNCERTAIN = "UNCERTAIN"
    NOT_EVALUATED = "NOT_EVALUATED"
    NOT_VERIFIED = "NOT_VERIFIED"


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
    UX = "UX"
    PERFORMANCE = "PERFORMANCE"
    STABILITY = "STABILITY"
    TYPOGRAPHY = "TYPOGRAPHY"
    ANIMATION = "ANIMATION"
    RESPONSIVENESS = "RESPONSIVENESS"
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


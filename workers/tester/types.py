from __future__ import annotations

from enum import Enum


class TestCategory(str, Enum):
    """Categories of automated and exploratory testing."""
    UNIT = "UNIT"
    INTEGRATION = "INTEGRATION"
    SYSTEM = "SYSTEM"
    REGRESSION = "REGRESSION"
    END_TO_END = "END_TO_END"
    UI = "UI"
    SECURITY = "SECURITY"
    PERFORMANCE = "PERFORMANCE"
    COMPATIBILITY = "COMPATIBILITY"
    SMOKE = "SMOKE"
    NEGATIVE = "NEGATIVE"
    PERSISTENCE = "PERSISTENCE"


class TestExecutionStatus(str, Enum):
    """Result status of an individual test command or case."""
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"


class RequirementVerificationStatus(str, Enum):
    """Authoritative evaluation of whether a requirement was verified."""
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    NOT_TESTED = "NOT_TESTED"
    BLOCKED = "BLOCKED"
    INCONCLUSIVE = "INCONCLUSIVE"


class DefectSeverity(str, Enum):
    """Impact severity of a diagnosed defect."""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFORMATIONAL = "INFORMATIONAL"


class FailureClassification(str, Enum):
    """Diagnostic classification of why a test or check failed."""
    IMPLEMENTATION_BUG = "IMPLEMENTATION_BUG"
    REGRESSION = "REGRESSION"
    TEST_BUG = "TEST_BUG"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    CONFIGURATION_FAILURE = "CONFIGURATION_FAILURE"
    FLAKY_TEST = "FLAKY_TEST"
    UNKNOWN = "UNKNOWN"


class RootCauseConfidence(str, Enum):
    """Confidence rating in the suspected failure root cause."""
    CONFIRMED = "CONFIRMED"
    LIKELY = "LIKELY"
    POSSIBLE = "POSSIBLE"
    UNKNOWN = "UNKNOWN"


class TesterFinalStatus(str, Enum):
    """Final high-level status of the Tester's evaluation."""
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    BLOCKED = "BLOCKED"
    INCONCLUSIVE = "INCONCLUSIVE"

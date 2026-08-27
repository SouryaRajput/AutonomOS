from enum import Enum


class VerificationStatus(str, Enum):
    """Authoritative outcome status of a verification process."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    BLOCKED = "BLOCKED"


class CheckStatus(str, Enum):
    """Outcome status of an individual verification check."""
    NOT_RUN = "NOT_RUN"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    UNCERTAIN = "UNCERTAIN"
    ERROR = "ERROR"


class CheckType(str, Enum):
    """Category and type of verification check."""
    # File Checks
    FILE_EXISTS = "FILE_EXISTS"
    FILE_NOT_EXISTS = "FILE_NOT_EXISTS"
    FILE_HASH = "FILE_HASH"
    FILE_CONTENT_MATCH = "FILE_CONTENT_MATCH"

    # Repository & Scope Checks
    GIT_DIFF = "GIT_DIFF"
    GIT_STATUS = "GIT_STATUS"
    EXPECTED_FILES_CHANGED = "EXPECTED_FILES_CHANGED"
    UNEXPECTED_FILES_CHANGED = "UNEXPECTED_FILES_CHANGED"

    # Build & Compiler Checks
    BUILD = "BUILD"
    COMPILE = "COMPILE"

    # Test Execution Checks
    TEST_SUITE = "TEST_SUITE"
    TEST_CASE = "TEST_CASE"

    # Static Analysis Checks
    LINT = "LINT"
    TYPE_CHECK = "TYPE_CHECK"

    # Command Execution Checks
    COMMAND_EXIT_CODE = "COMMAND_EXIT_CODE"
    COMMAND_OUTPUT_MATCH = "COMMAND_OUTPUT_MATCH"

    # Artifact Checks
    ARTIFACT_EXISTS = "ARTIFACT_EXISTS"
    ARTIFACT_HASH = "ARTIFACT_HASH"

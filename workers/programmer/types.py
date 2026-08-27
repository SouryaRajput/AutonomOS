from __future__ import annotations

from enum import Enum


class ProgrammingMode(str, Enum):
    """Execution mode tailoring planning depth and verification strictness."""
    FEATURE = "FEATURE"
    BUGFIX = "BUGFIX"
    REFACTOR = "REFACTOR"
    PATCH = "PATCH"


class FileChangeType(str, Enum):
    """Categorization of individual filesystem operations."""
    CREATE = "CREATE"
    MODIFY = "MODIFY"
    DELETE = "DELETE"
    RENAME = "RENAME"


class TestFailureType(str, Enum):
    """Deterministic classification of test and check failures."""
    IMPLEMENTATION_FAILURE = "IMPLEMENTATION_FAILURE"
    TEST_FAILURE = "TEST_FAILURE"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
    PREEXISTING_FAILURE = "PREEXISTING_FAILURE"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    UNKNOWN = "UNKNOWN"


class ProgrammingStatus(str, Enum):
    """Final operational status reported by the Programmer."""
    SUCCESS = "SUCCESS"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"

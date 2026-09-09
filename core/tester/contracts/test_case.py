from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from core.tester.contracts.identifiers import (
    new_test_case_id,
    validate_test_case_id,
)
from core.tester.errors import TesterValidationError
from core.tester.types import TestCaseStatus, TestCategory


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TestCaseResult:
    """
    Structured evaluation record for an individual test case evaluated by Tester.
    Represents discrete observations, expectations, and evidence gathered.
    Does not introduce unnecessary framework abstractions.
    """
    __test__ = False
    test_id: str
    name: str
    category: TestCategory = TestCategory.FUNCTIONAL
    status: TestCaseStatus = TestCaseStatus.NOT_RUN
    description: str = ""
    expected_behavior: str = ""
    observed_behavior: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    trace: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_test_case_id(self.test_id)
        if not self.name or not self.name.strip():
            raise TesterValidationError("TestCaseResult must have a non-empty name.", field_name="name")

        # Normalize category
        if isinstance(self.category, str):
            try:
                self.category = TestCategory(self.category.upper())
            except (ValueError, KeyError):
                self.category = TestCategory.FUNCTIONAL

        # Normalize status
        if isinstance(self.status, str):
            try:
                self.status = TestCaseStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = TestCaseStatus.NOT_RUN

        self.evidence_ids = list(self.evidence_ids)
        self.trace = dict(self.trace)

    @property
    def is_pass(self) -> bool:
        return self.status == TestCaseStatus.PASS

    @property
    def is_fail(self) -> bool:
        return self.status == TestCaseStatus.FAIL

    @property
    def is_blocked(self) -> bool:
        return self.status == TestCaseStatus.BLOCKED

    @property
    def is_not_run(self) -> bool:
        return self.status == TestCaseStatus.NOT_RUN

    @property
    def is_not_verified(self) -> bool:
        return self.status == TestCaseStatus.NOT_VERIFIED

    def validate(self) -> None:
        """Validate internal consistency of the test case result."""
        validate_test_case_id(self.test_id)
        if not self.name or not self.name.strip():
            raise TesterValidationError("TestCaseResult must have a non-empty name.", field_name="name")
        if not isinstance(self.status, TestCaseStatus):
            raise TesterValidationError(f"Invalid test case status: {self.status}", field_name="status")

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "name": self.name,
            "category": self.category.value if hasattr(self.category, "value") else str(self.category),
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "description": self.description,
            "expected_behavior": self.expected_behavior,
            "observed_behavior": self.observed_behavior,
            "evidence_ids": list(self.evidence_ids),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "trace": dict(self.trace),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestCaseResult:
        cat_raw = data.get("category", TestCategory.FUNCTIONAL.value)
        try:
            category = TestCategory(str(cat_raw).upper())
        except (ValueError, KeyError):
            category = TestCategory.FUNCTIONAL

        st_raw = data.get("status", TestCaseStatus.NOT_RUN.value)
        try:
            status = TestCaseStatus(str(st_raw).upper())
        except (ValueError, KeyError):
            status = TestCaseStatus.NOT_RUN

        return cls(
            test_id=str(data.get("test_id", new_test_case_id())),
            name=str(data.get("name", "")),
            category=category,
            status=status,
            description=str(data.get("description", "")),
            expected_behavior=str(data.get("expected_behavior", "")),
            observed_behavior=str(data.get("observed_behavior", "")),
            evidence_ids=list(data.get("evidence_ids", [])),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            trace=dict(data.get("trace", {})),
        )

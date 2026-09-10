from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from core.tester.contracts.identifiers import (
    new_test_case_id,
    validate_execution_id,
    validate_test_case_id,
)
from core.tester.errors import TesterValidationError
from core.tester.types import TestCaseStatus, TestCategory


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TestStepResult:
    """
    Structured outcome of an individual TestStep execution within a TestCase.
    Captures step identity, execution status, action performed, actual vs expected,
    associated evidence and observation IDs, duration, and failure information.
    """
    __test__ = False
    step_number: int
    description: str
    status: TestCaseStatus = TestCaseStatus.NOT_RUN
    action: Optional[str] = None
    target: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    duration_ms: float = 0.0
    error: Optional[str] = None
    evidence_ids: list[str] = field(default_factory=list)
    observation_ids: list[str] = field(default_factory=list)
    step_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.step_number < 1:
            raise TesterValidationError("step_number must be a positive integer >= 1.", field_name="step_number")
        if not self.description or not str(self.description).strip():
            raise TesterValidationError("TestStepResult must have a non-empty description.", field_name="description")

        if isinstance(self.status, str):
            try:
                self.status = TestCaseStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = TestCaseStatus.NOT_RUN

        self.evidence_ids = list(self.evidence_ids)
        self.observation_ids = list(self.observation_ids)
        self.metadata = dict(self.metadata)

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_number": self.step_number,
            "description": self.description,
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "action": self.action,
            "target": self.target,
            "expected": self.expected,
            "actual": self.actual,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "evidence_ids": list(self.evidence_ids),
            "observation_ids": list(self.observation_ids),
            "step_id": self.step_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestStepResult:
        st_raw = data.get("status", TestCaseStatus.NOT_RUN.value)
        try:
            status = TestCaseStatus(str(st_raw).upper())
        except (ValueError, KeyError):
            status = TestCaseStatus.NOT_RUN

        return cls(
            step_number=int(data.get("step_number", 1)),
            description=str(data.get("description", "")),
            status=status,
            action=data.get("action"),
            target=data.get("target"),
            expected=data.get("expected"),
            actual=data.get("actual"),
            duration_ms=float(data.get("duration_ms", 0.0)),
            error=data.get("error"),
            evidence_ids=list(data.get("evidence_ids", [])),
            observation_ids=list(data.get("observation_ids", [])),
            step_id=data.get("step_id"),
            metadata=dict(data.get("metadata", {})),
        )


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
    execution_id: Optional[str] = None
    step_results: list[TestStepResult] = field(default_factory=list)
    observations: list[Any] = field(default_factory=list)
    failure_information: Optional[dict[str, Any]] = None
    duration_ms: float = 0.0

    def __post_init__(self) -> None:
        validate_test_case_id(self.test_id)
        if not self.name or not self.name.strip():
            raise TesterValidationError("TestCaseResult must have a non-empty name.", field_name="name")

        if self.execution_id is not None:
            validate_execution_id(self.execution_id)

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
        self.step_results = list(self.step_results)
        self.observations = list(self.observations)
        if self.failure_information is not None:
            self.failure_information = dict(self.failure_information)

    @property
    def test_case_id(self) -> str:
        """Alias for test_id per Phase 5 TestCase representation."""
        return self.test_id

    @property
    def duration(self) -> float:
        """Alias for duration_ms."""
        return self.duration_ms

    @property
    def evidence(self) -> list[str]:
        """Alias for evidence_ids."""
        return self.evidence_ids

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
            "test_case_id": self.test_case_id,
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
            "execution_id": self.execution_id,
            "step_results": [sr.to_dict() if hasattr(sr, "to_dict") else sr for sr in self.step_results],
            "observations": [
                obs.to_dict() if hasattr(obs, "to_dict") else obs
                for obs in self.observations
            ],
            "failure_information": dict(self.failure_information) if self.failure_information is not None else None,
            "duration_ms": self.duration_ms,
            "duration": self.duration,
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

        tid = str(data.get("test_id") or data.get("test_case_id") or new_test_case_id())

        ev_ids = data.get("evidence_ids")
        if ev_ids is None and "evidence" in data:
            ev_ids = data["evidence"]
        if ev_ids is None:
            ev_ids = []

        dur = data.get("duration_ms")
        if dur is None and "duration" in data:
            dur = data["duration"]
        if dur is None:
            dur = 0.0

        step_res = [
            TestStepResult.from_dict(sr) if isinstance(sr, dict) else sr
            for sr in data.get("step_results", [])
        ]

        return cls(
            test_id=tid,
            name=str(data.get("name", "")),
            category=category,
            status=status,
            description=str(data.get("description", "")),
            expected_behavior=str(data.get("expected_behavior", "")),
            observed_behavior=str(data.get("observed_behavior", "")),
            evidence_ids=list(ev_ids),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            trace=dict(data.get("trace", {})),
            execution_id=data.get("execution_id"),
            step_results=step_res,
            observations=list(data.get("observations", [])),
            failure_information=data.get("failure_information"),
            duration_ms=float(dur),
        )

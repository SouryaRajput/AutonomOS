from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TestResultRecord:
    """
    Detailed audit record of a test suite or verification check execution.
    Separates concrete test tallies from high-level textual claims.
    """
    __test__ = False

    command: str

    passed: bool
    exit_code: int = 0
    duration_ms: float = 0.0
    tests_passed: int = 0
    tests_failed: int = 0
    tests_skipped: int = 0
    output_ref: Optional[str] = None
    failure_summary: Optional[str] = None
    timestamp: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tests(self) -> int:
        return self.tests_passed + self.tests_failed + self.tests_skipped

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "passed": self.passed,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "tests_skipped": self.tests_skipped,
            "output_ref": self.output_ref,
            "failure_summary": self.failure_summary,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestResultRecord:
        return cls(
            command=str(data.get("command", "")),
            passed=bool(data.get("passed", False)),
            exit_code=int(data.get("exit_code") or 0),
            duration_ms=float(data.get("duration_ms") or 0.0),
            tests_passed=int(data.get("tests_passed") or 0),
            tests_failed=int(data.get("tests_failed") or 0),
            tests_skipped=int(data.get("tests_skipped") or 0),
            output_ref=data.get("output_ref"),
            failure_summary=data.get("failure_summary"),
            timestamp=str(data.get("timestamp", utc_now())),
            metadata=dict(data.get("metadata", {})),
        )

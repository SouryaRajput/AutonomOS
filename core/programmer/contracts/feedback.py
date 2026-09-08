from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Optional
import uuid

from core.programmer.contracts.identifiers import (
    FEEDBACK_ID_PREFIX,
    new_feedback_id,
    validate_feedback_id,
    validate_work_order_id,
)
from core.programmer.errors import ProgrammerValidationError
from core.programmer.types import (
    FeedbackConfidence,
    FeedbackIssueType,
    FeedbackSeverity,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EngineeringFeedback:
    """
    Structured defect, regression, or design feedback from an external worker (e.g. Tester, Designer).
    Provides empirical reproduction details and evidence without directly commanding Programmer.
    Manager remains the decision-maker regarding whether feedback is accepted as work.
    """
    feedback_id: str
    source_worker: str
    source_task: str
    target_project: str
    related_work_order: str
    issue: str
    observed_behavior: str
    expected_behavior: str
    issue_type: FeedbackIssueType = FeedbackIssueType.BUG
    severity: FeedbackSeverity = FeedbackSeverity.MEDIUM
    reproduction_information: dict[str, Any] | str = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    suggested_direction: Optional[str] = None
    confidence: FeedbackConfidence = FeedbackConfidence.REPRODUCED
    trace: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self):
        # Normalize enums
        if isinstance(self.issue_type, str):
            try:
                self.issue_type = FeedbackIssueType(self.issue_type.upper())
            except (ValueError, KeyError):
                self.issue_type = FeedbackIssueType.OTHER

        if isinstance(self.severity, str):
            try:
                self.severity = FeedbackSeverity(self.severity.upper())
            except (ValueError, KeyError):
                self.severity = FeedbackSeverity.MEDIUM

        if isinstance(self.confidence, str):
            try:
                self.confidence = FeedbackConfidence(self.confidence.upper())
            except (ValueError, KeyError):
                self.confidence = FeedbackConfidence.REPRODUCED

        # Defensive copies of collections
        if isinstance(self.reproduction_information, dict):
            self.reproduction_information = dict(self.reproduction_information)
        self.evidence = [dict(e) if isinstance(e, dict) else {"data": e} for e in (self.evidence or [])]
        self.trace = dict(self.trace or {})

        # Validation
        self.validate()

    def validate(self) -> None:
        """Enforces ID syntax, non-empty fields, and work order lineage."""
        validate_feedback_id(self.feedback_id)

        if not self.source_worker or not str(self.source_worker).strip():
            raise ProgrammerValidationError("source_worker cannot be empty", field_name="source_worker")

        if not self.source_task or not str(self.source_task).strip():
            raise ProgrammerValidationError("source_task cannot be empty", field_name="source_task")

        if not self.target_project or not str(self.target_project).strip():
            raise ProgrammerValidationError("target_project cannot be empty", field_name="target_project")

        if not self.related_work_order or not str(self.related_work_order).strip():
            raise ProgrammerValidationError("related_work_order cannot be empty", field_name="related_work_order")
        validate_work_order_id(self.related_work_order)

        if not self.issue or not str(self.issue).strip():
            raise ProgrammerValidationError("issue cannot be empty", field_name="issue")

        if not self.observed_behavior or not str(self.observed_behavior).strip():
            raise ProgrammerValidationError("observed_behavior cannot be empty", field_name="observed_behavior")

        if not self.expected_behavior or not str(self.expected_behavior).strip():
            raise ProgrammerValidationError("expected_behavior cannot be empty", field_name="expected_behavior")

    def to_dict(self) -> dict[str, Any]:
        return {
            "feedback_id": self.feedback_id,
            "source_worker": self.source_worker,
            "source_task": self.source_task,
            "target_project": self.target_project,
            "related_work_order": self.related_work_order,
            "issue": self.issue,
            "issue_type": self.issue_type.value if hasattr(self.issue_type, "value") else str(self.issue_type),
            "severity": self.severity.value if hasattr(self.severity, "value") else str(self.severity),
            "observed_behavior": self.observed_behavior,
            "expected_behavior": self.expected_behavior,
            "reproduction_information": dict(self.reproduction_information) if isinstance(self.reproduction_information, dict) else self.reproduction_information,
            "evidence": [dict(e) for e in self.evidence],
            "suggested_direction": self.suggested_direction,
            "confidence": self.confidence.value if hasattr(self.confidence, "value") else str(self.confidence),
            "trace": dict(self.trace),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EngineeringFeedback:
        it_raw = data.get("issue_type", FeedbackIssueType.BUG.value)
        try:
            issue_type = FeedbackIssueType(it_raw)
        except (ValueError, KeyError):
            issue_type = FeedbackIssueType.BUG

        sev_raw = data.get("severity", FeedbackSeverity.MEDIUM.value)
        try:
            severity = FeedbackSeverity(sev_raw)
        except (ValueError, KeyError):
            severity = FeedbackSeverity.MEDIUM

        conf_raw = data.get("confidence", FeedbackConfidence.REPRODUCED.value)
        try:
            confidence = FeedbackConfidence(conf_raw)
        except (ValueError, KeyError):
            confidence = FeedbackConfidence.REPRODUCED

        return cls(
            feedback_id=str(data.get("feedback_id", "")),
            source_worker=str(data.get("source_worker", "")),
            source_task=str(data.get("source_task", "")),
            target_project=str(data.get("target_project", "")),
            related_work_order=str(data.get("related_work_order", "")),
            issue=str(data.get("issue", "")),
            observed_behavior=str(data.get("observed_behavior", "")),
            expected_behavior=str(data.get("expected_behavior", "")),
            issue_type=issue_type,
            severity=severity,
            reproduction_information=data.get("reproduction_information", {}),
            evidence=list(data.get("evidence", [])),
            suggested_direction=data.get("suggested_direction"),
            confidence=confidence,
            trace=dict(data.get("trace", {})),
            created_at=str(data.get("created_at", utc_now())),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> EngineeringFeedback:
        return cls.from_dict(json.loads(json_str))

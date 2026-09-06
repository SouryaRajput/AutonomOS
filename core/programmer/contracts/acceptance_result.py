from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from core.programmer.types import AcceptanceStatus


@dataclass
class AcceptanceCriterionResult:
    """
    Evaluation record linking an individual AcceptanceCriterion to actual supporting evidence.
    Distinguishes verified outcomes (PASS, FAIL) from unverified outcomes (NOT_VERIFIED).
    """
    criterion_id: str
    description: str
    status: AcceptanceStatus = AcceptanceStatus.NOT_VERIFIED
    evidence_ids: list[str] = field(default_factory=list)
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = AcceptanceStatus(self.status)
            except ValueError:
                self.status = AcceptanceStatus.NOT_VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "description": self.description,
            "status": self.status.value if isinstance(self.status, AcceptanceStatus) else str(self.status),
            "evidence_ids": list(self.evidence_ids),
            "message": self.message,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AcceptanceCriterionResult:
        st_raw = data.get("status", AcceptanceStatus.NOT_VERIFIED.value)
        try:
            status = AcceptanceStatus(st_raw)
        except (ValueError, TypeError):
            status = AcceptanceStatus.NOT_VERIFIED

        return cls(
            criterion_id=str(data.get("criterion_id", "")),
            description=str(data.get("description", "")),
            status=status,
            evidence_ids=list(data.get("evidence_ids", [])),
            message=str(data.get("message", "")),
            metadata=dict(data.get("metadata", {})),
        )

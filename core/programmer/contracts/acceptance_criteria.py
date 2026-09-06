from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import uuid

from core.programmer.types import AcceptanceCriterionType


@dataclass
class AcceptanceCriterion:
    """
    Structured machine-evaluable criterion specifying how the Manager verifies
    that the Programmer's implementation succeeded.
    """
    criterion_id: str
    description: str
    criterion_type: AcceptanceCriterionType = AcceptanceCriterionType.CUSTOM
    target: Optional[str] = None
    is_mandatory: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "description": self.description,
            "criterion_type": self.criterion_type.value if isinstance(self.criterion_type, AcceptanceCriterionType) else str(self.criterion_type),
            "target": self.target,
            "is_mandatory": self.is_mandatory,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AcceptanceCriterion:
        ctype_raw = data.get("criterion_type", AcceptanceCriterionType.CUSTOM.value)
        try:
            ctype = AcceptanceCriterionType(ctype_raw)
        except (ValueError, TypeError):
            ctype = AcceptanceCriterionType.CUSTOM

        return cls(
            criterion_id=data.get("criterion_id", f"ac-{uuid.uuid4().hex[:6]}"),
            description=str(data.get("description", "")),
            criterion_type=ctype,
            target=data.get("target"),
            is_mandatory=bool(data.get("is_mandatory", True)),
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def from_str(cls, text: str, criterion_type: AcceptanceCriterionType = AcceptanceCriterionType.CUSTOM) -> AcceptanceCriterion:
        """Helper to create an AcceptanceCriterion from a plain text description."""
        return cls(
            criterion_id=f"ac-{uuid.uuid4().hex[:6]}",
            description=str(text).strip(),
            criterion_type=criterion_type,
        )

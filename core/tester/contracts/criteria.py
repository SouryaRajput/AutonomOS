from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import uuid

from core.tester.errors import TesterValidationError
from core.tester.types import TestCategory


@dataclass
class AcceptanceCriterion:
    """
    Evaluatable criterion defining what Tester is required to evaluate.
    Possesses a stable identity and explicit descriptive verification target.
    Does NOT evaluate anything itself.
    """
    __test__ = False
    criterion_id: str
    description: str
    is_mandatory: bool = True
    target: Optional[str] = None
    category: Optional[TestCategory] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.category, str):
            try:
                self.category = TestCategory(self.category.upper())
            except (ValueError, KeyError):
                self.category = None

    def validate(self) -> None:
        if not self.criterion_id or not str(self.criterion_id).strip():
            raise TesterValidationError(
                "AcceptanceCriterion must have a non-empty criterion_id.",
                field_name="acceptance_criteria.criterion_id",
            )
        if not self.description or not str(self.description).strip():
            raise TesterValidationError(
                "AcceptanceCriterion must have a non-empty description.",
                field_name="acceptance_criteria.description",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "description": self.description,
            "is_mandatory": self.is_mandatory,
            "target": self.target,
            "category": self.category.value if hasattr(self.category, "value") else (str(self.category) if self.category else None),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AcceptanceCriterion:
        cat = None
        if data.get("category"):
            try:
                cat = TestCategory(str(data["category"]).upper())
            except (ValueError, KeyError):
                cat = None

        return cls(
            criterion_id=str(data.get("criterion_id", f"ac-{uuid.uuid4().hex[:6]}")),
            description=str(data.get("description", "")),
            is_mandatory=bool(data.get("is_mandatory", True)),
            target=data.get("target"),
            category=cat,
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def from_str(cls, text: str, category: Optional[TestCategory] = None) -> AcceptanceCriterion:
        """Construct an AcceptanceCriterion from a plain text description."""
        cid = f"ac-{uuid.uuid4().hex[:6]}"
        return cls(
            criterion_id=cid,
            description=str(text).strip(),
            category=category,
        )

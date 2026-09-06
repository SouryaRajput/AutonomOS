from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from core.programmer.contracts.identifiers import (
    new_blocker_id,
    validate_blocker_id,
    validate_work_order_id,
)
from core.programmer.errors import ProgrammerValidationError
from core.programmer.types import (
    ProgrammerBlockerCategory,
    ProgrammerBlockerSeverity,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProgrammerBlocker:
    """
    Structured impediment preventing Programmer from proceeding safely.
    Distinguished strictly from execution failure:
    - BLOCKED: Programmer requires a decision, authorization, or missing context.
    - FAILED: Programmer attempted the work but execution failed.
    """
    blocker_id: str
    work_order_id: str
    category: ProgrammerBlockerCategory
    description: str
    severity: ProgrammerBlockerSeverity = ProgrammerBlockerSeverity.HIGH
    required_decision: str = ""
    required_context: Optional[str] = None
    execution_id: Optional[str] = None
    task_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    resolved_at: Optional[str] = None
    resolution_notes: Optional[str] = None
    resolved_by: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_blocker_id(self.blocker_id)
        validate_work_order_id(self.work_order_id)
        if not self.description or not self.description.strip():
            raise ProgrammerValidationError(
                "Blocker description cannot be empty.",
                field_name="description",
            )
        if isinstance(self.category, str):
            try:
                self.category = ProgrammerBlockerCategory(self.category.upper())
            except (ValueError, TypeError):
                raise ProgrammerValidationError(
                    f"Invalid blocker category '{self.category}'.",
                    field_name="category",
                )
        if isinstance(self.severity, str):
            try:
                self.severity = ProgrammerBlockerSeverity(self.severity.upper())
            except (ValueError, TypeError):
                raise ProgrammerValidationError(
                    f"Invalid blocker severity '{self.severity}'.",
                    field_name="severity",
                )

    @property
    def is_resolved(self) -> bool:
        """Check if this blocker has been formally resolved."""
        return self.resolved_at is not None

    @property
    def is_material(self) -> bool:
        """Check whether this blocker is material (HIGH or CRITICAL severity, or requires decision)."""
        sev = self.severity if isinstance(self.severity, ProgrammerBlockerSeverity) else ProgrammerBlockerSeverity(str(self.severity).upper())
        return sev in (ProgrammerBlockerSeverity.HIGH, ProgrammerBlockerSeverity.CRITICAL) or bool(self.required_decision)

    def resolve(self, resolution_notes: str, resolved_by: str = "manager") -> None:
        """Resolve this blocker with resolution provenance."""
        if not resolution_notes or not resolution_notes.strip():
            raise ProgrammerValidationError(
                "Resolution notes cannot be empty.",
                field_name="resolution_notes",
            )
        self.resolved_at = utc_now()
        self.resolution_notes = resolution_notes
        self.resolved_by = resolved_by

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocker_id": self.blocker_id,
            "work_order_id": self.work_order_id,
            "category": self.category.value if isinstance(self.category, ProgrammerBlockerCategory) else str(self.category),
            "description": self.description,
            "severity": self.severity.value if isinstance(self.severity, ProgrammerBlockerSeverity) else str(self.severity),
            "required_decision": self.required_decision,
            "required_context": self.required_context,
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolution_notes": self.resolution_notes,
            "resolved_by": self.resolved_by,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammerBlocker:
        cat_raw = data.get("category", ProgrammerBlockerCategory.OTHER.value)
        try:
            category = ProgrammerBlockerCategory(str(cat_raw).upper())
        except (ValueError, TypeError):
            category = ProgrammerBlockerCategory.OTHER

        sev_raw = data.get("severity", ProgrammerBlockerSeverity.HIGH.value)
        try:
            severity = ProgrammerBlockerSeverity(str(sev_raw).upper())
        except (ValueError, TypeError):
            severity = ProgrammerBlockerSeverity.HIGH

        return cls(
            blocker_id=str(data.get("blocker_id", new_blocker_id())),
            work_order_id=str(data.get("work_order_id", "")),
            category=category,
            description=str(data.get("description", "")),
            severity=severity,
            required_decision=str(data.get("required_decision", "")),
            required_context=data.get("required_context"),
            execution_id=data.get("execution_id"),
            task_id=data.get("task_id"),
            created_at=str(data.get("created_at", utc_now())),
            resolved_at=data.get("resolved_at"),
            resolution_notes=data.get("resolution_notes"),
            resolved_by=data.get("resolved_by"),
            metadata=dict(data.get("metadata", {})),
        )

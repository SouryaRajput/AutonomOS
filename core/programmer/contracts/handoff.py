from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Optional
import uuid

from core.programmer.contracts.identifiers import (
    HANDOFF_ID_PREFIX,
    new_handoff_id,
    validate_handoff_id,
    validate_work_order_id,
)
from core.programmer.errors import ProgrammerValidationError
from core.programmer.types import EngineeringHandoffType, HandoffPriority


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EngineeringHandoff:
    """
    Structured, validated contract for exchanging engineering work product between AutonomOS workers.
    Preserves lineage across tasks, work orders, projects, and traces without assuming direct
    organizational authority over peer workers (Manager remains orchestrator).
    """
    handoff_id: str
    project_id: str
    source_worker_id: str
    target_worker_id: str
    source_task_id: str
    objective: str
    requested_action: str
    handoff_type: EngineeringHandoffType = EngineeringHandoffType.RESEARCH_TO_PROGRAMMER
    target_task_id: Optional[str] = None
    work_order_id: Optional[str] = None
    context: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    risks: list[dict[str, Any]] = field(default_factory=list)
    known_unknowns: list[str] = field(default_factory=list)
    priority: HandoffPriority = HandoffPriority.NORMAL
    created_at: str = field(default_factory=utc_now)
    trace: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Normalize enums
        if isinstance(self.handoff_type, str):
            try:
                self.handoff_type = EngineeringHandoffType(self.handoff_type)
            except (ValueError, KeyError):
                raise ProgrammerValidationError(
                    f"Invalid handoff_type: '{self.handoff_type}'",
                    field_name="handoff_type",
                )
        if isinstance(self.priority, str):
            try:
                self.priority = HandoffPriority(self.priority)
            except (ValueError, KeyError):
                self.priority = HandoffPriority.NORMAL

        # Defensive copies of collections
        self.context = dict(self.context or {})
        self.artifacts = [str(a) for a in (self.artifacts or [])]
        self.requirements = [str(r) for r in (self.requirements or [])]
        self.constraints = [str(c) for c in (self.constraints or [])]
        self.acceptance_criteria = [str(ac) for ac in (self.acceptance_criteria or [])]
        self.evidence = [dict(e) if isinstance(e, dict) else {"data": e} for e in (self.evidence or [])]
        self.risks = [dict(r) if isinstance(r, dict) else {"risk": r} for r in (self.risks or [])]
        self.known_unknowns = [str(k) for k in (self.known_unknowns or [])]
        self.trace = dict(self.trace or {})

        # Run strict validation
        self.validate()

    def validate(self) -> None:
        """Enforces schema boundaries, ID formats, and organizational role constraints."""
        validate_handoff_id(self.handoff_id)

        if not self.project_id or not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ProgrammerValidationError("project_id cannot be empty", field_name="project_id")

        if not self.source_worker_id or not isinstance(self.source_worker_id, str) or not self.source_worker_id.strip():
            raise ProgrammerValidationError("source_worker_id cannot be empty", field_name="source_worker_id")

        if not self.target_worker_id or not isinstance(self.target_worker_id, str) or not self.target_worker_id.strip():
            raise ProgrammerValidationError("target_worker_id cannot be empty", field_name="target_worker_id")

        if self.source_worker_id.strip() == self.target_worker_id.strip():
            raise ProgrammerValidationError(
                f"Source worker and target worker must be distinct, got '{self.source_worker_id}' for both.",
                field_name="target_worker_id",
            )

        if not self.source_task_id or not isinstance(self.source_task_id, str) or not self.source_task_id.strip():
            raise ProgrammerValidationError("source_task_id cannot be empty", field_name="source_task_id")

        if not self.objective or not isinstance(self.objective, str) or not self.objective.strip():
            raise ProgrammerValidationError("objective cannot be empty", field_name="objective")

        if not self.requested_action or not isinstance(self.requested_action, str) or not self.requested_action.strip():
            raise ProgrammerValidationError("requested_action cannot be empty", field_name="requested_action")

        if self.work_order_id:
            validate_work_order_id(self.work_order_id)

    def to_dict(self) -> dict[str, Any]:
        """Convert EngineeringHandoff to dictionary representation."""
        return {
            "handoff_id": self.handoff_id,
            "project_id": self.project_id,
            "source_worker_id": self.source_worker_id,
            "target_worker_id": self.target_worker_id,
            "source_task_id": self.source_task_id,
            "target_task_id": self.target_task_id,
            "work_order_id": self.work_order_id,
            "handoff_type": self.handoff_type.value,
            "objective": self.objective,
            "context": dict(self.context),
            "artifacts": list(self.artifacts),
            "requirements": list(self.requirements),
            "constraints": list(self.constraints),
            "acceptance_criteria": list(self.acceptance_criteria),
            "evidence": [dict(e) for e in self.evidence],
            "risks": [dict(r) for r in self.risks],
            "known_unknowns": list(self.known_unknowns),
            "requested_action": self.requested_action,
            "priority": self.priority.value,
            "created_at": self.created_at,
            "trace": dict(self.trace),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EngineeringHandoff:
        """Construct EngineeringHandoff from dictionary representation."""
        ht_raw = data.get("handoff_type", EngineeringHandoffType.RESEARCH_TO_PROGRAMMER.value)
        try:
            handoff_type = EngineeringHandoffType(ht_raw)
        except (ValueError, KeyError):
            handoff_type = EngineeringHandoffType.RESEARCH_TO_PROGRAMMER

        p_raw = data.get("priority", HandoffPriority.NORMAL.value)
        try:
            priority = HandoffPriority(p_raw)
        except (ValueError, KeyError):
            priority = HandoffPriority.NORMAL

        return cls(
            handoff_id=str(data.get("handoff_id", "")),
            project_id=str(data.get("project_id", "")),
            source_worker_id=str(data.get("source_worker_id", "")),
            target_worker_id=str(data.get("target_worker_id", "")),
            source_task_id=str(data.get("source_task_id", "")),
            target_task_id=data.get("target_task_id"),
            work_order_id=data.get("work_order_id"),
            handoff_type=handoff_type,
            objective=str(data.get("objective", "")),
            context=dict(data.get("context", {})),
            artifacts=list(data.get("artifacts", [])),
            requirements=list(data.get("requirements", [])),
            constraints=list(data.get("constraints", [])),
            acceptance_criteria=list(data.get("acceptance_criteria", [])),
            evidence=list(data.get("evidence", [])),
            risks=list(data.get("risks", [])),
            known_unknowns=list(data.get("known_unknowns", [])),
            requested_action=str(data.get("requested_action", "")),
            priority=priority,
            created_at=str(data.get("created_at", utc_now())),
            trace=dict(data.get("trace", {})),
        )

    def to_json(self) -> str:
        """Serialize EngineeringHandoff to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> EngineeringHandoff:
        """Deserialize EngineeringHandoff from JSON string."""
        return cls.from_dict(json.loads(json_str))

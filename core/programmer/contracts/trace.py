from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Optional
import uuid

from core.models import Evidence as RuntimeEvidence
from core.programmer.contracts.identifiers import (
    new_trace_id,
    validate_execution_id,
    validate_trace_id,
    validate_work_order_id,
)
from core.programmer.errors import ProgrammerLineageError
from core.programmer.types import ProgrammerActionType


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def compute_sha256(content: str | bytes) -> str:
    """Compute SHA-256 hex digest for cryptographic integrity."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


@dataclass
class ProgrammerTrace:
    """
    Auditable record of an individual discrete action performed during a Programmer execution attempt.
    Maintains cryptographic proof and unforgeable causal lineage back to execution and work order.
    """
    trace_id: str
    execution_id: str
    work_order_id: str
    task_id: str
    project_id: str
    correlation_id: str
    action_type: ProgrammerActionType
    action_details: dict[str, Any] = field(default_factory=dict)
    checksum: str = ""
    timestamp: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_trace_id(self.trace_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        if not self.task_id:
            raise ProgrammerLineageError("ProgrammerTrace must have a valid non-empty task_id.")
        if not self.project_id:
            raise ProgrammerLineageError("ProgrammerTrace must have a valid non-empty project_id.")
        if not self.correlation_id:
            raise ProgrammerLineageError("ProgrammerTrace must have a valid non-empty correlation_id.")
        if not self.checksum:
            serialized_payload = json.dumps(self.action_details, sort_keys=True)
            self.checksum = compute_sha256(f"{self.action_type.value}|{serialized_payload}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "action_type": self.action_type.value if isinstance(self.action_type, ProgrammerActionType) else str(self.action_type),
            "action_details": dict(self.action_details),
            "checksum": self.checksum,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammerTrace:
        atype_raw = data.get("action_type", ProgrammerActionType.INITIALIZE.value)
        try:
            action_type = ProgrammerActionType(atype_raw)
        except (ValueError, TypeError):
            action_type = ProgrammerActionType.INITIALIZE

        return cls(
            trace_id=data.get("trace_id", new_trace_id()),
            execution_id=data.get("execution_id", ""),
            work_order_id=data.get("work_order_id", ""),
            task_id=data.get("task_id", ""),
            project_id=data.get("project_id", ""),
            correlation_id=data.get("correlation_id", ""),
            action_type=action_type,
            action_details=dict(data.get("action_details", {})),
            checksum=data.get("checksum", ""),
            timestamp=data.get("timestamp", utc_now()),
        )

    def to_runtime_evidence(self) -> RuntimeEvidence:
        """Convert trace to AutonomOS runtime Evidence for storage in runtime Store."""
        payload = {
            "trace_id": self.trace_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "action_type": self.action_type.value,
            "action_details": self.action_details,
        }
        return RuntimeEvidence(
            id=self.trace_id,
            task_id=self.task_id,
            evidence_type=f"PROGRAMMER_{self.action_type.value}",
            data=json.dumps(payload, sort_keys=True),
            checksum=self.checksum,
            created_at=self.timestamp,
        )

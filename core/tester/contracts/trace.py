from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Optional

from core.models import Evidence as RuntimeEvidence
from core.tester.contracts.identifiers import (
    new_trace_id,
    validate_execution_id,
    validate_trace_id,
    validate_work_order_id,
)
from core.tester.errors import TesterLineageError
from core.tester.types import TesterActionType


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def compute_sha256(content: str | bytes) -> str:
    """Compute SHA-256 hex digest for cryptographic integrity."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


@dataclass
class TesterTrace:
    """
    Auditable record of a discrete action performed during a Tester execution session.
    Maintains cryptographic proof and unforgeable causal lineage back to execution and work order.
    """
    __test__ = False
    trace_id: str
    execution_id: str
    work_order_id: str
    task_id: str
    project_id: str
    correlation_id: str
    action_type: TesterActionType
    action_details: dict[str, Any] = field(default_factory=dict)
    checksum: str = ""
    timestamp: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_trace_id(self.trace_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        if not self.task_id:
            raise TesterLineageError("TesterTrace must have a valid non-empty task_id.")
        if not self.project_id:
            raise TesterLineageError("TesterTrace must have a valid non-empty project_id.")
        if not self.correlation_id:
            raise TesterLineageError("TesterTrace must have a valid non-empty correlation_id.")
        if isinstance(self.action_type, str):
            try:
                self.action_type = TesterActionType(self.action_type.upper())
            except (ValueError, KeyError):
                pass
        if not self.checksum:
            serialized_payload = json.dumps(self.action_details, sort_keys=True)
            action_val = self.action_type.value if hasattr(self.action_type, "value") else str(self.action_type)
            self.checksum = compute_sha256(f"{action_val}|{serialized_payload}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "correlation_id": self.correlation_id,
            "action_type": self.action_type.value if hasattr(self.action_type, "value") else str(self.action_type),
            "action_details": dict(self.action_details),
            "checksum": self.checksum,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TesterTrace:
        atype_raw = data.get("action_type", TesterActionType.INITIALIZE.value)
        try:
            action_type = TesterActionType(atype_raw)
        except (ValueError, TypeError):
            action_type = TesterActionType.INITIALIZE

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
        action_val = self.action_type.value if hasattr(self.action_type, "value") else str(self.action_type)
        payload = {
            "trace_id": self.trace_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "action_type": action_val,
            "action_details": self.action_details,
        }
        return RuntimeEvidence(
            id=self.trace_id,
            task_id=self.task_id,
            evidence_type=f"TESTER_{action_val}",
            data=json.dumps(payload, sort_keys=True),
            checksum=self.checksum,
            created_at=self.timestamp,
        )

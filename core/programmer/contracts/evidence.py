from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Optional
import uuid

from core.models import Evidence as RuntimeEvidence
from core.programmer.types import ProgrammerEvidenceType


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def compute_sha256(content: str | bytes) -> str:
    """Compute SHA-256 hex digest for cryptographic integrity."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


@dataclass
class ProgrammerEvidence:
    """
    Structured proof of an actual execution or output captured during implementation.
    Represents concrete artifacts, exit codes, test tallies, or diffs rather than
    unsupported textual claims or LLM self-confidence scores.
    """
    evidence_id: str
    evidence_type: ProgrammerEvidenceType
    source: str
    execution_id: str
    work_order_id: str
    summary: str
    timestamp: str = field(default_factory=utc_now)
    artifact_ref: Optional[str] = None
    provenance: dict[str, Any] = field(default_factory=dict)
    checksum: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.evidence_type, str):
            try:
                self.evidence_type = ProgrammerEvidenceType(self.evidence_type)
            except ValueError:
                self.evidence_type = ProgrammerEvidenceType.CUSTOM

        if not self.checksum and self.summary:
            payload = f"{self.evidence_type.value}|{self.source}|{self.summary}|{self.artifact_ref or ''}"
            self.checksum = compute_sha256(payload)

    def to_runtime_evidence(self, task_id: str) -> RuntimeEvidence:
        """Convert to standard AutonomOS runtime Evidence for storage in runtime Store."""
        data_payload = {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type.value,
            "source": self.source,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "summary": self.summary,
            "artifact_ref": self.artifact_ref,
            "provenance": self.provenance,
        }
        return RuntimeEvidence(
            id=self.evidence_id,
            task_id=task_id,
            evidence_type=f"PROGRAMMER_{self.evidence_type.value}",
            data=json.dumps(data_payload, sort_keys=True),
            checksum=self.checksum,
            created_at=self.timestamp,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type.value if isinstance(self.evidence_type, ProgrammerEvidenceType) else str(self.evidence_type),
            "source": self.source,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "summary": self.summary,
            "timestamp": self.timestamp,
            "artifact_ref": self.artifact_ref,
            "provenance": dict(self.provenance),
            "checksum": self.checksum,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammerEvidence:
        et_raw = data.get("evidence_type", ProgrammerEvidenceType.CUSTOM.value)
        try:
            evidence_type = ProgrammerEvidenceType(et_raw)
        except (ValueError, TypeError):
            evidence_type = ProgrammerEvidenceType.CUSTOM

        return cls(
            evidence_id=str(data.get("evidence_id", f"pevid-{uuid.uuid4().hex[:8]}")),
            evidence_type=evidence_type,
            source=str(data.get("source", "programmer")),
            execution_id=str(data.get("execution_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            summary=str(data.get("summary", "")),
            timestamp=str(data.get("timestamp", utc_now())),
            artifact_ref=data.get("artifact_ref"),
            provenance=dict(data.get("provenance", {})),
            checksum=data.get("checksum"),
            metadata=dict(data.get("metadata", {})),
        )

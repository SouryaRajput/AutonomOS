from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Optional
from core.models import Artifact, Evidence

@dataclass
class ArtifactDTO:
    id: str
    project_id: str
    task_id: Optional[str]
    worker_id: Optional[str]
    type: str
    path: str
    description: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "type": self.type,
            "path": self.path,
            "description": self.description,
            "created_at": self.created_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ArtifactDTO:
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            task_id=data.get("task_id"),
            worker_id=data.get("worker_id"),
            type=data["type"],
            path=data["path"],
            description=data.get("description", ""),
            created_at=data["created_at"]
        )

    @classmethod
    def from_domain(cls, artifact: Artifact) -> ArtifactDTO:
        return cls(
            id=artifact.id,
            project_id=artifact.project_id,
            task_id=artifact.task_id,
            worker_id=artifact.worker_id,
            type=artifact.type.value if hasattr(artifact.type, 'value') else str(artifact.type),
            path=artifact.path,
            description=artifact.description,
            created_at=artifact.created_at
        )

@dataclass
class EvidenceDTO:
    id: str
    task_id: str
    evidence_type: str
    data_preview: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "evidence_type": self.evidence_type,
            "data_preview": self.data_preview,
            "created_at": self.created_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EvidenceDTO:
        return cls(
            id=data["id"],
            task_id=data["task_id"],
            evidence_type=data["evidence_type"],
            data_preview=data["data_preview"],
            created_at=data["created_at"]
        )

    @classmethod
    def from_domain(cls, evidence: Evidence) -> EvidenceDTO:
        data_preview = str(evidence.data)[:200] if evidence.data else ""
        return cls(
            id=evidence.id,
            task_id=evidence.task_id,
            evidence_type=evidence.evidence_type,
            data_preview=data_preview,
            created_at=evidence.created_at
        )

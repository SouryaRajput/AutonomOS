from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from core.models import WorkerManifest

@dataclass
class WorkerSummaryDTO:
    id: str
    name: str
    role: str
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "status": self.status
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WorkerSummaryDTO:
        return cls(
            id=data["id"],
            name=data["name"],
            role=data["role"],
            status=data["status"]
        )

@dataclass
class WorkerDTO:
    id: str
    name: str
    role: str
    description: str
    status: str
    capabilities: List[str]
    active_task_id: Optional[str]
    active_task_title: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "description": self.description,
            "status": self.status,
            "capabilities": self.capabilities,
            "active_task_id": self.active_task_id,
            "active_task_title": self.active_task_title
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WorkerDTO:
        return cls(
            id=data["id"],
            name=data["name"],
            role=data["role"],
            description=data.get("description", ""),
            status=data["status"],
            capabilities=data.get("capabilities", []),
            active_task_id=data.get("active_task_id"),
            active_task_title=data.get("active_task_title")
        )

    @classmethod
    def from_domain(cls, manifest: WorkerManifest, task_title: Optional[str] = None) -> WorkerDTO:
        return cls(
            id=manifest.id,
            name=manifest.name,
            role=manifest.role,
            description=manifest.description,
            status=manifest.status.value if hasattr(manifest.status, 'value') else str(manifest.status),
            capabilities=manifest.capabilities,
            active_task_id=manifest.active_task_id,
            active_task_title=task_title
        )

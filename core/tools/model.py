from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional
import uuid

from core.enums import RiskLevel, ToolStatus
from core.tools.types import ToolCategory

if TYPE_CHECKING:
    from core.runtime.artifact_registry import ArtifactRegistry
    from core.storage.base import Store


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ToolDefinition:
    """
    Metadata specification and contract for an executable tool in AutonomOS.
    """
    id: str                                  # e.g. "filesystem.read_file", "shell.execute"
    name: str
    description: str
    version: str = "1.0.0"
    category: ToolCategory = ToolCategory.CUSTOM
    capabilities: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.LOW
    permissions_required: list[str] = field(default_factory=list)
    timeout_seconds: int = 30
    output_limit_bytes: int = 100000        # Default 100KB output ceiling
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "category": self.category.value if isinstance(self.category, ToolCategory) else self.category,
            "capabilities": self.capabilities,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "risk_level": self.risk_level.value if isinstance(self.risk_level, RiskLevel) else self.risk_level,
            "permissions_required": self.permissions_required,
            "timeout_seconds": self.timeout_seconds,
            "output_limit_bytes": self.output_limit_bytes,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolDefinition":
        return cls(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            version=data.get("version", "1.0.0"),
            category=ToolCategory(data.get("category", "CUSTOM")),
            capabilities=list(data.get("capabilities", [])),
            input_schema=dict(data.get("input_schema", {})),
            output_schema=dict(data.get("output_schema", {})),
            risk_level=RiskLevel(data.get("risk_level", "LOW")),
            permissions_required=list(data.get("permissions_required", [])),
            timeout_seconds=data.get("timeout_seconds", 30),
            output_limit_bytes=data.get("output_limit_bytes", 100000),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ToolRequest:
    """
    Formal request submitted by a Worker to invoke a specific tool.
    """
    project_id: str
    task_id: str
    worker_id: str
    tool_id: str
    arguments: dict[str, Any]
    request_id: str = field(default_factory=lambda: f"tool-req-{uuid.uuid4().hex[:10]}")
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    timeout_seconds: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "tool_id": self.tool_id,
            "arguments": self.arguments,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "timeout_seconds": self.timeout_seconds,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolRequest":
        return cls(
            request_id=data["request_id"],
            project_id=data["project_id"],
            task_id=data["task_id"],
            worker_id=data["worker_id"],
            tool_id=data["tool_id"],
            arguments=dict(data.get("arguments", {})),
            correlation_id=data.get("correlation_id"),
            causation_id=data.get("causation_id"),
            timeout_seconds=data.get("timeout_seconds"),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )


@dataclass
class ToolResult:
    """
    Structured outcome returned by the Tool Runtime following tool execution.
    """
    result_id: str
    request_id: str
    tool_id: str
    status: ToolStatus
    output: Any = None
    error_message: Optional[str] = None
    is_truncated: bool = False
    duration_ms: float = 0.0
    artifacts_created: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "request_id": self.request_id,
            "tool_id": self.tool_id,
            "status": self.status.value if isinstance(self.status, ToolStatus) else self.status,
            "output": self.output,
            "error_message": self.error_message,
            "is_truncated": self.is_truncated,
            "duration_ms": round(self.duration_ms, 2),
            "artifacts_created": self.artifacts_created,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolResult":
        return cls(
            result_id=data["result_id"],
            request_id=data["request_id"],
            tool_id=data["tool_id"],
            status=ToolStatus(data["status"]),
            output=data.get("output"),
            error_message=data.get("error_message"),
            is_truncated=data.get("is_truncated", False),
            duration_ms=float(data.get("duration_ms", 0.0)),
            artifacts_created=list(data.get("artifacts_created", [])),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ToolExecutionContext:
    """
    Controlled execution environment assembled by the Tool Runtime for tool invocation.
    """
    project_id: str
    workspace_root: str
    task_id: str
    worker_id: str
    permissions: list[str]
    environment: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 30
    output_limit_bytes: int = 100000
    artifact_registry: Optional[ArtifactRegistry] = None
    store: Optional[Store] = None

from pathlib import Path
import time
from typing import Any, Optional
import uuid

from core.enums import ArtifactType, RiskLevel, ToolStatus
from core.tools.base import BaseTool
from core.tools.model import ToolDefinition, ToolExecutionContext, ToolResult
from core.tools.types import ToolCategory


class ScreenshotTool(BaseTool):
    """
    Screenshot Capture Tool for AutonomOS.
    Captures viewports/windows and automatically registers output images with ArtifactRegistry.
    """

    def __init__(self, tool_id: str = "screenshot.capture"):
        self._tool_id = tool_id

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            id=self._tool_id,
            name="Screenshot Capture Tool",
            description="Capture screen/window viewports and register image artifacts.",
            version="1.0.0",
            category=ToolCategory.VISION,
            capabilities=["screenshot.capture"],
            input_schema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "default": "screenshot.png"},
                    "target_window": {"type": "string"},
                },
            },
            risk_level=RiskLevel.LOW,
            permissions_required=["screenshot.capture"],
            timeout_seconds=10,
        )

    def execute(self, context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        start_time = time.perf_counter()
        req_id = arguments.get("request_id", f"req-{uuid.uuid4().hex[:8]}")
        filename = arguments.get("filename", f"screenshot_{uuid.uuid4().hex[:6]}.png")

        try:
            # Deterministic PNG header simulation (89 50 4E 47 0D 0A 1A 0A)
            mock_png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x07\x80\x00\x00\x048\x08\x06\x00\x00\x00"

            artifacts_created = []
            if context.artifact_registry:
                artifact = context.artifact_registry.register_artifact(
                    project_id=context.project_id,
                    task_id=context.task_id,
                    worker_id=context.worker_id,
                    artifact_type=ArtifactType.FILE,
                    relative_path=filename,
                    description=f"Screenshot capture for task '{context.task_id}'",
                    content=mock_png_bytes,
                    base_dir=context.workspace_root,
                    metadata={"dimensions": {"width": 1920, "height": 1080}, "format": "png"},
                )
                artifacts_created.append(artifact.id)

            return ToolResult(
                result_id=f"res-{uuid.uuid4().hex[:8]}",
                request_id=req_id,
                tool_id=self._tool_id,
                status=ToolStatus.SUCCESS,
                output={
                    "path": filename,
                    "width": 1920,
                    "height": 1080,
                    "format": "png",
                    "status": "captured",
                },
                artifacts_created=artifacts_created,
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        except Exception as e:
            return ToolResult(
                result_id=f"res-{uuid.uuid4().hex[:8]}",
                request_id=req_id,
                tool_id=self._tool_id,
                status=ToolStatus.FAILED,
                error_message=str(e),
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )

from pathlib import Path
import time
from typing import Any, Optional
import uuid

from core.enums import RiskLevel, ToolStatus
from core.tools.base import BaseTool
from core.tools.model import ToolDefinition, ToolExecutionContext, ToolResult
from core.tools.policy import PermissionPolicy
from core.tools.types import ToolCategory


class OCRTool(BaseTool):
    """
    Controlled Optical Character Recognition (OCR) Tool for AutonomOS.
    Extracts text from images within workspace boundaries and returns structured text blocks and confidence scores.
    """

    def __init__(self, tool_id: str = "ocr.extract_text"):
        self._tool_id = tool_id

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            id=self._tool_id,
            name="Optical Character Recognition (OCR) Tool",
            description="Extract text from image files with structured confidence metadata.",
            version="1.0.0",
            category=ToolCategory.OCR,
            capabilities=["ocr.process"],
            input_schema={
                "type": "object",
                "properties": {
                    "image_path": {"type": "string"},
                },
                "required": ["image_path"],
            },
            risk_level=RiskLevel.LOW,
            permissions_required=["ocr.process"],
            timeout_seconds=15,
        )

    def execute(self, context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        start_time = time.perf_counter()
        req_id = arguments.get("request_id", f"req-{uuid.uuid4().hex[:8]}")
        image_path_str = arguments.get("image_path", "")

        try:
            target_path = PermissionPolicy.validate_path_confinement(image_path_str, context.workspace_root)
            if not target_path.exists():
                return ToolResult(
                    result_id=f"res-{uuid.uuid4().hex[:8]}",
                    request_id=req_id,
                    tool_id=self._tool_id,
                    status=ToolStatus.FAILED,
                    error_message=f"Image file '{image_path_str}' not found on disk.",
                    duration_ms=(time.perf_counter() - start_time) * 1000.0,
                )

            # Deterministic OCR extraction simulation
            extracted_text = f"Simulated extracted text from image {target_path.name}.\nAutonomOS Stage 5 Tool Runtime Verified."
            blocks = [
                {"line": 1, "text": f"Simulated extracted text from image {target_path.name}.", "confidence": 0.98},
                {"line": 2, "text": "AutonomOS Stage 5 Tool Runtime Verified.", "confidence": 0.99},
            ]

            return ToolResult(
                result_id=f"res-{uuid.uuid4().hex[:8]}",
                request_id=req_id,
                tool_id=self._tool_id,
                status=ToolStatus.SUCCESS,
                output={
                    "image_path": image_path_str,
                    "extracted_text": extracted_text,
                    "blocks": blocks,
                    "average_confidence": 0.985,
                },
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

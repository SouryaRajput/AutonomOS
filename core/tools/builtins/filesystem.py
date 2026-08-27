import os
from pathlib import Path
import time
from typing import Any, Optional
import uuid

from core.enums import RiskLevel, ToolStatus
from core.errors import ToolArgumentValidationError
from core.tools.base import BaseTool
from core.tools.model import ToolDefinition, ToolExecutionContext, ToolResult
from core.tools.policy import PermissionPolicy
from core.tools.types import ToolCategory


class FilesystemTool(BaseTool):
    """
    Unified, security-enforced Filesystem Tool for AutonomOS.
    Executes controlled file and directory operations strictly confined within project workspaces.
    """

    def __init__(self, tool_id: str = "filesystem", action: Optional[str] = None):
        self._tool_id = tool_id
        self._action = action

    def get_definition(self) -> ToolDefinition:
        required_perm = "filesystem.write" if self._tool_id in ("filesystem.write_file", "filesystem.delete_file") else "filesystem.read"
        risk = RiskLevel.MEDIUM if "write" in self._tool_id else (RiskLevel.HIGH if "delete" in self._tool_id else RiskLevel.LOW)
        
        return ToolDefinition(
            id=self._tool_id,
            name=f"Filesystem Operations ({self._tool_id})",
            description="Safe, workspace-confined filesystem operations (read, write, list, delete, stat).",
            version="1.0.0",
            category=ToolCategory.FILESYSTEM,
            capabilities=["filesystem.read", "filesystem.write", "filesystem.list", "filesystem.delete"],
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read_file", "write_file", "create_file", "delete_file", "list_directory", "file_exists", "stat_file"]},
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "encoding": {"type": "string", "default": "utf-8"},
                    "overwrite": {"type": "boolean", "default": True},
                },
                "required": ["path"],
            },
            risk_level=risk,
            permissions_required=[required_perm],
            timeout_seconds=10,
            output_limit_bytes=200000,
        )

    def execute(self, context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        start_time = time.perf_counter()
        req_id = arguments.get("request_id", f"req-{uuid.uuid4().hex[:8]}")
        action = self._action or arguments.get("action", "read_file")
        rel_path = arguments.get("path", "")

        try:
            target_path = PermissionPolicy.validate_path_confinement(rel_path, context.workspace_root)
            
            output: Any = None
            is_truncated = False

            if action == "read_file":
                if not target_path.exists():
                    return ToolResult(
                        result_id=f"res-{uuid.uuid4().hex[:8]}",
                        request_id=req_id,
                        tool_id=self._tool_id,
                        status=ToolStatus.FAILED,
                        error_message=f"File '{rel_path}' not found.",
                        duration_ms=(time.perf_counter() - start_time) * 1000.0,
                    )
                if target_path.is_dir():
                    return ToolResult(
                        result_id=f"res-{uuid.uuid4().hex[:8]}",
                        request_id=req_id,
                        tool_id=self._tool_id,
                        status=ToolStatus.FAILED,
                        error_message=f"Path '{rel_path}' is a directory, not a file.",
                        duration_ms=(time.perf_counter() - start_time) * 1000.0,
                    )

                content = target_path.read_text(encoding=arguments.get("encoding", "utf-8"))
                if len(content) > context.output_limit_bytes:
                    content = content[:context.output_limit_bytes] + "\n[... Content truncated due to output limit ...]"
                    is_truncated = True

                output = {"path": rel_path, "content": content, "size": len(content)}

            elif action in ("write_file", "create_file"):
                content = arguments.get("content", "")
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(content, encoding=arguments.get("encoding", "utf-8"))
                output = {"path": rel_path, "bytes_written": len(content), "status": "written"}

            elif action == "delete_file":
                if not target_path.exists():
                    return ToolResult(
                        result_id=f"res-{uuid.uuid4().hex[:8]}",
                        request_id=req_id,
                        tool_id=self._tool_id,
                        status=ToolStatus.FAILED,
                        error_message=f"File '{rel_path}' does not exist.",
                        duration_ms=(time.perf_counter() - start_time) * 1000.0,
                    )
                if target_path.is_file():
                    target_path.unlink()
                elif target_path.is_dir():
                    os.rmdir(target_path)
                output = {"path": rel_path, "status": "deleted"}

            elif action == "list_directory":
                if not target_path.exists() or not target_path.is_dir():
                    return ToolResult(
                        result_id=f"res-{uuid.uuid4().hex[:8]}",
                        request_id=req_id,
                        tool_id=self._tool_id,
                        status=ToolStatus.FAILED,
                        error_message=f"Directory '{rel_path}' not found.",
                        duration_ms=(time.perf_counter() - start_time) * 1000.0,
                    )

                entries = []
                for child in sorted(target_path.iterdir()):
                    entries.append({
                        "name": child.name,
                        "is_dir": child.is_dir(),
                        "size": child.stat().st_size if child.is_file() else None,
                    })
                output = {"path": rel_path, "entries": entries}

            elif action == "file_exists":
                output = {"path": rel_path, "exists": target_path.exists()}

            elif action == "stat_file":
                if not target_path.exists():
                    return ToolResult(
                        result_id=f"res-{uuid.uuid4().hex[:8]}",
                        request_id=req_id,
                        tool_id=self._tool_id,
                        status=ToolStatus.FAILED,
                        error_message=f"Path '{rel_path}' not found.",
                        duration_ms=(time.perf_counter() - start_time) * 1000.0,
                    )
                st = target_path.stat()
                output = {
                    "path": rel_path,
                    "is_dir": target_path.is_dir(),
                    "is_file": target_path.is_file(),
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                }
            else:
                raise ToolArgumentValidationError(self._tool_id, [f"Unknown filesystem action '{action}'"])

            return ToolResult(
                result_id=f"res-{uuid.uuid4().hex[:8]}",
                request_id=req_id,
                tool_id=self._tool_id,
                status=ToolStatus.SUCCESS,
                output=output,
                is_truncated=is_truncated,
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

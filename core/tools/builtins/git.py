import os
from pathlib import Path
import subprocess
import time
from typing import Any, Optional
import uuid

from core.enums import RiskLevel, ToolStatus
from core.errors import ToolArgumentValidationError
from core.tools.base import BaseTool
from core.tools.model import ToolDefinition, ToolExecutionContext, ToolResult
from core.tools.policy import PermissionPolicy
from core.tools.types import ToolCategory


class GitTool(BaseTool):
    """
    Controlled Git Operations Tool for AutonomOS.
    Executes version control operations (status, diff, log, branch, add, commit) strictly inside the project workspace.
    """

    def __init__(self, tool_id: str = "git"):
        self._tool_id = tool_id

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            id=self._tool_id,
            name="Git Version Control Tool",
            description="Execute controlled Git operations (status, diff, log, branch, add, commit) in the workspace.",
            version="1.0.0",
            category=ToolCategory.GIT,
            capabilities=["git.read", "git.write"],
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["status", "diff", "log", "branch", "add", "commit"]},
                    "path": {"type": "string"},
                    "message": {"type": "string"},
                    "max_count": {"type": "integer", "default": 10},
                },
                "required": ["action"],
            },
            risk_level=RiskLevel.MEDIUM,
            permissions_required=["git.read"],
            timeout_seconds=20,
            output_limit_bytes=100000,
        )

    def execute(self, context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        start_time = time.perf_counter()
        req_id = arguments.get("request_id", f"req-{uuid.uuid4().hex[:8]}")
        action = arguments.get("action", "status")
        workspace = Path(context.workspace_root).resolve()

        # Check if write permission required for modifying git actions
        if action in ("add", "commit"):
            try:
                PermissionPolicy.evaluate_permissions(context.permissions, ["git.write"], context.worker_id, self._tool_id)
            except Exception as e:
                return ToolResult(
                    result_id=f"res-{uuid.uuid4().hex[:8]}",
                    request_id=req_id,
                    tool_id=self._tool_id,
                    status=ToolStatus.DENIED,
                    error_message=str(e),
                    duration_ms=(time.perf_counter() - start_time) * 1000.0,
                )

        cmd: list[str] = ["git"]
        if action == "status":
            cmd.extend(["status", "--porcelain"])
        elif action == "diff":
            target = arguments.get("path", "")
            cmd.extend(["diff"])
            if target:
                cmd.append(target)
        elif action == "log":
            max_n = arguments.get("max_count", 10)
            cmd.extend(["log", f"-n{max_n}", "--oneline"])
        elif action == "branch":
            cmd.extend(["branch", "--list"])
        elif action == "add":
            target = arguments.get("path", ".")
            cmd.extend(["add", target])
        elif action == "commit":
            msg = arguments.get("message")
            if not msg:
                return ToolResult(
                    result_id=f"res-{uuid.uuid4().hex[:8]}",
                    request_id=req_id,
                    tool_id=self._tool_id,
                    status=ToolStatus.VALIDATION_ERROR,
                    error_message="Commit message is required for git commit.",
                    duration_ms=(time.perf_counter() - start_time) * 1000.0,
                )
            cmd.extend(["commit", "-m", msg])
        else:
            return ToolResult(
                result_id=f"res-{uuid.uuid4().hex[:8]}",
                request_id=req_id,
                tool_id=self._tool_id,
                status=ToolStatus.VALIDATION_ERROR,
                error_message=f"Unknown git action '{action}'",
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        try:
            res = subprocess.run(
                cmd,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=context.timeout_seconds,
            )

            stdout_str = res.stdout
            is_truncated = False
            if len(stdout_str) > context.output_limit_bytes:
                stdout_str = stdout_str[:context.output_limit_bytes] + "\n[... git output truncated ...]"
                is_truncated = True

            status = ToolStatus.SUCCESS if res.returncode == 0 else ToolStatus.FAILED
            return ToolResult(
                result_id=f"res-{uuid.uuid4().hex[:8]}",
                request_id=req_id,
                tool_id=self._tool_id,
                status=status,
                output={
                    "action": action,
                    "stdout": stdout_str,
                    "stderr": res.stderr,
                    "exit_code": res.returncode,
                },
                error_message=res.stderr if res.returncode != 0 and not stdout_str else None,
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

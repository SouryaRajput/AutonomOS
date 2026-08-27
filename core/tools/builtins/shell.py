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


class ShellTool(BaseTool):
    """
    Controlled Shell Execution Tool for AutonomOS.
    Executes shell commands with strict timeout, workspace working directory confinement,
    output limits, and environment secret redaction.
    """

    def __init__(self, tool_id: str = "shell.execute"):
        self._tool_id = tool_id

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            id=self._tool_id,
            name="Controlled Shell Execution",
            description="Execute shell commands inside the project workspace with timeout and output bounds.",
            version="1.0.0",
            category=ToolCategory.SHELL,
            capabilities=["shell.execute"],
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "args": {"type": "array", "items": {"type": "string"}},
                    "working_directory": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "default": 30},
                    "env": {"type": "object"},
                },
            },
            risk_level=RiskLevel.HIGH,
            permissions_required=["shell.execute"],
            timeout_seconds=30,
            output_limit_bytes=100000,
        )

    def validate_arguments(self, arguments: dict[str, Any]) -> None:
        if not arguments.get("command") and not arguments.get("args"):
            raise ToolArgumentValidationError(self._tool_id, ["Either 'command' or 'args' must be provided."])

    def execute(self, context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        start_time = time.perf_counter()
        req_id = arguments.get("request_id", f"req-{uuid.uuid4().hex[:8]}")
        timeout = arguments.get("timeout_seconds", context.timeout_seconds)

        # 1. Resolve and validate working directory
        work_dir_rel = arguments.get("working_directory", "")
        try:
            cwd = PermissionPolicy.validate_path_confinement(work_dir_rel, context.workspace_root)
        except Exception as e:
            return ToolResult(
                result_id=f"res-{uuid.uuid4().hex[:8]}",
                request_id=req_id,
                tool_id=self._tool_id,
                status=ToolStatus.FAILED,
                error_message=str(e),
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        # 2. Sanitize environment
        custom_env = arguments.get("env")
        safe_env = PermissionPolicy.sanitize_environment(custom_env=custom_env, include_host_safe=True)

        # 3. Determine command invocation
        command = arguments.get("command")
        cmd_args = arguments.get("args")
        use_shell = bool(command and not cmd_args)
        cmd_input = command if use_shell else (cmd_args or command)

        try:
            process = subprocess.Popen(
                cmd_input,
                shell=use_shell,
                cwd=str(cwd),
                env=safe_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            try:
                stdout_str, stderr_str = process.communicate(timeout=timeout)
                exit_code = process.returncode
            except subprocess.TimeoutExpired:
                process.kill()
                stdout_str, stderr_str = process.communicate()
                return ToolResult(
                    result_id=f"res-{uuid.uuid4().hex[:8]}",
                    request_id=req_id,
                    tool_id=self._tool_id,
                    status=ToolStatus.TIMEOUT,
                    output={"stdout": stdout_str, "stderr": stderr_str, "exit_code": -1},
                    error_message=f"Command timed out after {timeout} seconds.",
                    duration_ms=(time.perf_counter() - start_time) * 1000.0,
                )

            is_truncated = False
            if len(stdout_str) > context.output_limit_bytes:
                stdout_str = stdout_str[:context.output_limit_bytes] + "\n[... stdout truncated ...]"
                is_truncated = True

            if len(stderr_str) > context.output_limit_bytes:
                stderr_str = stderr_str[:context.output_limit_bytes] + "\n[... stderr truncated ...]"
                is_truncated = True

            status = ToolStatus.SUCCESS if exit_code == 0 else ToolStatus.FAILED

            return ToolResult(
                result_id=f"res-{uuid.uuid4().hex[:8]}",
                request_id=req_id,
                tool_id=self._tool_id,
                status=status,
                output={
                    "stdout": stdout_str,
                    "stderr": stderr_str,
                    "exit_code": exit_code,
                },
                error_message=stderr_str if exit_code != 0 and not stdout_str else None,
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

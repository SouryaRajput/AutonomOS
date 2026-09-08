from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any, Optional, Union

from core.programmer.contracts.command_resolver import CommandDecision, CommandRequest
from core.programmer.contracts.event_translation import (
    ProgrammerExecutionEvent,
    ProgrammerExecutionTraceCollector,
)
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.filesystem_resolver import FilesystemDecision
from core.programmer.errors import (
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    ExecutionContextStatus,
    FilesystemOperation,
    ProgrammerExecutionEventType,
)

logger = logging.getLogger("AutonomOS.Programmer.CapabilityBinding")


@dataclass
class CapabilityOperationResult:
    """
    Structured outcome contract returned by every Programmer capability execution.
    
    Guarantees:
    - Clear distinction between authorization (allowed) and execution outcome (success).
    - Denied operations preserve structured denial reasons and policy rules.
    - Preserves causal lineage (execution_id, work_order_id, trace).
    """
    success: bool
    allowed: bool
    operation: str
    target: str
    output: Any = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    denial_reason: Optional[str] = None
    policy_rule: Optional[str] = None
    execution_id: str = ""
    work_order_id: str = ""
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "allowed": self.allowed,
            "operation": self.operation,
            "target": self.target,
            "output": self.output,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "denial_reason": self.denial_reason,
            "policy_rule": self.policy_rule,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    def to_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)


class ClineCapabilityBinding:
    """
    Authoritative capability binding interface mediating between Cline coding agent actions
    and the AutonomOS ProgrammerExecutionContext.
    
    Architectural Guarantee:
        Cline
          ↓
        ClineCapabilityBinding (Tool Gateway / Capability Interface)
          ↓
        AutonomOS policy (FilesystemBoundaryResolver / CommandBoundaryResolver)
          ↓
        Actual operation (strictly confined to authorized workspace)
    
    Invariants:
    1. Every exposed capability (read, write, create, delete, rename, command)
       MUST be authorized by AutonomOS policy before any disk or process action.
    2. Denied operations MUST NOT execute, return structured denial information,
       and record a correlated execution event.
    3. Allowed operations MUST remain inside workspace scope and record operation metadata.
    4. Anti-bypass dispatcher normalizes all tool aliases to authoritative policy methods.
    5. Cancelled or unready execution contexts immediately refuse capability requests.
    """

    def __init__(
        self,
        context: ProgrammerExecutionContext,
        trace_collector: Optional[ProgrammerExecutionTraceCollector] = None,
        on_event: Optional[Callable[[ProgrammerExecutionEvent], None]] = None,
    ):
        if context is None:
            raise ProgrammerValidationError("ProgrammerExecutionContext cannot be None.")
        self.context = context
        self.trace_collector = trace_collector or ProgrammerExecutionTraceCollector(
            execution_id=context.execution_id,
            work_order_id=context.work_order_id,
        )
        self.on_event = on_event
        self._active_processes: dict[str, Any] = {}
        self._cancel_requested: bool = False
        self._cancellation_reason: str = ""

    def cancel_active_commands(self, reason: str = "Cancellation requested") -> int:
        """
        Terminate all currently active command processes and set cancel flag.
        Returns the count of processes terminated.
        """
        self._cancel_requested = True
        self._cancellation_reason = reason
        terminated_count = 0
        for cmd_id, proc in list(self._active_processes.items()):
            try:
                if hasattr(proc, "poll") and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1.0)
                    except Exception:
                        if hasattr(proc, "kill"):
                            proc.kill()
                            proc.wait(timeout=1.0)
                    terminated_count += 1
            except Exception as err:
                logger.warning(f"Error terminating active command {cmd_id}: {err}")
            finally:
                self._active_processes.pop(cmd_id, None)
        return terminated_count

    @property
    def is_cancel_requested(self) -> bool:
        return self._cancel_requested

    @property
    def execution_id(self) -> str:
        return self.context.execution_id

    @property
    def work_order_id(self) -> str:
        return self.context.work_order_id

    def _check_context_readiness(self, operation: str, target: str) -> Optional[CapabilityOperationResult]:
        """Verify context is active and ready before attempting any policy query."""
        if not self.context.is_ready():
            err_msg = (
                f"Execution context is in '{self.context.status.value}' state: "
                f"{self.context.error_message or 'execution cancelled or not ready'}."
            )
            err_code = (
                "CANCELLED_EXECUTION_ERROR"
                if self.context.status == ExecutionContextStatus.FAILED and "cancel" in str(self.context.error_message).lower()
                else "FAILED_CONTEXT_ERROR"
            )
            # Record denial event
            self._record_event(
                event_type=ProgrammerExecutionEventType.WARNING,
                payload={
                    "operation": operation,
                    "target": target,
                    "allowed": False,
                    "error_code": err_code,
                    "denial_reason": err_msg,
                },
            )
            return CapabilityOperationResult(
                success=False,
                allowed=False,
                operation=operation,
                target=target,
                error_code=err_code,
                error_message=err_msg,
                denial_reason=err_msg,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
            )
        return None

    def _record_event(
        self,
        event_type: ProgrammerExecutionEventType,
        payload: dict[str, Any],
        raw_event_ref: Optional[str] = None,
    ) -> ProgrammerExecutionEvent:
        """Record a normalized execution event into the trace collector."""
        event = ProgrammerExecutionEvent(
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            event_type=event_type,
            payload=payload,
            raw_event_ref=raw_event_ref,
            trace={
                "execution_id": self.execution_id,
                "work_order_id": self.work_order_id,
                "workspace_id": self.context.workspace_id,
                "project_id": self.context.project_id,
            },
        )
        self.trace_collector.add_event(event)
        if self.on_event:
            try:
                self.on_event(event)
            except Exception as cb_err:
                logger.warning(f"Error invoking on_event in capability binding: {cb_err}")
        return event

    # ==========================================================================
    # 1. Filesystem Capabilities (Policy-Mediated)
    # ==========================================================================

    def read_file(self, path: str) -> CapabilityOperationResult:
        """Read file contents subject to FilesystemBoundaryResolver authorization."""
        readiness_err = self._check_context_readiness("FILE_READ", path)
        if readiness_err:
            return readiness_err

        # Policy authorization
        decision: FilesystemDecision = self.context.may_read_file(path)
        if not decision.allowed:
            self._record_event(
                event_type=ProgrammerExecutionEventType.FILE_OPERATION,
                payload={
                    "operation": "READ",
                    "path": path,
                    "allowed": False,
                    "scope": decision.scope.value,
                    "denial_reason": decision.reason,
                    "policy_rule": decision.policy_rule,
                },
            )
            return CapabilityOperationResult(
                success=False,
                allowed=False,
                operation="FILE_READ",
                target=path,
                error_code="PERMISSION_DENIED",
                error_message=f"File read denied: {decision.reason}",
                denial_reason=decision.reason,
                policy_rule=decision.policy_rule,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )

        # Authorized: perform actual read
        try:
            abs_path = self.context.workspace.resolve_path(path)
            if not os.path.exists(abs_path):
                return CapabilityOperationResult(
                    success=False,
                    allowed=True,
                    operation="FILE_READ",
                    target=path,
                    error_code="FILE_NOT_FOUND",
                    error_message=f"File '{path}' does not exist.",
                    execution_id=self.execution_id,
                    work_order_id=self.work_order_id,
                    trace=decision.trace,
                )

            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            self._record_event(
                event_type=ProgrammerExecutionEventType.FILE_OPERATION,
                payload={
                    "operation": "READ",
                    "path": decision.normalized_path,
                    "allowed": True,
                    "bytes_read": len(content.encode("utf-8")),
                },
            )

            return CapabilityOperationResult(
                success=True,
                allowed=True,
                operation="FILE_READ",
                target=path,
                output=content,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )
        except Exception as err:
            logger.error(f"Error reading authorized file '{path}': {err}")
            return CapabilityOperationResult(
                success=False,
                allowed=True,
                operation="FILE_READ",
                target=path,
                error_code="IO_ERROR",
                error_message=str(err),
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )

    def write_file(self, path: str, content: Union[str, bytes]) -> CapabilityOperationResult:
        """Modify or write file contents subject to FilesystemBoundaryResolver authorization."""
        readiness_err = self._check_context_readiness("FILE_WRITE", path)
        if readiness_err:
            return readiness_err

        decision: FilesystemDecision = self.context.may_write_file(path)
        if not decision.allowed:
            self._record_event(
                event_type=ProgrammerExecutionEventType.FILE_OPERATION,
                payload={
                    "operation": "WRITE",
                    "path": path,
                    "allowed": False,
                    "scope": decision.scope.value,
                    "denial_reason": decision.reason,
                    "policy_rule": decision.policy_rule,
                },
            )
            return CapabilityOperationResult(
                success=False,
                allowed=False,
                operation="FILE_WRITE",
                target=path,
                error_code="PERMISSION_DENIED",
                error_message=f"File write denied: {decision.reason}",
                denial_reason=decision.reason,
                policy_rule=decision.policy_rule,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )

        try:
            abs_path = self.context.workspace.resolve_path(path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)

            if isinstance(content, bytes):
                with open(abs_path, "wb") as f:
                    f.write(content)
                bytes_written = len(content)
            else:
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write(content)
                bytes_written = len(content.encode("utf-8"))

            self._record_event(
                event_type=ProgrammerExecutionEventType.FILE_OPERATION,
                payload={
                    "operation": "WRITE",
                    "path": decision.normalized_path,
                    "allowed": True,
                    "bytes_written": bytes_written,
                },
            )

            return CapabilityOperationResult(
                success=True,
                allowed=True,
                operation="FILE_WRITE",
                target=path,
                output={"path": decision.normalized_path, "bytes_written": bytes_written},
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )
        except Exception as err:
            logger.error(f"Error writing authorized file '{path}': {err}")
            return CapabilityOperationResult(
                success=False,
                allowed=True,
                operation="FILE_WRITE",
                target=path,
                error_code="IO_ERROR",
                error_message=str(err),
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )

    def create_file(self, path: str, content: Union[str, bytes] = "") -> CapabilityOperationResult:
        """Create a new file subject to FilesystemBoundaryResolver authorization."""
        readiness_err = self._check_context_readiness("FILE_CREATE", path)
        if readiness_err:
            return readiness_err

        decision: FilesystemDecision = self.context.may_create_file(path)
        if not decision.allowed:
            self._record_event(
                event_type=ProgrammerExecutionEventType.FILE_OPERATION,
                payload={
                    "operation": "CREATE",
                    "path": path,
                    "allowed": False,
                    "scope": decision.scope.value,
                    "denial_reason": decision.reason,
                    "policy_rule": decision.policy_rule,
                },
            )
            return CapabilityOperationResult(
                success=False,
                allowed=False,
                operation="FILE_CREATE",
                target=path,
                error_code="PERMISSION_DENIED",
                error_message=f"File creation denied: {decision.reason}",
                denial_reason=decision.reason,
                policy_rule=decision.policy_rule,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )

        try:
            abs_path = self.context.workspace.resolve_path(path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)

            if isinstance(content, bytes):
                with open(abs_path, "wb") as f:
                    f.write(content)
                bytes_written = len(content)
            else:
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write(content)
                bytes_written = len(content.encode("utf-8"))

            self._record_event(
                event_type=ProgrammerExecutionEventType.FILE_OPERATION,
                payload={
                    "operation": "CREATE",
                    "path": decision.normalized_path,
                    "allowed": True,
                    "bytes_written": bytes_written,
                },
            )

            return CapabilityOperationResult(
                success=True,
                allowed=True,
                operation="FILE_CREATE",
                target=path,
                output={"path": decision.normalized_path, "bytes_written": bytes_written},
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )
        except Exception as err:
            logger.error(f"Error creating authorized file '{path}': {err}")
            return CapabilityOperationResult(
                success=False,
                allowed=True,
                operation="FILE_CREATE",
                target=path,
                error_code="IO_ERROR",
                error_message=str(err),
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )

    def delete_file(self, path: str) -> CapabilityOperationResult:
        """Delete a file subject to FilesystemBoundaryResolver authorization."""
        readiness_err = self._check_context_readiness("FILE_DELETE", path)
        if readiness_err:
            return readiness_err

        decision: FilesystemDecision = self.context.may_delete_file(path)
        if not decision.allowed:
            self._record_event(
                event_type=ProgrammerExecutionEventType.FILE_OPERATION,
                payload={
                    "operation": "DELETE",
                    "path": path,
                    "allowed": False,
                    "scope": decision.scope.value,
                    "denial_reason": decision.reason,
                    "policy_rule": decision.policy_rule,
                },
            )
            return CapabilityOperationResult(
                success=False,
                allowed=False,
                operation="FILE_DELETE",
                target=path,
                error_code="PERMISSION_DENIED",
                error_message=f"File deletion denied: {decision.reason}",
                denial_reason=decision.reason,
                policy_rule=decision.policy_rule,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )

        try:
            abs_path = self.context.workspace.resolve_path(path)
            if os.path.exists(abs_path):
                if os.path.isdir(abs_path):
                    os.rmdir(abs_path)
                else:
                    os.remove(abs_path)

            self._record_event(
                event_type=ProgrammerExecutionEventType.FILE_OPERATION,
                payload={
                    "operation": "DELETE",
                    "path": decision.normalized_path,
                    "allowed": True,
                },
            )

            return CapabilityOperationResult(
                success=True,
                allowed=True,
                operation="FILE_DELETE",
                target=path,
                output={"path": decision.normalized_path, "deleted": True},
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )
        except Exception as err:
            logger.error(f"Error deleting authorized file '{path}': {err}")
            return CapabilityOperationResult(
                success=False,
                allowed=True,
                operation="FILE_DELETE",
                target=path,
                error_code="IO_ERROR",
                error_message=str(err),
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )

    def rename_file(self, source_path: str, destination_path: str) -> CapabilityOperationResult:
        """Rename or move a file subject to FilesystemBoundaryResolver authorization."""
        readiness_err = self._check_context_readiness("FILE_RENAME", f"{source_path} -> {destination_path}")
        if readiness_err:
            return readiness_err

        decision: FilesystemDecision = self.context.may_rename_file(source_path, destination_path)
        if not decision.allowed:
            self._record_event(
                event_type=ProgrammerExecutionEventType.FILE_OPERATION,
                payload={
                    "operation": "RENAME",
                    "source_path": source_path,
                    "destination_path": destination_path,
                    "allowed": False,
                    "denial_reason": decision.reason,
                    "policy_rule": decision.policy_rule,
                },
            )
            return CapabilityOperationResult(
                success=False,
                allowed=False,
                operation="FILE_RENAME",
                target=f"{source_path} -> {destination_path}",
                error_code="PERMISSION_DENIED",
                error_message=f"File rename denied: {decision.reason}",
                denial_reason=decision.reason,
                policy_rule=decision.policy_rule,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )

        try:
            src_abs = self.context.workspace.resolve_path(source_path)
            dst_abs = self.context.workspace.resolve_path(destination_path)
            os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
            os.rename(src_abs, dst_abs)

            self._record_event(
                event_type=ProgrammerExecutionEventType.FILE_OPERATION,
                payload={
                    "operation": "RENAME",
                    "source_path": decision.normalized_path,
                    "destination_path": decision.normalized_destination_path,
                    "allowed": True,
                },
            )

            return CapabilityOperationResult(
                success=True,
                allowed=True,
                operation="FILE_RENAME",
                target=f"{source_path} -> {destination_path}",
                output={
                    "source_path": decision.normalized_path,
                    "destination_path": decision.normalized_destination_path,
                },
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )
        except Exception as err:
            logger.error(f"Error renaming authorized file '{source_path}': {err}")
            return CapabilityOperationResult(
                success=False,
                allowed=True,
                operation="FILE_RENAME",
                target=f"{source_path} -> {destination_path}",
                error_code="IO_ERROR",
                error_message=str(err),
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )

    # ==========================================================================
    # 2. Command Execution Capability (Policy-Mediated)
    # ==========================================================================

    def execute_command(
        self,
        command: Union[str, list[str]],
        working_directory: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ) -> CapabilityOperationResult:
        """
        Execute a shell or binary command strictly authorized by CommandBoundaryResolver.
        
        Guarantees:
        - Only explicitly whitelisted commands/subcommands in WorkOrder are allowed.
        - Shell chaining operators (&&, ||, ;, |, etc.) are strictly denied.
        - Working directory must be confined within the workspace root.
        - Commands run without shell=True to eliminate shell injection escapes.
        """
        cmd_str = command if isinstance(command, str) else shlex.join(command)
        if self._cancel_requested:
            return CapabilityOperationResult(
                success=False,
                allowed=False,
                operation="COMMAND_EXECUTION",
                target=cmd_str,
                error_code="COMMAND_CANCELLED",
                error_message=f"Command execution cancelled: {self._cancellation_reason or 'Execution was cancelled'}.",
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
            )

        readiness_err = self._check_context_readiness("COMMAND_EXECUTION", cmd_str)
        if readiness_err:
            return readiness_err

        # Policy authorization
        decision: CommandDecision = self.context.may_execute_command(
            request=command,
            working_directory=working_directory,
        )

        if not decision.allowed:
            self._record_event(
                event_type=ProgrammerExecutionEventType.COMMAND_OPERATION,
                payload={
                    "command": cmd_str,
                    "working_directory": working_directory,
                    "allowed": False,
                    "decision": decision.decision.value,
                    "denial_reason": decision.reason,
                    "policy_rule": decision.matched_rule,
                },
            )
            return CapabilityOperationResult(
                success=False,
                allowed=False,
                operation="COMMAND_EXECUTION",
                target=cmd_str,
                error_code="COMMAND_DENIED",
                error_message=f"Command execution denied: {decision.reason}",
                denial_reason=decision.reason,
                policy_rule=decision.matched_rule,
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )

        # Authorized command: resolve confined working directory
        try:
            cwd = self.context.resolve_working_directory(working_directory)
        except Exception as cwd_err:
            return CapabilityOperationResult(
                success=False,
                allowed=False,
                operation="COMMAND_EXECUTION",
                target=cmd_str,
                error_code="UNAUTHORIZED_WORKING_DIRECTORY",
                error_message=str(cwd_err),
                denial_reason=str(cwd_err),
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )

        # Safe token execution (shell=False)
        tokens = shlex.split(cmd_str) if isinstance(command, str) else list(command)
        timeout = timeout_seconds or getattr(self.context.work_order, "time_budget", 60) or 60

        start_time = time.monotonic()
        proc: Optional[subprocess.Popen] = None
        cmd_key = f"cmd-{int(start_time * 1000)}"
        try:
            proc = subprocess.Popen(
                tokens,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._active_processes[cmd_key] = proc
            stdout, stderr = proc.communicate(timeout=timeout)
            returncode = proc.returncode
            duration_ms = int((time.monotonic() - start_time) * 1000)

            if self._cancel_requested:
                return CapabilityOperationResult(
                    success=False,
                    allowed=True,
                    operation="COMMAND_EXECUTION",
                    target=decision.normalized_command,
                    error_code="COMMAND_CANCELLED",
                    error_message=f"Command cancelled: {self._cancellation_reason or 'Execution was cancelled'}.",
                    output={
                        "exit_code": returncode,
                        "stdout": stdout,
                        "stderr": stderr,
                        "duration_ms": duration_ms,
                    },
                    execution_id=self.execution_id,
                    work_order_id=self.work_order_id,
                    trace=decision.trace,
                )

            self._record_event(
                event_type=ProgrammerExecutionEventType.COMMAND_OPERATION,
                payload={
                    "command": decision.normalized_command,
                    "working_directory": cwd,
                    "allowed": True,
                    "exit_code": returncode,
                    "duration_ms": duration_ms,
                },
            )

            return CapabilityOperationResult(
                success=(returncode == 0),
                allowed=True,
                operation="COMMAND_EXECUTION",
                target=decision.normalized_command,
                output={
                    "exit_code": returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "duration_ms": duration_ms,
                },
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )
        except subprocess.TimeoutExpired:
            if proc is not None:
                proc.kill()
                proc.wait()
            duration_ms = int((time.monotonic() - start_time) * 1000)
            self._record_event(
                event_type=ProgrammerExecutionEventType.ERROR,
                payload={
                    "command": decision.normalized_command,
                    "error": "COMMAND_TIMEOUT",
                    "duration_ms": duration_ms,
                },
            )
            return CapabilityOperationResult(
                success=False,
                allowed=True,
                operation="COMMAND_EXECUTION",
                target=decision.normalized_command,
                error_code="COMMAND_TIMEOUT",
                error_message=f"Command timed out after {timeout} seconds.",
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )
        except Exception as err:
            logger.error(f"Error executing command '{cmd_str}': {err}")
            return CapabilityOperationResult(
                success=False,
                allowed=True,
                operation="COMMAND_EXECUTION",
                target=cmd_str,
                error_code="EXECUTION_ERROR",
                error_message=str(err),
                execution_id=self.execution_id,
                work_order_id=self.work_order_id,
                trace=decision.trace,
            )

    # ==========================================================================
    # 3. Anti-Bypass Canonical Tool Dispatcher
    # ==========================================================================

    def dispatch_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> CapabilityOperationResult:
        """
        Canonical dispatcher mapping any Cline tool invocation or alias
        to the authoritative policy-enforced capability methods.
        
        Guarantees that alternative tool names cannot evade AutonomOS boundary checks.
        """
        canonical = str(tool_name).strip().lower()
        args = dict(arguments or {})

        # File read aliases
        if canonical in ("read_file", "read", "cat", "view_file", "open_file"):
            path = args.get("path") or args.get("file") or args.get("filename") or args.get("target") or ""
            return self.read_file(str(path))

        # File write/modify aliases
        if canonical in ("write_file", "write", "edit_file", "replace_file_content", "patch_file", "put_file", "modify_file"):
            path = args.get("path") or args.get("file") or args.get("filename") or args.get("target") or ""
            content = args.get("content", "")
            return self.write_file(str(path), content)

        # File creation aliases
        if canonical in ("create_file", "touch", "new_file"):
            path = args.get("path") or args.get("file") or args.get("filename") or args.get("target") or ""
            content = args.get("content", "")
            return self.create_file(str(path), content)

        # File deletion aliases
        if canonical in ("delete_file", "delete", "rm", "unlink", "remove_file"):
            path = args.get("path") or args.get("file") or args.get("filename") or args.get("target") or ""
            return self.delete_file(str(path))

        # File rename aliases
        if canonical in ("rename_file", "rename", "mv", "move_file"):
            src = args.get("source_path") or args.get("source") or args.get("old_path") or args.get("src") or ""
            dst = args.get("destination_path") or args.get("destination") or args.get("new_path") or args.get("dst") or ""
            return self.rename_file(str(src), str(dst))

        # Command execution aliases
        if canonical in ("execute_command", "exec", "command", "run_command", "bash", "sh", "terminal"):
            cmd = args.get("command") or args.get("cmd") or args.get("line") or ""
            cwd = args.get("working_directory") or args.get("cwd") or args.get("dir")
            timeout = args.get("timeout_seconds") or args.get("timeout")
            return self.execute_command(command=cmd, working_directory=cwd, timeout_seconds=timeout)

        # Unrecognized tool: strictly deny and log warning
        denial_msg = f"Unknown capability '{tool_name}' is not authorized or recognized by AutonomOS."
        self._record_event(
            event_type=ProgrammerExecutionEventType.WARNING,
            payload={
                "tool_name": tool_name,
                "arguments": args,
                "error": "UNKNOWN_CAPABILITY_ERROR",
                "denial_reason": denial_msg,
            },
        )
        return CapabilityOperationResult(
            success=False,
            allowed=False,
            operation=tool_name,
            target=json.dumps(args, sort_keys=True),
            error_code="UNKNOWN_CAPABILITY_ERROR",
            error_message=denial_msg,
            denial_reason=denial_msg,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
        )

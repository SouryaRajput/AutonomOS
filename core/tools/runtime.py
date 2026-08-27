from __future__ import annotations

import logging
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Optional
import uuid

from core.enums import RiskLevel, ToolStatus
from core.errors import (
    ProjectNotFoundError,
    TaskNotFoundError,
    ToolArgumentValidationError,
    ToolNotFoundError,
    ToolPermissionDeniedError,
    WorkerNotFoundError,
)
from core.events.model import Event
from core.events.types import EventSource, EventType
from core.models import Project, Task, WorkerManifest
from core.safety.checkpoint import CheckpointManager
from core.safety.evaluator import SafetyEvaluator
from core.safety.model import SafetyConfig, SafetyDecision
from core.safety.types import SafetyAction
from core.storage.base import Store

if TYPE_CHECKING:
    from core.runtime.artifact_registry import ArtifactRegistry
from core.tools.base import BaseTool
from core.tools.builtins.filesystem import FilesystemTool
from core.tools.builtins.git import GitTool
from core.tools.builtins.ocr import OCRTool
from core.tools.builtins.screenshot import ScreenshotTool
from core.tools.builtins.shell import ShellTool
from core.tools.builtins.web import WebTool
from core.tools.model import ToolDefinition, ToolExecutionContext, ToolRequest, ToolResult
from core.tools.policy import PermissionPolicy
from core.tools.registry import ToolRegistry

logger = logging.getLogger("AutonomOS.ToolRuntime")


class ToolRuntime:
    """
    Central, security-enforcing Tool Runtime for AutonomOS.
    Governs all tool discovery, request validation, permission authorization,
    safety evaluation, checkpointing, confinement, execution, output bounds, and event audit logging.
    """

    def __init__(
        self,
        store: Store,
        artifact_registry: Optional[ArtifactRegistry] = None,
        event_logger: Optional[Callable[..., Event]] = None,
        checkpoint_manager: Optional[CheckpointManager] = None,
        safety_config: Optional[SafetyConfig] = None,
    ):
        self.store = store
        self.artifact_registry = artifact_registry
        self.event_logger = event_logger
        self.checkpoint_manager = checkpoint_manager or CheckpointManager(store)
        self.safety_config = safety_config or SafetyConfig()
        self.registry = ToolRegistry()
        self._lock = threading.RLock()

        # Register standard default builtins
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register the initial suite of built-in deterministic tools."""
        # Filesystem
        self.registry.register_tool(FilesystemTool("filesystem"))
        self.registry.register_tool(FilesystemTool("filesystem.read_file", action="read_file"))
        self.registry.register_tool(FilesystemTool("filesystem.write_file", action="write_file"))
        self.registry.register_tool(FilesystemTool("filesystem.create_file", action="create_file"))
        self.registry.register_tool(FilesystemTool("filesystem.delete_file", action="delete_file"))
        self.registry.register_tool(FilesystemTool("filesystem.list_directory", action="list_directory"))
        self.registry.register_tool(FilesystemTool("filesystem.file_exists", action="file_exists"))
        self.registry.register_tool(FilesystemTool("filesystem.stat_file", action="stat_file"))

        # Shell
        self.registry.register_tool(ShellTool("shell.execute"))

        # Git
        self.registry.register_tool(GitTool("git"))

        # Web
        self.registry.register_tool(WebTool("web"))
        self.registry.register_tool(WebTool("web.search"))
        self.registry.register_tool(WebTool("web.fetch"))

        # Screenshot & OCR
        self.registry.register_tool(ScreenshotTool("screenshot.capture"))
        self.registry.register_tool(OCRTool("ocr.extract_text"))

    def execute_request(self, request: ToolRequest) -> ToolResult:
        """
        Main entry point for executing a ToolRequest submitted by a worker.
        Flow:
        1. Log TOOL_REQUESTED event.
        2. Resolve project, task, worker, and tool.
        3. Validate tool arguments.
        4. Check and enforce worker permissions (TOOL_AUTHORIZED or TOOL_DENIED).
        5. Safety & Contextual Risk Evaluation (Stage 6).
        6. Automatic Checkpoint if required.
        7. Build ToolExecutionContext with workspace confinement.
        8. Execute tool with timeouts and bounds.
        9. Log outcome event (TOOL_COMPLETED, TOOL_FAILED, TOOL_TIMED_OUT).
        10. Return structured ToolResult.
        """
        start_time = time.perf_counter()

        # 1. Log TOOL_REQUESTED event
        req_evt = self._emit_event(
            event_type=EventType.TOOL_REQUESTED,
            payload={
                "request_id": request.request_id,
                "tool_id": request.tool_id,
                "arguments": self._sanitize_payload_args(request.arguments),
            },
            project_id=request.project_id,
            task_id=request.task_id,
            worker_id=request.worker_id,
            correlation_id=request.correlation_id or request.task_id,
            causation_id=request.causation_id,
        )
        causation_id = req_evt.event_id if req_evt else None

        # 2. Look up Project, Task, Worker, Tool
        project = self.store.get_project(request.project_id)
        if not project:
            return self._fail_result(
                request=request,
                status=ToolStatus.VALIDATION_ERROR,
                error=f"Project '{request.project_id}' not found.",
                start_time=start_time,
                causation_id=causation_id,
            )

        task = self.store.get_task(request.task_id)
        if not task:
            # Create a mock task if not found for loose direct tool testing
            task = Task(
                id=request.task_id,
                project_id=project.id,
                title=f"Ad-hoc task {request.task_id}",
                objective="Direct tool execution",
            )

        worker = self.store.get_worker(request.worker_id)
        if not worker:
            if request.worker_id in ("verifier", "system", "runtime"):
                worker = WorkerManifest(
                    id=request.worker_id,
                    name="System Verifier",
                    role="System",
                    description="Internal runtime/verifier execution context",
                    permissions=["*"],
                    tools=["*"],
                )
            else:
                return self._fail_result(
                    request=request,
                    status=ToolStatus.VALIDATION_ERROR,
                    error=f"Worker '{request.worker_id}' not found.",
                    start_time=start_time,
                    causation_id=causation_id,
                )

        try:
            tool = self.registry.get_tool(request.tool_id)
        except ToolNotFoundError as e:
            return self._fail_result(
                request=request,
                status=ToolStatus.VALIDATION_ERROR,
                error=str(e),
                start_time=start_time,
                causation_id=causation_id,
            )

        tool_def = tool.get_definition()

        # 3. Validate Arguments
        try:
            tool.validate_arguments(request.arguments)
        except ToolArgumentValidationError as e:
            return self._fail_result(
                request=request,
                status=ToolStatus.VALIDATION_ERROR,
                error=str(e),
                start_time=start_time,
                causation_id=causation_id,
            )

        # 4. Evaluate Permissions
        try:
            PermissionPolicy.evaluate_permissions(
                worker_permissions=worker.permissions,
                required_permissions=tool_def.permissions_required,
                worker_id=worker.id,
                tool_id=tool_def.id,
            )
            self._emit_event(
                event_type=EventType.TOOL_AUTHORIZED,
                payload={"tool_id": tool_def.id, "risk_level": tool_def.risk_level.value},
                project_id=project.id,
                task_id=request.task_id,
                worker_id=worker.id,
                correlation_id=request.task_id,
                causation_id=causation_id,
            )
        except ToolPermissionDeniedError as e:
            self._emit_event(
                event_type=EventType.TOOL_DENIED,
                payload={"tool_id": tool_def.id, "reason": str(e)},
                project_id=project.id,
                task_id=request.task_id,
                worker_id=worker.id,
                correlation_id=request.task_id,
                causation_id=causation_id,
            )
            return ToolResult(
                result_id=f"res-{uuid.uuid4().hex[:8]}",
                request_id=request.request_id,
                tool_id=tool_def.id,
                status=ToolStatus.DENIED,
                error_message=str(e),
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        # 5. Safety & Contextual Risk Evaluation (Stage 6)
        safety_decision = SafetyEvaluator.evaluate_request(
            request=request,
            tool_def=tool_def,
            project=project,
            worker=worker,
            task=task,
            config=self.safety_config,
        )

        self._emit_event(
            event_type=EventType.SAFETY_CHECK_REQUESTED,
            payload={
                "tool_id": tool_def.id,
                "risk_level": safety_decision.risk_level.value,
                "decision": safety_decision.decision.value,
            },
            project_id=project.id,
            task_id=request.task_id,
            worker_id=worker.id,
            correlation_id=request.task_id,
            causation_id=causation_id,
        )

        if safety_decision.decision == SafetyAction.DENY:
            self._emit_event(
                event_type=EventType.SAFETY_DENIED,
                payload={"tool_id": tool_def.id, "reasons": safety_decision.reasons},
                project_id=project.id,
                task_id=request.task_id,
                worker_id=worker.id,
                correlation_id=request.task_id,
                causation_id=causation_id,
            )
            return ToolResult(
                result_id=f"res-{uuid.uuid4().hex[:8]}",
                request_id=request.request_id,
                tool_id=tool_def.id,
                status=ToolStatus.DENIED,
                error_message=f"Safety Policy Denied: {'; '.join(safety_decision.reasons)}",
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        if safety_decision.decision in (SafetyAction.REQUIRES_APPROVAL, SafetyAction.ESCALATE):
            self._emit_event(
                event_type=EventType.SAFETY_ESCALATED,
                payload={"tool_id": tool_def.id, "reasons": safety_decision.reasons},
                project_id=project.id,
                task_id=request.task_id,
                worker_id=worker.id,
                correlation_id=request.task_id,
                causation_id=causation_id,
            )
            return ToolResult(
                result_id=f"res-{uuid.uuid4().hex[:8]}",
                request_id=request.request_id,
                tool_id=tool_def.id,
                status=ToolStatus.DENIED,
                error_message=f"Safety Policy Requires Human Approval: {'; '.join(safety_decision.reasons)}",
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        self._emit_event(
            event_type=EventType.SAFETY_ALLOWED,
            payload={"tool_id": tool_def.id, "risk_level": safety_decision.risk_level.value},
            project_id=project.id,
            task_id=request.task_id,
            worker_id=worker.id,
            correlation_id=request.task_id,
            causation_id=causation_id,
        )

        # 6. Automatic Checkpoint Creation if required
        if safety_decision.required_checkpoint and self.checkpoint_manager:
            active_ckpt = self.checkpoint_manager.get_active_checkpoint_for_task(request.task_id)
            if not active_ckpt:
                new_ckpt = self.checkpoint_manager.create_checkpoint(
                    project_id=project.id,
                    task_id=request.task_id,
                    worker_id=worker.id,
                )
                self._emit_event(
                    event_type=EventType.CHECKPOINT_CREATED,
                    payload={"checkpoint_id": new_ckpt.id, "checkpoint_type": new_ckpt.checkpoint_type.value},
                    project_id=project.id,
                    task_id=request.task_id,
                    worker_id=worker.id,
                    correlation_id=request.task_id,
                    causation_id=causation_id,
                )

        # 7. Build Execution Context
        context = ToolExecutionContext(
            project_id=project.id,
            workspace_root=project.root_path,
            task_id=request.task_id,
            worker_id=worker.id,
            permissions=worker.permissions,
            timeout_seconds=request.timeout_seconds or tool_def.timeout_seconds,
            output_limit_bytes=tool_def.output_limit_bytes,
            artifact_registry=self.artifact_registry,
            store=self.store,
        )

        start_evt = self._emit_event(
            event_type=EventType.TOOL_STARTED,
            payload={"tool_id": tool_def.id, "timeout": context.timeout_seconds},
            project_id=project.id,
            task_id=request.task_id,
            worker_id=worker.id,
            correlation_id=request.task_id,
            causation_id=causation_id,
        )
        tool_causation_id = start_evt.event_id if start_evt else causation_id

        # 8. Execute Tool
        try:
            result = tool.execute(context, request.arguments)
            result.request_id = request.request_id
            result.tool_id = tool_def.id
        except Exception as e:
            result = ToolResult(
                result_id=f"res-{uuid.uuid4().hex[:8]}",
                request_id=request.request_id,
                tool_id=tool_def.id,
                status=ToolStatus.FAILED,
                error_message=f"Tool unhandled execution exception: {str(e)}",
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        # 9. Emit Outcome Event
        outcome_event_type = EventType.TOOL_COMPLETED
        if result.status == ToolStatus.TIMEOUT:
            outcome_event_type = EventType.TOOL_TIMED_OUT
        elif result.status == ToolStatus.FAILED:
            outcome_event_type = EventType.TOOL_FAILED

        self._emit_event(
            event_type=outcome_event_type,
            payload={
                "tool_id": tool_def.id,
                "status": result.status.value,
                "duration_ms": result.duration_ms,
                "is_truncated": result.is_truncated,
                "artifacts_count": len(result.artifacts_created),
                "error": result.error_message,
            },
            project_id=project.id,
            task_id=request.task_id,
            worker_id=worker.id,
            correlation_id=request.task_id,
            causation_id=tool_causation_id,
        )

        return result

    def _fail_result(
        self,
        request: ToolRequest,
        status: ToolStatus,
        error: str,
        start_time: float,
        causation_id: Optional[str],
    ) -> ToolResult:
        self._emit_event(
            event_type=EventType.TOOL_FAILED,
            payload={"tool_id": request.tool_id, "status": status.value, "error": error},
            project_id=request.project_id,
            task_id=request.task_id,
            worker_id=request.worker_id,
            correlation_id=request.task_id,
            causation_id=causation_id,
        )
        return ToolResult(
            result_id=f"res-{uuid.uuid4().hex[:8]}",
            request_id=request.request_id,
            tool_id=request.tool_id,
            status=status,
            error_message=error,
            duration_ms=(time.perf_counter() - start_time) * 1000.0,
        )

    def _emit_event(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> Optional[Event]:
        if self.event_logger:
            return self.event_logger(
                event_type=event_type,
                payload=payload,
                source=EventSource.RUNTIME,
                project_id=project_id,
                task_id=task_id,
                worker_id=worker_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        return None

    def _sanitize_payload_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """Redact secrets and truncate giant arguments in event payloads."""
        clean = {}
        for k, v in args.items():
            if any(s in k.upper() for s in ("SECRET", "PASSWORD", "KEY", "TOKEN")):
                clean[k] = "[REDACTED]"
            elif isinstance(v, str) and len(v) > 200:
                clean[k] = v[:200] + "... [truncated in event payload]"
            else:
                clean[k] = v
        return clean

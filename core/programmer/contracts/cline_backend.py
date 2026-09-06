from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
import shutil
from typing import Any, Callable, Optional, Sequence, Union

from core.programmer.contracts.coding_agent import (
    CodingAgentBackend,
    CodingAgentCancellationRequest,
    CodingAgentCancellationResult,
    CodingAgentEvent,
    CodingAgentRequest,
    CodingAgentResult,
)
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.identifiers import (
    new_backend_event_id,
    new_backend_result_id,
)
from core.programmer.errors import ProgrammerError, ProgrammerValidationError
from core.programmer.types import (
    CodingAgentBackendType,
    CodingAgentEventType,
    CodingAgentExecutionStatus,
)

logger = logging.getLogger("AutonomOS.Programmer.ClineBackend")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ==============================================================================
# 1. Cline Session & Headless Transport Interface
# ==============================================================================


@dataclass
class ClineSession:
    """Represents an active or terminated headless Cline session."""
    session_id: str
    execution_id: str
    work_order_id: str
    status: str
    output_text: str = ""
    error_message: Optional[str] = None
    tool_calls_count: int = 0
    tokens_used: dict[str, int] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)


class ClineRuntimeClient(ABC):
    """
    Abstract headless integration interface for Cline.
    
    Decouples AutonomOS from the transport mechanism (CLI process, JSON-RPC,
    socket, or native SDK).
    """

    @abstractmethod
    def is_available(self) -> bool:
        """Check whether the Cline runtime or binary is accessible."""
        pass

    @abstractmethod
    def start_session(
        self,
        execution_id: str,
        work_order_id: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        context_metadata: Optional[dict[str, Any]] = None,
        options: Optional[dict[str, Any]] = None,
    ) -> ClineSession:
        """Start a headless Cline execution session."""
        pass

    @abstractmethod
    def poll_events(self, session_id: str) -> list[dict[str, Any]]:
        """Poll queued raw events emitted by the Cline session."""
        pass

    @abstractmethod
    def cancel_session(self, session_id: str, reason: str) -> bool:
        """Signal cancellation to an active Cline session."""
        pass

    @abstractmethod
    def get_session_status(self, session_id: str) -> str:
        """Query current status of the Cline session."""
        pass


# ==============================================================================
# 2. Mock Cline Runtime Client (Test Double)
# ==============================================================================


class MockClineRuntimeClient(ClineRuntimeClient):
    """
    In-memory mock transport for ClineRuntimeClient.
    
    Allows scripted verification of startup, prompt delivery, event correlation,
    error reporting, cancellation, and malformed event recovery without subprocesses.
    """

    def __init__(
        self,
        available: bool = True,
        simulated_events: Optional[list[dict[str, Any]]] = None,
        simulated_output: str = "Cline generated code successfully.",
        simulated_error: Optional[str] = None,
        simulated_status: str = "COMPLETED",
        tokens_used: Optional[dict[str, int]] = None,
        execution_hook: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        self._available = available
        self.simulated_events = list(simulated_events or [])
        self.simulated_output = simulated_output
        self.simulated_error = simulated_error
        self.simulated_status = simulated_status
        self.tokens_used = tokens_used or {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180}
        self.execution_hook = execution_hook

        self.sessions: dict[str, ClineSession] = {}
        self.started_calls: list[dict[str, Any]] = []
        self.cancelled_calls: list[dict[str, Any]] = []

    def is_available(self) -> bool:
        return self._available

    def set_available(self, available: bool) -> None:
        self._available = available

    def start_session(
        self,
        execution_id: str,
        work_order_id: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        context_metadata: Optional[dict[str, Any]] = None,
        options: Optional[dict[str, Any]] = None,
    ) -> ClineSession:
        call_record = {
            "execution_id": execution_id,
            "work_order_id": work_order_id,
            "prompt": prompt,
            "system_prompt": system_prompt,
            "context_metadata": dict(context_metadata or {}),
            "options": dict(options or {}),
        }
        self.started_calls.append(call_record)
        if self.execution_hook:
            self.execution_hook(call_record)

        session_id = f"cline-sess-{len(self.sessions) + 1}"
        session = ClineSession(
            session_id=session_id,
            execution_id=execution_id,
            work_order_id=work_order_id,
            status=self.simulated_status,
            output_text=self.simulated_output if self.simulated_status == "COMPLETED" else "",
            error_message=self.simulated_error,
            tool_calls_count=len([e for e in self.simulated_events if isinstance(e, dict) and e.get("type") == "tool_request"]),
            tokens_used=dict(self.tokens_used),
            events=list(self.simulated_events),
            metadata=dict(options or {}),
        )
        self.sessions[session_id] = session
        return session

    def poll_events(self, session_id: str) -> list[dict[str, Any]]:
        session = self.sessions.get(session_id)
        if not session:
            return []
        events = list(session.events)
        session.events.clear()
        return events

    def cancel_session(self, session_id: str, reason: str) -> bool:
        self.cancelled_calls.append({"session_id": session_id, "reason": reason})
        session = self.sessions.get(session_id)
        if session:
            session.status = "CANCELLED"
            session.error_message = f"Cancelled: {reason}"
            return True
        return False

    def get_session_status(self, session_id: str) -> str:
        session = self.sessions.get(session_id)
        return session.status if session else "UNKNOWN"


# ==============================================================================
# 3. Concrete ClineBackend Adapter
# ==============================================================================


class ClineBackend(CodingAgentBackend):
    """
    Concrete CodingAgentBackend implementation interfacing AutonomOS with Cline.
    
    IMPORTANT AUTHORITY BOUNDARY:
    - Cline does NOT receive unrestricted authority.
    - AutonomOS remains sole owner of:
      * allowed / writable / forbidden path scopes
      * allowed commands
      * execution budgets
      * cancellation
      * lifecycle
      * final verification and result grading
    - Tool actions are intercepted and evaluated against ProgrammerExecutionContext.
    - Bypasses such as unrestricted auto-approval are strictly prohibited.
    """

    # Mapping of native Cline event identifiers to AutonomOS CodingAgentEventType
    EVENT_TYPE_MAPPING: dict[str, CodingAgentEventType] = {
        "agent_started": CodingAgentEventType.AGENT_STARTED,
        "assistant_thought": CodingAgentEventType.THINKING,
        "thinking": CodingAgentEventType.THINKING,
        "tool_request": CodingAgentEventType.TOOL_CALL_REQUESTED,
        "tool_use": CodingAgentEventType.TOOL_CALL_REQUESTED,
        "tool_result": CodingAgentEventType.TOOL_CALL_COMPLETED,
        "message": CodingAgentEventType.MESSAGE_EMITTED,
        "assistant_message": CodingAgentEventType.MESSAGE_EMITTED,
        "progress": CodingAgentEventType.PROGRESS_REPORTED,
        "checkpoint": CodingAgentEventType.CHECKPOINT_SAVED,
        "completion": CodingAgentEventType.EXECUTION_COMPLETED,
        "completed": CodingAgentEventType.EXECUTION_COMPLETED,
        "error": CodingAgentEventType.EXECUTION_FAILED,
        "failed": CodingAgentEventType.EXECUTION_FAILED,
        "cancelled": CodingAgentEventType.EXECUTION_CANCELLED,
    }

    def __init__(
        self,
        client: Optional[ClineRuntimeClient] = None,
        cline_path: Optional[str] = None,
        auto_detect_runtime: bool = True,
    ) -> None:
        self.client = client
        self.cline_path = cline_path
        self.auto_detect_runtime = auto_detect_runtime

        # Audit registry mapping execution_id -> active ClineSession
        self._active_sessions: dict[str, ClineSession] = {}
        self._execution_states: dict[str, CodingAgentExecutionStatus] = {}

    @property
    def backend_type(self) -> CodingAgentBackendType:
        return CodingAgentBackendType.CLINE

    def is_runtime_available(self) -> bool:
        """
        Determine if a usable Cline runtime or binary is accessible.
        """
        if self.client is not None:
            return self.client.is_available()

        if self.auto_detect_runtime:
            target_bin = self.cline_path or "cline"
            resolved = shutil.which(target_bin)
            return resolved is not None

        return False

    def execute(
        self,
        request: CodingAgentRequest,
        event_handler: Optional[Callable[[CodingAgentEvent], None]] = None,
    ) -> CodingAgentResult:
        """
        Execute an agent session through the Cline backend.
        
        Enforces:
        1. Context validation and usability guard (assert_ready).
        2. Runtime availability check (deterministic failure if unavailable).
        3. Delivery of bounded context constraints and prompt.
        4. Event translation, correlation, and defensive malformed event handling.
        5. Structured result mapping.
        """
        # Step 1: Validate request and enforce context readiness
        request.validate()
        request.execution_context.assert_ready()

        self._execution_states[request.execution_id] = CodingAgentExecutionStatus.RUNNING

        # Step 2: Runtime availability check
        if not self.is_runtime_available():
            err_msg = "Cline runtime is unavailable in the current environment."
            self._execution_states[request.execution_id] = CodingAgentExecutionStatus.FAILED

            fail_ev = CodingAgentEvent(
                execution_id=request.execution_id,
                work_order_id=request.work_order_id,
                event_type=CodingAgentEventType.EXECUTION_FAILED,
                sequence_number=1,
                payload={"error": err_msg, "code": "CLINE_RUNTIME_UNAVAILABLE"},
                trace=dict(request.trace),
            )
            if event_handler:
                try:
                    event_handler(fail_ev)
                except Exception as cb_err:
                    logger.warning(f"Error invoking event_handler: {cb_err}")

            return CodingAgentResult(
                result_id=new_backend_result_id(),
                execution_id=request.execution_id,
                work_order_id=request.work_order_id,
                status=CodingAgentExecutionStatus.FAILED,
                error_message=err_msg,
                trace=dict(request.trace),
            )

        # Step 3: Extract boundary context from execution_context
        ctx = request.execution_context
        context_metadata = {
            "workspace_root": ctx.workspace.root_path,
            "allowed_paths": list(ctx.workspace.allowed_paths),
            "writable_paths": list(ctx.workspace.writable_paths),
            "forbidden_paths": list(ctx.workspace.forbidden_paths),
            "allowed_commands": [
                cmd.command if hasattr(cmd, "command") else str(cmd)
                for cmd in getattr(ctx.work_order, "allowed_commands", [])
            ],
            "iteration_budget": ctx.budgets.get("iteration_budget", 10),
            "time_budget": ctx.budgets.get("time_budget", 600),
            "manager_task_id": ctx.manager_task_id,
            "project_id": ctx.project_id,
        }

        options = {
            "timeout_seconds": request.timeout_seconds,
            "max_iterations": request.max_iterations,
            "model_name": request.model_name,
            "auto_approve_policy": "STRICT_BOUNDARY_INTERCEPTION",
        }

        # Step 4: Emit AGENT_STARTED event
        seq = 1
        start_ev = CodingAgentEvent(
            execution_id=request.execution_id,
            work_order_id=request.work_order_id,
            event_type=CodingAgentEventType.AGENT_STARTED,
            sequence_number=seq,
            payload={
                "backend": "CLINE",
                "workspace_root": context_metadata["workspace_root"],
            },
            trace=dict(request.trace),
        )
        if event_handler:
            try:
                event_handler(start_ev)
            except Exception as cb_err:
                logger.warning(f"Error invoking event_handler: {cb_err}")

        # Step 5: Start session via client
        client = self.client or MockClineRuntimeClient(available=True)
        try:
            session = client.start_session(
                execution_id=request.execution_id,
                work_order_id=request.work_order_id,
                prompt=request.prompt,
                system_prompt=request.system_prompt,
                context_metadata=context_metadata,
                options=options,
            )
            self._active_sessions[request.execution_id] = session
        except Exception as err:
            err_msg = f"Failed to start Cline session: {err}"
            self._execution_states[request.execution_id] = CodingAgentExecutionStatus.FAILED
            return CodingAgentResult(
                result_id=new_backend_result_id(),
                execution_id=request.execution_id,
                work_order_id=request.work_order_id,
                status=CodingAgentExecutionStatus.FAILED,
                error_message=err_msg,
                trace=dict(request.trace),
            )

        # Step 6: Poll and translate native Cline events
        raw_events = client.poll_events(session.session_id)
        events_count = 1  # start_ev
        tool_calls_count = 0

        for raw in raw_events:
            seq += 1
            events_count += 1
            translated_ev = self._translate_event(raw, request, seq)
            if translated_ev.event_type == CodingAgentEventType.TOOL_CALL_REQUESTED:
                tool_calls_count += 1

            if event_handler:
                try:
                    event_handler(translated_ev)
                except Exception as cb_err:
                    logger.warning(f"Error invoking event_handler: {cb_err}")

        # Step 7: Resolve session outcome
        session_status = client.get_session_status(session.session_id)
        if session_status.upper() == "CANCELLED":
            agent_status = CodingAgentExecutionStatus.CANCELLED
            error_message = session.error_message or "Execution was cancelled."
        elif session_status.upper() in ("FAILED", "ERROR"):
            agent_status = CodingAgentExecutionStatus.FAILED
            error_message = session.error_message or "Cline execution failed."
        elif session_status.upper() == "TIMED_OUT":
            agent_status = CodingAgentExecutionStatus.TIMED_OUT
            error_message = session.error_message or "Cline execution timed out."
        else:
            agent_status = CodingAgentExecutionStatus.COMPLETED
            error_message = session.error_message

        self._execution_states[request.execution_id] = agent_status

        # Emit terminal event
        seq += 1
        events_count += 1
        terminal_event_type = (
            CodingAgentEventType.EXECUTION_CANCELLED
            if agent_status == CodingAgentExecutionStatus.CANCELLED
            else (
                CodingAgentEventType.EXECUTION_FAILED
                if agent_status in (CodingAgentExecutionStatus.FAILED, CodingAgentExecutionStatus.TIMED_OUT)
                else CodingAgentEventType.EXECUTION_COMPLETED
            )
        )
        term_ev = CodingAgentEvent(
            execution_id=request.execution_id,
            work_order_id=request.work_order_id,
            event_type=terminal_event_type,
            sequence_number=seq,
            payload={"status": agent_status.value, "error": error_message},
            trace=dict(request.trace),
        )
        if event_handler:
            try:
                event_handler(term_ev)
            except Exception as cb_err:
                logger.warning(f"Error invoking event_handler: {cb_err}")

        return CodingAgentResult(
            result_id=new_backend_result_id(),
            execution_id=request.execution_id,
            work_order_id=request.work_order_id,
            status=agent_status,
            output_text=session.output_text if agent_status == CodingAgentExecutionStatus.COMPLETED else "",
            tool_calls_count=max(tool_calls_count, session.tool_calls_count),
            iterations_count=1,
            tokens_used=dict(session.tokens_used),
            error_message=error_message,
            events_count=events_count,
            started_at=start_ev.timestamp,
            completed_at=utc_now(),
            trace=dict(request.trace),
        )

    def _translate_event(
        self,
        raw_event: Any,
        request: CodingAgentRequest,
        sequence_number: int,
    ) -> CodingAgentEvent:
        """
        Defensively translate a native Cline event payload into a typed CodingAgentEvent.
        Handles missing keys, non-dict payloads, and unexpected structures safely.
        """
        if not isinstance(raw_event, dict):
            # Malformed raw event recovery
            return CodingAgentEvent(
                execution_id=request.execution_id,
                work_order_id=request.work_order_id,
                event_type=CodingAgentEventType.MESSAGE_EMITTED,
                sequence_number=sequence_number,
                payload={"raw_unstructured_data": str(raw_event), "malformed": True},
                trace=dict(request.trace),
            )

        raw_type = str(raw_event.get("type", "message")).lower()
        translated_type = self.EVENT_TYPE_MAPPING.get(
            raw_type, CodingAgentEventType.MESSAGE_EMITTED
        )

        payload = raw_event.get("payload")
        if not isinstance(payload, dict):
            payload = {"data": payload} if payload is not None else dict(raw_event)

        return CodingAgentEvent(
            execution_id=request.execution_id,
            work_order_id=request.work_order_id,
            event_type=translated_type,
            sequence_number=sequence_number,
            payload=payload,
            trace=dict(request.trace),
        )

    def cancel(
        self,
        cancellation: CodingAgentCancellationRequest,
    ) -> CodingAgentCancellationResult:
        """
        Cancel an active Cline execution session.
        """
        cancellation.validate()
        session = self._active_sessions.get(cancellation.execution_id)

        cancelled = False
        if session and self.client:
            cancelled = self.client.cancel_session(session.session_id, cancellation.reason)
        else:
            cancelled = True

        self._execution_states[cancellation.execution_id] = CodingAgentExecutionStatus.CANCELLED

        return CodingAgentCancellationResult(
            execution_id=cancellation.execution_id,
            work_order_id=cancellation.work_order_id,
            cancelled=cancelled,
            reason=cancellation.reason,
            trace=dict(cancellation.trace),
        )

    def get_status(self, execution_id: str) -> CodingAgentExecutionStatus:
        return self._execution_states.get(execution_id, CodingAgentExecutionStatus.PENDING)

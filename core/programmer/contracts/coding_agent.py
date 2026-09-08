from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Callable, Optional, Union

from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.identifiers import (
    new_backend_event_id,
    new_backend_request_id,
    new_backend_result_id,
    validate_backend_request_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.programmer.errors import (
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    CodingAgentBackendType,
    CodingAgentEventType,
    CodingAgentExecutionStatus,
    ExecutionContextStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ==============================================================================
# 1. Request Contract
# ==============================================================================


@dataclass
class CodingAgentRequest:
    """
    Authoritative request contract issued by Programmer to a CodingAgentBackend.
    
    Architectural Constraints:
    - Programmer remains authoritative over task, work order, and execution envelope.
    - Backend receives ONLY authorized execution context (ProgrammerExecutionContext).
    - Backend does NOT own boundary authority, validation, or scope expansion.
    """
    execution_id: str
    work_order_id: str
    prompt: str
    execution_context: ProgrammerExecutionContext
    backend_type: CodingAgentBackendType = CodingAgentBackendType.MOCK
    request_id: str = field(default_factory=new_backend_request_id)
    system_prompt: Optional[str] = None
    model_name: Optional[str] = None
    timeout_seconds: Optional[int] = None
    max_iterations: Optional[int] = None
    created_at: str = field(default_factory=utc_now)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.backend_type, str):
            try:
                self.backend_type = CodingAgentBackendType(self.backend_type.upper())
            except (ValueError, TypeError):
                self.backend_type = CodingAgentBackendType.CUSTOM

        # Forward execution context lineage into request trace
        if self.execution_context and not self.trace:
            self.trace = {
                "execution_id": self.execution_id,
                "work_order_id": self.work_order_id,
                "workspace_id": getattr(self.execution_context, "workspace_id", ""),
                "project_id": getattr(self.execution_context, "project_id", ""),
                "manager_task_id": getattr(self.execution_context, "manager_task_id", ""),
            }

    def validate(self) -> None:
        """
        Validate structural integrity and causal lineage.
        Raises ProgrammerValidationError or ProgrammerLineageError if invalid.
        """
        validate_backend_request_id(self.request_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)

        if not self.prompt or not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ProgrammerValidationError("CodingAgentRequest must specify a non-empty prompt.")

        if not isinstance(self.execution_context, ProgrammerExecutionContext):
            raise ProgrammerValidationError(
                f"CodingAgentRequest requires a valid ProgrammerExecutionContext, got {type(self.execution_context).__name__}."
            )

        # Context usability guard: context MUST be in READY status
        if not self.execution_context.is_ready():
            raise ProgrammerError(
                f"CodingAgentRequest rejected: execution context is in '{self.execution_context.status.value}' state and cannot be used for agent execution.",
                code="FAILED_CONTEXT_ERROR",
            )

        # Lineage consistency check between request and context
        if self.execution_context.execution_id != self.execution_id:
            raise ProgrammerLineageError(
                f"Lineage mismatch: request execution_id is '{self.execution_id}', but execution_context is '{self.execution_context.execution_id}'."
            )
        if self.execution_context.work_order_id != self.work_order_id:
            raise ProgrammerLineageError(
                f"Lineage mismatch: request work_order_id is '{self.work_order_id}', but execution_context is '{self.execution_context.work_order_id}'."
            )

        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ProgrammerValidationError(
                f"timeout_seconds must be positive, got {self.timeout_seconds}."
            )
        if self.max_iterations is not None and self.max_iterations <= 0:
            raise ProgrammerValidationError(
                f"max_iterations must be positive, got {self.max_iterations}."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "prompt": self.prompt,
            "backend_type": self.backend_type.value if isinstance(self.backend_type, CodingAgentBackendType) else str(self.backend_type),
            "system_prompt": self.system_prompt,
            "model_name": self.model_name,
            "timeout_seconds": self.timeout_seconds,
            "max_iterations": self.max_iterations,
            "execution_context": self.execution_context.to_dict() if self.execution_context else None,
            "created_at": self.created_at,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        execution_context: Optional[ProgrammerExecutionContext] = None,
    ) -> CodingAgentRequest:
        ctx = execution_context
        if ctx is None and data.get("execution_context"):
            ctx = ProgrammerExecutionContext.from_dict(data["execution_context"])

        return cls(
            request_id=data.get("request_id", new_backend_request_id()),
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            prompt=data["prompt"],
            execution_context=ctx,  # type: ignore[arg-type]
            backend_type=CodingAgentBackendType(str(data.get("backend_type", "MOCK")).upper()),
            system_prompt=data.get("system_prompt"),
            model_name=data.get("model_name"),
            timeout_seconds=data.get("timeout_seconds"),
            max_iterations=data.get("max_iterations"),
            created_at=data.get("created_at", utc_now()),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )


# ==============================================================================
# 2. Event Contract
# ==============================================================================


@dataclass
class CodingAgentEvent:
    """
    Structured domain event emitted by a CodingAgentBackend during execution.
    Allows Programmer to observe real-time progress, tool requests, and thinking steps.
    """
    execution_id: str
    work_order_id: str
    event_type: CodingAgentEventType
    event_id: str = field(default_factory=new_backend_event_id)
    payload: dict[str, Any] = field(default_factory=dict)
    sequence_number: int = 0
    timestamp: str = field(default_factory=utc_now)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.event_type, str):
            try:
                self.event_type = CodingAgentEventType(self.event_type.upper())
            except (ValueError, TypeError):
                pass

        if not self.trace:
            self.trace = {
                "execution_id": self.execution_id,
                "work_order_id": self.work_order_id,
            }

    def validate(self) -> None:
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        if not isinstance(self.event_type, CodingAgentEventType):
            raise ProgrammerValidationError(f"Invalid event_type: {self.event_type}")
        if self.sequence_number < 0:
            raise ProgrammerValidationError(f"sequence_number must be non-negative, got {self.sequence_number}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "event_type": self.event_type.value if hasattr(self.event_type, "value") else str(self.event_type),
            "payload": dict(self.payload),
            "sequence_number": self.sequence_number,
            "timestamp": self.timestamp,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodingAgentEvent:
        return cls(
            event_id=data.get("event_id", new_backend_event_id()),
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            event_type=CodingAgentEventType(str(data["event_type"]).upper()),
            payload=dict(data.get("payload", {})),
            sequence_number=int(data.get("sequence_number", 0)),
            timestamp=data.get("timestamp", utc_now()),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )


# ==============================================================================
# 3. Result Contract
# ==============================================================================


@dataclass
class CodingAgentResult:
    """
    Structured outcome produced by a CodingAgentBackend upon session completion or failure.
    
    Architectural Constraints:
    - This is the backend execution report.
    - Programmer uses this report alongside test and validation evidence to synthesize
      the final authoritative ProgrammerResult returned to Manager.
    - The backend does NOT declare final task success.
    """
    execution_id: str
    work_order_id: str
    status: CodingAgentExecutionStatus = CodingAgentExecutionStatus.COMPLETED
    result_id: str = field(default_factory=new_backend_result_id)
    output_text: str = ""
    tool_calls_count: int = 0
    iterations_count: int = 0
    tokens_used: dict[str, int] = field(default_factory=dict)
    error_message: Optional[str] = None
    raw_output: dict[str, Any] = field(default_factory=dict)
    events_count: int = 0
    started_at: Optional[str] = None
    completed_at: str = field(default_factory=utc_now)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = CodingAgentExecutionStatus(self.status.upper())
            except (ValueError, TypeError):
                self.status = CodingAgentExecutionStatus.FAILED

        if not self.trace:
            self.trace = {
                "execution_id": self.execution_id,
                "work_order_id": self.work_order_id,
            }

    def is_successful(self) -> bool:
        """Check whether the agent execution finished normally without failure status."""
        return self.status == CodingAgentExecutionStatus.COMPLETED and not self.error_message

    def is_cancelled(self) -> bool:
        """Check whether the agent execution was cancelled."""
        return self.status == CodingAgentExecutionStatus.CANCELLED

    def is_failed(self) -> bool:
        """Check whether the agent execution failed or timed out."""
        return self.status in (CodingAgentExecutionStatus.FAILED, CodingAgentExecutionStatus.TIMED_OUT)

    def validate(self) -> None:
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        if not isinstance(self.status, CodingAgentExecutionStatus):
            raise ProgrammerValidationError(f"Invalid status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "status": self.status.value if isinstance(self.status, CodingAgentExecutionStatus) else str(self.status),
            "output_text": self.output_text,
            "tool_calls_count": self.tool_calls_count,
            "iterations_count": self.iterations_count,
            "tokens_used": dict(self.tokens_used),
            "error_message": self.error_message,
            "raw_output": dict(self.raw_output),
            "events_count": self.events_count,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodingAgentResult:
        return cls(
            result_id=data.get("result_id", new_backend_result_id()),
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            status=CodingAgentExecutionStatus(str(data.get("status", "COMPLETED")).upper()),
            output_text=str(data.get("output_text", "")),
            tool_calls_count=int(data.get("tool_calls_count", 0)),
            iterations_count=int(data.get("iterations_count", 0)),
            tokens_used=dict(data.get("tokens_used", {})),
            error_message=data.get("error_message"),
            raw_output=dict(data.get("raw_output", {})),
            events_count=int(data.get("events_count", 0)),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at", utc_now()),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )


# ==============================================================================
# 4. Cancellation Contract
# ==============================================================================


@dataclass
class CodingAgentCancellationRequest:
    """Handshake request sent to cancel an active coding agent session."""
    execution_id: str
    work_order_id: str
    reason: str
    requested_at: str = field(default_factory=utc_now)
    grace_period_seconds: int = 5
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        if not self.reason or not isinstance(self.reason, str) or not self.reason.strip():
            raise ProgrammerValidationError("Cancellation reason must be a non-empty string.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "reason": self.reason,
            "requested_at": self.requested_at,
            "grace_period_seconds": self.grace_period_seconds,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodingAgentCancellationRequest:
        return cls(
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            reason=data["reason"],
            requested_at=data.get("requested_at", utc_now()),
            grace_period_seconds=int(data.get("grace_period_seconds", 5)),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class CodingAgentCancellationResult:
    """Result of a cancellation request to a coding agent backend."""
    execution_id: str
    work_order_id: str
    cancelled: bool
    reason: str
    cancelled_at: str = field(default_factory=utc_now)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "cancelled": self.cancelled,
            "reason": self.reason,
            "cancelled_at": self.cancelled_at,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodingAgentCancellationResult:
        return cls(
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            cancelled=bool(data.get("cancelled", False)),
            reason=str(data.get("reason", "")),
            cancelled_at=data.get("cancelled_at", utc_now()),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )


# ==============================================================================
# 5. Authoritative Backend Interface
# ==============================================================================


class CodingAgentBackend(ABC):
    """
    Authoritative backend contract through which Programmer invokes an external coding agent.
    
    Architectural Constraints:
    - Decoupled from specific engines (supports Cline, Codex, Claude Code, Custom).
    - Programmer remains authoritative.
    - The backend does NOT own:
      * Manager authority
      * WorkOrder validation
      * Filesystem authorization
      * Command authorization
      * Scope expansion
      * Acceptance criteria
      * Final success determination
    """

    @property
    @abstractmethod
    def backend_type(self) -> CodingAgentBackendType:
        """The specific engine type of this backend implementation."""
        pass

    @abstractmethod
    def execute(
        self,
        request: CodingAgentRequest,
        event_handler: Optional[Callable[[CodingAgentEvent], None]] = None,
    ) -> CodingAgentResult:
        """
        Execute an agent session within the authorized execution context.
        
        Args:
            request: Validated CodingAgentRequest binding context, prompt, and limits.
            event_handler: Optional callback to receive streaming CodingAgentEvent instances.
            
        Returns:
            CodingAgentResult detailing session outcome and metrics.
        """
        pass

    @abstractmethod
    def cancel(
        self,
        cancellation: CodingAgentCancellationRequest,
    ) -> CodingAgentCancellationResult:
        """
        Request cancellation of an active or pending agent execution.
        """
        pass

    @abstractmethod
    def get_status(self, execution_id: str) -> CodingAgentExecutionStatus:
        """
        Query current execution status of a session.
        """
        pass


# ==============================================================================
# 6. Test Double: MockCodingAgentBackend
# ==============================================================================


class MockCodingAgentBackend(CodingAgentBackend):
    """
    Deterministic in-memory test double for CodingAgentBackend.
    Used for unit testing, contract verification, and simulation without real model or process execution.
    """

    def __init__(
        self,
        backend_type: CodingAgentBackendType = CodingAgentBackendType.MOCK,
        simulated_output: str = "Implementation completed successfully.",
        simulated_status: CodingAgentExecutionStatus = CodingAgentExecutionStatus.COMPLETED,
        simulated_error: Optional[str] = None,
        simulated_events: Optional[list[dict[str, Any]]] = None,
        execution_hook: Optional[Callable[[CodingAgentRequest], None]] = None,
    ) -> None:
        self._backend_type = backend_type
        self.simulated_output = simulated_output
        self.simulated_status = simulated_status
        self.simulated_error = simulated_error
        self.simulated_events = list(simulated_events or [])
        self.execution_hook = execution_hook

        # Audit history
        self.received_requests: list[CodingAgentRequest] = []
        self.received_cancellations: list[CodingAgentCancellationRequest] = []
        self.emitted_events: list[CodingAgentEvent] = []
        self._execution_states: dict[str, CodingAgentExecutionStatus] = {}

    @property
    def backend_type(self) -> CodingAgentBackendType:
        return self._backend_type

    def execute(
        self,
        request: CodingAgentRequest,
        event_handler: Optional[Callable[[CodingAgentEvent], None]] = None,
    ) -> CodingAgentResult:
        # Validate request integrity & lineage
        request.validate()
        self.received_requests.append(request)

        is_already_cancelled = (
            self._execution_states.get(request.execution_id) == CodingAgentExecutionStatus.CANCELLED
        )
        if not is_already_cancelled:
            self._execution_states[request.execution_id] = CodingAgentExecutionStatus.RUNNING

        # Emit initial start event
        start_event = CodingAgentEvent(
            execution_id=request.execution_id,
            work_order_id=request.work_order_id,
            event_type=CodingAgentEventType.AGENT_STARTED,
            sequence_number=1,
            payload={"backend_type": self.backend_type.value},
            trace=dict(request.trace),
        )
        self.emitted_events.append(start_event)
        if event_handler:
            event_handler(start_event)

        # Optional execution hook (for simulated capability operations)
        if self.execution_hook:
            self.execution_hook(request)

        # Emit scripted events
        seq = 2
        for ev_data in self.simulated_events:
            raw_t = ev_data.get("type", CodingAgentEventType.PROGRESS_REPORTED.value)
            if isinstance(raw_t, CodingAgentEventType):
                ev_type = raw_t
            elif str(raw_t).upper() in CodingAgentEventType.__members__:
                ev_type = CodingAgentEventType[str(raw_t).upper()]
            elif str(raw_t).lower() in ("progress", "step"):
                ev_type = CodingAgentEventType.PROGRESS_REPORTED
            elif str(raw_t).lower() in ("message", "say"):
                ev_type = CodingAgentEventType.MESSAGE_EMITTED
            else:
                ev_type = CodingAgentEventType.PROGRESS_REPORTED
            ev = CodingAgentEvent(
                execution_id=request.execution_id,
                work_order_id=request.work_order_id,
                event_type=ev_type,
                sequence_number=seq,
                payload=dict(ev_data.get("payload", {})),
                trace=dict(request.trace),
            )
            self.emitted_events.append(ev)
            if event_handler:
                event_handler(ev)
            seq += 1

        # Check if cancellation was pre-registered or simulated failure
        final_status = self.simulated_status
        err_msg = self.simulated_error
        if is_already_cancelled or self._execution_states.get(request.execution_id) == CodingAgentExecutionStatus.CANCELLED:
            final_status = CodingAgentExecutionStatus.CANCELLED
            err_msg = "Execution was cancelled."

        self._execution_states[request.execution_id] = final_status

        # Emit completion/failure event
        completion_ev_type = (
            CodingAgentEventType.EXECUTION_CANCELLED
            if final_status == CodingAgentExecutionStatus.CANCELLED
            else (
                CodingAgentEventType.EXECUTION_FAILED
                if final_status in (CodingAgentExecutionStatus.FAILED, CodingAgentExecutionStatus.TIMED_OUT)
                else CodingAgentEventType.EXECUTION_COMPLETED
            )
        )
        final_ev = CodingAgentEvent(
            execution_id=request.execution_id,
            work_order_id=request.work_order_id,
            event_type=completion_ev_type,
            sequence_number=seq,
            payload={"status": final_status.value, "error": err_msg},
            trace=dict(request.trace),
        )
        self.emitted_events.append(final_ev)
        if event_handler:
            event_handler(final_ev)

        result = CodingAgentResult(
            execution_id=request.execution_id,
            work_order_id=request.work_order_id,
            status=final_status,
            output_text=self.simulated_output if final_status == CodingAgentExecutionStatus.COMPLETED else "",
            tool_calls_count=len(self.simulated_events),
            iterations_count=1,
            tokens_used={"prompt_tokens": 150, "completion_tokens": 80, "total_tokens": 230},
            error_message=err_msg,
            events_count=len(self.emitted_events),
            started_at=start_event.timestamp,
            completed_at=utc_now(),
            trace=dict(request.trace),
        )
        return result

    def cancel(
        self,
        cancellation: CodingAgentCancellationRequest,
    ) -> CodingAgentCancellationResult:
        cancellation.validate()
        self.received_cancellations.append(cancellation)
        self._execution_states[cancellation.execution_id] = CodingAgentExecutionStatus.CANCELLED

        return CodingAgentCancellationResult(
            execution_id=cancellation.execution_id,
            work_order_id=cancellation.work_order_id,
            cancelled=True,
            reason=cancellation.reason,
            trace=dict(cancellation.trace),
        )

    def get_status(self, execution_id: str) -> CodingAgentExecutionStatus:
        return self._execution_states.get(execution_id, CodingAgentExecutionStatus.PENDING)

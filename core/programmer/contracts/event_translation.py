from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from typing import Any, Optional, Union

from core.programmer.contracts.identifiers import (
    new_programmer_event_id,
    validate_execution_id,
    validate_programmer_event_id,
    validate_work_order_id,
)
from core.programmer.errors import (
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    ProgrammerExecutionEventType,
)

logger = logging.getLogger("AutonomOS.Programmer.EventTranslation")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProgrammerExecutionEvent:
    """
    Normalized domain event representing a discrete occurrence during a Programmer execution attempt.
    
    Guarantees:
    - Encapsulates external coding agent internals behind AutonomOS domain contracts.
    - Preserves causal lineage to execution_id and work_order_id.
    - Preserves original Cline event references where available.
    - Explicitly flags agent dialogue/narration (`is_narration=True`) to prevent treating
      unverified agent claims (e.g. 'tests pass') as verified evidence.
    """
    execution_id: str
    work_order_id: str
    event_type: ProgrammerExecutionEventType
    event_id: str = field(default_factory=new_programmer_event_id)
    payload: dict[str, Any] = field(default_factory=dict)
    sequence_number: int = 0
    timestamp: str = field(default_factory=utc_now)
    raw_event_ref: Optional[str] = None
    is_narration: bool = False
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.event_type, str):
            try:
                self.event_type = ProgrammerExecutionEventType(self.event_type.upper())
            except (ValueError, TypeError):
                self.event_type = ProgrammerExecutionEventType.WARNING

        if not self.trace:
            self.trace = {
                "execution_id": self.execution_id,
                "work_order_id": self.work_order_id,
            }
        if self.raw_event_ref and "raw_event_ref" not in self.trace:
            self.trace["raw_event_ref"] = self.raw_event_ref

    def validate(self) -> None:
        """Validate event identity, causal lineage, and sequence constraints."""
        validate_programmer_event_id(self.event_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        if not isinstance(self.event_type, ProgrammerExecutionEventType):
            raise ProgrammerValidationError(f"Invalid event_type: {self.event_type}")
        if self.sequence_number < 0:
            raise ProgrammerValidationError(f"sequence_number must be non-negative, got {self.sequence_number}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "event_type": self.event_type.value if isinstance(self.event_type, ProgrammerExecutionEventType) else str(self.event_type),
            "payload": dict(self.payload),
            "sequence_number": self.sequence_number,
            "timestamp": self.timestamp,
            "raw_event_ref": self.raw_event_ref,
            "is_narration": self.is_narration,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    def to_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammerExecutionEvent:
        raw_type = data.get("event_type", ProgrammerExecutionEventType.AGENT_MESSAGE.value)
        try:
            event_type = ProgrammerExecutionEventType(str(raw_type).upper())
        except (ValueError, TypeError):
            event_type = ProgrammerExecutionEventType.WARNING

        return cls(
            event_id=data.get("event_id", new_programmer_event_id()),
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            event_type=event_type,
            payload=dict(data.get("payload", {})),
            sequence_number=int(data.get("sequence_number", 0)),
            timestamp=data.get("timestamp", utc_now()),
            raw_event_ref=data.get("raw_event_ref"),
            is_narration=bool(data.get("is_narration", False)),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )


class ClineEventTranslator:
    """
    Deterministic translator converting raw Cline runtime events into normalized ProgrammerExecutionEvents.

    Key Principles:
    - Never exposes raw Cline internals directly to the rest of AutonomOS.
    - Agent narration ('Tests are passing', 'File modified') is flagged as `is_narration=True`
      and categorized under `AGENT_MESSAGE`, never synthesized as verified test or filesystem proof.
    - Unknown or malformed raw events are defensively caught and normalized into `WARNING` or `ERROR`
      events without crashing the operational stream.
    - Preserves original Cline event references where available.
    """

    EVENT_TYPE_MAPPING: dict[str, ProgrammerExecutionEventType] = {
        "start": ProgrammerExecutionEventType.EXECUTION_STARTED,
        "started": ProgrammerExecutionEventType.EXECUTION_STARTED,
        "session_start": ProgrammerExecutionEventType.EXECUTION_STARTED,
        "session_started": ProgrammerExecutionEventType.EXECUTION_STARTED,
        "agent_started": ProgrammerExecutionEventType.EXECUTION_STARTED,
        "say": ProgrammerExecutionEventType.AGENT_MESSAGE,
        "agent_say": ProgrammerExecutionEventType.AGENT_MESSAGE,
        "message": ProgrammerExecutionEventType.AGENT_MESSAGE,
        "thought": ProgrammerExecutionEventType.AGENT_MESSAGE,
        "thinking": ProgrammerExecutionEventType.AGENT_MESSAGE,
        "file_read": ProgrammerExecutionEventType.FILE_OPERATION,
        "read_file": ProgrammerExecutionEventType.FILE_OPERATION,
        "file_write": ProgrammerExecutionEventType.FILE_OPERATION,
        "write_file": ProgrammerExecutionEventType.FILE_OPERATION,
        "file_create": ProgrammerExecutionEventType.FILE_OPERATION,
        "create_file": ProgrammerExecutionEventType.FILE_OPERATION,
        "file_delete": ProgrammerExecutionEventType.FILE_OPERATION,
        "delete_file": ProgrammerExecutionEventType.FILE_OPERATION,
        "file_rename": ProgrammerExecutionEventType.FILE_OPERATION,
        "rename_file": ProgrammerExecutionEventType.FILE_OPERATION,
        "file_edit": ProgrammerExecutionEventType.FILE_OPERATION,
        "command": ProgrammerExecutionEventType.COMMAND_OPERATION,
        "command_exec": ProgrammerExecutionEventType.COMMAND_OPERATION,
        "execute_command": ProgrammerExecutionEventType.COMMAND_OPERATION,
        "terminal": ProgrammerExecutionEventType.COMMAND_OPERATION,
        "progress": ProgrammerExecutionEventType.PROGRESS,
        "progress_reported": ProgrammerExecutionEventType.PROGRESS,
        "step": ProgrammerExecutionEventType.PROGRESS,
        "status_update": ProgrammerExecutionEventType.PROGRESS,
        "message_emitted": ProgrammerExecutionEventType.AGENT_MESSAGE,
        "warning": ProgrammerExecutionEventType.WARNING,
        "error": ProgrammerExecutionEventType.ERROR,
        "exception": ProgrammerExecutionEventType.ERROR,
        "failure": ProgrammerExecutionEventType.ERROR,
        "completed": ProgrammerExecutionEventType.EXECUTION_COMPLETED,
        "complete": ProgrammerExecutionEventType.EXECUTION_COMPLETED,
        "done": ProgrammerExecutionEventType.EXECUTION_COMPLETED,
        "cancelled": ProgrammerExecutionEventType.EXECUTION_CANCELLED,
        "cancel": ProgrammerExecutionEventType.EXECUTION_CANCELLED,
    }

    @classmethod
    def translate_event(
        cls,
        raw_event: Any,
        execution_id: str,
        work_order_id: str,
        sequence_number: int = 0,
        correlation_id: Optional[str] = None,
    ) -> ProgrammerExecutionEvent:
        """
        Defensively translate a single native Cline event payload into a typed ProgrammerExecutionEvent.
        """
        trace = {
            "execution_id": execution_id,
            "work_order_id": work_order_id,
        }
        if correlation_id:
            trace["correlation_id"] = correlation_id

        # 1. Defensive handling for malformed or non-dict payloads
        if not isinstance(raw_event, dict):
            return ProgrammerExecutionEvent(
                execution_id=execution_id,
                work_order_id=work_order_id,
                event_type=ProgrammerExecutionEventType.WARNING,
                sequence_number=sequence_number,
                payload={
                    "error": "Malformed raw event: expected dict structure.",
                    "raw_unstructured_data": str(raw_event),
                    "malformed": True,
                },
                trace=trace,
                is_narration=False,
            )

        # 2. Extract original event ID / reference
        raw_ref = (
            raw_event.get("event_id")
            or raw_event.get("id")
            or raw_event.get("ref")
            or raw_event.get("session_event_id")
        )
        if raw_ref:
            raw_ref = str(raw_ref)
            trace["raw_event_id"] = raw_ref

        # 3. Resolve event type
        raw_type_str = str(raw_event.get("type", "")).strip().lower()
        if not raw_type_str:
            raw_type_str = str(raw_event.get("event_type", "message")).strip().lower()

        # Check mapping
        if raw_type_str in cls.EVENT_TYPE_MAPPING:
            event_type = cls.EVENT_TYPE_MAPPING[raw_type_str]
            is_unknown = False
        else:
            # Unknown event type: safely categorize as WARNING or AGENT_MESSAGE
            event_type = ProgrammerExecutionEventType.WARNING
            is_unknown = True

        # 4. Extract and normalize payload
        raw_payload = raw_event.get("payload")
        if isinstance(raw_payload, dict):
            payload = dict(raw_payload)
        elif raw_payload is not None:
            payload = {"data": raw_payload}
        else:
            # Fallback to copying raw_event without control keys
            payload = {k: v for k, v in raw_event.items() if k not in ("type", "event_type", "id", "event_id")}

        if is_unknown:
            payload["unknown_raw_type"] = raw_type_str
            payload["warning"] = f"Unmapped Cline event type '{raw_type_str}' safely normalized."

        # 5. Anti-Hallucination & Narration Policy: Agent Narration is NOT Proof
        # Dialogue, thoughts, or agent messages are flagged with is_narration = True
        is_narration = event_type in (
            ProgrammerExecutionEventType.AGENT_MESSAGE,
            ProgrammerExecutionEventType.PROGRESS,
        )

        # Ensure that if the message text claims tests passed or files modified,
        # it is NOT elevated into verified evidence.
        text_content = payload.get("text") or payload.get("message") or payload.get("content") or ""
        if isinstance(text_content, str):
            payload["text"] = text_content
            # Disclaim authority in payload
            if is_narration:
                payload["is_narration"] = True
                payload["proof_status"] = "UNVERIFIED_AGENT_NARRATION"

        trace["cline_event_type"] = raw_type_str

        return ProgrammerExecutionEvent(
            execution_id=execution_id,
            work_order_id=work_order_id,
            event_type=event_type,
            payload=payload,
            sequence_number=sequence_number,
            raw_event_ref=raw_ref,
            is_narration=is_narration,
            trace=trace,
        )


class ProgrammerExecutionTraceCollector:
    """
    Maintains the chronological, correlated, and deduplicated trace of execution events
    for a single Programmer execution session.
    
    Guarantees:
    - Enforces execution correlation: events with mismatched execution_id or work_order_id are rejected.
    - Idempotent deduplication: identical events or duplicate raw references are filtered.
    - Ordered progression: events are recorded with monotonic sequence numbers.
    - Query capabilities: provides scoped views over file operations, commands, messages, and errors.
    """

    def __init__(self, execution_id: str, work_order_id: str):
        validate_execution_id(execution_id)
        validate_work_order_id(work_order_id)
        self.execution_id = execution_id
        self.work_order_id = work_order_id
        self._events: list[ProgrammerExecutionEvent] = []
        self._seen_event_ids: set[str] = set()
        self._seen_raw_refs: set[str] = set()
        self._current_sequence: int = 0

    @property
    def event_count(self) -> int:
        return len(self._events)

    def add_event(self, event: ProgrammerExecutionEvent) -> bool:
        """
        Record a normalized event into the execution trace.
        Validates lineage and discards duplicates. Returns True if recorded, False if duplicate.
        """
        if event is None:
            raise ProgrammerValidationError("Cannot record None event.")

        # Lineage check
        if event.execution_id != self.execution_id:
            raise ProgrammerLineageError(
                f"Execution ID mismatch: trace belongs to '{self.execution_id}', got event with '{event.execution_id}'."
            )
        if event.work_order_id != self.work_order_id:
            raise ProgrammerLineageError(
                f"Work order ID mismatch: trace belongs to '{self.work_order_id}', got event with '{event.work_order_id}'."
            )

        # Deduplication check
        if event.event_id in self._seen_event_ids:
            return False
        if event.raw_event_ref and event.raw_event_ref in self._seen_raw_refs:
            return False

        # Sequence assignment if unassigned
        if event.sequence_number == 0 and self._current_sequence > 0:
            self._current_sequence += 1
            event.sequence_number = self._current_sequence
        elif event.sequence_number > self._current_sequence:
            self._current_sequence = event.sequence_number
        elif event.sequence_number == 0:
            self._current_sequence += 1
            event.sequence_number = self._current_sequence

        self._seen_event_ids.add(event.event_id)
        if event.raw_event_ref:
            self._seen_raw_refs.add(event.raw_event_ref)

        self._events.append(event)
        return True

    def record_raw_event(self, raw_event: Any) -> ProgrammerExecutionEvent:
        """
        Convenience method: translates a raw Cline event and records it into the trace.
        """
        self._current_sequence += 1
        event = ClineEventTranslator.translate_event(
            raw_event=raw_event,
            execution_id=self.execution_id,
            work_order_id=self.work_order_id,
            sequence_number=self._current_sequence,
        )
        self.add_event(event)
        return event

    @property
    def events(self) -> list[ProgrammerExecutionEvent]:
        """Return all recorded events in chronological order."""
        return list(self._events)

    def get_events(self) -> list[ProgrammerExecutionEvent]:
        """Return all recorded events in chronological order."""
        return list(self._events)

    def get_command_executions(self) -> list[ProgrammerExecutionEvent]:
        """Alias for get_command_operations."""
        return self.get_command_operations()

    def get_events_by_type(
        self,
        event_type: ProgrammerExecutionEventType,
    ) -> list[ProgrammerExecutionEvent]:
        """Filter recorded events by event type."""
        return [e for e in self._events if e.event_type == event_type]

    def get_file_operations(self) -> list[ProgrammerExecutionEvent]:
        """Return all recorded file operations."""
        return self.get_events_by_type(ProgrammerExecutionEventType.FILE_OPERATION)

    def get_command_operations(self) -> list[ProgrammerExecutionEvent]:
        """Return all recorded command operations."""
        return self.get_events_by_type(ProgrammerExecutionEventType.COMMAND_OPERATION)

    def get_messages(self) -> list[ProgrammerExecutionEvent]:
        """Return all recorded agent messages and dialogue."""
        return self.get_events_by_type(ProgrammerExecutionEventType.AGENT_MESSAGE)

    def get_warnings_and_errors(self) -> list[ProgrammerExecutionEvent]:
        """Return all warnings and errors recorded during execution."""
        return [
            e
            for e in self._events
            if e.event_type in (ProgrammerExecutionEventType.WARNING, ProgrammerExecutionEventType.ERROR)
        ]

    def has_errors(self) -> bool:
        """Check whether any fatal error events were recorded."""
        return any(e.event_type == ProgrammerExecutionEventType.ERROR for e in self._events)

    def is_terminal(self) -> bool:
        """Check whether execution has reached a terminal event (COMPLETED or CANCELLED)."""
        terminal_types = (
            ProgrammerExecutionEventType.EXECUTION_COMPLETED,
            ProgrammerExecutionEventType.EXECUTION_CANCELLED,
        )
        return any(e.event_type in terminal_types for e in self._events)

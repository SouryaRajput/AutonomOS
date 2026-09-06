"""
Unit Tests for Programmer V1 Phase 3.4: Cline Event Translation & Execution Trace.

Tests the event translation layer between Cline runtime events and AutonomOS Programmer execution events:
- Normal event translation (minimum 9 categories)
- Unknown raw event handling and non-fatal normalization
- Malformed / corrupted raw event recovery
- Idempotent duplicate event deduplication
- Monotonic event sequence ordering
- Execution correlation and lineage enforcement
- Cancellation and failure event translation
- Strict invariant: Agent narration is NEVER treated as verified proof
"""

from pathlib import Path
import pytest

from core.programmer.contracts.event_translation import (
    ClineEventTranslator,
    ProgrammerExecutionEvent,
    ProgrammerExecutionTraceCollector,
)
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_work_order_id,
)
from core.programmer.errors import ProgrammerLineageError, ProgrammerValidationError
from core.programmer.types import ProgrammerExecutionEventType


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def execution_id() -> str:
    return new_execution_id()


@pytest.fixture
def work_order_id() -> str:
    return new_work_order_id()


@pytest.fixture
def trace_collector(execution_id: str, work_order_id: str) -> ProgrammerExecutionTraceCollector:
    return ProgrammerExecutionTraceCollector(
        execution_id=execution_id,
        work_order_id=work_order_id,
    )


# ==============================================================================
# 1. Normal Event Translation (Minimum 9 Categories)
# ==============================================================================


def test_translate_execution_started(execution_id: str, work_order_id: str):
    raw = {"type": "session_started", "id": "cline-ev-001", "payload": {"model": "claude-3-5-sonnet"}}
    ev = ClineEventTranslator.translate_event(raw, execution_id, work_order_id)
    assert ev.event_type == ProgrammerExecutionEventType.EXECUTION_STARTED
    assert ev.raw_event_ref == "cline-ev-001"
    assert ev.execution_id == execution_id
    assert ev.work_order_id == work_order_id
    assert ev.payload["model"] == "claude-3-5-sonnet"


def test_translate_agent_message_and_narration_quarantine(execution_id: str, work_order_id: str):
    # CRITICAL INVARIANT: Agent narration must NOT become verified proof
    raw = {
        "type": "say",
        "id": "cline-ev-002",
        "payload": {"text": "Tests are passing! All unit tests succeeded."},
    }
    ev = ClineEventTranslator.translate_event(raw, execution_id, work_order_id)
    assert ev.event_type == ProgrammerExecutionEventType.AGENT_MESSAGE
    assert ev.is_narration is True
    assert ev.payload["proof_status"] == "UNVERIFIED_AGENT_NARRATION"
    assert ev.payload["text"] == "Tests are passing! All unit tests succeeded."


def test_translate_file_operation(execution_id: str, work_order_id: str):
    raw = {
        "type": "write_file",
        "id": "cline-ev-003",
        "payload": {"path": "src/app.py", "content": "print('hello')"},
    }
    ev = ClineEventTranslator.translate_event(raw, execution_id, work_order_id)
    assert ev.event_type == ProgrammerExecutionEventType.FILE_OPERATION
    assert ev.raw_event_ref == "cline-ev-003"
    assert ev.payload["path"] == "src/app.py"


def test_translate_command_operation(execution_id: str, work_order_id: str):
    raw = {
        "type": "command_exec",
        "id": "cline-ev-004",
        "payload": {"command": "pytest -v"},
    }
    ev = ClineEventTranslator.translate_event(raw, execution_id, work_order_id)
    assert ev.event_type == ProgrammerExecutionEventType.COMMAND_OPERATION
    assert ev.payload["command"] == "pytest -v"


def test_translate_progress(execution_id: str, work_order_id: str):
    raw = {
        "type": "progress",
        "id": "cline-ev-005",
        "payload": {"step": 2, "total_steps": 5, "message": "Refactoring classes"},
    }
    ev = ClineEventTranslator.translate_event(raw, execution_id, work_order_id)
    assert ev.event_type == ProgrammerExecutionEventType.PROGRESS
    assert ev.is_narration is True
    assert ev.payload["step"] == 2


def test_translate_warning(execution_id: str, work_order_id: str):
    raw = {
        "type": "warning",
        "id": "cline-ev-006",
        "payload": {"message": "Deprecation warning in package"},
    }
    ev = ClineEventTranslator.translate_event(raw, execution_id, work_order_id)
    assert ev.event_type == ProgrammerExecutionEventType.WARNING
    assert ev.is_narration is False


def test_translate_error(execution_id: str, work_order_id: str):
    raw = {
        "type": "error",
        "id": "cline-ev-007",
        "payload": {"error": "SyntaxError in generated code"},
    }
    ev = ClineEventTranslator.translate_event(raw, execution_id, work_order_id)
    assert ev.event_type == ProgrammerExecutionEventType.ERROR
    assert ev.payload["error"] == "SyntaxError in generated code"


def test_translate_execution_completed(execution_id: str, work_order_id: str):
    raw = {
        "type": "completed",
        "id": "cline-ev-008",
        "payload": {"exit_reason": "Task finished successfully"},
    }
    ev = ClineEventTranslator.translate_event(raw, execution_id, work_order_id)
    assert ev.event_type == ProgrammerExecutionEventType.EXECUTION_COMPLETED


def test_translate_execution_cancelled(execution_id: str, work_order_id: str):
    raw = {
        "type": "cancelled",
        "id": "cline-ev-009",
        "payload": {"reason": "Cancelled by user intervention"},
    }
    ev = ClineEventTranslator.translate_event(raw, execution_id, work_order_id)
    assert ev.event_type == ProgrammerExecutionEventType.EXECUTION_CANCELLED


# ==============================================================================
# 2. Unknown and Malformed Events Handling
# ==============================================================================


def test_unknown_event_type_is_normalized_without_crashing(execution_id: str, work_order_id: str):
    raw = {
        "type": "unrecognized_custom_opcode",
        "id": "cline-ev-unknown",
        "payload": {"data": [1, 2, 3]},
    }
    ev = ClineEventTranslator.translate_event(raw, execution_id, work_order_id)
    assert ev.event_type == ProgrammerExecutionEventType.WARNING
    assert ev.payload["unknown_raw_type"] == "unrecognized_custom_opcode"
    assert "Unmapped" in ev.payload["warning"]
    assert ev.raw_event_ref == "cline-ev-unknown"


def test_malformed_non_dict_event_handling(execution_id: str, work_order_id: str):
    # Non-dict string raw event
    raw = "malformed_string_event"
    ev = ClineEventTranslator.translate_event(raw, execution_id, work_order_id)
    assert ev.event_type == ProgrammerExecutionEventType.WARNING
    assert ev.payload["malformed"] is True
    assert "malformed_string_event" in ev.payload["raw_unstructured_data"]


def test_malformed_none_event_handling(execution_id: str, work_order_id: str):
    ev = ClineEventTranslator.translate_event(None, execution_id, work_order_id)
    assert ev.event_type == ProgrammerExecutionEventType.WARNING
    assert ev.payload["malformed"] is True


# ==============================================================================
# 3. Duplicate Events & Idempotent Deduplication
# ==============================================================================


def test_trace_collector_deduplicates_events(
    trace_collector: ProgrammerExecutionTraceCollector,
    execution_id: str,
    work_order_id: str,
):
    ev1 = ProgrammerExecutionEvent(
        execution_id=execution_id,
        work_order_id=work_order_id,
        event_type=ProgrammerExecutionEventType.PROGRESS,
        event_id="pevt-dup-001",
        raw_event_ref="raw-ref-001",
        payload={"step": 1},
    )
    # Add first time
    assert trace_collector.add_event(ev1) is True
    assert trace_collector.event_count == 1

    # Add same event again (by event_id)
    assert trace_collector.add_event(ev1) is False
    assert trace_collector.event_count == 1

    # Add different event_id but identical raw_event_ref
    ev2 = ProgrammerExecutionEvent(
        execution_id=execution_id,
        work_order_id=work_order_id,
        event_type=ProgrammerExecutionEventType.PROGRESS,
        event_id="pevt-dup-002",
        raw_event_ref="raw-ref-001",  # Same raw ref!
        payload={"step": 1},
    )
    assert trace_collector.add_event(ev2) is False
    assert trace_collector.event_count == 1


# ==============================================================================
# 4. Event Ordering & Sequence Monotonicity
# ==============================================================================


def test_trace_collector_maintains_monotonic_sequence(
    trace_collector: ProgrammerExecutionTraceCollector,
):
    ev1 = trace_collector.record_raw_event({"type": "session_started", "id": "r1"})
    ev2 = trace_collector.record_raw_event({"type": "say", "id": "r2", "payload": {"text": "Thinking"}})
    ev3 = trace_collector.record_raw_event({"type": "completed", "id": "r3"})

    events = trace_collector.get_events()
    assert len(events) == 3
    assert events[0].sequence_number == 1
    assert events[1].sequence_number == 2
    assert events[2].sequence_number == 3
    assert events[0].sequence_number < events[1].sequence_number < events[2].sequence_number


# ==============================================================================
# 5. Execution Correlation & Lineage Enforcement
# ==============================================================================


def test_trace_collector_rejects_mismatched_execution_id(
    trace_collector: ProgrammerExecutionTraceCollector,
    work_order_id: str,
):
    alien_ev = ProgrammerExecutionEvent(
        execution_id=new_execution_id(),  # Different execution!
        work_order_id=work_order_id,
        event_type=ProgrammerExecutionEventType.PROGRESS,
    )
    with pytest.raises(ProgrammerLineageError, match="Execution ID mismatch"):
        trace_collector.add_event(alien_ev)


def test_trace_collector_rejects_mismatched_work_order_id(
    trace_collector: ProgrammerExecutionTraceCollector,
    execution_id: str,
):
    alien_ev = ProgrammerExecutionEvent(
        execution_id=execution_id,
        work_order_id=new_work_order_id(),  # Different work order!
        event_type=ProgrammerExecutionEventType.PROGRESS,
    )
    with pytest.raises(ProgrammerLineageError, match="Work order ID mismatch"):
        trace_collector.add_event(alien_ev)


# ==============================================================================
# 6. Lifecycle & State Queries
# ==============================================================================


def test_trace_collector_queries(trace_collector: ProgrammerExecutionTraceCollector):
    assert trace_collector.is_terminal() is False
    assert trace_collector.has_errors() is False

    trace_collector.record_raw_event({"type": "write_file", "id": "f1", "payload": {"path": "a.py"}})
    trace_collector.record_raw_event({"type": "command", "id": "c1", "payload": {"command": "ls"}})
    trace_collector.record_raw_event({"type": "error", "id": "e1", "payload": {"error": "syntax"}})
    trace_collector.record_raw_event({"type": "completed", "id": "t1"})

    assert len(trace_collector.get_file_operations()) == 1
    assert len(trace_collector.get_command_operations()) == 1
    assert trace_collector.has_errors() is True
    assert trace_collector.is_terminal() is True

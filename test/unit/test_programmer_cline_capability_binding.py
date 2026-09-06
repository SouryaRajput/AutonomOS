"""
Adversarial Unit Tests for Programmer V1 Phase 3.5: Cline Capability Binding.

Tests the strict policy-governed capability interface binding Cline to ProgrammerExecutionContext:
- Allowed and denied file reads
- Allowed and denied file writes
- Absolute denial of forbidden paths (secrets)
- Path traversal defense
- Command authorization and denial
- Command injection defense (shell chaining blocked)
- Working directory confinement
- Cancellation during operation
- Anti-bypass canonical tool dispatching
- Traceability and normalized execution event emission
"""

import json
import os
from pathlib import Path
import pytest

from core.enums import RiskLevel
from core.programmer.contracts.capability_binding import (
    CapabilityOperationResult,
    ClineCapabilityBinding,
)
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.event_translation import (
    ProgrammerExecutionEvent,
    ProgrammerExecutionTraceCollector,
)
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_work_order_id,
)
from core.programmer.contracts.provisioner import WorkspaceProvisioner
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import ProgrammerError, ProgrammerValidationError
from core.programmer.types import (
    ExecutionContextStatus,
    ProgrammerExecutionEventType,
    ProgrammerExecutionStatus,
)


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "binding_project"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "src").mkdir(parents=True, exist_ok=True)
    (ws / "src" / "app.py").write_text("def app(): return 42\n")
    (ws / "docs").mkdir(parents=True, exist_ok=True)
    (ws / "docs" / "readme.md").write_text("# Project Readme\n")
    (ws / "secrets").mkdir(parents=True, exist_ok=True)
    (ws / "secrets" / "key.txt").write_text("TOP_SECRET_CREDENTIAL")
    return ws


@pytest.fixture
def bound_work_order() -> ProgrammerWorkOrder:
    return ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id="tsk-mgr-binding",
        project_id="prj-binding-test",
        correlation_id="corr-binding-001",
        objective="Implement feature with bounded capabilities",
        allowed_paths=["src", "docs"],
        writable_paths=["src/app.py", "src/new_file.py", "src/renamed.py"],
        read_only_paths=["docs"],
        forbidden_paths=["secrets"],
        allowed_commands=[
            AllowedCommand(
                command="python3",
                description="Run python interpreter",
                allowed_subcommands=["-c"],
                timeout_seconds=30,
            ),
            AllowedCommand(
                command="echo",
                description="Echo text",
                timeout_seconds=10,
            ),
        ],
        time_budget=300,
        iteration_budget=10,
    )


@pytest.fixture
def bound_execution(bound_work_order: ProgrammerWorkOrder) -> ProgrammerExecution:
    return ProgrammerExecution(
        execution_id=new_execution_id(),
        work_order_id=bound_work_order.work_order_id,
        task_id=bound_work_order.manager_task_id,
        project_id=bound_work_order.project_id,
        correlation_id=bound_work_order.correlation_id,
        status=ProgrammerExecutionStatus.STARTING,
    )


@pytest.fixture
def ready_context(
    bound_work_order: ProgrammerWorkOrder,
    bound_execution: ProgrammerExecution,
    workspace_dir: Path,
) -> ProgrammerExecutionContext:
    provisioner = WorkspaceProvisioner(
        project_resolver={bound_work_order.project_id: str(workspace_dir)}
    )
    result = provisioner.provision(bound_work_order, bound_execution)
    assert result.is_ready() is True
    assert result.execution_context is not None
    return result.execution_context


@pytest.fixture
def binding(ready_context: ProgrammerExecutionContext) -> ClineCapabilityBinding:
    return ClineCapabilityBinding(context=ready_context)


# ==============================================================================
# 1. Filesystem Tests: Allowed vs Denied Reads
# ==============================================================================


def test_allowed_file_read(binding: ClineCapabilityBinding):
    res = binding.read_file("src/app.py")
    assert res.allowed is True
    assert res.success is True
    assert "def app(): return 42" in res.output
    assert res.execution_id == binding.execution_id

    # Verify event recorded in trace
    events = binding.trace_collector.get_file_operations()
    assert len(events) >= 1
    read_ev = events[-1]
    assert read_ev.payload["operation"] == "READ"
    assert read_ev.payload["allowed"] is True


def test_denied_file_read_outside_allowed_paths(binding: ClineCapabilityBinding):
    # 'other_dir' is not in allowed_paths
    res = binding.read_file("other_dir/test.txt")
    assert res.allowed is False
    assert res.success is False
    assert res.error_code == "PERMISSION_DENIED"
    assert "outside" in res.denial_reason.lower() or "not allowed" in res.denial_reason.lower()

    # Event recorded with denial
    events = binding.trace_collector.get_file_operations()
    assert len(events) >= 1
    assert events[-1].payload["allowed"] is False


def test_forbidden_file_read(binding: ClineCapabilityBinding):
    # 'secrets/key.txt' is strictly forbidden
    res = binding.read_file("secrets/key.txt")
    assert res.allowed is False
    assert res.success is False
    assert res.error_code == "PERMISSION_DENIED"
    assert "forbidden" in res.denial_reason.lower()

    # Verify disk content was NEVER read
    assert res.output is None


def test_path_traversal_read_escape_blocked(binding: ClineCapabilityBinding):
    # Path traversal attempting to read /etc/passwd or escape workspace root
    res = binding.read_file("../../../etc/passwd")
    assert res.allowed is False
    assert res.success is False
    assert res.error_code == "PERMISSION_DENIED"


# ==============================================================================
# 2. Filesystem Tests: Allowed vs Denied Writes
# ==============================================================================


def test_allowed_file_write(binding: ClineCapabilityBinding, workspace_dir: Path):
    target = "src/app.py"
    new_content = "def app(): return 100\n"
    res = binding.write_file(target, new_content)
    assert res.allowed is True
    assert res.success is True

    # Check disk actually updated
    disk_content = (workspace_dir / "src" / "app.py").read_text()
    assert disk_content == new_content

    # Trace event recorded
    events = binding.trace_collector.get_file_operations()
    assert len(events) >= 1
    write_ev = events[-1]
    assert write_ev.payload["operation"] == "WRITE"
    assert write_ev.payload["allowed"] is True
    assert write_ev.payload["bytes_written"] == len(new_content)


def test_denied_file_write_read_only(binding: ClineCapabilityBinding, workspace_dir: Path):
    # docs/readme.md is in read_only_paths
    target = "docs/readme.md"
    orig_content = (workspace_dir / target).read_text()
    res = binding.write_file(target, "# HACKED CONTENT")

    assert res.allowed is False
    assert res.success is False
    assert res.error_code == "PERMISSION_DENIED"
    assert "read_only" in res.denial_reason.lower() or "read-only" in res.denial_reason.lower() or "writable" in res.denial_reason.lower()

    # Check disk was NOT modified
    assert (workspace_dir / target).read_text() == orig_content


def test_forbidden_file_write(binding: ClineCapabilityBinding, workspace_dir: Path):
    target = "secrets/key.txt"
    orig_content = (workspace_dir / target).read_text()
    res = binding.write_file(target, "OVERWRITTEN_SECRET")

    assert res.allowed is False
    assert res.success is False
    assert (workspace_dir / target).read_text() == orig_content


def test_allowed_file_creation(binding: ClineCapabilityBinding, workspace_dir: Path):
    target = "src/new_file.py"
    content = "# Newly created module\n"
    res = binding.create_file(target, content)
    assert res.allowed is True
    assert res.success is True

    assert (workspace_dir / target).exists()
    assert (workspace_dir / target).read_text() == content


def test_file_deletion_and_rename(binding: ClineCapabilityBinding, workspace_dir: Path):
    # Rename src/app.py -> src/renamed.py
    res_rename = binding.rename_file("src/app.py", "src/renamed.py")
    assert res_rename.allowed is True
    assert res_rename.success is True
    assert (workspace_dir / "src" / "renamed.py").exists()
    assert not (workspace_dir / "src" / "app.py").exists()

    # Delete src/renamed.py
    res_delete = binding.delete_file("src/renamed.py")
    assert res_delete.allowed is True
    assert res_delete.success is True
    assert not (workspace_dir / "src" / "renamed.py").exists()


# ==============================================================================
# 3. Command Execution Tests: Allowed vs Denied Commands & Injections
# ==============================================================================


def test_allowed_command_execution(binding: ClineCapabilityBinding):
    res = binding.execute_command('python3 -c "print(40 + 2)"')
    assert res.allowed is True
    assert res.success is True
    assert "42" in res.output["stdout"]

    # Verify event recorded
    cmds = binding.trace_collector.get_command_operations()
    assert len(cmds) >= 1
    assert cmds[-1].payload["allowed"] is True


def test_denied_unknown_command(binding: ClineCapabilityBinding):
    # curl is not in allowed_commands
    res = binding.execute_command("curl http://example.com")
    assert res.allowed is False
    assert res.success is False
    assert res.error_code == "COMMAND_DENIED"
    assert "not in allowed_commands" in res.denial_reason.lower() or "allowed_commands" in res.denial_reason.lower()

    # Verify event recorded
    cmds = binding.trace_collector.get_command_operations()
    assert len(cmds) >= 1
    assert cmds[-1].payload["allowed"] is False


def test_denied_command_injection_chaining(binding: ClineCapabilityBinding):
    # Chaining attempt: python3 is allowed, but && rm is injected
    res = binding.execute_command('python3 -c "print(1)" && rm -rf /')
    assert res.allowed is False
    assert res.success is False
    assert res.error_code == "COMMAND_DENIED"
    assert "shell construct" in res.denial_reason.lower() or "chaining" in res.denial_reason.lower() or "&&" in res.denial_reason


def test_denied_command_injection_semicolon(binding: ClineCapabilityBinding):
    res = binding.execute_command('echo hello; cat /etc/passwd')
    assert res.allowed is False
    assert res.success is False
    assert res.error_code == "COMMAND_DENIED"


def test_unauthorized_working_directory(binding: ClineCapabilityBinding):
    # Attempting to run in /tmp or outside workspace root
    res = binding.execute_command('echo hello', working_directory="/tmp")
    assert res.allowed is False
    assert res.success is False
    assert "working directory" in res.denial_reason.lower() or "escaped" in res.denial_reason.lower()


# ==============================================================================
# 4. Cancellation Defense: Refusal of Operational Requests
# ==============================================================================


def test_cancellation_refuses_operations(binding: ClineCapabilityBinding):
    # Simulate execution cancellation
    object.__setattr__(binding.context, "status", ExecutionContextStatus.FAILED)
    object.__setattr__(binding.context, "error_message", "Execution was cancelled by Manager.")

    # Read refused
    res_read = binding.read_file("src/app.py")
    assert res_read.allowed is False
    assert res_read.success is False
    assert res_read.error_code in ("CANCELLED_EXECUTION_ERROR", "FAILED_CONTEXT_ERROR")

    # Write refused
    res_write = binding.write_file("src/app.py", "new content")
    assert res_write.allowed is False
    assert res_write.success is False

    # Command refused
    res_cmd = binding.execute_command("echo test")
    assert res_cmd.allowed is False
    assert res_cmd.success is False


# ==============================================================================
# 5. Anti-Bypass Tool Dispatcher Tests
# ==============================================================================


def test_dispatch_tool_call_aliases(binding: ClineCapabilityBinding):
    # 'cat' alias for reading
    res_cat = binding.dispatch_tool_call("cat", {"file": "src/app.py"})
    assert res_cat.allowed is True
    assert "def app(): return 42" in res_cat.output

    # 'edit_file' alias for writing
    res_edit = binding.dispatch_tool_call("edit_file", {"path": "src/app.py", "content": "# Updated\n"})
    assert res_edit.allowed is True
    assert res_edit.success is True

    # 'terminal' alias for running command
    res_term = binding.dispatch_tool_call("terminal", {"cmd": 'python3 -c "print(99)"'})
    assert res_term.allowed is True
    assert "99" in res_term.output["stdout"]


def test_dispatch_tool_call_unknown_capability(binding: ClineCapabilityBinding):
    res = binding.dispatch_tool_call("unauthorized_eval_sandbox", {"code": "import os"})
    assert res.allowed is False
    assert res.success is False
    assert res.error_code == "UNKNOWN_CAPABILITY_ERROR"


# ==============================================================================
# 6. Traceability & Lineage Preservation
# ==============================================================================


def test_operations_preserve_lineage_and_emit_events(binding: ClineCapabilityBinding):
    initial_count = binding.trace_collector.event_count

    binding.read_file("src/app.py")
    binding.execute_command('echo "trace check"')
    binding.read_file("secrets/key.txt")  # Denied

    assert binding.trace_collector.event_count == initial_count + 3

    events = binding.trace_collector.get_events()
    for ev in events:
        assert ev.execution_id == binding.execution_id
        assert ev.work_order_id == binding.work_order_id
        assert ev.timestamp is not None

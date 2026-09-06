"""
End-to-End Domain Contract & Manager Integration Tests for Programmer Subsystem.

Tests the formal orchestration boundary between Manager and Programmer:
- Manager issues structured, bounded ProgrammerWorkOrder from Task
- Programmer validates authorization envelope
- Lifecycle state transitions and domain event emissions:
  * PROGRAMMER_REQUESTED
  * PROGRAMMER_STARTED
  * PROGRAMMER_BLOCKED
  * PROGRAMMER_COMPLETED
  * PROGRAMMER_FAILED
  * PROGRAMMER_CANCELLED
- Blocker escalation to Manager and unblocking flow
- Cancellation provenance tracking
- Strict lineage verification
- Critical regression test: Programmer completes execution autonomously using the
  complete authorization envelope (objective, context, path scope, command scope,
  constraints, acceptance criteria, budgets) without asking Manager to restate authorization.

All tests use test doubles (FakeProgrammerWorker) with no Cline, no shell commands,
and no filesystem mutations.
"""

import pytest

from core.enums import RiskLevel, TaskStatus
from core.events.types import EventSource, EventType
from core.models import Task, WorkerOutput
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.blocker import ProgrammerBlocker
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.identifiers import (
    new_blocker_id,
    new_execution_id,
    new_result_id,
    new_work_order_id,
)
from core.programmer.contracts.manager_bridge import (
    FakeProgrammerWorker,
    ProgrammerManagerBridge,
)
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    InvalidProgrammerTransitionError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    AcceptanceCriterionType,
    AcceptanceStatus,
    ProgrammerBlockerCategory,
    ProgrammerBlockerSeverity,
    ProgrammerExecutionStatus,
    ProgrammerResultStatus,
    ProgrammerWorkOrderStatus,
)


@pytest.fixture
def manager_task() -> Task:
    """Fixture providing a standard Manager Task."""
    return Task(
        id="tsk-mgr-1001",
        project_id="prj-autonomos-alpha",
        title="Implement authentication token rotation",
        objective="Add JWT refresh token rotation with redis blacklisting",
        metadata={
            "correlation_id": "corr-auth-rotation-1",
            "allowed_paths": ["src/auth", "tests/auth"],
            "writable_paths": ["src/auth/tokens.py", "tests/auth/test_tokens.py"],
            "read_only_paths": ["src/auth/config.py"],
            "forbidden_paths": ["config/secrets.env", ".git"],
            "allowed_commands": [
                {"command": "pytest tests/auth", "purpose": "Run auth unit tests"}
            ],
            "constraints": ["Do not store refresh tokens in plaintext"],
            "instructions": ["Implement atomic rotation with revocation"],
            "technical_requirements": ["Use redis SETEX for blacklist"],
            "context": {"token_lifetime_seconds": 3600, "refresh_lifetime_seconds": 86400},
            "acceptance_criteria": [
                {
                    "criterion_id": "ac-auth-01",
                    "criterion_type": "TEST_PASS",
                    "description": "Auth tests pass with 100% coverage on token module",
                },
                {
                    "criterion_id": "ac-auth-02",
                    "criterion_type": "NO_REGRESSION",
                    "description": "Existing session login flow continues to function",
                },
            ],
            "iteration_budget": 10,
            "time_budget": 600,
            "risk_level": "LOW",
        },
    )


def test_manager_issues_structured_work_order(manager_task: Task) -> None:
    """
    Test that Manager successfully issues a bounded ProgrammerWorkOrder from a Task
    and emits PROGRAMMER_REQUESTED.
    """
    bridge = ProgrammerManagerBridge()
    work_order = bridge.issue_work_order(manager_task)

    # 1. Structural validity & lineage
    assert work_order.work_order_id.startswith("pwo-")
    assert work_order.manager_task_id == manager_task.id
    assert work_order.project_id == manager_task.project_id
    assert work_order.correlation_id == "corr-auth-rotation-1"
    assert work_order.objective == manager_task.objective

    # 2. Path boundaries
    assert work_order.allowed_paths == ["src/auth", "tests/auth"]
    assert work_order.writable_paths == ["src/auth/tokens.py", "tests/auth/test_tokens.py"]
    assert work_order.read_only_paths == ["src/auth/config.py"]
    assert work_order.forbidden_paths == ["config/secrets.env", ".git"]

    # 3. Acceptance criteria & budgets
    assert len(work_order.acceptance_criteria) == 2
    assert work_order.iteration_budget == 10
    assert work_order.time_budget == 600

    # 4. Domain event PROGRAMMER_REQUESTED emitted
    assert len(bridge.events) == 1
    req_evt = bridge.events[0]
    assert req_evt.event_type == EventType.PROGRAMMER_REQUESTED
    assert req_evt.source == EventSource.MANAGER
    assert req_evt.project_id == manager_task.project_id
    assert req_evt.task_id == manager_task.id
    assert req_evt.payload["work_order_id"] == work_order.work_order_id
    assert req_evt.payload["manager_task_id"] == manager_task.id


def test_dispatch_and_execution_lifecycle_events(manager_task: Task) -> None:
    """
    Test that dispatching a work order creates an execution context and emits
    PROGRAMMER_STARTED, and finishing emits PROGRAMMER_COMPLETED.
    """
    bridge = ProgrammerManagerBridge()
    work_order = bridge.issue_work_order(manager_task)
    fake_worker = FakeProgrammerWorker(bridge=bridge)

    # Execute through fake worker
    execution, result, worker_output = fake_worker.execute_work_order(work_order)

    # Verify execution
    assert execution.is_terminal
    assert execution.status == ProgrammerExecutionStatus.COMPLETED
    assert execution.work_order_id == work_order.work_order_id
    assert execution.task_id == manager_task.id

    # Verify result
    assert result.is_success()
    assert result.work_order_id == work_order.work_order_id
    assert result.task_id == manager_task.id
    assert result.execution_id == execution.execution_id
    assert result.is_acceptance_fully_verified()

    # Verify WorkerOutput bridge
    assert isinstance(worker_output, WorkerOutput)
    assert worker_output.success is True
    assert worker_output.metadata["result_id"] == result.result_id
    assert worker_output.metadata["work_order_id"] == work_order.work_order_id
    assert worker_output.metadata["programmer_status"] == ProgrammerResultStatus.SUCCESS.value

    # Verify domain events sequence: PROGRAMMER_REQUESTED -> PROGRAMMER_STARTED -> PROGRAMMER_COMPLETED
    event_types = [e.event_type for e in bridge.events]
    assert event_types == [
        EventType.PROGRAMMER_REQUESTED,
        EventType.PROGRAMMER_STARTED,
        EventType.PROGRAMMER_COMPLETED,
    ]

    started_evt = bridge.events[1]
    assert started_evt.payload["execution_id"] == execution.execution_id
    assert started_evt.payload["work_order_id"] == work_order.work_order_id

    completed_evt = bridge.events[2]
    assert completed_evt.payload["result_id"] == result.result_id
    assert completed_evt.payload["acceptance_fully_verified"] is True


def test_failed_execution_lifecycle_and_event(manager_task: Task) -> None:
    """
    Test that a failed execution cycle transitions execution to FAILED and emits
    PROGRAMMER_FAILED.
    """
    bridge = ProgrammerManagerBridge()
    work_order = bridge.issue_work_order(manager_task)
    fake_worker = FakeProgrammerWorker(bridge=bridge, simulate_failure=True)

    execution, result, worker_output = fake_worker.execute_work_order(work_order)

    # Verify state and result
    assert execution.status == ProgrammerExecutionStatus.FAILED
    assert result.status == ProgrammerResultStatus.FAILED
    assert worker_output.success is False

    # Verify events
    event_types = [e.event_type for e in bridge.events]
    assert event_types == [
        EventType.PROGRAMMER_REQUESTED,
        EventType.PROGRAMMER_STARTED,
        EventType.PROGRAMMER_FAILED,
    ]

    failed_evt = bridge.events[2]
    assert failed_evt.event_type == EventType.PROGRAMMER_FAILED
    assert failed_evt.payload["execution_id"] == execution.execution_id
    assert failed_evt.payload["result_id"] == result.result_id


def test_blocker_escalation_and_resolution_flow(manager_task: Task) -> None:
    """
    Test material blocker escalation from Programmer to Manager and subsequent resolution:
    - Programmer encounters blocker requiring Manager authorization
    - Escalates blocker: execution transitions to BLOCKED, emits PROGRAMMER_BLOCKED
    - Manager inspects blocker details
    - Manager resolves blocker: execution unblocked back to RUNNING
    """
    bridge = ProgrammerManagerBridge()
    work_order = bridge.issue_work_order(manager_task)
    execution = bridge.dispatch_work_order(work_order)

    blocker = ProgrammerBlocker(
        blocker_id=new_blocker_id(),
        execution_id=execution.execution_id,
        work_order_id=work_order.work_order_id,
        category=ProgrammerBlockerCategory.PERMISSION,
        severity=ProgrammerBlockerSeverity.HIGH,
        description="Write access needed for shared config file 'src/auth/config.py'",
        required_decision="Grant writable permission to 'src/auth/config.py' in work order",
    )

    # Programmer escalates blocker
    escalated = bridge.escalate_blocker(execution, blocker, work_order=work_order)
    assert execution.status == ProgrammerExecutionStatus.BLOCKED
    assert len(execution.active_blockers) == 1
    assert escalated.is_material is True

    # PROGRAMMER_BLOCKED event emitted
    assert len(bridge.events) == 3  # REQUESTED, STARTED, BLOCKED
    blocked_evt = bridge.events[2]
    assert blocked_evt.event_type == EventType.PROGRAMMER_BLOCKED
    assert blocked_evt.payload["blocker_id"] == blocker.blocker_id
    assert blocked_evt.payload["category"] == "PERMISSION"
    assert blocked_evt.payload["required_decision"] == "Grant writable permission to 'src/auth/config.py' in work order"

    # Manager inspects and resolves blocker
    bridge.resolve_blocker(
        execution=execution,
        blocker_id=blocker.blocker_id,
        resolution="Approved scope amendment; permissions adjusted in operational context",
        target_status=ProgrammerExecutionStatus.RUNNING,
    )

    assert execution.status == ProgrammerExecutionStatus.RUNNING
    assert len(execution.active_blockers) == 0
    assert blocker.is_resolved is True
    assert blocker.resolved_at is not None


def test_cancellation_provenance_and_event(manager_task: Task) -> None:
    """
    Test cancellation flow initiated by Manager:
    - Active execution is cancelled
    - Transition to CANCELLED state
    - Cancellation provenance recorded
    - PROGRAMMER_CANCELLED domain event emitted
    """
    bridge = ProgrammerManagerBridge()
    work_order = bridge.issue_work_order(manager_task)
    execution = bridge.dispatch_work_order(work_order)

    cancellation = bridge.cancel_execution(
        execution=execution,
        requested_by="MANAGER",
        reason="Task reprioritized by project planner",
        work_order=work_order,
    )

    assert execution.status == ProgrammerExecutionStatus.CANCELLED
    assert execution.is_terminal is True
    assert execution.cancellation is not None
    assert execution.cancellation.requested_by == "MANAGER"
    assert execution.cancellation.reason == "Task reprioritized by project planner"

    # Verify event
    cancelled_evt = bridge.events[-1]
    assert cancelled_evt.event_type == EventType.PROGRAMMER_CANCELLED
    assert cancelled_evt.source == EventSource.MANAGER
    assert cancelled_evt.payload["requested_by"] == "MANAGER"
    assert cancelled_evt.payload["reason"] == "Task reprioritized by project planner"


def test_lineage_protection_mismatched_work_order_or_task(manager_task: Task) -> None:
    """
    Test that ProgrammerResult cannot be accepted if work_order_id, task_id,
    or execution_id does not match the issued work order.
    """
    bridge = ProgrammerManagerBridge()
    work_order = bridge.issue_work_order(manager_task)
    execution = bridge.dispatch_work_order(work_order)

    # 1. Mismatched work_order_id
    bad_wo_result = ProgrammerResult(
        result_id=new_result_id(),
        work_order_id="pwo-other-9999",
        execution_id=execution.execution_id,
        task_id=manager_task.id,
        project_id=manager_task.project_id,
        correlation_id=work_order.correlation_id,
        status=ProgrammerResultStatus.SUCCESS,
        summary="Done",
    )
    with pytest.raises(ProgrammerLineageError, match="does not match work order"):
        bridge.receive_result(bad_wo_result, execution, work_order)

    # 2. Mismatched task_id
    bad_task_result = ProgrammerResult(
        result_id=new_result_id(),
        work_order_id=work_order.work_order_id,
        execution_id=execution.execution_id,
        task_id="tsk-foreign-8888",
        project_id=manager_task.project_id,
        correlation_id=work_order.correlation_id,
        status=ProgrammerResultStatus.SUCCESS,
        summary="Done",
    )
    with pytest.raises(ProgrammerLineageError, match="does not match work order task_id"):
        bridge.receive_result(bad_task_result, execution, work_order)

    # 3. Mismatched execution_id
    bad_exec_result = ProgrammerResult(
        result_id=new_result_id(),
        work_order_id=work_order.work_order_id,
        execution_id="pexec-different-7777",
        task_id=manager_task.id,
        project_id=manager_task.project_id,
        correlation_id=work_order.correlation_id,
        status=ProgrammerResultStatus.SUCCESS,
        summary="Done",
    )
    with pytest.raises(ProgrammerLineageError, match="does not match execution"):
        bridge.receive_result(bad_exec_result, execution, work_order)


def test_critical_regression_autonomous_execution_envelope(manager_task: Task) -> None:
    """
    CRITICAL REGRESSION TEST:
    Demonstrates that:
    1. Manager creates a ProgrammerWorkOrder containing:
       - objective
       - context
       - path scope (allowed, writable, read_only, forbidden)
       - command scope (allowed commands with purposes)
       - constraints
       - acceptance criteria
       - budgets (iteration and time)
    2. Programmer subsystem accepts it, runs a mocked execution, and returns a ProgrammerResult with:
       - evidence
       - command records
       - test records
       - acceptance criteria evaluations
       - lineage intact back to Manager task
    3. The Programmer is capable of doing this WITHOUT asking Manager to restate basic authorization.
    """
    bridge = ProgrammerManagerBridge()

    # 1. Manager creates the bounded authorization envelope
    work_order = bridge.issue_work_order(
        task=manager_task,
        objective="Implement token rotation and blacklist cache",
        allowed_paths=["src/auth", "tests/auth"],
        writable_paths=["src/auth/tokens.py", "tests/auth/test_tokens.py"],
        read_only_paths=["src/auth/config.py"],
        forbidden_paths=["config/secrets.env", ".git"],
        allowed_commands=[
            AllowedCommand(command="pytest tests/auth -v", description="Execute auth test suite"),
            AllowedCommand(command="python -m mypy src/auth", description="Run static type checks"),
        ],
        constraints=[
            "Do not commit plaintext secret keys",
            "Maintain backward compatibility with v1 API clients",
        ],
        acceptance_criteria=[
            AcceptanceCriterion(
                criterion_id="ac-01",
                criterion_type=AcceptanceCriterionType.TEST_PASS,
                description="All unit tests pass",
                target="pytest tests/auth -v",
            ),
            AcceptanceCriterion(
                criterion_id="ac-02",
                criterion_type=AcceptanceCriterionType.TYPECHECK_PASS,
                description="Typecheck passes with zero errors",
                target="python -m mypy src/auth",
            ),
        ],
        technical_requirements=[
            "Use redis cluster client with connection pooling",
            "Handle connection timeouts gracefully",
        ],
        context={
            "redis_host": "127.0.0.1",
            "redis_port": 6379,
            "token_version": 2,
        },
        iteration_budget=8,
        time_budget=480,
    )

    # Verify the envelope contains all necessary operational permissions
    assert work_order.objective == "Implement token rotation and blacklist cache"
    assert "src/auth/tokens.py" in work_order.writable_paths
    assert "config/secrets.env" in work_order.forbidden_paths
    assert len(work_order.allowed_commands) == 2
    assert len(work_order.constraints) == 2
    assert len(work_order.acceptance_criteria) == 2
    assert work_order.context["redis_port"] == 6379
    assert work_order.iteration_budget == 8
    assert work_order.time_budget == 480

    # 2. Programmer executes within envelope without prompting Manager
    fake_worker = FakeProgrammerWorker(
        bridge=bridge,
        custom_diff="--- a/src/auth/tokens.py\n+++ b/src/auth/tokens.py\n@@ -10,3 +10,12 @@\n+def rotate_token():\n+    pass\n",
        files_changed=["src/auth/tokens.py"],
    )

    execution, result, worker_output = fake_worker.execute_work_order(work_order)

    # 3. Verify ProgrammerResult completeness and lineage
    assert result.is_success()
    assert result.work_order_id == work_order.work_order_id
    assert result.task_id == manager_task.id
    assert result.project_id == manager_task.project_id
    assert result.correlation_id == manager_task.metadata["correlation_id"]

    # Files changed strictly within writable scope
    assert result.files_changed == ["src/auth/tokens.py"]
    for f in result.files_changed:
        assert work_order.is_path_writable(f)
        assert not work_order.is_path_forbidden(f)

    # Evidence attached
    assert len(result.evidence) >= 1
    assert result.evidence[0].work_order_id == work_order.work_order_id
    assert "rotate_token" in result.evidence[0].provenance.get("diff", "")

    # Command records attached
    assert len(result.commands_executed) >= 1
    assert result.commands_executed[0].exit_code == 0

    # Test records attached
    assert len(result.test_results) >= 1
    assert result.test_results[0].tests_passed > 0
    assert result.test_results[0].tests_failed == 0

    # Acceptance criteria fully evaluated and passed
    assert len(result.acceptance_results) == 2
    assert result.is_acceptance_fully_verified()

    # WorkerOutput returned to Manager
    assert worker_output.success is True
    assert worker_output.metadata["acceptance_fully_verified"] is True
    assert worker_output.metadata["files_changed"] == ["src/auth/tokens.py"]

    # All events emitted on existing AutonomOS event system
    event_types = [e.event_type for e in bridge.events]
    assert event_types == [
        EventType.PROGRAMMER_REQUESTED,
        EventType.PROGRAMMER_STARTED,
        EventType.PROGRAMMER_COMPLETED,
    ]

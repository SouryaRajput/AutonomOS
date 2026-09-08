from __future__ import annotations

import pytest

from core.enums import RiskLevel
from core.programmer.contracts.blocker import ProgrammerBlocker
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.escalation import (
    EscalationCoordinator,
    EscalationStatus,
    ManagerEscalationResponse,
    ManagerEscalationResponseAction,
    ProgrammerEscalation,
    ProgrammerEscalationCategory,
)
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.identifiers import (
    ESCALATION_ID_PREFIX,
    new_escalation_id,
    new_execution_id,
    new_work_order_id,
    validate_escalation_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    InvalidProgrammerIdError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    ProgrammerBlockerCategory,
    ProgrammerBlockerSeverity,
    ProgrammerExecutionStatus,
)


@pytest.fixture
def sample_work_order():
    wo_id = new_work_order_id()
    return ProgrammerWorkOrder(
        work_order_id=wo_id,
        manager_task_id="mtask-esc-001",
        project_id="proj-esc",
        correlation_id="corr-esc",
        objective="Implement core module with external dependencies",
        allowed_paths=["/workspace/src", "/workspace/tests"],
        writable_paths=["/workspace/src"],
        allowed_commands=[AllowedCommand(command="pytest", description="Run pytest tests")],
        context={"framework": "fastapi"},
        iteration_budget=5,
    )


@pytest.fixture
def sample_execution(sample_work_order):
    exec_id = new_execution_id()
    execution = ProgrammerExecution(
        execution_id=exec_id,
        work_order_id=sample_work_order.work_order_id,
        task_id=sample_work_order.manager_task_id,
        project_id=sample_work_order.project_id,
        correlation_id=sample_work_order.correlation_id,
    )
    execution.status = ProgrammerExecutionStatus.RUNNING
    return execution


# ==============================================================================
# 1. Escalation Creation Tests
# ==============================================================================


def test_escalation_creation_and_fields(sample_work_order, sample_execution):
    coordinator = EscalationCoordinator()
    observed_facts = ["Command 'pip install libpq-dev' returned exit code 1", "Missing system header: libpq-fe.h"]
    attempted_actions = ["pip install libpq-dev"]
    suggested_options = ["Install postgresql development package", "Mock database client"]

    escalation = coordinator.create_escalation(
        execution=sample_execution,
        work_order=sample_work_order,
        category=ProgrammerEscalationCategory.PERMISSION,
        requested_decision="Require root permissions or pre-installed libpq-dev headers to proceed.",
        observed_facts=observed_facts,
        attempted_actions=attempted_actions,
        suggested_options=suggested_options,
        severity=ProgrammerBlockerSeverity.HIGH,
    )

    # Invariants & Identifiers
    validate_escalation_id(escalation.escalation_id)
    assert escalation.escalation_id.startswith(ESCALATION_ID_PREFIX)
    assert escalation.execution_id == sample_execution.execution_id
    assert escalation.work_order_id == sample_work_order.work_order_id
    assert escalation.category == ProgrammerEscalationCategory.PERMISSION
    assert escalation.severity == ProgrammerBlockerSeverity.HIGH
    assert escalation.status == EscalationStatus.PENDING
    assert escalation.observed_facts == observed_facts
    assert escalation.attempted_actions == attempted_actions
    assert escalation.suggested_options == suggested_options

    # Scope snapshot must capture current boundaries
    assert escalation.current_scope["allowed_paths"] == ["/workspace/src", "/workspace/tests"]
    assert escalation.current_scope["writable_paths"] == ["/workspace/src"]
    assert len(escalation.current_scope["allowed_commands"]) == 1

    # Execution must transition to BLOCKED
    assert sample_execution.status == ProgrammerExecutionStatus.BLOCKED
    assert len(sample_execution.blockers) == 1
    assert sample_execution.blockers[0].category == ProgrammerBlockerCategory.PERMISSION

    # Serialization roundtrip
    d = escalation.to_dict()
    assert d["escalation_id"] == escalation.escalation_id
    assert d["status"] == "PENDING"
    reconstructed = ProgrammerEscalation.from_dict(d)
    assert reconstructed.escalation_id == escalation.escalation_id
    assert reconstructed.category == ProgrammerEscalationCategory.PERMISSION
    assert reconstructed.status == EscalationStatus.PENDING


# ==============================================================================
# 2. Escalation Evidence Tests
# ==============================================================================


def test_escalation_with_evidence(sample_work_order, sample_execution):
    coordinator = EscalationCoordinator()
    evidence = [
        "vchk-01020304: command execution failed",
        "vevid-abcdef01: build output shows exit code 127",
    ]

    escalation = coordinator.create_escalation(
        execution=sample_execution,
        work_order=sample_work_order,
        category=ProgrammerEscalationCategory.DEPENDENCY,
        requested_decision="Missing external binary: protoc compiler.",
        evidence=evidence,
    )

    assert escalation.evidence == evidence
    d = escalation.to_dict()
    assert d["evidence"] == evidence

    reconstructed = ProgrammerEscalation.from_dict(d)
    assert reconstructed.evidence == evidence


# ==============================================================================
# 3. Manager Approval Tests
# ==============================================================================


def test_manager_approval(sample_work_order, sample_execution):
    coordinator = EscalationCoordinator()
    escalation = coordinator.create_escalation(
        execution=sample_execution,
        work_order=sample_work_order,
        category=ProgrammerEscalationCategory.SCOPE,
        requested_decision="Need read access to /workspace/docs for schema specifications.",
    )
    assert sample_execution.status == ProgrammerExecutionStatus.BLOCKED

    response = ManagerEscalationResponse(
        response_id="mresp-001",
        escalation_id=escalation.escalation_id,
        action=ManagerEscalationResponseAction.APPROVE,
        responder="manager",
        reason="Approved reading /workspace/docs",
    )

    final_status, revised_wo = coordinator.apply_response(
        escalation=escalation,
        response=response,
        execution=sample_execution,
        work_order=sample_work_order,
    )

    assert final_status == EscalationStatus.RESOLVED
    assert escalation.status == EscalationStatus.RESOLVED
    assert revised_wo is None
    # Execution must be unblocked
    assert sample_execution.status == ProgrammerExecutionStatus.RUNNING
    # Blocker must be marked resolved
    assert escalation.blocker.resolved_at is not None
    assert "Approved by manager" in escalation.blocker.resolution_notes


# ==============================================================================
# 4. Manager Denial Tests
# ==============================================================================


def test_manager_denial(sample_work_order, sample_execution):
    coordinator = EscalationCoordinator()
    escalation = coordinator.create_escalation(
        execution=sample_execution,
        work_order=sample_work_order,
        category=ProgrammerEscalationCategory.PERMISSION,
        requested_decision="Request sudo to alter system configuration.",
    )
    assert sample_execution.status == ProgrammerExecutionStatus.BLOCKED

    response = ManagerEscalationResponse(
        response_id="mresp-002",
        escalation_id=escalation.escalation_id,
        action=ManagerEscalationResponseAction.DENY,
        responder="user",
        reason="Privileged escalation is strictly prohibited.",
    )

    final_status, revised_wo = coordinator.apply_response(
        escalation=escalation,
        response=response,
        execution=sample_execution,
        work_order=sample_work_order,
    )

    assert final_status == EscalationStatus.REJECTED
    assert escalation.status == EscalationStatus.REJECTED
    assert revised_wo is None
    # Execution remains BLOCKED
    assert sample_execution.status == ProgrammerExecutionStatus.BLOCKED
    assert escalation.blocker.resolved_at is None


# ==============================================================================
# 5. Context Provision Tests
# ==============================================================================


def test_context_provision(sample_work_order, sample_execution):
    coordinator = EscalationCoordinator()
    escalation = coordinator.create_escalation(
        execution=sample_execution,
        work_order=sample_work_order,
        category=ProgrammerEscalationCategory.MISSING_CONTEXT,
        requested_decision="Clarification needed on expected payload schema for API endpoint.",
    )

    additional_context = {
        "api_schema": {"version": "v2", "strict_mode": True},
        "mock_server_port": 8080,
    }
    response = ManagerEscalationResponse(
        response_id="mresp-003",
        escalation_id=escalation.escalation_id,
        action=ManagerEscalationResponseAction.PROVIDE_CONTEXT,
        responder="manager",
        reason="Provided schema clarification and mock server port.",
        additional_context=additional_context,
    )

    final_status, revised_wo = coordinator.apply_response(
        escalation=escalation,
        response=response,
        execution=sample_execution,
        work_order=sample_work_order,
    )

    assert final_status == EscalationStatus.RESOLVED
    assert escalation.status == EscalationStatus.RESOLVED
    assert revised_wo is None
    assert sample_execution.status == ProgrammerExecutionStatus.RUNNING
    # Context updated in work_order
    assert sample_work_order.context["api_schema"] == {"version": "v2", "strict_mode": True}
    assert sample_work_order.context["mock_server_port"] == 8080
    assert sample_work_order.context["framework"] == "fastapi"


# ==============================================================================
# 6. Cancellation Tests
# ==============================================================================


def test_manager_cancellation(sample_work_order, sample_execution):
    coordinator = EscalationCoordinator()
    escalation = coordinator.create_escalation(
        execution=sample_execution,
        work_order=sample_work_order,
        category=ProgrammerEscalationCategory.ARCHITECTURAL,
        requested_decision="The requested microservice architecture conflicts with monolithic repo layout.",
    )

    response = ManagerEscalationResponse(
        response_id="mresp-004",
        escalation_id=escalation.escalation_id,
        action=ManagerEscalationResponseAction.CANCEL,
        responder="manager",
        reason="Aborting work order due to architectural mismatch; will re-plan at portfolio level.",
    )

    final_status, revised_wo = coordinator.apply_response(
        escalation=escalation,
        response=response,
        execution=sample_execution,
        work_order=sample_work_order,
    )

    assert final_status == EscalationStatus.CANCELLED
    assert escalation.status == EscalationStatus.CANCELLED
    assert revised_wo is None
    # Execution transitioned to CANCELLED with provenance
    assert sample_execution.status == ProgrammerExecutionStatus.CANCELLED
    assert sample_execution.cancellation is not None
    assert sample_execution.cancellation.requested_by == "manager"
    assert "Aborting work order" in sample_execution.cancellation.reason


# ==============================================================================
# 7. WorkOrder Revision Tests
# ==============================================================================


def test_work_order_revision(sample_work_order, sample_execution):
    coordinator = EscalationCoordinator()
    escalation = coordinator.create_escalation(
        execution=sample_execution,
        work_order=sample_work_order,
        category=ProgrammerEscalationCategory.SCOPE,
        requested_decision="Need expanded allowed_paths and writable_paths to build migrations.",
    )

    modifications = {
        "allowed_paths": ["/workspace/src", "/workspace/tests", "/workspace/migrations"],
        "writable_paths": ["/workspace/src", "/workspace/migrations"],
    }
    response = ManagerEscalationResponse(
        response_id="mresp-005",
        escalation_id=escalation.escalation_id,
        action=ManagerEscalationResponseAction.MODIFY_WORK_ORDER,
        responder="manager",
        reason="Approved expanded migrations path.",
        additional_context={"modifications": modifications},
    )

    final_status, revised_wo = coordinator.apply_response(
        escalation=escalation,
        response=response,
        execution=sample_execution,
        work_order=sample_work_order,
    )

    assert final_status == EscalationStatus.RESOLVED
    assert revised_wo is not None

    # Lineage and revision properties
    assert revised_wo.work_order_id != sample_work_order.work_order_id
    assert revised_wo.work_order_id.startswith("pwo-")
    assert revised_wo.parent_work_order_id == sample_work_order.work_order_id
    assert revised_wo.revision_number == 2
    assert revised_wo.manager_task_id == sample_work_order.manager_task_id
    assert revised_wo.project_id == sample_work_order.project_id
    assert revised_wo.correlation_id == sample_work_order.correlation_id

    # Modified fields
    assert "/workspace/migrations" in revised_wo.allowed_paths
    assert "/workspace/migrations" in revised_wo.writable_paths

    # Original work order remains untouched
    assert sample_work_order.revision_number == 1
    assert sample_work_order.parent_work_order_id is None
    assert "/workspace/migrations" not in sample_work_order.allowed_paths
    assert "/workspace/migrations" not in sample_work_order.writable_paths

    # Execution unblocked
    assert sample_execution.status == ProgrammerExecutionStatus.RUNNING


# ==============================================================================
# 8. Stale Escalation Response Tests
# ==============================================================================


def test_stale_escalation_response_rejected(sample_work_order, sample_execution):
    coordinator = EscalationCoordinator()
    escalation = coordinator.create_escalation(
        execution=sample_execution,
        work_order=sample_work_order,
        category=ProgrammerEscalationCategory.OTHER,
        requested_decision="Decide on testing strategy.",
    )

    first_response = ManagerEscalationResponse(
        response_id="mresp-006a",
        escalation_id=escalation.escalation_id,
        action=ManagerEscalationResponseAction.APPROVE,
        responder="manager",
        reason="First approval.",
    )
    coordinator.apply_response(
        escalation=escalation,
        response=first_response,
        execution=sample_execution,
        work_order=sample_work_order,
    )
    assert escalation.status == EscalationStatus.RESOLVED

    # Applying a second response to an already resolved escalation must fail
    second_response = ManagerEscalationResponse(
        response_id="mresp-006b",
        escalation_id=escalation.escalation_id,
        action=ManagerEscalationResponseAction.DENY,
        responder="manager",
        reason="Second response trying to deny.",
    )
    with pytest.raises(ProgrammerValidationError) as exc_info:
        coordinator.apply_response(
            escalation=escalation,
            response=second_response,
            execution=sample_execution,
            work_order=sample_work_order,
        )
    assert "Stale escalation response" in str(exc_info.value)


# ==============================================================================
# 9. Unauthorized Self-Approval Tests
# ==============================================================================


@pytest.mark.parametrize("bad_responder", [
    "programmer",
    "PROGRAMMER",
    "cline",
    "CLINE",
    "cline_backend",
    "worker",
    "worker.programmer",
    "agent",
    "subagent",
])
def test_unauthorized_self_approval_rejected(sample_work_order, sample_execution, bad_responder):
    escalation_id = new_escalation_id()

    # The response validation itself rejects unauthorized responders
    with pytest.raises(ProgrammerValidationError) as exc_info:
        ManagerEscalationResponse(
            response_id="mresp-bad-001",
            escalation_id=escalation_id,
            action=ManagerEscalationResponseAction.APPROVE,
            responder=bad_responder,
            reason="Self approving permission to run command.",
        )
    assert "Unauthorized escalation responder" in str(exc_info.value)


# ==============================================================================
# 10. Lineage Preservation Tests
# ==============================================================================


def test_lineage_preservation(sample_work_order, sample_execution):
    coordinator = EscalationCoordinator()
    escalation = coordinator.create_escalation(
        execution=sample_execution,
        work_order=sample_work_order,
        category=ProgrammerEscalationCategory.DEPENDENCY,
        requested_decision="Need dependency approval.",
    )

    # 1. Mismatched execution_id
    diff_exec = ProgrammerExecution(
        execution_id=new_execution_id(),
        work_order_id=sample_work_order.work_order_id,
        task_id=sample_work_order.manager_task_id,
        project_id=sample_work_order.project_id,
        correlation_id=sample_work_order.correlation_id,
    )
    resp = ManagerEscalationResponse(
        response_id="mresp-lin-01",
        escalation_id=escalation.escalation_id,
        action=ManagerEscalationResponseAction.APPROVE,
        responder="manager",
        reason="OK",
    )
    with pytest.raises(ProgrammerLineageError) as exc_info:
        coordinator.apply_response(
            escalation=escalation,
            response=resp,
            execution=diff_exec,
            work_order=sample_work_order,
        )
    assert "execution_id" in str(exc_info.value)

    # 2. Mismatched work_order_id
    diff_wo = ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id=sample_work_order.manager_task_id,
        project_id=sample_work_order.project_id,
        correlation_id=sample_work_order.correlation_id,
        objective="Different work order",
    )
    with pytest.raises(ProgrammerLineageError) as exc_info:
        coordinator.apply_response(
            escalation=escalation,
            response=resp,
            execution=sample_execution,
            work_order=diff_wo,
        )
    assert "work_order_id" in str(exc_info.value)

    # 3. Mismatched escalation_id on response
    mismatched_resp = ManagerEscalationResponse(
        response_id="mresp-lin-02",
        escalation_id=new_escalation_id(),
        action=ManagerEscalationResponseAction.APPROVE,
        responder="manager",
        reason="OK",
    )
    with pytest.raises(ProgrammerLineageError) as exc_info:
        coordinator.apply_response(
            escalation=escalation,
            response=mismatched_resp,
            execution=sample_execution,
            work_order=sample_work_order,
        )
    assert "escalation_id" in str(exc_info.value)

    # 4. Attempting to override immutable lineage in create_revision
    with pytest.raises(ProgrammerLineageError) as exc_info:
        sample_work_order.create_revision(modifications={"manager_task_id": "hijacked-task-id"})
    assert "immutable lineage field" in str(exc_info.value)


# ==============================================================================
# 11. Request Alternative and Coordinator Lookup Tests
# ==============================================================================


def test_request_alternative_and_lookups(sample_work_order, sample_execution):
    coordinator = EscalationCoordinator()
    escalation = coordinator.create_escalation(
        execution=sample_execution,
        work_order=sample_work_order,
        category=ProgrammerEscalationCategory.ARCHITECTURAL,
        requested_decision="Adopt NoSQL database instead of relational schema.",
        suggested_options=["Option A: MongoDB", "Option B: DynamoDB"],
    )

    # Lookups while pending
    assert coordinator.get_pending_escalation(sample_execution.execution_id) == escalation
    assert coordinator.get_escalation(escalation.escalation_id) == escalation
    assert len(coordinator.get_escalations(sample_execution.execution_id)) == 1

    resp = ManagerEscalationResponse(
        response_id="mresp-alt-01",
        escalation_id=escalation.escalation_id,
        action=ManagerEscalationResponseAction.REQUEST_ALTERNATIVE,
        responder="manager",
        reason="NoSQL is not allowed in this cluster; please propose relational partitioning.",
    )

    final_status, revised_wo = coordinator.apply_response(
        escalation=escalation,
        response=resp,
        execution=sample_execution,
        work_order=sample_work_order,
    )

    assert final_status == EscalationStatus.REJECTED
    assert escalation.status == EscalationStatus.REJECTED
    assert sample_execution.status == ProgrammerExecutionStatus.BLOCKED
    assert revised_wo is None

    # No pending escalations remain
    assert coordinator.get_pending_escalation(sample_execution.execution_id) is None

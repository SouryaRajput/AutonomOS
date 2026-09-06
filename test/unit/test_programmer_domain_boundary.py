from __future__ import annotations

import unittest
import uuid

from core.errors import AutonomOSError
from core.models import Evidence as RuntimeEvidence, Task, WorkerOutput
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.identifiers import (
    EXECUTION_ID_PREFIX,
    RESULT_ID_PREFIX,
    TRACE_ID_PREFIX,
    WORK_ORDER_ID_PREFIX,
    is_programmer_id,
    new_execution_id,
    new_result_id,
    new_trace_id,
    new_work_order_id,
    validate_execution_id,
    validate_result_id,
    validate_trace_id,
    validate_work_order_id,
)
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.trace import ProgrammerTrace, compute_sha256
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    InvalidProgrammerIdError,
    InvalidProgrammerTransitionError,
    ProgrammerBlockerError,
    ProgrammerError,
    ProgrammerLineageError,
)
from core.programmer.types import (
    ProgrammerActionType,
    ProgrammerExecutionStatus,
    ProgrammerResultStatus,
    ProgrammerWorkOrderStatus,
)


class TestProgrammerDomainBoundary(unittest.TestCase):
    """
    Unit tests establishing the clean Programmer subsystem domain boundary (Programmer V1, Step 1.1).
    Validates:
    - Programmer identity and distinguishability
    - Task lineage (ManagerTask -> ProgrammerWorkOrder -> ProgrammerExecution -> ProgrammerResult)
    - Trace and cryptographic provenance
    - Lifecycle state machine
    - Material blocker escalation
    - Full serialization roundtrip
    - Invalid identifier enforcement
    - Compatibility with existing worker conventions
    """

    def test_programmer_identity_prefixes_and_distinguishability(self):
        """Verify domain ID generation, prefix conformance, and clear distinguishability from other subsystems."""
        wo_id = new_work_order_id()
        exec_id = new_execution_id()
        trace_id = new_trace_id()
        res_id = new_result_id()

        self.assertTrue(wo_id.startswith(WORK_ORDER_ID_PREFIX))
        self.assertTrue(exec_id.startswith(EXECUTION_ID_PREFIX))
        self.assertTrue(trace_id.startswith(TRACE_ID_PREFIX))
        self.assertTrue(res_id.startswith(RESULT_ID_PREFIX))

        # Positive identification
        self.assertTrue(is_programmer_id(wo_id))
        self.assertTrue(is_programmer_id(exec_id))
        self.assertTrue(is_programmer_id(trace_id))
        self.assertTrue(is_programmer_id(res_id))

        # Distinguishability from Manager IDs
        self.assertFalse(is_programmer_id("cycle-12345"))
        self.assertFalse(is_programmer_id("decision-abcde"))
        self.assertFalse(is_programmer_id("plan-98765"))

        # Distinguishability from Researcher IDs
        self.assertFalse(is_programmer_id("req-12345"))
        self.assertFalse(is_programmer_id("f-abcde"))
        self.assertFalse(is_programmer_id("gap-12345"))
        self.assertFalse(is_programmer_id("rec-12345"))

        # Distinguishability from Crawler IDs
        self.assertFalse(is_programmer_id("ctask-web-1"))
        self.assertFalse(is_programmer_id("crep-fetch-2"))
        self.assertFalse(is_programmer_id("crawler.web.search"))

        # Distinguishability from generic task and project IDs
        self.assertFalse(is_programmer_id("task-001"))
        self.assertFalse(is_programmer_id("proj-autonomos"))
        self.assertFalse(is_programmer_id("worker.programmer"))
        self.assertFalse(is_programmer_id(""))
        self.assertFalse(is_programmer_id(None))

    def test_invalid_identifiers_rejection(self):
        """Verify strict validation errors for malformed or wrong-prefix identifiers."""
        # Work Order validation
        with self.assertRaises(InvalidProgrammerIdError) as ctx:
            validate_work_order_id("task-12345")
        self.assertEqual(ctx.exception.code, "INVALID_PROGRAMMER_ID")
        self.assertEqual(ctx.exception.identifier_type, "work_order_id")
        self.assertEqual(ctx.exception.expected_prefix, WORK_ORDER_ID_PREFIX)

        with self.assertRaises(InvalidProgrammerIdError):
            validate_work_order_id("pwo-")  # empty suffix

        with self.assertRaises(InvalidProgrammerIdError):
            validate_work_order_id("pwo-invalid @ id")  # forbidden character

        # Execution validation
        with self.assertRaises(InvalidProgrammerIdError):
            validate_execution_id("cycle-12345")
        with self.assertRaises(InvalidProgrammerIdError):
            validate_execution_id("pexec-")

        # Trace validation
        with self.assertRaises(InvalidProgrammerIdError):
            validate_trace_id("ev-12345")
        with self.assertRaises(InvalidProgrammerIdError):
            validate_trace_id("ptrace-")

        # Result validation
        with self.assertRaises(InvalidProgrammerIdError):
            validate_result_id("res-12345")
        with self.assertRaises(InvalidProgrammerIdError):
            validate_result_id("pres-")

    def test_task_lineage_preservation_golden_path(self):
        """
        Verify unforgeable lineage:
        ManagerTask -> ProgrammerWorkOrder -> ProgrammerExecution -> ProgrammerResult
        """
        # 1. Manager authorizes a Task
        manager_task = Task(
            id="task-oauth-01",
            project_id="proj-autonomos",
            title="Implement OAuth2 Callback Handler",
            objective="Add secure OAuth2 callback endpoint supporting Google and GitHub providers",
            metadata={"correlation_id": "corr-root-999", "priority": "HIGH"},
        )

        # 2. Construct authorized ProgrammerWorkOrder
        work_order = ProgrammerWorkOrder.from_task(manager_task)
        self.assertTrue(work_order.work_order_id.startswith(WORK_ORDER_ID_PREFIX))
        self.assertEqual(work_order.task_id, "task-oauth-01")
        self.assertEqual(work_order.project_id, "proj-autonomos")
        self.assertEqual(work_order.correlation_id, "corr-root-999")
        self.assertEqual(work_order.objective, manager_task.objective)
        self.assertEqual(work_order.status, ProgrammerWorkOrderStatus.ASSIGNED)

        # 3. Spawn ProgrammerExecution session
        execution = work_order.create_execution(worker_id="worker.programmer")
        self.assertTrue(execution.execution_id.startswith(EXECUTION_ID_PREFIX))
        self.assertEqual(execution.work_order_id, work_order.work_order_id)
        self.assertEqual(execution.task_id, "task-oauth-01")
        self.assertEqual(execution.project_id, "proj-autonomos")
        self.assertEqual(execution.correlation_id, "corr-root-999")
        self.assertEqual(execution.worker_id, "worker.programmer")
        self.assertEqual(execution.status, ProgrammerExecutionStatus.INITIALIZED)

        # 4. Record Execution Traces
        trace_init = execution.create_trace(
            action_type=ProgrammerActionType.INITIALIZE,
            action_details={"step": "inspect_requirements"},
        )
        self.assertTrue(trace_init.trace_id.startswith(TRACE_ID_PREFIX))
        self.assertEqual(trace_init.execution_id, execution.execution_id)
        self.assertEqual(trace_init.work_order_id, work_order.work_order_id)
        self.assertEqual(trace_init.task_id, "task-oauth-01")
        self.assertEqual(trace_init.project_id, "proj-autonomos")
        self.assertEqual(trace_init.correlation_id, "corr-root-999")
        self.assertTrue(len(trace_init.checksum) == 64)

        # 5. Transition execution and produce ProgrammerResult
        execution.transition_to(ProgrammerExecutionStatus.RUNNING)
        self.assertIsNotNone(execution.started_at)

        execution.transition_to(ProgrammerExecutionStatus.COMPLETED)
        self.assertIsNotNone(execution.completed_at)

        result = execution.create_result(
            status=ProgrammerResultStatus.SUCCESS,
            summary_for_manager="OAuth2 callback successfully implemented and validated.",
            evidence_ids=[trace_init.trace_id],
            artifacts=["src/auth/oauth.py"],
        )
        self.assertTrue(result.result_id.startswith(RESULT_ID_PREFIX))
        self.assertEqual(result.execution_id, execution.execution_id)
        self.assertEqual(result.work_order_id, work_order.work_order_id)
        self.assertEqual(result.task_id, "task-oauth-01")
        self.assertEqual(result.project_id, "proj-autonomos")
        self.assertEqual(result.correlation_id, "corr-root-999")
        self.assertEqual(result.status, ProgrammerResultStatus.SUCCESS)

        # 6. Validate lineage integrity
        result.validate_lineage(execution=execution, work_order=work_order)

    def test_lineage_tampering_and_mismatch_detection(self):
        """Verify that any disruption or forgery in the causal chain is detected and blocked."""
        # 1. Reject WorkOrder creation from invalid Task
        task_without_id = Task(id="", project_id="proj-1", title="No ID", objective="Obj")
        with self.assertRaises(ProgrammerLineageError):
            ProgrammerWorkOrder.from_task(task_without_id)

        task_without_project = Task(id="task-1", project_id="", title="No Project", objective="Obj")
        with self.assertRaises(ProgrammerLineageError):
            ProgrammerWorkOrder.from_task(task_without_project)

        # 2. Reject WorkOrder missing critical lineage links
        with self.assertRaises(ProgrammerLineageError):
            ProgrammerWorkOrder(
                work_order_id=new_work_order_id(),
                task_id="",  # missing link to ManagerTask
                project_id="proj-1",
                correlation_id="corr-1",
                objective="Objective",
            )

        with self.assertRaises(ProgrammerLineageError):
            ProgrammerWorkOrder(
                work_order_id=new_work_order_id(),
                task_id="task-1",
                project_id="",  # missing project boundary
                correlation_id="corr-1",
                objective="Objective",
            )

        # 3. Reject Execution with mismatched lineage
        wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            task_id="task-1",
            project_id="proj-1",
            correlation_id="corr-1",
            objective="Objective",
        )
        exec_obj = wo.create_execution()
        res_obj = exec_obj.create_result(status=ProgrammerResultStatus.SUCCESS)

        # Tampered execution_id in result
        tampered_result = ProgrammerResult(
            result_id=new_result_id(),
            execution_id=new_execution_id(),  # Forged execution ID
            work_order_id=wo.work_order_id,
            task_id=wo.task_id,
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            status=ProgrammerResultStatus.SUCCESS,
        )
        with self.assertRaises(ProgrammerLineageError):
            tampered_result.validate_lineage(execution=exec_obj, work_order=wo)

        # Tampered task_id in result
        tampered_task_result = ProgrammerResult(
            result_id=new_result_id(),
            execution_id=exec_obj.execution_id,
            work_order_id=wo.work_order_id,
            task_id="task-different",  # Forged task ID
            project_id=wo.project_id,
            correlation_id=wo.correlation_id,
            status=ProgrammerResultStatus.SUCCESS,
        )
        with self.assertRaises(ProgrammerLineageError):
            tampered_task_result.validate_lineage(execution=exec_obj, work_order=wo)

    def test_trace_and_cryptographic_provenance(self):
        """Verify that ProgrammerTrace computes deterministic SHA-256 and bridges into RuntimeEvidence."""
        trace = ProgrammerTrace(
            trace_id=new_trace_id(),
            execution_id=new_execution_id(),
            work_order_id=new_work_order_id(),
            task_id="task-42",
            project_id="proj-core",
            correlation_id="corr-42",
            action_type=ProgrammerActionType.EXECUTE,
            action_details={"command": "pytest test/unit", "exit_code": 0},
        )
        self.assertTrue(len(trace.checksum) == 64)

        # Check conversion to RuntimeEvidence
        runtime_ev = trace.to_runtime_evidence()
        self.assertIsInstance(runtime_ev, RuntimeEvidence)
        self.assertEqual(runtime_ev.id, trace.trace_id)
        self.assertEqual(runtime_ev.task_id, "task-42")
        self.assertEqual(runtime_ev.evidence_type, "PROGRAMMER_EXECUTE")
        self.assertEqual(runtime_ev.checksum, trace.checksum)
        self.assertIn("pytest test/unit", runtime_ev.data)

    def test_execution_lifecycle_state_machine(self):
        """Verify valid operational transitions and rejection of illegal state changes."""
        wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            task_id="task-sm-01",
            project_id="proj-sm",
            correlation_id="corr-sm",
            objective="State machine testing",
        )
        execution = wo.create_execution()
        self.assertEqual(execution.status, ProgrammerExecutionStatus.INITIALIZED)

        # Legal: INITIALIZED -> RUNNING
        execution.transition_to(ProgrammerExecutionStatus.RUNNING)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.RUNNING)
        self.assertIsNotNone(execution.started_at)

        # Legal: RUNNING -> BLOCKED
        execution.transition_to(ProgrammerExecutionStatus.BLOCKED, reason="Missing API key")
        self.assertEqual(execution.status, ProgrammerExecutionStatus.BLOCKED)

        # Legal: BLOCKED -> RUNNING
        execution.transition_to(ProgrammerExecutionStatus.RUNNING, reason="API key provided")
        self.assertEqual(execution.status, ProgrammerExecutionStatus.RUNNING)

        # Legal: RUNNING -> COMPLETED
        execution.transition_to(ProgrammerExecutionStatus.COMPLETED)
        self.assertEqual(execution.status, ProgrammerExecutionStatus.COMPLETED)
        self.assertIsNotNone(execution.completed_at)

        # Illegal: COMPLETED is terminal, cannot transition back to RUNNING
        with self.assertRaises(InvalidProgrammerTransitionError) as ctx:
            execution.transition_to(ProgrammerExecutionStatus.RUNNING)
        self.assertEqual(ctx.exception.code, "INVALID_PROGRAMMER_TRANSITION")
        self.assertEqual(ctx.exception.current_status, "COMPLETED")
        self.assertEqual(ctx.exception.target_status, "RUNNING")

        # Illegal: INITIALIZED -> COMPLETED without running
        exec2 = wo.create_execution()
        with self.assertRaises(InvalidProgrammerTransitionError):
            exec2.transition_to(ProgrammerExecutionStatus.COMPLETED)

    def test_material_blocker_escalation(self):
        """Verify that material blockers can be escalated cleanly through ProgrammerResult to Manager."""
        wo = ProgrammerWorkOrder(
            work_order_id=new_work_order_id(),
            task_id="task-blocker-01",
            project_id="proj-core",
            correlation_id="corr-block",
            objective="Update database schema",
        )
        execution = wo.create_execution()
        execution.transition_to(ProgrammerExecutionStatus.RUNNING)
        execution.transition_to(ProgrammerExecutionStatus.BLOCKED, reason="Database port 5432 unreachable")

        blocker_msg = "Database unreachable: port 5432 connection timed out."
        result = execution.create_result(
            status=ProgrammerResultStatus.BLOCKED,
            summary_for_manager="Execution blocked: cannot connect to migration database.",
            material_blockers=[blocker_msg],
        )
        self.assertEqual(result.status, ProgrammerResultStatus.BLOCKED)
        self.assertIn(blocker_msg, result.material_blockers)

        # Conversion to WorkerOutput for Manager consumption
        worker_output = result.to_worker_output()
        self.assertIsInstance(worker_output, WorkerOutput)
        self.assertFalse(worker_output.success)
        self.assertIn("Execution blocked", worker_output.summary)
        self.assertEqual(worker_output.error_message, blocker_msg)
        self.assertEqual(worker_output.metadata["programmer_status"], "BLOCKED")
        self.assertIn(blocker_msg, worker_output.metadata["material_blockers"])

    def test_serialization_fidelity_roundtrip(self):
        """Verify full round-trip dictionary serialization for all Programmer contracts."""
        # 1. ProgrammerWorkOrder
        wo = ProgrammerWorkOrder(
            work_order_id="pwo-abc12345",
            task_id="task-100",
            project_id="proj-100",
            correlation_id="corr-100",
            objective="Refactor payment gateway",
            status=ProgrammerWorkOrderStatus.IN_PROGRESS,
            metadata={"priority": 1, "owner": "manager"},
        )
        d_wo = wo.to_dict()
        restored_wo = ProgrammerWorkOrder.from_dict(d_wo)
        self.assertEqual(restored_wo.work_order_id, wo.work_order_id)
        self.assertEqual(restored_wo.task_id, wo.task_id)
        self.assertEqual(restored_wo.project_id, wo.project_id)
        self.assertEqual(restored_wo.correlation_id, wo.correlation_id)
        self.assertEqual(restored_wo.objective, wo.objective)
        self.assertEqual(restored_wo.status, ProgrammerWorkOrderStatus.IN_PROGRESS)
        self.assertEqual(restored_wo.metadata["owner"], "manager")

        # 2. ProgrammerTrace
        trace = ProgrammerTrace(
            trace_id="ptrace-12345678",
            execution_id="pexec-12345678",
            work_order_id="pwo-12345678",
            task_id="task-100",
            project_id="proj-100",
            correlation_id="corr-100",
            action_type=ProgrammerActionType.VALIDATE,
            action_details={"check": "linter", "passed": True},
        )
        d_trace = trace.to_dict()
        restored_trace = ProgrammerTrace.from_dict(d_trace)
        self.assertEqual(restored_trace.trace_id, trace.trace_id)
        self.assertEqual(restored_trace.action_type, ProgrammerActionType.VALIDATE)
        self.assertEqual(restored_trace.checksum, trace.checksum)
        self.assertEqual(restored_trace.action_details["passed"], True)

        # 3. ProgrammerExecution
        execution = ProgrammerExecution(
            execution_id="pexec-12345678",
            work_order_id="pwo-12345678",
            task_id="task-100",
            project_id="proj-100",
            correlation_id="corr-100",
            worker_id="worker.programmer",
            status=ProgrammerExecutionStatus.RUNNING,
            traces=[trace],
            metadata={"attempt": 1},
        )
        d_exec = execution.to_dict()
        restored_exec = ProgrammerExecution.from_dict(d_exec)
        self.assertEqual(restored_exec.execution_id, execution.execution_id)
        self.assertEqual(restored_exec.status, ProgrammerExecutionStatus.RUNNING)
        self.assertEqual(len(restored_exec.traces), 1)
        self.assertEqual(restored_exec.traces[0].trace_id, trace.trace_id)

        # 4. ProgrammerResult
        result = ProgrammerResult(
            result_id="pres-12345678",
            execution_id="pexec-12345678",
            work_order_id="pwo-12345678",
            task_id="task-100",
            project_id="proj-100",
            correlation_id="corr-100",
            status=ProgrammerResultStatus.SUCCESS,
            summary_for_manager="Payment gateway refactored cleanly.",
            evidence_ids=["ptrace-12345678"],
            artifacts=["src/payment/gateway.py"],
        )
        d_res = result.to_dict()
        restored_res = ProgrammerResult.from_dict(d_res)
        self.assertEqual(restored_res.result_id, result.result_id)
        self.assertEqual(restored_res.status, ProgrammerResultStatus.SUCCESS)
        self.assertEqual(restored_res.evidence_ids, ["ptrace-12345678"])
        self.assertEqual(restored_res.artifacts, ["src/payment/gateway.py"])

    def test_compatibility_with_existing_worker_conventions(self):
        """Verify interoperability with AutonomOS base errors, Task, and WorkerOutput."""
        # 1. Error hierarchy adheres to AutonomOSError
        err = InvalidProgrammerIdError("execution_id", "bad-id", "pexec-")
        self.assertIsInstance(err, ProgrammerError)
        self.assertIsInstance(err, AutonomOSError)
        d_err = err.to_dict()
        self.assertEqual(d_err["error"], "INVALID_PROGRAMMER_ID")
        self.assertIn("bad-id", d_err["message"])

        # 2. WorkerOutput conforms to runtime expectations
        res = ProgrammerResult(
            result_id="pres-88888888",
            execution_id="pexec-88888888",
            work_order_id="pwo-88888888",
            task_id="task-88",
            project_id="proj-88",
            correlation_id="corr-88",
            status=ProgrammerResultStatus.SUCCESS,
            summary_for_manager="Task finished",
        )
        worker_output = res.to_worker_output()
        self.assertEqual(worker_output.status, "COMPLETED")
        self.assertTrue(worker_output.success)
        self.assertEqual(worker_output.result["programmer_status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()

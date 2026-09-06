from __future__ import annotations

from dataclasses import asdict
import logging
from typing import Any, Callable, Optional, Sequence, Union
import uuid

from core.enums import RiskLevel, TaskStatus, WorkerStatus
from core.events.model import Event, new_event_id, utc_now
from core.events.types import EventSource, EventType
from core.models import Task, WorkerOutput
from core.programmer.contracts.acceptance_criteria import AcceptanceCriterion
from core.programmer.contracts.acceptance_result import AcceptanceCriterionResult
from core.programmer.contracts.blocker import ProgrammerBlocker
from core.programmer.contracts.cancellation import ProgrammerCancellation
from core.programmer.contracts.command_record import CommandExecutionRecord
from core.programmer.contracts.command_scope import AllowedCommand
from core.programmer.contracts.evidence import ProgrammerEvidence
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.identifiers import (
    new_blocker_id,
    new_execution_id,
    new_result_id,
    new_work_order_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.programmer.contracts.lifecycle import ProgrammerLifecycle
from core.programmer.contracts.research_reference import ResearchEvidenceReference
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.test_record import TestResultRecord
from core.programmer.contracts.trace import ProgrammerTrace, new_trace_id
from core.programmer.contracts.validator import ProgrammerWorkOrderValidator
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    InvalidProgrammerTransitionError,
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    AcceptanceCriterionType,
    AcceptanceStatus,
    ProgrammerActionType,
    ProgrammerBlockerCategory,
    ProgrammerBlockerSeverity,
    ProgrammerEvidenceType,
    ProgrammerExecutionStatus,
    ProgrammerResultStatus,
    ProgrammerWorkOrderStatus,
)

logger = logging.getLogger("AutonomOS.ProgrammerManagerBridge")


class ProgrammerManagerBridge:
    """
    Orchestration adapter bridging Manager decisions with the Programmer domain contracts.
    
    Architectural boundary rules:
    - Manager = WHAT + WHY + AUTHORITY + BOUNDARIES (issues work orders, sets scope/budgets, receives results)
    - Programmer = HOW + IMPLEMENTATION + VERIFICATION (executes within envelope, records evidence, returns results)
    
    This bridge:
    1. Translates Manager Task assignments into strongly-typed, validated ProgrammerWorkOrders.
    2. Emits authoritative lifecycle domain events on the AutonomOS event system:
       - PROGRAMMER_REQUESTED
       - PROGRAMMER_STARTED
       - PROGRAMMER_BLOCKED
       - PROGRAMMER_COMPLETED
       - PROGRAMMER_FAILED
       - PROGRAMMER_CANCELLED
    3. Enforces strict causal lineage from Manager Task down through Execution and Result.
    4. Facilitates blocker escalation to Manager and cancellation provenance.
    5. Converts ProgrammerResult into standard AutonomOS WorkerOutput.
    """

    def __init__(
        self,
        event_sink: Optional[Callable[..., Any]] = None,
        runtime: Optional[Any] = None,
    ) -> None:
        self.event_sink = event_sink
        self.runtime = runtime
        self.events: list[Event] = []
        self.work_orders: dict[str, ProgrammerWorkOrder] = {}
        self.executions: dict[str, ProgrammerExecution] = {}

    def emit_event(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        project_id: str,
        task_id: str,
        correlation_id: str,
        causation_id: Optional[str] = None,
        worker_id: Optional[str] = "worker.programmer",
        source: EventSource = EventSource.WORKER,
    ) -> Event:
        """
        Record and publish a domain event through the configured sink or runtime.
        """
        event = Event(
            event_id=new_event_id(),
            event_type=event_type,
            timestamp=utc_now(),
            source=source,
            correlation_id=correlation_id,
            causation_id=causation_id,
            project_id=project_id,
            task_id=task_id,
            worker_id=worker_id,
            payload=payload,
        )
        self.events.append(event)

        # Dispatch to injected sink or runtime logger if available
        if self.event_sink:
            try:
                self.event_sink(event)
            except TypeError:
                try:
                    self.event_sink(
                        event_type=event_type,
                        payload=payload,
                        project_id=project_id,
                        task_id=task_id,
                        correlation_id=correlation_id,
                        causation_id=causation_id,
                        worker_id=worker_id,
                        source=source,
                    )
                except Exception as e:
                    logger.debug(f"Could not emit event via event_sink: {e}")
            except Exception as e:
                logger.debug(f"Error in event_sink: {e}")
        elif self.runtime and hasattr(self.runtime, "log_event"):
            try:
                self.runtime.log_event(
                    event_type=event_type,
                    payload=payload,
                    project_id=project_id,
                    task_id=task_id,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                    worker_id=worker_id,
                )
            except Exception as e:
                logger.debug(f"Error in runtime.log_event: {e}")

        return event

    def issue_work_order(
        self,
        task: Union[Task, dict[str, Any]],
        objective: Optional[str] = None,
        allowed_paths: Optional[list[str]] = None,
        writable_paths: Optional[list[str]] = None,
        read_only_paths: Optional[list[str]] = None,
        forbidden_paths: Optional[list[str]] = None,
        allowed_commands: Optional[list[Any]] = None,
        constraints: Optional[list[str]] = None,
        acceptance_criteria: Optional[list[Any]] = None,
        instructions: Optional[list[str]] = None,
        technical_requirements: Optional[list[str]] = None,
        context: Optional[dict[str, Any]] = None,
        research_evidence: Optional[list[Any]] = None,
        iteration_budget: Optional[int] = None,
        time_budget: Optional[int] = None,
        risk_level: Optional[Union[RiskLevel, str]] = None,
        work_order_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> ProgrammerWorkOrder:
        """
        Manager issues an authorized, bounded ProgrammerWorkOrder from a Task.
        
        Validates the entire envelope before returning, and emits PROGRAMMER_REQUESTED.
        """
        # Resolve task properties
        if isinstance(task, dict):
            task_id = task.get("id") or ""
            project_id = task.get("project_id") or ""
            task_obj = task.get("objective") or task.get("title") or ""
            task_meta = dict(task.get("metadata", {}))
            task_crit = task.get("success_criteria", [])
            task_risk = task.get("risk", RiskLevel.LOW)
            correlation_id = task_meta.get("correlation_id") or task_id
        else:
            task_id = getattr(task, "id", "")
            project_id = getattr(task, "project_id", "")
            task_obj = getattr(task, "objective", "") or getattr(task, "title", "")
            task_meta = dict(getattr(task, "metadata", {}) or {})
            task_crit = getattr(task, "success_criteria", [])
            task_risk = getattr(task, "risk", RiskLevel.LOW)
            correlation_id = task_meta.get("correlation_id") or task_id

        if not task_id:
            raise ProgrammerLineageError("Cannot issue ProgrammerWorkOrder: task must have a non-empty id.")
        if not project_id:
            raise ProgrammerLineageError(f"Cannot issue ProgrammerWorkOrder: task '{task_id}' must have a non-empty project_id.")

        final_objective = objective or task_obj
        final_allowed_paths = allowed_paths if allowed_paths is not None else task_meta.get("allowed_paths", [])
        final_writable_paths = writable_paths if writable_paths is not None else task_meta.get("writable_paths", [])
        final_read_only_paths = read_only_paths if read_only_paths is not None else task_meta.get("read_only_paths", [])
        final_forbidden_paths = forbidden_paths if forbidden_paths is not None else task_meta.get("forbidden_paths", [])
        final_allowed_commands = allowed_commands if allowed_commands is not None else task_meta.get("allowed_commands", [])
        final_constraints = constraints if constraints is not None else task_meta.get("constraints", [])
        final_ac = acceptance_criteria if acceptance_criteria is not None else (task_meta.get("acceptance_criteria") or task_crit or [])
        final_instructions = instructions if instructions is not None else task_meta.get("instructions", [])
        final_tech_reqs = technical_requirements if technical_requirements is not None else task_meta.get("technical_requirements", [])
        final_context = context if context is not None else task_meta.get("context", {})
        final_research_evidence = research_evidence if research_evidence is not None else task_meta.get("research_evidence", [])
        final_iteration_budget = iteration_budget if iteration_budget is not None else int(task_meta.get("iteration_budget", 10))
        final_time_budget = time_budget if time_budget is not None else int(task_meta.get("time_budget", 600))
        final_risk = risk_level if risk_level is not None else task_risk
        final_work_order_id = work_order_id or new_work_order_id()

        work_order = ProgrammerWorkOrder(
            work_order_id=final_work_order_id,
            manager_task_id=task_id,
            project_id=project_id,
            correlation_id=correlation_id,
            objective=final_objective,
            instructions=final_instructions,
            context=final_context,
            allowed_paths=final_allowed_paths,
            writable_paths=final_writable_paths,
            read_only_paths=final_read_only_paths,
            forbidden_paths=final_forbidden_paths,
            allowed_commands=final_allowed_commands,
            constraints=final_constraints,
            technical_requirements=final_tech_reqs,
            acceptance_criteria=final_ac,
            research_evidence=final_research_evidence,
            iteration_budget=final_iteration_budget,
            time_budget=final_time_budget,
            risk_level=final_risk,
            status=ProgrammerWorkOrderStatus.ASSIGNED,
            metadata=task_meta,
        )

        # Deterministic validation
        work_order.validate()

        self.work_orders[work_order.work_order_id] = work_order

        # Emit PROGRAMMER_REQUESTED
        self.emit_event(
            event_type=EventType.PROGRAMMER_REQUESTED,
            payload={
                "work_order_id": work_order.work_order_id,
                "manager_task_id": work_order.manager_task_id,
                "objective": work_order.objective,
                "allowed_paths": work_order.allowed_paths,
                "writable_paths": work_order.writable_paths,
                "iteration_budget": work_order.iteration_budget,
                "time_budget": work_order.time_budget,
                "acceptance_criteria_count": len(work_order.acceptance_criteria),
            },
            project_id=work_order.project_id,
            task_id=work_order.manager_task_id,
            correlation_id=work_order.correlation_id,
            causation_id=causation_id,
            source=EventSource.MANAGER,
        )

        return work_order

    def dispatch_work_order(
        self,
        work_order: ProgrammerWorkOrder,
        worker_id: str = "worker.programmer",
        causation_id: Optional[str] = None,
    ) -> ProgrammerExecution:
        """
        Transition work order into execution, create the ProgrammerExecution context,
        and emit PROGRAMMER_STARTED.
        """
        work_order.validate()

        execution = work_order.create_execution(worker_id=worker_id)
        # Transition from REQUESTED to STARTING
        execution.transition_to(ProgrammerExecutionStatus.STARTING, reason="Execution initialized by manager dispatch")
        
        self.executions[execution.execution_id] = execution

        # Emit PROGRAMMER_STARTED
        self.emit_event(
            event_type=EventType.PROGRAMMER_STARTED,
            payload={
                "execution_id": execution.execution_id,
                "work_order_id": work_order.work_order_id,
                "objective": work_order.objective,
                "worker_id": worker_id,
                "allowed_paths": work_order.allowed_paths,
                "writable_paths": work_order.writable_paths,
            },
            project_id=work_order.project_id,
            task_id=work_order.manager_task_id,
            correlation_id=work_order.correlation_id,
            causation_id=causation_id,
            worker_id=worker_id,
            source=EventSource.WORKER,
        )

        return execution

    def escalate_blocker(
        self,
        execution: ProgrammerExecution,
        blocker: ProgrammerBlocker,
        work_order: Optional[ProgrammerWorkOrder] = None,
        causation_id: Optional[str] = None,
    ) -> ProgrammerBlocker:
        """
        Programmer reports a material blocker requiring Manager intervention.
        Transitions execution to BLOCKED and emits PROGRAMMER_BLOCKED.
        """
        if work_order:
            if execution.work_order_id != work_order.work_order_id:
                raise ProgrammerLineageError(
                    f"Execution work_order_id '{execution.work_order_id}' does not match work_order '{work_order.work_order_id}'"
                )

        execution.transition_to(
            ProgrammerExecutionStatus.BLOCKED,
            reason=blocker.description,
            blocker=blocker,
        )

        self.emit_event(
            event_type=EventType.PROGRAMMER_BLOCKED,
            payload={
                "execution_id": execution.execution_id,
                "work_order_id": execution.work_order_id,
                "blocker_id": blocker.blocker_id,
                "category": blocker.category.value if hasattr(blocker.category, "value") else str(blocker.category),
                "severity": blocker.severity.value if hasattr(blocker.severity, "value") else str(blocker.severity),
                "reason": blocker.description,
                "required_decision": blocker.required_decision,
                "is_material": getattr(blocker, "is_material", True),
            },
            project_id=execution.project_id,
            task_id=execution.task_id,
            correlation_id=execution.correlation_id,
            causation_id=causation_id,
            worker_id=execution.worker_id,
            source=EventSource.WORKER,
        )

        return blocker

    def resolve_blocker(
        self,
        execution: ProgrammerExecution,
        blocker_id: str,
        resolution: str,
        target_status: ProgrammerExecutionStatus = ProgrammerExecutionStatus.RUNNING,
        work_order: Optional[ProgrammerWorkOrder] = None,
        causation_id: Optional[str] = None,
    ) -> None:
        """
        Manager provides a resolution to an active blocker, resuming execution.
        """
        matching = [b for b in execution.blockers if b.blocker_id == blocker_id]
        if not matching:
            raise ProgrammerError(f"Blocker '{blocker_id}' not found in execution '{execution.execution_id}'")
        
        for b in matching:
            b.resolve(resolution)

        # Transition back to active operational state
        execution.transition_to(target_status, reason=f"Blocker '{blocker_id}' resolved: {resolution}")

    def cancel_execution(
        self,
        execution: ProgrammerExecution,
        requested_by: str,
        reason: str,
        work_order: Optional[ProgrammerWorkOrder] = None,
        causation_id: Optional[str] = None,
    ) -> ProgrammerCancellation:
        """
        Manager or authorized user cancels an active Programmer execution.
        Transitions execution to CANCELLED and emits PROGRAMMER_CANCELLED.
        """
        if work_order and execution.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"Execution work_order_id '{execution.work_order_id}' does not match work order '{work_order.work_order_id}'"
            )

        cancellation = ProgrammerCancellation(
            requested_by=requested_by,
            reason=reason,
            metadata={
                "execution_id": execution.execution_id,
                "work_order_id": execution.work_order_id,
            },
        )

        execution.transition_to(
            ProgrammerExecutionStatus.CANCELLED,
            reason=reason,
            cancellation=cancellation,
        )

        self.emit_event(
            event_type=EventType.PROGRAMMER_CANCELLED,
            payload={
                "execution_id": execution.execution_id,
                "work_order_id": execution.work_order_id,
                "requested_by": requested_by,
                "reason": reason,
            },
            project_id=execution.project_id,
            task_id=execution.task_id,
            correlation_id=execution.correlation_id,
            causation_id=causation_id,
            worker_id=execution.worker_id,
            source=EventSource.MANAGER if requested_by.upper() == "MANAGER" else EventSource.USER,
        )

        return cancellation

    def receive_result(
        self,
        result: ProgrammerResult,
        execution: ProgrammerExecution,
        work_order: ProgrammerWorkOrder,
        causation_id: Optional[str] = None,
    ) -> WorkerOutput:
        """
        Manager receives ProgrammerResult, validates strict causal lineage,
        emits terminal event (PROGRAMMER_COMPLETED / PROGRAMMER_FAILED), and converts
        to WorkerOutput.
        """
        # Strict lineage check
        if result.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"Result work_order_id '{result.work_order_id}' does not match work order '{work_order.work_order_id}'."
            )
        if result.task_id != work_order.manager_task_id:
            raise ProgrammerLineageError(
                f"Result task_id '{result.task_id}' does not match work order task_id '{work_order.manager_task_id}'."
            )
        if result.execution_id != execution.execution_id:
            raise ProgrammerLineageError(
                f"Result execution_id '{result.execution_id}' does not match execution '{execution.execution_id}'."
            )

        # Update execution state if not terminal
        if not execution.is_terminal:
            if result.is_success():
                # Allow transition from RUNNING/VERIFYING/COMPLETING to COMPLETED
                if execution.status in (ProgrammerExecutionStatus.RUNNING, ProgrammerExecutionStatus.STARTING):
                    execution.transition_to(ProgrammerExecutionStatus.VERIFYING, "Verifying results")
                    execution.transition_to(ProgrammerExecutionStatus.COMPLETING, "Completing execution")
                elif execution.status == ProgrammerExecutionStatus.VERIFYING:
                    execution.transition_to(ProgrammerExecutionStatus.COMPLETING, "Completing execution")
                execution.transition_to(ProgrammerExecutionStatus.COMPLETED, "Work order completed successfully")
            elif result.status == ProgrammerResultStatus.BLOCKED or execution.status == ProgrammerExecutionStatus.BLOCKED:
                if execution.status != ProgrammerExecutionStatus.BLOCKED:
                    execution.transition_to(ProgrammerExecutionStatus.BLOCKED, reason=result.summary or "Execution blocked")
            elif result.status == ProgrammerResultStatus.CANCELLED or execution.status == ProgrammerExecutionStatus.CANCELLED:
                if execution.status != ProgrammerExecutionStatus.CANCELLED:
                    execution.cancel(requested_by="manager", reason=result.summary or "Execution cancelled")
            else:
                execution.transition_to(ProgrammerExecutionStatus.FAILED, reason=result.summary or "Execution failed")

        # Emit corresponding event
        if result.is_success():
            self.emit_event(
                event_type=EventType.PROGRAMMER_COMPLETED,
                payload={
                    "execution_id": execution.execution_id,
                    "work_order_id": work_order.work_order_id,
                    "result_id": result.result_id,
                    "files_changed_count": len(result.files_changed),
                    "summary": result.summary,
                    "acceptance_fully_verified": result.is_acceptance_fully_verified(),
                    "commands_count": len(result.commands_executed),
                    "tests_count": len(result.test_results),
                },
                project_id=execution.project_id,
                task_id=execution.task_id,
                correlation_id=execution.correlation_id,
                causation_id=causation_id,
                worker_id=execution.worker_id,
                source=EventSource.WORKER,
            )
        elif result.status == ProgrammerResultStatus.BLOCKED or execution.status == ProgrammerExecutionStatus.BLOCKED:
            self.emit_event(
                event_type=EventType.PROGRAMMER_BLOCKED,
                payload={
                    "execution_id": execution.execution_id,
                    "work_order_id": work_order.work_order_id,
                    "result_id": result.result_id,
                    "reason": "; ".join(result.material_blockers or result.blockers) if (result.material_blockers or result.blockers) else result.summary,
                    "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                },
                project_id=execution.project_id,
                task_id=execution.task_id,
                correlation_id=execution.correlation_id,
                causation_id=causation_id,
                worker_id=execution.worker_id,
                source=EventSource.WORKER,
            )
        elif result.status == ProgrammerResultStatus.CANCELLED or execution.status == ProgrammerExecutionStatus.CANCELLED:
            self.emit_event(
                event_type=EventType.PROGRAMMER_CANCELLED,
                payload={
                    "execution_id": execution.execution_id,
                    "work_order_id": work_order.work_order_id,
                    "result_id": result.result_id,
                    "reason": result.summary or "Execution cancelled",
                    "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                },
                project_id=execution.project_id,
                task_id=execution.task_id,
                correlation_id=execution.correlation_id,
                causation_id=causation_id,
                worker_id=execution.worker_id,
                source=EventSource.WORKER,
            )
        else:
            self.emit_event(
                event_type=EventType.PROGRAMMER_FAILED,
                payload={
                    "execution_id": execution.execution_id,
                    "work_order_id": work_order.work_order_id,
                    "result_id": result.result_id,
                    "error": result.summary or "Programmer execution failed",
                    "reason": "; ".join(result.material_blockers or result.blockers) if (result.material_blockers or result.blockers) else result.summary,
                    "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                },
                project_id=execution.project_id,
                task_id=execution.task_id,
                correlation_id=execution.correlation_id,
                causation_id=causation_id,
                worker_id=execution.worker_id,
                source=EventSource.WORKER,
            )

        return result.to_worker_output()


class FakeProgrammerWorker:
    """
    Test double for Programmer subsystem execution.
    
    Simulates Programmer execution without running Cline, shell commands, or modifying
    the filesystem. Used for deterministic end-to-end contract and integration testing.
    """

    def __init__(
        self,
        bridge: ProgrammerManagerBridge,
        worker_id: str = "worker.programmer",
        simulate_failure: bool = False,
        simulate_blocker: Optional[ProgrammerBlocker] = None,
        custom_diff: str = "--- a/src/core.py\n+++ b/src/core.py\n@@ -1,3 +1,4 @@\n+# Added feature\n",
        files_changed: Optional[list[str]] = None,
    ) -> None:
        self.bridge = bridge
        self.worker_id = worker_id
        self.simulate_failure = simulate_failure
        self.simulate_blocker = simulate_blocker
        self.custom_diff = custom_diff
        self.files_changed = files_changed or ["src/core.py"]

    def execute_work_order(
        self,
        work_order: ProgrammerWorkOrder,
    ) -> tuple[ProgrammerExecution, ProgrammerResult, WorkerOutput]:
        """
        Simulate deterministic Programmer execution cycle:
        1. Dispatch work order (STARTING)
        2. Progress through RUNNING -> VERIFYING -> COMPLETING
        3. Evaluate acceptance criteria
        4. Package evidence and traces
        5. Return ProgrammerResult and WorkerOutput via Manager bridge
        """
        # 1. Dispatch
        execution = self.bridge.dispatch_work_order(work_order, worker_id=self.worker_id)

        # 2. Simulate blocker if configured
        if self.simulate_blocker:
            self.bridge.escalate_blocker(execution, self.simulate_blocker, work_order=work_order)
            result = ProgrammerResult(
                result_id=new_result_id(),
                work_order_id=work_order.work_order_id,
                execution_id=execution.execution_id,
                task_id=work_order.manager_task_id,
                project_id=work_order.project_id,
                correlation_id=work_order.correlation_id,
                status=ProgrammerResultStatus.BLOCKED,
                summary=f"Execution blocked: {self.simulate_blocker.description}",
                blockers=[self.simulate_blocker.description],
                material_blockers=[self.simulate_blocker.description],
            )
            worker_output = self.bridge.receive_result(result, execution, work_order)
            return execution, result, worker_output

        # 3. Progress to RUNNING
        execution.transition_to(ProgrammerExecutionStatus.RUNNING, "Analyzing requirements and formulating code modifications")

        # 4. Progress to VERIFYING
        execution.transition_to(ProgrammerExecutionStatus.VERIFYING, "Executing verification checks and test validation")

        # 5. Build acceptance results
        acceptance_results: list[AcceptanceCriterionResult] = []
        for ac in work_order.acceptance_criteria:
            ac_id = ac.criterion_id if hasattr(ac, "criterion_id") else ac.get("criterion_id", "ac-1")
            ac_desc = ac.description if hasattr(ac, "description") else ac.get("description", "Check")
            
            status = AcceptanceStatus.FAIL if self.simulate_failure else AcceptanceStatus.PASS
            acceptance_results.append(
                AcceptanceCriterionResult(
                    criterion_id=ac_id,
                    description=ac_desc,
                    status=status,
                    evidence_ids=[f"ev-{ac_id}"],
                    message="Verified via simulated deterministic test double",
                )
            )

        # 6. Build command and test records
        cmd_records = [
            CommandExecutionRecord(
                command="pytest test/unit -v",
                status="FAILED" if self.simulate_failure else "SUCCESS",
                exit_code=1 if self.simulate_failure else 0,
                duration_ms=450.0,
                output_snippet="Simulated test output: all assertions passed" if not self.simulate_failure else "Simulated failure",
            )
        ]
        test_records = [
            TestResultRecord(
                command="pytest test/unit -v",
                passed=not self.simulate_failure,
                exit_code=1 if self.simulate_failure else 0,
                duration_ms=450.0,
                tests_passed=0 if self.simulate_failure else 5,
                tests_failed=1 if self.simulate_failure else 0,
            )
        ]

        # 7. Progress to COMPLETING
        execution.transition_to(ProgrammerExecutionStatus.COMPLETING, "Formulating final ProgrammerResult package")

        # 8. Produce ProgrammerResult
        status = ProgrammerResultStatus.FAILED if self.simulate_failure else ProgrammerResultStatus.SUCCESS
        summary = "Simulated failure occurred" if self.simulate_failure else f"Successfully implemented objective: '{work_order.objective}'"

        evidence = [
            ProgrammerEvidence(
                evidence_id="pe-sim-01",
                evidence_type=ProgrammerEvidenceType.DIFF,
                source="FakeProgrammerWorker",
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
                summary="Simulated patch diff",
                provenance={"diff": self.custom_diff},
            )
        ]

        result = ProgrammerResult(
            result_id=new_result_id(),
            work_order_id=work_order.work_order_id,
            execution_id=execution.execution_id,
            task_id=work_order.manager_task_id,
            project_id=work_order.project_id,
            correlation_id=work_order.correlation_id,
            status=status,
            summary=summary,
            files_changed=self.files_changed if not self.simulate_failure else [],
            diff_summary=self.custom_diff if not self.simulate_failure else "",
            commands_executed=cmd_records,
            test_results=test_records,
            acceptance_results=acceptance_results,
            evidence=evidence,
            evidence_ids=[e.evidence_id for e in evidence],
        )

        # 9. Manager receives result via bridge
        worker_output = self.bridge.receive_result(result, execution, work_order)

        return execution, result, worker_output

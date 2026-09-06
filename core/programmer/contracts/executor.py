from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Callable, Optional, Sequence, Union

from core.programmer.contracts.capability_binding import ClineCapabilityBinding
from core.programmer.contracts.coding_agent import (
    CodingAgentBackend,
    CodingAgentCancellationRequest,
    CodingAgentEvent,
    CodingAgentRequest,
    CodingAgentResult,
    MockCodingAgentBackend,
)
from core.programmer.contracts.command_record import CommandExecutionRecord
from core.programmer.contracts.event_translation import (
    ClineEventTranslator,
    ProgrammerExecutionEvent,
    ProgrammerExecutionTraceCollector,
)
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_result_id,
)
from core.programmer.contracts.prompt_builder import ProgrammerPromptBuilder
from core.programmer.contracts.provisioner import WorkspaceProvisioner
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    CodingAgentExecutionStatus,
    ExecutionContextStatus,
    ProgrammerActionType,
    ProgrammerExecutionEventType,
    ProgrammerExecutionStatus,
    ProgrammerResultStatus,
    WorkspaceIsolationMode,
)

logger = logging.getLogger("AutonomOS.Programmer.Executor")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ==============================================================================
# 1. Controlled Execution Outcome Contract
# ==============================================================================


@dataclass
class ControlledExecutionOutcome:
    """
    Structured outcome contract returned by ControlledProgrammerExecutor.
    
    Guarantees:
    - Preserves causal lineage across Execution, Context, Backend, Trace, and Result.
    - Clearly distinguishes between agent execution success and final task verification.
    - Surfaces whether the execution succeeded, was cancelled, or failed.
    """
    execution: ProgrammerExecution
    context: Optional[ProgrammerExecutionContext] = None
    capability_binding: Optional[ClineCapabilityBinding] = None
    backend_result: Optional[CodingAgentResult] = None
    result: Optional[ProgrammerResult] = None
    trace_collector: Optional[ProgrammerExecutionTraceCollector] = None
    error_message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        """
        Check whether the controlled agent execution finished normally without fatal error.
        
        NOTE: This does NOT mean final task verification passed. Final verification
        is performed in subsequent phases; result.metadata['verification_status'] is 'UNVERIFIED'.
        """
        return (
            self.execution.status == ProgrammerExecutionStatus.COMPLETING
            and self.backend_result is not None
            and self.backend_result.is_successful()
            and not self.error_message
        )

    @property
    def is_cancelled(self) -> bool:
        """Check whether execution was cancelled by Manager or caller."""
        return (
            self.execution.status == ProgrammerExecutionStatus.CANCELLED
            or (self.backend_result is not None and self.backend_result.is_cancelled())
        )

    @property
    def is_failed(self) -> bool:
        """Check whether execution failed during setup or agent execution."""
        return (
            self.execution.status == ProgrammerExecutionStatus.FAILED
            or (self.backend_result is not None and self.backend_result.is_failed())
            or bool(self.error_message and not self.is_cancelled)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution.execution_id,
            "work_order_id": self.execution.work_order_id,
            "task_id": self.execution.task_id,
            "project_id": self.execution.project_id,
            "execution_status": (
                self.execution.status.value
                if isinstance(self.execution.status, ProgrammerExecutionStatus)
                else str(self.execution.status)
            ),
            "is_success": self.is_success,
            "is_cancelled": self.is_cancelled,
            "is_failed": self.is_failed,
            "error_message": self.error_message,
            "backend_result": self.backend_result.to_dict() if self.backend_result else None,
            "result": self.result.to_dict() if self.result else None,
            "event_count": self.trace_collector.event_count if self.trace_collector else 0,
            "metadata": dict(self.metadata),
        }


# ==============================================================================
# 2. Controlled Programmer Executor
# ==============================================================================


class ControlledProgrammerExecutor:
    """
    Controlled end-to-end execution coordinator for the Programmer subsystem.
    
    Architecture:
        ProgrammerWorkOrder
                 ↓
        ProgrammerExecution (REQUESTED → STARTING)
                 ↓
        ProgrammerExecutionContext (WorkspaceProvisioner)
                 ↓
        ClineCapabilityBinding (Policy Enclosure: Filesystem & Command)
                 ↓
        ProgrammerPromptBuilder (Instruction & Quarantine Package)
                 ↓
        CodingAgentBackend (RUNNING)
                 ↓
        Execution Trace Collector (Event Translation & Normalization)
                 ↓
        ProgrammerExecution (COMPLETING / FAILED / CANCELLED)
                 ↓
        Preliminary ProgrammerResult (verification_status = "UNVERIFIED")
    
    Critical Architectural Guarantees:
    1. STRICT CAPABILITY ENCLOSURE: The coding agent operates strictly inside the
       execution capability boundary. All filesystem changes and commands are policy-mediated.
    2. LIFECYCLE PROGRESSION: Transitions deterministically through
       REQUESTED → STARTING → RUNNING → COMPLETING (or FAILED / CANCELLED).
    3. PRELIMINARY RESULT ≠ FINAL VERIFICATION: Agent execution success does NOT equal
       task completion. The produced ProgrammerResult records status PARTIAL and
       metadata['verification_status'] = "UNVERIFIED".
    4. FAIL-CLOSED: Malformed work orders, provisioning errors, or unhandled exceptions
       transition to FAILED with full lineage and diagnostic reasons.
    5. NO PROHIBITED FEATURES: No autonomous retries, no git integration, no production
       deployment, no self-grading, and no unapproved scope expansion.
    """

    def __init__(
        self,
        provisioner: Optional[WorkspaceProvisioner] = None,
        backend: Optional[CodingAgentBackend] = None,
        project_resolver: Optional[Any] = None,
        default_projects_dir: Optional[str] = None,
    ) -> None:
        self.provisioner = provisioner or WorkspaceProvisioner(
            project_resolver=project_resolver,
            default_projects_dir=default_projects_dir,
        )
        self.backend = backend
        self._active_executions: dict[str, ProgrammerExecution] = {}
        self._active_backends: dict[str, CodingAgentBackend] = {}

    def execute(
        self,
        work_order: ProgrammerWorkOrder,
        execution: Optional[ProgrammerExecution] = None,
        backend: Optional[CodingAgentBackend] = None,
        on_event: Optional[Callable[[ProgrammerExecutionEvent], None]] = None,
        root_path_override: Optional[str] = None,
        system_prompt_override: Optional[str] = None,
        prompt_override: Optional[str] = None,
        isolation_mode: Optional[Union[WorkspaceIsolationMode, str]] = None,
    ) -> ControlledExecutionOutcome:
        """
        Execute an end-to-end controlled Programmer implementation session.
        
        Args:
            work_order: Validated ProgrammerWorkOrder authorizing the task.
            execution: Optional existing ProgrammerExecution session. If None, created.
            backend: Optional CodingAgentBackend override. Defaults to self.backend or MockCodingAgentBackend.
            on_event: Optional streaming callback receiving normalized ProgrammerExecutionEvent instances.
            root_path_override: Optional workspace root directory path.
            system_prompt_override: Optional system prompt text override.
            prompt_override: Optional user prompt text override.
            isolation_mode: Optional workspace isolation mode (SHARED or ISOLATED).
            
        Returns:
            ControlledExecutionOutcome encapsulating execution state, capabilities, trace, and preliminary result.
        """
        # ----------------------------------------------------------------------
        # Step 1: Validate WorkOrder
        # ----------------------------------------------------------------------
        if work_order is None:
            raise ProgrammerValidationError("ProgrammerWorkOrder cannot be None.")
        work_order.validate()

        # ----------------------------------------------------------------------
        # Step 2: Lineage / Execution Binding
        # ----------------------------------------------------------------------
        if execution is None:
            execution = ProgrammerExecution(
                execution_id=new_execution_id(),
                work_order_id=work_order.work_order_id,
                task_id=work_order.task_id,
                project_id=work_order.project_id,
                correlation_id=work_order.correlation_id,
                status=ProgrammerExecutionStatus.REQUESTED,
            )
        else:
            if execution.work_order_id != work_order.work_order_id:
                raise ProgrammerLineageError(
                    f"Lineage mismatch: execution work_order_id '{execution.work_order_id}' "
                    f"does not match work order '{work_order.work_order_id}'."
                )
            if execution.task_id != work_order.task_id:
                raise ProgrammerLineageError(
                    f"Lineage mismatch: execution task_id '{execution.task_id}' "
                    f"does not match work order '{work_order.task_id}'."
                )
            if execution.project_id != work_order.project_id:
                raise ProgrammerLineageError(
                    f"Lineage mismatch: execution project_id '{execution.project_id}' "
                    f"does not match work order '{work_order.project_id}'."
                )

        self._active_executions[execution.execution_id] = execution

        # ----------------------------------------------------------------------
        # Step 3: Lifecycle Transition: REQUESTED → STARTING
        # ----------------------------------------------------------------------
        if execution.status == ProgrammerExecutionStatus.REQUESTED:
            execution.transition_to(
                ProgrammerExecutionStatus.STARTING,
                reason="Starting controlled programmer execution",
            )
        execution.create_trace(
            ProgrammerActionType.INITIALIZE,
            {"work_order_id": work_order.work_order_id},
        )

        # ----------------------------------------------------------------------
        # Step 4: Workspace Provisioning & ExecutionContext Creation
        # ----------------------------------------------------------------------
        prov_result = self.provisioner.provision(
            work_order=work_order,
            execution=execution,
            isolation_mode=isolation_mode,
            root_path_override=root_path_override,
        )

        if not prov_result.is_ready() or prov_result.execution_context is None:
            err_msg = prov_result.error_message or "Workspace provisioning failed."
            logger.error(f"Execution {execution.execution_id} provisioning failed: {err_msg}")
            if not execution.is_terminal:
                execution.transition_to(
                    ProgrammerExecutionStatus.FAILED,
                    reason=err_msg,
                )
            fail_result = ProgrammerResult(
                result_id=new_result_id(),
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
                task_id=work_order.task_id,
                project_id=work_order.project_id,
                correlation_id=work_order.correlation_id,
                status=ProgrammerResultStatus.FAILED,
                summary=f"Workspace provisioning failed: {err_msg}",
                summary_for_manager=f"Workspace provisioning failed: {err_msg}",
                metadata={
                    "verification_status": "UNVERIFIED",
                    "agent_execution_status": "FAILED",
                    "implementation_status": "FAILED",
                    "error_code": str(prov_result.error_code),
                },
                blockers=[err_msg],
                material_blockers=[err_msg],
            )
            return ControlledExecutionOutcome(
                execution=execution,
                result=fail_result,
                error_message=err_msg,
            )

        context = prov_result.execution_context

        # ----------------------------------------------------------------------
        # Step 5: Setup Trace Collector & Capability Binding
        # ----------------------------------------------------------------------
        trace_collector = ProgrammerExecutionTraceCollector(
            execution_id=execution.execution_id,
            work_order_id=work_order.work_order_id,
        )
        capability_binding = ClineCapabilityBinding(
            context=context,
            trace_collector=trace_collector,
            on_event=on_event,
        )
        context.metadata["capability_binding"] = capability_binding

        # ----------------------------------------------------------------------
        # Step 6: Resolve Backend & Build Prompt Package / CodingAgentRequest
        # ----------------------------------------------------------------------
        active_backend = backend or self.backend or MockCodingAgentBackend()
        self._active_backends[execution.execution_id] = active_backend

        request = ProgrammerPromptBuilder.build_request(
            work_order=work_order,
            context=context,
            backend_type=active_backend.backend_type,
            system_prompt_override=system_prompt_override,
            prompt_override=prompt_override,
            metadata={"capability_binding": capability_binding},
        )

        # ----------------------------------------------------------------------
        # Step 7: Lifecycle Transition: STARTING → RUNNING
        # ----------------------------------------------------------------------
        if execution.status == ProgrammerExecutionStatus.STARTING:
            execution.transition_to(
                ProgrammerExecutionStatus.RUNNING,
                reason="Coding agent execution started",
            )
        execution.create_trace(
            ProgrammerActionType.EXECUTE,
            {
                "backend_type": str(active_backend.backend_type),
                "request_id": request.request_id,
            },
        )

        # ----------------------------------------------------------------------
        # Step 8: Execute Backend with Event Routing
        # ----------------------------------------------------------------------
        def internal_event_handler(event: CodingAgentEvent) -> None:
            """Translate and record agent events into normalized execution trace."""
            try:
                pevt = ClineEventTranslator.translate_event(
                    raw_event=event.to_dict(),
                    execution_id=execution.execution_id,
                    work_order_id=work_order.work_order_id,
                    sequence_number=0,
                    correlation_id=work_order.correlation_id,
                )
                trace_collector.add_event(pevt)
                if on_event:
                    try:
                        on_event(pevt)
                    except Exception as cb_err:
                        logger.warning(f"Error invoking on_event callback: {cb_err}")
            except Exception as ev_err:
                logger.warning(f"Error handling event in executor: {ev_err}")

        try:
            backend_result = active_backend.execute(
                request=request,
                event_handler=internal_event_handler,
            )
        except Exception as exec_err:
            err_msg = f"Fatal backend execution error: {exec_err}"
            logger.error(err_msg)
            if not execution.is_terminal:
                execution.transition_to(
                    ProgrammerExecutionStatus.FAILED,
                    reason=err_msg,
                )
            fail_result = ProgrammerResult(
                result_id=new_result_id(),
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
                task_id=work_order.task_id,
                project_id=work_order.project_id,
                correlation_id=work_order.correlation_id,
                status=ProgrammerResultStatus.FAILED,
                summary=err_msg,
                summary_for_manager=err_msg,
                metadata={
                    "verification_status": "UNVERIFIED",
                    "agent_execution_status": "FAILED",
                    "implementation_status": "FAILED",
                },
                blockers=[err_msg],
                material_blockers=[err_msg],
            )
            return ControlledExecutionOutcome(
                execution=execution,
                context=context,
                capability_binding=capability_binding,
                result=fail_result,
                trace_collector=trace_collector,
                error_message=err_msg,
            )

        # ----------------------------------------------------------------------
        # Step 9: Evaluate Backend Result & Lifecycle Progression
        # ----------------------------------------------------------------------
        if backend_result.is_cancelled():
            if not execution.is_terminal:
                execution.cancel(
                    requested_by="system",
                    reason=backend_result.error_message or "Execution was cancelled.",
                )
            result_status = ProgrammerResultStatus.CANCELLED
            agent_status = "CANCELLED"
            impl_status = "CANCELLED"
            summary_for_manager = f"Execution cancelled: {backend_result.error_message or 'Operation cancelled'}"
        elif backend_result.is_failed():
            if not execution.is_terminal:
                execution.transition_to(
                    ProgrammerExecutionStatus.FAILED,
                    reason=backend_result.error_message or "Agent execution failed.",
                )
            result_status = ProgrammerResultStatus.FAILED
            agent_status = "FAILED"
            impl_status = "FAILED"
            summary_for_manager = f"Coding agent execution failed: {backend_result.error_message or 'Fatal failure'}"
        else:
            # Backend execution succeeded.
            # INVARIANT: Transition to COMPLETING. Do NOT transition to COMPLETED yet.
            # Final verification is deferred to Phase 4.
            if execution.status == ProgrammerExecutionStatus.RUNNING:
                execution.transition_to(
                    ProgrammerExecutionStatus.COMPLETING,
                    reason="Coding agent execution completed successfully. Verification pending.",
                )
            execution.create_trace(
                ProgrammerActionType.REPORT,
                {
                    "status": "COMPLETING",
                    "backend_result_id": backend_result.result_id,
                },
            )
            result_status = ProgrammerResultStatus.PARTIAL
            agent_status = "COMPLETED"
            impl_status = "COMPLETED"
            summary_for_manager = "Coding agent implementation finished. Verification not yet performed."

        # ----------------------------------------------------------------------
        # Step 10: Aggregate Observed Operations from Trace Collector
        # ----------------------------------------------------------------------
        files_changed_set: set[str] = set()
        files_created_set: set[str] = set()
        files_deleted_set: set[str] = set()
        commands_executed: list[CommandExecutionRecord] = []
        denied_file_ops_count = 0
        denied_command_ops_count = 0

        for ev in trace_collector.get_file_operations():
            allowed = ev.payload.get("allowed", False)
            if not allowed:
                denied_file_ops_count += 1
                continue

            op = str(ev.payload.get("operation", "")).upper()
            path = ev.payload.get("path")
            if op in ("CREATE", "FILE_CREATE") and path:
                files_created_set.add(str(path))
                files_changed_set.add(str(path))
            elif op in ("WRITE", "FILE_WRITE") and path:
                files_changed_set.add(str(path))
            elif op in ("DELETE", "FILE_DELETE") and path:
                files_deleted_set.add(str(path))
            elif op in ("RENAME", "FILE_RENAME"):
                src = ev.payload.get("source_path")
                dst = ev.payload.get("destination_path")
                if src:
                    files_deleted_set.add(str(src))
                if dst:
                    files_created_set.add(str(dst))
                    files_changed_set.add(str(dst))

        for ev in trace_collector.get_command_operations():
            allowed = ev.payload.get("allowed", False)
            if not allowed:
                denied_command_ops_count += 1
                continue

            cmd_str = str(ev.payload.get("command", ""))
            exit_code = int(ev.payload.get("exit_code", 0))
            duration_ms = float(ev.payload.get("duration_ms", 0.0))
            cmd_record = CommandExecutionRecord(
                command=cmd_str,
                status="SUCCESS" if exit_code == 0 else "FAILURE",
                exit_code=exit_code,
                duration_ms=duration_ms,
                metadata=dict(ev.payload),
            )
            commands_executed.append(cmd_record)

        # ----------------------------------------------------------------------
        # Step 11: Produce Preliminary ProgrammerResult
        # ----------------------------------------------------------------------
        result_metadata = {
            "verification_status": "UNVERIFIED",
            "agent_execution_status": agent_status,
            "implementation_status": impl_status,
            "tool_calls_count": backend_result.tool_calls_count if backend_result else 0,
            "tokens_used": backend_result.tokens_used if backend_result else {},
            "events_count": trace_collector.event_count,
            "denied_file_operations": denied_file_ops_count,
            "denied_command_operations": denied_command_ops_count,
        }

        result = ProgrammerResult(
            result_id=new_result_id(),
            execution_id=execution.execution_id,
            work_order_id=work_order.work_order_id,
            task_id=work_order.task_id,
            project_id=work_order.project_id,
            correlation_id=work_order.correlation_id,
            status=result_status,
            summary=summary_for_manager,
            summary_for_manager=summary_for_manager,
            files_changed=sorted(list(files_changed_set)),
            files_created=sorted(list(files_created_set)),
            files_deleted=sorted(list(files_deleted_set)),
            commands_executed=commands_executed,
            test_results=[],  # Final verification deferred to Phase 4
            validation_results=[],  # Final verification deferred to Phase 4
            acceptance_results=[],  # Final verification deferred to Phase 4
            blockers=[b.description for b in execution.active_blockers],
            material_blockers=[b.description for b in execution.active_blockers],
            metadata=result_metadata,
            trace={
                "execution_id": execution.execution_id,
                "work_order_id": work_order.work_order_id,
                "workspace_id": context.workspace_id,
                "backend_type": str(active_backend.backend_type),
            },
        )

        return ControlledExecutionOutcome(
            execution=execution,
            context=context,
            capability_binding=capability_binding,
            backend_result=backend_result,
            result=result,
            trace_collector=trace_collector,
            error_message=backend_result.error_message if backend_result and backend_result.error_message else None,
            metadata={
                "denied_file_operations": denied_file_ops_count,
                "denied_command_operations": denied_command_ops_count,
            },
        )

    def execute_correction(
        self,
        work_order: ProgrammerWorkOrder,
        execution: ProgrammerExecution,
        context: ProgrammerExecutionContext,
        correction_prompt: str,
        backend: Optional[CodingAgentBackend] = None,
        on_event: Optional[Callable[[ProgrammerExecutionEvent], None]] = None,
        trace_collector: Optional[ProgrammerExecutionTraceCollector] = None,
        capability_binding: Optional[ClineCapabilityBinding] = None,
        system_prompt_override: Optional[str] = None,
    ) -> ControlledExecutionOutcome:
        """
        Execute a controlled Programmer correction turn within an existing ExecutionContext.
        
        Guarantees:
        - Reuses the existing workspace and ExecutionContext (no re-provisioning).
        - Writable paths and command policies remain strictly immutable.
        - Transitions execution to RUNNING, sends the correction prompt to backend,
          and processes normalized events.
        - Returns an updated ControlledExecutionOutcome.
        """
        if work_order is None:
            raise ProgrammerValidationError("ProgrammerWorkOrder cannot be None.")
        if execution is None:
            raise ProgrammerValidationError("ProgrammerExecution cannot be None.")
        if context is None:
            raise ProgrammerValidationError("ProgrammerExecutionContext cannot be None.")

        if execution.work_order_id != work_order.work_order_id:
            raise ProgrammerLineageError(
                f"Lineage mismatch: execution work_order_id '{execution.work_order_id}' "
                f"does not match work order '{work_order.work_order_id}'."
            )

        if execution.is_terminal:
            raise ProgrammerLineageError(
                f"Cannot execute correction on terminal execution in state '{execution.status.value}'."
            )

        # Transition execution to RUNNING for the correction turn
        if execution.status in (ProgrammerExecutionStatus.VERIFYING, ProgrammerExecutionStatus.COMPLETING):
            execution.transition_to(
                ProgrammerExecutionStatus.RUNNING,
                reason="Starting coding agent correction turn",
            )
        elif execution.status != ProgrammerExecutionStatus.RUNNING:
            execution.transition_to(
                ProgrammerExecutionStatus.RUNNING,
                reason="Starting coding agent correction turn",
            )

        execution.create_trace(
            ProgrammerActionType.EXECUTE,
            {
                "work_order_id": work_order.work_order_id,
                "mode": "CORRECTION",
            },
        )

        resolved_trace = trace_collector or getattr(capability_binding, "trace_collector", None)
        if resolved_trace is None:
            resolved_trace = ProgrammerExecutionTraceCollector(
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
            )

        resolved_binding = capability_binding or context.metadata.get("capability_binding")
        if resolved_binding is None:
            resolved_binding = ClineCapabilityBinding(
                context=context,
                trace_collector=resolved_trace,
                on_event=on_event,
            )
            context.metadata["capability_binding"] = resolved_binding

        active_backend = backend or self.backend or MockCodingAgentBackend()
        self._active_backends[execution.execution_id] = active_backend

        request = ProgrammerPromptBuilder.build_request(
            work_order=work_order,
            context=context,
            backend_type=active_backend.backend_type,
            system_prompt_override=system_prompt_override,
            prompt_override=correction_prompt,
            metadata={"capability_binding": resolved_binding, "correction": True},
        )

        def internal_event_handler(event: CodingAgentEvent) -> None:
            try:
                pevt = ClineEventTranslator.translate_event(
                    raw_event=event.to_dict(),
                    execution_id=execution.execution_id,
                    work_order_id=work_order.work_order_id,
                    sequence_number=resolved_trace.event_count,
                    correlation_id=work_order.correlation_id,
                )
                resolved_trace.add_event(pevt)
                if on_event:
                    try:
                        on_event(pevt)
                    except Exception as cb_err:
                        logger.warning(f"Error invoking on_event callback during correction: {cb_err}")
            except Exception as ev_err:
                logger.warning(f"Error handling event in executor correction: {ev_err}")

        try:
            backend_result = active_backend.execute(
                request=request,
                event_handler=internal_event_handler,
            )
        except Exception as exec_err:
            err_msg = f"Fatal backend execution error during correction: {exec_err}"
            logger.error(err_msg)
            if not execution.is_terminal:
                execution.transition_to(
                    ProgrammerExecutionStatus.FAILED,
                    reason=err_msg,
                )
            fail_result = ProgrammerResult(
                result_id=new_result_id(),
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
                task_id=work_order.task_id,
                project_id=work_order.project_id,
                correlation_id=work_order.correlation_id,
                status=ProgrammerResultStatus.FAILED,
                summary=err_msg,
                summary_for_manager=err_msg,
                metadata={
                    "verification_status": "UNVERIFIED",
                    "agent_execution_status": "FAILED",
                    "implementation_status": "FAILED",
                },
                blockers=[err_msg],
                material_blockers=[err_msg],
            )
            return ControlledExecutionOutcome(
                execution=execution,
                context=context,
                capability_binding=resolved_binding,
                result=fail_result,
                trace_collector=resolved_trace,
                error_message=err_msg,
            )

        if backend_result.is_cancelled():
            if not execution.is_terminal:
                execution.cancel(
                    requested_by="system",
                    reason=backend_result.error_message or "Execution was cancelled.",
                )
            result_status = ProgrammerResultStatus.CANCELLED
            agent_status = "CANCELLED"
            impl_status = "CANCELLED"
            summary_for_manager = f"Execution cancelled during correction: {backend_result.error_message or 'Operation cancelled'}"
        elif backend_result.is_failed():
            if not execution.is_terminal:
                execution.transition_to(
                    ProgrammerExecutionStatus.FAILED,
                    reason=backend_result.error_message or "Agent execution failed during correction.",
                )
            result_status = ProgrammerResultStatus.FAILED
            agent_status = "FAILED"
            impl_status = "FAILED"
            summary_for_manager = f"Coding agent correction failed: {backend_result.error_message or 'Fatal failure'}"
        else:
            if execution.status == ProgrammerExecutionStatus.RUNNING:
                execution.transition_to(
                    ProgrammerExecutionStatus.COMPLETING,
                    reason="Coding agent correction completed successfully. Verification pending.",
                )
            execution.create_trace(
                ProgrammerActionType.REPORT,
                {
                    "status": "COMPLETING",
                    "backend_result_id": backend_result.result_id,
                    "mode": "CORRECTION",
                },
            )
            result_status = ProgrammerResultStatus.PARTIAL
            agent_status = "COMPLETED"
            impl_status = "COMPLETED"
            summary_for_manager = "Coding agent correction finished. Verification pending."

        files_changed_set: set[str] = set()
        files_created_set: set[str] = set()
        files_deleted_set: set[str] = set()
        commands_executed: list[CommandExecutionRecord] = []
        denied_file_ops_count = 0
        denied_command_ops_count = 0

        for ev in resolved_trace.get_file_operations():
            allowed = ev.payload.get("allowed", False)
            if not allowed:
                denied_file_ops_count += 1
                continue

            op = str(ev.payload.get("operation", "")).upper()
            path = ev.payload.get("path")
            if op in ("CREATE", "FILE_CREATE") and path:
                files_created_set.add(str(path))
                files_changed_set.add(str(path))
            elif op in ("WRITE", "FILE_WRITE") and path:
                files_changed_set.add(str(path))
            elif op in ("DELETE", "FILE_DELETE") and path:
                files_deleted_set.add(str(path))
            elif op in ("RENAME", "FILE_RENAME"):
                src = ev.payload.get("source_path")
                dst = ev.payload.get("destination_path")
                if src:
                    files_deleted_set.add(str(src))
                if dst:
                    files_created_set.add(str(dst))
                    files_changed_set.add(str(dst))

        for ev in resolved_trace.get_command_operations():
            allowed = ev.payload.get("allowed", False)
            if not allowed:
                denied_command_ops_count += 1
                continue

            cmd_str = str(ev.payload.get("command", ""))
            exit_code = int(ev.payload.get("exit_code", 0))
            duration_ms = float(ev.payload.get("duration_ms", 0.0))
            cmd_record = CommandExecutionRecord(
                command=cmd_str,
                status="SUCCESS" if exit_code == 0 else "FAILURE",
                exit_code=exit_code,
                duration_ms=duration_ms,
                metadata=dict(ev.payload),
            )
            commands_executed.append(cmd_record)

        result_metadata = {
            "verification_status": "UNVERIFIED",
            "agent_execution_status": agent_status,
            "implementation_status": impl_status,
            "tool_calls_count": backend_result.tool_calls_count if backend_result else 0,
            "tokens_used": backend_result.tokens_used if backend_result else {},
            "events_count": resolved_trace.event_count,
            "denied_file_operations": denied_file_ops_count,
            "denied_command_operations": denied_command_ops_count,
        }

        result = ProgrammerResult(
            result_id=new_result_id(),
            execution_id=execution.execution_id,
            work_order_id=work_order.work_order_id,
            task_id=work_order.task_id,
            project_id=work_order.project_id,
            correlation_id=work_order.correlation_id,
            status=result_status,
            summary=summary_for_manager,
            summary_for_manager=summary_for_manager,
            files_changed=sorted(list(files_changed_set)),
            files_created=sorted(list(files_created_set)),
            files_deleted=sorted(list(files_deleted_set)),
            commands_executed=commands_executed,
            test_results=[],
            validation_results=[],
            acceptance_results=[],
            blockers=[b.description for b in execution.active_blockers],
            material_blockers=[b.description for b in execution.active_blockers],
            metadata=result_metadata,
            trace={
                "execution_id": execution.execution_id,
                "work_order_id": work_order.work_order_id,
                "workspace_id": context.workspace_id,
                "backend_type": str(active_backend.backend_type),
                "mode": "CORRECTION",
            },
        )

        return ControlledExecutionOutcome(
            execution=execution,
            context=context,
            capability_binding=resolved_binding,
            backend_result=backend_result,
            result=result,
            trace_collector=resolved_trace,
            error_message=backend_result.error_message if backend_result and backend_result.error_message else None,
            metadata={
                "denied_file_operations": denied_file_ops_count,
                "denied_command_operations": denied_command_ops_count,
                "mode": "CORRECTION",
            },
        )

    def cancel(
        self,
        execution_id: str,
        reason: str,
        requested_by: str = "manager",
    ) -> bool:
        """
        Request cancellation of an active execution session.
        
        Propagates cancellation to the active backend and transitions execution to CANCELLED.
        """
        execution = self._active_executions.get(execution_id)
        if not execution or execution.is_terminal:
            return False

        # Signal backend if running
        backend = self._active_backends.get(execution_id)
        if backend:
            try:
                backend.cancel(
                    CodingAgentCancellationRequest(
                        execution_id=execution_id,
                        work_order_id=execution.work_order_id,
                        reason=reason,
                    )
                )
            except Exception as b_err:
                logger.warning(f"Error cancelling backend for execution {execution_id}: {b_err}")

        # Transition execution
        execution.cancel(
            requested_by=requested_by,
            reason=reason,
        )
        return True


# Alias for backward-compatible or simplified imports
ProgrammerExecutor = ControlledProgrammerExecutor

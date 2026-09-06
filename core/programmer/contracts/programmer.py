from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Sequence, Union

from core.models import WorkerOutput
from core.programmer.contracts.acceptance_evaluator import AcceptanceCriteriaEvaluator
from core.programmer.contracts.coding_agent import CodingAgentBackend
from core.programmer.contracts.correction_loop import (
    BoundedCorrectionLoop,
    CorrectionLoopResult,
    CorrectionPromptBuilder,
)
from core.programmer.contracts.diff_verifier import DiffScopeVerifier
from core.programmer.contracts.evidence_aggregator import VerificationEvidenceAggregator
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.executor import ControlledProgrammerExecutor
from core.programmer.contracts.identifiers import new_execution_id
from core.programmer.contracts.manager_bridge import ProgrammerManagerBridge
from core.programmer.contracts.provisioner import WorkspaceProvisioner
from core.programmer.contracts.result import ProgrammerResult
from core.programmer.contracts.verification_runner import VerificationRunner
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    ProgrammerExecutionStatus,
    ProgrammerResultStatus,
    WorkspaceIsolationMode,
)

logger = logging.getLogger("AutonomOS.Programmer")


class Programmer:
    """
    AutonomOS Programmer Subsystem V1 Controller.
    
    Acts as the top-level orchestration coordinator for the Programmer domain:
    - Receives validated ProgrammerWorkOrder (or Manager Task via bridge).
    - Manages lifecycle transitions:
      REQUESTED -> STARTING -> RUNNING -> implementation -> VERIFYING ->
      [correction if necessary -> VERIFYING] -> COMPLETING -> final ProgrammerResult.
    - Provisions or binds to ProgrammerExecutionContext.
    - Coordinates controlled coding-agent implementation turns through authorized capability bindings.
    - Executes AutonomOS verification (VerificationRunner, DiffScopeVerifier,
      AcceptanceCriteriaEvaluator, VerificationEvidenceAggregator).
    - Owns and manages the BoundedCorrectionLoop strictly governed by work_order.iteration_budget.
    - Ensures AutonomOS, never the coding agent, determines final execution status and verification success.
    - Produces final, evidence-backed ProgrammerResult for Manager consumption.
    """

    def __init__(
        self,
        provisioner: Optional[WorkspaceProvisioner] = None,
        backend: Optional[CodingAgentBackend] = None,
        executor: Optional[ControlledProgrammerExecutor] = None,
        correction_loop: Optional[BoundedCorrectionLoop] = None,
        bridge: Optional[ProgrammerManagerBridge] = None,
        diff_verifier: Optional[DiffScopeVerifier] = None,
        acceptance_evaluator: Optional[AcceptanceCriteriaEvaluator] = None,
        evidence_aggregator: Optional[VerificationEvidenceAggregator] = None,
        prompt_builder: Optional[CorrectionPromptBuilder] = None,
        verification_runner_factory: Optional[Callable[[ProgrammerExecutionContext], VerificationRunner]] = None,
        default_projects_dir: Optional[str] = None,
        project_resolver: Optional[Any] = None,
    ) -> None:
        self.backend = backend
        self.provisioner = provisioner or WorkspaceProvisioner(
            project_resolver=project_resolver,
            default_projects_dir=default_projects_dir,
        )
        self.executor = executor or ControlledProgrammerExecutor(
            provisioner=self.provisioner,
            backend=self.backend,
        )
        self.diff_verifier = diff_verifier or DiffScopeVerifier()
        self.acceptance_evaluator = acceptance_evaluator or AcceptanceCriteriaEvaluator()
        self.evidence_aggregator = evidence_aggregator or VerificationEvidenceAggregator()
        self.prompt_builder = prompt_builder or CorrectionPromptBuilder()
        self.verification_runner_factory = verification_runner_factory

        self.correction_loop = correction_loop or BoundedCorrectionLoop(
            executor=self.executor,
            verification_runner_factory=self.verification_runner_factory,
            diff_verifier=self.diff_verifier,
            acceptance_evaluator=self.acceptance_evaluator,
            evidence_aggregator=self.evidence_aggregator,
            prompt_builder=self.prompt_builder,
        )
        self.bridge = bridge

        self.last_execution: Optional[ProgrammerExecution] = None
        self.last_loop_result: Optional[CorrectionLoopResult] = None
        self.last_result: Optional[ProgrammerResult] = None

    def execute(
        self,
        work_order: ProgrammerWorkOrder,
        execution: Optional[ProgrammerExecution] = None,
        backend: Optional[CodingAgentBackend] = None,
        on_event: Optional[Callable[[Any], None]] = None,
        root_path_override: Optional[str] = None,
        isolation_mode: Optional[Union[WorkspaceIsolationMode, str]] = None,
        baseline_snapshot: Optional[dict[str, str]] = None,
    ) -> ProgrammerResult:
        """
        Execute a complete, verified Programmer work order through the bounded correction loop.
        
        Args:
            work_order: Strongly-typed, validated ProgrammerWorkOrder authorizing the task.
            execution: Optional existing ProgrammerExecution session.
            backend: Optional CodingAgentBackend override.
            on_event: Optional streaming callback receiving normalized ProgrammerExecutionEvent instances.
            root_path_override: Optional workspace root directory override.
            isolation_mode: Optional workspace isolation mode.
            baseline_snapshot: Optional baseline file hash snapshot for diff verification.
            
        Returns:
            Authoritative, evidence-backed ProgrammerResult.
        """
        if work_order is None:
            raise ProgrammerValidationError("ProgrammerWorkOrder cannot be None.")
        work_order.validate()

        effective_backend = backend or self.backend

        # Establish Execution Session if not supplied
        if execution is None:
            execution = ProgrammerExecution(
                execution_id=new_execution_id(),
                work_order_id=work_order.work_order_id,
                task_id=getattr(work_order, "task_id", "") or getattr(work_order, "manager_task_id", ""),
                project_id=work_order.project_id,
                correlation_id=work_order.correlation_id,
                status=ProgrammerExecutionStatus.REQUESTED,
            )

        self.last_execution = execution

        # Run bounded implementation -> verification -> correction loop
        loop_result: CorrectionLoopResult = self.correction_loop.run(
            work_order=work_order,
            execution=execution,
            backend=effective_backend,
            on_event=on_event,
            root_path_override=root_path_override,
            isolation_mode=isolation_mode,
            baseline_snapshot=baseline_snapshot,
        )

        self.last_loop_result = loop_result
        self.last_result = loop_result.result

        # If Manager bridge is configured, report outcome to bridge for event emission
        if self.bridge and loop_result.result:
            try:
                self.bridge.receive_result(loop_result.result, execution, work_order)
            except Exception as bridge_err:
                logger.warning("Error reporting result to ProgrammerManagerBridge: %s", bridge_err)

        return loop_result.result

    def execute_work_order(
        self,
        work_order: ProgrammerWorkOrder,
        execution: Optional[ProgrammerExecution] = None,
        backend: Optional[CodingAgentBackend] = None,
        on_event: Optional[Callable[[Any], None]] = None,
        root_path_override: Optional[str] = None,
        isolation_mode: Optional[Union[WorkspaceIsolationMode, str]] = None,
        baseline_snapshot: Optional[dict[str, str]] = None,
    ) -> tuple[ProgrammerExecution, ProgrammerResult, Optional[WorkerOutput]]:
        """
        Full orchestration entry point compatible with Manager task dispatch conventions.
        
        Returns:
            (execution, result, worker_output)
        """
        if work_order is None:
            raise ProgrammerValidationError("ProgrammerWorkOrder cannot be None.")
        work_order.validate()

        # If bridge is configured, dispatch through bridge to emit PROGRAMMER_STARTED
        if self.bridge and execution is None:
            execution = self.bridge.dispatch_work_order(work_order)

        result = self.execute(
            work_order=work_order,
            execution=execution,
            backend=backend,
            on_event=on_event,
            root_path_override=root_path_override,
            isolation_mode=isolation_mode,
            baseline_snapshot=baseline_snapshot,
        )

        # Build WorkerOutput
        worker_output: Optional[WorkerOutput] = None
        if self.bridge and execution:
            worker_output = self.bridge.receive_result(result, execution, work_order)
        elif result:
            worker_output = result.to_worker_output()

        return self.last_execution or execution, result, worker_output

    def cancel(self, execution_id: Optional[str] = None, reason: str = "Execution cancelled by user/manager.") -> None:
        """Signal cancellation to the running correction loop and active execution."""
        self.correction_loop.cancel()
        if self.last_execution and not self.last_execution.is_terminal:
            if execution_id is None or self.last_execution.execution_id == execution_id:
                self.last_execution.cancel(requested_by="manager", reason=reason)

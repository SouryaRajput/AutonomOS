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
from core.programmer.contracts.delivery import (
    DeliveryPackage,
    DeliveryPreparer,
)
from core.programmer.contracts.diff_verifier import DiffScopeVerifier
from core.programmer.contracts.escalation import (
    EscalationCoordinator,
    EscalationStatus,
    ManagerEscalationResponse,
    ManagerEscalationResponseAction,
    ProgrammerEscalation,
    ProgrammerEscalationCategory,
)
from core.programmer.contracts.evidence_aggregator import VerificationEvidenceAggregator
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.execution_context import ProgrammerExecutionContext
from core.programmer.contracts.execution_supervisor import (
    ExecutionSupervisionRecord,
    ExecutionSupervisor,
)
from core.programmer.contracts.executor import ControlledProgrammerExecutor
from core.programmer.contracts.git_model import GitExecutionContext
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_result_id,
)
from core.programmer.contracts.intelligence_pipeline import (
    EngineeringIntelligencePipeline,
    PreImplementationIntelligence,
)
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
    DeviationClassification,
    PlanValidationStatus,
    ProgrammerBlockerCategory,
    ProgrammerBlockerSeverity,
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
    - Runs pre-implementation intelligence pipeline (CodebaseUnderstanding, ImpactAnalysis,
      ImplementationPlanning, EngineeringRiskAnalysis, PlanValidation & Escalation).
    - Coordinates controlled coding-agent implementation turns through authorized capability bindings.
    - Supervise execution against ImplementationPlan (detecting deviations, new risks, blockers).
    - Executes AutonomOS verification (VerificationRunner, DiffScopeVerifier,
      AcceptanceCriteriaEvaluator, VerificationEvidenceAggregator).
    - Owns and manages the BoundedCorrectionLoop strictly governed by work_order.iteration_budget.
    - Ensures AutonomOS, never the coding agent, determines final execution status and verification success.
    - Prepares Phase 6 DeliveryPackage for verified git workspaces.
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
        intelligence_pipeline: Optional[EngineeringIntelligencePipeline] = None,
        escalation_coordinator: Optional[EscalationCoordinator] = None,
        delivery_preparer: Optional[DeliveryPreparer] = None,
        enable_intelligence: bool = True,
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

        self.enable_intelligence = enable_intelligence
        self.intelligence_pipeline = intelligence_pipeline or (EngineeringIntelligencePipeline() if enable_intelligence else None)
        self.escalation_coordinator = escalation_coordinator or EscalationCoordinator()
        self.delivery_preparer = delivery_preparer or DeliveryPreparer()

        self.last_execution: Optional[ProgrammerExecution] = None
        self.last_loop_result: Optional[CorrectionLoopResult] = None
        self.last_result: Optional[ProgrammerResult] = None
        self.last_intelligence: Optional[PreImplementationIntelligence] = None
        self.last_supervisor: Optional[ExecutionSupervisor] = None
        self.last_delivery_package: Optional[DeliveryPackage] = None

    def execute(
        self,
        work_order: ProgrammerWorkOrder,
        execution: Optional[ProgrammerExecution] = None,
        backend: Optional[CodingAgentBackend] = None,
        on_event: Optional[Callable[[Any], None]] = None,
        root_path_override: Optional[str] = None,
        isolation_mode: Optional[Union[WorkspaceIsolationMode, str]] = None,
        baseline_snapshot: Optional[dict[str, str]] = None,
        manager_response: Optional[Union[ManagerEscalationResponse, Callable[[ProgrammerEscalation], ManagerEscalationResponse]]] = None,
        git_context: Optional[GitExecutionContext] = None,
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
            manager_response: Optional Manager escalation response or responder callback if escalation arises.
            git_context: Optional GitExecutionContext for Phase 6 Delivery preparation.
            
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

        if execution.status == ProgrammerExecutionStatus.REQUESTED:
            execution.transition_to(ProgrammerExecutionStatus.STARTING, reason="Starting execution and workspace provisioning")

        self.last_execution = execution

        # Step 1: Workspace Provisioning
        prov_result = self.provisioner.provision(
            work_order=work_order,
            execution=execution,
            root_path_override=root_path_override,
            isolation_mode=isolation_mode,
        )
        if not prov_result.is_ready() or prov_result.execution_context is None:
            err_msg = prov_result.error_message or "Workspace provisioning failed."
            logger.error("Execution %s provisioning failed: %s", execution.execution_id, err_msg)
            if not execution.is_terminal:
                execution.transition_to(ProgrammerExecutionStatus.FAILED, reason=err_msg)
            fail_res = ProgrammerResult(
                result_id=new_result_id(),
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
                task_id=work_order.task_id,
                project_id=work_order.project_id,
                correlation_id=work_order.correlation_id,
                status=ProgrammerResultStatus.FAILED,
                summary=f"Workspace provisioning failed: {err_msg}",
                summary_for_manager=f"Workspace provisioning failed: {err_msg}",
                blockers=[err_msg],
                material_blockers=[err_msg],
                metadata={"error_code": str(prov_result.error_code)},
            )
            self.last_result = fail_res
            return fail_res

        context = prov_result.execution_context
        workspace = prov_result.workspace

        # Step 2: Engineering Intelligence Pipeline (Phase 7.1 - 7.5)
        pre_intel: Optional[PreImplementationIntelligence] = None
        supervisor: Optional[ExecutionSupervisor] = None
        supervised_on_event: Optional[Callable[[Any], None]] = on_event

        if self.enable_intelligence and self.intelligence_pipeline:
            pre_intel = self.intelligence_pipeline.run_pre_implementation(
                work_order=work_order,
                workspace=workspace,
                execution_id=execution.execution_id,
                execution_context=context,
            )
            self.last_intelligence = pre_intel

            # Attach intelligence artifacts to context metadata for prompt generation
            context.metadata["understanding"] = pre_intel.understanding
            context.metadata["impact_analysis"] = pre_intel.impact_analysis
            context.metadata["plan"] = pre_intel.plan
            context.metadata["risk_assessment"] = pre_intel.risk_assessment
            context.metadata["plan_validation"] = pre_intel.plan_validation

            # Check Plan Validation & Manager Escalation
            if pre_intel.requires_escalation:
                created_escalations: list[ProgrammerEscalation] = []
                if pre_intel.plan_validation.escalation_candidates:
                    for cand in pre_intel.plan_validation.escalation_candidates:
                        esc = self.escalation_coordinator.escalate_candidate(
                            execution=execution,
                            work_order=work_order,
                            candidate=cand,
                        )
                        created_escalations.append(esc)
                else:
                    esc = self.escalation_coordinator.create_escalation(
                        execution=execution,
                        work_order=work_order,
                        category=ProgrammerEscalationCategory.SCOPE,
                        requested_decision="Plan validation requires Manager escalation.",
                        observed_facts=pre_intel.plan_validation.blocking_issues or pre_intel.plan_validation.warnings,
                    )
                    created_escalations.append(esc)

                # Process manager escalation response if provided
                if manager_response is not None:
                    all_approved = True
                    for esc in created_escalations:
                        resp = manager_response(esc) if callable(manager_response) else manager_response
                        esc_status, revised_wo = self.escalation_coordinator.apply_response(
                            escalation=esc,
                            response=resp,
                            execution=execution,
                            work_order=work_order,
                        )
                        if esc_status != EscalationStatus.RESOLVED:
                            all_approved = False
                        if revised_wo:
                            work_order = revised_wo

                    if not all_approved:
                        term_status = (
                            ProgrammerExecutionStatus.CANCELLED
                            if execution.status == ProgrammerExecutionStatus.CANCELLED
                            else ProgrammerExecutionStatus.BLOCKED
                        )
                        res_status = (
                            ProgrammerResultStatus.CANCELLED
                            if term_status == ProgrammerExecutionStatus.CANCELLED
                            else ProgrammerResultStatus.BLOCKED
                        )
                        desc = "Manager denied or cancelled escalation."
                        block_res = ProgrammerResult(
                            result_id=new_result_id(),
                            execution_id=execution.execution_id,
                            work_order_id=work_order.work_order_id,
                            task_id=work_order.task_id,
                            project_id=work_order.project_id,
                            correlation_id=work_order.correlation_id,
                            status=res_status,
                            summary=desc,
                            summary_for_manager=desc,
                            blockers=[e.requested_decision for e in created_escalations],
                            escalations=[e.requested_decision for e in created_escalations],
                            metadata={
                                "pre_implementation_intelligence": pre_intel.to_dict(),
                                "escalations": [e.to_dict() for e in created_escalations],
                            },
                        )
                        self.last_result = block_res
                        return block_res
                else:
                    # No response provided; execution stays BLOCKED waiting for Manager
                    desc = "Execution blocked pending Manager escalation response."
                    block_res = ProgrammerResult(
                        result_id=new_result_id(),
                        execution_id=execution.execution_id,
                        work_order_id=work_order.work_order_id,
                        task_id=work_order.task_id,
                        project_id=work_order.project_id,
                        correlation_id=work_order.correlation_id,
                        status=ProgrammerResultStatus.BLOCKED,
                        summary=desc,
                        summary_for_manager=desc,
                        blockers=[e.requested_decision for e in created_escalations],
                        escalations=[e.requested_decision for e in created_escalations],
                        metadata={
                            "pre_implementation_intelligence": pre_intel.to_dict(),
                            "escalations": [e.to_dict() for e in created_escalations],
                        },
                    )
                    self.last_result = block_res
                    return block_res

            elif pre_intel.plan_validation.status == PlanValidationStatus.INVALID:
                desc = f"Plan validation failed (INVALID): {'; '.join(pre_intel.plan_validation.blocking_issues)}"
                if not execution.is_terminal:
                    execution.block(
                        reason=desc,
                        category=ProgrammerBlockerCategory.POLICY,
                        severity=ProgrammerBlockerSeverity.HIGH,
                        required_decision="Manager intervention required to revise work order requirements.",
                    )
                fail_plan_res = ProgrammerResult(
                    result_id=new_result_id(),
                    execution_id=execution.execution_id,
                    work_order_id=work_order.work_order_id,
                    task_id=work_order.task_id,
                    project_id=work_order.project_id,
                    correlation_id=work_order.correlation_id,
                    status=ProgrammerResultStatus.BLOCKED,
                    summary=desc,
                    summary_for_manager=desc,
                    blockers=list(pre_intel.plan_validation.blocking_issues),
                    metadata={"pre_implementation_intelligence": pre_intel.to_dict()},
                )
                self.last_result = fail_plan_res
                return fail_plan_res

            # Step 3: Setup Intelligent Execution Supervisor (Phase 7.6)
            supervisor = self.intelligence_pipeline.create_supervisor(
                plan=pre_intel.plan,
                work_order=work_order,
                execution_context=context,
            )
            self.last_supervisor = supervisor

            def supervised_callback(event: Any) -> None:
                dev = supervisor.record_event(event)
                if dev and dev.classification == DeviationClassification.BLOCKING:
                    self.correction_loop.cancel()
                if on_event:
                    on_event(event)

            supervised_on_event = supervised_callback

        # Step 4: Run bounded implementation -> verification -> correction loop
        loop_result: CorrectionLoopResult = self.correction_loop.run(
            work_order=work_order,
            execution=execution,
            context=context,
            backend=effective_backend,
            on_event=supervised_on_event,
            root_path_override=root_path_override,
            isolation_mode=isolation_mode,
            baseline_snapshot=baseline_snapshot,
        )

        self.last_loop_result = loop_result
        final_res = loop_result.result

        if not final_res:
            res_status = (
                ProgrammerResultStatus.CANCELLED
                if execution.status == ProgrammerExecutionStatus.CANCELLED
                else ProgrammerResultStatus.BLOCKED
                if execution.status == ProgrammerExecutionStatus.BLOCKED
                else ProgrammerResultStatus.FAILED
            )
            final_res = execution.create_result(
                status=res_status,
                summary_for_manager=loop_result.error_message or "Execution terminated.",
                material_blockers=[b.description for b in execution.active_blockers]
                or ([loop_result.error_message] if loop_result.error_message else []),
            )

        # Step 5: Post-Execution Intelligence & Delivery Enrichment
        if final_res:
            if pre_intel:
                final_res.metadata["understanding"] = pre_intel.understanding.to_dict()
                final_res.metadata["impact_analysis"] = pre_intel.impact_analysis.to_dict()
                final_res.metadata["plan"] = pre_intel.plan.to_dict()
                final_res.metadata["risk_assessment"] = pre_intel.risk_assessment.to_dict()
                final_res.metadata["plan_validation"] = pre_intel.plan_validation.to_dict()
                for u in pre_intel.understanding.uncertainties:
                    if u not in final_res.metadata.setdefault("known_unknowns", []):
                        final_res.metadata["known_unknowns"].append(u)
                for imp_u in pre_intel.impact_analysis.unknowns:
                    if imp_u not in final_res.metadata.setdefault("known_unknowns", []):
                        final_res.metadata["known_unknowns"].append(imp_u)
                for r in pre_intel.risk_assessment.risks:
                    r_desc = f"[{r.category.value}] {r.description}"
                    if r_desc not in final_res.risks:
                        final_res.risks.append(r_desc)

            if supervisor:
                sup_rec = supervisor.to_record()
                final_res.metadata["supervision_record"] = sup_rec.to_dict()
                if supervisor.has_blocking_deviations() and final_res.status == ProgrammerResultStatus.COMPLETED:
                    final_res.status = ProgrammerResultStatus.BLOCKED
                    for d in supervisor.deviations:
                        if d.classification == DeviationClassification.BLOCKING:
                            final_res.blockers.append(d.description)
                            final_res.material_blockers.append(d.description)

            # Delivery Package preparation (Phase 6)
            effective_git_ctx = git_context or (context if isinstance(context, GitExecutionContext) else context.metadata.get("git_execution_context"))
            if self.delivery_preparer and effective_git_ctx:
                if effective_git_ctx.execution_id != execution.execution_id:
                    effective_git_ctx.execution_id = execution.execution_id
                delivery_pkg = self.delivery_preparer.prepare(
                    execution_context=effective_git_ctx,
                    work_order=work_order,
                    change_set=context.metadata.get("change_set"),
                    repository_verification=context.metadata.get("repository_verification"),
                    verification_summary=loop_result.final_summary,
                    test_results=final_res.test_results,
                    acceptance_results=final_res.acceptance_results,
                    blockers=final_res.blockers,
                    risks=final_res.risks,
                    evidence=loop_result.final_summary.evidence if loop_result.final_summary else None,
                )
                self.last_delivery_package = delivery_pkg
                final_res.metadata["delivery_package"] = delivery_pkg.to_dict()

        self.last_result = final_res

        # If Manager bridge is configured, report outcome to bridge for event emission
        if self.bridge and final_res:
            try:
                self.bridge.receive_result(final_res, execution, work_order)
            except Exception as bridge_err:
                logger.warning("Error reporting result to ProgrammerManagerBridge: %s", bridge_err)

        return final_res

    def execute_work_order(
        self,
        work_order: ProgrammerWorkOrder,
        execution: Optional[ProgrammerExecution] = None,
        backend: Optional[CodingAgentBackend] = None,
        on_event: Optional[Callable[[Any], None]] = None,
        root_path_override: Optional[str] = None,
        isolation_mode: Optional[Union[WorkspaceIsolationMode, str]] = None,
        baseline_snapshot: Optional[dict[str, str]] = None,
        manager_response: Optional[Union[ManagerEscalationResponse, Callable[[ProgrammerEscalation], ManagerEscalationResponse]]] = None,
        git_context: Optional[GitExecutionContext] = None,
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
            manager_response=manager_response,
            git_context=git_context,
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

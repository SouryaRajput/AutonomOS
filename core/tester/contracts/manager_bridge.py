from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Sequence, Union

from core.enums import RiskLevel, TaskStatus, WorkerStatus
from core.events.model import Event, new_event_id, utc_now
from core.events.types import EventSource, EventType
from core.models import Task, WorkerManifest, WorkerOutput
from core.tester.contracts.blocker import TesterBlocker
from core.tester.contracts.boundary import (
    TESTER_ALLOWED_CAPABILITIES,
    TESTER_FORBIDDEN_ACTIONS,
    TesterBoundaryGuard,
)
from core.tester.contracts.criteria import AcceptanceCriterion
from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.finding import (
    AcceptanceCriterionResult,
    TesterDefect,
    TesterEvidence,
    TesterFinding,
)
from core.tester.contracts.identifiers import (
    new_blocker_id,
    new_defect_id,
    new_evidence_id,
    new_execution_id,
    new_finding_id,
    new_recommendation_id,
    new_result_id,
    new_test_case_id,
    new_trace_id,
    new_work_order_id,
    validate_blocker_id,
    validate_execution_id,
    validate_result_id,
    validate_work_order_id,
)
from core.tester.contracts.lifecycle import TesterLifecycle
from core.tester.contracts.quality_summary import QualitySummary
from core.tester.contracts.recommendation import TesterRecommendation
from core.tester.contracts.result import TesterResult
from core.tester.contracts.scope import TestScope
from core.tester.contracts.test_case import TestCaseResult
from core.tester.contracts.thresholds import QualityThresholds
from core.tester.contracts.validator import TesterWorkOrderValidator
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    InvalidTesterTransitionError,
    TesterBlockerError,
    TesterBoundaryViolationError,
    TesterError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    AcceptanceCriterionStatus,
    DefectSeverity,
    DefectType,
    EvidenceType,
    FindingCategory,
    ForbiddenTesterAction,
    RecommendationPriority,
    ShipRecommendation,
    TestCaseStatus,
    TestCategory,
    TesterActionType,
    TesterBlockerCategory,
    TesterBlockerSeverity,
    TesterExecutionPhase,
    TesterExecutionStatus,
    TesterResultStatus,
    TesterWorkOrderStatus,
    TestingCapability,
    WorkerType,
)
from pkg.sdk.worker import Worker

logger = logging.getLogger("AutonomOS.TesterManagerBridge")


class TesterManagerBridge:
    """
    Orchestration adapter bridging Manager decisions with the Tester domain contracts.
    
    Architectural boundary rules:
    - Manager = WHAT + WHY + BOUNDARIES + BUDGETS + DECISIONS
      (Issues work orders, sets scope, budgets, acceptance criteria, evaluates recommendations, decides releases)
    - Tester = HOW + INDEPENDENT EVALUATION + EVIDENCE
      (Executes within authorized scope/capabilities, gathers evidence, reports factual findings, provides advisory recommendations)
    
    This bridge:
    1. Translates Manager Task assignments into strongly-typed, validated TesterWorkOrders.
    2. Emits authoritative lifecycle domain events on the AutonomOS event system:
       - TESTER_REQUESTED
       - TESTER_STARTED
       - TESTER_BLOCKED
       - TESTER_COMPLETED
       - TESTER_FAILED
       - TESTER_CANCELLED
    3. Enforces strict causal lineage from Manager Task down through Execution and Result.
    4. Facilitates blocker escalation to Manager, unblocking, and cancellation provenance.
    5. Converts TesterResult into standard AutonomOS WorkerOutput with advisory recommendations.
    6. Enforces authority boundaries: Tester cannot expand scope, modify WorkOrder, or increase budgets.
    """
    __test__ = False

    def __init__(
        self,
        event_sink: Optional[Callable[..., Any]] = None,
        runtime: Optional[Any] = None,
    ) -> None:
        self.event_sink = event_sink
        self.runtime = runtime
        self.events: list[Event] = []
        self.work_orders: dict[str, TesterWorkOrder] = {}
        self.executions: dict[str, TesterExecution] = {}

    def emit_event(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        project_id: str,
        task_id: str,
        correlation_id: str,
        causation_id: Optional[str] = None,
        worker_id: Optional[str] = "worker.tester",
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
        instructions: Optional[list[str]] = None,
        product_artifact: Optional[Any] = None,
        source_revision: Optional[Any] = None,
        test_scope: Optional[Any] = None,
        test_categories: Optional[list[Any]] = None,
        required_flows: Optional[list[str]] = None,
        acceptance_criteria: Optional[list[Any]] = None,
        authorized_capabilities: Optional[list[Any]] = None,
        test_environment: Optional[Any] = None,
        constraints: Optional[list[str]] = None,
        quality_thresholds: Optional[Any] = None,
        time_budget: Optional[int] = None,
        iteration_budget: Optional[int] = None,
        work_order_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> TesterWorkOrder:
        """
        Manager issues an authoritative, bounded TesterWorkOrder from a Task.
        
        Validates the entire envelope before returning, and emits TESTER_REQUESTED.
        """
        if isinstance(task, dict):
            task_id = task.get("id") or ""
            project_id = task.get("project_id") or ""
            task_obj = task.get("objective") or task.get("title") or ""
            task_meta = dict(task.get("metadata", {}))
            task_crit = task.get("success_criteria", [])
            task_artifacts = task.get("artifacts", [])
            correlation_id = task_meta.get("correlation_id") or task_id
        else:
            task_id = getattr(task, "id", "")
            project_id = getattr(task, "project_id", "")
            task_obj = getattr(task, "objective", "") or getattr(task, "title", "")
            task_meta = dict(getattr(task, "metadata", {}) or {})
            task_crit = getattr(task, "success_criteria", [])
            task_artifacts = getattr(task, "artifacts", [])
            correlation_id = task_meta.get("correlation_id") or task_id

        if not task_id:
            raise TesterLineageError("Cannot issue TesterWorkOrder: task must have a non-empty id.")
        if not project_id:
            raise TesterLineageError(f"Cannot issue TesterWorkOrder: task '{task_id}' must have a non-empty project_id.")

        final_objective = objective or task_obj
        final_instructions = instructions if instructions is not None else task_meta.get("instructions", [])
        final_product = product_artifact if product_artifact is not None else (
            task_meta.get("product_artifact") or (task_artifacts[0] if task_artifacts else "default_product")
        )
        final_revision = source_revision if source_revision is not None else task_meta.get("source_revision")
        final_scope = test_scope if test_scope is not None else task_meta.get("test_scope", [])
        final_categories = test_categories if test_categories is not None else task_meta.get(
            "test_categories", [TestCategory.FUNCTIONAL]
        )
        final_flows = required_flows if required_flows is not None else task_meta.get("required_flows", [])
        final_ac = acceptance_criteria if acceptance_criteria is not None else (
            task_meta.get("acceptance_criteria") or task_crit or []
        )
        final_caps = authorized_capabilities if authorized_capabilities is not None else task_meta.get(
            "authorized_capabilities", list(TESTER_ALLOWED_CAPABILITIES)
        )
        final_env = test_environment if test_environment is not None else task_meta.get("test_environment")
        final_constraints = constraints if constraints is not None else task_meta.get("constraints", [])
        final_thresholds = quality_thresholds if quality_thresholds is not None else task_meta.get("quality_thresholds")
        final_time_budget = time_budget if time_budget is not None else int(task_meta.get("time_budget", 300))
        final_iteration_budget = iteration_budget if iteration_budget is not None else int(task_meta.get("iteration_budget", 5))
        final_wo_id = work_order_id or new_work_order_id()

        work_order = TesterWorkOrder(
            work_order_id=final_wo_id,
            manager_task_id=task_id,
            project_id=project_id,
            correlation_id=correlation_id,
            objective=final_objective,
            instructions=final_instructions,
            product_artifact=final_product,
            source_revision=final_revision,
            test_scope=final_scope,
            test_categories=final_categories,
            required_flows=final_flows,
            acceptance_criteria=final_ac,
            authorized_capabilities=final_caps,
            test_environment=final_env,
            constraints=final_constraints,
            quality_thresholds=final_thresholds,
            time_budget=final_time_budget,
            iteration_budget=final_iteration_budget,
            status=TesterWorkOrderStatus.ASSIGNED,
            metadata=task_meta,
        )

        work_order.validate()
        self.work_orders[work_order.work_order_id] = work_order

        # Emit TESTER_REQUESTED
        self.emit_event(
            event_type=EventType.TESTER_REQUESTED,
            payload={
                "work_order_id": work_order.work_order_id,
                "manager_task_id": work_order.manager_task_id,
                "project_id": work_order.project_id,
                "objective": work_order.objective,
                "test_scope": work_order.test_scope.to_list() if hasattr(work_order.test_scope, "to_list") else list(work_order.test_scope),
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
        work_order: TesterWorkOrder,
        worker_id: str = "worker.tester",
        causation_id: Optional[str] = None,
    ) -> TesterExecution:
        """
        Transition work order into active execution context and emit TESTER_STARTED.
        """
        work_order.validate()

        execution = work_order.create_execution(worker_id=worker_id)
        execution.transition_to(
            TesterExecutionStatus.STARTING,
            reason="Execution initialized by manager dispatch",
        )

        self.executions[execution.execution_id] = execution

        self.emit_event(
            event_type=EventType.TESTER_STARTED,
            payload={
                "execution_id": execution.execution_id,
                "work_order_id": work_order.work_order_id,
                "manager_task_id": work_order.manager_task_id,
                "project_id": work_order.project_id,
                "objective": work_order.objective,
                "worker_id": worker_id,
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
        execution: TesterExecution,
        blocker: TesterBlocker,
        work_order: Optional[TesterWorkOrder] = None,
        causation_id: Optional[str] = None,
    ) -> TesterBlocker:
        """
        Tester reports an operational impediment requiring Manager intervention.
        Transitions execution to BLOCKED and emits TESTER_BLOCKED.
        """
        if work_order:
            if execution.work_order_id != work_order.work_order_id:
                raise TesterLineageError(
                    f"Execution work_order_id '{execution.work_order_id}' does not match work_order '{work_order.work_order_id}'"
                )
            if execution.task_id != work_order.manager_task_id:
                raise TesterLineageError(
                    f"Execution task_id '{execution.task_id}' does not match work_order task_id '{work_order.manager_task_id}'"
                )
            if execution.project_id != work_order.project_id:
                raise TesterLineageError(
                    f"Execution project_id '{execution.project_id}' does not match work_order project_id '{work_order.project_id}'"
                )

        execution.transition_to(
            TesterExecutionStatus.BLOCKED,
            reason=blocker.description,
            blocker=blocker,
        )

        self.emit_event(
            event_type=EventType.TESTER_BLOCKED,
            payload={
                "execution_id": execution.execution_id,
                "work_order_id": execution.work_order_id,
                "manager_task_id": execution.task_id,
                "project_id": execution.project_id,
                "blocker_id": blocker.blocker_id,
                "category": blocker.category.value if hasattr(blocker.category, "value") else str(blocker.category),
                "severity": blocker.severity.value if hasattr(blocker.severity, "value") else str(blocker.severity),
                "reason": blocker.description,
                "required_decision": blocker.required_decision,
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
        execution: TesterExecution,
        blocker_id: str,
        resolution: str,
        target_status: TesterExecutionStatus = TesterExecutionStatus.RUNNING,
        work_order: Optional[TesterWorkOrder] = None,
        causation_id: Optional[str] = None,
    ) -> None:
        """
        Manager provides resolution to an active blocker, resuming execution.
        """
        if work_order and execution.work_order_id != work_order.work_order_id:
            raise TesterLineageError(
                f"Execution work_order_id '{execution.work_order_id}' does not match work order '{work_order.work_order_id}'"
            )

        matching = [b for b in execution.blockers if b.blocker_id == blocker_id]
        if not matching:
            raise TesterBlockerError(
                blocker_id=blocker_id,
                reason=f"Blocker '{blocker_id}' not found in execution '{execution.execution_id}'",
            )

        for b in matching:
            b.resolve(resolution_notes=resolution, resolved_by="manager")

        execution.transition_to(
            target_status,
            reason=f"Blocker '{blocker_id}' resolved by manager: {resolution}",
        )

    def cancel_execution(
        self,
        execution: TesterExecution,
        requested_by: str,
        reason: str,
        work_order: Optional[TesterWorkOrder] = None,
        causation_id: Optional[str] = None,
    ) -> None:
        """
        Manager or authorized user cancels an active Tester execution attempt.
        Transitions execution to CANCELLED and emits TESTER_CANCELLED.
        """
        if work_order and execution.work_order_id != work_order.work_order_id:
            raise TesterLineageError(
                f"Execution work_order_id '{execution.work_order_id}' does not match work order '{work_order.work_order_id}'"
            )

        execution.transition_to(
            TesterExecutionStatus.CANCELLED,
            reason=reason,
        )

        self.emit_event(
            event_type=EventType.TESTER_CANCELLED,
            payload={
                "execution_id": execution.execution_id,
                "work_order_id": execution.work_order_id,
                "manager_task_id": execution.task_id,
                "project_id": execution.project_id,
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

    def assert_authority_boundary(
        self,
        work_order: TesterWorkOrder,
        execution: Optional[TesterExecution] = None,
        original_work_order: Optional[TesterWorkOrder] = None,
    ) -> None:
        """
        Enforce the strict architectural boundary between Manager and Tester.
        
        Manager owns:
        - what is tested
        - why it is tested
        - budgets (iteration and time)
        - acceptance criteria
        - scope boundaries
        - final shipping decisions
        
        Tester owns:
        - how tests are executed within authorized envelope
        - factual findings and defect identification
        - evidence collection
        - advisory recommendations (SHIP / DO_NOT_SHIP)
        
        Tester MUST NOT:
        - expand scope
        - modify WorkOrder parameters
        - self-retest without Manager assignment
        - increase budgets
        """
        if original_work_order is not None:
            if work_order.time_budget > original_work_order.time_budget:
                raise TesterBoundaryViolationError(
                    action="INCREASE_BUDGET",
                    reason=f"Tester cannot increase time_budget ({work_order.time_budget} > {original_work_order.time_budget}). Budgets are solely Manager authority.",
                )
            if work_order.iteration_budget > original_work_order.iteration_budget:
                raise TesterBoundaryViolationError(
                    action="INCREASE_BUDGET",
                    reason=f"Tester cannot increase iteration_budget ({work_order.iteration_budget} > {original_work_order.iteration_budget}). Budgets are solely Manager authority.",
                )
            orig_scopes = set(original_work_order.test_scope.to_list() if hasattr(original_work_order.test_scope, "to_list") else original_work_order.test_scope)
            curr_scopes = set(work_order.test_scope.to_list() if hasattr(work_order.test_scope, "to_list") else work_order.test_scope)
            if not curr_scopes.issubset(orig_scopes) and orig_scopes:
                raise TesterBoundaryViolationError(
                    action="EXPAND_TEST_SCOPE",
                    reason=f"Tester cannot expand test scope beyond Manager authorization: {curr_scopes - orig_scopes}.",
                )

        if execution is not None:
            wo_scopes = work_order.test_scope.to_list() if hasattr(work_order.test_scope, "to_list") else list(work_order.test_scope)
            plan_cases = getattr(getattr(execution, "test_plan", None), "test_cases", []) or []
            plan_case_ids = {getattr(c, "test_case_id", "") for c in plan_cases}
            for tc in execution.test_cases:
                # If test case was planned in authorized frozen test plan, it is within authorized boundary
                if tc.test_id in plan_case_ids:
                    continue
                target = getattr(tc, "name", None) or getattr(tc, "test_name", None) or tc.test_id
                # If test case was planned from authorized acceptance criteria, it is within authorized scope
                if target.startswith("Evaluate acceptance criterion") or any(
                    getattr(ac, "criterion_id", "") in target or getattr(ac, "description", "") in target
                    for ac in getattr(work_order, "acceptance_criteria", [])
                ):
                    continue
                # If target surfaces or metadata link to authorized scope
                surfaces = getattr(tc, "covered_surfaces", []) or getattr(tc, "metadata", {}).get("covered_surfaces", [])
                if surfaces and any(any(s.strip().lower() in str(surf).strip().lower() or str(surf).strip().lower() in s.strip().lower() for s in wo_scopes) for surf in surfaces):
                    continue
                TesterBoundaryGuard.assert_scope_bounded(target, wo_scopes)

    def receive_result(
        self,
        result: TesterResult,
        execution: TesterExecution,
        work_order: TesterWorkOrder,
        causation_id: Optional[str] = None,
    ) -> WorkerOutput:
        """
        Manager receives TesterResult, validates strict causal lineage,
        transitions execution to terminal state, emits terminal event, and converts
        to WorkerOutput.
        """
        # Strict causal lineage check
        result.validate_lineage(execution=execution, work_order=work_order)
        if execution.work_order_id != work_order.work_order_id:
            raise TesterLineageError(
                f"Execution work_order_id '{execution.work_order_id}' does not match work order '{work_order.work_order_id}'"
            )
        if execution.task_id != work_order.manager_task_id:
            raise TesterLineageError(
                f"Execution task_id '{execution.task_id}' does not match work order task_id '{work_order.manager_task_id}'"
            )
        if execution.project_id != work_order.project_id:
            raise TesterLineageError(
                f"Execution project_id '{execution.project_id}' does not match work order project_id '{work_order.project_id}'"
            )

        # Enforce authority boundary on completed execution
        self.assert_authority_boundary(work_order, execution)

        # Transition execution to terminal state if not already terminal
        if not execution.is_terminal:
            if result.is_success():
                # Legitimate evaluation completion progresses to COMPLETED
                if execution.status in (TesterExecutionStatus.REQUESTED, TesterExecutionStatus.STARTING):
                    execution.transition_to(TesterExecutionStatus.RUNNING, "Running tests")
                    execution.transition_to(TesterExecutionStatus.EVALUATING, "Evaluating criteria")
                    execution.transition_to(TesterExecutionStatus.REPORTING, "Compiling report")
                elif execution.status == TesterExecutionStatus.RUNNING:
                    execution.transition_to(TesterExecutionStatus.EVALUATING, "Evaluating criteria")
                    execution.transition_to(TesterExecutionStatus.REPORTING, "Compiling report")
                elif execution.status == TesterExecutionStatus.EVALUATING:
                    execution.transition_to(TesterExecutionStatus.REPORTING, "Compiling report")
                execution.transition_to(TesterExecutionStatus.COMPLETED, "Tester evaluation completed successfully")
            elif result.status == TesterResultStatus.BLOCKED or execution.status == TesterExecutionStatus.BLOCKED:
                if execution.status != TesterExecutionStatus.BLOCKED:
                    execution.transition_to(TesterExecutionStatus.BLOCKED, reason=result.summary or "Execution blocked")
            elif result.status == TesterResultStatus.CANCELLED or execution.status == TesterExecutionStatus.CANCELLED:
                if execution.status != TesterExecutionStatus.CANCELLED:
                    execution.transition_to(TesterExecutionStatus.CANCELLED, reason=result.summary or "Execution cancelled")
            else:
                execution.transition_to(TesterExecutionStatus.FAILED, reason=result.summary or "Tester execution failed")

        # Emit corresponding terminal event
        if result.is_success():
            self.emit_event(
                event_type=EventType.TESTER_COMPLETED,
                payload={
                    "execution_id": execution.execution_id,
                    "work_order_id": work_order.work_order_id,
                    "manager_task_id": work_order.manager_task_id,
                    "project_id": work_order.project_id,
                    "result_id": result.result_id,
                    "ship_recommendation": (
                        result.ship_recommendation.value
                        if hasattr(result.ship_recommendation, "value")
                        else str(result.ship_recommendation)
                    ),
                    "summary": result.summary_for_manager or result.summary,
                    "tests_run": result.tests_run,
                    "tests_passed": result.tests_passed,
                    "tests_failed": result.tests_failed,
                    "defects_count": len(result.defects),
                    "critical_defects_count": sum(1 for d in result.defects if d.severity == DefectSeverity.CRITICAL),
                    "acceptance_fully_verified": result.is_acceptance_fully_verified(),
                },
                project_id=execution.project_id,
                task_id=execution.task_id,
                correlation_id=execution.correlation_id,
                causation_id=causation_id,
                worker_id=execution.worker_id,
                source=EventSource.WORKER,
            )
        elif result.status == TesterResultStatus.BLOCKED or execution.status == TesterExecutionStatus.BLOCKED:
            self.emit_event(
                event_type=EventType.TESTER_BLOCKED,
                payload={
                    "execution_id": execution.execution_id,
                    "work_order_id": work_order.work_order_id,
                    "manager_task_id": work_order.manager_task_id,
                    "project_id": work_order.project_id,
                    "result_id": result.result_id,
                    "reason": "; ".join(result.blockers) if result.blockers else result.summary,
                    "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                },
                project_id=execution.project_id,
                task_id=execution.task_id,
                correlation_id=execution.correlation_id,
                causation_id=causation_id,
                worker_id=execution.worker_id,
                source=EventSource.WORKER,
            )
        elif result.status == TesterResultStatus.CANCELLED or execution.status == TesterExecutionStatus.CANCELLED:
            self.emit_event(
                event_type=EventType.TESTER_CANCELLED,
                payload={
                    "execution_id": execution.execution_id,
                    "work_order_id": work_order.work_order_id,
                    "manager_task_id": work_order.manager_task_id,
                    "project_id": work_order.project_id,
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
                event_type=EventType.TESTER_FAILED,
                payload={
                    "execution_id": execution.execution_id,
                    "work_order_id": work_order.work_order_id,
                    "manager_task_id": work_order.manager_task_id,
                    "project_id": work_order.project_id,
                    "result_id": result.result_id,
                    "error": result.summary or "Tester execution failed",
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

    def execute_work_order(
        self,
        work_order: TesterWorkOrder,
        pipeline: Optional[Any] = None,
        **pipeline_kwargs: Any,
    ) -> tuple[TesterExecution, TesterResult, WorkerOutput]:
        """
        Execute an authoritative TesterWorkOrder through the functional evaluation pipeline.
        Ensures issuing or dispatching a work order directly activates real testing.
        """
        from core.tester.contracts.functional_pipeline import FunctionalEvaluationPipeline
        active_pipeline = pipeline or FunctionalEvaluationPipeline(bridge=self, event_sink=self.event_sink)
        return active_pipeline.execute(work_order=work_order, **pipeline_kwargs)


class FakeTesterWorker(Worker):
    """
    Test double for Tester subsystem execution.
    
    Simulates deterministic Tester execution cycles without running real browsers,
    capturing real video, or touching external networks/systems.
    """
    __test__ = False
    worker_type: str = WorkerType.TESTER.value

    def __init__(
        self,
        bridge: TesterManagerBridge,
        worker_id: str = "worker.tester",
        simulate_failure: bool = False,
        simulate_blocker: Optional[TesterBlocker] = None,
        simulate_defects: Optional[list[TesterDefect]] = None,
        simulate_test_failures: bool = False,
        test_cases: Optional[list[TestCaseResult]] = None,
        ship_recommendation: Optional[ShipRecommendation] = None,
        use_planning: bool = False,
        use_real_pipeline: bool = False,
        files_manifest: Optional[list[str]] = None,
        target_environment: Optional[TestEnvironment] = None,
    ) -> None:
        self.bridge = bridge
        self.worker_id = worker_id
        self.simulate_failure = simulate_failure
        self.simulate_blocker = simulate_blocker
        self.simulate_defects = list(simulate_defects or [])
        self.simulate_test_failures = simulate_test_failures
        self.test_cases = test_cases
        self.ship_recommendation = ship_recommendation
        self.use_planning = use_planning
        self.use_real_pipeline = use_real_pipeline
        self.files_manifest = files_manifest
        self.target_environment = target_environment

    def get_manifest(self) -> WorkerManifest:
        """Return static manifest representing Tester V1 capability boundary."""
        return WorkerManifest(
            id=self.worker_id,
            name="Specialist Tester Worker",
            role="Tester",
            description="Evaluates products and reports evidence-backed findings to Manager.",
            version="1.0.0",
            capabilities=[
                "TESTING",
                "CODE_ANALYSIS",
                "STRUCTURED_OUTPUT",
            ],
            tools=["test.run", "test.report"],
            permissions=["read:artifacts", "create:evidence"],
            status=WorkerStatus.IDLE,
        )

    def execute_task(self, context: Any, task: Optional[Task] = None) -> WorkerOutput:
        """Standard Worker.execute_task interface implementation."""
        t = task or getattr(context, "task", None)
        wo = self.bridge.issue_work_order(t)
        _, _, output = self.execute_work_order(wo)
        return output

    def execute_work_order(
        self,
        work_order: TesterWorkOrder,
    ) -> tuple[TesterExecution, TesterResult, WorkerOutput]:
        """
        Simulate deterministic Tester execution cycle:
        1. Dispatch work order (REQUESTED -> STARTING)
        2. Progress through RUNNING -> EVALUATING -> REPORTING -> COMPLETED
        3. Record test cases and collected evidence
        4. Identify defects and evaluate acceptance criteria
        5. Formulate final TesterResult with advisory ShipRecommendation
        6. Return TesterResult and WorkerOutput via Manager bridge
        """
        # 1. Dispatch
        execution = self.bridge.dispatch_work_order(work_order, worker_id=self.worker_id)

        # 2. Simulate blocker if configured
        if self.simulate_blocker:
            self.bridge.escalate_blocker(execution, self.simulate_blocker, work_order=work_order)
            result = execution.create_result(
                status=TesterResultStatus.BLOCKED,
                summary_for_manager=f"Execution blocked: {self.simulate_blocker.description}",
                ship_recommendation=ShipRecommendation.NOT_VERIFIED,
            )
            worker_output = self.bridge.receive_result(result, execution, work_order)
            return execution, result, worker_output

        if self.use_real_pipeline:
            from core.tester.contracts.functional_pipeline import FunctionalEvaluationPipeline
            pipeline = FunctionalEvaluationPipeline(bridge=self.bridge, worker_id=self.worker_id)
            return pipeline.execute(
                work_order=work_order,
                execution=execution,
                environment=self.target_environment or getattr(work_order, "test_environment", None),
                files_manifest=self.files_manifest,
            )

        # Optional Phase 3 planning pipeline integration
        if self.use_planning:
            from core.tester.contracts.planning_pipeline import TestPlanningPipeline
            pipeline = TestPlanningPipeline(bridge=self.bridge)
            execution, terminal_result, plan = pipeline.execute_planning_pipeline(
                execution=execution,
                work_order=work_order,
                environment=self.target_environment or getattr(work_order, "test_environment", None),
                runtime_capabilities=work_order.authorized_capabilities,
                files_manifest=self.files_manifest,
            )
            if terminal_result is not None:
                worker_output = self.bridge.receive_result(terminal_result, execution, work_order)
                return execution, terminal_result, worker_output
        else:
            # 3. Progress directly to RUNNING
            execution.transition_to(
                TesterExecutionStatus.RUNNING,
                reason="Executing authorized tests within scope",
            )

        # 4. Record test cases
        if self.test_cases:
            for tc in self.test_cases:
                execution.record_test_case(tc)
        else:
            wo_scopes = work_order.test_scope.to_list() if hasattr(work_order.test_scope, "to_list") else list(work_order.test_scope)
            primary_scope = wo_scopes[0] if wo_scopes else "test.functional"
            tc_status = TestCaseStatus.FAIL if self.simulate_test_failures else TestCaseStatus.PASS
            obs_behavior = "Simulated assertion failure in test execution" if self.simulate_test_failures else "Verified"
            execution.record_test_case(
                TestCaseResult(
                    test_id=new_test_case_id(),
                    name=f"{primary_scope}.verification",
                    status=tc_status,
                    observed_behavior=obs_behavior,
                    evidence_ids=[new_evidence_id()],
                )
            )

        # 5. Record evidence
        execution.record_evidence(
            TesterEvidence(
                evidence_id=new_evidence_id(),
                evidence_type=EvidenceType.TEST_OUTPUT,
                description="Deterministic simulated test execution output",
                data="All assertions evaluated." if not self.simulate_test_failures else "AssertionError: expected True but got False",
            )
        )

        # 6. Record defects if simulated
        for defect in self.simulate_defects:
            execution.record_defect(
                title=defect.title,
                description=defect.description,
                severity=defect.severity,
                defect_type=defect.defect_type,
                reproduction_steps=defect.reproduction_steps,
                expected_behavior=defect.expected_behavior,
                actual_behavior=defect.actual_behavior,
                evidence_ids=defect.evidence_ids,
            )

        # 7. Progress to EVALUATING
        execution.transition_to(
            TesterExecutionStatus.EVALUATING,
            reason="Evaluating acceptance criteria, findings, and defects",
        )

        # Evaluate acceptance criteria from work order
        for ac in work_order.acceptance_criteria:
            ac_id = getattr(ac, "criterion_id", None) or "ac-1"
            ac_desc = getattr(ac, "description", None) or "Criteria check"
            if self.simulate_test_failures or any(d.severity == DefectSeverity.CRITICAL for d in self.simulate_defects):
                ac_status = AcceptanceCriterionStatus.FAIL
            else:
                ac_status = AcceptanceCriterionStatus.PASS
            execution.record_acceptance_result(
                criterion_id=ac_id,
                description=ac_desc,
                status=ac_status,
                notes="Simulated verification outcome",
            )

        # Record evaluative finding
        execution.record_finding(
            category=FindingCategory.OBSERVATION,
            title="Evaluation observation",
            description=f"Evaluated {len(work_order.acceptance_criteria)} acceptance criteria against authorized scope.",
        )

        # Determine advisory ship recommendation
        if self.ship_recommendation:
            final_ship_rec = self.ship_recommendation
        elif self.simulate_test_failures or any(d.severity in (DefectSeverity.CRITICAL, DefectSeverity.HIGH) for d in self.simulate_defects):
            final_ship_rec = ShipRecommendation.DO_NOT_SHIP
        elif self.simulate_defects:
            final_ship_rec = ShipRecommendation.SHIP_WITH_WARNINGS
        else:
            final_ship_rec = ShipRecommendation.SHIP

        # 8. Progress to REPORTING
        execution.transition_to(
            TesterExecutionStatus.REPORTING,
            reason="Compiling final TesterResult package",
        )

        # 9. Create final result
        if self.simulate_failure:
            res_status = TesterResultStatus.FAILED
            summary = "Simulated internal execution failure occurred"
        else:
            res_status = TesterResultStatus.COMPLETED
            summary = f"Completed evaluation of objective: '{work_order.objective}'. Advisory: {final_ship_rec.value}"

        result = execution.create_result(
            status=res_status,
            summary_for_manager=summary,
            ship_recommendation=final_ship_rec,
        )

        # 10. Manager receives result via bridge
        worker_output = self.bridge.receive_result(result, execution, work_order)

        return execution, result, worker_output

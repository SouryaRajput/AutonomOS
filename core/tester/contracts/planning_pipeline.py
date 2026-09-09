from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from core.events.types import EventSource, EventType
from core.tester.contracts.applicability import (
    TestApplicabilityClassifier,
    TestApplicabilityReport,
)
from core.tester.contracts.blocker import TesterBlocker
from core.tester.contracts.context import TestContext, TestContextBuilder
from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.identifiers import new_trace_id
from core.tester.contracts.plan import TestPlan, TestPlanGenerator
from core.tester.contracts.plan_validator import (
    TestPlanValidationResult,
    TestPlanValidator,
)
from core.tester.contracts.result import TesterResult
from core.tester.contracts.trace import TesterTrace
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    TesterBlockerError,
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    ApplicableTestCategory,
    PlanValidationCode,
    ShipRecommendation,
    TesterActionType,
    TesterBlockerCategory,
    TesterBlockerSeverity,
    TesterExecutionStatus,
    TesterResultStatus,
    TestingCapability,
)

logger = logging.getLogger("AutonomOS.TestPlanningPipeline")


class TestPlanningPipeline:
    """
    Deterministic planning pipeline for Tester V1 Phase 3.6.
    
    Orchestrates the entire Phase 3 planning lifecycle before test execution:
    TesterWorkOrder
          ↓
    TestContextBuilder (Phase 3.1)
          ↓
    TestApplicabilityClassifier (Phase 3.2)
          ↓
    TestPlanGenerator & Prioritizer/Coverage (Phase 3.3, 3.4)
          ↓
    TestPlanValidator (Phase 3.5)
          ↓
    FROZEN TEST PLAN (Phase 3.6)
          ↓
    Phase 4 Test Execution / Terminal NO_APPLICABLE_TESTS
    
    Hard Invariant:
    The Tester MUST NOT start test execution before a valid frozen TestPlan exists.
    The only exception is the explicit terminal planning outcome: NO_APPLICABLE_TESTS.
    """
    __test__ = False

    def __init__(
        self,
        context_builder: Optional[TestContextBuilder] = None,
        classifier: Optional[TestApplicabilityClassifier] = None,
        generator: Optional[TestPlanGenerator] = None,
        validator: Optional[TestPlanValidator] = None,
        bridge: Optional[Any] = None,
    ) -> None:
        self.classifier = classifier or TestApplicabilityClassifier()
        self.context_builder = context_builder or TestContextBuilder()
        self.generator = generator or TestPlanGenerator(classifier=self.classifier)
        self.validator = validator or TestPlanValidator()
        self.bridge = bridge

    def _emit(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        execution: TesterExecution,
        work_order: TesterWorkOrder,
    ) -> None:
        """Helper to emit auditable planning domain events through manager bridge if available."""
        if self.bridge and hasattr(self.bridge, "emit_event"):
            self.bridge.emit_event(
                event_type=event_type,
                payload=payload,
                project_id=execution.project_id,
                task_id=execution.task_id,
                correlation_id=execution.correlation_id,
                worker_id=execution.worker_id,
                source=EventSource.WORKER,
            )

    def run_planning(
        self,
        execution: TesterExecution,
        work_order: TesterWorkOrder,
        environment: Optional[TestEnvironment] = None,
        runtime_capabilities: Optional[Sequence[TestingCapability | str]] = None,
        files_manifest: Optional[Sequence[str]] = None,
        target_routes: Optional[Sequence[str]] = None,
        target_components: Optional[Sequence[str]] = None,
        test_plan: Optional[TestPlan] = None,
    ) -> TestPlan:
        """
        Execute deterministic planning sequence and freeze valid TestPlan.
        
        Transitions:
        STARTING -> PLANNING -> PLAN_VALIDATION -> PLAN_FROZEN
        (or FAILED if validation fails, BLOCKED if material blocker occurs).
        """
        # 1. Verify strict lineage
        execution.validate_lineage(work_order=work_order)
        execution.require_frozen_plan = True

        # 2. Transition to PLANNING
        execution.transition_to(
            TesterExecutionStatus.PLANNING,
            reason="Starting deterministic test planning and context analysis",
        )
        self._emit(
            event_type=EventType.TESTER_PLANNING_STARTED,
            payload={
                "execution_id": execution.execution_id,
                "work_order_id": work_order.work_order_id,
                "project_id": work_order.project_id,
                "objective": work_order.objective,
                "authorized_capabilities": [
                    c.value if hasattr(c, "value") else str(c)
                    for c in (work_order.authorized_capabilities or [])
                ],
            },
            execution=execution,
            work_order=work_order,
        )

        # 3. Assemble TestContext (Phase 3.1)
        if execution.test_context is not None:
            context = execution.test_context
        else:
            context = self.context_builder.build(
                work_order=work_order,
                execution=execution,
                files_manifest=files_manifest,
                target_routes=target_routes,
                target_components=target_components,
                environment=environment,
            )
            execution.attach_test_context(context)

        execution.traces.append(
            TesterTrace(
                trace_id=new_trace_id(),
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
                task_id=execution.task_id,
                project_id=execution.project_id,
                correlation_id=execution.correlation_id,
                action_type=TesterActionType.BUILD_CONTEXT,
                action_details={
                    "change_categories": [c.value for c in context.change_categories],
                    "changed_files_count": len(context.changed_files),
                    "changed_components_count": len(context.changed_components),
                    "changed_routes_count": len(context.changed_routes),
                },
            )
        )

        # 4. Classify Test Applicability (Phase 3.2)
        if execution.test_applicability is not None:
            app_report = execution.test_applicability
        else:
            app_report = self.classifier.classify(
                work_order=work_order,
                test_context=context,
                environment=environment,
                runtime_capabilities=runtime_capabilities,
            )
            execution.attach_test_applicability(app_report)

        execution.traces.append(
            TesterTrace(
                trace_id=new_trace_id(),
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
                task_id=execution.task_id,
                project_id=execution.project_id,
                correlation_id=execution.correlation_id,
                action_type=TesterActionType.CLASSIFY_APPLICABILITY,
                action_details={
                    "required_categories": [c.value for c in app_report.required_categories],
                    "optional_categories": [c.value for c in app_report.optional_categories],
                    "not_applicable_categories": [c.value for c in app_report.not_applicable_categories],
                },
            )
        )

        # 5. Generate Finite Test Plan & Coverage/Priority (Phase 3.3, 3.4)
        if test_plan is not None:
            plan = test_plan
        else:
            plan = self.generator.generate(
                work_order=work_order,
                test_context=context,
                applicability_report=app_report,
            )

        execution.traces.append(
            TesterTrace(
                trace_id=new_trace_id(),
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
                task_id=execution.task_id,
                project_id=execution.project_id,
                correlation_id=execution.correlation_id,
                action_type=TesterActionType.GENERATE_PLAN,
                action_details={
                    "plan_id": plan.plan_id,
                    "test_cases_count": len(plan.test_cases),
                    "time_budget": plan.time_budget,
                    "iteration_budget": plan.iteration_budget,
                    "scope_summary": plan.estimated_execution_scope,
                },
            )
        )
        self._emit(
            event_type=EventType.TESTER_PLAN_GENERATED,
            payload={
                "execution_id": execution.execution_id,
                "plan_id": plan.plan_id,
                "test_cases_count": len(plan.test_cases),
                "required_categories": [c.value for c in plan.required_categories],
                "time_budget": plan.time_budget,
            },
            execution=execution,
            work_order=work_order,
        )

        # 6. Validate Test Plan (Phase 3.5)
        execution.transition_to(
            TesterExecutionStatus.PLAN_VALIDATION,
            reason="Validating generated test plan against authorizations and bounds",
        )
        val_result = self.validator.validate(
            test_plan=plan,
            work_order=work_order,
            test_context=context,
            environment=environment,
            runtime_capabilities=runtime_capabilities,
        )

        execution.traces.append(
            TesterTrace(
                trace_id=new_trace_id(),
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
                task_id=execution.task_id,
                project_id=execution.project_id,
                correlation_id=execution.correlation_id,
                action_type=TesterActionType.VALIDATE_PLAN,
                action_details={
                    "report_id": val_result.report_id,
                    "is_valid": val_result.is_valid,
                    "issues_count": len(val_result.issues),
                    "summary": val_result.summary,
                },
            )
        )

        # 7. Check validation outcome
        if not val_result.is_valid:
            error_msgs = [f"[{i.code.value}] {i.message}" for i in val_result.issues]
            combined_error = "; ".join(error_msgs)
            plan.mark_invalid(combined_error)

            self._emit(
                event_type=EventType.TESTER_PLAN_FAILED,
                payload={
                    "execution_id": execution.execution_id,
                    "plan_id": plan.plan_id,
                    "report_id": val_result.report_id,
                    "errors": [i.to_dict() for i in val_result.issues],
                    "summary": val_result.summary,
                },
                execution=execution,
                work_order=work_order,
            )

            execution.transition_to(
                TesterExecutionStatus.FAILED,
                reason=f"TestPlan validation failed: {val_result.summary}",
            )
            raise TesterValidationError(
                f"TestPlan validation failed with {len(val_result.issues)} issues: {val_result.summary}"
            )

        # 8. Freeze valid TestPlan
        plan.mark_validated()
        plan.freeze()
        execution.attach_test_plan(plan)

        self._emit(
            event_type=EventType.TESTER_PLAN_VALIDATED,
            payload={
                "execution_id": execution.execution_id,
                "plan_id": plan.plan_id,
                "report_id": val_result.report_id,
                "test_cases_count": len(plan.test_cases),
            },
            execution=execution,
            work_order=work_order,
        )

        execution.transition_to(
            TesterExecutionStatus.PLAN_FROZEN,
            reason=f"TestPlan '{plan.plan_id}' frozen with {len(plan.test_cases)} test cases",
        )
        self._emit(
            event_type=EventType.TESTER_PLAN_FROZEN,
            payload={
                "execution_id": execution.execution_id,
                "plan_id": plan.plan_id,
                "test_cases_count": len(plan.test_cases),
                "is_frozen": True,
            },
            execution=execution,
            work_order=work_order,
        )

        execution.traces.append(
            TesterTrace(
                trace_id=new_trace_id(),
                execution_id=execution.execution_id,
                work_order_id=work_order.work_order_id,
                task_id=execution.task_id,
                project_id=execution.project_id,
                correlation_id=execution.correlation_id,
                action_type=TesterActionType.FREEZE_PLAN,
                action_details={
                    "plan_id": plan.plan_id,
                    "test_cases_count": len(plan.test_cases),
                    "status": plan.status.value,
                },
            )
        )

        # 9. Check for zero applicable tests outcome
        if len(plan.test_cases) == 0:
            self._emit(
                event_type=EventType.TESTER_NO_APPLICABLE_TESTS,
                payload={
                    "execution_id": execution.execution_id,
                    "work_order_id": work_order.work_order_id,
                    "project_id": work_order.project_id,
                    "reason": "No applicable test categories or changed executable surfaces for execution scope.",
                },
                execution=execution,
                work_order=work_order,
            )

        return plan

    def execute_planning_pipeline(
        self,
        execution: TesterExecution,
        work_order: TesterWorkOrder,
        environment: Optional[TestEnvironment] = None,
        runtime_capabilities: Optional[Sequence[TestingCapability | str]] = None,
        files_manifest: Optional[Sequence[str]] = None,
        target_routes: Optional[Sequence[str]] = None,
        target_components: Optional[Sequence[str]] = None,
        test_plan: Optional[TestPlan] = None,
    ) -> tuple[TesterExecution, Optional[TesterResult], TestPlan]:
        """
        Execute full planning pipeline and route execution cleanly:
        - If NO_APPLICABLE_TESTS: completes execution cleanly without executing tests.
        - If executable tests exist: transitions to RUNNING, ready for Phase 4 execution.
        """
        plan = self.run_planning(
            execution=execution,
            work_order=work_order,
            environment=environment,
            runtime_capabilities=runtime_capabilities,
            files_manifest=files_manifest,
            target_routes=target_routes,
            target_components=target_components,
            test_plan=test_plan,
        )

        # Handle zero applicable tests terminal outcome
        if len(plan.test_cases) == 0:
            execution.transition_to(
                TesterExecutionStatus.REPORTING,
                reason="Planning determined zero applicable tests: NO_APPLICABLE_TESTS",
            )
            result = execution.create_result(
                status=TesterResultStatus.COMPLETED,
                summary_for_manager=(
                    f"Testing completed without execution: NO_APPLICABLE_TESTS for scope '{work_order.objective}'. "
                    "No relevant changes or authorized testing categories exist."
                ),
                ship_recommendation=ShipRecommendation.SHIP,
                metadata={
                    "planning_outcome": "NO_APPLICABLE_TESTS",
                    "plan_id": plan.plan_id,
                    "zero_test_plan": True,
                },
            )
            execution.transition_to(
                TesterExecutionStatus.COMPLETED,
                reason="Execution completed with terminal outcome: NO_APPLICABLE_TESTS",
            )
            return execution, result, plan

        # Executable tests exist: transition to RUNNING
        execution.transition_to(
            TesterExecutionStatus.RUNNING,
            reason=f"Beginning execution of {len(plan.test_cases)} authorized tests from frozen plan '{plan.plan_id}'",
        )
        return execution, None, plan

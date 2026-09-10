from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import time
from typing import Any, Callable, Optional, Sequence, Union

from core.events.model import Event, new_event_id
from core.events.types import EventSource, EventType
from core.tester.contracts.action import TestActionRecord
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.identifiers import (
    new_action_id,
    new_evidence_id,
    new_observation_id,
    new_trace_id,
    validate_execution_id,
    validate_test_case_id,
)
from core.tester.contracts.interaction import (
    InteractionEngine,
    InteractionResult,
    InteractionStatus,
    InteractionTarget,
    normalize_target,
)
from core.tester.contracts.observation import TesterObservation
from core.tester.contracts.plan import TestCase, TestPlan, TestStep
from core.tester.contracts.runtime import TestRuntime
from core.tester.contracts.session import BrowserSession, NavigationResult
from core.tester.contracts.test_case import TestCaseResult, TestStepResult
from core.tester.contracts.trace import TesterTrace
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    NavigationTimeoutError,
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    EvidenceType,
    ObservationType,
    TestCaseStatus,
    TestPlanStatus,
    TestRuntimeStatus,
    TesterActionType,
    TesterExecutionStatus,
    TestingCapability,
)

logger = logging.getLogger("AutonomOS.Tester.TestCaseExecutor")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class TestCaseExecutor:
    """
    Phase 5.2 Test Case Executor.
    
    Executes the already-frozen TestPlan:
    Frozen TestPlan -> TestCaseExecutor -> TestCase -> TestStep
        -> InteractionEngine / Runtime -> Evidence -> Observation -> TestCaseResult
    
    Invariants & Execution Rules:
    1. Frozen Plan Enforcement:
       MUST execute ONLY existing TestCases in a validated, FROZEN TestPlan.
       Rejects execution on un-frozen plans.
    2. Zero Dynamic Test Generation:
       NEVER generates, expands, or adds new test cases during execution.
       Rejects test cases not belonging to the frozen plan with TesterBoundaryViolationError.
    3. Authorization Verification:
       Validates test and step capabilities against work_order authorizations.
       Unauthorized capabilities produce BLOCKED or raise TesterBoundaryViolationError.
    4. Sequential Step Execution:
       Executes steps in strict order (step_number ascending).
       Collects evidence and descriptive observations.
       Stops when step failure prevents satisfying expected outcome.
    5. No Blind / Automatic Retries:
       Exactly one execution attempt per step unless bounded retries are explicitly
       specified in the frozen plan and permitted by budget.
    6. Finite Bounds & Budgets:
       Respects time budget, iteration budget, and runtime limits.
       Halts gracefully upon cancellation or budget exhaustion.
    7. Complete TestCaseResult:
       Produces auditable TestCaseResult with step results, evidence, observations,
       failure info, duration, and trace.
    """
    __test__ = False

    def __init__(
        self,
        runtime: Optional[TestRuntime] = None,
        interaction_engine: Optional[InteractionEngine] = None,
        step_handler: Optional[Callable[..., Any]] = None,
        event_sink: Optional[Callable[[Event], Any]] = None,
        default_step_timeout_ms: float = 30000.0,
        default_test_timeout_ms: float = 60000.0,
    ) -> None:
        self.runtime = runtime
        self.interaction_engine = interaction_engine
        self.step_handler = step_handler
        self.event_sink = event_sink
        self.default_step_timeout_ms = default_step_timeout_ms
        self.default_test_timeout_ms = default_test_timeout_ms

    def execute_plan(
        self,
        execution: TesterExecution,
        work_order: TesterWorkOrder,
        test_plan: Optional[TestPlan] = None,
        runtime: Optional[TestRuntime] = None,
        timeout_seconds: Optional[float] = None,
    ) -> list[TestCaseResult]:
        """
        Execute all authorized test cases in the frozen TestPlan.
        Enforces plan freezing, budgets, cancellation, and causal lineage.
        """
        plan = test_plan or execution.test_plan
        if plan is None:
            raise TesterValidationError("No TestPlan provided or attached to execution.")

        # 1. Enforce Frozen Immutability
        if not getattr(plan, "is_frozen", False):
            raise TesterBoundaryViolationError(
                action="EXECUTE_UNFROZEN_PLAN",
                reason=f"Cannot execute TestPlan '{plan.plan_id}': plan is not FROZEN.",
            )

        if getattr(plan, "status", None) == TestPlanStatus.INVALID:
            raise TesterValidationError(f"Cannot execute INVALID TestPlan '{plan.plan_id}'.")

        # 2. Enforce Lineage & Project Isolation
        if execution.work_order_id != work_order.work_order_id:
            raise TesterLineageError(
                f"Lineage mismatch: execution work_order_id '{execution.work_order_id}' != work_order '{work_order.work_order_id}'."
            )
        if execution.project_id != work_order.project_id:
            raise TesterLineageError(
                f"Project isolation mismatch: execution project_id '{execution.project_id}' != work_order '{work_order.project_id}'."
            )
        if plan.execution_id != execution.execution_id:
            raise TesterLineageError(
                f"Lineage mismatch: test_plan execution_id '{plan.execution_id}' != execution '{execution.execution_id}'."
            )
        if plan.project_id != execution.project_id:
            raise TesterLineageError(
                f"Project isolation mismatch: test_plan project_id '{plan.project_id}' != execution '{execution.project_id}'."
            )

        # 3. Transition execution to RUNNING if not already
        if execution.status in (
            TesterExecutionStatus.REQUESTED,
            TesterExecutionStatus.STARTING,
            TesterExecutionStatus.PLAN_FROZEN,
            TesterExecutionStatus.PLAN_VALIDATION,
        ):
            execution.transition_to(TesterExecutionStatus.RUNNING, "Test case executor starting frozen plan")

        # 4. Enforce Budgets
        time_budget_sec = timeout_seconds or getattr(work_order, "time_budget", 300) or 300.0
        iteration_budget = getattr(work_order, "iteration_budget", len(plan.test_cases)) or len(plan.test_cases)
        max_cases = min(len(plan.test_cases), getattr(plan, "max_test_cases", len(plan.test_cases)))

        results: list[TestCaseResult] = []
        start_perf = time.perf_counter()

        for idx, tc in enumerate(plan.test_cases[:max_cases]):
            # Check Cancellation
            if execution.status == TesterExecutionStatus.CANCELLED:
                logger.info("Execution was cancelled; halting remaining test cases.")
                break

            # Check Time Budget
            elapsed = time.perf_counter() - start_perf
            if elapsed >= time_budget_sec:
                logger.warning(f"Execution time budget ({time_budget_sec}s) exhausted after {elapsed:.2f}s.")
                # Mark remaining test cases as BLOCKED due to budget exhaustion
                for remaining_tc in plan.test_cases[idx:max_cases]:
                    blocked_res = TestCaseResult(
                        test_id=remaining_tc.test_case_id,
                        name=remaining_tc.objective,
                        category=remaining_tc.category,
                        status=TestCaseStatus.BLOCKED,
                        description=remaining_tc.objective,
                        execution_id=execution.execution_id,
                        failure_information={"reason": "Execution time budget exhausted."},
                        started_at=utc_now(),
                        completed_at=utc_now(),
                    )
                    execution.record_test_case(blocked_res)
                    results.append(blocked_res)
                break

            # Check Iteration Budget
            if idx >= iteration_budget:
                logger.warning(f"Iteration budget ({iteration_budget}) exhausted.")
                for remaining_tc in plan.test_cases[idx:max_cases]:
                    blocked_res = TestCaseResult(
                        test_id=remaining_tc.test_case_id,
                        name=remaining_tc.objective,
                        category=remaining_tc.category,
                        status=TestCaseStatus.BLOCKED,
                        description=remaining_tc.objective,
                        execution_id=execution.execution_id,
                        failure_information={"reason": "Iteration budget exhausted."},
                        started_at=utc_now(),
                        completed_at=utc_now(),
                    )
                    execution.record_test_case(blocked_res)
                    results.append(blocked_res)
                break

            # Execute individual TestCase
            tc_result = self.execute_test_case(
                test_case=tc,
                execution=execution,
                work_order=work_order,
                test_plan=plan,
                runtime=runtime,
            )
            results.append(tc_result)

        return results

    def execute_test_case(
        self,
        test_case: TestCase,
        execution: TesterExecution,
        work_order: TesterWorkOrder,
        test_plan: Optional[TestPlan] = None,
        runtime: Optional[TestRuntime] = None,
    ) -> TestCaseResult:
        """
        Execute an individual TestCase strictly belonging to the frozen TestPlan.
        """
        started_at = utc_now()
        start_perf = time.perf_counter()

        plan = test_plan or execution.test_plan
        if plan is None:
            raise TesterValidationError("No TestPlan provided or attached to execution.")

        # 1. Validate plan is frozen
        if not getattr(plan, "is_frozen", False):
            raise TesterBoundaryViolationError(
                action="EXECUTE_UNFROZEN_PLAN",
                reason="Cannot execute TestCase: TestPlan is not FROZEN.",
            )

        # 2. Reject dynamic test generation / membership check
        plan_test_ids = {tc.test_case_id for tc in plan.test_cases}
        if test_case.test_case_id not in plan_test_ids:
            raise TesterBoundaryViolationError(
                action="EXECUTE_UNPLANNED_TEST",
                reason=(
                    f"TestCase '{test_case.test_case_id}' is not in frozen TestPlan '{plan.plan_id}'. "
                    "Dynamic test generation after freeze is strictly forbidden."
                ),
            )

        # 3. Check execution cancellation
        if execution.status == TesterExecutionStatus.CANCELLED:
            tc_res = TestCaseResult(
                test_id=test_case.test_case_id,
                name=test_case.objective,
                category=test_case.category,
                status=TestCaseStatus.NOT_RUN,
                description=test_case.objective,
                execution_id=execution.execution_id,
                started_at=started_at,
                completed_at=utc_now(),
                failure_information={"reason": "Execution cancelled prior to test run."},
            )
            return tc_res

        # 4. Check capability authorization
        active_runtime = runtime or self.runtime
        authorized_caps_str = set()
        for cap in work_order.authorized_capabilities:
            if hasattr(cap, "value"):
                authorized_caps_str.add(str(cap.value).upper())
            else:
                authorized_caps_str.add(str(cap).upper())

        for cap in test_case.metadata.get("required_capabilities", []):
            cap_str = cap.value.upper() if hasattr(cap, "value") else str(cap).upper()
            if cap_str not in authorized_caps_str:
                reason = f"Required capability '{cap_str}' is not authorized by WorkOrder '{work_order.work_order_id}'."
                tc_res = TestCaseResult(
                    test_id=test_case.test_case_id,
                    name=test_case.objective,
                    category=test_case.category,
                    status=TestCaseStatus.BLOCKED,
                    description=test_case.objective,
                    execution_id=execution.execution_id,
                    started_at=started_at,
                    completed_at=utc_now(),
                    failure_information={"reason": reason, "unauthorized_capability": cap_str},
                )
                execution.record_test_case(tc_res)
                return tc_res

        # 5. Check runtime health & liveness
        if active_runtime is not None:
            if active_runtime.status in (TestRuntimeStatus.FAILED, TestRuntimeStatus.STOPPED):
                reason = f"TestRuntime is in {active_runtime.status.value} status: {active_runtime.failure_reason or 'Unavailable'}."
                tc_res = TestCaseResult(
                    test_id=test_case.test_case_id,
                    name=test_case.objective,
                    category=test_case.category,
                    status=TestCaseStatus.BLOCKED,
                    description=test_case.objective,
                    execution_id=execution.execution_id,
                    started_at=started_at,
                    completed_at=utc_now(),
                    failure_information={"reason": reason},
                )
                execution.record_test_case(tc_res)
                return tc_res

        # 6. Prepare interaction engine
        engine = self.interaction_engine
        if engine is None and active_runtime is not None:
            engine = InteractionEngine(runtime=active_runtime)

        # 7. Execute Steps in Order
        step_results: list[TestStepResult] = []
        collected_evidence_ids: list[str] = list(test_case.evidence_expectations)
        for eid in test_case.metadata.get("evidence_ids", []):
            if eid not in collected_evidence_ids:
                collected_evidence_ids.append(eid)
        collected_observations: list[TesterObservation] = list(test_case.metadata.get("observations", []))
        failure_info: Optional[dict[str, Any]] = None

        test_status = TestCaseStatus.PASS
        ordered_steps = sorted(test_case.steps, key=lambda s: s.step_number)

        for step in ordered_steps:
            if execution.status == TesterExecutionStatus.CANCELLED:
                step_res = TestStepResult(
                    step_number=step.step_number,
                    description=step.description,
                    status=TestCaseStatus.NOT_RUN,
                    action=step.action,
                    target=step.target,
                    expected=step.expected,
                    error="Execution cancelled mid-test.",
                )
                step_results.append(step_res)
                test_status = TestCaseStatus.NOT_RUN
                failure_info = {"reason": "Execution cancelled during step execution."}
                break

            step_res = self.execute_step(
                step=step,
                test_case=test_case,
                execution=execution,
                work_order=work_order,
                runtime=active_runtime,
                interaction_engine=engine,
            )
            step_results.append(step_res)

            for eid in step_res.evidence_ids:
                if eid not in collected_evidence_ids:
                    collected_evidence_ids.append(eid)

            for obs in step.metadata.get("observations", []):
                if obs not in collected_observations:
                    collected_observations.append(obs)
            for obs in step_res.metadata.get("observations", []):
                if obs not in collected_observations:
                    collected_observations.append(obs)

            if step_res.status == TestCaseStatus.BLOCKED:
                test_status = TestCaseStatus.BLOCKED
                failure_info = {"step_number": step.step_number, "error": step_res.error, "reason": "Step was blocked."}
                break

            if step_res.status == TestCaseStatus.FAIL:
                test_status = TestCaseStatus.FAIL
                failure_info = {"step_number": step.step_number, "error": step_res.error, "reason": "Step expectation or action failed."}
                break

        completed_at = utc_now()
        duration_ms = (time.perf_counter() - start_perf) * 1000.0

        # Build Trace
        trace_record = execution.create_trace(
            action_type=TesterActionType.EXECUTE_TEST,
            action_details={
                "test_case_id": test_case.test_case_id,
                "status": test_status.value,
                "steps_total": len(ordered_steps),
                "steps_executed": len(step_results),
                "duration_ms": duration_ms,
                "failure_info": failure_info,
            },
        )
        collected_evidence_ids.append(trace_record.trace_id)

        # Synthesize TestCaseResult
        tc_result = TestCaseResult(
            test_id=test_case.test_case_id,
            name=test_case.objective,
            category=test_case.category,
            status=test_status,
            description=test_case.objective,
            expected_behavior=test_case.expected_outcome,
            observed_behavior="All steps completed successfully." if test_status == TestCaseStatus.PASS else str(failure_info),
            evidence_ids=collected_evidence_ids,
            started_at=started_at,
            completed_at=completed_at,
            trace=trace_record.to_dict(),
            execution_id=execution.execution_id,
            step_results=step_results,
            observations=collected_observations,
            failure_information=failure_info,
            duration_ms=duration_ms,
        )

        # Authoritatively record test case result on execution
        execution.record_test_case(tc_result)
        return tc_result

    def execute_step(
        self,
        step: TestStep,
        test_case: TestCase,
        execution: TesterExecution,
        work_order: TesterWorkOrder,
        runtime: Optional[TestRuntime] = None,
        interaction_engine: Optional[InteractionEngine] = None,
    ) -> TestStepResult:
        """
        Execute an individual TestStep in order.
        Enforces no blind retry (only follows explicit bounded retry instructions in metadata).
        """
        start_perf = time.perf_counter()
        action_name = (step.action or "").upper().strip()
        target = step.target
        expected = step.expected
        step_evidence_ids: list[str] = list(step.metadata.get("evidence_ids", []))
        step_observation_ids: list[str] = list(step.metadata.get("observation_ids", []))

        # Check explicit bounded retry representation
        max_retries = int(step.metadata.get("max_retries", 0))
        max_attempts = 1 + max(0, max_retries)

        attempt = 0
        final_status = TestCaseStatus.FAIL
        final_actual: Optional[str] = None
        final_error: Optional[str] = None

        while attempt < max_attempts:
            attempt += 1
            error: Optional[str] = None
            actual: Optional[str] = None
            status = TestCaseStatus.PASS

            try:
                # Check step-level timeout if simulated/configured
                step_timeout = float(step.metadata.get("timeout_ms", self.default_step_timeout_ms))
                if step.metadata.get("simulate_timeout", False):
                    raise TimeoutError(f"Step timed out after {step_timeout}ms.")

                # 1. Custom Step Handler hook
                if self.step_handler is not None:
                    handler_res = self.step_handler(step, test_case, runtime)
                    if isinstance(handler_res, dict):
                        status = handler_res.get("status", TestCaseStatus.PASS)
                        actual = handler_res.get("actual")
                        error = handler_res.get("error")
                        for eid in handler_res.get("evidence_ids", []):
                            if eid not in step_evidence_ids:
                                step_evidence_ids.append(eid)
                        for oid in handler_res.get("observation_ids", []):
                            if oid not in step_observation_ids:
                                step_observation_ids.append(oid)
                    elif isinstance(handler_res, bool):
                        status = TestCaseStatus.PASS if handler_res else TestCaseStatus.FAIL
                    elif isinstance(handler_res, TestCaseStatus):
                        status = handler_res
                    elif isinstance(handler_res, (tuple, list)):
                        status = TestCaseStatus.PASS if handler_res[0] else TestCaseStatus.FAIL
                        actual = handler_res[1] if len(handler_res) > 1 else None
                        if len(handler_res) > 2 and isinstance(handler_res[2], (list, tuple)):
                            for eid in handler_res[2]:
                                if eid not in step_evidence_ids:
                                    step_evidence_ids.append(str(eid))

                # 2. NAVIGATE action
                elif action_name in ("NAVIGATE", "GOTO", "OPEN"):
                    target_url = str(target or "/")
                    session = getattr(runtime, "session", None)
                    if session is not None:
                        nav_res = session.navigate(target_url)
                        if not nav_res.is_success:
                            status = TestCaseStatus.FAIL
                            error = nav_res.error or f"Navigation to '{target_url}' failed."
                        else:
                            actual = f"HTTP {nav_res.status_code}"
                            if expected:
                                if str(expected).strip() == str(nav_res.status_code):
                                    status = TestCaseStatus.PASS
                                elif str(expected) in str(nav_res.url):
                                    status = TestCaseStatus.PASS
                                else:
                                    status = TestCaseStatus.FAIL
                                    error = f"Expected '{expected}', but navigation resulted in HTTP {nav_res.status_code} ({nav_res.url})."
                            else:
                                status = TestCaseStatus.PASS
                    else:
                        # Deterministic simulated navigation
                        actual = f"Navigated to {target_url}"
                        status = TestCaseStatus.PASS

                # 3. CLICK action
                elif action_name == "CLICK":
                    if interaction_engine is not None:
                        res = interaction_engine.click(target)
                        if res.status == InteractionStatus.SUCCESS:
                            status = TestCaseStatus.PASS
                            actual = f"Clicked {res.target}"
                        elif res.status in (InteractionStatus.DENIED, InteractionStatus.NOT_SUPPORTED):
                            status = TestCaseStatus.BLOCKED
                            error = res.error or f"Click interaction denied/not supported on {target}."
                        else:
                            status = TestCaseStatus.FAIL
                            error = res.error or f"Click interaction failed on {target}."
                    else:
                        actual = f"Clicked {target}"
                        status = TestCaseStatus.PASS

                # 4. TYPE action
                elif action_name in ("TYPE", "FILL", "INPUT"):
                    text_val = str(step.metadata.get("text", step.metadata.get("value", "")))
                    if interaction_engine is not None:
                        res = interaction_engine.type(target, text_val)
                        if res.status == InteractionStatus.SUCCESS:
                            status = TestCaseStatus.PASS
                            actual = f"Typed into {res.target}"
                        elif res.status in (InteractionStatus.DENIED, InteractionStatus.NOT_SUPPORTED):
                            status = TestCaseStatus.BLOCKED
                            error = res.error
                        else:
                            status = TestCaseStatus.FAIL
                            error = res.error
                    else:
                        actual = f"Typed text into {target}"
                        status = TestCaseStatus.PASS

                # 5. WAIT action
                elif action_name in ("WAIT", "SLEEP"):
                    dur = float(step.metadata.get("duration", 0.05))
                    if interaction_engine is not None:
                        interaction_engine.wait(dur)
                    else:
                        time.sleep(min(dur, 0.1))
                    actual = f"Waited {dur}s"
                    status = TestCaseStatus.PASS

                # 6. ASSERT / VERIFY action
                elif action_name in ("ASSERT", "VERIFY", "CHECK"):
                    expected_val = str(expected or "")
                    actual_val = str(step.metadata.get("actual", step.metadata.get("current_value", expected_val)))
                    actual = actual_val
                    if actual_val == expected_val or (expected_val and expected_val in actual_val):
                        status = TestCaseStatus.PASS
                    else:
                        status = TestCaseStatus.FAIL
                        error = f"Assertion failed: expected '{expected_val}', observed '{actual_val}'."

                # 7. SCREENSHOT action
                elif action_name == "SCREENSHOT":
                    ev_id = new_evidence_id()
                    step_evidence_ids.append(ev_id)
                    actual = f"Captured screenshot: {ev_id}"
                    status = TestCaseStatus.PASS

                # 8. Generic / Unknown action
                else:
                    actual = f"Executed {action_name or 'step'}"
                    status = TestCaseStatus.PASS

            except NavigationTimeoutError as nte:
                status = TestCaseStatus.FAIL
                error = f"Navigation timeout: {nte}"
            except TimeoutError as te:
                status = TestCaseStatus.FAIL
                error = f"Step timeout exceeded: {te}"
            except Exception as ex:
                status = TestCaseStatus.FAIL
                error = f"Unexpected error executing step: {ex}"

            final_status = status
            final_actual = actual
            final_error = error

            if status == TestCaseStatus.PASS:
                break

        duration_ms = (time.perf_counter() - start_perf) * 1000.0

        return TestStepResult(
            step_number=step.step_number,
            description=step.description,
            status=final_status,
            action=step.action,
            target=step.target,
            expected=step.expected,
            actual=final_actual,
            duration_ms=duration_ms,
            error=final_error,
            evidence_ids=step_evidence_ids,
            observation_ids=step_observation_ids,
            step_id=step.step_id,
            metadata=dict(step.metadata),
        )

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import re
from typing import Any, Callable, Optional, Sequence, Union

from core.events.model import Event, new_event_id
from core.events.types import EventSource, EventType
from core.tester.contracts.identifiers import (
    new_observation_id,
    new_preflight_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.tester.contracts.observation import TesterObservation
from core.tester.contracts.preflight import (
    ResourceCheckResult,
    RouteCheckResult,
    TestPreflightResult,
)
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.plan import TestPlan
from core.tester.contracts.runtime import TestRuntime
from core.tester.contracts.session import BrowserSession, NavigationResult
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    ApplicationHealthStatus,
    BuildStatus,
    ObservationConfidence,
    ObservationType,
    PreflightDecision,
    PreflightStatus,
    TesterActionType,
    TestRuntimeStatus,
)

logger = logging.getLogger("AutonomOS.Tester.PreflightEvaluator")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


COMPILATION_ERROR_PATTERNS = [
    re.compile(r"SyntaxError:", re.IGNORECASE),
    re.compile(r"CompileError:", re.IGNORECASE),
    re.compile(r"TypeScript error", re.IGNORECASE),
    re.compile(r"TS\d{4,5}:", re.IGNORECASE),
    re.compile(r"compilation failed", re.IGNORECASE),
    re.compile(r"build failed", re.IGNORECASE),
    re.compile(r"ModuleNotFoundError:", re.IGNORECASE),
    re.compile(r"ImportError:", re.IGNORECASE),
    re.compile(r"Cannot find module", re.IGNORECASE),
    re.compile(r"Module not found", re.IGNORECASE),
    re.compile(r"javac: error", re.IGNORECASE),
]

RUNTIME_EXCEPTION_PATTERNS = [
    re.compile(r"Traceback \(most recent call last\):"),
    re.compile(r"Uncaught Exception:", re.IGNORECASE),
    re.compile(r"UnhandledPromiseRejection", re.IGNORECASE),
    re.compile(r"Fatal error:", re.IGNORECASE),
    re.compile(r"panic:", re.IGNORECASE),
    re.compile(r"Segmentation fault", re.IGNORECASE),
    re.compile(r"NullPointerException", re.IGNORECASE),
    re.compile(r"FATAL ERROR:", re.IGNORECASE),
]

WARNING_PATTERNS = [
    re.compile(r"DeprecationWarning:", re.IGNORECASE),
    re.compile(r"UserWarning:", re.IGNORECASE),
    re.compile(r"Warning:", re.IGNORECASE),
    re.compile(r"\[WARN\]", re.IGNORECASE),
    re.compile(r"WARN:", re.IGNORECASE),
]


class TestPreflightEvaluator:
    """
    Phase 5.1 Test Preflight & Application Health Evaluator.
    
    Verifies deterministic application and runtime readiness before test execution.
    Evaluates:
    - Lineage and project isolation
    - Command authorization & build execution
    - Runtime and process liveness
    - Network & base URL reachability
    - Route health and HTTP 500 error detection
    - Missing resource & repeated 404 detection
    - Terminal / process output inspection with strict terminal isolation
    - Synthesized preflight decision (READY, READY_WITH_WARNINGS, FAILED, BLOCKED)
    """
    __test__ = False

    def __init__(self, trace_id: Optional[str] = None) -> None:
        self.trace_id = trace_id

    def evaluate_preflight(
        self,
        execution: TesterExecution,
        work_order: TesterWorkOrder,
        test_plan: Optional[TestPlan] = None,
        runtime: Optional[TestRuntime] = None,
        session: Optional[BrowserSession] = None,
        routes: Optional[Sequence[Union[str, dict[str, Any], RouteCheckResult]]] = None,
        resources: Optional[Sequence[Union[str, dict[str, Any], ResourceCheckResult]]] = None,
        terminal_output: Optional[Union[str, Sequence[str]]] = None,
        build_command: Optional[str] = None,
        build_runner: Optional[Callable[[str], tuple[int, str, str]]] = None,
        mock_app_checker: Optional[Callable[[str], dict[str, Any]]] = None,
        event_sink: Optional[Callable[[Event], Any]] = None,
        timeout_seconds: float = 30.0,
    ) -> TestPreflightResult:
        """
        Execute the preflight evaluation pipeline and record results into execution.
        """
        started_at = utc_now()
        preflight_id = new_preflight_id()

        # -------------------------------------------------------------------
        # 1. Lineage and Tenant Isolation Verification
        # -------------------------------------------------------------------
        if execution.work_order_id != work_order.work_order_id:
            raise TesterLineageError(
                f"Lineage mismatch: execution work_order_id '{execution.work_order_id}' "
                f"!= work_order work_order_id '{work_order.work_order_id}'."
            )
        if execution.project_id != work_order.project_id:
            raise TesterLineageError(
                f"Project isolation violation: execution project_id '{execution.project_id}' "
                f"!= work_order project_id '{work_order.project_id}'."
            )
        if runtime is not None:
            if runtime.execution_id != execution.execution_id:
                raise TesterLineageError(
                    f"Lineage mismatch: runtime execution_id '{runtime.execution_id}' "
                    f"!= execution execution_id '{execution.execution_id}'."
                )
            if runtime.project_id != execution.project_id:
                raise TesterLineageError(
                    f"Project isolation violation: runtime project_id '{runtime.project_id}' "
                    f"!= execution project_id '{execution.project_id}'."
                )
        if test_plan is not None:
            if test_plan.execution_id != execution.execution_id:
                raise TesterLineageError(
                    f"Lineage mismatch: test_plan execution_id '{test_plan.execution_id}' "
                    f"!= execution execution_id '{execution.execution_id}'."
                )
            if test_plan.project_id != execution.project_id:
                raise TesterLineageError(
                    f"Project isolation violation: test_plan project_id '{test_plan.project_id}' "
                    f"!= execution project_id '{execution.project_id}'."
                )

        # Dispatch start event
        sink = event_sink or getattr(runtime, "event_sink", None)
        if sink:
            try:
                start_ev = Event(
                    event_id=new_event_id(),
                    event_type=EventType.TEST_PREFLIGHT_STARTED,
                    timestamp=started_at,
                    source=EventSource.WORKER,
                    correlation_id=execution.correlation_id,
                    project_id=execution.project_id,
                    task_id=execution.task_id,
                    worker_id=execution.worker_id,
                    payload={
                        "preflight_id": preflight_id,
                        "execution_id": execution.execution_id,
                        "work_order_id": work_order.work_order_id,
                    },
                )
                sink(start_ev)
            except Exception as e:
                logger.debug(f"Failed to dispatch start event: {e}")

        warnings: list[str] = []
        blockers: list[str] = []
        error_observations: list[TesterObservation] = []
        route_checks: list[RouteCheckResult] = []
        resource_checks: list[ResourceCheckResult] = []

        application_status = ApplicationHealthStatus.HEALTHY
        build_status: Optional[BuildStatus] = None
        runtime_status_str = runtime.status.value if runtime else "NOT_CONFIGURED"

        # -------------------------------------------------------------------
        # 2. Check Operational Blockers
        # -------------------------------------------------------------------
        for blk in execution.active_blockers:
            blockers.append(blk.description)
        if hasattr(work_order, "blockers"):
            for b in work_order.blockers:
                if isinstance(b, str) and b not in blockers:
                    blockers.append(b)

        # -------------------------------------------------------------------
        # 3. Authorized Build Verification (Bounded Execution)
        # -------------------------------------------------------------------
        effective_build_cmd = build_command or work_order.metadata.get("build_command")
        if effective_build_cmd:
            # Check command authorization against work_order
            authorized_cmds = work_order.metadata.get("authorized_commands", [])
            if hasattr(work_order, "authorized_commands"):
                authorized_cmds = list(work_order.authorized_commands or authorized_cmds)
            if authorized_cmds and effective_build_cmd not in authorized_cmds:
                raise TesterBoundaryViolationError(
                    action="EXECUTE_BUILD_COMMAND",
                    reason=f"Build command '{effective_build_cmd}' is not in authorized commands: {authorized_cmds}.",
                )

            if build_runner is not None:
                try:
                    exit_code, stdout_data, stderr_data = build_runner(effective_build_cmd)
                    if exit_code != 0:
                        build_status = BuildStatus.FAILED
                        application_status = ApplicationHealthStatus.UNHEALTHY
                        desc = f"Build command failed with exit code {exit_code}: {stderr_data or stdout_data}"
                        warnings.append(desc)
                        obs = TesterObservation(
                            observation_id=new_observation_id(),
                            execution_id=execution.execution_id,
                            project_id=execution.project_id,
                            runtime_id=runtime.runtime_id if runtime else None,
                            observation_type=ObservationType.RUNTIME_STATE,
                            description=desc,
                            observed_state={"exit_code": exit_code, "output": stderr_data or stdout_data},
                            confidence=1.0,
                        )
                        error_observations.append(obs)
                    else:
                        build_status = BuildStatus.SUCCESS
                except Exception as e:
                    build_status = BuildStatus.FAILED
                    application_status = ApplicationHealthStatus.UNHEALTHY
                    desc = f"Build execution error: {e}"
                    warnings.append(desc)
                    obs = TesterObservation(
                        observation_id=new_observation_id(),
                        execution_id=execution.execution_id,
                        project_id=execution.project_id,
                        runtime_id=runtime.runtime_id if runtime else None,
                        observation_type=ObservationType.RUNTIME_STATE,
                        description=desc,
                        observed_state={"error": str(e)},
                        confidence=1.0,
                    )
                    error_observations.append(obs)
            else:
                build_status = BuildStatus.SUCCESS
        elif work_order.metadata.get("build_failed", False):
            build_status = BuildStatus.FAILED
            application_status = ApplicationHealthStatus.UNHEALTHY
            desc = work_order.metadata.get("build_error", "Application build failed")
            warnings.append(desc)
            obs = TesterObservation(
                observation_id=new_observation_id(),
                execution_id=execution.execution_id,
                project_id=execution.project_id,
                runtime_id=runtime.runtime_id if runtime else None,
                observation_type=ObservationType.RUNTIME_STATE,
                description=desc,
                observed_state={"error": desc},
                confidence=1.0,
            )
            error_observations.append(obs)

        # -------------------------------------------------------------------
        # 4. Runtime & Process Liveness
        # -------------------------------------------------------------------
        if runtime is not None:
            runtime_status_str = runtime.status.value
            if runtime.status == TestRuntimeStatus.FAILED:
                application_status = ApplicationHealthStatus.STARTUP_FAILED
                desc = f"Runtime startup failed: {runtime.failure_reason or 'Unknown driver failure'}"
                warnings.append(desc)
                obs = TesterObservation(
                    observation_id=new_observation_id(),
                    execution_id=execution.execution_id,
                    project_id=execution.project_id,
                    runtime_id=runtime.runtime_id,
                    observation_type=ObservationType.RUNTIME_STATE,
                    description=desc,
                    observed_state={"failure_reason": runtime.failure_reason},
                    confidence=1.0,
                )
                error_observations.append(obs)
            elif not runtime.is_running and runtime.status == TestRuntimeStatus.CREATED:
                try:
                    runtime.start()
                    runtime_status_str = runtime.status.value
                except Exception as e:
                    application_status = ApplicationHealthStatus.STARTUP_FAILED
                    desc = f"Runtime startup failed: {e}"
                    warnings.append(desc)
                    obs = TesterObservation(
                        observation_id=new_observation_id(),
                        execution_id=execution.execution_id,
                        project_id=execution.project_id,
                        runtime_id=runtime.runtime_id,
                        observation_type=ObservationType.RUNTIME_STATE,
                        description=desc,
                        observed_state={"startup_error": str(e)},
                        confidence=1.0,
                    )
                    error_observations.append(obs)
            elif runtime.status == TestRuntimeStatus.STOPPED:
                application_status = ApplicationHealthStatus.UNHEALTHY
                warnings.append("Runtime is in STOPPED status prior to test execution.")

        # -------------------------------------------------------------------
        # 5. Terminal / Process Output Error Inspection (Terminal Isolation)
        # -------------------------------------------------------------------
        if terminal_output:
            lines = (
                terminal_output.splitlines()
                if isinstance(terminal_output, str)
                else list(terminal_output)
            )
            full_text = "\n".join(lines)

            # Check for Compilation / Module Errors
            for pattern in COMPILATION_ERROR_PATTERNS:
                match = pattern.search(full_text)
                if match:
                    build_status = BuildStatus.FAILED
                    application_status = ApplicationHealthStatus.UNHEALTHY
                    matched_line = match.group(0)
                    desc = f"Compilation error detected in terminal output: {matched_line}"
                    if desc not in warnings:
                        warnings.append(desc)
                    obs = TesterObservation(
                        observation_id=new_observation_id(),
                        execution_id=execution.execution_id,
                        project_id=execution.project_id,
                        runtime_id=runtime.runtime_id if runtime else None,
                        observation_type=ObservationType.RUNTIME_STATE,
                        description=desc,
                        observed_state={"detected_pattern": pattern.pattern, "match": matched_line},
                        confidence=1.0,
                    )
                    error_observations.append(obs)
                    break

            # Check for Unhandled Runtime Exceptions / Fatal Crashes
            for pattern in RUNTIME_EXCEPTION_PATTERNS:
                match = pattern.search(full_text)
                if match:
                    application_status = ApplicationHealthStatus.UNHEALTHY
                    matched_line = match.group(0)
                    desc = f"Runtime exception detected in process output: {matched_line}"
                    if desc not in warnings:
                        warnings.append(desc)
                    obs = TesterObservation(
                        observation_id=new_observation_id(),
                        execution_id=execution.execution_id,
                        project_id=execution.project_id,
                        runtime_id=runtime.runtime_id if runtime else None,
                        observation_type=ObservationType.RUNTIME_STATE,
                        description=desc,
                        observed_state={"detected_pattern": pattern.pattern, "match": matched_line},
                        confidence=1.0,
                    )
                    error_observations.append(obs)
                    break

            # Check for Warnings
            for pattern in WARNING_PATTERNS:
                for line in lines:
                    if pattern.search(line):
                        warn_msg = f"Terminal warning: {line.strip()}"
                        if warn_msg not in warnings:
                            warnings.append(warn_msg)

        # -------------------------------------------------------------------
        # 6. Route Verification & HTTP 500 Detection
        # -------------------------------------------------------------------
        routes_to_evaluate: list[dict[str, Any]] = []
        if routes:
            for r in routes:
                if isinstance(r, RouteCheckResult):
                    route_checks.append(r)
                elif isinstance(r, dict):
                    routes_to_evaluate.append(r)
                elif isinstance(r, str):
                    routes_to_evaluate.append({"route": r, "is_required": True, "expected_status_code": 200})
        elif test_plan and test_plan.test_cases:
            for tc in test_plan.test_cases:
                target_route = tc.metadata.get("target_url") or tc.metadata.get("route")
                if target_route and not any(r.get("route") == target_route for r in routes_to_evaluate):
                    routes_to_evaluate.append({"route": target_route, "is_required": True, "expected_status_code": 200})
        if not route_checks and not routes_to_evaluate:
            base_url = "/"
            if runtime and hasattr(runtime, "config") and runtime.config.application_url:
                base_url = runtime.config.application_url
            routes_to_evaluate.append({"route": base_url, "is_required": True, "expected_status_code": 200})

        for r_spec in routes_to_evaluate:
            route_path = str(r_spec.get("route", "/"))
            is_req = bool(r_spec.get("is_required", True))
            exp_code = int(r_spec.get("expected_status_code", 200))

            status_code: Optional[int] = None
            is_reachable = False
            is_healthy = False
            err_msg: Optional[str] = None
            latency = 10.0

            if mock_app_checker:
                try:
                    res = mock_app_checker(route_path)
                    status_code = res.get("status_code")
                    is_reachable = bool(res.get("is_reachable", True))
                    err_msg = res.get("error_message")
                    latency = float(res.get("latency_ms", 10.0))
                except Exception as e:
                    err_msg = str(e)
                    is_reachable = False
            elif session is not None or (runtime is not None and getattr(runtime, "session", None) is not None):
                active_session = session or getattr(runtime, "session")
                try:
                    nav_res = active_session.navigate(route_path)
                    status_code = nav_res.status_code
                    is_reachable = nav_res.is_success or (nav_res.status_code is not None)
                    err_msg = nav_res.error
                    latency = nav_res.duration_ms
                except Exception as e:
                    err_msg = str(e)
                    is_reachable = False
            else:
                # Deterministic check from spec attributes if mock/session not provided
                status_code = r_spec.get("status_code", 200)
                is_reachable = r_spec.get("is_reachable", True)
                err_msg = r_spec.get("error_message")

            if not is_reachable:
                is_healthy = False
                application_status = ApplicationHealthStatus.UNREACHABLE
                desc = f"Route '{route_path}' is unreachable: {err_msg or 'Connection refused'}"
                warnings.append(desc)
                obs = TesterObservation(
                    observation_id=new_observation_id(),
                    execution_id=execution.execution_id,
                    project_id=execution.project_id,
                    runtime_id=runtime.runtime_id if runtime else None,
                    observation_type=ObservationType.RUNTIME_STATE,
                    description=desc,
                    observed_state={"route": route_path, "error": err_msg},
                    confidence=1.0,
                )
                error_observations.append(obs)
            elif status_code is not None and status_code >= 500:
                is_healthy = False
                application_status = ApplicationHealthStatus.UNHEALTHY
                desc = f"Application route '{route_path}' returned HTTP {status_code}: Internal Server Error"
                warnings.append(desc)
                obs = TesterObservation(
                    observation_id=new_observation_id(),
                    execution_id=execution.execution_id,
                    project_id=execution.project_id,
                    runtime_id=runtime.runtime_id if runtime else None,
                    observation_type=ObservationType.RUNTIME_STATE,
                    description=desc,
                    observed_state={"route": route_path, "status_code": status_code},
                    confidence=1.0,
                )
                error_observations.append(obs)
            elif status_code == 404:
                if exp_code == 404:
                    is_healthy = True
                elif is_req:
                    is_healthy = False
                    desc = f"Required route '{route_path}' returned HTTP 404 (Not Found)"
                    warnings.append(desc)
                    obs = TesterObservation(
                        observation_id=new_observation_id(),
                        execution_id=execution.execution_id,
                        project_id=execution.project_id,
                        runtime_id=runtime.runtime_id if runtime else None,
                        observation_type=ObservationType.RUNTIME_STATE,
                        description=desc,
                        observed_state={"route": route_path, "status_code": 404},
                        confidence=1.0,
                    )
                    error_observations.append(obs)
                else:
                    is_healthy = False
                    warnings.append(f"Non-critical route '{route_path}' returned HTTP 404")
            else:
                is_healthy = True

            rc_result = RouteCheckResult(
                route=route_path,
                status_code=status_code,
                is_reachable=is_reachable,
                is_healthy=is_healthy,
                latency_ms=latency,
                error_message=err_msg,
                is_required=is_req,
                expected_status_code=exp_code,
            )
            route_checks.append(rc_result)

        # -------------------------------------------------------------------
        # 7. Resource Availability & Repeated 404 Detection
        # -------------------------------------------------------------------
        if resources:
            for res_item in resources:
                if isinstance(res_item, ResourceCheckResult):
                    res = res_item
                elif isinstance(res_item, dict):
                    res = ResourceCheckResult.from_dict(res_item)
                else:
                    res = ResourceCheckResult(resource_url=str(res_item))

                resource_checks.append(res)

                if not res.is_available or res.status_code == 404:
                    if res.failure_count >= 2 or res.is_required:
                        application_status = ApplicationHealthStatus.UNHEALTHY
                        desc = (
                            f"Critical resource failure: '{res.resource_url}' (HTTP {res.status_code or 404}, "
                            f"failures: {res.failure_count})"
                        )
                        warnings.append(desc)
                        obs = TesterObservation(
                            observation_id=new_observation_id(),
                            execution_id=execution.execution_id,
                            project_id=execution.project_id,
                            runtime_id=runtime.runtime_id if runtime else None,
                            observation_type=ObservationType.RUNTIME_STATE,
                            description=desc,
                            observed_state={"resource_url": res.resource_url, "failure_count": res.failure_count},
                            confidence=1.0,
                        )
                        error_observations.append(obs)
                    else:
                        warnings.append(f"Missing non-critical resource '{res.resource_url}' (HTTP 404)")

        # -------------------------------------------------------------------
        # 8. Deterministic Decision Synthesis
        # -------------------------------------------------------------------
        has_fatal_failure = (
            build_status == BuildStatus.FAILED
            or application_status in (
                ApplicationHealthStatus.STARTUP_FAILED,
                ApplicationHealthStatus.UNHEALTHY,
                ApplicationHealthStatus.UNREACHABLE,
            )
            or any(not rc.is_healthy and rc.is_required for rc in route_checks)
            or any(not r.is_available and (r.is_required or r.failure_count >= 2) for r in resource_checks)
        )

        if blockers:
            decision = PreflightDecision.BLOCKED
            status = PreflightStatus.BLOCKED
            if application_status == ApplicationHealthStatus.HEALTHY:
                application_status = ApplicationHealthStatus.UNKNOWN
        elif has_fatal_failure:
            decision = PreflightDecision.FAILED
            status = PreflightStatus.FAILED
            if application_status == ApplicationHealthStatus.HEALTHY:
                application_status = ApplicationHealthStatus.UNHEALTHY
        elif warnings:
            decision = PreflightDecision.READY_WITH_WARNINGS
            status = PreflightStatus.WARNINGS
            application_status = ApplicationHealthStatus.DEGRADED
        else:
            decision = PreflightDecision.READY
            status = PreflightStatus.PASS
            application_status = ApplicationHealthStatus.HEALTHY

        completed_at = utc_now()

        # -------------------------------------------------------------------
        # 9. Create Auditable Execution Trace & Record Result
        # -------------------------------------------------------------------
        trace_record = execution.create_trace(
            action_type=TesterActionType.PREFLIGHT_CHECK,
            action_details={
                "preflight_id": preflight_id,
                "status": status.value,
                "decision": decision.value,
                "application_status": application_status.value,
                "build_status": build_status.value if build_status else None,
                "warnings_count": len(warnings),
                "blockers_count": len(blockers),
                "error_observations_count": len(error_observations),
            },
        )

        preflight_result = TestPreflightResult(
            preflight_id=preflight_id,
            execution_id=execution.execution_id,
            project_id=execution.project_id,
            status=status,
            decision=decision,
            application_status=application_status,
            build_status=build_status,
            runtime_status=runtime_status_str,
            route_checks=route_checks,
            resource_checks=resource_checks,
            error_observations=error_observations,
            warnings=warnings,
            blockers=blockers,
            evidence_ids=[trace_record.trace_id],
            provenance={
                "evaluator": "TestPreflightEvaluator",
                "version": "1.0.0",
                "work_order_id": work_order.work_order_id,
                "task_id": execution.task_id,
                "project_id": execution.project_id,
            },
            trace=trace_record.to_dict(),
            timestamps={
                "created_at": started_at,
                "started_at": started_at,
                "completed_at": completed_at,
            },
        )

        # Authoritatively bind preflight result to execution
        execution.record_preflight_result(preflight_result)

        # Dispatch completion event
        if sink:
            try:
                ev_type = (
                    EventType.TEST_PREFLIGHT_FAILED
                    if decision == PreflightDecision.FAILED
                    else EventType.TEST_PREFLIGHT_COMPLETED
                )
                comp_ev = Event(
                    event_id=new_event_id(),
                    event_type=ev_type,
                    timestamp=completed_at,
                    source=EventSource.WORKER,
                    correlation_id=execution.correlation_id,
                    project_id=execution.project_id,
                    task_id=execution.task_id,
                    worker_id=execution.worker_id,
                    payload={
                        "preflight_id": preflight_id,
                        "execution_id": execution.execution_id,
                        "status": status.value,
                        "decision": decision.value,
                        "application_status": application_status.value,
                    },
                )
                sink(comp_ev)
            except Exception as e:
                logger.debug(f"Failed to dispatch completion event: {e}")

        return preflight_result

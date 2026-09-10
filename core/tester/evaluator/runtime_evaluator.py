from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import re
from typing import Any, Callable, Optional, Sequence, Union

from core.events.model import Event, new_event_id
from core.events.types import EventSource, EventType
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.identifiers import (
    new_runtime_event_id,
    new_trace_id,
    validate_execution_id,
)
from core.tester.contracts.observation import TesterObservation
from core.tester.contracts.runtime_observation import RuntimeObservation
from core.tester.contracts.trace import TesterTrace
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    ObservationType,
    RuntimeEventSeverity,
    RuntimeEventType,
    TesterActionType,
)

logger = logging.getLogger("AutonomOS.Tester.RuntimeEvaluator")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Patterns identifying third-party noise, browser extensions, or ambient host logs
DEFAULT_EXTERNAL_NOISE_PATTERNS = [
    r"net::err_blocked_by_client",
    r"google-analytics\.com",
    r"googletagmanager\.com",
    r"doubleclick\.net",
    r"chrome-extension://",
    r"moz-extension://",
    r"safari-extension://",
    r"\[deprecation\]",
    r"devtools listening on",
    r"adblock",
    r"optimizely",
    r"segment\.io",
    r"hotjar",
]


class RuntimeEvaluator:
    """
    Phase 5.3: Runtime & Request Evaluator.
    
    Performs deterministic evaluation of runtime behavior, network requests,
    console output, and process liveness observed during testing.
    
    Invariants & Execution Rules:
    1. Observation Boundary:
       Runtime events are purely observations; does NOT create TesterDefect objects directly.
    2. Contextual Severity:
       Technical seriousness (INFO, WARNING, ERROR, CRITICAL) is determined based on context:
       - Required vs optional resource 404s
       - Server 500s vs benign 200s
       - Application-owned crashes vs irrelevant ambient host errors
    3. Noise Filtering:
       Filters out host OS telemetry, ad blocker hits, and browser extension errors.
    4. Causal Lineage & Project Isolation:
       Enforces execution_id and project_id binding.
    5. Determinism:
       Identical sequence of runtime events yields identical sequence of evaluated observations.
    """
    __test__ = False

    def __init__(
        self,
        execution: Optional[TesterExecution] = None,
        work_order: Optional[TesterWorkOrder] = None,
        project_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        event_sink: Optional[Callable[[Event], Any]] = None,
        external_noise_patterns: Optional[Sequence[str]] = None,
    ) -> None:
        self.execution = execution
        self.work_order = work_order
        self.execution_id = (
            execution.execution_id
            if execution is not None
            else (execution_id or "")
        )
        self.project_id = (
            execution.project_id
            if execution is not None
            else (work_order.project_id if work_order is not None else (project_id or ""))
        )
        self.event_sink = event_sink

        # Compile noise patterns
        patterns = list(external_noise_patterns or DEFAULT_EXTERNAL_NOISE_PATTERNS)
        self._noise_regexes = [re.compile(p, re.IGNORECASE) for p in patterns]

        self.recorded_events: list[RuntimeObservation] = []

    def _validate_lineage(
        self,
        execution_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> None:
        """Enforce execution and project isolation."""
        if execution_id and self.execution_id and execution_id != self.execution_id:
            raise TesterLineageError(
                f"Execution lineage mismatch: event execution_id '{execution_id}' != evaluator '{self.execution_id}'."
            )
        if project_id and self.project_id and project_id != self.project_id:
            raise TesterLineageError(
                f"Project isolation violation: event project_id '{project_id}' != evaluator '{self.project_id}'."
            )

    def is_external_noise(self, text: str) -> bool:
        """Check if message matches known third-party or host noise patterns."""
        if not text:
            return False
        clean = text.strip()
        for regex in self._noise_regexes:
            if regex.search(clean):
                return True
        return False

    def evaluate_http(
        self,
        url: str,
        method: str = "GET",
        status_code: Optional[int] = 200,
        timing_ms: Optional[float] = None,
        is_required: bool = True,
        error_message: Optional[str] = None,
        test_case_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        project_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        evidence_ids: Optional[list[str]] = None,
        is_api: Optional[bool] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> RuntimeObservation:
        """
        Deterministically evaluate an HTTP request and response.
        Distinguishes required from optional resource failures and identifies API errors.
        """
        eff_exec_id = execution_id or self.execution_id
        eff_proj_id = project_id or self.project_id
        self._validate_lineage(eff_exec_id, eff_proj_id)

        # Check if URL represents an API endpoint
        url_lower = (url or "").lower()
        method_upper = (method or "GET").upper()
        api_detected = is_api if is_api is not None else (
            "/api/" in url_lower
            or url_lower.endswith(".json")
            or method_upper in ("POST", "PUT", "PATCH", "DELETE")
        )

        event_type = RuntimeEventType.HTTP_RESPONSE
        severity = RuntimeEventSeverity.INFO
        msg = f"HTTP {status_code or 0} {method_upper} {url}"

        # 1. Connection or Network Failure
        if status_code is None or status_code == 0:
            if api_detected:
                event_type = RuntimeEventType.API_FAILURE
                severity = RuntimeEventSeverity.CRITICAL if is_required else RuntimeEventSeverity.ERROR
                msg = f"API request failed: {method_upper} {url} ({error_message or 'No response'})"
            else:
                event_type = RuntimeEventType.RESOURCE_FAILURE if is_required else RuntimeEventType.HTTP_RESPONSE
                severity = RuntimeEventSeverity.ERROR if is_required else RuntimeEventSeverity.WARNING
                msg = f"Network request failed: {method_upper} {url} ({error_message or 'Connection failed'})"

        # 2. Success Status Codes (2xx, 3xx)
        elif 200 <= status_code < 400:
            event_type = RuntimeEventType.HTTP_RESPONSE
            severity = RuntimeEventSeverity.INFO
            msg = f"HTTP {status_code} {method_upper} {url}"

        # 3. Not Found (404)
        elif status_code == 404:
            if is_required:
                event_type = RuntimeEventType.RESOURCE_FAILURE
                severity = RuntimeEventSeverity.ERROR
                msg = f"Required resource not found (HTTP 404): {url}"
            else:
                event_type = RuntimeEventType.HTTP_RESPONSE
                severity = RuntimeEventSeverity.WARNING
                msg = f"Optional resource not found (HTTP 404): {url}"

        # 4. Client Error (4xx except 404)
        elif 400 <= status_code < 500:
            if api_detected:
                event_type = RuntimeEventType.API_FAILURE
                severity = RuntimeEventSeverity.ERROR
                msg = f"API client error (HTTP {status_code}): {method_upper} {url}"
            else:
                event_type = RuntimeEventType.HTTP_RESPONSE
                severity = RuntimeEventSeverity.WARNING
                msg = f"Client request error (HTTP {status_code}): {method_upper} {url}"

        # 5. Server Error (5xx)
        elif 500 <= status_code < 600:
            if api_detected:
                event_type = RuntimeEventType.API_FAILURE
                severity = RuntimeEventSeverity.CRITICAL if is_required else RuntimeEventSeverity.ERROR
                msg = f"API server error (HTTP {status_code}): {method_upper} {url}"
            else:
                event_type = RuntimeEventType.HTTP_RESPONSE
                severity = RuntimeEventSeverity.CRITICAL if is_required else RuntimeEventSeverity.ERROR
                msg = f"Server error (HTTP {status_code}): {method_upper} {url}"

        # Build RuntimeObservation
        obs = self._create_observation(
            event_type=event_type,
            message=msg,
            severity=severity,
            url=url,
            method=method_upper,
            status_code=status_code,
            source="NETWORK",
            test_case_id=test_case_id,
            execution_id=eff_exec_id,
            project_id=eff_proj_id,
            duration_ms=timing_ms,
            is_required_resource=is_required,
            evidence_ids=evidence_ids,
            trace_id=trace_id,
            metadata=metadata,
        )
        return obs

    def evaluate_console(
        self,
        message: str,
        level: str = "error",
        source: str = "console",
        test_case_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        project_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        evidence_ids: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> RuntimeObservation:
        """
        Evaluate console output from browser or application.
        Filters out irrelevant third-party noise, tracker blocks, and extension errors.
        """
        eff_exec_id = execution_id or self.execution_id
        eff_proj_id = project_id or self.project_id
        self._validate_lineage(eff_exec_id, eff_proj_id)

        is_noise = self.is_external_noise(message)
        level_clean = (level or "error").lower()

        if is_noise:
            # Filtered external noise: marked not application-owned with INFO severity
            event_type = (
                RuntimeEventType.CONSOLE_WARNING
                if level_clean in ("warning", "warn")
                else RuntimeEventType.CONSOLE_ERROR
            )
            severity = RuntimeEventSeverity.INFO
            is_app_owned = False
            annotated_msg = f"[External Noise Filtered] {message}"
        else:
            # Application-owned error / warning
            is_app_owned = True
            if level_clean in ("warning", "warn"):
                event_type = RuntimeEventType.CONSOLE_WARNING
                severity = RuntimeEventSeverity.WARNING
            else:
                event_type = RuntimeEventType.CONSOLE_ERROR
                severity = RuntimeEventSeverity.ERROR
            annotated_msg = message

        obs = self._create_observation(
            event_type=event_type,
            message=annotated_msg,
            severity=severity,
            source=source or "CONSOLE",
            test_case_id=test_case_id,
            execution_id=eff_exec_id,
            project_id=eff_proj_id,
            is_application_owned=is_app_owned,
            evidence_ids=evidence_ids,
            trace_id=trace_id,
            metadata=metadata,
        )
        return obs

    def evaluate_process(
        self,
        message: str,
        exit_code: Optional[int] = None,
        signal: Optional[str] = None,
        is_crash: bool = False,
        test_case_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        project_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        evidence_ids: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> RuntimeObservation:
        """
        Evaluate process runtime behavior.
        Distinguishes application crashes (CRITICAL) from uncaught exceptions (ERROR).
        """
        eff_exec_id = execution_id or self.execution_id
        eff_proj_id = project_id or self.project_id
        self._validate_lineage(eff_exec_id, eff_proj_id)

        fatal_signals = {"SIGSEGV", "SIGABRT", "SIGKILL", "SIGTERM", "SIGBUS", "SIGFPE"}
        signal_upper = str(signal or "").upper()
        crash_detected = is_crash or (exit_code is not None and exit_code != 0) or (signal_upper in fatal_signals)

        if crash_detected:
            event_type = RuntimeEventType.APPLICATION_CRASH
            severity = RuntimeEventSeverity.CRITICAL
            msg = f"Application crash detected: {message}"
            if exit_code is not None:
                msg += f" (exit code {exit_code})"
            if signal:
                msg += f" (signal {signal})"
        else:
            event_type = RuntimeEventType.PROCESS_ERROR
            severity = RuntimeEventSeverity.ERROR
            msg = f"Runtime process error: {message}"

        meta = dict(metadata or {})
        if exit_code is not None:
            meta["exit_code"] = exit_code
        if signal:
            meta["signal"] = signal

        obs = self._create_observation(
            event_type=event_type,
            message=msg,
            severity=severity,
            source="PROCESS",
            test_case_id=test_case_id,
            execution_id=eff_exec_id,
            project_id=eff_proj_id,
            evidence_ids=evidence_ids,
            trace_id=trace_id,
            metadata=meta,
        )
        return obs

    def evaluate_navigation(
        self,
        url: str,
        error_message: str,
        status_code: Optional[int] = None,
        test_case_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        project_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        evidence_ids: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> RuntimeObservation:
        """
        Evaluate navigation failure (DNS, unreachable host, timeout, connection abort).
        """
        eff_exec_id = execution_id or self.execution_id
        eff_proj_id = project_id or self.project_id
        self._validate_lineage(eff_exec_id, eff_proj_id)

        msg = f"Navigation failed to '{url}': {error_message}"
        if status_code is not None:
            msg += f" (HTTP {status_code})"

        obs = self._create_observation(
            event_type=RuntimeEventType.NAVIGATION_FAILURE,
            message=msg,
            severity=RuntimeEventSeverity.ERROR,
            url=url,
            status_code=status_code,
            source="NAVIGATION",
            test_case_id=test_case_id,
            execution_id=eff_exec_id,
            project_id=eff_proj_id,
            evidence_ids=evidence_ids,
            trace_id=trace_id,
            metadata=metadata,
        )
        return obs

    def evaluate_api(
        self,
        endpoint: str,
        method: str = "GET",
        status_code: Optional[int] = None,
        error_message: Optional[str] = None,
        duration_ms: Optional[float] = None,
        is_required: bool = True,
        test_case_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        project_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        evidence_ids: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> RuntimeObservation:
        """
        Explicitly evaluate an API interaction.
        """
        return self.evaluate_http(
            url=endpoint,
            method=method,
            status_code=status_code,
            timing_ms=duration_ms,
            is_required=is_required,
            error_message=error_message,
            test_case_id=test_case_id,
            execution_id=execution_id,
            project_id=project_id,
            trace_id=trace_id,
            evidence_ids=evidence_ids,
            is_api=True,
            metadata=metadata,
        )

    def _create_observation(
        self,
        event_type: RuntimeEventType,
        message: str,
        severity: RuntimeEventSeverity,
        url: Optional[str] = None,
        method: Optional[str] = None,
        status_code: Optional[int] = None,
        source: str = "",
        test_case_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        project_id: Optional[str] = None,
        is_application_owned: bool = True,
        duration_ms: Optional[float] = None,
        is_required_resource: Optional[bool] = None,
        evidence_ids: Optional[list[str]] = None,
        trace_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> RuntimeObservation:
        """Construct and record a RuntimeObservation instance."""
        eid = new_runtime_event_id()
        eff_exec = execution_id or self.execution_id
        eff_proj = project_id or self.project_id
        tid = trace_id or (self.execution.create_trace(
            action_type=TesterActionType.EVALUATE_RUNTIME,
            action_details={"event_id": eid, "event_type": event_type.value, "severity": severity.value},
        ).trace_id if self.execution is not None else new_trace_id())

        obs = RuntimeObservation(
            event_id=eid,
            execution_id=eff_exec,
            project_id=eff_proj,
            event_type=event_type,
            message=message,
            test_case_id=test_case_id,
            url=url,
            method=method,
            status_code=status_code,
            source=source,
            severity=severity,
            evidence_ids=list(evidence_ids or []),
            provenance={
                "execution_id": eff_exec,
                "project_id": eff_proj,
                "work_order_id": getattr(self.work_order, "work_order_id", None),
                "test_case_id": test_case_id,
            },
            trace={"trace_id": tid, "action_type": TesterActionType.EVALUATE_RUNTIME.value},
            metadata=dict(metadata or {}),
            is_application_owned=is_application_owned,
            duration_ms=duration_ms,
            is_required_resource=is_required_resource,
        )

        self.recorded_events.append(obs)

        # Emit domain event if sink available
        if self.event_sink is not None:
            evt = Event(
                event_id=new_event_id(),
                event_type=EventType.TEST_RUNTIME_EVENT_OBSERVED,
                timestamp=utc_now(),
                source=EventSource.WORKER,
                project_id=eff_proj,
                correlation_id=getattr(self.execution, "correlation_id", ""),
                payload=obs.to_dict(),
            )
            self.event_sink(evt)

        # Auto-record on execution if attached
        if self.execution is not None:
            if hasattr(self.execution, "record_runtime_observation"):
                self.execution.record_runtime_observation(obs)
            else:
                self.execution.record_observation(obs.to_observation())

        return obs

    def get_observations(
        self,
        severity: Optional[Union[RuntimeEventSeverity, str]] = None,
        event_type: Optional[Union[RuntimeEventType, str]] = None,
        test_case_id: Optional[str] = None,
        application_owned_only: bool = False,
    ) -> list[RuntimeObservation]:
        """Query recorded runtime observations with optional filters."""
        res = list(self.recorded_events)
        if severity is not None:
            target_sev = severity.value if hasattr(severity, "value") else str(severity).upper()
            res = [o for o in res if o.severity.value == target_sev]
        if event_type is not None:
            target_type = event_type.value if hasattr(event_type, "value") else str(event_type).upper()
            res = [o for o in res if o.event_type.value == target_type]
        if test_case_id is not None:
            res = [o for o in res if o.test_case_id == test_case_id]
        if application_owned_only:
            res = [o for o in res if o.is_application_owned]
        return res

    @property
    def failures(self) -> list[RuntimeObservation]:
        """Return all actionable application failure events (ERROR and CRITICAL)."""
        return [o for o in self.recorded_events if o.is_failure and o.is_application_owned]

    def to_observations(self) -> list[TesterObservation]:
        """Convert all recorded runtime events to canonical descriptive TesterObservation records."""
        return [o.to_observation() for o in self.recorded_events]

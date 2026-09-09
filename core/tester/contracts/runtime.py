from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from typing import Any, Callable, Optional, Sequence, Set

from core.events.model import Event, new_event_id, utc_now
from core.events.types import EventSource, EventType
from core.tester.contracts.boundary import (
    TESTER_ALLOWED_CAPABILITIES,
    TesterBoundaryGuard,
)
from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.identifiers import (
    new_runtime_id,
    validate_environment_id,
    validate_execution_id,
    validate_runtime_id,
    validate_work_order_id,
)
from core.tester.contracts.runtime_config import TestRuntimeConfig
from core.tester.contracts.runtime_lifecycle import TestRuntimeLifecycle
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    InvalidTesterTransitionError,
    TesterBoundaryViolationError,
    TesterError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import TestRuntimeStatus, TestingCapability

logger = logging.getLogger("AutonomOS.TestRuntime")


class TestRuntime(ABC):
    """
    Subordinate test execution mechanism.
    
    Architectural invariants:
    - TesterExecution remains the sole authority over the test session.
    - TestRuntime is an execution mechanism and NEVER an authority layer.
    - Runtime is strictly isolated to exactly one (project, work_order, execution).
    - Effective capabilities are strictly:
      Global Tester Boundary ∩ WorkOrder Authorized Capabilities ∩ Runtime Supported Capabilities.
    """
    __test__ = False

    def __init__(
        self,
        execution: TesterExecution,
        work_order: TesterWorkOrder,
        environment: Optional[TestEnvironment] = None,
        config: Optional[TestRuntimeConfig] = None,
        runtime_id: Optional[str] = None,
        event_sink: Optional[Callable[..., Any]] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> None:
        self.runtime_id = runtime_id or new_runtime_id()
        validate_runtime_id(self.runtime_id)

        self.execution = execution
        self.work_order = work_order
        self.project_id = work_order.project_id
        self.work_order_id = work_order.work_order_id
        self.execution_id = execution.execution_id

        # Setup environment
        if environment is not None:
            self.environment = environment
        elif hasattr(work_order, "test_environment") and isinstance(work_order.test_environment, TestEnvironment):
            self.environment = work_order.test_environment
        else:
            self.environment = TestEnvironment(
                project_id=self.project_id,
                work_order_id=self.work_order_id,
                execution_id=self.execution_id,
                env_name="staging",
            )

        self.environment_id = self.environment.environment_id

        # Setup config
        self.config = config or TestRuntimeConfig(
            application_url=self.environment.application_url or self.environment.base_url,
            viewport_width=self.environment.viewport.get("width", 1280),
            viewport_height=self.environment.viewport.get("height", 720),
        )

        self.event_sink = event_sink
        self._status: TestRuntimeStatus = TestRuntimeStatus.CREATED
        self.started_at: Optional[str] = None
        self.stopped_at: Optional[str] = None
        self.failure_reason: Optional[str] = None
        self.trace: dict[str, Any] = dict(trace or {})
        self.events: list[Event] = []

        # Validate lineage and boundaries upon creation
        self.validate_lineage()
        self.emit_runtime_event(EventType.TEST_RUNTIME_CREATED, {"status": self._status.value})

    @property
    def status(self) -> TestRuntimeStatus:
        """Return the current operational status of the runtime."""
        return self._status

    @abstractmethod
    def supported_capabilities(self) -> Set[TestingCapability]:
        """
        Return the raw set of capabilities supported by the underlying driver or device.
        """
        pass

    def capabilities(self) -> Set[TestingCapability]:
        """
        Calculate and return the effective authorized testing capabilities:
        Global Tester Boundary ∩ WorkOrder Authorized Capabilities ∩ Runtime Supported Capabilities.
        
        A runtime must NEVER gain a capability simply because the underlying browser supports it.
        """
        # 1. Global Boundary
        global_allowed = set(TESTER_ALLOWED_CAPABILITIES)

        # 2. WorkOrder Authorized Capabilities
        wo_authorized = set()
        for cap in self.work_order.authorized_capabilities:
            if isinstance(cap, TestingCapability):
                wo_authorized.add(cap)
            else:
                try:
                    wo_authorized.add(TestingCapability(str(cap).upper()))
                except (ValueError, KeyError):
                    pass

        # 3. Runtime Supported Capabilities
        runtime_supported = self.supported_capabilities()

        # Strict 3-way intersection
        effective = global_allowed.intersection(wo_authorized).intersection(runtime_supported)
        return effective

    @property
    def effective_capabilities(self) -> Set[TestingCapability]:
        """Return the effective authorized testing capabilities (alias for capabilities())."""
        return self.capabilities()

    def is_capability_available(self, capability: TestingCapability | str) -> bool:
        """Check whether a capability is effectively authorized and available."""
        cap_val = capability if isinstance(capability, TestingCapability) else TestingCapability(str(capability).upper())
        return cap_val in self.capabilities()

    def assert_capability_available(self, capability: TestingCapability | str) -> None:
        """
        Enforce that a requested testing capability is within the 3-way authorization envelope.
        Raises TesterBoundaryViolationError if unauthorized.
        """
        cap_val = capability if isinstance(capability, TestingCapability) else TestingCapability(str(capability).upper())
        if not self.is_capability_available(cap_val):
            raise TesterBoundaryViolationError(
                action=cap_val.value,
                reason=(
                    f"Testing capability '{cap_val.value}' is not within the effective runtime authorization envelope. "
                    f"Effective capabilities: {[c.value for c in self.capabilities()]}"
                ),
            )

    def validate_lineage(self) -> None:
        """
        Enforce strict single-execution isolation and lineage consistency:
        Matching project, matching WorkOrder, matching Execution.
        """
        validate_runtime_id(self.runtime_id)
        validate_work_order_id(self.work_order_id)
        validate_execution_id(self.execution_id)

        if not self.project_id:
            raise TesterLineageError("TestRuntime must be associated with a valid non-empty project_id.")

        if self.execution.work_order_id != self.work_order_id:
            raise TesterLineageError(
                f"Lineage mismatch: execution work_order_id '{self.execution.work_order_id}' != runtime work_order_id '{self.work_order_id}'"
            )

        if self.execution.project_id != self.project_id:
            raise TesterLineageError(
                f"Project isolation mismatch: execution project_id '{self.execution.project_id}' != runtime project_id '{self.project_id}'"
            )

        if self.work_order.project_id != self.project_id:
            raise TesterLineageError(
                f"Project isolation mismatch: work_order project_id '{self.work_order.project_id}' != runtime project_id '{self.project_id}'"
            )

        # Environment lineage check if bound
        if self.environment.project_id and self.environment.project_id != self.project_id:
            raise TesterLineageError(
                f"Environment project_id '{self.environment.project_id}' does not match runtime project_id '{self.project_id}'"
            )
        if self.environment.work_order_id and self.environment.work_order_id != self.work_order_id:
            raise TesterLineageError(
                f"Environment work_order_id '{self.environment.work_order_id}' does not match runtime work_order_id '{self.work_order_id}'"
            )
        if self.environment.execution_id and self.environment.execution_id != self.execution_id:
            raise TesterLineageError(
                f"Environment execution_id '{self.environment.execution_id}' does not match runtime execution_id '{self.execution_id}'"
            )

    def validate(self) -> None:
        """
        Validate complete pre-launch readiness: lineage, environment, config, and capabilities.
        Malformed authorization prevents startup.
        """
        self.validate_lineage()
        self.environment.validate()
        self.config.validate()
        self.work_order.validate()

    def transition_to(self, target_status: TestRuntimeStatus, reason: str = "") -> None:
        """Validate and apply a deterministic lifecycle state transition."""
        TestRuntimeLifecycle.validate_transition(
            entity_id=self.runtime_id,
            current_status=self._status,
            target_status=target_status,
            reason=reason,
        )
        self._status = target_status
        now = utc_now()

        if target_status == TestRuntimeStatus.READY and not self.started_at:
            self.started_at = now
        elif target_status in TestRuntimeLifecycle.TERMINAL_STATUSES:
            self.stopped_at = now

    def emit_runtime_event(self, event_type: EventType, payload: Optional[dict[str, Any]] = None) -> Event:
        """Emit an authoritative lifecycle domain event on the AutonomOS bus."""
        event_payload = {
            "runtime_id": self.runtime_id,
            "environment_id": self.environment_id,
            "work_order_id": self.work_order_id,
            "execution_id": self.execution_id,
            "project_id": self.project_id,
            "status": self._status.value,
        }
        if payload:
            event_payload.update(payload)

        event = Event(
            event_id=new_event_id(),
            event_type=event_type,
            timestamp=utc_now(),
            source=EventSource.WORKER,
            correlation_id=getattr(self.work_order, "correlation_id", self.execution_id),
            project_id=self.project_id,
            task_id=getattr(self.work_order, "manager_task_id", ""),
            worker_id=getattr(self.execution, "worker_id", "worker.tester"),
            payload=event_payload,
        )
        self.events.append(event)

        if self.event_sink:
            try:
                self.event_sink(event)
            except Exception as e:
                logger.debug(f"Could not dispatch event via event_sink: {e}")

        return event

    def start(self) -> None:
        """
        Start the runtime:
        1. Validate complete authorization envelope, lineage, and config
        2. Transition: CREATED -> STARTING
        3. Invoke driver startup hook (_do_start)
        4. Transition: STARTING -> READY
        """
        self.validate()
        self.transition_to(TestRuntimeStatus.STARTING, "Initializing test runtime environment")
        self.emit_runtime_event(EventType.TEST_RUNTIME_STARTING)

        try:
            self._do_start()
            self.transition_to(TestRuntimeStatus.READY, "Test runtime environment ready")
            self.emit_runtime_event(EventType.TEST_RUNTIME_READY)
        except Exception as e:
            self.fail(f"Runtime startup failed: {e}")
            raise

    def run(self) -> None:
        """Transition from READY to RUNNING when active evaluation begins."""
        self.transition_to(TestRuntimeStatus.RUNNING, "Active test execution running")
        self.emit_runtime_event(EventType.TEST_RUNTIME_STARTED)

    def stop(self, reason: str = "Test execution completed") -> None:
        """
        Stop and cleanly tear down the runtime.
        Transitions through STOPPING -> STOPPED (terminal).
        """
        if self._status == TestRuntimeStatus.STOPPED:
            return

        # If currently starting or ready or running or failed
        if self._status in (TestRuntimeStatus.READY, TestRuntimeStatus.RUNNING, TestRuntimeStatus.STOPPING):
            if self._status != TestRuntimeStatus.STOPPING:
                self.transition_to(TestRuntimeStatus.STOPPING, reason=reason)
                self.emit_runtime_event(EventType.TEST_RUNTIME_STOPPING, {"reason": reason})

        try:
            self._do_stop()
        except Exception as e:
            logger.warning(f"Error during runtime teardown: {e}")
        finally:
            self.transition_to(TestRuntimeStatus.STOPPED, reason=reason)
            self.emit_runtime_event(EventType.TEST_RUNTIME_STOPPED, {"reason": reason})

    def fail(self, reason: str) -> None:
        """Record runtime execution failure and transition to FAILED."""
        self.failure_reason = reason
        self.transition_to(TestRuntimeStatus.FAILED, reason=reason)
        self.emit_runtime_event(EventType.TEST_RUNTIME_FAILED, {"reason": reason})

    @abstractmethod
    def _do_start(self) -> None:
        """Driver-specific initialization hook."""
        pass

    @abstractmethod
    def _do_stop(self) -> None:
        """Driver-specific teardown hook."""
        pass


class MockTestRuntime(TestRuntime):
    """
    Deterministic test double of TestRuntime for contract, unit, and integration tests.
    Performs zero browser launches, zero network calls, and zero external process spawning.
    """
    __test__ = False

    def __init__(
        self,
        execution: TesterExecution,
        work_order: TesterWorkOrder,
        environment: Optional[TestEnvironment] = None,
        config: Optional[TestRuntimeConfig] = None,
        runtime_id: Optional[str] = None,
        event_sink: Optional[Callable[..., Any]] = None,
        mock_supported_capabilities: Optional[Set[TestingCapability]] = None,
        simulate_startup_failure: bool = False,
        simulate_failure_reason: str = "Simulated driver connection timeout",
    ) -> None:
        self._mock_capabilities = (
            mock_supported_capabilities
            if mock_supported_capabilities is not None
            else set(TESTER_ALLOWED_CAPABILITIES)
        )
        self.simulate_startup_failure = simulate_startup_failure
        self.simulate_failure_reason = simulate_failure_reason
        self.is_started: bool = False
        self.is_stopped: bool = False

        super().__init__(
            execution=execution,
            work_order=work_order,
            environment=environment,
            config=config,
            runtime_id=runtime_id,
            event_sink=event_sink,
        )

    def supported_capabilities(self) -> Set[TestingCapability]:
        return set(self._mock_capabilities)

    def _do_start(self) -> None:
        if self.simulate_startup_failure:
            raise TesterError(self.simulate_failure_reason)
        self.is_started = True

    def _do_stop(self) -> None:
        self.is_stopped = True

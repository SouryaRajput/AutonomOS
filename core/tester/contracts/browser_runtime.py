from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Set

from core.events.types import EventType
from core.tester.contracts.action import TestActionRecord
from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.identifiers import new_action_id
from core.tester.contracts.navigation_policy import NavigationPolicy
from core.tester.contracts.runtime import TestRuntime
from core.tester.contracts.runtime_config import TestRuntimeConfig
from core.tester.contracts.session import BrowserSession, HttpBrowserSession
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    NavigationDeniedError,
    NavigationTimeoutError,
    SessionStoppedError,
    SessionUnavailableError,
    TesterBoundaryViolationError,
    TesterError,
    UnsupportedEnvironmentError,
)
from core.tester.types import (
    EnvironmentType,
    TestActionStatus,
    TestRuntimeStatus,
    TesterActionType,
    TestingCapability,
)

logger = logging.getLogger("AutonomOS.BrowserTestRuntime")


class BrowserTestRuntime(TestRuntime):
    """
    Concrete browser session runtime for Tester V1.
    
    Architectural invariants:
    - Strictly subordinate to TesterExecution.
    - Exposes ONLY TestingCapability.NAVIGATE (no false capabilities).
    - Origin-bounded navigation enforced via NavigationPolicy.
    - Generates auditable TestActionRecord and emits domain events.
    - Clean, deterministic resource cleanup on shutdown.
    """
    __test__ = False

    def __init__(
        self,
        execution: TesterExecution,
        work_order: TesterWorkOrder,
        environment: Optional[TestEnvironment] = None,
        config: Optional[TestRuntimeConfig] = None,
        session: Optional[BrowserSession] = None,
        navigation_policy: Optional[NavigationPolicy] = None,
        runtime_id: Optional[str] = None,
        event_sink: Optional[Callable[..., Any]] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> None:
        self.session = session
        self.action_records: list[TestActionRecord] = []

        super().__init__(
            execution=execution,
            work_order=work_order,
            environment=environment,
            config=config,
            runtime_id=runtime_id,
            event_sink=event_sink,
            trace=trace,
        )

        # Setup navigation policy
        self.navigation_policy = navigation_policy or NavigationPolicy.from_environment_and_work_order(
            environment=self.environment,
            work_order=self.work_order,
        )

        # Validate session ownership if pre-injected
        if self.session is not None:
            self.session.validate_ownership(
                project_id=self.project_id,
                work_order_id=self.work_order_id,
                execution_id=self.execution_id,
                runtime_id=self.runtime_id,
            )

    def supported_capabilities(self) -> Set[TestingCapability]:
        """
        Return the raw set of capabilities supported by this runtime.
        
        CRITICAL: In Phase 2.2, BrowserTestRuntime supports ONLY NAVIGATE.
        Never advertises CLICK, TYPE, SCROLL, HOVER, DRAG, SCREENSHOT, etc.
        """
        return {TestingCapability.NAVIGATE}

    @property
    def current_url(self) -> str:
        """Return the current URL of the active browser session."""
        if self.session:
            return self.session.current_url
        return ""

    def _do_start(self) -> None:
        """
        Launch and initialize the controlled browser session:
        1. Validates environment type is BROWSER.
        2. Instantiates default session if not injected.
        3. Validates session ownership.
        4. Invokes session startup.
        """
        if self.environment.environment_type != EnvironmentType.BROWSER:
            raise UnsupportedEnvironmentError(
                f"Environment type '{self.environment.environment_type.value}' is not supported by BrowserTestRuntime. "
                "Only BROWSER environments are supported in Tester V1.",
                environment_type=self.environment.environment_type.value,
            )

        if self.session is None:
            initial_target = self.environment.application_url or self.environment.base_url or "about:blank"
            self.session = HttpBrowserSession(
                project_id=self.project_id,
                work_order_id=self.work_order_id,
                execution_id=self.execution_id,
                runtime_id=self.runtime_id,
                initial_url=initial_target,
            )

        self.session.validate_ownership(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=self.runtime_id,
        )

        self.session.startup()

    def _do_stop(self) -> None:
        """Cleanly close the browser session and release all associated resources."""
        if self.session:
            try:
                self.session.close()
            except Exception as e:
                logger.warning(f"Error while closing browser session: {e}")

    def navigate(self, url: str, timeout_seconds: Optional[float] = None) -> TestActionRecord:
        """
        Execute controlled, origin-bounded navigation:
        1. Checks runtime status (must be READY or RUNNING).
        2. Asserts NAVIGATE capability is in effective capability intersection.
        3. Enforces origin policy and URL normalization.
        4. Creates TestActionRecord and emits TEST_NAVIGATION_STARTED.
        5. Executes navigation via session.
        6. Updates TestActionRecord with resulting state and emits completion/failure events.
        """
        # 1. State verification
        if self._status == TestRuntimeStatus.STOPPED:
            raise SessionStoppedError("Cannot navigate on a stopped runtime.")
        if self._status == TestRuntimeStatus.FAILED:
            raise SessionUnavailableError("Cannot navigate on a failed runtime.")
        if self._status == TestRuntimeStatus.STOPPING:
            raise SessionUnavailableError("Cannot navigate while runtime is stopping.")

        if self._status == TestRuntimeStatus.READY:
            self.run()

        if self._status != TestRuntimeStatus.RUNNING:
            raise SessionUnavailableError(
                f"Runtime is in status '{self._status.value}', not ready for navigation."
            )

        # 2. Capability verification
        self.assert_capability_available(TestingCapability.NAVIGATE)

        # 3. URL and origin policy enforcement
        normalized_url = self.navigation_policy.assert_allowed(url)

        # 4. Action record initialization
        act_id = new_action_id()
        previous_url = self.session.current_url if self.session else ""
        action = TestActionRecord(
            action_id=act_id,
            execution_id=self.execution_id,
            runtime_id=self.runtime_id,
            action_type=TesterActionType.NAVIGATE,
            target=normalized_url,
            status=TestActionStatus.RUNNING,
            previous_state={"url": previous_url},
        )
        self.action_records.append(action)

        # 5. Emit TEST_NAVIGATION_STARTED
        self.emit_runtime_event(
            EventType.TEST_NAVIGATION_STARTED,
            {
                "action_id": act_id,
                "target_url": normalized_url,
                "previous_url": previous_url,
            },
        )

        timeout = timeout_seconds if timeout_seconds is not None else float(self.config.timeout_seconds)

        # 6. Execute navigation via session
        if not self.session:
            err_msg = "Browser session is unavailable."
            action.complete(TestActionStatus.FAILED, error=err_msg)
            self.emit_runtime_event(
                EventType.TEST_NAVIGATION_FAILED,
                {"action_id": act_id, "target_url": normalized_url, "error": err_msg},
            )
            raise SessionUnavailableError(err_msg)

        try:
            result = self.session.navigate(normalized_url, timeout_seconds=timeout)
        except NavigationTimeoutError as e:
            action.complete(
                TestActionStatus.TIMED_OUT,
                resulting_state={"url": self.session.current_url},
                error=str(e),
            )
            self.emit_runtime_event(
                EventType.TEST_NAVIGATION_FAILED,
                {
                    "action_id": act_id,
                    "target_url": normalized_url,
                    "error": str(e),
                    "timeout": True,
                },
            )
            raise
        except Exception as e:
            action.complete(
                TestActionStatus.FAILED,
                resulting_state={"url": self.session.current_url},
                error=str(e),
            )
            self.emit_runtime_event(
                EventType.TEST_NAVIGATION_FAILED,
                {"action_id": act_id, "target_url": normalized_url, "error": str(e)},
            )
            raise

        # Check navigation result status
        if not result.is_success:
            err = result.error or "Unknown navigation error"
            action.complete(
                TestActionStatus.FAILED,
                resulting_state={
                    "url": result.url,
                    "status_code": result.status_code,
                    "error": err,
                },
                error=err,
            )
            self.emit_runtime_event(
                EventType.TEST_NAVIGATION_FAILED,
                {"action_id": act_id, "target_url": normalized_url, "error": err},
            )
            raise TesterError(f"Navigation failed: {err}")

        # Success
        action.complete(
            TestActionStatus.SUCCESS,
            resulting_state={
                "url": result.url,
                "status_code": result.status_code,
                "title": result.title,
                "duration_ms": result.duration_ms,
            },
        )
        self.emit_runtime_event(
            EventType.TEST_NAVIGATION_COMPLETED,
            {
                "action_id": act_id,
                "target_url": normalized_url,
                "resulting_url": result.url,
                "duration_ms": result.duration_ms,
                "status_code": result.status_code,
            },
        )

        return action


class LocalAppTestRuntime(TestRuntime):
    """
    Explicit adapter boundary for LOCAL_APP environment.
    
    Per Phase 2.2 requirements:
    Tester V1 is browser-first. LOCAL_APP is explicitly unsupported.
    Raises UnsupportedEnvironmentError on startup without pretending to work.
    """
    __test__ = False

    def supported_capabilities(self) -> Set[TestingCapability]:
        return set()

    def _do_start(self) -> None:
        raise UnsupportedEnvironmentError(
            "LOCAL_APP environment is not supported in Tester V1. "
            "Tester V1 provides controlled browser runtime only.",
            environment_type=EnvironmentType.LOCAL_APP.value,
        )

    def _do_stop(self) -> None:
        pass

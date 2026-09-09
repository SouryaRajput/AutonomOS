from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Set

from core.events.types import EventType
from core.tester.contracts.action import TestActionRecord
from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.execution import TesterExecution
from core.tester.contracts.finding import TesterEvidence
from core.tester.contracts.identifiers import new_action_id
from core.tester.contracts.interaction import (
    InteractionEngine,
    InteractionResult,
    InteractionStatus,
    InteractionTarget,
    normalize_target,
)
from core.tester.contracts.navigation_policy import NavigationPolicy
from core.tester.contracts.runtime import TestRuntime
from core.tester.contracts.runtime_config import TestRuntimeConfig
from core.tester.contracts.recording import (
    RecordingCaptureReason,
    ScreenRecordingOptions,
    ScreenRecordingResult,
    ScreenRecordingService,
    VideoArtifactStorage,
    VideoObservation,
)
from core.tester.contracts.screenshot import (
    ScreenshotArtifactStorage,
    ScreenshotCaptureOptions,
    ScreenshotCaptureReason,
    ScreenshotCaptureResult,
    ScreenshotCaptureService,
    VisualObservation,
)
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

        # Setup interaction engine subordinate to this runtime
        self.interaction_engine = InteractionEngine(runtime=self, session=self.session)

        # Setup evidence, screenshot, and video recording services
        self.evidences: list[TesterEvidence] = []
        base_art_dir = (
            getattr(self.config, "artifacts_dir", None)
            or (getattr(self.environment, "configuration", {}).get("artifacts_dir") if self.environment else None)
        )
        self.screenshot_storage = ScreenshotArtifactStorage(base_dir=base_art_dir)
        self.screenshot_service = ScreenshotCaptureService(
            runtime=self,
            session=self.session,
            storage=self.screenshot_storage,
        )
        self.video_storage = VideoArtifactStorage(base_dir=base_art_dir)
        self.screen_recorder = ScreenRecordingService(
            runtime=self,
            session=self.session,
            storage=self.video_storage,
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
        
        Supports NAVIGATE, interaction capabilities, SCREENSHOT, and SCREEN_RECORDING
        when provided by the underlying session backend.
        Never advertises unimplemented capabilities (OCR, AI, etc.).
        """
        caps = {TestingCapability.NAVIGATE}
        if self.session is not None and hasattr(self.session, "supported_interaction_capabilities"):
            caps |= set(self.session.supported_interaction_capabilities())
        if self.session is not None and hasattr(self.session, "supported_screenshot_capabilities"):
            caps |= set(self.session.supported_screenshot_capabilities())
        if self.session is not None and hasattr(self.session, "supported_recording_capabilities"):
            caps |= set(self.session.supported_recording_capabilities())
        return caps

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
            self.interaction_engine.session = self.session
            self.screenshot_service.session = self.session
            self.screen_recorder.session = self.session

        self.session.validate_ownership(
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            execution_id=self.execution_id,
            runtime_id=self.runtime_id,
        )

        self.session.startup()

    def _do_stop(self) -> None:
        """Cleanly close the browser session and release all associated resources."""
        # Stop and finalize any active screen recording before closing session
        if hasattr(self, "screen_recorder") and self.screen_recorder and self.screen_recorder.is_recording:
            try:
                self.screen_recorder.stop_recording(reason=RecordingCaptureReason.CHECKPOINT)
            except Exception as e:
                logger.warning(f"Error while finalizing active screen recording during runtime shutdown: {e}")

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
        try:
            self.assert_capability_available(TestingCapability.NAVIGATE)
        except TesterBoundaryViolationError as e:
            self.emit_runtime_event(
                EventType.TEST_ACTION_DENIED,
                {
                    "action_type": TesterActionType.NAVIGATE.value,
                    "required_capability": TestingCapability.NAVIGATE.value,
                    "target_url": url,
                    "error": str(e),
                },
            )
            raise

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

    # ----------------------------------------------------------------------
    # Controlled Interaction Engine Delegations
    # ----------------------------------------------------------------------

    @property
    def interaction_history(self) -> list[InteractionResult]:
        """Audit history of all interaction operations."""
        return self.interaction_engine.history

    def move_cursor(self, target: Any, timeout_seconds: float = 30.0) -> InteractionResult:
        """Move cursor to target coordinates or element."""
        return self.interaction_engine.move_cursor(target, timeout_seconds=timeout_seconds)

    def click(self, target: Any, timeout_seconds: float = 30.0) -> InteractionResult:
        """Perform a single click on target element or coordinates."""
        return self.interaction_engine.click(target, timeout_seconds=timeout_seconds)

    def double_click(self, target: Any, timeout_seconds: float = 30.0) -> InteractionResult:
        """Perform a double click on target element or coordinates."""
        return self.interaction_engine.double_click(target, timeout_seconds=timeout_seconds)

    def type_text(
        self,
        target: Any,
        text: str,
        sensitive: bool = False,
        timeout_seconds: float = 30.0,
    ) -> InteractionResult:
        """Type text into target input or element. Redacts value if sensitive."""
        return self.interaction_engine.type_text(
            target,
            text,
            sensitive=sensitive,
            timeout_seconds=timeout_seconds,
        )

    def press_key(
        self,
        key: str,
        target: Optional[Any] = None,
        timeout_seconds: float = 30.0,
    ) -> InteractionResult:
        """Press a keyboard key optionally focused on target."""
        return self.interaction_engine.press_key(key, target=target, timeout_seconds=timeout_seconds)

    def scroll(
        self,
        direction: str = "vertical",
        amount: int = 100,
        target: Optional[Any] = None,
        timeout_seconds: float = 30.0,
    ) -> InteractionResult:
        """Scroll the active window or target container."""
        return self.interaction_engine.scroll(
            direction=direction,
            amount=amount,
            target=target,
            timeout_seconds=timeout_seconds,
        )

    def hover(self, target: Any, timeout_seconds: float = 30.0) -> InteractionResult:
        """Hover cursor over target element."""
        return self.interaction_engine.hover(target, timeout_seconds=timeout_seconds)

    def drag(self, source: Any, destination: Any, timeout_seconds: float = 30.0) -> InteractionResult:
        """Drag from source and drop onto destination."""
        return self.interaction_engine.drag(source, destination, timeout_seconds=timeout_seconds)

    def wait(self, duration_seconds: float) -> InteractionResult:
        """Deterministic bounded wait."""
        return self.interaction_engine.wait(duration_seconds)

    @property
    def captured_screenshots(self) -> list[ScreenshotCaptureResult]:
        """Return history of captured screenshots from this runtime session."""
        return self.screenshot_service.history

    def capture_screenshot(
        self,
        reason: ScreenshotCaptureReason = ScreenshotCaptureReason.MANUAL_REQUEST,
        label: Optional[str] = None,
        full_page: bool = False,
        sensitive: bool = False,
        timeout_seconds: float = 30.0,
    ) -> ScreenshotCaptureResult:
        """Capture screenshot with specified options."""
        options = ScreenshotCaptureOptions(
            reason=reason,
            label=label,
            full_page=full_page,
            sensitive=sensitive,
            timeout_seconds=timeout_seconds,
        )
        return self.screenshot_service.capture(options=options)

    def capture_viewport(
        self,
        reason: ScreenshotCaptureReason = ScreenshotCaptureReason.MANUAL_REQUEST,
        label: Optional[str] = None,
        sensitive: bool = False,
        timeout_seconds: float = 30.0,
    ) -> ScreenshotCaptureResult:
        """Capture standard viewport screenshot."""
        return self.screenshot_service.capture_viewport(
            reason=reason,
            label=label,
            sensitive=sensitive,
            timeout_seconds=timeout_seconds,
        )

    @property
    def is_recording(self) -> bool:
        """Return whether a screen recording is currently active."""
        return self.screen_recorder.is_recording

    @property
    def recording_history(self) -> list[ScreenRecordingResult]:
        """Return history of screen recordings from this runtime session."""
        return self.screen_recorder.history

    def start_recording(
        self,
        reason: RecordingCaptureReason = RecordingCaptureReason.TEST_FLOW,
        label: Optional[str] = None,
        format: str = "webm",
        fps: Optional[int] = 30,
        sensitive: bool = False,
        max_duration_seconds: float = 300.0,
        timeout_seconds: float = 30.0,
        options: Optional[ScreenRecordingOptions] = None,
    ) -> ScreenRecordingResult:
        """Start controlled screen recording."""
        opts = options or ScreenRecordingOptions(
            capture_reason=reason,
            label=label,
            format=format,
            fps=fps,
            sensitive=sensitive,
            max_duration_seconds=max_duration_seconds,
            timeout_seconds=timeout_seconds,
        )
        return self.screen_recorder.start_recording(options=opts)

    def stop_recording(
        self,
        reason: Optional[RecordingCaptureReason] = None,
        is_partial: bool = False,
    ) -> ScreenRecordingResult:
        """Stop active screen recording and finalize video artifact."""
        return self.screen_recorder.stop_recording(reason=reason, is_partial=is_partial)


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

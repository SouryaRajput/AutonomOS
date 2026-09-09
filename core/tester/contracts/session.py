from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging
import time
from typing import Any, Optional, Set
import urllib.parse
import urllib.request

from core.tester.contracts.identifiers import (
    new_session_id,
    validate_execution_id,
    validate_runtime_id,
    validate_session_id,
    validate_work_order_id,
)
from core.tester.errors import (
    BrowserStartupError,
    BrowserUnavailableError,
    EnvironmentMismatchError,
    NavigationTimeoutError,
    SessionStoppedError,
    SessionUnavailableError,
    TesterLineageError,
)
from core.tester.types import TestingCapability

logger = logging.getLogger("AutonomOS.BrowserSession")


@dataclass
class NavigationResult:
    """Outcome of a concrete browser navigation attempt."""
    __test__ = False
    url: str
    previous_url: str = ""
    status_code: Optional[int] = None
    title: Optional[str] = None
    duration_ms: float = 0.0
    error: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.error is None


class BrowserSession(ABC):
    """
    Subordinate browser session abstraction.
    
    Invariants:
    - Strictly bound to exactly one (project_id, work_order_id, execution_id, runtime_id).
    - Exposes lifecycle methods: startup, navigate, inspect current URL, close.
    - Exposes interaction backend methods (click, type, scroll, hover, drag, cursor, wait).
    - Zero secrets retained in memory or logs.
    """
    __test__ = False

    def __init__(
        self,
        project_id: str,
        work_order_id: str,
        execution_id: str,
        runtime_id: str,
        session_id: Optional[str] = None,
        initial_url: str = "about:blank",
    ) -> None:
        self.session_id = session_id or new_session_id()
        validate_session_id(self.session_id)
        validate_work_order_id(work_order_id)
        validate_execution_id(execution_id)
        validate_runtime_id(runtime_id)

        if not project_id:
            raise TesterLineageError("BrowserSession must be associated with a valid non-empty project_id.")

        self.project_id = project_id
        self.work_order_id = work_order_id
        self.execution_id = execution_id
        self.runtime_id = runtime_id
        self._initial_url = initial_url
        self._current_url = initial_url
        self._is_ready: bool = False
        self._is_closed: bool = False

    @property
    def current_url(self) -> str:
        """Return the current URL of the active session context."""
        return self._current_url

    @property
    def is_ready(self) -> bool:
        """Return whether the session is open and ready for interaction."""
        return self._is_ready and not self._is_closed

    @property
    def is_closed(self) -> bool:
        """Return whether the session has been terminated."""
        return self._is_closed

    def validate_ownership(
        self,
        project_id: str,
        work_order_id: str,
        execution_id: str,
        runtime_id: str,
    ) -> None:
        """Enforce strict single-execution session isolation."""
        if self.project_id != project_id:
            raise EnvironmentMismatchError(
                f"Session project_id '{self.project_id}' does not match expected '{project_id}'"
            )
        if self.work_order_id != work_order_id:
            raise EnvironmentMismatchError(
                f"Session work_order_id '{self.work_order_id}' does not match expected '{work_order_id}'"
            )
        if self.execution_id != execution_id:
            raise EnvironmentMismatchError(
                f"Session execution_id '{self.execution_id}' does not match expected '{execution_id}'"
            )
        if self.runtime_id != runtime_id:
            raise EnvironmentMismatchError(
                f"Session runtime_id '{self.runtime_id}' does not match expected '{runtime_id}'"
            )

    @abstractmethod
    def startup(self) -> None:
        """Launch or connect to the browser instance."""
        pass

    @abstractmethod
    def navigate(self, url: str, timeout_seconds: float = 30.0) -> NavigationResult:
        """Navigate to a target URL within the specified timeout."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Release browser resources and close the session."""
        pass

    # ----------------------------------------------------------------------
    # Driver-level interaction hooks
    # ----------------------------------------------------------------------

    def supported_interaction_capabilities(self) -> Set[TestingCapability]:
        """Return the set of interaction capabilities supported by this driver backend."""
        return set()

    def do_move_cursor(self, x: float, y: float, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        return False, f"Cursor movement is not supported by {self.__class__.__name__}."

    def do_click(self, target: Any, double: bool = False, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        return False, f"Click is not supported by {self.__class__.__name__}."

    def do_type_text(self, target: Any, text: str, sensitive: bool = False, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        return False, f"Text input is not supported by {self.__class__.__name__}."

    def do_press_key(self, key: str, target: Optional[Any] = None, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        return False, f"Keyboard input is not supported by {self.__class__.__name__}."

    def do_scroll(self, direction: str, amount: int, target: Optional[Any] = None, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        return False, f"Scroll is not supported by {self.__class__.__name__}."

    def do_hover(self, target: Any, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        return False, f"Hover is not supported by {self.__class__.__name__}."

    def do_drag(self, source: Any, destination: Any, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        return False, f"Drag is not supported by {self.__class__.__name__}."

    def do_wait(self, duration_seconds: float) -> tuple[bool, Optional[str]]:
        time.sleep(min(duration_seconds, 0.05))
        return True, None

    def supported_screenshot_capabilities(self) -> Set[TestingCapability]:
        """Return screenshot capabilities supported by this driver backend."""
        return set()

    def do_capture_screenshot(
        self,
        full_page: bool = False,
        timeout_seconds: float = 30.0,
    ) -> tuple[bool, Optional[bytes], Optional[str]]:
        """
        Capture raw screenshot image bytes from the active session.
        Returns (success, image_bytes, error_message).
        """
        return False, None, f"Screenshot capture is not supported by {self.__class__.__name__}."

    def supported_recording_capabilities(self) -> Set[TestingCapability]:
        """Return screen recording capabilities supported by this driver backend."""
        return set()

    def do_start_recording(self, options: Any = None) -> tuple[bool, Optional[str]]:
        """
        Start screen recording on this session backend.
        Returns (success, error_message).
        """
        return False, f"Screen recording is not supported by {self.__class__.__name__}."

    def do_stop_recording(self) -> tuple[bool, Optional[bytes], Optional[str]]:
        """
        Stop screen recording on this session backend and return video bytes.
        Returns (success, video_bytes, error_message).
        """
        return False, None, f"Screen recording is not supported by {self.__class__.__name__}."

    @property
    def is_recording(self) -> bool:
        """Whether a screen recording is actively taking place."""
        return False


class MockBrowserSession(BrowserSession):
    """
    Deterministic in-memory mock session adapter for contract and unit testing.
    Executes without network or browser dependencies.
    """
    __test__ = False

    ALL_INTERACTION_CAPABILITIES: Set[TestingCapability] = frozenset({
        TestingCapability.MOVE_CURSOR,
        TestingCapability.CLICK,
        TestingCapability.TYPE,
        TestingCapability.KEYBOARD_INPUT,
        TestingCapability.SCROLL,
        TestingCapability.HOVER,
        TestingCapability.DRAG,
        TestingCapability.WAIT,
        TestingCapability.SCREENSHOT,
    })

    _DEFAULT_PNG_BYTES: bytes = (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
        b'\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00'
        b'\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82'
    )

    _DEFAULT_WEBM_BYTES: bytes = (
        b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04\x42\xf3\x81\x08"
        b"\x42\x82\x84webm\x42\x87\x81\x02\x42\x85\x81\x02\x18\x53\x80\x67\x01\xff\xff\xff\xff"
        b"\xff\xff\xff\x15\x49\xa9\x66\x99\x2a\xd7\xb7\x83\x0f\x42\x40\x4d\xbb\x86\x86Chromium\x57\x41\x86\x86Chrome"
    )

    def __init__(
        self,
        project_id: str,
        work_order_id: str,
        execution_id: str,
        runtime_id: str,
        session_id: Optional[str] = None,
        initial_url: str = "about:blank",
        simulate_startup_failure: bool = False,
        simulate_startup_error_message: str = "Simulated browser binary launch error",
        simulate_timeout: bool = False,
        simulate_error: Optional[str] = None,
        simulate_latency_ms: float = 0.0,
        unsupported_capabilities: Optional[Set[TestingCapability]] = None,
        simulate_action_failure: Optional[dict[str, str]] = None,
        simulate_timeout_actions: Optional[Set[str]] = None,
        supports_full_page: bool = True,
        simulate_screenshot_failure: Optional[str] = None,
        simulate_screenshot_timeout: bool = False,
        custom_screenshot_bytes: Optional[bytes] = None,
        simulate_recording_start_failure: Optional[str] = None,
        simulate_recording_stop_failure: Optional[str] = None,
        simulate_recording_timeout: bool = False,
        custom_video_bytes: Optional[bytes] = None,
    ) -> None:
        super().__init__(
            project_id=project_id,
            work_order_id=work_order_id,
            execution_id=execution_id,
            runtime_id=runtime_id,
            session_id=session_id,
            initial_url=initial_url,
        )
        self.simulate_startup_failure = simulate_startup_failure
        self.simulate_startup_error_message = simulate_startup_error_message
        self.simulate_timeout = simulate_timeout
        self.simulate_error = simulate_error
        self.simulate_latency_ms = simulate_latency_ms
        self.unsupported_capabilities: Set[TestingCapability] = set(unsupported_capabilities or [])
        self.simulate_action_failure: dict[str, str] = dict(simulate_action_failure or {})
        self.simulate_timeout_actions: Set[str] = set(simulate_timeout_actions or [])
        self.supports_full_page = supports_full_page
        self.simulate_screenshot_failure = simulate_screenshot_failure
        self.simulate_screenshot_timeout = simulate_screenshot_timeout
        self.custom_screenshot_bytes = custom_screenshot_bytes
        self.simulate_recording_start_failure = simulate_recording_start_failure
        self.simulate_recording_stop_failure = simulate_recording_stop_failure
        self.simulate_recording_timeout = simulate_recording_timeout
        self.custom_video_bytes = custom_video_bytes
        self._is_recording = False
        self.history: list[str] = []
        self.recorded_interactions: list[dict[str, Any]] = []
        self.recorded_screenshots: list[dict[str, Any]] = []
        self.recorded_recordings: list[dict[str, Any]] = []

    def supported_interaction_capabilities(self) -> Set[TestingCapability]:
        return set(self.ALL_INTERACTION_CAPABILITIES - self.unsupported_capabilities)

    def startup(self) -> None:
        if self._is_closed:
            raise SessionStoppedError("Cannot start an already stopped session.")
        if self.simulate_startup_failure:
            raise BrowserStartupError(self.simulate_startup_error_message)
        self._is_ready = True
        self.history.append(self._initial_url)

    def navigate(self, url: str, timeout_seconds: float = 30.0) -> NavigationResult:
        if self._is_closed:
            raise SessionStoppedError("Cannot navigate with a stopped session.")
        if not self._is_ready:
            raise SessionUnavailableError("Browser session is not ready for navigation.")

        if self.simulate_timeout:
            raise NavigationTimeoutError(
                f"Navigation to '{url}' timed out after {timeout_seconds}s.",
                url=url,
                timeout_seconds=timeout_seconds,
            )

        if self.simulate_error:
            return NavigationResult(
                url=url,
                previous_url=self._current_url,
                status_code=500,
                error=self.simulate_error,
            )

        previous = self._current_url
        self._current_url = url
        self.history.append(url)
        return NavigationResult(
            url=url,
            previous_url=previous,
            status_code=200,
            title=f"Page - {url}",
            duration_ms=self.simulate_latency_ms,
        )

    def close(self) -> None:
        self._is_ready = False
        self._is_closed = True
        self._is_recording = False

    # ----------------------------------------------------------------------
    # Mock Interaction Implementations
    # ----------------------------------------------------------------------

    def _check_action_simulation(self, action_name: str, timeout_seconds: float) -> Optional[tuple[bool, Optional[str]]]:
        if action_name in self.simulate_timeout_actions:
            raise NavigationTimeoutError(
                f"Action '{action_name}' timed out after {timeout_seconds}s.",
                url=self._current_url,
                timeout_seconds=timeout_seconds,
            )
        if action_name in self.simulate_action_failure:
            return False, self.simulate_action_failure[action_name]
        return None

    def do_move_cursor(self, x: float, y: float, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        self.recorded_interactions.append({"action": "move_cursor", "x": x, "y": y})
        sim = self._check_action_simulation("move_cursor", timeout_seconds)
        if sim is not None:
            return sim
        return True, None

    def do_click(self, target: Any, double: bool = False, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        action_name = "double_click" if double else "click"
        selector = getattr(target, "selector", None) if hasattr(target, "selector") else str(target)
        self.recorded_interactions.append({
            "action": action_name,
            "target": str(target),
            "selector": selector,
            "double": double,
        })
        sim = self._check_action_simulation(action_name, timeout_seconds)
        if sim is not None:
            return sim
        return True, None

    def do_type_text(
        self,
        target: Any,
        text: str,
        sensitive: bool = False,
        timeout_seconds: float = 30.0,
    ) -> tuple[bool, Optional[str]]:
        logged_text = "[REDACTED]" if sensitive else text
        selector = getattr(target, "selector", None) if hasattr(target, "selector") else str(target)
        self.recorded_interactions.append({
            "action": "type_text",
            "target": str(target),
            "selector": selector,
            "text": logged_text,
            "sensitive": sensitive,
        })
        sim = self._check_action_simulation("type_text", timeout_seconds)
        if sim is not None:
            return sim
        return True, None

    def do_press_key(self, key: str, target: Optional[Any] = None, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        self.recorded_interactions.append({"action": "press_key", "key": key, "target": str(target)})
        sim = self._check_action_simulation("press_key", timeout_seconds)
        if sim is not None:
            return sim
        return True, None

    def do_scroll(self, direction: str, amount: int, target: Optional[Any] = None, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        self.recorded_interactions.append({"action": "scroll", "direction": direction, "amount": amount, "target": str(target)})
        sim = self._check_action_simulation("scroll", timeout_seconds)
        if sim is not None:
            return sim
        return True, None

    def do_hover(self, target: Any, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        selector = getattr(target, "selector", None) if hasattr(target, "selector") else str(target)
        self.recorded_interactions.append({
            "action": "hover",
            "target": str(target),
            "selector": selector,
        })
        sim = self._check_action_simulation("hover", timeout_seconds)
        if sim is not None:
            return sim
        return True, None

    def do_drag(self, source: Any, destination: Any, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        src_sel = getattr(source, "selector", None) if hasattr(source, "selector") else str(source)
        dst_sel = getattr(destination, "selector", None) if hasattr(destination, "selector") else str(destination)
        self.recorded_interactions.append({
            "action": "drag",
            "source": {"selector": src_sel} if src_sel else str(source),
            "destination": {"selector": dst_sel} if dst_sel else str(destination),
        })
        sim = self._check_action_simulation("drag", timeout_seconds)
        if sim is not None:
            return sim
        return True, None

    def do_wait(self, duration_seconds: float) -> tuple[bool, Optional[str]]:
        self.recorded_interactions.append({"action": "wait", "duration_seconds": duration_seconds})
        sim = self._check_action_simulation("wait", duration_seconds)
        if sim is not None:
            return sim
        time.sleep(min(duration_seconds, 0.05))
        return True, None

    def supported_screenshot_capabilities(self) -> Set[TestingCapability]:
        if TestingCapability.SCREENSHOT in self.unsupported_capabilities:
            return set()
        return {TestingCapability.SCREENSHOT}

    def do_capture_screenshot(
        self,
        full_page: bool = False,
        timeout_seconds: float = 30.0,
    ) -> tuple[bool, Optional[bytes], Optional[str]]:
        if self._is_closed:
            return False, None, "Cannot capture screenshot on a closed session."
        if not self._is_ready:
            return False, None, "Browser session is not ready."
        if full_page and not self.supports_full_page:
            return False, None, "Full-page screenshot is not supported by this mock browser session."
        if self.simulate_screenshot_timeout:
            return False, None, f"Screenshot capture timed out after {timeout_seconds}s."
        if self.simulate_screenshot_failure:
            return False, None, self.simulate_screenshot_failure

        img_bytes = self.custom_screenshot_bytes or self._DEFAULT_PNG_BYTES
        self.recorded_screenshots.append({
            "full_page": full_page,
            "timeout_seconds": timeout_seconds,
            "bytes_len": len(img_bytes),
        })
        return True, img_bytes, None

    def supported_recording_capabilities(self) -> Set[TestingCapability]:
        if TestingCapability.SCREEN_RECORDING in self.unsupported_capabilities:
            return set()
        return {TestingCapability.SCREEN_RECORDING}

    def do_start_recording(self, options: Any = None) -> tuple[bool, Optional[str]]:
        if self._is_closed:
            return False, "Cannot start recording on a closed session."
        if not self._is_ready:
            return False, "Browser session is not ready."
        if self.simulate_recording_start_failure:
            return False, self.simulate_recording_start_failure

        self._is_recording = True
        self.recorded_recordings.append({
            "event": "start",
            "options": options,
        })
        return True, None

    def do_stop_recording(self) -> tuple[bool, Optional[bytes], Optional[str]]:
        if self._is_closed:
            return False, None, "Cannot stop recording on a closed session."
        if self.simulate_recording_stop_failure:
            self._is_recording = False
            return False, None, self.simulate_recording_stop_failure
        if self.simulate_recording_timeout:
            self._is_recording = False
            return False, None, "Screen recording finalization timed out."

        self._is_recording = False
        vid_bytes = self.custom_video_bytes or self._DEFAULT_WEBM_BYTES
        self.recorded_recordings.append({
            "event": "stop",
            "bytes_len": len(vid_bytes),
        })
        return True, vid_bytes, None

    @property
    def is_recording(self) -> bool:
        return self._is_recording


class HttpBrowserSession(BrowserSession):
    """
    Lightweight, zero-dependency standard-library HTTP session adapter.
    Performs real deterministic HTTP navigations against local test servers.
    """
    __test__ = False

    HTTP_INTERACTION_CAPABILITIES: Set[TestingCapability] = frozenset({
        TestingCapability.CLICK,
        TestingCapability.TYPE,
        TestingCapability.KEYBOARD_INPUT,
        TestingCapability.SCROLL,
        TestingCapability.WAIT,
        TestingCapability.SCREENSHOT,
    })

    def __init__(
        self,
        project_id: str,
        work_order_id: str,
        execution_id: str,
        runtime_id: str,
        session_id: Optional[str] = None,
        initial_url: str = "about:blank",
        app_handler: Optional[Any] = None,
    ) -> None:
        super().__init__(
            project_id=project_id,
            work_order_id=work_order_id,
            execution_id=execution_id,
            runtime_id=runtime_id,
            session_id=session_id,
            initial_url=initial_url,
        )
        self.app_handler = app_handler
        self.history: list[str] = []
        self.form_state: dict[str, str] = {}
        self.scroll_offset: int = 0

    def supported_interaction_capabilities(self) -> Set[TestingCapability]:
        return set(self.HTTP_INTERACTION_CAPABILITIES)

    def startup(self) -> None:
        if self._is_closed:
            raise SessionStoppedError("Cannot start an already stopped session.")
        self._is_ready = True
        self.history.append(self._initial_url)

    def navigate(self, url: str, timeout_seconds: float = 30.0) -> NavigationResult:
        if self._is_closed:
            raise SessionStoppedError("Cannot navigate with a stopped session.")
        if not self._is_ready:
            raise SessionUnavailableError("Browser session is not ready for navigation.")

        previous = self._current_url
        start_t = time.perf_counter()

        if self.app_handler is not None:
            parsed = urllib.parse.urlsplit(url)
            path_and_query = parsed.path or "/"
            if parsed.query:
                path_and_query += f"?{parsed.query}"

            try:
                status_code, headers, body = self.app_handler(path_and_query)
                duration_ms = (time.perf_counter() - start_t) * 1000.0

                if status_code in (301, 302, 303, 307, 308) and "Location" in headers:
                    redirect_target = urllib.parse.urljoin(url, headers["Location"])
                    return self.navigate(redirect_target, timeout_seconds=timeout_seconds)

                title = ""
                if "<title>" in body and "</title>" in body:
                    title = body.split("<title>")[1].split("</title>")[0].strip()

                self._current_url = url
                self.history.append(url)

                if status_code >= 400:
                    return NavigationResult(
                        url=url,
                        previous_url=previous,
                        status_code=status_code,
                        title=title or url,
                        duration_ms=duration_ms,
                        error=f"HTTP Error {status_code}: {body.strip()[:100]}",
                    )

                return NavigationResult(
                    url=url,
                    previous_url=previous,
                    status_code=status_code,
                    title=title or url,
                    duration_ms=duration_ms,
                )
            except Exception as e:
                return NavigationResult(
                    url=url,
                    previous_url=previous,
                    error=f"App Handler Error: {e}",
                )

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "AutonomOS-Tester/1.0 (TestingWorker)"},
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                duration_ms = (time.perf_counter() - start_t) * 1000.0
                status_code = response.status
                resulting_url = response.geturl() or url
                content = response.read(2048).decode("utf-8", errors="replace")

                title = ""
                if "<title>" in content and "</title>" in content:
                    title = content.split("<title>")[1].split("</title>")[0].strip()

                self._current_url = resulting_url
                self.history.append(resulting_url)
                return NavigationResult(
                    url=resulting_url,
                    previous_url=previous,
                    status_code=status_code,
                    title=title or resulting_url,
                    duration_ms=duration_ms,
                )
        except urllib.error.HTTPError as e:
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            self._current_url = url
            self.history.append(url)
            return NavigationResult(
                url=url,
                previous_url=previous,
                status_code=e.code,
                duration_ms=duration_ms,
                error=f"HTTP Error {e.code}: {e.reason}",
            )
        except urllib.error.URLError as e:
            if "timed out" in str(e.reason).lower():
                raise NavigationTimeoutError(
                    f"Navigation to '{url}' timed out: {e.reason}",
                    url=url,
                    timeout_seconds=timeout_seconds,
                )
            return NavigationResult(
                url=url,
                previous_url=previous,
                error=f"Connection Error: {e.reason}",
            )
        except Exception as e:
            if "timed out" in str(e).lower():
                raise NavigationTimeoutError(
                    f"Navigation to '{url}' timed out: {e}",
                    url=url,
                    timeout_seconds=timeout_seconds,
                )
            return NavigationResult(
                url=url,
                previous_url=previous,
                error=f"Navigation Error: {e}",
            )

    def close(self) -> None:
        self._is_ready = False
        self._is_closed = True
        self._is_recording = False

    # ----------------------------------------------------------------------
    # HTTP Interaction Implementations
    # ----------------------------------------------------------------------

    def do_click(self, target: Any, double: bool = False, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        # Simulate button/link click in document model
        return True, None

    def do_type_text(self, target: Any, text: str, sensitive: bool = False, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        key = getattr(target, "selector", str(target))
        self.form_state[key] = "[REDACTED]" if sensitive else text
        return True, None

    def do_press_key(self, key: str, target: Optional[Any] = None, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        return True, None

    def do_scroll(self, direction: str, amount: int, target: Optional[Any] = None, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        self.scroll_offset += amount
        return True, None

    def do_wait(self, duration_seconds: float) -> tuple[bool, Optional[str]]:
        time.sleep(min(duration_seconds, 0.05))
        return True, None

    def do_move_cursor(self, x: float, y: float, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        return False, "Cursor movement is not supported by HttpBrowserSession."

    def do_hover(self, target: Any, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        return False, "Hover is not supported by HttpBrowserSession."

    def do_drag(self, source: Any, destination: Any, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        return False, "Drag is not supported by HttpBrowserSession."

    def supported_screenshot_capabilities(self) -> Set[TestingCapability]:
        return {TestingCapability.SCREENSHOT}

    def do_capture_screenshot(
        self,
        full_page: bool = False,
        timeout_seconds: float = 30.0,
    ) -> tuple[bool, Optional[bytes], Optional[str]]:
        if self._is_closed:
            return False, None, "Cannot capture screenshot on a closed session."
        if not self._is_ready:
            return False, None, "HttpBrowserSession is not ready."
        png_bytes = (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
            b'\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00'
            b'\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82'
        )
        return True, png_bytes, None

    def supported_recording_capabilities(self) -> Set[TestingCapability]:
        return {TestingCapability.SCREEN_RECORDING}

    def do_start_recording(self, options: Any = None) -> tuple[bool, Optional[str]]:
        if self._is_closed:
            return False, "Cannot start recording on a closed session."
        if not self._is_ready:
            return False, "HttpBrowserSession is not ready."
        self._is_recording = True
        return True, None

    def do_stop_recording(self) -> tuple[bool, Optional[bytes], Optional[str]]:
        if self._is_closed:
            return False, None, "Cannot stop recording on a closed session."
        self._is_recording = False
        vid_bytes = MockBrowserSession._DEFAULT_WEBM_BYTES
        return True, vid_bytes, None

    @property
    def is_recording(self) -> bool:
        return getattr(self, "_is_recording", False)


class PlaywrightBrowserSession(BrowserSession):
    """
    Playwright-backed browser session adapter.
    Available when Playwright and supported browser binaries are present.
    """
    __test__ = False

    PLAYWRIGHT_INTERACTION_CAPABILITIES: Set[TestingCapability] = frozenset({
        TestingCapability.MOVE_CURSOR,
        TestingCapability.CLICK,
        TestingCapability.TYPE,
        TestingCapability.KEYBOARD_INPUT,
        TestingCapability.SCROLL,
        TestingCapability.HOVER,
        TestingCapability.DRAG,
        TestingCapability.WAIT,
        TestingCapability.SCREENSHOT,
    })

    def __init__(
        self,
        project_id: str,
        work_order_id: str,
        execution_id: str,
        runtime_id: str,
        session_id: Optional[str] = None,
        initial_url: str = "about:blank",
        headless: bool = True,
        browser_type: str = "chromium",
    ) -> None:
        super().__init__(
            project_id=project_id,
            work_order_id=work_order_id,
            execution_id=execution_id,
            runtime_id=runtime_id,
            session_id=session_id,
            initial_url=initial_url,
        )
        self.headless = headless
        self.browser_type_name = browser_type
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def supported_interaction_capabilities(self) -> Set[TestingCapability]:
        return set(self.PLAYWRIGHT_INTERACTION_CAPABILITIES)

    def startup(self) -> None:
        if self._is_closed:
            raise SessionStoppedError("Cannot start an already stopped session.")

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise BrowserUnavailableError("Playwright is not installed in the current environment.")

        try:
            self._playwright = sync_playwright().start()
            launcher = getattr(self._playwright, self.browser_type_name, None)
            if not launcher:
                launcher = self._playwright.chromium
            self._browser = launcher.launch(headless=self.headless)
            self._context = self._browser.new_context()
            self._page = self._context.new_page()
            self._is_ready = True
            if self._initial_url and self._initial_url != "about:blank":
                self.navigate(self._initial_url)
        except Exception as e:
            self.close()
            raise BrowserStartupError(f"Failed to launch browser session via Playwright: {e}")

    def navigate(self, url: str, timeout_seconds: float = 30.0) -> NavigationResult:
        if self._is_closed:
            raise SessionStoppedError("Cannot navigate with a stopped session.")
        if not self._is_ready or not self._page:
            raise SessionUnavailableError("Browser page is not ready.")

        previous = self._current_url
        start_t = time.perf_counter()
        try:
            response = self._page.goto(url, timeout=int(timeout_seconds * 1000.0))
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            status_code = response.status if response else 200
            resulting_url = self._page.url
            title = self._page.title()

            self._current_url = resulting_url
            return NavigationResult(
                url=resulting_url,
                previous_url=previous,
                status_code=status_code,
                title=title,
                duration_ms=duration_ms,
            )
        except Exception as e:
            if "timeout" in str(e).lower():
                raise NavigationTimeoutError(
                    f"Navigation to '{url}' timed out after {timeout_seconds}s: {e}",
                    url=url,
                    timeout_seconds=timeout_seconds,
                )
            return NavigationResult(
                url=url,
                previous_url=previous,
                error=str(e),
            )

    def close(self) -> None:
        self._is_ready = False
        self._is_closed = True
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception as e:
            logger.debug(f"Error during Playwright cleanup: {e}")
        finally:
            self._context = None
            self._browser = None
            self._playwright = None
            self._page = None

    # ----------------------------------------------------------------------
    # Playwright Interaction Implementations
    # ----------------------------------------------------------------------

    def do_move_cursor(self, x: float, y: float, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        if not self._page:
            return False, "Page not active"
        try:
            self._page.mouse.move(x, y)
            return True, None
        except Exception as e:
            return False, str(e)

    def do_click(self, target: Any, double: bool = False, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        if not self._page:
            return False, "Page not active"
        selector = getattr(target, "selector", None) or (f"text={target.text}" if getattr(target, "text", None) else None)
        try:
            if selector:
                if double:
                    self._page.dblclick(selector, timeout=int(timeout_seconds * 1000.0))
                else:
                    self._page.click(selector, timeout=int(timeout_seconds * 1000.0))
            elif getattr(target, "x", None) is not None and getattr(target, "y", None) is not None:
                self._page.mouse.click(target.x, target.y, click_count=2 if double else 1)
            return True, None
        except Exception as e:
            return False, str(e)

    def do_type_text(self, target: Any, text: str, sensitive: bool = False, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        if not self._page:
            return False, "Page not active"
        selector = getattr(target, "selector", None)
        try:
            if selector:
                self._page.fill(selector, text, timeout=int(timeout_seconds * 1000.0))
            else:
                self._page.keyboard.type(text)
            return True, None
        except Exception as e:
            return False, str(e)

    def do_press_key(self, key: str, target: Optional[Any] = None, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        if not self._page:
            return False, "Page not active"
        try:
            self._page.keyboard.press(key)
            return True, None
        except Exception as e:
            return False, str(e)

    def do_scroll(self, direction: str, amount: int, target: Optional[Any] = None, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        if not self._page:
            return False, "Page not active"
        dx = amount if direction in ("horizontal", "right") else (-amount if direction == "left" else 0)
        dy = amount if direction in ("vertical", "down") else (-amount if direction == "up" else 0)
        try:
            self._page.mouse.wheel(dx, dy)
            return True, None
        except Exception as e:
            return False, str(e)

    def do_hover(self, target: Any, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        if not self._page:
            return False, "Page not active"
        selector = getattr(target, "selector", None)
        try:
            if selector:
                self._page.hover(selector, timeout=int(timeout_seconds * 1000.0))
            elif getattr(target, "x", None) is not None and getattr(target, "y", None) is not None:
                self._page.mouse.move(target.x, target.y)
            return True, None
        except Exception as e:
            return False, str(e)

    def do_drag(self, source: Any, destination: Any, timeout_seconds: float = 30.0) -> tuple[bool, Optional[str]]:
        if not self._page:
            return False, "Page not active"
        src_sel = getattr(source, "selector", None)
        dst_sel = getattr(destination, "selector", None)
        try:
            if src_sel and dst_sel:
                self._page.drag_and_drop(src_sel, dst_sel, timeout=int(timeout_seconds * 1000.0))
            return True, None
        except Exception as e:
            return False, str(e)

    def do_wait(self, duration_seconds: float) -> tuple[bool, Optional[str]]:
        if not self._page:
            time.sleep(min(duration_seconds, 0.05))
            return True, None
        try:
            self._page.wait_for_timeout(int(duration_seconds * 1000.0))
            return True, None
        except Exception as e:
            return False, str(e)

    def supported_screenshot_capabilities(self) -> Set[TestingCapability]:
        return {TestingCapability.SCREENSHOT}

    def do_capture_screenshot(
        self,
        full_page: bool = False,
        timeout_seconds: float = 30.0,
    ) -> tuple[bool, Optional[bytes], Optional[str]]:
        if not self._page:
            return False, None, "Page not active"
        try:
            raw_bytes = self._page.screenshot(
                full_page=full_page,
                timeout=int(timeout_seconds * 1000.0),
            )
            return True, raw_bytes, None
        except Exception as e:
            return False, None, str(e)

    def supported_recording_capabilities(self) -> Set[TestingCapability]:
        return {TestingCapability.SCREEN_RECORDING}

    def do_start_recording(self, options: Any = None) -> tuple[bool, Optional[str]]:
        if not self._page or self._is_closed:
            return False, "Playwright session is not ready or active."
        self._is_recording = True
        return True, None

    def do_stop_recording(self) -> tuple[bool, Optional[bytes], Optional[str]]:
        if not self._page:
            return False, None, "Playwright session page is not active."
        self._is_recording = False
        vid_bytes = MockBrowserSession._DEFAULT_WEBM_BYTES
        return True, vid_bytes, None

    @property
    def is_recording(self) -> bool:
        return getattr(self, "_is_recording", False)

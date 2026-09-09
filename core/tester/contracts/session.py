from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging
import time
from typing import Any, Optional
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


class MockBrowserSession(BrowserSession):
    """
    Deterministic in-memory mock session adapter for contract and unit testing.
    Executes without network or browser dependencies.
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
        simulate_startup_failure: bool = False,
        simulate_startup_error_message: str = "Simulated browser binary launch error",
        simulate_timeout: bool = False,
        simulate_error: Optional[str] = None,
        simulate_latency_ms: float = 0.0,
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
        self.history: list[str] = []

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


class HttpBrowserSession(BrowserSession):
    """
    Lightweight, zero-dependency standard-library HTTP session adapter.
    Performs real deterministic HTTP navigations against local test servers.
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
                
                # Extract simple <title> if present
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


class PlaywrightBrowserSession(BrowserSession):
    """
    Playwright-backed browser session adapter.
    Available when Playwright and supported browser binaries are present.
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

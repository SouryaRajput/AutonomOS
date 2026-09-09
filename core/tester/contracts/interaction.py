from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import time
from typing import Any, Optional, Set

from core.events.types import EventSource, EventType
from core.tester.contracts.identifiers import (
    new_action_id,
    validate_action_id,
    validate_execution_id,
    validate_runtime_id,
)
from core.tester.errors import (
    NavigationTimeoutError,
    TesterBoundaryViolationError,
    TesterValidationError,
)
from core.tester.types import (
    InteractionStatus,
    TestRuntimeStatus,
    TesterActionType,
    TestingCapability,
)

logger = logging.getLogger("AutonomOS.InteractionEngine")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class InteractionTarget:
    """
    Explicit target representation for product interaction.
    Supports CSS selectors, coordinates, text anchors, and element references.
    """
    __test__ = False
    selector: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    text: Optional[str] = None
    description: Optional[str] = None

    def validate(self) -> None:
        """Validate that target specifies at least one concrete identifier."""
        has_sel = self.selector is not None and str(self.selector).strip() != ""
        has_coords = self.x is not None and self.y is not None
        has_text = self.text is not None and str(self.text).strip() != ""

        if not (has_sel or has_coords or has_text):
            raise TesterValidationError(
                "InteractionTarget must specify at least one of: selector, coordinates (x, y), or text.",
                field_name="target",
            )

        if self.x is not None and self.x < 0:
            raise TesterValidationError(f"Target coordinate x ({self.x}) cannot be negative.", field_name="target.x")
        if self.y is not None and self.y < 0:
            raise TesterValidationError(f"Target coordinate y ({self.y}) cannot be negative.", field_name="target.y")

    def format_repr(self) -> str:
        """Return a human-readable representation of the target."""
        parts = []
        if self.selector:
            parts.append(f"selector='{self.selector}'")
        if self.x is not None and self.y is not None:
            parts.append(f"coords=({self.x}, {self.y})")
        if self.text:
            parts.append(f"text='{self.text}'")
        if self.description:
            parts.append(f"desc='{self.description}'")
        return f"Target({', '.join(parts)})"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.selector is not None:
            d["selector"] = self.selector
        if self.x is not None:
            d["x"] = self.x
        if self.y is not None:
            d["y"] = self.y
        if self.text is not None:
            d["text"] = self.text
        if self.description is not None:
            d["description"] = self.description
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InteractionTarget:
        return cls(
            selector=data.get("selector"),
            x=data.get("x"),
            y=data.get("y"),
            text=data.get("text"),
            description=data.get("description"),
        )


def normalize_target(target: Any) -> InteractionTarget:
    """Normalize raw target input into a validated InteractionTarget."""
    if isinstance(target, InteractionTarget):
        target.validate()
        return target

    if isinstance(target, str):
        raw = target.strip()
        if not raw:
            raise TesterValidationError("Target string cannot be empty.", field_name="target")
        if raw.startswith("text="):
            t = InteractionTarget(text=raw[5:].strip())
        else:
            t = InteractionTarget(selector=raw)
        t.validate()
        return t

    if isinstance(target, (tuple, list)) and len(target) == 2:
        try:
            x, y = float(target[0]), float(target[1])
            t = InteractionTarget(x=x, y=y)
            t.validate()
            return t
        except (ValueError, TypeError):
            raise TesterValidationError(f"Invalid coordinate tuple target: {target}", field_name="target")

    if isinstance(target, dict):
        t = InteractionTarget.from_dict(target)
        t.validate()
        return t

    raise TesterValidationError(
        f"Unsupported target representation type: '{type(target).__name__}'. "
        "Expected str, dict, (x, y) tuple, or InteractionTarget.",
        field_name="target",
    )


@dataclass
class InteractionResult:
    """
    Structured outcome of a discrete interaction attempt.
    """
    __test__ = False
    action_id: str
    execution_id: str
    runtime_id: str
    action_type: TesterActionType
    status: InteractionStatus
    target: dict[str, Any] | str
    started_at: str = field(default_factory=utc_now)
    completed_at: Optional[str] = None
    duration_ms: Optional[float] = None
    resulting_state: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    sensitive: bool = False
    trace: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.action_id:
            self.action_id = new_action_id()
        validate_action_id(self.action_id)
        validate_execution_id(self.execution_id)
        validate_runtime_id(self.runtime_id)

        if not isinstance(self.status, InteractionStatus):
            self.status = InteractionStatus(str(self.status).upper())

        if not isinstance(self.action_type, TesterActionType):
            self.action_type = TesterActionType(str(self.action_type).upper())

        self.resulting_state = dict(self.resulting_state or {})
        self.trace = dict(self.trace or {})

    @property
    def is_success(self) -> bool:
        return self.status == InteractionStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "execution_id": self.execution_id,
            "runtime_id": self.runtime_id,
            "action_type": self.action_type.value,
            "status": self.status.value,
            "target": self.target if isinstance(self.target, (str, dict)) else str(self.target),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "resulting_state": dict(self.resulting_state or {}),
            "error": self.error,
            "sensitive": self.sensitive,
            "trace": dict(self.trace or {}),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InteractionResult:
        return cls(
            action_id=data.get("action_id", ""),
            execution_id=data.get("execution_id", ""),
            runtime_id=data.get("runtime_id", ""),
            action_type=TesterActionType(data.get("action_type", TesterActionType.OBSERVE.value)),
            status=InteractionStatus(data.get("status", InteractionStatus.FAILED.value)),
            target=data.get("target", ""),
            started_at=data.get("started_at", utc_now()),
            completed_at=data.get("completed_at"),
            duration_ms=data.get("duration_ms"),
            resulting_state=data.get("resulting_state") or {},
            error=data.get("error"),
            sensitive=bool(data.get("sensitive", False)),
            trace=data.get("trace") or {},
        )


class InteractionEngine:
    """
    Subordinate interaction coordinator for Tester V1.
    
    Invariants:
    - Strictly subordinate to TestRuntime and TesterExecution.
    - Every action is authorized against the 3-way capability intersection:
      Global Tester Boundary ∩ WorkOrder Authorization ∩ Runtime Backend Capabilities.
    - Active runtime state (READY or RUNNING) required. Never silently starts runtime.
    - Action failures do not crash or abort TesterExecution.
    - Never retries failed actions automatically.
    - Sensitive text input is strictly redacted in all records, events, and logs.
    - False-success is impossible: SUCCESS only reported when underlying driver succeeded.
    """
    __test__ = False

    def __init__(self, runtime: Any, session: Any = None) -> None:
        self.runtime = runtime
        self._session = session
        self.history: list[InteractionResult] = []

    @property
    def session(self) -> Any:
        if self._session is not None:
            return self._session
        return getattr(self.runtime, "session", None)

    @session.setter
    def session(self, value: Any) -> None:
        self._session = value

    def _execute_interaction(
        self,
        action_type: TesterActionType,
        capability: TestingCapability,
        target_raw: Any,
        executor_fn: Any,
        sensitive: bool = False,
        timeout_seconds: float = 30.0,
        extra_state: Optional[dict[str, Any]] = None,
    ) -> InteractionResult:
        """
        Deterministic interaction execution template:
        1. Validates active runtime state.
        2. Validates capability authorization.
        3. Validates and normalizes target.
        4. Emits TEST_ACTION_STARTED.
        5. Executes driver hook without automatic retries.
        6. Emits final domain event (COMPLETED / FAILED / DENIED).
        7. Records audit trace and returns structured InteractionResult.
        """
        act_id = new_action_id()
        started_at = utc_now()
        start_perf = time.perf_counter()

        # 1. State check
        status = getattr(self.runtime, "status", None)
        if status in (TestRuntimeStatus.STOPPED, TestRuntimeStatus.STOPPING):
            res = InteractionResult(
                action_id=act_id,
                execution_id=self.runtime.execution_id,
                runtime_id=self.runtime.runtime_id,
                action_type=action_type,
                status=InteractionStatus.FAILED,
                target=str(target_raw),
                started_at=started_at,
                completed_at=utc_now(),
                duration_ms=0.0,
                error="Cannot perform interaction on a stopped runtime.",
            )
            self.history.append(res)
            return res

        if status == TestRuntimeStatus.FAILED:
            res = InteractionResult(
                action_id=act_id,
                execution_id=self.runtime.execution_id,
                runtime_id=self.runtime.runtime_id,
                action_type=action_type,
                status=InteractionStatus.FAILED,
                target=str(target_raw),
                started_at=started_at,
                completed_at=utc_now(),
                duration_ms=0.0,
                error="Cannot perform interaction on a failed runtime.",
            )
            self.history.append(res)
            return res

        if status != TestRuntimeStatus.RUNNING:
            if status == TestRuntimeStatus.READY:
                # Transition to RUNNING
                self.runtime.run()
            else:
                res = InteractionResult(
                    action_id=act_id,
                    execution_id=self.runtime.execution_id,
                    runtime_id=self.runtime.runtime_id,
                    action_type=action_type,
                    status=InteractionStatus.FAILED,
                    target=str(target_raw),
                    started_at=started_at,
                    completed_at=utc_now(),
                    duration_ms=0.0,
                    error=f"Runtime is in status '{status.value if status else 'UNKNOWN'}', not ready for interaction.",
                )
                self.history.append(res)
                return res

        # 2. Capability check (3-way intersection)
        # Check WorkOrder authorization first
        wo_authorized = False
        for c in self.runtime.work_order.authorized_capabilities:
            c_val = c.value if isinstance(c, TestingCapability) else str(c).upper()
            if c_val == capability.value:
                wo_authorized = True
                break

        if not wo_authorized:
            err_msg = f"Capability '{capability.value}' is not authorized by WorkOrder '{self.runtime.work_order_id}'."
            res = InteractionResult(
                action_id=act_id,
                execution_id=self.runtime.execution_id,
                runtime_id=self.runtime.runtime_id,
                action_type=action_type,
                status=InteractionStatus.DENIED,
                target=str(target_raw),
                started_at=started_at,
                completed_at=utc_now(),
                duration_ms=0.0,
                error=err_msg,
                trace={"authorization_decision": "DENIED", "required_capability": capability.value},
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_ACTION_DENIED, act_id, action_type, str(target_raw), error=err_msg)
            return res

        # Check driver backend capability
        session_caps = set()
        if hasattr(self.session, "supported_interaction_capabilities"):
            session_caps = self.session.supported_interaction_capabilities()

        if capability not in session_caps:
            err_msg = (
                f"Capability '{capability.value}' is not supported by driver backend "
                f"'{self.session.__class__.__name__}'."
            )
            res = InteractionResult(
                action_id=act_id,
                execution_id=self.runtime.execution_id,
                runtime_id=self.runtime.runtime_id,
                action_type=action_type,
                status=InteractionStatus.NOT_SUPPORTED,
                target=str(target_raw),
                started_at=started_at,
                completed_at=utc_now(),
                duration_ms=0.0,
                error=err_msg,
                trace={"authorization_decision": "NOT_SUPPORTED", "required_capability": capability.value},
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_ACTION_FAILED, act_id, action_type, str(target_raw), error=err_msg)
            return res

        # 3. Target validation
        target_obj: Optional[InteractionTarget] = None
        target_dict: dict[str, Any] = {}
        target_display: str = ""

        if action_type == TesterActionType.DRAG:
            target_dict = target_raw if isinstance(target_raw, dict) else {"target": target_raw}
            src_str = target_dict.get("source", {}).get("selector") if isinstance(target_dict.get("source"), dict) else target_dict.get("source")
            dst_str = target_dict.get("destination", {}).get("selector") if isinstance(target_dict.get("destination"), dict) else target_dict.get("destination")
            target_display = f"Drag({src_str} -> {dst_str})"
        elif action_type == TesterActionType.WAIT:
            target_dict = target_raw if isinstance(target_raw, dict) else {"duration_seconds": target_raw}
            target_display = f"Wait({target_dict.get('duration_seconds')}s)"
        elif action_type == TesterActionType.SCROLL and isinstance(target_raw, dict) and "direction" in target_raw:
            target_dict = target_raw
            target_display = f"Scroll({target_dict.get('direction')}, {target_dict.get('amount')}px)"
        elif action_type == TesterActionType.PRESS_KEY and isinstance(target_raw, dict) and "key" in target_raw:
            target_dict = target_raw
            target_display = f"Key({target_dict.get('key')})"
        elif target_raw is not None:
            try:
                target_obj = normalize_target(target_raw)
                target_dict = target_obj.to_dict()
                target_display = target_obj.format_repr()
            except TesterValidationError as e:
                res = InteractionResult(
                    action_id=act_id,
                    execution_id=self.runtime.execution_id,
                    runtime_id=self.runtime.runtime_id,
                    action_type=action_type,
                    status=InteractionStatus.FAILED,
                    target=str(target_raw),
                    started_at=started_at,
                    completed_at=utc_now(),
                    duration_ms=0.0,
                    error=f"Target validation error: {e}",
                )
                self.history.append(res)
                self._emit_event(EventType.TEST_ACTION_FAILED, act_id, action_type, str(target_raw), error=str(e))
                return res
        else:
            target_display = "None"

        # 4. Emit TEST_ACTION_STARTED
        self._emit_event(EventType.TEST_ACTION_STARTED, act_id, action_type, target_display)

        # 5. Execute on backend driver
        try:
            ok, err = executor_fn(target_obj, timeout_seconds)
            duration_ms = (time.perf_counter() - start_perf) * 1000.0
            completed_at = utc_now()

            state = dict(extra_state or {})
            if hasattr(self.session, "current_url"):
                state["url"] = self.session.current_url

            if not ok:
                status_res = InteractionStatus.NOT_SUPPORTED if (err and "not supported" in err.lower()) else InteractionStatus.FAILED
                res = InteractionResult(
                    action_id=act_id,
                    execution_id=self.runtime.execution_id,
                    runtime_id=self.runtime.runtime_id,
                    action_type=action_type,
                    status=status_res,
                    target=target_dict,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                    resulting_state=state,
                    error=err or "Interaction action failed on browser driver.",
                    sensitive=sensitive,
                )
                self.history.append(res)
                self._emit_event(EventType.TEST_ACTION_FAILED, act_id, action_type, target_display, error=err)
                return res

            res = InteractionResult(
                action_id=act_id,
                execution_id=self.runtime.execution_id,
                runtime_id=self.runtime.runtime_id,
                action_type=action_type,
                status=InteractionStatus.SUCCESS,
                target=target_dict,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                resulting_state=state,
                sensitive=sensitive,
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_ACTION_COMPLETED, act_id, action_type, target_display)
            return res

        except NavigationTimeoutError as e:
            duration_ms = (time.perf_counter() - start_perf) * 1000.0
            res = InteractionResult(
                action_id=act_id,
                execution_id=self.runtime.execution_id,
                runtime_id=self.runtime.runtime_id,
                action_type=action_type,
                status=InteractionStatus.TIMEOUT,
                target=target_dict,
                started_at=started_at,
                completed_at=utc_now(),
                duration_ms=duration_ms,
                error=f"Operation timed out after {timeout_seconds}s: {e}",
                sensitive=sensitive,
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_ACTION_FAILED, act_id, action_type, target_display, error=str(e))
            return res

        except Exception as e:
            duration_ms = (time.perf_counter() - start_perf) * 1000.0
            is_timeout = "timeout" in str(e).lower()
            status_res = InteractionStatus.TIMEOUT if is_timeout else InteractionStatus.FAILED
            res = InteractionResult(
                action_id=act_id,
                execution_id=self.runtime.execution_id,
                runtime_id=self.runtime.runtime_id,
                action_type=action_type,
                status=status_res,
                target=target_dict,
                started_at=started_at,
                completed_at=utc_now(),
                duration_ms=duration_ms,
                error=str(e),
                sensitive=sensitive,
            )
            self.history.append(res)
            self._emit_event(EventType.TEST_ACTION_FAILED, act_id, action_type, target_display, error=str(e))
            return res

    def _emit_event(
        self,
        event_type: EventType,
        action_id: str,
        action_type: TesterActionType,
        target_display: str,
        error: Optional[str] = None,
    ) -> None:
        payload: dict[str, Any] = {
            "action_id": action_id,
            "action_type": action_type.value,
            "target": target_display,
        }
        if error:
            payload["error"] = error

        if hasattr(self.runtime, "emit_runtime_event"):
            self.runtime.emit_runtime_event(event_type, payload)

    # ----------------------------------------------------------------------
    # Concrete Interaction Methods
    # ----------------------------------------------------------------------

    def move_cursor(self, target: Any, timeout_seconds: float = 30.0) -> InteractionResult:
        """Move cursor to target coordinates or element."""
        def _fn(tgt: InteractionTarget, timeout: float) -> tuple[bool, Optional[str]]:
            return self.session.do_move_cursor(tgt.x or 0.0, tgt.y or 0.0, timeout_seconds=timeout)

        return self._execute_interaction(
            action_type=TesterActionType.MOVE_CURSOR,
            capability=TestingCapability.MOVE_CURSOR,
            target_raw=target,
            executor_fn=_fn,
            timeout_seconds=timeout_seconds,
        )

    def click(self, target: Any, timeout_seconds: float = 30.0) -> InteractionResult:
        """Perform a single click on the target element or coordinates."""
        def _fn(tgt: InteractionTarget, timeout: float) -> tuple[bool, Optional[str]]:
            return self.session.do_click(tgt, double=False, timeout_seconds=timeout)

        return self._execute_interaction(
            action_type=TesterActionType.CLICK,
            capability=TestingCapability.CLICK,
            target_raw=target,
            executor_fn=_fn,
            timeout_seconds=timeout_seconds,
        )

    def double_click(self, target: Any, timeout_seconds: float = 30.0) -> InteractionResult:
        """Perform a double click on the target element or coordinates."""
        def _fn(tgt: InteractionTarget, timeout: float) -> tuple[bool, Optional[str]]:
            return self.session.do_click(tgt, double=True, timeout_seconds=timeout)

        return self._execute_interaction(
            action_type=TesterActionType.DOUBLE_CLICK,
            capability=TestingCapability.CLICK,
            target_raw=target,
            executor_fn=_fn,
            timeout_seconds=timeout_seconds,
        )

    def type_text(
        self,
        target: Any,
        text: str,
        sensitive: bool = False,
        timeout_seconds: float = 30.0,
    ) -> InteractionResult:
        """
        Type text into the target input or element.
        If sensitive=True, the text is redacted in all records and events.
        """
        logged_value = "[REDACTED]" if sensitive else text

        def _fn(tgt: InteractionTarget, timeout: float) -> tuple[bool, Optional[str]]:
            return self.session.do_type_text(tgt, text, sensitive=sensitive, timeout_seconds=timeout)

        return self._execute_interaction(
            action_type=TesterActionType.TYPE,
            capability=TestingCapability.TYPE,
            target_raw=target,
            executor_fn=_fn,
            sensitive=sensitive,
            timeout_seconds=timeout_seconds,
            extra_state={"text_entered": logged_value, "sensitive": sensitive},
        )

    def press_key(self, key: str, target: Optional[Any] = None, timeout_seconds: float = 30.0) -> InteractionResult:
        """Press a keyboard key (e.g. Enter, Tab, Escape, ArrowDown) optionally focused on target."""
        if not key or not str(key).strip():
            raise TesterValidationError("Key name cannot be empty.", field_name="key")

        def _fn(tgt: Optional[InteractionTarget], timeout: float) -> tuple[bool, Optional[str]]:
            return self.session.do_press_key(key, target=tgt, timeout_seconds=timeout)

        return self._execute_interaction(
            action_type=TesterActionType.PRESS_KEY,
            capability=TestingCapability.KEYBOARD_INPUT,
            target_raw=target if target is not None else {"key": key},
            executor_fn=_fn,
            timeout_seconds=timeout_seconds,
            extra_state={"key": key},
        )

    def scroll(
        self,
        direction: str = "vertical",
        amount: int = 100,
        target: Optional[Any] = None,
        timeout_seconds: float = 30.0,
    ) -> InteractionResult:
        """Scroll the active window or target container."""
        norm_dir = direction.lower().strip()
        if norm_dir not in ("vertical", "horizontal", "up", "down", "left", "right"):
            raise TesterValidationError(f"Unsupported scroll direction '{direction}'.", field_name="direction")

        def _fn(tgt: Optional[InteractionTarget], timeout: float) -> tuple[bool, Optional[str]]:
            return self.session.do_scroll(norm_dir, amount, target=tgt, timeout_seconds=timeout)

        return self._execute_interaction(
            action_type=TesterActionType.SCROLL,
            capability=TestingCapability.SCROLL,
            target_raw=target if target is not None else {"direction": norm_dir, "amount": amount},
            executor_fn=_fn,
            timeout_seconds=timeout_seconds,
            extra_state={"direction": norm_dir, "amount": amount},
        )

    def hover(self, target: Any, timeout_seconds: float = 30.0) -> InteractionResult:
        """Hover cursor over an explicit target element."""
        def _fn(tgt: InteractionTarget, timeout: float) -> tuple[bool, Optional[str]]:
            return self.session.do_hover(tgt, timeout_seconds=timeout)

        return self._execute_interaction(
            action_type=TesterActionType.HOVER,
            capability=TestingCapability.HOVER,
            target_raw=target,
            executor_fn=_fn,
            timeout_seconds=timeout_seconds,
        )

    def drag(self, source: Any, destination: Any, timeout_seconds: float = 30.0) -> InteractionResult:
        """Drag from a source target and drop onto a destination target."""
        if source is None:
            raise TesterValidationError("Drag source cannot be None.", field_name="source")
        if destination is None:
            raise TesterValidationError("Drag destination cannot be None.", field_name="destination")

        src_target = normalize_target(source)
        dst_target = normalize_target(destination)

        def _fn(tgt: InteractionTarget, timeout: float) -> tuple[bool, Optional[str]]:
            return self.session.do_drag(src_target, dst_target, timeout_seconds=timeout)

        combined_raw = {
            "source": src_target.to_dict(),
            "destination": dst_target.to_dict(),
        }

        return self._execute_interaction(
            action_type=TesterActionType.DRAG,
            capability=TestingCapability.DRAG,
            target_raw=combined_raw,
            executor_fn=_fn,
            timeout_seconds=timeout_seconds,
            extra_state={"drag_source": src_target.to_dict(), "drag_destination": dst_target.to_dict()},
        )

    def wait(self, duration_seconds: float) -> InteractionResult:
        """
        Execute a deterministic bounded wait.
        Rejects negative or unreasonable/unbounded durations.
        """
        if duration_seconds < 0:
            raise TesterValidationError(
                f"Wait duration cannot be negative: {duration_seconds}s.",
                field_name="duration_seconds",
            )
        if duration_seconds > 60.0:
            raise TesterValidationError(
                f"Wait duration exceeds maximum permitted limit (60.0s): {duration_seconds}s.",
                field_name="duration_seconds",
            )

        def _fn(tgt: Optional[InteractionTarget], timeout: float) -> tuple[bool, Optional[str]]:
            return self.session.do_wait(duration_seconds)

        return self._execute_interaction(
            action_type=TesterActionType.WAIT,
            capability=TestingCapability.WAIT,
            target_raw={"duration_seconds": duration_seconds},
            executor_fn=_fn,
            timeout_seconds=duration_seconds + 5.0,
            extra_state={"duration_seconds": duration_seconds},
        )

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import math
from typing import Any, Optional, Sequence

from core.tester.contracts.finding import TesterDefect
from core.tester.contracts.identifiers import (
    new_scroll_evaluation_id,
    validate_scroll_evaluation_id,
)
from core.tester.errors import (
    TesterBoundaryViolationError,
    TesterLineageError,
    TesterValidationError,
)
from core.tester.types import (
    DefectSeverity,
    DefectType,
    ScrollDirection,
    ScrollEvaluationStatus,
)

logger = logging.getLogger("AutonomOS.Tester.ScrollContracts")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ScrollPosition:
    """
    Observable scroll position and boundaries of a scrollable surface or window.
    """
    scroll_x: float = 0.0
    scroll_y: float = 0.0
    max_scroll_x: float = 0.0
    max_scroll_y: float = 0.0
    viewport_width: float = 1024.0
    viewport_height: float = 768.0
    timestamp: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name, val in (
            ("scroll_x", self.scroll_x),
            ("scroll_y", self.scroll_y),
            ("max_scroll_x", self.max_scroll_x),
            ("max_scroll_y", self.max_scroll_y),
            ("viewport_width", self.viewport_width),
            ("viewport_height", self.viewport_height),
        ):
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise TesterValidationError(
                    f"ScrollPosition '{name}' must be numeric, got {type(val).__name__}."
                )
            if not math.isfinite(val):
                raise TesterValidationError(
                    f"ScrollPosition '{name}' must be finite, got {val}."
                )

    def delta_to(self, other: ScrollPosition) -> tuple[float, float]:
        """Compute (delta_x, delta_y) from self to other."""
        if not isinstance(other, ScrollPosition):
            raise TesterValidationError(f"Expected ScrollPosition instance, got {type(other).__name__}.")
        return (other.scroll_x - self.scroll_x, other.scroll_y - self.scroll_y)

    def distance_to(self, other: ScrollPosition) -> float:
        """Calculate Euclidean distance between two scroll positions."""
        dx, dy = self.delta_to(other)
        return math.hypot(dx, dy)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scroll_x": float(self.scroll_x),
            "scroll_y": float(self.scroll_y),
            "max_scroll_x": float(self.max_scroll_x),
            "max_scroll_y": float(self.max_scroll_y),
            "viewport_width": float(self.viewport_width),
            "viewport_height": float(self.viewport_height),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScrollPosition:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for ScrollPosition, got {type(data).__name__}.")
        return cls(
            scroll_x=float(data.get("scroll_x", 0.0)),
            scroll_y=float(data.get("scroll_y", 0.0)),
            max_scroll_x=float(data.get("max_scroll_x", 0.0)),
            max_scroll_y=float(data.get("max_scroll_y", 0.0)),
            viewport_width=float(data.get("viewport_width", 1024.0)),
            viewport_height=float(data.get("viewport_height", 768.0)),
            timestamp=str(data.get("timestamp", utc_now())),
        )


@dataclass(frozen=True)
class ScrollBudget:
    """
    Finite bounded execution budget for scrolling evaluations.
    Enforces maximum actions, maximum scroll distance, and maximum duration.
    Guarantees that infinite scrolling loops are impossible.
    """
    max_actions: int = 10
    max_distance_px: float = 5000.0
    max_duration_seconds: float = 30.0
    step_size_px: float = 300.0

    def __post_init__(self) -> None:
        if self.max_actions <= 0:
            raise TesterValidationError(f"max_actions must be strictly positive, got {self.max_actions}.")
        if self.max_distance_px <= 0:
            raise TesterValidationError(f"max_distance_px must be strictly positive, got {self.max_distance_px}.")
        if self.max_duration_seconds <= 0:
            raise TesterValidationError(f"max_duration_seconds must be positive, got {self.max_duration_seconds}.")
        if self.step_size_px <= 0:
            raise TesterValidationError(f"step_size_px must be positive, got {self.step_size_px}.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_actions": self.max_actions,
            "max_distance_px": self.max_distance_px,
            "max_duration_seconds": self.max_duration_seconds,
            "step_size_px": self.step_size_px,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScrollBudget:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for ScrollBudget, got {type(data).__name__}.")
        return cls(
            max_actions=int(data.get("max_actions", 10)),
            max_distance_px=float(data.get("max_distance_px", 5000.0)),
            max_duration_seconds=float(data.get("max_duration_seconds", 30.0)),
            step_size_px=float(data.get("step_size_px", 300.0)),
        )


@dataclass
class ScrollAssertion:
    """
    Specification of an explicit scrolling check or expectation.
    """
    __test__ = False
    check_id: str = field(default_factory=new_scroll_evaluation_id)
    direction: ScrollDirection = ScrollDirection.VERTICAL
    target_element_id: Optional[str] = None
    container_id: Optional[str] = None
    expected_behavior: str = ""
    is_intentional_overflow: bool = False
    expected_scrollable: bool = True
    budget: ScrollBudget = field(default_factory=ScrollBudget)
    test_case_id: Optional[str] = None
    test_step_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_scroll_evaluation_id(self.check_id)
        if isinstance(self.direction, str):
            try:
                self.direction = ScrollDirection(self.direction.upper())
            except (ValueError, KeyError):
                self.direction = ScrollDirection.VERTICAL

        if isinstance(self.budget, dict):
            self.budget = ScrollBudget.from_dict(self.budget)

        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "direction": self.direction.value,
            "target_element_id": self.target_element_id,
            "container_id": self.container_id,
            "expected_behavior": self.expected_behavior,
            "is_intentional_overflow": self.is_intentional_overflow,
            "expected_scrollable": self.expected_scrollable,
            "budget": self.budget.to_dict(),
            "test_case_id": self.test_case_id,
            "test_step_id": self.test_step_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScrollAssertion:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for ScrollAssertion, got {type(data).__name__}.")
        raw_dir = data.get("direction", ScrollDirection.VERTICAL.value)
        try:
            direction = ScrollDirection(str(raw_dir).upper())
        except (ValueError, KeyError):
            direction = ScrollDirection.VERTICAL

        raw_budget = data.get("budget", {})
        budget = ScrollBudget.from_dict(raw_budget) if isinstance(raw_budget, dict) else ScrollBudget()

        return cls(
            check_id=str(data.get("check_id") or new_scroll_evaluation_id()),
            direction=direction,
            target_element_id=data.get("target_element_id"),
            container_id=data.get("container_id"),
            expected_behavior=str(data.get("expected_behavior", "")),
            is_intentional_overflow=bool(data.get("is_intentional_overflow", False)),
            expected_scrollable=bool(data.get("expected_scrollable", True)),
            budget=budget,
            test_case_id=data.get("test_case_id"),
            test_step_id=data.get("test_step_id"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ScrollEvaluationResult:
    """
    Authoritative outcome of a scrolling or overflow evaluation.
    Contains measured scroll metrics, target element accessibility, and any resulting defect.
    """
    __test__ = False
    evaluation_id: str
    status: ScrollEvaluationStatus = ScrollEvaluationStatus.PASS
    initial_position: Optional[ScrollPosition] = None
    final_position: Optional[ScrollPosition] = None
    delta_x: float = 0.0
    delta_y: float = 0.0
    actions_performed: int = 0
    distance_scrolled_px: float = 0.0
    duration_seconds: float = 0.0
    target_reached: Optional[bool] = None
    target_element_id: Optional[str] = None
    has_unexpected_overflow: bool = False
    description: str = ""
    defect: Optional[TesterDefect] = None
    evidence_ids: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    test_case_id: Optional[str] = None
    test_step_id: Optional[str] = None
    evaluated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_scroll_evaluation_id(self.evaluation_id)
        if isinstance(self.status, str):
            try:
                self.status = ScrollEvaluationStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = ScrollEvaluationStatus.UNVERIFIED

        if isinstance(self.initial_position, dict):
            self.initial_position = ScrollPosition.from_dict(self.initial_position)
        if isinstance(self.final_position, dict):
            self.final_position = ScrollPosition.from_dict(self.final_position)

        self.evidence_ids = list(self.evidence_ids)
        self.trace = dict(self.trace)

    @property
    def is_pass(self) -> bool:
        return self.status == ScrollEvaluationStatus.PASS

    @property
    def is_fail(self) -> bool:
        return self.status == ScrollEvaluationStatus.FAIL

    @property
    def is_blocked(self) -> bool:
        return self.status == ScrollEvaluationStatus.BLOCKED

    @property
    def is_unverified(self) -> bool:
        return self.status == ScrollEvaluationStatus.UNVERIFIED

    @property
    def is_skipped(self) -> bool:
        return self.status == ScrollEvaluationStatus.SKIPPED

    def apply_fix(self, *args, **kwargs) -> Any:
        """Zero fixing guard: Scroll evaluation does NOT modify source code or attempt auto repairs."""
        raise TesterBoundaryViolationError(
            action="SCROLL_AUTO_FIX",
            reason=(
                f"ScrollEvaluationResult '{self.evaluation_id}' is an evaluation object. "
                "Tester does not perform automatic fixes on product or source code."
            ),
        )

    def auto_fix(self, *args, **kwargs) -> Any:
        return self.apply_fix(*args, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "status": self.status.value,
            "initial_position": self.initial_position.to_dict() if self.initial_position else None,
            "final_position": self.final_position.to_dict() if self.final_position else None,
            "delta_x": float(self.delta_x),
            "delta_y": float(self.delta_y),
            "actions_performed": int(self.actions_performed),
            "distance_scrolled_px": float(self.distance_scrolled_px),
            "duration_seconds": float(self.duration_seconds),
            "target_reached": self.target_reached,
            "target_element_id": self.target_element_id,
            "has_unexpected_overflow": self.has_unexpected_overflow,
            "description": self.description,
            "defect": self.defect.to_dict() if self.defect and hasattr(self.defect, "to_dict") else None,
            "evidence_ids": list(self.evidence_ids),
            "trace": dict(self.trace),
            "test_case_id": self.test_case_id,
            "test_step_id": self.test_step_id,
            "evaluated_at": self.evaluated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScrollEvaluationResult:
        if not isinstance(data, dict):
            raise TesterValidationError(f"Expected dict for ScrollEvaluationResult, got {type(data).__name__}.")

        raw_status = data.get("status", ScrollEvaluationStatus.PASS.value)
        try:
            status = ScrollEvaluationStatus(str(raw_status).upper())
        except (ValueError, KeyError):
            status = ScrollEvaluationStatus.UNVERIFIED

        init_pos = data.get("initial_position")
        if isinstance(init_pos, dict):
            init_pos = ScrollPosition.from_dict(init_pos)

        final_pos = data.get("final_position")
        if isinstance(final_pos, dict):
            final_pos = ScrollPosition.from_dict(final_pos)

        defect_data = data.get("defect")
        defect_obj = TesterDefect.from_dict(defect_data) if isinstance(defect_data, dict) else None

        return cls(
            evaluation_id=str(data.get("evaluation_id", "")),
            status=status,
            initial_position=init_pos,
            final_position=final_pos,
            delta_x=float(data.get("delta_x", 0.0)),
            delta_y=float(data.get("delta_y", 0.0)),
            actions_performed=int(data.get("actions_performed", 0)),
            distance_scrolled_px=float(data.get("distance_scrolled_px", 0.0)),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            target_reached=data.get("target_reached"),
            target_element_id=data.get("target_element_id"),
            has_unexpected_overflow=bool(data.get("has_unexpected_overflow", False)),
            description=str(data.get("description", "")),
            defect=defect_obj,
            evidence_ids=list(data.get("evidence_ids", [])),
            trace=dict(data.get("trace", {})),
            test_case_id=data.get("test_case_id"),
            test_step_id=data.get("test_step_id"),
            evaluated_at=str(data.get("evaluated_at", utc_now())),
        )

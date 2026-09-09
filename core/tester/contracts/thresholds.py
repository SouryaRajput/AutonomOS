from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from core.tester.errors import TesterValidationError


@dataclass
class QualityThresholds:
    """
    Deterministic quality thresholds configured by Manager.
    Defines what constitutes acceptable quality criteria for product evaluation.
    
    Invariants:
    - Pure deterministic boundaries (no AI scoring or fuzzy rules).
    - Defect counts must be non-negative integers.
    - Acceptance pass rate must be between 0.0 (0%) and 1.0 (100%).
    - Performance threshold must be non-negative if specified.
    """
    __test__ = False
    max_critical_defects: int = 0
    max_high_defects: int = 0
    min_acceptance_pass_rate: float = 1.0
    max_performance_threshold_ms: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate threshold parameters."""
        if not isinstance(self.max_critical_defects, int) or self.max_critical_defects < 0:
            raise TesterValidationError(
                f"max_critical_defects must be a non-negative integer, got {self.max_critical_defects}.",
                field_name="max_critical_defects",
            )
        if not isinstance(self.max_high_defects, int) or self.max_high_defects < 0:
            raise TesterValidationError(
                f"max_high_defects must be a non-negative integer, got {self.max_high_defects}.",
                field_name="max_high_defects",
            )
        if not (0.0 <= float(self.min_acceptance_pass_rate) <= 1.0):
            raise TesterValidationError(
                f"min_acceptance_pass_rate must be between 0.0 and 1.0, got {self.min_acceptance_pass_rate}.",
                field_name="min_acceptance_pass_rate",
            )
        if self.max_performance_threshold_ms is not None:
            if float(self.max_performance_threshold_ms) < 0:
                raise TesterValidationError(
                    f"max_performance_threshold_ms must be non-negative, got {self.max_performance_threshold_ms}.",
                    field_name="max_performance_threshold_ms",
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_critical_defects": self.max_critical_defects,
            "max_high_defects": self.max_high_defects,
            "min_acceptance_pass_rate": self.min_acceptance_pass_rate,
            "max_performance_threshold_ms": self.max_performance_threshold_ms,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QualityThresholds:
        return cls(
            max_critical_defects=int(data.get("max_critical_defects", 0)),
            max_high_defects=int(data.get("max_high_defects", 0)),
            min_acceptance_pass_rate=float(data.get("min_acceptance_pass_rate", 1.0)),
            max_performance_threshold_ms=(
                float(data["max_performance_threshold_ms"])
                if data.get("max_performance_threshold_ms") is not None
                else None
            ),
            metadata=dict(data.get("metadata", {})),
        )

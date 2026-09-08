from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from typing import Any, Callable, Optional, Sequence, Union

from core.enums import RiskLevel
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.failure import ProgrammerFailure
from core.programmer.contracts.identifiers import (
    new_retry_attempt_id,
    validate_execution_id,
    validate_failure_id,
    validate_retry_attempt_id,
    validate_work_order_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    ProgrammerExecutionStatus,
    ProgrammerFailureCategory,
    ProgrammerFailureSeverity,
    RecoveryDisposition,
)

logger = logging.getLogger("AutonomOS.Programmer.Retry")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Default sets of retryable and non-retryable categories
DEFAULT_RETRYABLE_CATEGORIES: tuple[ProgrammerFailureCategory, ...] = (
    ProgrammerFailureCategory.AGENT_STARTUP,
    ProgrammerFailureCategory.AGENT_CRASH,
    ProgrammerFailureCategory.RESOURCE,
)

DEFAULT_NON_RETRYABLE_CATEGORIES: tuple[ProgrammerFailureCategory, ...] = (
    ProgrammerFailureCategory.PERMISSION,
    ProgrammerFailureCategory.SCOPE,
    ProgrammerFailureCategory.CANCELLATION,
    ProgrammerFailureCategory.BUDGET,
    ProgrammerFailureCategory.TIMEOUT,
    ProgrammerFailureCategory.VERIFICATION_FAILURE,
    ProgrammerFailureCategory.MISSING_CONTEXT,
    ProgrammerFailureCategory.DEPENDENCY,
    ProgrammerFailureCategory.INTERNAL,
    ProgrammerFailureCategory.COMMAND_FAILURE,
    ProgrammerFailureCategory.UNKNOWN,
)


# ==============================================================================
# 1. Retry Attempt Record
# ==============================================================================


@dataclass
class RetryAttemptRecord:
    """
    Strongly-typed auditable record of an individual execution retry attempt.
    
    Guarantees:
    - Unique attempt_id prefixed with 'pretry-'.
    - Maintains causal lineage back to failure_id, execution_id, and work_order_id.
    - Records the attempt number (1, 2, ...), calculated backoff delay, and action taken.
    - Retains diagnostic trace and metadata.
    """
    attempt_id: str
    execution_id: str
    work_order_id: str
    failure_id: str
    attempt_number: int
    failure_category: ProgrammerFailureCategory
    delay_seconds: float
    action_taken: str  # e.g., "RETRY_INITIATED", "EXHAUSTED", "ABORTED_CANCELLED", "NON_RETRYABLE"
    timestamp: str = field(default_factory=utc_now)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.failure_category, str):
            try:
                self.failure_category = ProgrammerFailureCategory(self.failure_category.upper())
            except ValueError:
                self.failure_category = ProgrammerFailureCategory.UNKNOWN

        if not self.trace:
            self.trace = {
                "attempt_id": self.attempt_id,
                "execution_id": self.execution_id,
                "work_order_id": self.work_order_id,
                "failure_id": self.failure_id,
                "attempt_number": self.attempt_number,
                "action_taken": self.action_taken,
                "delay_seconds": self.delay_seconds,
            }

    def validate(self) -> None:
        """Validate structural integrity and lineage constraints."""
        validate_retry_attempt_id(self.attempt_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        validate_failure_id(self.failure_id)

        if self.attempt_number < 1:
            raise ProgrammerValidationError(
                f"attempt_number must be >= 1, got {self.attempt_number}.",
                field_name="attempt_number",
            )
        if self.delay_seconds < 0:
            raise ProgrammerValidationError(
                f"delay_seconds must be non-negative, got {self.delay_seconds}.",
                field_name="delay_seconds",
            )
        if not self.action_taken or not str(self.action_taken).strip():
            raise ProgrammerValidationError("action_taken must be a non-empty string.", field_name="action_taken")

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "attempt_id": self.attempt_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "failure_id": self.failure_id,
            "attempt_number": self.attempt_number,
            "failure_category": self.failure_category.value,
            "delay_seconds": self.delay_seconds,
            "action_taken": self.action_taken,
            "timestamp": self.timestamp,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetryAttemptRecord:
        """Reconstruct a validated RetryAttemptRecord from dictionary."""
        record = cls(
            attempt_id=str(data.get("attempt_id", "")),
            execution_id=str(data.get("execution_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            failure_id=str(data.get("failure_id", "")),
            attempt_number=int(data.get("attempt_number", 1)),
            failure_category=ProgrammerFailureCategory(str(data.get("failure_category", "UNKNOWN")).upper()),
            delay_seconds=float(data.get("delay_seconds", 0.0)),
            action_taken=str(data.get("action_taken", "RETRY_INITIATED")),
            timestamp=str(data.get("timestamp", utc_now())),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )
        record.validate()
        return record


# ==============================================================================
# 2. Retry Policy
# ==============================================================================


@dataclass
class RetryPolicy:
    """
    Deterministic configuration governing bounded retry behavior and backoff.
    
    Guarantees:
    - Strictly separates retryable from non-retryable failure categories.
    - Explicitly bounds retries via max_retries and total_retry_budget.
    - Calculates deterministic exponential backoff bounded by max_delay_seconds.
    - Forbids retrying scope, permission, cancellation, budget, or verification defects.
    """
    max_retries: int = 2
    initial_delay_seconds: float = 1.0
    backoff_factor: float = 2.0
    max_delay_seconds: float = 30.0
    total_retry_budget: int = 3
    retryable_categories: tuple[ProgrammerFailureCategory, ...] = DEFAULT_RETRYABLE_CATEGORIES
    non_retryable_categories: tuple[ProgrammerFailureCategory, ...] = DEFAULT_NON_RETRYABLE_CATEGORIES

    def __post_init__(self) -> None:
        # Coerce any string categories
        ret_cats: list[ProgrammerFailureCategory] = []
        for c in self.retryable_categories:
            if isinstance(c, str):
                try:
                    ret_cats.append(ProgrammerFailureCategory(c.upper()))
                except ValueError:
                    pass
            else:
                ret_cats.append(c)
        self.retryable_categories = tuple(ret_cats)

        non_ret_cats: list[ProgrammerFailureCategory] = []
        for c in self.non_retryable_categories:
            if isinstance(c, str):
                try:
                    non_ret_cats.append(ProgrammerFailureCategory(c.upper()))
                except ValueError:
                    pass
            else:
                non_ret_cats.append(c)
        self.non_retryable_categories = tuple(non_ret_cats)

        self.validate()

    def validate(self) -> None:
        """Validate bounds and ensure no forbidden categories are marked retryable."""
        if self.max_retries < 0:
            raise ProgrammerValidationError("max_retries cannot be negative.", field_name="max_retries")
        if self.total_retry_budget < 0:
            raise ProgrammerValidationError("total_retry_budget cannot be negative.", field_name="total_retry_budget")
        if self.initial_delay_seconds <= 0:
            raise ProgrammerValidationError("initial_delay_seconds must be positive.", field_name="initial_delay_seconds")
        if self.backoff_factor < 1.0:
            raise ProgrammerValidationError("backoff_factor must be >= 1.0.", field_name="backoff_factor")
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ProgrammerValidationError("max_delay_seconds cannot be less than initial_delay_seconds.", field_name="max_delay_seconds")

        # Critical Invariant: Forbidden categories must NEVER be in retryable_categories
        forbidden = (
            ProgrammerFailureCategory.PERMISSION,
            ProgrammerFailureCategory.SCOPE,
            ProgrammerFailureCategory.CANCELLATION,
            ProgrammerFailureCategory.BUDGET,
            ProgrammerFailureCategory.VERIFICATION_FAILURE,
        )
        for f in forbidden:
            if f in self.retryable_categories:
                raise ProgrammerValidationError(
                    f"Policy contradiction: {f.value} cannot be in retryable_categories. "
                    "Scope, permissions, cancellation, budgets, and verification failures cannot be retried.",
                    field_name="retryable_categories",
                )

    def calculate_backoff(self, attempt: int) -> float:
        """
        Calculate deterministic exponential backoff delay for given attempt number (1-indexed).
        delay = min(initial_delay * (backoff_factor ** (attempt - 1)), max_delay)
        """
        if attempt < 1:
            raise ValueError(f"attempt must be >= 1, got {attempt}")
        delay = self.initial_delay_seconds * (self.backoff_factor ** (attempt - 1))
        return min(float(delay), self.max_delay_seconds)

    def is_category_retryable(self, category: Union[ProgrammerFailureCategory, str]) -> bool:
        """Check if a failure category is permitted for retry under this policy."""
        cat = category if isinstance(category, ProgrammerFailureCategory) else ProgrammerFailureCategory(str(category).upper())
        return (cat in self.retryable_categories) and (cat not in self.non_retryable_categories)

    @classmethod
    def from_work_order(cls, work_order: Optional[ProgrammerWorkOrder]) -> RetryPolicy:
        """
        Derive a validated RetryPolicy reflecting WorkOrder risk level and metadata.
        """
        if work_order is None:
            return cls()

        risk = getattr(work_order, "risk_level", RiskLevel.LOW)
        max_retries = 2
        total_budget = 3

        # Critical risk tasks disable execution retries by default
        if isinstance(risk, RiskLevel) and risk == RiskLevel.CRITICAL:
            max_retries = 0
            total_budget = 0

        meta_policy = dict(getattr(work_order, "metadata", {}).get("retry_policy", {}))

        return cls(
            max_retries=int(meta_policy.get("max_retries", max_retries)),
            initial_delay_seconds=float(meta_policy.get("initial_delay_seconds", 1.0)),
            backoff_factor=float(meta_policy.get("backoff_factor", 2.0)),
            max_delay_seconds=float(meta_policy.get("max_delay_seconds", 30.0)),
            total_retry_budget=int(meta_policy.get("total_retry_budget", total_budget)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_retries": self.max_retries,
            "initial_delay_seconds": self.initial_delay_seconds,
            "backoff_factor": self.backoff_factor,
            "max_delay_seconds": self.max_delay_seconds,
            "total_retry_budget": self.total_retry_budget,
            "retryable_categories": [c.value for c in self.retryable_categories],
            "non_retryable_categories": [c.value for c in self.non_retryable_categories],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetryPolicy:
        ret_cats = tuple(ProgrammerFailureCategory(c.upper()) for c in data.get("retryable_categories", [])) or DEFAULT_RETRYABLE_CATEGORIES
        non_ret_cats = tuple(ProgrammerFailureCategory(c.upper()) for c in data.get("non_retryable_categories", [])) or DEFAULT_NON_RETRYABLE_CATEGORIES

        return cls(
            max_retries=int(data.get("max_retries", 2)),
            initial_delay_seconds=float(data.get("initial_delay_seconds", 1.0)),
            backoff_factor=float(data.get("backoff_factor", 2.0)),
            max_delay_seconds=float(data.get("max_delay_seconds", 30.0)),
            total_retry_budget=int(data.get("total_retry_budget", 3)),
            retryable_categories=ret_cats,
            non_retryable_categories=non_ret_cats,
        )


# ==============================================================================
# 3. Retry Coordinator
# ==============================================================================


class RetryCoordinator:
    """
    Deterministic coordinator evaluating retry eligibility, managing backoff delays,
    and maintaining strictly isolated retry attempt records.
    
    Critical Invariants:
    1. STRICT ELIGIBILITY: A retry occurs ONLY when:
       - Failure is classified retryable.
       - Retry budget remains (category & total).
       - Execution is not cancelled.
       - WorkOrder remains valid.
       - Retry does not require expanded authority.
    2. RETRY VS CORRECTION DISTINCTION:
       - RETRY: re-attempts execution after infrastructure/runtime failure.
       - CORRECTION: prompts coding agent to correct failed verification.
       - Neither resets the other's budget.
    3. BOUNDED BACKOFF:
       - Deterministic backoff calculation without complex scheduling.
       - Cancellation during backoff halts immediately.
    """

    def __init__(self, default_policy: Optional[RetryPolicy] = None) -> None:
        self.default_policy = default_policy or RetryPolicy()
        self._retry_attempts_by_execution: dict[str, list[RetryAttemptRecord]] = {}
        self._category_retry_counts: dict[str, dict[ProgrammerFailureCategory, int]] = {}
        self._correction_counts: dict[str, int] = {}

    def can_retry(
        self,
        failure: ProgrammerFailure,
        execution: ProgrammerExecution,
        work_order: Optional[ProgrammerWorkOrder] = None,
        policy: Optional[RetryPolicy] = None,
    ) -> tuple[bool, str]:
        """
        Evaluate whether an execution failure is permitted to retry.
        Returns (can_retry: bool, reason: str).
        """
        if failure is None:
            raise ProgrammerValidationError("ProgrammerFailure cannot be None.")
        if execution is None:
            raise ProgrammerValidationError("ProgrammerExecution cannot be None.")

        failure.validate()
        active_policy = policy or (RetryPolicy.from_work_order(work_order) if work_order else self.default_policy)
        active_policy.validate()

        # Rule 1: Authority Guard (NEVER retry permission/scope violations)
        if failure.category in (ProgrammerFailureCategory.PERMISSION, ProgrammerFailureCategory.SCOPE):
            return False, f"Authority expansion forbidden: {failure.category.value} cannot be retried."

        # Rule 2: Classification Check
        if not failure.retryable:
            return False, f"Failure '{failure.failure_id}' is explicitly marked non-retryable."

        if not active_policy.is_category_retryable(failure.category):
            return False, f"Category '{failure.category.value}' is designated non-retryable by policy."

        # Rule 3: Execution Cancellation Guard
        if execution.status == ProgrammerExecutionStatus.CANCELLED or execution.metadata.get("cancellation_requested"):
            return False, "Execution has been cancelled; cannot initiate retry."

        # Rule 4: WorkOrder Validity & Lineage
        if work_order is not None:
            if failure.work_order_id != work_order.work_order_id:
                raise ProgrammerLineageError(
                    f"Lineage mismatch: failure work_order_id '{failure.work_order_id}' "
                    f"does not match work order '{work_order.work_order_id}'."
                )
            if execution.work_order_id != work_order.work_order_id:
                raise ProgrammerLineageError(
                    f"Lineage mismatch: execution work_order_id '{execution.work_order_id}' "
                    f"does not match work order '{work_order.work_order_id}'."
                )
            try:
                work_order.validate()
            except Exception as wo_err:
                return False, f"WorkOrder validation failed: {wo_err}"

        # Rule 5: Budget Exhaustion Checks
        exec_id = execution.execution_id
        cat = failure.category

        cat_count = self.get_category_retry_count(exec_id, cat)
        total_count = self.get_total_retry_count(exec_id)

        if cat_count >= active_policy.max_retries:
            return False, f"Category retry budget exhausted ({cat_count}/{active_policy.max_retries})."

        if total_count >= active_policy.total_retry_budget:
            return False, f"Total execution retry budget exhausted ({total_count}/{active_policy.total_retry_budget})."

        return True, f"Retry permitted ({cat_count + 1}/{active_policy.max_retries} for category {cat.value})."

    def execute_backoff(
        self,
        attempt_number: int,
        policy: Optional[RetryPolicy] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
        sleep_func: Optional[Callable[[float], None]] = None,
    ) -> float:
        """
        Compute and execute backoff delay for the given attempt number.
        If cancellation is signaled before or during backoff, aborts immediately.
        
        Returns:
            Calculated delay in seconds.
            
        Raises:
            ProgrammerError: If cancellation occurs before or during backoff.
        """
        active_policy = policy or self.default_policy
        delay = active_policy.calculate_backoff(attempt_number)

        # Check cancellation before sleep
        if is_cancelled and is_cancelled():
            raise ProgrammerError(f"Retry backoff aborted: cancellation requested prior to backoff delay ({delay}s).")

        # Execute delay
        if sleep_func:
            sleep_func(delay)

        # Check cancellation after sleep
        if is_cancelled and is_cancelled():
            raise ProgrammerError(f"Retry backoff aborted: cancellation requested during backoff delay ({delay}s).")

        return delay

    def record_retry_attempt(
        self,
        failure: ProgrammerFailure,
        execution: ProgrammerExecution,
        work_order: ProgrammerWorkOrder,
        delay_seconds: float,
        action_taken: str = "RETRY_INITIATED",
        policy: Optional[RetryPolicy] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> RetryAttemptRecord:
        """
        Record an auditable RetryAttemptRecord and update internal counters.
        """
        validate_failure_id(failure.failure_id)
        validate_execution_id(execution.execution_id)
        validate_work_order_id(work_order.work_order_id)

        exec_id = execution.execution_id
        cat = failure.category

        cat_counts = self._category_retry_counts.setdefault(exec_id, {})
        current_cat_count = cat_counts.get(cat, 0)
        next_cat_count = current_cat_count + 1
        cat_counts[cat] = next_cat_count

        record = RetryAttemptRecord(
            attempt_id=new_retry_attempt_id(),
            execution_id=exec_id,
            work_order_id=work_order.work_order_id,
            failure_id=failure.failure_id,
            attempt_number=next_cat_count,
            failure_category=cat,
            delay_seconds=delay_seconds,
            action_taken=action_taken,
            timestamp=utc_now(),
            trace={
                "execution_id": exec_id,
                "work_order_id": work_order.work_order_id,
                "failure_id": failure.failure_id,
                "category_attempt": next_cat_count,
                "total_attempts": self.get_total_retry_count(exec_id) + 1,
            },
            metadata=dict(metadata or {}),
        )
        record.validate()
        self._retry_attempts_by_execution.setdefault(exec_id, []).append(record)

        logger.info(
            f"Recorded retry attempt {next_cat_count} for execution '{exec_id}' "
            f"category {cat.value}: {action_taken} (delay {delay_seconds:.2f}s)"
        )
        return record

    def record_correction_turn(self, execution_id: str) -> int:
        """
        Record a verification correction turn.
        CRITICAL: Never resets or alters execution retry counts.
        """
        count = self._correction_counts.get(execution_id, 0) + 1
        self._correction_counts[execution_id] = count
        return count

    def get_total_retry_count(self, execution_id: str) -> int:
        """Get total execution retry attempts recorded for an execution."""
        return len(self._retry_attempts_by_execution.get(execution_id, []))

    def get_category_retry_count(self, execution_id: str, category: ProgrammerFailureCategory) -> int:
        """Get number of retry attempts recorded for a specific category."""
        return self._category_retry_counts.get(execution_id, {}).get(category, 0)

    def get_correction_count(self, execution_id: str) -> int:
        """Get total correction turns recorded for an execution."""
        return self._correction_counts.get(execution_id, 0)

    def get_attempts(self, execution_id: str) -> list[RetryAttemptRecord]:
        """Retrieve all retry attempt records for an execution."""
        return list(self._retry_attempts_by_execution.get(execution_id, []))

    def reset(self, execution_id: Optional[str] = None) -> None:
        """Reset retry tracking for a specific execution or all executions."""
        if execution_id:
            self._retry_attempts_by_execution.pop(execution_id, None)
            self._category_retry_counts.pop(execution_id, None)
            self._correction_counts.pop(execution_id, None)
        else:
            self._retry_attempts_by_execution.clear()
            self._category_retry_counts.clear()
            self._correction_counts.clear()

from __future__ import annotations

import pytest

from core.enums import RiskLevel
from core.programmer.contracts.execution import ProgrammerExecution
from core.programmer.contracts.failure import ProgrammerFailure
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_failure_id,
    new_retry_attempt_id,
    new_work_order_id,
    validate_retry_attempt_id,
)
from core.programmer.contracts.retry import (
    RetryAttemptRecord,
    RetryCoordinator,
    RetryPolicy,
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
)


@pytest.fixture
def base_context():
    wo_id = new_work_order_id()
    exec_id = new_execution_id()
    work_order = ProgrammerWorkOrder(
        work_order_id=wo_id,
        manager_task_id="task-retry-001",
        project_id="proj-retry",
        correlation_id="corr-retry",
        objective="Verify retry and backoff policy",
        iteration_budget=5,
    )
    execution = ProgrammerExecution(
        execution_id=exec_id,
        work_order_id=wo_id,
        task_id="task-retry-001",
        project_id="proj-retry",
        correlation_id="corr-retry",
        status=ProgrammerExecutionStatus.RUNNING,
    )
    return work_order, execution


# ==============================================================================
# 1. First Retry & Backoff Calculation Tests
# ==============================================================================


def test_first_retry_permitted(base_context):
    work_order, execution = base_context
    coordinator = RetryCoordinator()
    policy = RetryPolicy(
        max_retries=2,
        initial_delay_seconds=1.5,
        backoff_factor=2.0,
        max_delay_seconds=20.0,
    )

    failure = ProgrammerFailure.create(
        execution_id=execution.execution_id,
        work_order_id=work_order.work_order_id,
        category=ProgrammerFailureCategory.AGENT_CRASH,
        message="Process terminated with exit code 1",
        retryable=True,
    )

    can_ret, reason = coordinator.can_retry(
        failure=failure,
        execution=execution,
        work_order=work_order,
        policy=policy,
    )
    assert can_ret is True
    assert "Retry permitted" in reason

    # Calculate backoff for attempt 1
    delay = policy.calculate_backoff(attempt=1)
    assert delay == 1.5

    # Record attempt
    record = coordinator.record_retry_attempt(
        failure=failure,
        execution=execution,
        work_order=work_order,
        delay_seconds=delay,
        action_taken="RETRY_INITIATED",
        policy=policy,
    )
    assert record.attempt_number == 1
    assert record.delay_seconds == 1.5
    assert record.action_taken == "RETRY_INITIATED"
    validate_retry_attempt_id(record.attempt_id)
    assert coordinator.get_category_retry_count(execution.execution_id, ProgrammerFailureCategory.AGENT_CRASH) == 1
    assert coordinator.get_total_retry_count(execution.execution_id) == 1


def test_backoff_calculation():
    policy = RetryPolicy(
        initial_delay_seconds=1.0,
        backoff_factor=2.0,
        max_delay_seconds=10.0,
    )

    assert policy.calculate_backoff(1) == 1.0
    assert policy.calculate_backoff(2) == 2.0
    assert policy.calculate_backoff(3) == 4.0
    assert policy.calculate_backoff(4) == 8.0
    assert policy.calculate_backoff(5) == 10.0  # Capped at max_delay_seconds
    assert policy.calculate_backoff(10) == 10.0  # Still capped

    with pytest.raises(ValueError, match="attempt must be >= 1"):
        policy.calculate_backoff(0)


# ==============================================================================
# 2. Repeated Retries & Budget Exhaustion Tests
# ==============================================================================


def test_repeated_retry_failures_and_exhaustion(base_context):
    work_order, execution = base_context
    coordinator = RetryCoordinator()
    policy = RetryPolicy(
        max_retries=2,
        initial_delay_seconds=1.0,
        backoff_factor=3.0,
        max_delay_seconds=30.0,
        total_retry_budget=5,
    )

    # Attempt 1
    f1 = ProgrammerFailure.create(
        execution_id=execution.execution_id,
        work_order_id=work_order.work_order_id,
        category=ProgrammerFailureCategory.AGENT_CRASH,
        message="Crash 1",
        retryable=True,
    )
    can1, _ = coordinator.can_retry(f1, execution, work_order, policy)
    assert can1 is True
    delay1 = policy.calculate_backoff(1)
    assert delay1 == 1.0
    coordinator.record_retry_attempt(f1, execution, work_order, delay_seconds=delay1)

    # Attempt 2
    f2 = ProgrammerFailure.create(
        execution_id=execution.execution_id,
        work_order_id=work_order.work_order_id,
        category=ProgrammerFailureCategory.AGENT_CRASH,
        message="Crash 2",
        retryable=True,
    )
    can2, _ = coordinator.can_retry(f2, execution, work_order, policy)
    assert can2 is True
    delay2 = policy.calculate_backoff(2)
    assert delay2 == 3.0
    coordinator.record_retry_attempt(f2, execution, work_order, delay_seconds=delay2)

    # Attempt 3: Category max_retries (2) reached -> Exhausted!
    f3 = ProgrammerFailure.create(
        execution_id=execution.execution_id,
        work_order_id=work_order.work_order_id,
        category=ProgrammerFailureCategory.AGENT_CRASH,
        message="Crash 3",
        retryable=True,
    )
    can3, reason3 = coordinator.can_retry(f3, execution, work_order, policy)
    assert can3 is False
    assert "Category retry budget exhausted (2/2)" in reason3


def test_total_retry_budget_exhaustion(base_context):
    work_order, execution = base_context
    coordinator = RetryCoordinator()
    policy = RetryPolicy(
        max_retries=5,
        total_retry_budget=2,  # Global execution retry budget is 2
    )

    # Retry 1 on AGENT_STARTUP
    f1 = ProgrammerFailure.create(execution_id=execution.execution_id, work_order_id=work_order.work_order_id, category=ProgrammerFailureCategory.AGENT_STARTUP, message="Startup error", retryable=True)
    can1, _ = coordinator.can_retry(f1, execution, work_order, policy)
    assert can1 is True
    coordinator.record_retry_attempt(f1, execution, work_order, delay_seconds=1.0)

    # Retry 2 on AGENT_CRASH
    f2 = ProgrammerFailure.create(execution_id=execution.execution_id, work_order_id=work_order.work_order_id, category=ProgrammerFailureCategory.AGENT_CRASH, message="Crash", retryable=True)
    can2, _ = coordinator.can_retry(f2, execution, work_order, policy)
    assert can2 is True
    coordinator.record_retry_attempt(f2, execution, work_order, delay_seconds=1.0)

    # Retry 3 on RESOURCE: category budget is fine, but total_retry_budget (2) is exhausted!
    f3 = ProgrammerFailure.create(execution_id=execution.execution_id, work_order_id=work_order.work_order_id, category=ProgrammerFailureCategory.RESOURCE, message="Resource error", retryable=True)
    can3, reason3 = coordinator.can_retry(f3, execution, work_order, policy)
    assert can3 is False
    assert "Total execution retry budget exhausted (2/2)" in reason3


# ==============================================================================
# 3. Successful Retry Flow
# ==============================================================================


def test_successful_retry_flow(base_context):
    work_order, execution = base_context
    coordinator = RetryCoordinator()

    # Step 1: Agent crashes
    f1 = ProgrammerFailure.create(
        execution_id=execution.execution_id,
        work_order_id=work_order.work_order_id,
        category=ProgrammerFailureCategory.AGENT_CRASH,
        message="Intermittent process crash",
        retryable=True,
    )
    can1, _ = coordinator.can_retry(f1, execution, work_order)
    assert can1 is True
    record = coordinator.record_retry_attempt(f1, execution, work_order, delay_seconds=1.0)
    assert record.attempt_number == 1

    # Step 2: Second attempt succeeds, execution transitions towards COMPLETING
    execution.transition_to(ProgrammerExecutionStatus.COMPLETING, reason="Retry attempt succeeded")
    assert execution.status == ProgrammerExecutionStatus.COMPLETING
    assert coordinator.get_total_retry_count(execution.execution_id) == 1


# ==============================================================================
# 4. Cancellation Handling During Backoff
# ==============================================================================


def test_cancellation_during_backoff():
    coordinator = RetryCoordinator()
    policy = RetryPolicy(initial_delay_seconds=2.0)

    cancelled_flag = False

    def is_cancelled() -> bool:
        return cancelled_flag

    # Cancelled before sleep
    cancelled_flag = True
    with pytest.raises(ProgrammerError, match="prior to backoff delay"):
        coordinator.execute_backoff(
            attempt_number=1,
            policy=policy,
            is_cancelled=is_cancelled,
        )

    # Cancelled during/after sleep
    cancelled_flag = False

    def sleep_and_cancel(secs: float) -> None:
        nonlocal cancelled_flag
        cancelled_flag = True  # Cancellation arrives during wait

    with pytest.raises(ProgrammerError, match="during backoff delay"):
        coordinator.execute_backoff(
            attempt_number=1,
            policy=policy,
            is_cancelled=is_cancelled,
            sleep_func=sleep_and_cancel,
        )


def test_can_retry_rejects_when_execution_cancelled(base_context):
    work_order, execution = base_context
    coordinator = RetryCoordinator()
    execution.status = ProgrammerExecutionStatus.CANCELLED

    failure = ProgrammerFailure.create(
        execution_id=execution.execution_id,
        work_order_id=work_order.work_order_id,
        category=ProgrammerFailureCategory.AGENT_CRASH,
        message="Crash during cancellation",
        retryable=True,
    )
    can_ret, reason = coordinator.can_retry(failure, execution, work_order)
    assert can_ret is False
    assert "Execution has been cancelled" in reason


# ==============================================================================
# 5. Non-Retryable Failure Categories Tests
# ==============================================================================


def test_non_retryable_categories(base_context):
    work_order, execution = base_context
    coordinator = RetryCoordinator()

    non_retryables = [
        ProgrammerFailureCategory.PERMISSION,
        ProgrammerFailureCategory.SCOPE,
        ProgrammerFailureCategory.BUDGET,
        ProgrammerFailureCategory.TIMEOUT,
        ProgrammerFailureCategory.CANCELLATION,
        ProgrammerFailureCategory.MISSING_CONTEXT,
        ProgrammerFailureCategory.INTERNAL,
        ProgrammerFailureCategory.COMMAND_FAILURE,
        ProgrammerFailureCategory.UNKNOWN,
    ]

    for cat in non_retryables:
        # Create failure without forcing retryable=True on invariant-protected categories
        f = ProgrammerFailure.create(
            execution_id=execution.execution_id,
            work_order_id=work_order.work_order_id,
            category=cat,
            message=f"Test failure for {cat.value}",
        )
        can_ret, reason = coordinator.can_retry(f, execution, work_order)
        assert can_ret is False, f"Category {cat.value} should not be retryable!"
        assert ("Authority expansion forbidden" in reason or "non-retryable" in reason or "explicitly marked non-retryable" in reason)


def test_policy_forbids_unsafe_categories_in_retryable():
    # Invariant: SCOPE, PERMISSION, CANCELLATION, BUDGET, and VERIFICATION_FAILURE
    # can NEVER be put in retryable_categories.
    unsafe_candidates = [
        ProgrammerFailureCategory.PERMISSION,
        ProgrammerFailureCategory.SCOPE,
        ProgrammerFailureCategory.CANCELLATION,
        ProgrammerFailureCategory.BUDGET,
        ProgrammerFailureCategory.VERIFICATION_FAILURE,
    ]
    for unsafe in unsafe_candidates:
        with pytest.raises(ProgrammerValidationError, match="Policy contradiction"):
            RetryPolicy(retryable_categories=(unsafe,))


# ==============================================================================
# 6. Distinction Between Retry and Correction Tests
# ==============================================================================


def test_distinction_between_retry_and_correction(base_context):
    work_order, execution = base_context
    coordinator = RetryCoordinator()

    # 1. VERIFICATION_FAILURE cannot be retried via execution retry
    vf = ProgrammerFailure.create(
        execution_id=execution.execution_id,
        work_order_id=work_order.work_order_id,
        category=ProgrammerFailureCategory.VERIFICATION_FAILURE,
        message="Acceptance criterion failed",
    )
    can_ret, reason = coordinator.can_retry(vf, execution, work_order)
    assert can_ret is False
    assert "non-retryable" in reason

    # 2. Budget Isolation: Correction does NOT reset retry budget
    # Record a retry attempt
    crash_f = ProgrammerFailure.create(
        execution_id=execution.execution_id,
        work_order_id=work_order.work_order_id,
        category=ProgrammerFailureCategory.AGENT_CRASH,
        message="Crash",
        retryable=True,
    )
    coordinator.record_retry_attempt(crash_f, execution, work_order, delay_seconds=1.0)
    assert coordinator.get_total_retry_count(execution.execution_id) == 1
    assert coordinator.get_correction_count(execution.execution_id) == 0

    # Record correction turn
    c_turn = coordinator.record_correction_turn(execution.execution_id)
    assert c_turn == 1
    assert coordinator.get_correction_count(execution.execution_id) == 1
    # Retry count must remain 1 (NOT reset!)
    assert coordinator.get_total_retry_count(execution.execution_id) == 1

    # Record another retry attempt
    coordinator.record_retry_attempt(crash_f, execution, work_order, delay_seconds=2.0)
    assert coordinator.get_total_retry_count(execution.execution_id) == 2
    # Correction count must remain 1 (NOT reset!)
    assert coordinator.get_correction_count(execution.execution_id) == 1


# ==============================================================================
# 7. Lineage Across Attempts Tests
# ==============================================================================


def test_lineage_across_attempts(base_context):
    work_order, execution = base_context
    coordinator = RetryCoordinator()

    f = ProgrammerFailure.create(
        execution_id=execution.execution_id,
        work_order_id=work_order.work_order_id,
        category=ProgrammerFailureCategory.AGENT_CRASH,
        message="Crash",
        retryable=True,
    )
    rec = coordinator.record_retry_attempt(f, execution, work_order, delay_seconds=1.0)

    assert rec.work_order_id == work_order.work_order_id
    assert rec.execution_id == execution.execution_id
    assert rec.failure_id == f.failure_id

    # Lineage mismatch between failure and work order raises ProgrammerLineageError
    unrelated_f = ProgrammerFailure.create(
        execution_id=execution.execution_id,
        work_order_id="pwo-other-999",
        category=ProgrammerFailureCategory.AGENT_CRASH,
        message="Unrelated crash",
        retryable=True,
    )
    with pytest.raises(ProgrammerLineageError, match="Lineage mismatch"):
        coordinator.can_retry(unrelated_f, execution, work_order)


# ==============================================================================
# 8. Serialization Roundtrip Tests
# ==============================================================================


def test_retry_attempt_record_serialization(base_context):
    work_order, execution = base_context
    coordinator = RetryCoordinator()

    f = ProgrammerFailure.create(
        execution_id=execution.execution_id,
        work_order_id=work_order.work_order_id,
        category=ProgrammerFailureCategory.AGENT_CRASH,
        message="Crash",
        retryable=True,
    )
    rec = coordinator.record_retry_attempt(f, execution, work_order, delay_seconds=1.5)

    d = rec.to_dict()
    restored = RetryAttemptRecord.from_dict(d)
    assert restored.attempt_id == rec.attempt_id
    assert restored.execution_id == rec.execution_id
    assert restored.delay_seconds == 1.5
    assert restored.failure_category == ProgrammerFailureCategory.AGENT_CRASH


def test_retry_policy_serialization():
    policy = RetryPolicy(max_retries=4, initial_delay_seconds=2.5, backoff_factor=3.0)
    d = policy.to_dict()
    restored = RetryPolicy.from_dict(d)
    assert restored.max_retries == 4
    assert restored.initial_delay_seconds == 2.5
    assert restored.backoff_factor == 3.0


def test_retry_policy_from_work_order():
    critical_wo = ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id="task-crit",
        project_id="proj-rec",
        correlation_id="corr-rec",
        objective="Critical production task",
        risk_level=RiskLevel.CRITICAL,
    )
    crit_policy = RetryPolicy.from_work_order(critical_wo)
    assert crit_policy.max_retries == 0
    assert crit_policy.total_retry_budget == 0

from __future__ import annotations

import pytest

from core.enums import RiskLevel
from core.programmer.contracts.failure import ProgrammerFailure
from core.programmer.contracts.identifiers import (
    new_execution_id,
    new_failure_id,
    new_recovery_decision_id,
    new_work_order_id,
    validate_recovery_decision_id,
)
from core.programmer.contracts.recovery import (
    FailureRecoveryController,
    RecoveryDecision,
    RecoveryPolicy,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import ProgrammerLineageError, ProgrammerValidationError
from core.programmer.types import (
    FailureSourceType,
    ProgrammerFailureCategory,
    ProgrammerFailureSeverity,
    RecoveryDisposition,
)


@pytest.fixture
def base_ids():
    wo_id = new_work_order_id()
    exec_id = new_execution_id()
    return wo_id, exec_id


@pytest.fixture
def mock_work_order(base_ids):
    wo_id, _ = base_ids
    return ProgrammerWorkOrder(
        work_order_id=wo_id,
        manager_task_id="task-recovery-001",
        project_id="proj-rec",
        correlation_id="corr-rec",
        objective="Verify failure recovery controller",
        iteration_budget=4,
    )


# ==============================================================================
# 1. Major Failure Categories Tests
# ==============================================================================


def test_each_major_failure_category(base_ids):
    wo_id, exec_id = base_ids
    controller = FailureRecoveryController()
    policy = RecoveryPolicy(
        max_execution_retries=2,
        max_startup_retries=1,
        max_correction_iterations=3,
        max_total_recovery_attempts=20,
    )

    categories_expected = [
        (ProgrammerFailureCategory.VERIFICATION_FAILURE, RecoveryDisposition.CORRECT, "RULE_VERIFICATION_CORRECTION_BUDGET"),
        (ProgrammerFailureCategory.AGENT_CRASH, RecoveryDisposition.RETRY, "RULE_AGENT_CRASH_RETRY_BUDGET"),
        (ProgrammerFailureCategory.AGENT_STARTUP, RecoveryDisposition.RETRY, "RULE_AGENT_STARTUP_RETRY_BUDGET"),
        (ProgrammerFailureCategory.AGENT_HUNG, RecoveryDisposition.CANCEL, "RULE_AGENT_HUNG_POLICY"),
        (ProgrammerFailureCategory.TIMEOUT, RecoveryDisposition.FAIL, "RULE_TIMEOUT_POLICY"),
        (ProgrammerFailureCategory.PERMISSION, RecoveryDisposition.ESCALATE, "RULE_PERMISSION_ESCALATE"),
        (ProgrammerFailureCategory.SCOPE, RecoveryDisposition.BLOCK, "RULE_SCOPE_VIOLATION"),
        (ProgrammerFailureCategory.BUDGET, RecoveryDisposition.BLOCK, "RULE_BUDGET_EXHAUSTED"),
        (ProgrammerFailureCategory.MISSING_CONTEXT, RecoveryDisposition.BLOCK, "RULE_MISSING_CONTEXT_BLOCK"),
        (ProgrammerFailureCategory.CANCELLATION, RecoveryDisposition.CANCEL, "RULE_CANCELLATION_TERMINAL"),
        (ProgrammerFailureCategory.INTERNAL, RecoveryDisposition.FAIL, "RULE_INTERNAL_ERROR"),
        (ProgrammerFailureCategory.COMMAND_FAILURE, RecoveryDisposition.FAIL, "RULE_COMMAND_FAILURE"),
        (ProgrammerFailureCategory.DEPENDENCY, RecoveryDisposition.BLOCK, "RULE_DEPENDENCY_BLOCK"),
        (ProgrammerFailureCategory.UNKNOWN, RecoveryDisposition.FAIL, "RULE_UNKNOWN_FAILURE"),
    ]

    for cat, expected_action, expected_rule in categories_expected:
        sub_exec_id = new_execution_id()
        failure = ProgrammerFailure.create(
            execution_id=sub_exec_id,
            work_order_id=wo_id,
            category=cat,
            message=f"Test failure for category {cat.value}",
        )
        decision = controller.evaluate_recovery(failure=failure, policy=policy)
        assert decision.action == expected_action, f"Category {cat.value} expected {expected_action.value}, got {decision.action.value}"
        assert decision.policy_rule == expected_rule
        assert decision.recovery_attempt == 1
        validate_recovery_decision_id(decision.decision_id)


# ==============================================================================
# 2. Retryable & Non-Retryable Failure Tests
# ==============================================================================


def test_retryable_failure_agent_crash(base_ids):
    wo_id, exec_id = base_ids
    controller = FailureRecoveryController()
    failure = ProgrammerFailure.create(
        execution_id=exec_id,
        work_order_id=wo_id,
        category=ProgrammerFailureCategory.AGENT_CRASH,
        message="Process terminated abnormally with exit code 1",
        retryable=True,
    )
    decision = controller.evaluate_recovery(failure=failure)
    assert decision.is_retry
    assert decision.action == RecoveryDisposition.RETRY
    assert decision.recovery_attempt == 1
    assert "RULE_AGENT_CRASH_RETRY_BUDGET" in decision.policy_rule


def test_non_retryable_critical_crash(base_ids):
    wo_id, exec_id = base_ids
    controller = FailureRecoveryController()
    # Critical severity agent crash should escalate rather than blind retry
    failure = ProgrammerFailure.create(
        execution_id=exec_id,
        work_order_id=wo_id,
        category=ProgrammerFailureCategory.AGENT_CRASH,
        message="Segfault in underlying native runtime",
        severity=ProgrammerFailureSeverity.CRITICAL,
        retryable=False,
    )
    decision = controller.evaluate_recovery(failure=failure)
    assert decision.is_escalate
    assert decision.action == RecoveryDisposition.ESCALATE
    assert decision.policy_rule == "RULE_CRASH_NON_RETRYABLE"


def test_resource_transient_vs_permanent(base_ids):
    wo_id, exec_id = base_ids
    controller = FailureRecoveryController()
    policy = RecoveryPolicy(max_resource_retries=1)

    # Transient resource failure marked retryable
    transient_fail = ProgrammerFailure.create(
        execution_id=exec_id,
        work_order_id=wo_id,
        category=ProgrammerFailureCategory.RESOURCE,
        message="Temporary socket exhaustion",
        retryable=True,
    )
    dec1 = controller.evaluate_recovery(failure=transient_fail, policy=policy)
    assert dec1.is_retry
    assert dec1.action == RecoveryDisposition.RETRY

    # Permanent resource failure marked non-retryable
    perm_exec_id = new_execution_id()
    perm_fail = ProgrammerFailure.create(
        execution_id=perm_exec_id,
        work_order_id=wo_id,
        category=ProgrammerFailureCategory.RESOURCE,
        message="Disk quota exceeded permanently",
        retryable=False,
    )
    dec2 = controller.evaluate_recovery(failure=perm_fail, policy=policy)
    assert dec2.is_escalate
    assert dec2.action == RecoveryDisposition.ESCALATE


# ==============================================================================
# 3. Exhausted Retry Budget Tests
# ==============================================================================


def test_exhausted_retry_budget_agent_crash(base_ids):
    wo_id, exec_id = base_ids
    controller = FailureRecoveryController()
    policy = RecoveryPolicy(max_execution_retries=2, max_total_recovery_attempts=10)

    # Attempt 1 -> RETRY
    f1 = ProgrammerFailure.create(execution_id=exec_id, work_order_id=wo_id, category=ProgrammerFailureCategory.AGENT_CRASH, message="Crash 1", retryable=True)
    d1 = controller.evaluate_recovery(failure=f1, policy=policy)
    assert d1.action == RecoveryDisposition.RETRY
    assert d1.recovery_attempt == 1

    # Attempt 2 -> RETRY
    f2 = ProgrammerFailure.create(execution_id=exec_id, work_order_id=wo_id, category=ProgrammerFailureCategory.AGENT_CRASH, message="Crash 2", retryable=True)
    d2 = controller.evaluate_recovery(failure=f2, policy=policy)
    assert d2.action == RecoveryDisposition.RETRY
    assert d2.recovery_attempt == 2

    # Attempt 3 -> FAIL (budget exhausted)
    f3 = ProgrammerFailure.create(execution_id=exec_id, work_order_id=wo_id, category=ProgrammerFailureCategory.AGENT_CRASH, message="Crash 3", retryable=True)
    d3 = controller.evaluate_recovery(failure=f3, policy=policy)
    assert d3.action == RecoveryDisposition.FAIL
    assert d3.is_fail
    assert d3.policy_rule == "RULE_AGENT_CRASH_RETRIES_EXHAUSTED"
    assert d3.recovery_attempt == 3


def test_exhausted_startup_retry_budget(base_ids):
    wo_id, exec_id = base_ids
    controller = FailureRecoveryController()
    policy = RecoveryPolicy(max_startup_retries=1)

    f1 = ProgrammerFailure.create(execution_id=exec_id, work_order_id=wo_id, category=ProgrammerFailureCategory.AGENT_STARTUP, message="Startup error 1")
    d1 = controller.evaluate_recovery(failure=f1, policy=policy)
    assert d1.action == RecoveryDisposition.RETRY

    f2 = ProgrammerFailure.create(execution_id=exec_id, work_order_id=wo_id, category=ProgrammerFailureCategory.AGENT_STARTUP, message="Startup error 2")
    d2 = controller.evaluate_recovery(failure=f2, policy=policy)
    assert d2.action == RecoveryDisposition.FAIL
    assert d2.policy_rule == "RULE_AGENT_STARTUP_RETRIES_EXHAUSTED"


# ==============================================================================
# 4. Verification Correction Budget Tests
# ==============================================================================


def test_verification_correction_budget(base_ids):
    wo_id, exec_id = base_ids
    controller = FailureRecoveryController()
    policy = RecoveryPolicy(max_correction_iterations=2, max_total_recovery_attempts=10)

    # Turn 1 -> CORRECT
    f1 = ProgrammerFailure.create(execution_id=exec_id, work_order_id=wo_id, category=ProgrammerFailureCategory.VERIFICATION_FAILURE, message="Check vchk-01 failed")
    d1 = controller.evaluate_recovery(failure=f1, policy=policy)
    assert d1.is_correct
    assert d1.action == RecoveryDisposition.CORRECT
    assert d1.recovery_attempt == 1

    # Turn 2 -> CORRECT
    f2 = ProgrammerFailure.create(execution_id=exec_id, work_order_id=wo_id, category=ProgrammerFailureCategory.VERIFICATION_FAILURE, message="Check vchk-02 failed")
    d2 = controller.evaluate_recovery(failure=f2, policy=policy)
    assert d2.is_correct
    assert d2.action == RecoveryDisposition.CORRECT
    assert d2.recovery_attempt == 2

    # Turn 3 -> FAIL (correction budget exhausted)
    f3 = ProgrammerFailure.create(execution_id=exec_id, work_order_id=wo_id, category=ProgrammerFailureCategory.VERIFICATION_FAILURE, message="Check vchk-02 failed again")
    d3 = controller.evaluate_recovery(failure=f3, policy=policy)
    assert d3.is_fail
    assert d3.action == RecoveryDisposition.FAIL
    assert d3.policy_rule == "RULE_VERIFICATION_CORRECTION_EXHAUSTED"
    assert d3.recovery_attempt == 3


# ==============================================================================
# 5. Permission & Scope Invariant Tests
# ==============================================================================


def test_permission_failure_always_escalates(base_ids):
    wo_id, exec_id = base_ids
    controller = FailureRecoveryController()
    failure = ProgrammerFailure.create(
        execution_id=exec_id,
        work_order_id=wo_id,
        category=ProgrammerFailureCategory.PERMISSION,
        message="Command 'git push' denied by policy",
    )
    decision = controller.evaluate_recovery(failure=failure)
    assert decision.is_escalate
    assert decision.action == RecoveryDisposition.ESCALATE
    assert decision.policy_rule == "RULE_PERMISSION_ESCALATE"


def test_scope_failure_always_blocks(base_ids):
    wo_id, exec_id = base_ids
    controller = FailureRecoveryController()
    failure = ProgrammerFailure.create(
        execution_id=exec_id,
        work_order_id=wo_id,
        category=ProgrammerFailureCategory.SCOPE,
        message="Attempted modification to /etc/hosts outside workspace",
    )
    decision = controller.evaluate_recovery(failure=failure)
    assert decision.is_block
    assert decision.action == RecoveryDisposition.BLOCK
    assert decision.policy_rule == "RULE_SCOPE_VIOLATION"


def test_scope_and_permission_policy_contradiction_guards(base_ids):
    wo_id, exec_id = base_ids
    # 1. RecoveryPolicy forbids setting scope_action=RETRY or CORRECT
    with pytest.raises(ProgrammerValidationError, match="scope_action cannot be RETRY"):
        RecoveryPolicy(scope_action=RecoveryDisposition.RETRY)

    with pytest.raises(ProgrammerValidationError, match="permission_action cannot be RETRY"):
        RecoveryPolicy(permission_action=RecoveryDisposition.RETRY)

    # 2. RecoveryDecision.validate() forbids action=RETRY on SCOPE or PERMISSION
    fail_scope = ProgrammerFailure.create(
        execution_id=exec_id,
        work_order_id=wo_id,
        category=ProgrammerFailureCategory.SCOPE,
        message="Scope error",
    )
    with pytest.raises(ProgrammerValidationError, match="Policy contradiction"):
        RecoveryDecision.create(
            failure=fail_scope,
            recovery_attempt=1,
            action=RecoveryDisposition.RETRY,
            reason="Unsafe retry",
            policy_rule="RULE_TEST",
        )


# ==============================================================================
# 6. Cancellation & Hung Policy Tests
# ==============================================================================


def test_cancellation_always_cancels(base_ids):
    wo_id, exec_id = base_ids
    controller = FailureRecoveryController()
    failure = ProgrammerFailure.create(
        execution_id=exec_id,
        work_order_id=wo_id,
        category=ProgrammerFailureCategory.CANCELLATION,
        message="User clicked Cancel",
    )
    decision = controller.evaluate_recovery(failure=failure)
    assert decision.is_cancel
    assert decision.action == RecoveryDisposition.CANCEL
    assert decision.policy_rule == "RULE_CANCELLATION_TERMINAL"


def test_cancellation_decision_validation_guards(base_ids):
    wo_id, exec_id = base_ids
    fail_cancel = ProgrammerFailure.create(
        execution_id=exec_id,
        work_order_id=wo_id,
        category=ProgrammerFailureCategory.CANCELLATION,
        message="User cancelled",
    )
    with pytest.raises(ProgrammerValidationError, match="CANCELLATION failure must decide CANCEL"):
        RecoveryDecision.create(
            failure=fail_cancel,
            recovery_attempt=1,
            action=RecoveryDisposition.RETRY,
            reason="Illegal retry",
            policy_rule="RULE_TEST",
        )


def test_agent_hung_default_vs_restart_policy(base_ids):
    wo_id, exec_id = base_ids
    controller = FailureRecoveryController()

    # Default policy: allow_agent_hung_restart=False -> hung_action=CANCEL
    f1 = ProgrammerFailure.create(execution_id=exec_id, work_order_id=wo_id, category=ProgrammerFailureCategory.AGENT_HUNG, message="Silence exceeded 120s")
    d1 = controller.evaluate_recovery(failure=f1)
    assert d1.action == RecoveryDisposition.CANCEL
    assert d1.policy_rule == "RULE_AGENT_HUNG_POLICY"

    # Explicit restart policy: allow_agent_hung_restart=True -> RETRY
    sub_exec_id = new_execution_id()
    restart_policy = RecoveryPolicy(allow_agent_hung_restart=True, max_execution_retries=1)
    f2 = ProgrammerFailure.create(execution_id=sub_exec_id, work_order_id=wo_id, category=ProgrammerFailureCategory.AGENT_HUNG, message="Silence exceeded 120s")
    d2 = controller.evaluate_recovery(failure=f2, policy=restart_policy)
    assert d2.action == RecoveryDisposition.RETRY
    assert d2.policy_rule == "RULE_AGENT_HUNG_RESTART_ALLOWED"


# ==============================================================================
# 7. Repeated Failures & Global Budget Limit Tests
# ==============================================================================


def test_repeated_failures_halt_at_global_limit(base_ids):
    wo_id, exec_id = base_ids
    controller = FailureRecoveryController()
    policy = RecoveryPolicy(
        max_execution_retries=10,
        max_correction_iterations=10,
        max_total_recovery_attempts=3,  # Global cap is 3
    )

    f1 = ProgrammerFailure.create(execution_id=exec_id, work_order_id=wo_id, category=ProgrammerFailureCategory.AGENT_CRASH, message="Crash 1", retryable=True)
    d1 = controller.evaluate_recovery(failure=f1, policy=policy)
    assert d1.action == RecoveryDisposition.RETRY
    assert d1.recovery_attempt == 1

    f2 = ProgrammerFailure.create(execution_id=exec_id, work_order_id=wo_id, category=ProgrammerFailureCategory.VERIFICATION_FAILURE, message="Verification fail")
    d2 = controller.evaluate_recovery(failure=f2, policy=policy)
    assert d2.action == RecoveryDisposition.CORRECT
    assert d2.recovery_attempt == 2

    f3 = ProgrammerFailure.create(execution_id=exec_id, work_order_id=wo_id, category=ProgrammerFailureCategory.AGENT_CRASH, message="Crash 2", retryable=True)
    d3 = controller.evaluate_recovery(failure=f3, policy=policy)
    assert d3.action == RecoveryDisposition.RETRY
    assert d3.recovery_attempt == 3

    # Attempt 4 exceeds global budget of 3 -> FAIL
    f4 = ProgrammerFailure.create(execution_id=exec_id, work_order_id=wo_id, category=ProgrammerFailureCategory.AGENT_CRASH, message="Crash 3", retryable=True)
    d4 = controller.evaluate_recovery(failure=f4, policy=policy)
    assert d4.action == RecoveryDisposition.FAIL
    assert d4.policy_rule == "RULE_TOTAL_ATTEMPTS_EXHAUSTED"
    assert d4.recovery_attempt == 4

    decisions = controller.get_decisions(exec_id)
    assert len(decisions) == 4
    assert controller.get_attempt_count(exec_id) == 4
    assert controller.get_category_attempt_count(exec_id, ProgrammerFailureCategory.AGENT_CRASH) == 3


# ==============================================================================
# 8. Lineage & WorkOrder Policy Derivation Tests
# ==============================================================================


def test_lineage_mismatch_raises_error(mock_work_order):
    controller = FailureRecoveryController()
    mismatched_failure = ProgrammerFailure.create(
        execution_id=new_execution_id(),
        work_order_id="pwo-other-000",
        category=ProgrammerFailureCategory.AGENT_CRASH,
        message="Crash in unrelated task",
    )
    with pytest.raises(ProgrammerLineageError, match="Lineage mismatch"):
        controller.evaluate_recovery(failure=mismatched_failure, work_order=mock_work_order)


def test_recovery_policy_from_work_order(mock_work_order):
    # mock_work_order has iteration_budget = 4
    policy = RecoveryPolicy.from_work_order(mock_work_order)
    assert policy.max_correction_iterations == 4

    # CRITICAL risk task adjusts default execution retries to 0
    critical_wo = ProgrammerWorkOrder(
        work_order_id=new_work_order_id(),
        manager_task_id="task-crit",
        project_id="proj-rec",
        correlation_id="corr-rec",
        objective="Critical production task",
        risk_level=RiskLevel.CRITICAL,
    )
    crit_policy = RecoveryPolicy.from_work_order(critical_wo)
    assert crit_policy.max_execution_retries == 0


# ==============================================================================
# 9. Serialization & Model Integrity Tests
# ==============================================================================


def test_recovery_decision_and_policy_serialization(base_ids):
    wo_id, exec_id = base_ids
    controller = FailureRecoveryController()
    failure = ProgrammerFailure.create(
        execution_id=exec_id,
        work_order_id=wo_id,
        category=ProgrammerFailureCategory.VERIFICATION_FAILURE,
        message="Check failed",
    )
    decision = controller.evaluate_recovery(failure=failure)

    # RecoveryDecision roundtrip
    d_dict = decision.to_dict()
    restored_decision = RecoveryDecision.from_dict(d_dict)
    assert restored_decision.decision_id == decision.decision_id
    assert restored_decision.action == decision.action
    assert restored_decision.policy_rule == decision.policy_rule

    # RecoveryPolicy roundtrip
    policy = RecoveryPolicy(max_execution_retries=3, allow_agent_hung_restart=True)
    p_dict = policy.to_dict()
    restored_policy = RecoveryPolicy.from_dict(p_dict)
    assert restored_policy.max_execution_retries == 3
    assert restored_policy.allow_agent_hung_restart is True


def test_controller_decision_callback_and_reset(base_ids):
    wo_id, exec_id = base_ids
    recorded_decisions = []

    def on_dec(d: RecoveryDecision) -> None:
        recorded_decisions.append(d)

    controller = FailureRecoveryController(on_decision=on_dec)
    f1 = ProgrammerFailure.create(execution_id=exec_id, work_order_id=wo_id, category=ProgrammerFailureCategory.AGENT_STARTUP, message="Init failed")
    d1 = controller.evaluate_recovery(failure=f1)

    assert len(recorded_decisions) == 1
    assert recorded_decisions[0].decision_id == d1.decision_id

    assert controller.get_last_decision(exec_id).decision_id == d1.decision_id

    # Reset specific execution
    controller.reset(exec_id)
    assert controller.get_attempt_count(exec_id) == 0
    assert controller.get_decisions(exec_id) == []

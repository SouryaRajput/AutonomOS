from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from typing import Any, Callable, Optional, Sequence, Union

from core.enums import RiskLevel
from core.programmer.contracts.failure import ProgrammerFailure
from core.programmer.contracts.identifiers import (
    new_recovery_decision_id,
    validate_execution_id,
    validate_failure_id,
    validate_recovery_decision_id,
    validate_work_order_id,
)
from core.programmer.contracts.work_order import ProgrammerWorkOrder
from core.programmer.errors import (
    ProgrammerError,
    ProgrammerLineageError,
    ProgrammerValidationError,
)
from core.programmer.types import (
    ProgrammerFailureCategory,
    ProgrammerFailureSeverity,
    RecoveryDisposition,
)

logger = logging.getLogger("AutonomOS.Programmer.Recovery")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ==============================================================================
# 1. Recovery Decision Contract
# ==============================================================================


@dataclass
class RecoveryDecision:
    """
    Strongly-typed, auditable record of a deterministic recovery action decided
    for a Programmer execution failure.
    
    Guarantees:
    - Maintained lineage back to failure_id, execution_id, and work_order_id.
    - Captures the exact recovery_attempt sequence number (1, 2, ...).
    - Designates the permitted action from the closed taxonomy:
      RETRY, CORRECT, ESCALATE, BLOCK, FAIL, CANCEL.
    - Cites the specific policy rule and structured reasoning.
    - Enforces critical safety invariants (no retry for scope/permission/cancellation/budget).
    """
    decision_id: str
    failure_id: str
    execution_id: str
    work_order_id: str
    recovery_attempt: int
    action: RecoveryDisposition
    reason: str
    policy_rule: str
    timestamp: str = field(default_factory=utc_now)
    failure_category: Optional[ProgrammerFailureCategory] = None
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.action, str):
            try:
                self.action = RecoveryDisposition(self.action.upper())
            except ValueError:
                self.action = RecoveryDisposition.FAIL

        if isinstance(self.failure_category, str):
            try:
                self.failure_category = ProgrammerFailureCategory(self.failure_category.upper())
            except ValueError:
                self.failure_category = ProgrammerFailureCategory.UNKNOWN

        if not self.trace:
            self.trace = {
                "decision_id": self.decision_id,
                "failure_id": self.failure_id,
                "execution_id": self.execution_id,
                "work_order_id": self.work_order_id,
                "recovery_attempt": self.recovery_attempt,
                "action": self.action.value,
                "policy_rule": self.policy_rule,
            }

    def validate(self) -> None:
        """
        Validate structural integrity, lineage, and policy invariant consistency.
        Raises ProgrammerValidationError or ProgrammerLineageError.
        """
        # 1. Identifier and Lineage Validation
        if not self.decision_id or not str(self.decision_id).strip():
            raise ProgrammerValidationError("decision_id must be a non-empty string.", field_name="decision_id")
        validate_recovery_decision_id(self.decision_id)

        if not self.failure_id or not str(self.failure_id).strip():
            raise ProgrammerValidationError("failure_id must be a non-empty string.", field_name="failure_id")
        validate_failure_id(self.failure_id)

        if not self.execution_id or not str(self.execution_id).strip():
            raise ProgrammerLineageError("RecoveryDecision must have a non-empty execution_id.")
        validate_execution_id(self.execution_id)

        if not self.work_order_id or not str(self.work_order_id).strip():
            raise ProgrammerLineageError("RecoveryDecision must have a non-empty work_order_id.")
        validate_work_order_id(self.work_order_id)

        # 2. Attempt Number Validation
        if self.recovery_attempt < 1:
            raise ProgrammerValidationError(
                f"recovery_attempt must be >= 1, got {self.recovery_attempt}.",
                field_name="recovery_attempt",
            )

        # 3. Action and Reason Validation
        if not isinstance(self.action, RecoveryDisposition):
            raise ProgrammerValidationError(f"Invalid recovery action '{self.action}'.", field_name="action")

        if not self.reason or not str(self.reason).strip():
            raise ProgrammerValidationError("Recovery reason must be a non-empty string.", field_name="reason")

        if not self.policy_rule or not str(self.policy_rule).strip():
            raise ProgrammerValidationError("policy_rule must be a non-empty string.", field_name="policy_rule")

        # 4. Critical Safety Invariant Guards:
        # A. Scope and Permission violations can NEVER decide RETRY or CORRECT.
        if self.failure_category in (ProgrammerFailureCategory.SCOPE, ProgrammerFailureCategory.PERMISSION):
            if self.action in (RecoveryDisposition.RETRY, RecoveryDisposition.CORRECT):
                raise ProgrammerValidationError(
                    f"Policy contradiction: {self.failure_category.value} failure cannot decide {self.action.value}. "
                    "Authority and filesystem scope cannot expand through automated recovery.",
                    field_name="action",
                )

        # B. Cancellation can NEVER decide RETRY, CORRECT, ESCALATE, BLOCK, or FAIL.
        if self.failure_category == ProgrammerFailureCategory.CANCELLATION:
            if self.action != RecoveryDisposition.CANCEL:
                raise ProgrammerValidationError(
                    f"Policy contradiction: CANCELLATION failure must decide CANCEL, got {self.action.value}.",
                    field_name="action",
                )

        # C. Budget exhaustion can NEVER decide RETRY.
        if self.failure_category == ProgrammerFailureCategory.BUDGET:
            if self.action == RecoveryDisposition.RETRY:
                raise ProgrammerValidationError(
                    "Policy contradiction: BUDGET exhaustion cannot decide RETRY. "
                    "Budgets cannot be silently reset.",
                    field_name="action",
                )

    @property
    def is_retry(self) -> bool:
        """True if the decision permits an execution retry."""
        return self.action == RecoveryDisposition.RETRY

    @property
    def is_correct(self) -> bool:
        """True if the decision permits a bounded verification self-correction turn."""
        return self.action == RecoveryDisposition.CORRECT

    @property
    def is_escalate(self) -> bool:
        """True if the decision escalates to Manager."""
        return self.action == RecoveryDisposition.ESCALATE

    @property
    def is_block(self) -> bool:
        """True if the decision establishes a blocker requiring Manager resolution."""
        return self.action == RecoveryDisposition.BLOCK

    @property
    def is_fail(self) -> bool:
        """True if the decision terminates execution as permanently failed."""
        return self.action == RecoveryDisposition.FAIL

    @property
    def is_cancel(self) -> bool:
        """True if the decision terminates execution as cancelled."""
        return self.action == RecoveryDisposition.CANCEL

    @property
    def is_terminal(self) -> bool:
        """True if the decision terminates recovery attempts (FAIL or CANCEL)."""
        return self.action in (RecoveryDisposition.FAIL, RecoveryDisposition.CANCEL)

    @classmethod
    def create(
        cls,
        failure: ProgrammerFailure,
        recovery_attempt: int,
        action: Union[RecoveryDisposition, str],
        reason: str,
        policy_rule: str,
        decision_id: Optional[str] = None,
        trace: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> RecoveryDecision:
        """Factory creating and validating a RecoveryDecision directly from a ProgrammerFailure."""
        if failure is None:
            raise ProgrammerValidationError("ProgrammerFailure cannot be None.")

        act = action if isinstance(action, RecoveryDisposition) else RecoveryDisposition(str(action).upper())
        cat = failure.category if isinstance(failure.category, ProgrammerFailureCategory) else ProgrammerFailureCategory(str(failure.category).upper())

        tr = dict(trace or {})
        tr.update({
            "failure_id": failure.failure_id,
            "failure_category": cat.value,
            "execution_id": failure.execution_id,
            "work_order_id": failure.work_order_id,
            "policy_rule": policy_rule,
        })

        decision = cls(
            decision_id=decision_id or new_recovery_decision_id(),
            failure_id=failure.failure_id,
            execution_id=failure.execution_id,
            work_order_id=failure.work_order_id,
            recovery_attempt=recovery_attempt,
            action=act,
            reason=reason,
            policy_rule=policy_rule,
            timestamp=utc_now(),
            failure_category=cat,
            trace=tr,
            metadata=dict(metadata or {}),
        )
        decision.validate()
        return decision

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "decision_id": self.decision_id,
            "failure_id": self.failure_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "recovery_attempt": self.recovery_attempt,
            "action": self.action.value,
            "reason": self.reason,
            "policy_rule": self.policy_rule,
            "timestamp": self.timestamp,
            "failure_category": self.failure_category.value if self.failure_category else None,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecoveryDecision:
        """Reconstruct a validated RecoveryDecision from dictionary."""
        decision = cls(
            decision_id=str(data.get("decision_id", "")),
            failure_id=str(data.get("failure_id", "")),
            execution_id=str(data.get("execution_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            recovery_attempt=int(data.get("recovery_attempt", 1)),
            action=RecoveryDisposition(str(data.get("action", "FAIL")).upper()),
            reason=str(data.get("reason", "")),
            policy_rule=str(data.get("policy_rule", "")),
            timestamp=str(data.get("timestamp", utc_now())),
            failure_category=(
                ProgrammerFailureCategory(str(data["failure_category"]).upper())
                if data.get("failure_category")
                else None
            ),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )
        decision.validate()
        return decision


# ==============================================================================
# 2. Recovery Policy
# ==============================================================================


@dataclass
class RecoveryPolicy:
    """
    Deterministic configuration governing recovery rules, retry budgets,
    and fallback actions for Programmer execution failures.
    
    Guarantees:
    - Never hard-codes assumptions that conflict with WorkOrder policy.
    - Explicitly bounds retries across execution, startup, correction, and global attempts.
    - Enforces safety limits: scope and permission actions are restricted to BLOCK or ESCALATE.
    """
    max_execution_retries: int = 2          # Max retries for AGENT_CRASH
    max_startup_retries: int = 1            # Max retries for AGENT_STARTUP
    max_correction_iterations: int = 3      # Max correction turns for VERIFICATION_FAILURE
    max_resource_retries: int = 1           # Max retries for transient RESOURCE failures
    max_total_recovery_attempts: int = 5    # Global limit per execution to prevent infinite recovery loops
    allow_agent_hung_restart: bool = False  # If True, AGENT_HUNG can retry; if False, applies hung_action
    hung_action: RecoveryDisposition = RecoveryDisposition.CANCEL
    timeout_action: RecoveryDisposition = RecoveryDisposition.FAIL
    internal_error_action: RecoveryDisposition = RecoveryDisposition.FAIL
    scope_action: RecoveryDisposition = RecoveryDisposition.BLOCK
    permission_action: RecoveryDisposition = RecoveryDisposition.ESCALATE
    missing_context_action: RecoveryDisposition = RecoveryDisposition.BLOCK
    budget_action: RecoveryDisposition = RecoveryDisposition.BLOCK
    command_failure_action: RecoveryDisposition = RecoveryDisposition.FAIL
    dependency_action: RecoveryDisposition = RecoveryDisposition.BLOCK
    unknown_action: RecoveryDisposition = RecoveryDisposition.FAIL

    def __post_init__(self) -> None:
        # Coerce any string dispositions
        for attr in (
            "hung_action",
            "timeout_action",
            "internal_error_action",
            "scope_action",
            "permission_action",
            "missing_context_action",
            "budget_action",
            "command_failure_action",
            "dependency_action",
            "unknown_action",
        ):
            val = getattr(self, attr)
            if isinstance(val, str):
                try:
                    setattr(self, attr, RecoveryDisposition(val.upper()))
                except ValueError:
                    pass
        self.validate()

    def validate(self) -> None:
        """Validate bounds and ensure no unsafe recovery policies are configured."""
        if self.max_execution_retries < 0:
            raise ProgrammerValidationError("max_execution_retries cannot be negative.", field_name="max_execution_retries")
        if self.max_startup_retries < 0:
            raise ProgrammerValidationError("max_startup_retries cannot be negative.", field_name="max_startup_retries")
        if self.max_correction_iterations < 0:
            raise ProgrammerValidationError("max_correction_iterations cannot be negative.", field_name="max_correction_iterations")
        if self.max_resource_retries < 0:
            raise ProgrammerValidationError("max_resource_retries cannot be negative.", field_name="max_resource_retries")
        if self.max_total_recovery_attempts < 1:
            raise ProgrammerValidationError("max_total_recovery_attempts must be >= 1.", field_name="max_total_recovery_attempts")

        # Critical Safety Invariant: Scope/Permission policies CANNOT permit RETRY or CORRECT
        if self.scope_action in (RecoveryDisposition.RETRY, RecoveryDisposition.CORRECT):
            raise ProgrammerValidationError(
                f"Unsafe policy: scope_action cannot be {self.scope_action.value}. Scope expansion is forbidden.",
                field_name="scope_action",
            )
        if self.permission_action in (RecoveryDisposition.RETRY, RecoveryDisposition.CORRECT):
            raise ProgrammerValidationError(
                f"Unsafe policy: permission_action cannot be {self.permission_action.value}. Permission expansion is forbidden.",
                field_name="permission_action",
            )
        if self.budget_action == RecoveryDisposition.RETRY:
            raise ProgrammerValidationError(
                "Unsafe policy: budget_action cannot be RETRY. Budget resets are forbidden.",
                field_name="budget_action",
            )

    @classmethod
    def from_work_order(cls, work_order: ProgrammerWorkOrder) -> RecoveryPolicy:
        """
        Derive a validated RecoveryPolicy reflecting WorkOrder iteration_budget,
        risk_level, and metadata overrides without hard-coding conflicting assumptions.
        """
        if work_order is None:
            return cls()

        # Align correction budget with work_order.iteration_budget if set
        correction_budget = (
            int(work_order.iteration_budget)
            if getattr(work_order, "iteration_budget", None) is not None and int(work_order.iteration_budget) > 0
            else 3
        )

        # Risk-level adjustments: CRITICAL risk reduces blind execution retries
        risk = getattr(work_order, "risk_level", RiskLevel.LOW)
        max_exec_retries = 2
        if isinstance(risk, RiskLevel) and risk == RiskLevel.CRITICAL:
            max_exec_retries = 0  # Escalate immediately on crash for critical tasks

        # Extract explicit metadata overrides if present
        meta_policy = dict(getattr(work_order, "metadata", {}).get("recovery_policy", {}))

        policy = cls(
            max_execution_retries=int(meta_policy.get("max_execution_retries", max_exec_retries)),
            max_startup_retries=int(meta_policy.get("max_startup_retries", 1)),
            max_correction_iterations=int(meta_policy.get("max_correction_iterations", correction_budget)),
            max_resource_retries=int(meta_policy.get("max_resource_retries", 1)),
            max_total_recovery_attempts=int(meta_policy.get("max_total_recovery_attempts", 5)),
            allow_agent_hung_restart=bool(meta_policy.get("allow_agent_hung_restart", False)),
            hung_action=meta_policy.get("hung_action", RecoveryDisposition.CANCEL),
            timeout_action=meta_policy.get("timeout_action", RecoveryDisposition.FAIL),
            internal_error_action=meta_policy.get("internal_error_action", RecoveryDisposition.FAIL),
            scope_action=meta_policy.get("scope_action", RecoveryDisposition.BLOCK),
            permission_action=meta_policy.get("permission_action", RecoveryDisposition.ESCALATE),
            missing_context_action=meta_policy.get("missing_context_action", RecoveryDisposition.BLOCK),
            budget_action=meta_policy.get("budget_action", RecoveryDisposition.BLOCK),
            command_failure_action=meta_policy.get("command_failure_action", RecoveryDisposition.FAIL),
            dependency_action=meta_policy.get("dependency_action", RecoveryDisposition.BLOCK),
            unknown_action=meta_policy.get("unknown_action", RecoveryDisposition.FAIL),
        )
        policy.validate()
        return policy

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_execution_retries": self.max_execution_retries,
            "max_startup_retries": self.max_startup_retries,
            "max_correction_iterations": self.max_correction_iterations,
            "max_resource_retries": self.max_resource_retries,
            "max_total_recovery_attempts": self.max_total_recovery_attempts,
            "allow_agent_hung_restart": self.allow_agent_hung_restart,
            "hung_action": self.hung_action.value,
            "timeout_action": self.timeout_action.value,
            "internal_error_action": self.internal_error_action.value,
            "scope_action": self.scope_action.value,
            "permission_action": self.permission_action.value,
            "missing_context_action": self.missing_context_action.value,
            "budget_action": self.budget_action.value,
            "command_failure_action": self.command_failure_action.value,
            "dependency_action": self.dependency_action.value,
            "unknown_action": self.unknown_action.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecoveryPolicy:
        policy = cls(
            max_execution_retries=int(data.get("max_execution_retries", 2)),
            max_startup_retries=int(data.get("max_startup_retries", 1)),
            max_correction_iterations=int(data.get("max_correction_iterations", 3)),
            max_resource_retries=int(data.get("max_resource_retries", 1)),
            max_total_recovery_attempts=int(data.get("max_total_recovery_attempts", 5)),
            allow_agent_hung_restart=bool(data.get("allow_agent_hung_restart", False)),
            hung_action=RecoveryDisposition(str(data.get("hung_action", "CANCEL")).upper()),
            timeout_action=RecoveryDisposition(str(data.get("timeout_action", "FAIL")).upper()),
            internal_error_action=RecoveryDisposition(str(data.get("internal_error_action", "FAIL")).upper()),
            scope_action=RecoveryDisposition(str(data.get("scope_action", "BLOCK")).upper()),
            permission_action=RecoveryDisposition(str(data.get("permission_action", "ESCALATE")).upper()),
            missing_context_action=RecoveryDisposition(str(data.get("missing_context_action", "BLOCK")).upper()),
            budget_action=RecoveryDisposition(str(data.get("budget_action", "BLOCK")).upper()),
            command_failure_action=RecoveryDisposition(str(data.get("command_failure_action", "FAIL")).upper()),
            dependency_action=RecoveryDisposition(str(data.get("dependency_action", "BLOCK")).upper()),
            unknown_action=RecoveryDisposition(str(data.get("unknown_action", "FAIL")).upper()),
        )
        policy.validate()
        return policy


# ==============================================================================
# 3. Failure Recovery Controller
# ==============================================================================


class FailureRecoveryController:
    """
    Deterministic controller responsible for evaluating Programmer execution failures
    against structured recovery policy rules and recording authoritative RecoveryDecisions.
    
    Critical Architectural Guarantees:
    1. DETERMINISTIC DISPOSITION: Permitted recovery action is decided strictly by
       structured facts, failure category, attempt counts, and explicit policy rules.
    2. BOUNDED ATTEMPTS: Never retries forever. Bounded by category budgets and global
       max_total_recovery_attempts.
    3. AUTHORITY INVARIANTS (IMMUTABLE):
       - Recovery NEVER expands filesystem scope (SCOPE failures -> BLOCK / ESCALATE).
       - Recovery NEVER expands command permissions (PERMISSION failures -> ESCALATE).
       - Recovery NEVER alters WorkOrder objectives or acceptance criteria.
       - Recovery NEVER modifies Manager authority or silently resets budgets.
    4. STRICT PROVENANCE & LINEAGE: Every decision tracks recovery_attempt, failure_id,
       execution_id, work_order_id, policy_rule, reason, and trace telemetry.
    5. NON-MUTATING CONTROLLER CONTRACT: Decides and records the permitted action;
       does NOT autonomously execute process restarts or workspace modifications.
    """

    def __init__(
        self,
        default_policy: Optional[RecoveryPolicy] = None,
        on_decision: Optional[Callable[[RecoveryDecision], None]] = None,
    ) -> None:
        self.default_policy = default_policy or RecoveryPolicy()
        self.default_policy.validate()
        self._on_decision = on_decision

        # In-memory session tracking
        self._attempts_by_execution: dict[str, int] = {}
        self._category_attempts: dict[str, dict[ProgrammerFailureCategory, int]] = {}
        self._decisions_by_execution: dict[str, list[RecoveryDecision]] = {}

    def evaluate_recovery(
        self,
        failure: ProgrammerFailure,
        policy: Optional[RecoveryPolicy] = None,
        work_order: Optional[ProgrammerWorkOrder] = None,
    ) -> RecoveryDecision:
        """
        Evaluate a ProgrammerFailure against RecoveryPolicy and return an auditable RecoveryDecision.
        
        Args:
            failure: Validated ProgrammerFailure instance from Phase 5.1 / 5.2 / 5.3.
            policy: Optional explicit RecoveryPolicy override.
            work_order: Optional ProgrammerWorkOrder for lineage verification and policy derivation.
            
        Returns:
            Validated RecoveryDecision recording the decided action, attempt number, and rule.
        """
        # ----------------------------------------------------------------------
        # Step 1: Validate Failure & Verify Lineage
        # ----------------------------------------------------------------------
        if failure is None:
            raise ProgrammerValidationError("ProgrammerFailure cannot be None.")
        failure.validate()

        if work_order is not None:
            if failure.work_order_id != work_order.work_order_id:
                raise ProgrammerLineageError(
                    f"Lineage mismatch: failure work_order_id '{failure.work_order_id}' "
                    f"does not match work order '{work_order.work_order_id}'."
                )

        # ----------------------------------------------------------------------
        # Step 2: Resolve and Validate Policy
        # ----------------------------------------------------------------------
        active_policy = policy
        if active_policy is None:
            active_policy = (
                RecoveryPolicy.from_work_order(work_order)
                if work_order is not None
                else self.default_policy
            )
        active_policy.validate()

        execution_id = failure.execution_id
        cat = failure.category

        # ----------------------------------------------------------------------
        # Step 3: Compute Attempt Numbers
        # ----------------------------------------------------------------------
        current_total = self._attempts_by_execution.get(execution_id, 0)
        next_total = current_total + 1

        exec_cat_counts = self._category_attempts.setdefault(execution_id, {})
        current_cat_count = exec_cat_counts.get(cat, 0)

        # ----------------------------------------------------------------------
        # Step 4: Evaluate Rules Deterministically
        # ----------------------------------------------------------------------
        action: RecoveryDisposition
        reason: str
        policy_rule: str

        # 4.0 Global Guard: Total Recovery Attempts Exhausted
        if current_total >= active_policy.max_total_recovery_attempts:
            action = RecoveryDisposition.FAIL
            reason = (
                f"Global recovery budget exhausted ({current_total}/{active_policy.max_total_recovery_attempts} total attempts). "
                "Halting further automated recovery."
            )
            policy_rule = "RULE_TOTAL_ATTEMPTS_EXHAUSTED"

        # 4.1 VERIFICATION_FAILURE: CORRECT if budget remains, else FAIL
        elif cat == ProgrammerFailureCategory.VERIFICATION_FAILURE:
            if current_cat_count < active_policy.max_correction_iterations:
                action = RecoveryDisposition.CORRECT
                reason = (
                    f"Verification failure within correction budget "
                    f"({current_cat_count + 1}/{active_policy.max_correction_iterations}). "
                    "Permitted bounded self-correction turn."
                )
                policy_rule = "RULE_VERIFICATION_CORRECTION_BUDGET"
            else:
                action = RecoveryDisposition.FAIL
                reason = (
                    f"Verification correction budget exhausted "
                    f"({current_cat_count}/{active_policy.max_correction_iterations}). "
                    "Halting bounded correction loop."
                )
                policy_rule = "RULE_VERIFICATION_CORRECTION_EXHAUSTED"

        # 4.2 AGENT_CRASH: RETRY if retry budget remains, else FAIL or ESCALATE
        elif cat == ProgrammerFailureCategory.AGENT_CRASH:
            if not failure.retryable or failure.severity == ProgrammerFailureSeverity.CRITICAL:
                action = RecoveryDisposition.ESCALATE
                reason = "Agent crash designated critical/non-retryable; escalating to Manager."
                policy_rule = "RULE_CRASH_NON_RETRYABLE"
            elif current_cat_count < active_policy.max_execution_retries:
                action = RecoveryDisposition.RETRY
                reason = (
                    f"Agent crash within execution retry budget "
                    f"({current_cat_count + 1}/{active_policy.max_execution_retries}). "
                    "Permitted execution retry."
                )
                policy_rule = "RULE_AGENT_CRASH_RETRY_BUDGET"
            else:
                action = RecoveryDisposition.FAIL
                reason = (
                    f"Agent crash retry budget exhausted "
                    f"({current_cat_count}/{active_policy.max_execution_retries}). "
                    "Halting execution."
                )
                policy_rule = "RULE_AGENT_CRASH_RETRIES_EXHAUSTED"

        # 4.3 AGENT_STARTUP: RETRY if startup retry budget remains
        elif cat == ProgrammerFailureCategory.AGENT_STARTUP:
            if current_cat_count < active_policy.max_startup_retries:
                action = RecoveryDisposition.RETRY
                reason = (
                    f"Agent startup failure within startup retry budget "
                    f"({current_cat_count + 1}/{active_policy.max_startup_retries}). "
                    "Permitted agent initialization retry."
                )
                policy_rule = "RULE_AGENT_STARTUP_RETRY_BUDGET"
            else:
                action = RecoveryDisposition.FAIL
                reason = (
                    f"Agent startup retry budget exhausted "
                    f"({current_cat_count}/{active_policy.max_startup_retries}). "
                    "Halting execution."
                )
                policy_rule = "RULE_AGENT_STARTUP_RETRIES_EXHAUSTED"

        # 4.4 AGENT_HUNG: CANCEL or RESTART only if explicitly supported by policy
        elif cat == ProgrammerFailureCategory.AGENT_HUNG:
            if active_policy.allow_agent_hung_restart and current_cat_count < active_policy.max_execution_retries:
                action = RecoveryDisposition.RETRY
                reason = (
                    f"Agent hung state detected; restart policy explicitly authorized "
                    f"({current_cat_count + 1}/{active_policy.max_execution_retries})."
                )
                policy_rule = "RULE_AGENT_HUNG_RESTART_ALLOWED"
            else:
                action = active_policy.hung_action
                reason = (
                    "Agent hung state detected without authorized restart policy; "
                    f"applying policy action '{active_policy.hung_action.value}'."
                )
                policy_rule = "RULE_AGENT_HUNG_POLICY"

        # 4.5 TIMEOUT: STOP and escalate/fail according to policy
        elif cat == ProgrammerFailureCategory.TIMEOUT:
            action = active_policy.timeout_action
            reason = (
                "Execution exceeded configured timeout; stopping execution without "
                f"automated retry ({active_policy.timeout_action.value})."
            )
            policy_rule = "RULE_TIMEOUT_POLICY"

        # 4.6 PERMISSION: ESCALATE (Recovery must NEVER expand permissions)
        elif cat == ProgrammerFailureCategory.PERMISSION:
            action = RecoveryDisposition.ESCALATE
            reason = (
                "Permission violation detected. Authority cannot be expanded through automated "
                "recovery; escalating to Manager."
            )
            policy_rule = "RULE_PERMISSION_ESCALATE"

        # 4.7 SCOPE: ESCALATE or BLOCK (Recovery must NEVER expand filesystem scope)
        elif cat == ProgrammerFailureCategory.SCOPE:
            action = active_policy.scope_action
            reason = (
                "Filesystem scope violation detected. Writable boundaries cannot be expanded "
                f"autonomously; establishing blocker for Manager decision ({active_policy.scope_action.value})."
            )
            policy_rule = "RULE_SCOPE_VIOLATION"

        # 4.8 RESOURCE: RETRY only if explicitly retryable
        elif cat == ProgrammerFailureCategory.RESOURCE:
            if failure.retryable and current_cat_count < active_policy.max_resource_retries:
                action = RecoveryDisposition.RETRY
                reason = (
                    f"Transient resource failure explicitly designated retryable "
                    f"({current_cat_count + 1}/{active_policy.max_resource_retries})."
                )
                policy_rule = "RULE_RESOURCE_TRANSIENT_RETRY"
            else:
                action = RecoveryDisposition.ESCALATE
                reason = "Resource exhaustion/defect is permanent or retry budget exhausted; escalating to Manager."
                policy_rule = "RULE_RESOURCE_NON_RETRYABLE"

        # 4.9 BUDGET: ESCALATE or BLOCK (Never silently reset budgets)
        elif cat == ProgrammerFailureCategory.BUDGET:
            action = active_policy.budget_action
            reason = (
                "Task budget exhaustion detected. Budgets cannot be silently reset; "
                f"establishing blocker for Manager review ({active_policy.budget_action.value})."
            )
            policy_rule = "RULE_BUDGET_EXHAUSTED"

        # 4.10 MISSING_CONTEXT: ESCALATE or BLOCK
        elif cat == ProgrammerFailureCategory.MISSING_CONTEXT:
            action = active_policy.missing_context_action
            reason = (
                "Critical implementation context missing; establishing blocker for "
                f"Manager clarification ({active_policy.missing_context_action.value})."
            )
            policy_rule = "RULE_MISSING_CONTEXT_BLOCK"

        # 4.11 CANCELLATION: CANCEL
        elif cat == ProgrammerFailureCategory.CANCELLATION:
            action = RecoveryDisposition.CANCEL
            reason = "Execution cancellation requested; terminating cleanly without further action."
            policy_rule = "RULE_CANCELLATION_TERMINAL"

        # 4.12 INTERNAL: FAIL or ESCALATE
        elif cat == ProgrammerFailureCategory.INTERNAL:
            action = active_policy.internal_error_action
            reason = f"Internal Programmer subsystem defect: {failure.message}."
            policy_rule = "RULE_INTERNAL_ERROR"

        # 4.13 COMMAND_FAILURE: FAIL or policy disposition
        elif cat == ProgrammerFailureCategory.COMMAND_FAILURE:
            action = active_policy.command_failure_action
            reason = f"Command execution failure: {failure.message}."
            policy_rule = "RULE_COMMAND_FAILURE"

        # 4.14 DEPENDENCY: BLOCK or ESCALATE
        elif cat == ProgrammerFailureCategory.DEPENDENCY:
            action = active_policy.dependency_action
            reason = f"Unresolved environment or repository dependency: {failure.message}."
            policy_rule = "RULE_DEPENDENCY_BLOCK"

        # 4.15 UNKNOWN
        else:
            action = active_policy.unknown_action
            reason = f"Unclassified execution failure: {failure.message}."
            policy_rule = "RULE_UNKNOWN_FAILURE"

        # ----------------------------------------------------------------------
        # Step 5: Update Controller State
        # ----------------------------------------------------------------------
        self._attempts_by_execution[execution_id] = next_total
        exec_cat_counts[cat] = current_cat_count + 1

        # ----------------------------------------------------------------------
        # Step 6: Assemble, Validate, and Store RecoveryDecision
        # ----------------------------------------------------------------------
        decision = RecoveryDecision.create(
            failure=failure,
            recovery_attempt=next_total,
            action=action,
            reason=reason,
            policy_rule=policy_rule,
            trace={
                "previous_total_attempts": current_total,
                "category_attempt": current_cat_count + 1,
                "failure_source": failure.source.value if hasattr(failure.source, "value") else str(failure.source),
            },
            metadata={
                "failure_message": failure.message,
                "failure_severity": failure.severity.value if hasattr(failure.severity, "value") else str(failure.severity),
            },
        )

        self._decisions_by_execution.setdefault(execution_id, []).append(decision)
        logger.info(
            f"Recovery decided for execution '{execution_id}' (attempt {next_total}): "
            f"{action.value} [{policy_rule}] - {reason}"
        )

        if self._on_decision:
            try:
                self._on_decision(decision)
            except Exception as cb_err:
                logger.warning(f"Error in on_decision callback: {cb_err}")

        return decision

    def get_decisions(self, execution_id: str) -> list[RecoveryDecision]:
        """Retrieve all recovery decisions made for an execution session."""
        return list(self._decisions_by_execution.get(execution_id, []))

    def get_last_decision(self, execution_id: str) -> Optional[RecoveryDecision]:
        """Retrieve the most recent recovery decision made for an execution session."""
        decisions = self._decisions_by_execution.get(execution_id, [])
        return decisions[-1] if decisions else None

    def get_attempt_count(self, execution_id: str) -> int:
        """Get total number of recovery attempts evaluated for an execution."""
        return self._attempts_by_execution.get(execution_id, 0)

    def get_category_attempt_count(self, execution_id: str, category: ProgrammerFailureCategory) -> int:
        """Get number of recovery attempts evaluated for a specific failure category."""
        return self._category_attempts.get(execution_id, {}).get(category, 0)

    def reset(self, execution_id: Optional[str] = None) -> None:
        """Reset in-memory recovery tracking for a specific execution or all executions."""
        if execution_id:
            self._attempts_by_execution.pop(execution_id, None)
            self._category_attempts.pop(execution_id, None)
            self._decisions_by_execution.pop(execution_id, None)
        else:
            self._attempts_by_execution.clear()
            self._category_attempts.clear()
            self._decisions_by_execution.clear()

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Optional, Sequence, Union

from core.programmer.contracts.identifiers import (
    new_failure_id,
    validate_execution_id,
    validate_failure_id,
    validate_work_order_id,
)
from core.programmer.errors import ProgrammerLineageError, ProgrammerValidationError
from core.programmer.types import (
    FailureSourceType,
    ProgrammerFailureCategory,
    ProgrammerFailureSeverity,
    RecoveryDisposition,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# Deterministic mapping of failure category to default retryability and recovery disposition.
# Policy rule: Never infer retryability from vague natural-language agent text.
# Decisions must be based on structured facts and policy.
CATEGORY_POLICY_MAP: dict[ProgrammerFailureCategory, tuple[bool, RecoveryDisposition]] = {
    ProgrammerFailureCategory.AGENT_STARTUP: (True, RecoveryDisposition.RETRY),
    ProgrammerFailureCategory.AGENT_CRASH: (True, RecoveryDisposition.RETRY),
    ProgrammerFailureCategory.AGENT_HUNG: (True, RecoveryDisposition.RETRY),
    ProgrammerFailureCategory.COMMAND_FAILURE: (False, RecoveryDisposition.FAIL),
    ProgrammerFailureCategory.VERIFICATION_FAILURE: (False, RecoveryDisposition.CORRECT),
    ProgrammerFailureCategory.TIMEOUT: (False, RecoveryDisposition.FAIL),
    ProgrammerFailureCategory.CANCELLATION: (False, RecoveryDisposition.CANCEL),
    ProgrammerFailureCategory.PERMISSION: (False, RecoveryDisposition.BLOCK),
    ProgrammerFailureCategory.SCOPE: (False, RecoveryDisposition.BLOCK),
    ProgrammerFailureCategory.RESOURCE: (False, RecoveryDisposition.ESCALATE),
    ProgrammerFailureCategory.BUDGET: (False, RecoveryDisposition.FAIL),
    ProgrammerFailureCategory.DEPENDENCY: (False, RecoveryDisposition.BLOCK),
    ProgrammerFailureCategory.MISSING_CONTEXT: (False, RecoveryDisposition.BLOCK),
    ProgrammerFailureCategory.INTERNAL: (False, RecoveryDisposition.FAIL),
    ProgrammerFailureCategory.UNKNOWN: (False, RecoveryDisposition.FAIL),
}


def determine_default_disposition(
    category: Union[ProgrammerFailureCategory, str],
    severity: Optional[Union[ProgrammerFailureSeverity, str]] = None,
) -> tuple[bool, RecoveryDisposition]:
    """
    Deterministic policy function resolving (retryable, recovery_disposition)
    from failure category and severity without relying on agent claims.
    """
    cat = category if isinstance(category, ProgrammerFailureCategory) else ProgrammerFailureCategory(str(category).upper())
    sev = (
        severity
        if isinstance(severity, ProgrammerFailureSeverity)
        else (ProgrammerFailureSeverity(str(severity).upper()) if severity else ProgrammerFailureSeverity.HIGH)
    )

    retryable, disposition = CATEGORY_POLICY_MAP.get(cat, (False, RecoveryDisposition.FAIL))

    # Critical severity on crash or hung escalates rather than retrying blindly
    if sev == ProgrammerFailureSeverity.CRITICAL and cat in (
        ProgrammerFailureCategory.AGENT_CRASH,
        ProgrammerFailureCategory.AGENT_HUNG,
    ):
        return (False, RecoveryDisposition.ESCALATE)

    return (retryable, disposition)


@dataclass
class ProgrammerFailure:
    """
    Structured domain model representing a Programmer execution failure.
    
    Guarantees:
    - Maintains causal lineage back to execution_id and work_order_id.
    - Category taxonomy cleanly separates agent lifecycle, command execution,
      scope boundaries, and verification verdicts.
    - Determines high-level recovery disposition deterministically based on structured facts.
    - Strictly forbids inferring retryability from natural-language agent claims.
    """
    failure_id: str
    execution_id: str
    work_order_id: str
    category: ProgrammerFailureCategory
    message: str
    severity: ProgrammerFailureSeverity = ProgrammerFailureSeverity.HIGH
    observed_at: str = field(default_factory=utc_now)
    source: FailureSourceType = FailureSourceType.UNKNOWN
    retryable: bool = False
    recovery_disposition: RecoveryDisposition = RecoveryDisposition.FAIL
    evidence: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Coerce enum values if passed as strings
        if isinstance(self.category, str):
            try:
                self.category = ProgrammerFailureCategory(self.category.upper())
            except ValueError:
                self.category = ProgrammerFailureCategory.UNKNOWN

        if isinstance(self.severity, str):
            try:
                self.severity = ProgrammerFailureSeverity(self.severity.upper())
            except ValueError:
                self.severity = ProgrammerFailureSeverity.HIGH

        if isinstance(self.source, str):
            try:
                self.source = FailureSourceType(self.source.upper())
            except ValueError:
                self.source = FailureSourceType.UNKNOWN

        if isinstance(self.recovery_disposition, str):
            try:
                self.recovery_disposition = RecoveryDisposition(self.recovery_disposition.upper())
            except ValueError:
                self.recovery_disposition = RecoveryDisposition.FAIL

        # Normalize evidence list
        if self.evidence:
            self.evidence = [str(e) for e in self.evidence if e and str(e).strip()]
        else:
            self.evidence = []

        # Forward identifiers to trace if trace is empty
        if not self.trace:
            self.trace = {
                "failure_id": self.failure_id,
                "execution_id": self.execution_id,
                "work_order_id": self.work_order_id,
                "category": self.category.value if isinstance(self.category, ProgrammerFailureCategory) else str(self.category),
            }

    def validate(self) -> None:
        """
        Validate structural integrity, lineage invariants, and policy consistency.
        Raises ProgrammerValidationError or ProgrammerLineageError on invalid records.
        """
        # 1. Identifier and Lineage Validation
        if not self.failure_id or not str(self.failure_id).strip():
            raise ProgrammerValidationError("failure_id must be a non-empty string.", field_name="failure_id")
        validate_failure_id(self.failure_id)

        if not self.execution_id or not str(self.execution_id).strip():
            raise ProgrammerLineageError("ProgrammerFailure must have a non-empty execution_id.")
        validate_execution_id(self.execution_id)

        if not self.work_order_id or not str(self.work_order_id).strip():
            raise ProgrammerLineageError("ProgrammerFailure must have a non-empty work_order_id.")
        validate_work_order_id(self.work_order_id)

        # 2. Message Validation
        if not self.message or not str(self.message).strip():
            raise ProgrammerValidationError("Failure message must be a non-empty string.", field_name="message")

        # 3. Enum Integrity
        if not isinstance(self.category, ProgrammerFailureCategory):
            raise ProgrammerValidationError(f"Invalid failure category '{self.category}'.", field_name="category")

        if not isinstance(self.severity, ProgrammerFailureSeverity):
            raise ProgrammerValidationError(f"Invalid failure severity '{self.severity}'.", field_name="severity")

        if not isinstance(self.recovery_disposition, RecoveryDisposition):
            raise ProgrammerValidationError(f"Invalid recovery disposition '{self.recovery_disposition}'.", field_name="recovery_disposition")

        # 4. Policy Invariants: Contradiction Guards
        # Scope / Permission violations can NEVER be automatically retried without policy grant
        if self.category in (ProgrammerFailureCategory.SCOPE, ProgrammerFailureCategory.PERMISSION) and self.retryable:
            raise ProgrammerValidationError(
                f"Policy contradiction: {self.category.value} failure cannot be marked retryable. "
                "Authority cannot be expanded through automated retry.",
                field_name="retryable",
            )

        # Cancellation can NEVER be marked retryable or have a non-CANCEL disposition
        if self.category == ProgrammerFailureCategory.CANCELLATION:
            if self.retryable:
                raise ProgrammerValidationError(
                    "Policy contradiction: CANCELLATION cannot be retryable.",
                    field_name="retryable",
                )
            if self.recovery_disposition != RecoveryDisposition.CANCEL:
                raise ProgrammerValidationError(
                    f"Policy contradiction: CANCELLATION disposition must be CANCEL, got {self.recovery_disposition.value}.",
                    field_name="recovery_disposition",
                )

        # Budget exhaustion can NEVER be retryable
        if self.category == ProgrammerFailureCategory.BUDGET and self.retryable:
            raise ProgrammerValidationError(
                "Policy contradiction: BUDGET exhaustion cannot be retryable.",
                field_name="retryable",
            )

        # Evidence references must be valid non-empty strings
        for ev in self.evidence:
            if not isinstance(ev, str) or not ev.strip():
                raise ProgrammerValidationError(
                    "All evidence references must be non-empty strings.",
                    field_name="evidence",
                )

    @property
    def is_retryable(self) -> bool:
        """True if this failure is designated safe for retry."""
        return self.retryable

    @property
    def requires_escalation(self) -> bool:
        """True if this failure requires escalation to Manager."""
        return self.recovery_disposition == RecoveryDisposition.ESCALATE

    @property
    def requires_blocker(self) -> bool:
        """True if this failure requires establishing a formal ProgrammerBlocker."""
        return self.recovery_disposition == RecoveryDisposition.BLOCK

    @property
    def is_scope_or_permission(self) -> bool:
        """True if this failure represents an unauthorized scope or permission boundary violation."""
        return self.category in (ProgrammerFailureCategory.SCOPE, ProgrammerFailureCategory.PERMISSION)

    @property
    def is_verification_failure(self) -> bool:
        """True if the failure represents an implementation verification check or acceptance failure."""
        return self.category == ProgrammerFailureCategory.VERIFICATION_FAILURE

    @property
    def is_agent_failure(self) -> bool:
        """True if the failure originates from coding-agent startup, crash, or hang."""
        return self.category in (
            ProgrammerFailureCategory.AGENT_STARTUP,
            ProgrammerFailureCategory.AGENT_CRASH,
            ProgrammerFailureCategory.AGENT_HUNG,
        )

    @classmethod
    def create(
        cls,
        execution_id: str,
        work_order_id: str,
        category: Union[ProgrammerFailureCategory, str],
        message: str,
        severity: Optional[Union[ProgrammerFailureSeverity, str]] = None,
        source: Optional[Union[FailureSourceType, str]] = None,
        failure_id: Optional[str] = None,
        retryable: Optional[bool] = None,
        recovery_disposition: Optional[Union[RecoveryDisposition, str]] = None,
        evidence: Optional[Sequence[str]] = None,
        trace: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ProgrammerFailure:
        """
        Factory creating a validated ProgrammerFailure with policy-determined default disposition.
        """
        cat = category if isinstance(category, ProgrammerFailureCategory) else ProgrammerFailureCategory(str(category).upper())
        sev = (
            severity
            if isinstance(severity, ProgrammerFailureSeverity)
            else (ProgrammerFailureSeverity(str(severity).upper()) if severity else ProgrammerFailureSeverity.HIGH)
        )

        def_retryable, def_disposition = determine_default_disposition(cat, sev)

        final_retryable = retryable if retryable is not None else def_retryable
        final_disposition = (
            (recovery_disposition if isinstance(recovery_disposition, RecoveryDisposition) else RecoveryDisposition(str(recovery_disposition).upper()))
            if recovery_disposition is not None
            else def_disposition
        )

        src = (
            source
            if isinstance(source, FailureSourceType)
            else (FailureSourceType(str(source).upper()) if source else FailureSourceType.UNKNOWN)
        )

        failure = cls(
            failure_id=failure_id or new_failure_id(),
            execution_id=execution_id,
            work_order_id=work_order_id,
            category=cat,
            severity=sev,
            message=message,
            source=src,
            retryable=final_retryable,
            recovery_disposition=final_disposition,
            evidence=list(evidence or []),
            trace=dict(trace or {}),
            metadata=dict(metadata or {}),
        )
        failure.validate()
        return failure

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "failure_id": self.failure_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "category": self.category.value if isinstance(self.category, ProgrammerFailureCategory) else str(self.category),
            "severity": self.severity.value if isinstance(self.severity, ProgrammerFailureSeverity) else str(self.severity),
            "message": self.message,
            "observed_at": self.observed_at,
            "source": self.source.value if isinstance(self.source, FailureSourceType) else str(self.source),
            "retryable": self.retryable,
            "recovery_disposition": self.recovery_disposition.value if isinstance(self.recovery_disposition, RecoveryDisposition) else str(self.recovery_disposition),
            "evidence": list(self.evidence),
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgrammerFailure:
        """Reconstruct a validated ProgrammerFailure instance from dictionary."""
        failure = cls(
            failure_id=str(data.get("failure_id", "")),
            execution_id=str(data.get("execution_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            category=data.get("category", ProgrammerFailureCategory.UNKNOWN),
            severity=data.get("severity", ProgrammerFailureSeverity.HIGH),
            message=str(data.get("message", "")),
            observed_at=str(data.get("observed_at", utc_now())),
            source=data.get("source", FailureSourceType.UNKNOWN),
            retryable=bool(data.get("retryable", False)),
            recovery_disposition=data.get("recovery_disposition", RecoveryDisposition.FAIL),
            evidence=list(data.get("evidence", [])),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )
        failure.validate()
        return failure

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Optional

from core.programmer.contracts.identifiers import (
    new_verification_check_id,
    new_verification_evidence_id,
    validate_execution_id,
    validate_verification_check_id,
    validate_verification_evidence_id,
    validate_work_order_id,
)
from core.programmer.errors import ProgrammerLineageError, ProgrammerValidationError
from core.programmer.types import (
    VerificationCheckType,
    VerificationEvidenceSourceType,
    VerificationStatus,
    VerificationSummaryStatus,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def compute_sha256(content: str | bytes) -> str:
    """Compute SHA-256 hex digest for cryptographic integrity."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


@dataclass
class VerificationEvidence:
    """
    Structured proof of an observed verification execution or outcome.
    Explicitly distinguishes authoritative AutonomOS observation from unverified agent claims.
    """
    evidence_id: str = field(default_factory=new_verification_evidence_id)
    execution_id: str = ""
    work_order_id: str = ""
    source_type: VerificationEvidenceSourceType = VerificationEvidenceSourceType.COMMAND_OUTPUT
    source_reference: Optional[str] = None
    description: str = ""
    observed_at: str = field(default_factory=utc_now)
    is_agent_claim: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    checksum: Optional[str] = None
    trace: Optional[Any] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.source_type, str):
            try:
                self.source_type = VerificationEvidenceSourceType(self.source_type)
            except ValueError:
                self.source_type = VerificationEvidenceSourceType.AGENT_CLAIM if self.is_agent_claim else VerificationEvidenceSourceType.COMMAND_OUTPUT

        # If source_type is AGENT_CLAIM, automatically ensure is_agent_claim is True
        if self.source_type == VerificationEvidenceSourceType.AGENT_CLAIM:
            self.is_agent_claim = True

        if not self.checksum:
            try:
                payload = f"{self.evidence_id}|{self.execution_id}|{self.work_order_id}|{self.source_type.value}|{self.is_agent_claim}|{json.dumps(self.data, sort_keys=True)}"
            except Exception:
                payload = f"{self.evidence_id}|{self.execution_id}|{self.work_order_id}|{self.source_type.value}|{self.is_agent_claim}"
            self.checksum = compute_sha256(payload)

    def validate(self) -> None:
        """Validate identifier syntax and required causal lineage."""
        validate_verification_evidence_id(self.evidence_id)
        if not self.execution_id:
            raise ProgrammerLineageError("VerificationEvidence must have a non-empty execution_id.")
        validate_execution_id(self.execution_id)
        if not self.work_order_id:
            raise ProgrammerLineageError("VerificationEvidence must have a non-empty work_order_id.")
        validate_work_order_id(self.work_order_id)

    def is_authoritative(self) -> bool:
        """Return True only if this evidence is an actual observed result, not an agent narration/claim."""
        return not self.is_agent_claim

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "source_type": self.source_type.value if isinstance(self.source_type, VerificationEvidenceSourceType) else str(self.source_type),
            "source_reference": self.source_reference,
            "description": self.description,
            "observed_at": self.observed_at,
            "is_agent_claim": self.is_agent_claim,
            "data": dict(self.data),
            "checksum": self.checksum,
            "trace": self.trace.to_dict() if hasattr(self.trace, "to_dict") else (dict(self.trace) if isinstance(self.trace, dict) else self.trace),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationEvidence:
        st_raw = data.get("source_type", VerificationEvidenceSourceType.COMMAND_OUTPUT.value)
        try:
            source_type = VerificationEvidenceSourceType(st_raw)
        except (ValueError, TypeError):
            source_type = VerificationEvidenceSourceType.COMMAND_OUTPUT

        return cls(
            evidence_id=str(data.get("evidence_id", "")),
            execution_id=str(data.get("execution_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            source_type=source_type,
            source_reference=data.get("source_reference"),
            description=str(data.get("description", "")),
            observed_at=str(data.get("observed_at", utc_now())),
            is_agent_claim=bool(data.get("is_agent_claim", False)),
            data=dict(data.get("data", {})),
            checksum=data.get("checksum"),
            trace=data.get("trace"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class VerificationCheck:
    """
    Deterministic record of an individual verification action (command, test, lint, etc.) executed by AutonomOS.
    Preserves timing, exit codes, output snippets, and links to verified evidence.
    """
    check_id: str = field(default_factory=new_verification_check_id)
    execution_id: str = ""
    work_order_id: str = ""
    check_type: VerificationCheckType = VerificationCheckType.TEST
    command: Optional[str] = None
    status: VerificationStatus = VerificationStatus.NOT_RUN
    exit_code: Optional[int] = None
    duration_ms: Optional[float] = None
    output_reference: Optional[str] = None
    output_snippet: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    evidence: list[str] = field(default_factory=list)
    trace: Optional[Any] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.check_type, str):
            try:
                self.check_type = VerificationCheckType(self.check_type)
            except ValueError:
                self.check_type = VerificationCheckType.CUSTOM

        if isinstance(self.status, str):
            try:
                self.status = VerificationStatus(self.status)
            except ValueError:
                self.status = VerificationStatus.NOT_VERIFIED

    @property
    def duration(self) -> Optional[float]:
        """Convenience alias for duration_ms."""
        return self.duration_ms

    def validate(self) -> None:
        """Validate check identifier, causal lineage, and status integrity."""
        validate_verification_check_id(self.check_id)
        if not self.execution_id:
            raise ProgrammerLineageError("VerificationCheck must have a non-empty execution_id.")
        validate_execution_id(self.execution_id)
        if not self.work_order_id:
            raise ProgrammerLineageError("VerificationCheck must have a non-empty work_order_id.")
        validate_work_order_id(self.work_order_id)

        # Status integrity validations
        if self.status == VerificationStatus.PASS:
            if self.exit_code is not None and self.exit_code != 0:
                raise ProgrammerValidationError(
                    f"VerificationCheck '{self.check_id}' marked PASS cannot have non-zero exit_code {self.exit_code}."
                )
        elif self.status == VerificationStatus.NOT_RUN:
            if self.completed_at is not None:
                raise ProgrammerValidationError(
                    f"VerificationCheck '{self.check_id}' with status NOT_RUN cannot have completed_at timestamp."
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "check_type": self.check_type.value if isinstance(self.check_type, VerificationCheckType) else str(self.check_type),
            "command": self.command,
            "status": self.status.value if isinstance(self.status, VerificationStatus) else str(self.status),
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "output_reference": self.output_reference,
            "output_snippet": self.output_snippet,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "evidence": list(self.evidence),
            "trace": self.trace.to_dict() if hasattr(self.trace, "to_dict") else (dict(self.trace) if isinstance(self.trace, dict) else self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationCheck:
        ct_raw = data.get("check_type", VerificationCheckType.TEST.value)
        try:
            check_type = VerificationCheckType(ct_raw)
        except (ValueError, TypeError):
            check_type = VerificationCheckType.CUSTOM

        st_raw = data.get("status", VerificationStatus.NOT_RUN.value)
        try:
            status = VerificationStatus(st_raw)
        except (ValueError, TypeError):
            status = VerificationStatus.NOT_VERIFIED

        return cls(
            check_id=str(data.get("check_id", "")),
            execution_id=str(data.get("execution_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            check_type=check_type,
            command=data.get("command"),
            status=status,
            exit_code=data.get("exit_code"),
            duration_ms=data.get("duration_ms"),
            output_reference=data.get("output_reference"),
            output_snippet=data.get("output_snippet"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            evidence=list(data.get("evidence", [])),
            trace=data.get("trace"),
            metadata=dict(data.get("metadata", {})),
            created_at=str(data.get("created_at", utc_now())),
        )


@dataclass
class AcceptanceResult:
    """
    Evaluation record linking an individual AcceptanceCriterion to verified evidence.
    Enforces the invariant that a PASS verdict requires actual supporting evidence.
    """
    criterion_id: str
    status: VerificationStatus = VerificationStatus.NOT_VERIFIED
    explanation: str = ""
    evidence: list[str] = field(default_factory=list)
    execution_id: str = ""
    work_order_id: str = ""
    trace: Optional[Any] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = VerificationStatus(self.status)
            except ValueError:
                self.status = VerificationStatus.NOT_VERIFIED

    def validate(self, criteria_catalog: Optional[Any] = None) -> None:
        """Validate acceptance criterion evaluation and evidence linkage."""
        if not self.criterion_id:
            raise ProgrammerValidationError("AcceptanceResult must have a non-empty criterion_id.")

        if self.execution_id:
            validate_execution_id(self.execution_id)
        if self.work_order_id:
            validate_work_order_id(self.work_order_id)

        # PASS requires evidence linkage
        if self.status == VerificationStatus.PASS:
            if not self.evidence:
                raise ProgrammerValidationError(
                    f"AcceptanceResult for '{self.criterion_id}' marked PASS must reference at least one evidence item."
                )

        # Validate against catalog if provided
        if criteria_catalog is not None:
            catalog_ids: set[str] = set()
            if isinstance(criteria_catalog, (list, tuple, set)):
                for item in criteria_catalog:
                    if hasattr(item, "criterion_id"):
                        catalog_ids.add(item.criterion_id)
                    elif isinstance(item, str):
                        catalog_ids.add(item)
            elif isinstance(criteria_catalog, dict):
                catalog_ids = set(criteria_catalog.keys())

            if catalog_ids and self.criterion_id not in catalog_ids:
                raise ProgrammerValidationError(
                    f"Acceptance criterion '{self.criterion_id}' not found in provided criteria catalog."
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "status": self.status.value if isinstance(self.status, VerificationStatus) else str(self.status),
            "explanation": self.explanation,
            "evidence": list(self.evidence),
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "trace": self.trace.to_dict() if hasattr(self.trace, "to_dict") else (dict(self.trace) if isinstance(self.trace, dict) else self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AcceptanceResult:
        st_raw = data.get("status", VerificationStatus.NOT_VERIFIED.value)
        try:
            status = VerificationStatus(st_raw)
        except (ValueError, TypeError):
            status = VerificationStatus.NOT_VERIFIED

        return cls(
            criterion_id=str(data.get("criterion_id", "")),
            status=status,
            explanation=str(data.get("explanation", "")),
            evidence=list(data.get("evidence", [])),
            execution_id=str(data.get("execution_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            trace=data.get("trace"),
            metadata=dict(data.get("metadata", {})),
            created_at=str(data.get("created_at", utc_now())),
        )


@dataclass
class VerificationSummary:
    """
    Authoritative summary of all verification performed for a Programmer execution attempt.
    Aggregates checks, acceptance results, repository diffs, and evidence into an immutable audit package.
    """
    overall_status: VerificationSummaryStatus | VerificationStatus | str
    execution_id: str
    work_order_id: str
    checks: list[VerificationCheck] = field(default_factory=list)
    verification_checks: list[VerificationCheck] = field(default_factory=list)
    acceptance_results: list[AcceptanceResult] = field(default_factory=list)
    diff_verification: Optional[Any] = None
    evidence: list[VerificationEvidence] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    verified_at: str = field(default_factory=utc_now)
    timestamps: dict[str, str] = field(default_factory=dict)
    summary_text: str = ""
    trace: Optional[Any] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.overall_status, (VerificationStatus, VerificationSummaryStatus)):
            pass
        elif isinstance(self.overall_status, str):
            try:
                self.overall_status = VerificationSummaryStatus(self.overall_status.upper())
            except ValueError:
                try:
                    self.overall_status = VerificationStatus(self.overall_status.upper())
                except ValueError:
                    self.overall_status = VerificationSummaryStatus.UNVERIFIED

        # Synchronize checks and verification_checks
        if not self.verification_checks and self.checks:
            self.verification_checks = list(self.checks)
        elif not self.checks and self.verification_checks:
            self.checks = list(self.verification_checks)

        # Synchronize timestamps
        if self.verified_at and "verified_at" not in self.timestamps:
            self.timestamps["verified_at"] = self.verified_at
        elif "verified_at" in self.timestamps and not self.verified_at:
            self.verified_at = self.timestamps["verified_at"]

        # Rehydrate diff_verification if passed as dict
        if isinstance(self.diff_verification, dict):
            try:
                from core.programmer.contracts.diff_verifier import DiffVerification
                self.diff_verification = DiffVerification.from_dict(self.diff_verification)
            except Exception:
                pass

    @property
    def is_verified(self) -> bool:
        return self.overall_status in (VerificationSummaryStatus.VERIFIED, VerificationStatus.PASS, "VERIFIED", "PASS")

    @property
    def is_partially_verified(self) -> bool:
        return self.overall_status in (VerificationSummaryStatus.PARTIALLY_VERIFIED, "PARTIALLY_VERIFIED")

    @property
    def is_failed(self) -> bool:
        return self.overall_status in (VerificationSummaryStatus.FAILED, VerificationStatus.FAIL, VerificationStatus.ERROR, "FAILED", "FAIL")

    @property
    def is_unverified(self) -> bool:
        return self.overall_status in (VerificationSummaryStatus.UNVERIFIED, VerificationStatus.NOT_VERIFIED, "UNVERIFIED", "NOT_VERIFIED")

    def validate(self) -> None:
        """
        Validate cross-entity lineage, referential integrity, and authoritativeness:
        1. Execution and WorkOrder lineage must match exactly across all components.
        2. All evidence referenced by checks and acceptance results must exist in evidence list.
        3. Agent narration alone can NEVER satisfy PASS / VERIFIED.
        """
        if not self.execution_id:
            raise ProgrammerLineageError("VerificationSummary must have a non-empty execution_id.")
        validate_execution_id(self.execution_id)
        if not self.work_order_id:
            raise ProgrammerLineageError("VerificationSummary must have a non-empty work_order_id.")
        validate_work_order_id(self.work_order_id)

        # 1. Lineage validation
        for chk in self.verification_checks:
            chk.validate()
            if chk.execution_id != self.execution_id:
                raise ProgrammerLineageError(
                    f"VerificationCheck '{chk.check_id}' execution_id '{chk.execution_id}' does not match VerificationSummary execution_id '{self.execution_id}'."
                )
            if chk.work_order_id != self.work_order_id:
                raise ProgrammerLineageError(
                    f"VerificationCheck '{chk.check_id}' work_order_id '{chk.work_order_id}' does not match VerificationSummary work_order_id '{self.work_order_id}'."
                )

        for res in self.acceptance_results:
            res.validate()
            if res.execution_id and res.execution_id != self.execution_id:
                raise ProgrammerLineageError(
                    f"AcceptanceResult for '{res.criterion_id}' execution_id '{res.execution_id}' does not match VerificationSummary execution_id '{self.execution_id}'."
                )
            if res.work_order_id and res.work_order_id != self.work_order_id:
                raise ProgrammerLineageError(
                    f"AcceptanceResult for '{res.criterion_id}' work_order_id '{res.work_order_id}' does not match VerificationSummary work_order_id '{self.work_order_id}'."
                )

        for ev in self.evidence:
            ev.validate()
            if ev.execution_id != self.execution_id:
                raise ProgrammerLineageError(
                    f"VerificationEvidence '{ev.evidence_id}' execution_id '{ev.execution_id}' does not match VerificationSummary execution_id '{self.execution_id}'."
                )
            if ev.work_order_id != self.work_order_id:
                raise ProgrammerLineageError(
                    f"VerificationEvidence '{ev.evidence_id}' work_order_id '{ev.work_order_id}' does not match VerificationSummary work_order_id '{self.work_order_id}'."
                )

        if self.diff_verification is not None:
            if hasattr(self.diff_verification, "execution_id") and self.diff_verification.execution_id:
                if self.diff_verification.execution_id != self.execution_id:
                    raise ProgrammerLineageError(
                        f"DiffVerification execution_id '{self.diff_verification.execution_id}' does not match VerificationSummary execution_id '{self.execution_id}'."
                    )
            if hasattr(self.diff_verification, "work_order_id") and self.diff_verification.work_order_id:
                if self.diff_verification.work_order_id != self.work_order_id:
                    raise ProgrammerLineageError(
                        f"DiffVerification work_order_id '{self.diff_verification.work_order_id}' does not match VerificationSummary work_order_id '{self.work_order_id}'."
                    )

        # 2. Referential integrity
        evidence_map: dict[str, VerificationEvidence] = {ev.evidence_id: ev for ev in self.evidence}

        for chk in self.verification_checks:
            for ev_id in chk.evidence:
                if ev_id not in evidence_map:
                    raise ProgrammerValidationError(
                        f"VerificationCheck '{chk.check_id}' references unknown evidence_id '{ev_id}'."
                    )

        for res in self.acceptance_results:
            for ev_id in res.evidence:
                if ev_id not in evidence_map:
                    raise ProgrammerValidationError(
                        f"AcceptanceResult for '{res.criterion_id}' references unknown evidence_id '{ev_id}'."
                    )

        # 3. Authoritative verification requirement:
        # Agent narration is NOT verification evidence.
        for res in self.acceptance_results:
            if res.status in (VerificationStatus.PASS, "PASS"):
                authoritative = [
                    evidence_map[ev_id]
                    for ev_id in res.evidence
                    if evidence_map[ev_id].is_authoritative()
                ]
                if not authoritative:
                    raise ProgrammerValidationError(
                        f"AcceptanceResult for '{res.criterion_id}' cannot have status PASS based solely on agent narration. At least one authoritative evidence item is required."
                    )

        if self.is_verified:
            if any(chk.status in (VerificationStatus.FAIL, VerificationStatus.ERROR) for chk in self.verification_checks):
                raise ProgrammerValidationError(
                    "VerificationSummary cannot have overall_status PASS when one or more checks have status FAIL or ERROR."
                )
            if any(res.status in (VerificationStatus.FAIL, VerificationStatus.ERROR) for res in self.acceptance_results):
                raise ProgrammerValidationError(
                    "VerificationSummary cannot have overall_status PASS when one or more acceptance results have status FAIL or ERROR."
                )
            if self.diff_verification is not None:
                if hasattr(self.diff_verification, "has_unauthorized_changes") and self.diff_verification.has_unauthorized_changes:
                    raise ProgrammerValidationError(
                        "VerificationSummary cannot have overall_status PASS when diff verification contains unauthorized changes."
                    )
                if hasattr(self.diff_verification, "scope_status") and self.diff_verification.scope_status in (VerificationStatus.FAIL, VerificationStatus.ERROR):
                    raise ProgrammerValidationError(
                        "VerificationSummary cannot have overall_status PASS when diff verification scope_status is FAIL or ERROR."
                    )
            authoritative_evidence = [ev for ev in self.evidence if ev.is_authoritative()]
            if not authoritative_evidence:
                raise ProgrammerValidationError(
                    "VerificationSummary cannot have overall_status PASS without at least one authoritative verification evidence record (agent narration is not evidence)."
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status.value if hasattr(self.overall_status, "value") else str(self.overall_status),
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "checks": [c.to_dict() for c in self.checks],
            "verification_checks": [c.to_dict() for c in self.verification_checks],
            "acceptance_results": [r.to_dict() for r in self.acceptance_results],
            "diff_verification": self.diff_verification.to_dict() if hasattr(self.diff_verification, "to_dict") else self.diff_verification,
            "evidence": [e.to_dict() for e in self.evidence],
            "risks": list(self.risks),
            "limitations": list(self.limitations),
            "verified_at": self.verified_at,
            "timestamps": dict(self.timestamps),
            "summary_text": self.summary_text,
            "trace": self.trace.to_dict() if hasattr(self.trace, "to_dict") else (dict(self.trace) if isinstance(self.trace, dict) else self.trace),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationSummary:
        st_raw = data.get("overall_status", VerificationSummaryStatus.UNVERIFIED.value)
        overall_status = None
        if st_raw in ("PASS", "FAIL", "NOT_VERIFIED", "ERROR", "NOT_RUN"):
            try:
                overall_status = VerificationStatus(st_raw)
            except (ValueError, TypeError):
                pass

        if overall_status is None:
            try:
                overall_status = VerificationSummaryStatus(st_raw)
            except (ValueError, TypeError):
                try:
                    overall_status = VerificationStatus(st_raw)
                except (ValueError, TypeError):
                    overall_status = VerificationSummaryStatus.UNVERIFIED

        raw_checks = data.get("verification_checks") or data.get("checks", [])
        checks = [VerificationCheck.from_dict(c) for c in raw_checks]

        diff_v = None
        if data.get("diff_verification"):
            try:
                from core.programmer.contracts.diff_verifier import DiffVerification
                diff_v = DiffVerification.from_dict(data["diff_verification"])
            except Exception:
                diff_v = data["diff_verification"]

        return cls(
            overall_status=overall_status,
            execution_id=str(data.get("execution_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            checks=checks,
            verification_checks=checks,
            acceptance_results=[AcceptanceResult.from_dict(r) for r in data.get("acceptance_results", [])],
            diff_verification=diff_v,
            evidence=[VerificationEvidence.from_dict(e) for e in data.get("evidence", [])],
            risks=list(data.get("risks", [])),
            limitations=list(data.get("limitations", [])),
            verified_at=str(data.get("verified_at", utc_now())),
            timestamps=dict(data.get("timestamps", {})),
            summary_text=str(data.get("summary_text", "")),
            trace=data.get("trace"),
            metadata=dict(data.get("metadata", {})),
        )


from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Optional

from core.tester.contracts.identifiers import (
    new_defect_id,
    new_evidence_id,
    new_finding_id,
    validate_defect_id,
    validate_evidence_id,
    validate_finding_id,
    validate_work_order_id,
)
from core.tester.errors import TesterBoundaryViolationError, TesterValidationError
from core.tester.types import (
    AcceptanceCriterionStatus,
    DefectSeverity,
    DefectType,
    EvidenceType,
    FindingCategory,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def compute_sha256(content: str | bytes) -> str:
    """Compute SHA-256 hex digest for cryptographic integrity."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


# Phrases indicating purely subjective opinions without concrete observations
SUBJECTIVE_OPINION_PATTERNS = (
    "feels slightly",
    "feels boring",
    "feels like",
    "more premium",
    "users won't like",
    "users might not like",
    "maybe users",
    "looks ugly",
    "a bit ugly",
    "not pretty",
    "personal preference",
)


def is_vague_opinion(text: str) -> bool:
    norm = str(text).strip().lower()
    return any(p in norm for p in SUBJECTIVE_OPINION_PATTERNS)


@dataclass
class TesterEvidence:
    """
    Evidence record captured or produced during testing.
    Maintains cryptographic integrity via SHA-256 checksum where applicable.
    
    Invariants:
    - Zero truth claim: A checksum proves payload/artifact integrity, not truthfulness.
    - Stable evidence references rather than duplicating large payloads.
    - Do not claim an artifact reference exists if none is specified.
    """
    __test__ = False
    evidence_id: str
    evidence_type: EvidenceType | str = EvidenceType.OBSERVATION
    data: str = ""
    description: str = ""
    artifact_reference: Optional[str] = None
    execution_id: Optional[str] = None
    work_order_id: Optional[str] = None
    source: str = ""
    checksum: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    captured_at: str = field(default_factory=utc_now)
    created_at: str = field(default_factory=utc_now)
    trace: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_evidence_id(self.evidence_id)
        if isinstance(self.evidence_type, str):
            try:
                self.evidence_type = EvidenceType(self.evidence_type.upper())
            except (ValueError, KeyError):
                pass

        if not self.data and not self.artifact_reference and not self.description:
            raise TesterValidationError("Evidence must have data, an artifact_reference, or a description.", field_name="data")

        if not self.created_at and self.captured_at:
            self.created_at = self.captured_at
        elif not self.captured_at and self.created_at:
            self.captured_at = self.created_at

        if not self.checksum:
            content_to_hash = self.data or self.artifact_reference or self.description
            self.checksum = compute_sha256(f"{self.evidence_type}|{content_to_hash}")

        self.trace = dict(self.trace)

    def validate(self) -> None:
        validate_evidence_id(self.evidence_id)
        if not self.data and not self.artifact_reference and not self.description:
            raise TesterValidationError("Evidence must have data, an artifact_reference, or a description.", field_name="data")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_type": (
                self.evidence_type.value
                if hasattr(self.evidence_type, "value")
                else str(self.evidence_type)
            ),
            "data": self.data,
            "description": self.description,
            "artifact_reference": self.artifact_reference,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "source": self.source,
            "checksum": self.checksum,
            "metadata": dict(self.metadata),
            "captured_at": self.captured_at,
            "created_at": self.created_at,
            "trace": dict(self.trace),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TesterEvidence:
        et_raw = data.get("evidence_type", EvidenceType.OBSERVATION.value)
        try:
            ev_type = EvidenceType(str(et_raw).upper())
        except (ValueError, KeyError):
            ev_type = str(et_raw)

        return cls(
            evidence_id=str(data.get("evidence_id", new_evidence_id())),
            evidence_type=ev_type,
            data=str(data.get("data", "")),
            description=str(data.get("description", "")),
            artifact_reference=data.get("artifact_reference"),
            execution_id=data.get("execution_id"),
            work_order_id=data.get("work_order_id"),
            source=str(data.get("source", "")),
            checksum=str(data.get("checksum", "")),
            metadata=dict(data.get("metadata", {})),
            captured_at=str(data.get("captured_at") or data.get("created_at") or utc_now()),
            created_at=str(data.get("created_at") or data.get("captured_at") or utc_now()),
            trace=dict(data.get("trace", {})),
        )


@dataclass
class AcceptanceCriterionResult:
    """
    Evaluation result for an explicit acceptance criterion authorized by Manager.
    Evidence-backed: must link to supporting evidence IDs when evaluated.
    
    Invariants:
    - PASS status is strictly rejected if evidence_ids is empty.
    - Missing evidence is never automatically interpreted as PASS.
    """
    __test__ = False
    criterion_id: str
    description: str
    status: AcceptanceCriterionStatus = AcceptanceCriterionStatus.NOT_EVALUATED
    observed_behavior: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    explanation: str = ""
    notes: str = ""
    trace: dict[str, Any] = field(default_factory=dict)
    supporting_test_cases: list[str] = field(default_factory=list)
    observations: list[Any] = field(default_factory=list)
    reason: str = ""
    confidence: float = 1.0
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = AcceptanceCriterionStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = AcceptanceCriterionStatus.NOT_EVALUATED

        if not self.reason and (self.explanation or self.notes):
            self.reason = self.explanation or self.notes
        elif self.reason and not self.explanation:
            self.explanation = self.reason
            self.notes = self.reason

        if not self.explanation and self.notes:
            self.explanation = self.notes
        elif not self.notes and self.explanation:
            self.notes = self.explanation

        self.evidence_ids = list(self.evidence_ids)
        self.trace = dict(self.trace)
        self.supporting_test_cases = list(self.supporting_test_cases)
        self.observations = list(self.observations)
        self.provenance = dict(self.provenance)

    @property
    def is_pass(self) -> bool:
        return self.status == AcceptanceCriterionStatus.PASS

    @property
    def is_fail(self) -> bool:
        return self.status == AcceptanceCriterionStatus.FAIL

    @property
    def is_blocked(self) -> bool:
        return self.status == AcceptanceCriterionStatus.BLOCKED

    @property
    def is_not_verified(self) -> bool:
        return self.status in (
            AcceptanceCriterionStatus.NOT_VERIFIED,
            AcceptanceCriterionStatus.NOT_EVALUATED,
            AcceptanceCriterionStatus.UNCERTAIN,
        )

    @property
    def is_not_applicable(self) -> bool:
        return self.status == AcceptanceCriterionStatus.NOT_APPLICABLE

    @property
    def is_uncertain(self) -> bool:
        return self.is_not_verified

    def apply_fix(self, *args, **kwargs) -> Any:
        """Zero fixing guard: Acceptance evaluation does not perform automatic fixes."""
        raise TesterBoundaryViolationError(
            action="AUTOMATIC_FIX",
            reason=(
                f"AcceptanceCriterionResult '{self.criterion_id}' is an evaluation object. "
                "Tester does not perform automatic fixes on product or source code."
            ),
        )

    def auto_fix(self, *args, **kwargs) -> Any:
        return self.apply_fix(*args, **kwargs)

    def validate(self) -> None:
        if not self.criterion_id or not str(self.criterion_id).strip():
            raise TesterValidationError("AcceptanceCriterionResult must have a criterion_id.", field_name="criterion_id")
        if self.status == AcceptanceCriterionStatus.PASS and not self.evidence_ids:
            raise TesterValidationError(
                f"AcceptanceCriterion '{self.criterion_id}' marked PASS without supporting evidence. "
                "PASS status strictly requires non-empty evidence_ids.",
                field_name="evidence_ids",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "description": self.description,
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "observed_behavior": self.observed_behavior,
            "evidence_ids": list(self.evidence_ids),
            "explanation": self.explanation,
            "notes": self.notes,
            "trace": dict(self.trace),
            "supporting_test_cases": list(self.supporting_test_cases),
            "observations": [
                obs.to_dict() if hasattr(obs, "to_dict") else obs
                for obs in self.observations
            ],
            "reason": self.reason,
            "confidence": self.confidence,
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AcceptanceCriterionResult:
        st_raw = data.get("status", AcceptanceCriterionStatus.NOT_EVALUATED.value)
        try:
            status = AcceptanceCriterionStatus(str(st_raw).upper())
        except (ValueError, KeyError):
            status = AcceptanceCriterionStatus.NOT_EVALUATED

        reason_val = str(data.get("reason") or data.get("explanation") or data.get("notes") or "")
        return cls(
            criterion_id=str(data.get("criterion_id", "")),
            description=str(data.get("description", "")),
            status=status,
            observed_behavior=str(data.get("observed_behavior", "")),
            evidence_ids=list(data.get("evidence_ids", [])),
            explanation=reason_val,
            notes=reason_val,
            trace=dict(data.get("trace", {})),
            supporting_test_cases=list(data.get("supporting_test_cases", [])),
            observations=list(data.get("observations", [])),
            reason=reason_val,
            confidence=float(data.get("confidence", 1.0)),
            provenance=dict(data.get("provenance", {})),
        )


@dataclass
class TesterDefect:
    """
    Authoritative defect record identified through product evaluation.
    Grounds findings in concrete reproduction steps, actual vs expected behavior,
    and verified evidence.
    
    Invariants:
    - Must be based on an actual observation or valid test failure.
    - Purely vague subjective opinions ("feels boring", "make more premium") are rejected.
    """
    __test__ = False
    defect_id: str
    work_order_id: str
    title: str
    description: str
    severity: DefectSeverity = DefectSeverity.MEDIUM
    defect_type: DefectType = DefectType.FUNCTIONAL
    execution_id: Optional[str] = None
    reproduction_steps: list[str] = field(default_factory=list)
    expected_behavior: str = ""
    observed_behavior: str = ""
    actual_behavior: str = ""
    affected_area: str = ""
    affected_components: list[str] = field(default_factory=list)
    test_id: Optional[str] = None
    evidence_ids: list[str] = field(default_factory=list)
    confidence: float = 1.0
    is_regression: bool = False
    trace: dict[str, Any] = field(default_factory=dict)
    acceptance_criterion_id: Optional[str] = None
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_defect_id(self.defect_id)
        if self.work_order_id:
            validate_work_order_id(self.work_order_id)
        if not self.title or not self.title.strip():
            raise TesterValidationError("Defect title cannot be empty.", field_name="title")

        if isinstance(self.severity, str):
            try:
                self.severity = DefectSeverity(self.severity.upper())
            except (ValueError, KeyError):
                self.severity = DefectSeverity.MEDIUM

        if isinstance(self.defect_type, str):
            try:
                self.defect_type = DefectType(self.defect_type.upper())
            except (ValueError, KeyError):
                self.defect_type = DefectType.FUNCTIONAL

        # Synchronize observed_behavior and actual_behavior aliases
        if not self.observed_behavior and self.actual_behavior:
            self.observed_behavior = self.actual_behavior
        elif not self.actual_behavior and self.observed_behavior:
            self.actual_behavior = self.observed_behavior

        # Synchronize affected_area and affected_components
        if not self.affected_area and self.affected_components:
            self.affected_area = ", ".join(self.affected_components)
        elif self.affected_area and not self.affected_components:
            self.affected_components = [c.strip() for c in self.affected_area.split(",") if c.strip()]

        self.reproduction_steps = list(self.reproduction_steps)
        self.evidence_ids = list(self.evidence_ids)
        self.trace = dict(self.trace)
        self.provenance = dict(self.provenance)

    @property
    def test_case_id(self) -> Optional[str]:
        return self.test_id

    @test_case_id.setter
    def test_case_id(self, val: Optional[str]) -> None:
        self.test_id = val

    @property
    def type(self) -> DefectType:
        return self.defect_type

    @type.setter
    def type(self, val: DefectType | str) -> None:
        if isinstance(val, str):
            try:
                self.defect_type = DefectType(val.upper())
            except (ValueError, KeyError):
                self.defect_type = DefectType.FUNCTIONAL
        else:
            self.defect_type = val

    @property
    def affected_surface(self) -> str:
        return self.affected_area

    @affected_surface.setter
    def affected_surface(self, val: str) -> None:
        self.affected_area = val

    def apply_fix(self, *args, **kwargs) -> Any:
        raise TesterBoundaryViolationError(
            action="AUTOMATIC_FIX",
            reason="TesterDefect cannot automatically apply fixes. Defect repair is strictly prohibited in Tester V1.",
        )

    def auto_fix(self, *args, **kwargs) -> Any:
        return self.apply_fix(*args, **kwargs)

    def validate(self) -> None:
        """Validate defect structure and enforce the anti-opinion boundary."""
        validate_defect_id(self.defect_id)
        if self.work_order_id:
            validate_work_order_id(self.work_order_id)
        if not self.title or not self.title.strip():
            raise TesterValidationError("Defect title cannot be empty.", field_name="title")

        # Reject vague subjective opinions
        combined = f"{self.title} {self.description} {self.observed_behavior or self.actual_behavior}"
        if is_vague_opinion(combined) and not self.reproduction_steps and not self.test_id:
            raise TesterValidationError(
                f"Vague subjective opinion is not a valid defect: '{self.description or self.title}'. "
                "Defects must be based on concrete observations, reproduction steps, or valid test failures.",
                field_name="description",
            )

        obs = self.observed_behavior or self.actual_behavior or self.description
        if not obs or not obs.strip():
            raise TesterValidationError(
                "Defect must contain a concrete description or observed_behavior.",
                field_name="observed_behavior",
            )

        if not (0.0 <= float(self.confidence) <= 1.0):
            raise TesterValidationError(
                f"Confidence must be between 0.0 and 1.0, got {self.confidence}.",
                field_name="confidence",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "defect_id": self.defect_id,
            "work_order_id": self.work_order_id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value if hasattr(self.severity, "value") else str(self.severity),
            "defect_type": self.defect_type.value if hasattr(self.defect_type, "value") else str(self.defect_type),
            "type": self.defect_type.value if hasattr(self.defect_type, "value") else str(self.defect_type),
            "execution_id": self.execution_id,
            "reproduction_steps": list(self.reproduction_steps),
            "expected_behavior": self.expected_behavior,
            "observed_behavior": self.observed_behavior,
            "actual_behavior": self.actual_behavior,
            "affected_area": self.affected_area,
            "affected_surface": self.affected_area,
            "affected_components": list(self.affected_components),
            "test_id": self.test_id,
            "test_case_id": self.test_id,
            "evidence_ids": list(self.evidence_ids),
            "acceptance_criterion_id": self.acceptance_criterion_id,
            "confidence": self.confidence,
            "is_regression": self.is_regression,
            "trace": dict(self.trace),
            "provenance": dict(self.provenance),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TesterDefect:
        sev_raw = data.get("severity", DefectSeverity.MEDIUM.value)
        try:
            severity = DefectSeverity(str(sev_raw).upper())
        except (ValueError, KeyError):
            severity = DefectSeverity.MEDIUM

        dt_raw = data.get("defect_type") or data.get("type", DefectType.FUNCTIONAL.value)
        try:
            defect_type = DefectType(str(dt_raw).upper())
        except (ValueError, KeyError):
            defect_type = DefectType.FUNCTIONAL

        obs = str(data.get("observed_behavior") or data.get("actual_behavior", ""))
        tid = data.get("test_id") or data.get("test_case_id")
        aff_area = str(data.get("affected_area") or data.get("affected_surface", ""))
        return cls(
            defect_id=str(data.get("defect_id", new_defect_id())),
            work_order_id=str(data.get("work_order_id", "")),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            severity=severity,
            defect_type=defect_type,
            execution_id=data.get("execution_id"),
            reproduction_steps=list(data.get("reproduction_steps", [])),
            expected_behavior=str(data.get("expected_behavior", "")),
            observed_behavior=obs,
            actual_behavior=obs,
            affected_area=aff_area,
            affected_components=list(data.get("affected_components", [])),
            test_id=tid,
            evidence_ids=list(data.get("evidence_ids", [])),
            acceptance_criterion_id=data.get("acceptance_criterion_id"),
            confidence=float(data.get("confidence", 1.0)),
            is_regression=bool(data.get("is_regression", False)),
            trace=dict(data.get("trace", {})),
            provenance=dict(data.get("provenance", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=str(data.get("created_at", utc_now())),
        )


@dataclass
class TesterFinding:
    """
    Evaluative observation, UX/performance finding, recommendation, or reported uncertainty.
    Distinguishes objective evidence-backed findings from subjective impressions.
    A finding MAY be informational and does not force every observation to become a defect.
    """
    __test__ = False
    finding_id: str
    category: FindingCategory
    title: str
    description: str
    severity: DefectSeverity = DefectSeverity.LOW
    observed_behavior: str = ""
    affected_area: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    confidence: float = 1.0
    actionable: bool = True
    is_uncertain: bool = False
    recommendation: Optional[str] = None
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_finding_id(self.finding_id)
        if not self.title or not self.title.strip():
            raise TesterValidationError("Finding title cannot be empty.", field_name="title")

        if isinstance(self.category, str):
            try:
                self.category = FindingCategory(self.category.upper())
            except (ValueError, KeyError):
                self.category = FindingCategory.OBSERVATION

        if isinstance(self.severity, str):
            try:
                self.severity = DefectSeverity(self.severity.upper())
            except (ValueError, KeyError):
                self.severity = DefectSeverity.LOW

        self.evidence_ids = list(self.evidence_ids)
        self.trace = dict(self.trace)

    def validate(self) -> None:
        validate_finding_id(self.finding_id)
        if not self.title or not self.title.strip():
            raise TesterValidationError("Finding title cannot be empty.", field_name="title")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise TesterValidationError(
                f"Confidence must be between 0.0 and 1.0, got {self.confidence}.",
                field_name="confidence",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "category": self.category.value if hasattr(self.category, "value") else str(self.category),
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value if hasattr(self.severity, "value") else str(self.severity),
            "observed_behavior": self.observed_behavior,
            "affected_area": self.affected_area,
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
            "actionable": self.actionable,
            "is_uncertain": self.is_uncertain,
            "recommendation": self.recommendation,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TesterFinding:
        cat_raw = data.get("category", FindingCategory.OBSERVATION.value)
        try:
            category = FindingCategory(str(cat_raw).upper())
        except (ValueError, KeyError):
            category = FindingCategory.OBSERVATION

        sev_raw = data.get("severity", DefectSeverity.LOW.value)
        try:
            severity = DefectSeverity(str(sev_raw).upper())
        except (ValueError, KeyError):
            severity = DefectSeverity.LOW

        return cls(
            finding_id=str(data.get("finding_id", new_finding_id())),
            category=category,
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            severity=severity,
            observed_behavior=str(data.get("observed_behavior", "")),
            affected_area=str(data.get("affected_area", "")),
            evidence_ids=list(data.get("evidence_ids", [])),
            confidence=float(data.get("confidence", 1.0)),
            actionable=bool(data.get("actionable", True)),
            is_uncertain=bool(data.get("is_uncertain", False)),
            recommendation=data.get("recommendation"),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=str(data.get("created_at", utc_now())),
        )

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Optional, Sequence

from core.enums import RiskLevel
from core.programmer.contracts.escalation import ProgrammerEscalationCategory
from core.programmer.contracts.identifiers import (
    new_engineering_risk_id,
    new_escalation_candidate_id,
    new_risk_assessment_id,
    validate_engineering_risk_id,
    validate_execution_id,
    validate_plan_id,
    validate_risk_assessment_id,
    validate_work_order_id,
)
from core.programmer.contracts.implementation_plan import EscalationCandidate
from core.programmer.contracts.verification import VerificationEvidence
from core.programmer.errors import ProgrammerValidationError
from core.programmer.types import (
    EngineeringRiskCategory,
    ProgrammerBlockerSeverity,
    UnderstandingConfidence,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EngineeringRisk:
    """
    Structured domain model representing an empirical or inferred engineering risk
    detected in an implementation plan prior to code execution.

    Invariants:
    1. Grounded in repository evidence or concrete impact graph analysis (no speculative hysteria).
    2. Epistemically calibrated: OBSERVED (directly observed in manifests/DDL),
       INFERRED (deduced from call graph/public routes), or UNKNOWN (unresolvable).
    3. Escalation required only for material risks that require Manager authority.
    """
    risk_id: str = field(default_factory=new_engineering_risk_id)
    category: EngineeringRiskCategory = EngineeringRiskCategory.UNKNOWN
    severity: RiskLevel = RiskLevel.MEDIUM
    confidence: UnderstandingConfidence = UnderstandingConfidence.INFERRED
    description: str = ""
    affected_area: str = ""
    evidence: list[VerificationEvidence] = field(default_factory=list)
    mitigation: str = ""
    escalation_required: bool = False
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.category, str):
            try:
                self.category = EngineeringRiskCategory(self.category.upper())
            except (ValueError, TypeError):
                self.category = EngineeringRiskCategory.UNKNOWN

        if isinstance(self.severity, str):
            try:
                self.severity = RiskLevel(self.severity.upper())
            except (ValueError, TypeError):
                self.severity = RiskLevel.MEDIUM

        if isinstance(self.confidence, str):
            try:
                self.confidence = UnderstandingConfidence(self.confidence.upper())
            except (ValueError, TypeError):
                self.confidence = UnderstandingConfidence.INFERRED

        normalized_ev: list[VerificationEvidence] = []
        for e in self.evidence:
            if isinstance(e, dict):
                normalized_ev.append(VerificationEvidence.from_dict(e))
            else:
                normalized_ev.append(e)
        self.evidence = normalized_ev

        self.validate()

    def validate(self) -> None:
        validate_engineering_risk_id(self.risk_id)
        if not self.description or not self.description.strip():
            raise ProgrammerValidationError(
                f"EngineeringRisk '{self.risk_id}' description cannot be empty.",
                field_name="description",
            )
        if not self.affected_area or not self.affected_area.strip():
            raise ProgrammerValidationError(
                f"EngineeringRisk '{self.risk_id}' affected_area cannot be empty.",
                field_name="affected_area",
            )

    def to_escalation_candidate(self) -> EscalationCandidate:
        """
        Convert this risk into an EscalationCandidate when material Manager authority is required.
        """
        # Map EngineeringRiskCategory to ProgrammerEscalationCategory
        cat_mapping = {
            EngineeringRiskCategory.SECURITY: ProgrammerEscalationCategory.PERMISSION,
            EngineeringRiskCategory.AUTHENTICATION: ProgrammerEscalationCategory.PERMISSION,
            EngineeringRiskCategory.AUTHORIZATION: ProgrammerEscalationCategory.PERMISSION,
            EngineeringRiskCategory.DATA_LOSS: ProgrammerEscalationCategory.ARCHITECTURAL,
            EngineeringRiskCategory.DATABASE_MIGRATION: ProgrammerEscalationCategory.ARCHITECTURAL,
            EngineeringRiskCategory.PUBLIC_API_CHANGE: ProgrammerEscalationCategory.ARCHITECTURAL,
            EngineeringRiskCategory.BREAKING_CHANGE: ProgrammerEscalationCategory.ARCHITECTURAL,
            EngineeringRiskCategory.ARCHITECTURAL_CHANGE: ProgrammerEscalationCategory.ARCHITECTURAL,
            EngineeringRiskCategory.DESTRUCTIVE_OPERATION: ProgrammerEscalationCategory.SCOPE,
            EngineeringRiskCategory.DEPENDENCY_CHANGE: ProgrammerEscalationCategory.DEPENDENCY,
            EngineeringRiskCategory.LARGE_SCOPE: ProgrammerEscalationCategory.SCOPE,
            EngineeringRiskCategory.INSUFFICIENT_TEST_COVERAGE: ProgrammerEscalationCategory.VERIFICATION,
            EngineeringRiskCategory.CONFIGURATION_CHANGE: ProgrammerEscalationCategory.ARCHITECTURAL,
            EngineeringRiskCategory.UNKNOWN: ProgrammerEscalationCategory.MISSING_CONTEXT,
        }
        esc_cat = cat_mapping.get(self.category, ProgrammerEscalationCategory.OTHER)

        # Map RiskLevel to ProgrammerBlockerSeverity
        sev_mapping = {
            RiskLevel.LOW: ProgrammerBlockerSeverity.LOW,
            RiskLevel.MEDIUM: ProgrammerBlockerSeverity.MEDIUM,
            RiskLevel.HIGH: ProgrammerBlockerSeverity.HIGH,
            RiskLevel.CRITICAL: ProgrammerBlockerSeverity.CRITICAL,
        }
        esc_sev = sev_mapping.get(self.severity, ProgrammerBlockerSeverity.HIGH)

        options: list[str] = []
        if self.mitigation:
            options.append(self.mitigation)
        options.append(f"Authorize {self.category.value} changes in {self.affected_area}")
        options.append("Adjust implementation plan to eliminate or isolate risk")

        return EscalationCandidate(
            candidate_id=new_escalation_candidate_id(),
            category=esc_cat,
            reason=f"Material engineering risk [{self.category.value}]: {self.description}",
            target=self.affected_area,
            requested_decision=f"Manager authority required for material {self.category.value} risk in {self.affected_area}.",
            severity=esc_sev,
            suggested_options=options,
            trace=dict(self.trace or {
                "risk_id": self.risk_id,
                "created_at": utc_now(),
            }),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_id": self.risk_id,
            "category": self.category.value,
            "severity": self.severity.value,
            "confidence": self.confidence.value,
            "description": self.description,
            "affected_area": self.affected_area,
            "evidence": [e.to_dict() if hasattr(e, "to_dict") else e for e in self.evidence],
            "mitigation": self.mitigation,
            "escalation_required": self.escalation_required,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EngineeringRisk:
        cat_raw = data.get("category", EngineeringRiskCategory.UNKNOWN.value)
        try:
            category = EngineeringRiskCategory(str(cat_raw).upper())
        except (ValueError, TypeError):
            category = EngineeringRiskCategory.UNKNOWN

        sev_raw = data.get("severity", RiskLevel.MEDIUM.value)
        try:
            severity = RiskLevel(str(sev_raw).upper())
        except (ValueError, TypeError):
            severity = RiskLevel.MEDIUM

        conf_raw = data.get("confidence", UnderstandingConfidence.INFERRED.value)
        try:
            confidence = UnderstandingConfidence(str(conf_raw).upper())
        except (ValueError, TypeError):
            confidence = UnderstandingConfidence.INFERRED

        return cls(
            risk_id=data.get("risk_id", new_engineering_risk_id()),
            category=category,
            severity=severity,
            confidence=confidence,
            description=str(data.get("description", "")),
            affected_area=str(data.get("affected_area", "")),
            evidence=list(data.get("evidence", [])),
            mitigation=str(data.get("mitigation", "")),
            escalation_required=bool(data.get("escalation_required", False)),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class RiskAssessment:
    """
    Authoritative domain model representing the complete engineering risk evaluation
    of an ImplementationPlan prior to code modification.

    Invariants:
    1. Purely advisory evaluation; never modifies files or commands.
    2. Identifies material risks requiring Manager intervention.
    3. Retains full audit evidence linkage.
    """
    assessment_id: str = field(default_factory=new_risk_assessment_id)
    execution_id: str = ""
    work_order_id: str = ""
    project_id: str = ""
    plan_id: str = ""
    risks: list[EngineeringRisk] = field(default_factory=list)
    material_risks: list[EngineeringRisk] = field(default_factory=list)
    escalation_candidates: list[EscalationCandidate] = field(default_factory=list)
    overall_risk_level: RiskLevel = RiskLevel.LOW
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        normalized_risks: list[EngineeringRisk] = []
        for r in self.risks:
            if isinstance(r, dict):
                normalized_risks.append(EngineeringRisk.from_dict(r))
            else:
                normalized_risks.append(r)
        self.risks = normalized_risks

        if not self.material_risks:
            self.material_risks = [
                r for r in self.risks
                if r.escalation_required or r.severity in (RiskLevel.HIGH, RiskLevel.CRITICAL)
            ]
        else:
            norm_mat: list[EngineeringRisk] = []
            for r in self.material_risks:
                if isinstance(r, dict):
                    norm_mat.append(EngineeringRisk.from_dict(r))
                else:
                    norm_mat.append(r)
            self.material_risks = norm_mat

        if not self.escalation_candidates and self.material_risks:
            self.escalation_candidates = [
                r.to_escalation_candidate() for r in self.material_risks if r.escalation_required
            ]
        else:
            norm_esc: list[EscalationCandidate] = []
            for e in self.escalation_candidates:
                if isinstance(e, dict):
                    norm_esc.append(EscalationCandidate.from_dict(e))
                else:
                    norm_esc.append(e)
            self.escalation_candidates = norm_esc

        if isinstance(self.overall_risk_level, str):
            try:
                self.overall_risk_level = RiskLevel(self.overall_risk_level.upper())
            except (ValueError, TypeError):
                self.overall_risk_level = RiskLevel.LOW

        self.validate()

    def validate(self) -> None:
        validate_risk_assessment_id(self.assessment_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        validate_plan_id(self.plan_id)
        if not self.project_id or not isinstance(self.project_id, str):
            raise ProgrammerValidationError("project_id must be a non-empty string.", field_name="project_id")

    def has_material_risks(self) -> bool:
        """Return True if any risk requires Manager intervention or carries HIGH/CRITICAL severity."""
        return len(self.material_risks) > 0

    def get_risks_by_category(self, category: EngineeringRiskCategory | str) -> list[EngineeringRisk]:
        """Retrieve all risks in the specified category."""
        if isinstance(category, str):
            try:
                category = EngineeringRiskCategory(category.upper())
            except (ValueError, TypeError):
                category = EngineeringRiskCategory.UNKNOWN
        return [r for r in self.risks if r.category == category]

    def get_risks_by_severity(self, severity: RiskLevel | str) -> list[EngineeringRisk]:
        """Retrieve all risks with the specified severity."""
        if isinstance(severity, str):
            try:
                severity = RiskLevel(severity.upper())
            except (ValueError, TypeError):
                severity = RiskLevel.MEDIUM
        return [r for r in self.risks if r.severity == severity]

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "project_id": self.project_id,
            "plan_id": self.plan_id,
            "risks": [r.to_dict() for r in self.risks],
            "material_risks": [r.to_dict() for r in self.material_risks],
            "escalation_candidates": [e.to_dict() for e in self.escalation_candidates],
            "overall_risk_level": self.overall_risk_level.value,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RiskAssessment:
        raw_level = data.get("overall_risk_level", RiskLevel.LOW.value)
        try:
            overall_risk = RiskLevel(str(raw_level).upper())
        except (ValueError, TypeError):
            overall_risk = RiskLevel.LOW

        return cls(
            assessment_id=data["assessment_id"],
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            project_id=data["project_id"],
            plan_id=data["plan_id"],
            risks=[EngineeringRisk.from_dict(r) for r in data.get("risks", [])],
            material_risks=[EngineeringRisk.from_dict(r) for r in data.get("material_risks", [])],
            escalation_candidates=[EscalationCandidate.from_dict(e) for e in data.get("escalation_candidates", [])],
            overall_risk_level=overall_risk,
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )

    def to_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> RiskAssessment:
        return cls.from_dict(json.loads(json_str))

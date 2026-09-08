from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Optional, Sequence

from core.programmer.contracts.identifiers import (
    IMPACT_ANALYSIS_ID_PREFIX,
    new_impact_analysis_id,
    validate_codebase_understanding_id,
    validate_execution_id,
    validate_impact_analysis_id,
    validate_work_order_id,
)
from core.programmer.contracts.verification import VerificationEvidence
from core.programmer.errors import (
    ProgrammerValidationError,
)
from core.programmer.types import (
    ImpactLevel,
    UnderstandingConfidence,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ImpactItem:
    """
    Epistemically-calibrated individual impact finding.

    Guarantees:
    - Categorized into DIRECT, INDIRECT, POTENTIAL, or UNKNOWN.
    - Preserves verifiable rationale and source reference (e.g. import statement, route definition).
    - Preserves audit evidence linkage.
    """
    target: str
    target_type: str  # "file", "module", "interface", "test", "config", "dependency"
    level: ImpactLevel
    rationale: str
    source_reference: Optional[str] = None
    evidence_id: Optional[str] = None
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.level, str):
            try:
                self.level = ImpactLevel(self.level.upper())
            except (ValueError, TypeError):
                self.level = ImpactLevel.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "target_type": self.target_type,
            "level": self.level.value,
            "rationale": self.rationale,
            "source_reference": self.source_reference,
            "evidence_id": self.evidence_id,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImpactItem:
        lvl_raw = data.get("level", ImpactLevel.UNKNOWN.value)
        try:
            level = ImpactLevel(str(lvl_raw).upper())
        except (ValueError, TypeError):
            level = ImpactLevel.UNKNOWN

        return cls(
            target=str(data.get("target", "")),
            target_type=str(data.get("target_type", "file")),
            level=level,
            rationale=str(data.get("rationale", "")),
            source_reference=data.get("source_reference"),
            evidence_id=data.get("evidence_id"),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ImpactAnalysis:
    """
    Authoritative domain model representing the predicted impact of an implementation
    across an authorized codebase before code modifications begin.

    Core Invariants:
    1. Bounded strictly by Programmer ExecutionContext and authorized filesystem boundaries.
    2. Empirical evidence over guesswork: relies on observable imports, routes, manifests, tests.
    3. Epistemic calibration: DIRECT, INDIRECT, POTENTIAL, and UNKNOWN are strictly distinct.
    4. Never automatically expands WorkOrder writable scope. Restricted files remain UNKNOWN.
    5. Immutable audit evidence linkage.
    """
    analysis_id: str
    execution_id: str
    work_order_id: str
    project_id: str
    understanding_id: str
    directly_affected_files: list[str] = field(default_factory=list)
    indirectly_affected_files: list[str] = field(default_factory=list)
    affected_modules: list[str] = field(default_factory=list)
    affected_interfaces: list[str] = field(default_factory=list)
    affected_tests: list[str] = field(default_factory=list)
    affected_configuration: list[str] = field(default_factory=list)
    dependency_impacts: list[str] = field(default_factory=list)
    potential_side_effects: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    impact_items: list[ImpactItem] = field(default_factory=list)
    evidence: list[VerificationEvidence] = field(default_factory=list)
    confidence: UnderstandingConfidence = UnderstandingConfidence.INFERRED
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.confidence, str):
            try:
                self.confidence = UnderstandingConfidence(self.confidence.upper())
            except (ValueError, TypeError):
                self.confidence = UnderstandingConfidence.UNKNOWN

        normalized_items: list[ImpactItem] = []
        for item in self.impact_items:
            if isinstance(item, dict):
                normalized_items.append(ImpactItem.from_dict(item))
            else:
                normalized_items.append(item)
        self.impact_items = normalized_items

        normalized_ev: list[VerificationEvidence] = []
        for e in self.evidence:
            if isinstance(e, dict):
                normalized_ev.append(VerificationEvidence.from_dict(e))
            else:
                normalized_ev.append(e)
        self.evidence = normalized_ev

        self.validate()

    def validate(self) -> None:
        validate_impact_analysis_id(self.analysis_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        validate_codebase_understanding_id(self.understanding_id)
        if not self.project_id or not isinstance(self.project_id, str):
            raise ProgrammerValidationError("project_id must be a non-empty string.", field_name="project_id")

    # -------------------------------------------------------------------------
    # Epistemic Query Helpers
    # -------------------------------------------------------------------------

    def get_direct_impacts(self) -> list[ImpactItem]:
        """Return impact items classified as directly affected."""
        return [i for i in self.impact_items if i.level == ImpactLevel.DIRECT]

    def get_indirect_impacts(self) -> list[ImpactItem]:
        """Return impact items classified as indirectly affected through dependencies/callers."""
        return [i for i in self.impact_items if i.level == ImpactLevel.INDIRECT]

    def get_potential_impacts(self) -> list[ImpactItem]:
        """Return impact items plausibly affected but requiring verification."""
        return [i for i in self.impact_items if i.level == ImpactLevel.POTENTIAL]

    def get_unknown_impacts(self) -> list[ImpactItem]:
        """Return impact items that could not be determined due to scope restrictions or missing references."""
        return [i for i in self.impact_items if i.level == ImpactLevel.UNKNOWN]

    def has_unknowns(self) -> bool:
        """True if any unknown impacts or scope-restricted targets exist."""
        return bool(self.unknowns or self.get_unknown_impacts())

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "project_id": self.project_id,
            "understanding_id": self.understanding_id,
            "directly_affected_files": list(self.directly_affected_files),
            "indirectly_affected_files": list(self.indirectly_affected_files),
            "affected_modules": list(self.affected_modules),
            "affected_interfaces": list(self.affected_interfaces),
            "affected_tests": list(self.affected_tests),
            "affected_configuration": list(self.affected_configuration),
            "dependency_impacts": list(self.dependency_impacts),
            "potential_side_effects": list(self.potential_side_effects),
            "unknowns": list(self.unknowns),
            "impact_items": [i.to_dict() for i in self.impact_items],
            "evidence": [e.to_dict() for e in self.evidence],
            "confidence": self.confidence.value,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImpactAnalysis:
        conf_raw = data.get("confidence", UnderstandingConfidence.INFERRED.value)
        try:
            confidence = UnderstandingConfidence(str(conf_raw).upper())
        except (ValueError, TypeError):
            confidence = UnderstandingConfidence.UNKNOWN

        items_list = [ImpactItem.from_dict(i) for i in data.get("impact_items", [])]
        ev_list = [VerificationEvidence.from_dict(e) for e in data.get("evidence", [])]

        return cls(
            analysis_id=data["analysis_id"],
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            project_id=data["project_id"],
            understanding_id=data["understanding_id"],
            directly_affected_files=list(data.get("directly_affected_files", [])),
            indirectly_affected_files=list(data.get("indirectly_affected_files", [])),
            affected_modules=list(data.get("affected_modules", [])),
            affected_interfaces=list(data.get("affected_interfaces", [])),
            affected_tests=list(data.get("affected_tests", [])),
            affected_configuration=list(data.get("affected_configuration", [])),
            dependency_impacts=list(data.get("dependency_impacts", [])),
            potential_side_effects=list(data.get("potential_side_effects", [])),
            unknowns=list(data.get("unknowns", [])),
            impact_items=items_list,
            evidence=ev_list,
            confidence=confidence,
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )

    @classmethod
    def from_json(cls, json_str: str) -> ImpactAnalysis:
        return cls.from_dict(json.loads(json_str))

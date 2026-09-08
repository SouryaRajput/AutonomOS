from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Optional, Sequence

from core.programmer.contracts.identifiers import (
    UNDERSTANDING_ID_PREFIX,
    new_codebase_understanding_id,
    validate_codebase_understanding_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.programmer.contracts.verification import VerificationEvidence
from core.programmer.errors import (
    ProgrammerValidationError,
)
from core.programmer.types import (
    UnderstandingConfidence,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class UnderstandingInsight:
    """
    Epistemically-calibrated individual finding extracted from codebase exploration.

    Guarantees:
    - Strictly distinguishes OBSERVED (directly verified on disk), INFERRED (deduced),
      and UNKNOWN (insufficient evidence).
    - Never presents inference as fact.
    - Preserves source path and evidence linkage where available.
    """
    category: str
    key: str
    confidence: UnderstandingConfidence
    value: Any = None
    source_path: Optional[str] = None
    rationale: str = ""
    evidence_id: Optional[str] = None
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.confidence, str):
            try:
                self.confidence = UnderstandingConfidence(self.confidence.upper())
            except (ValueError, TypeError):
                self.confidence = UnderstandingConfidence.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "key": self.key,
            "confidence": self.confidence.value,
            "value": self.value,
            "source_path": self.source_path,
            "rationale": self.rationale,
            "evidence_id": self.evidence_id,
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnderstandingInsight:
        conf_raw = data.get("confidence", UnderstandingConfidence.UNKNOWN.value)
        try:
            confidence = UnderstandingConfidence(str(conf_raw).upper())
        except (ValueError, TypeError):
            confidence = UnderstandingConfidence.UNKNOWN

        return cls(
            category=str(data.get("category", "")),
            key=str(data.get("key", "")),
            confidence=confidence,
            value=data.get("value"),
            source_path=data.get("source_path"),
            rationale=str(data.get("rationale", "")),
            evidence_id=data.get("evidence_id"),
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class CodebaseUnderstanding:
    """
    Authoritative domain model representing Programmer's structured understanding
    of an authorized repository prior to implementation.

    Boundaries & Invariants:
    1. Bounded strictly by Programmer ExecutionContext and authorized filesystem boundaries.
    2. Focused exploration driven by WorkOrder objective, avoiding exhaustive file parsing.
    3. Epistemic calibration: OBSERVED (direct facts), INFERRED (interpretations),
       UNKNOWN (unverified elements). Inferences are never asserted as facts.
    4. Immutable audit evidence linkage.
    """
    understanding_id: str
    execution_id: str
    work_order_id: str
    project_id: str
    repository_id: Optional[str] = None
    project_type: str = "unknown"
    project_type_confidence: UnderstandingConfidence = UnderstandingConfidence.UNKNOWN
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    repository_structure: dict[str, Any] = field(default_factory=dict)
    important_directories: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    configuration_files: list[str] = field(default_factory=list)
    test_locations: list[str] = field(default_factory=list)
    dependency_manifests: list[str] = field(default_factory=list)
    relevant_modules: list[str] = field(default_factory=list)
    detected_conventions: dict[str, Any] = field(default_factory=dict)
    uncertainties: list[str] = field(default_factory=list)
    insights: list[UnderstandingInsight] = field(default_factory=list)
    evidence: list[VerificationEvidence] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.project_type_confidence, str):
            try:
                self.project_type_confidence = UnderstandingConfidence(self.project_type_confidence.upper())
            except (ValueError, TypeError):
                self.project_type_confidence = UnderstandingConfidence.UNKNOWN

        normalized_insights: list[UnderstandingInsight] = []
        for i in self.insights:
            if isinstance(i, dict):
                normalized_insights.append(UnderstandingInsight.from_dict(i))
            else:
                normalized_insights.append(i)
        self.insights = normalized_insights

        normalized_ev: list[VerificationEvidence] = []
        for e in self.evidence:
            if isinstance(e, dict):
                normalized_ev.append(VerificationEvidence.from_dict(e))
            else:
                normalized_ev.append(e)
        self.evidence = normalized_ev

        self.validate()

    def validate(self) -> None:
        validate_codebase_understanding_id(self.understanding_id)
        validate_execution_id(self.execution_id)
        validate_work_order_id(self.work_order_id)
        if not self.project_id or not isinstance(self.project_id, str):
            raise ProgrammerValidationError("project_id must be a non-empty string.", field_name="project_id")

    # -------------------------------------------------------------------------
    # Query & Epistemic Inspection Helpers
    # -------------------------------------------------------------------------

    def get_observed_insights(self, category: Optional[str] = None) -> list[UnderstandingInsight]:
        """Return insights whose facts were directly observed on disk."""
        return [
            i for i in self.insights
            if i.confidence == UnderstandingConfidence.OBSERVED
            and (category is None or i.category == category)
        ]

    def get_inferred_insights(self, category: Optional[str] = None) -> list[UnderstandingInsight]:
        """Return insights derived through reasonable inference."""
        return [
            i for i in self.insights
            if i.confidence == UnderstandingConfidence.INFERRED
            and (category is None or i.category == category)
        ]

    def get_unknown_insights(self, category: Optional[str] = None) -> list[UnderstandingInsight]:
        """Return insights that could not be determined due to missing evidence."""
        return [
            i for i in self.insights
            if i.confidence == UnderstandingConfidence.UNKNOWN
            and (category is None or i.category == category)
        ]

    def is_observed(self, category: str, key: str) -> bool:
        """Check whether a specific insight was directly observed."""
        return any(
            i.category == category and i.key == key and i.confidence == UnderstandingConfidence.OBSERVED
            for i in self.insights
        )

    def is_inferred(self, category: str, key: str) -> bool:
        """Check whether a specific insight was inferred."""
        return any(
            i.category == category and i.key == key and i.confidence == UnderstandingConfidence.INFERRED
            for i in self.insights
        )

    def is_unknown(self, category: str, key: str) -> bool:
        """Check whether a specific insight remains unknown."""
        return any(
            i.category == category and i.key == key and i.confidence == UnderstandingConfidence.UNKNOWN
            for i in self.insights
        )

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "understanding_id": self.understanding_id,
            "execution_id": self.execution_id,
            "work_order_id": self.work_order_id,
            "project_id": self.project_id,
            "repository_id": self.repository_id,
            "project_type": self.project_type,
            "project_type_confidence": self.project_type_confidence.value,
            "languages": list(self.languages),
            "frameworks": list(self.frameworks),
            "package_managers": list(self.package_managers),
            "repository_structure": dict(self.repository_structure),
            "important_directories": list(self.important_directories),
            "entry_points": list(self.entry_points),
            "configuration_files": list(self.configuration_files),
            "test_locations": list(self.test_locations),
            "dependency_manifests": list(self.dependency_manifests),
            "relevant_modules": list(self.relevant_modules),
            "detected_conventions": dict(self.detected_conventions),
            "uncertainties": list(self.uncertainties),
            "insights": [i.to_dict() for i in self.insights],
            "evidence": [e.to_dict() for e in self.evidence],
            "trace": dict(self.trace),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodebaseUnderstanding:
        conf_raw = data.get("project_type_confidence", UnderstandingConfidence.UNKNOWN.value)
        try:
            pt_conf = UnderstandingConfidence(str(conf_raw).upper())
        except (ValueError, TypeError):
            pt_conf = UnderstandingConfidence.UNKNOWN

        insights_list = [UnderstandingInsight.from_dict(i) for i in data.get("insights", [])]
        ev_list = [VerificationEvidence.from_dict(e) for e in data.get("evidence", [])]

        return cls(
            understanding_id=data["understanding_id"],
            execution_id=data["execution_id"],
            work_order_id=data["work_order_id"],
            project_id=data["project_id"],
            repository_id=data.get("repository_id"),
            project_type=data.get("project_type", "unknown"),
            project_type_confidence=pt_conf,
            languages=list(data.get("languages", [])),
            frameworks=list(data.get("frameworks", [])),
            package_managers=list(data.get("package_managers", [])),
            repository_structure=dict(data.get("repository_structure", {})),
            important_directories=list(data.get("important_directories", [])),
            entry_points=list(data.get("entry_points", [])),
            configuration_files=list(data.get("configuration_files", [])),
            test_locations=list(data.get("test_locations", [])),
            dependency_manifests=list(data.get("dependency_manifests", [])),
            relevant_modules=list(data.get("relevant_modules", [])),
            detected_conventions=dict(data.get("detected_conventions", {})),
            uncertainties=list(data.get("uncertainties", [])),
            insights=insights_list,
            evidence=ev_list,
            trace=dict(data.get("trace", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at", utc_now()),
        )

    @classmethod
    def from_json(cls, json_str: str) -> CodebaseUnderstanding:
        return cls.from_dict(json.loads(json_str))

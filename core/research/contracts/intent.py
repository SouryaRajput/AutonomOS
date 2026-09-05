from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.research.types import (
    DesiredOutput,
    FreshnessRequirement,
    IntentType,
    SourceType,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "intent") -> str:
    """Generate unique identifier with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@dataclass
class IntentConfidence:
    """
    Dimensioned confidence assessment of research intent understanding.
    Avoids opaque single magic scores by rating confidence across 5 core dimensions.
    Each score must fall in the normalized range [0.0, 1.0].
    """
    objective: float = 1.0
    entities: float = 1.0
    dimensions: float = 1.0
    constraints: float = 1.0
    assumptions: float = 1.0
    overall_override: Optional[float] = None

    @property
    def overall(self) -> float:
        """
        Computed overall confidence score. Returns overall_override if explicitly set,
        otherwise the arithmetic mean of the five core dimension scores.
        """
        if self.overall_override is not None:
            return round(self.overall_override, 4)
        scores = [self.objective, self.entities, self.dimensions, self.constraints, self.assumptions]
        return round(sum(scores) / len(scores), 4)

    def is_confident(self, threshold: float = 0.7) -> bool:
        """Return True if all core dimension confidence scores are at or above the threshold."""
        return (
            self.objective >= threshold
            and self.entities >= threshold
            and self.dimensions >= threshold
            and self.constraints >= threshold
            and self.assumptions >= threshold
        )

    def validate(self) -> list[str]:
        """Validate that all confidence scores fall within [0.0, 1.0]."""
        errors: list[str] = []
        for name, val in [
            ("objective", self.objective),
            ("entities", self.entities),
            ("dimensions", self.dimensions),
            ("constraints", self.constraints),
            ("assumptions", self.assumptions),
        ]:
            if not isinstance(val, (int, float)) or val < 0.0 or val > 1.0:
                errors.append(f"Confidence score for '{name}' must be a float in [0.0, 1.0], got {val}")
        if self.overall_override is not None:
            if not isinstance(self.overall_override, (int, float)) or self.overall_override < 0.0 or self.overall_override > 1.0:
                errors.append(f"Confidence score for 'overall_override' must be a float in [0.0, 1.0], got {self.overall_override}")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "entities": self.entities,
            "dimensions": self.dimensions,
            "constraints": self.constraints,
            "assumptions": self.assumptions,
            "overall": self.overall,
            "overall_override": self.overall_override,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IntentConfidence:
        return cls(
            objective=float(data.get("objective", 1.0)),
            entities=float(data.get("entities", 1.0)),
            dimensions=float(data.get("dimensions", 1.0)),
            constraints=float(data.get("constraints", 1.0)),
            assumptions=float(data.get("assumptions", 1.0)),
            overall_override=float(data["overall_override"]) if data.get("overall_override") is not None else None,
        )


@dataclass
class Ambiguity:
    """
    An identified ambiguity, underspecification, or conflicting premise in a research request.
    """
    ambiguity_id: str = field(default_factory=lambda: f"amb-{uuid.uuid4().hex[:6]}")
    description: str = ""
    impact: str = ""
    affected_fields: list[str] = field(default_factory=list)
    suggested_interpretation: Optional[str] = None
    blocking: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ambiguity_id": self.ambiguity_id,
            "description": self.description,
            "impact": self.impact,
            "affected_fields": list(self.affected_fields),
            "suggested_interpretation": self.suggested_interpretation,
            "blocking": self.blocking,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ambiguity:
        return cls(
            ambiguity_id=str(data.get("ambiguity_id", f"amb-{uuid.uuid4().hex[:6]}")),
            description=str(data.get("description", "")),
            impact=str(data.get("impact", "")),
            affected_fields=list(data.get("affected_fields", [])),
            suggested_interpretation=data.get("suggested_interpretation"),
            blocking=bool(data.get("blocking", False)),
        )


@dataclass
class ClarificationQuestion:
    """
    A concrete question formulated to resolve an identified ambiguity before research planning.
    """
    question_id: str = field(default_factory=lambda: f"cq-{uuid.uuid4().hex[:6]}")
    question_text: str = ""
    target_ambiguity_id: Optional[str] = None
    options: list[str] = field(default_factory=list)
    default_assumption: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question_text": self.question_text,
            "target_ambiguity_id": self.target_ambiguity_id,
            "options": list(self.options),
            "default_assumption": self.default_assumption,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClarificationQuestion:
        return cls(
            question_id=str(data.get("question_id", f"cq-{uuid.uuid4().hex[:6]}")),
            question_text=str(data.get("question_text", "")),
            target_ambiguity_id=data.get("target_ambiguity_id"),
            options=list(data.get("options", [])),
            default_assumption=data.get("default_assumption"),
        )


@dataclass
class TemporalScope:
    """
    Temporal boundaries defining the relevant epoch or time window for evidence collection.
    """
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    reference_point: Optional[str] = None
    recency_days: Optional[int] = None
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "reference_point": self.reference_point,
            "recency_days": self.recency_days,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TemporalScope:
        return cls(
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            reference_point=data.get("reference_point"),
            recency_days=int(data["recency_days"]) if data.get("recency_days") is not None else None,
            description=str(data.get("description", "")),
        )


@dataclass
class VersionScope:
    """
    Technical version constraints specifying package, runtime, or framework boundaries.
    """
    target_version: Optional[str] = None
    min_version: Optional[str] = None
    max_version: Optional[str] = None
    version_specifier: Optional[str] = None
    ecosystem: Optional[str] = None
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_version": self.target_version,
            "min_version": self.min_version,
            "max_version": self.max_version,
            "version_specifier": self.version_specifier,
            "ecosystem": self.ecosystem,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VersionScope:
        return cls(
            target_version=data.get("target_version"),
            min_version=data.get("min_version"),
            max_version=data.get("max_version"),
            version_specifier=data.get("version_specifier"),
            ecosystem=data.get("ecosystem"),
            description=str(data.get("description", "")),
        )


@dataclass
class EvidenceRequirement:
    """
    Explicit evidence criteria required to satisfy the research intent.
    Reuses existing SourceType classification from core.research.types.
    """
    requirement_id: str = field(default_factory=lambda: f"ev-req-{uuid.uuid4().hex[:6]}")
    description: str = ""
    source_types: list[SourceType] = field(default_factory=list)
    min_independent_sources: int = 1
    mandatory: bool = True
    verification_criteria: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "description": self.description,
            "source_types": [
                s.value if isinstance(s, SourceType) else str(s) for s in self.source_types
            ],
            "min_independent_sources": self.min_independent_sources,
            "mandatory": self.mandatory,
            "verification_criteria": self.verification_criteria,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceRequirement:
        st_list: list[SourceType] = []
        for s in data.get("source_types", []):
            try:
                st_list.append(SourceType(s))
            except (ValueError, TypeError):
                st_list.append(SourceType.OTHER)

        return cls(
            requirement_id=str(data.get("requirement_id", f"ev-req-{uuid.uuid4().hex[:6]}")),
            description=str(data.get("description", "")),
            source_types=st_list,
            min_independent_sources=int(data.get("min_independent_sources", 1)),
            mandatory=bool(data.get("mandatory", True)),
            verification_criteria=data.get("verification_criteria"),
        )


@dataclass
class ResearchIntent:
    """
    Domain model representing the Researcher's structured understanding of WHAT a ResearchRequest means.
    Completely decoupled from HOW the research will be performed (crawlers, search queries, or plans).
    """
    objective: str
    intent_types: list[IntentType] = field(default_factory=lambda: [IntentType.DESCRIPTIVE])
    subjects: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    comparison_targets: list[str] = field(default_factory=list)
    research_dimensions: list[str] = field(default_factory=list)
    explicit_constraints: list[str] = field(default_factory=list)
    inferred_constraints: list[str] = field(default_factory=list)
    freshness_requirement: FreshnessRequirement = FreshnessRequirement.STATIC
    temporal_scope: Optional[TemporalScope] = None
    geographic_scope: Optional[str] = None
    version_scope: Optional[VersionScope] = None
    desired_output: DesiredOutput = DesiredOutput.FACTUAL_ANSWER
    evidence_requirements: list[EvidenceRequirement] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    ambiguities: list[Ambiguity] = field(default_factory=list)
    clarification_required: bool = False
    clarification_questions: list[ClarificationQuestion] = field(default_factory=list)
    confidence: IntentConfidence = field(default_factory=IntentConfidence)
    source_request_id: str = ""
    understanding_trace: list[str] = field(default_factory=list)
    intent_id: str = field(default_factory=lambda: new_id("intent"))
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def has_intent_type(self, intent_type: IntentType | str) -> bool:
        """Check if the intent includes a specific intent type."""
        match_val = intent_type.value if isinstance(intent_type, IntentType) else str(intent_type)
        return any(
            (it.value if isinstance(it, IntentType) else str(it)) == match_val
            for it in self.intent_types
        )

    def has_blocking_ambiguity(self) -> bool:
        """Return True if any registered ambiguity is marked as blocking."""
        return any(amb.blocking for amb in self.ambiguities)

    def add_ambiguity(
        self,
        description: str,
        impact: str = "",
        affected_fields: Optional[list[str]] = None,
        suggested_interpretation: Optional[str] = None,
        blocking: bool = False,
    ) -> Ambiguity:
        """Add an ambiguity to the intent and update clarification_required if blocking."""
        amb = Ambiguity(
            description=description,
            impact=impact,
            affected_fields=affected_fields or [],
            suggested_interpretation=suggested_interpretation,
            blocking=blocking,
        )
        self.ambiguities.append(amb)
        if blocking:
            self.clarification_required = True
        return amb

    def add_clarification_question(
        self,
        question_text: str,
        target_ambiguity_id: Optional[str] = None,
        options: Optional[list[str]] = None,
        default_assumption: Optional[str] = None,
    ) -> ClarificationQuestion:
        """Add a clarification question and mark clarification_required."""
        cq = ClarificationQuestion(
            question_text=question_text,
            target_ambiguity_id=target_ambiguity_id,
            options=options or [],
            default_assumption=default_assumption,
        )
        self.clarification_questions.append(cq)
        self.clarification_required = True
        return cq

    def add_evidence_requirement(
        self,
        description: str,
        source_types: Optional[list[SourceType]] = None,
        min_independent_sources: int = 1,
        mandatory: bool = True,
        verification_criteria: Optional[str] = None,
    ) -> EvidenceRequirement:
        """Add an evidence requirement to the intent."""
        req = EvidenceRequirement(
            description=description,
            source_types=source_types or [],
            min_independent_sources=min_independent_sources,
            mandatory=mandatory,
            verification_criteria=verification_criteria,
        )
        self.evidence_requirements.append(req)
        return req

    def validate(self) -> list[str]:
        """
        Validate semantic invariants of the ResearchIntent.
        Returns a list of validation error strings. An empty list signifies a valid intent.
        """
        errors: list[str] = []

        if not self.objective or not self.objective.strip():
            errors.append("ResearchIntent 'objective' cannot be empty or whitespace.")

        if not self.intent_types:
            errors.append("ResearchIntent must specify at least one IntentType in 'intent_types'.")

        # Confidence bounds validation
        errors.extend(self.confidence.validate())

        # If clarification is required, verify there are ambiguities or questions
        if self.clarification_required and not self.ambiguities and not self.clarification_questions:
            errors.append(
                "ResearchIntent has 'clarification_required=True' but specifies no ambiguities or clarification_questions."
            )

        # Comparative intent requires comparison targets or entities
        if self.has_intent_type(IntentType.COMPARATIVE):
            if not self.comparison_targets and len(self.entities) < 2 and len(self.subjects) < 2:
                errors.append(
                    "ResearchIntent with COMPARATIVE intent type should declare comparison_targets or at least 2 entities/subjects."
                )

        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialize ResearchIntent to a standard JSON-compatible dictionary."""
        return {
            "intent_id": self.intent_id,
            "objective": self.objective,
            "intent_types": [
                it.value if isinstance(it, IntentType) else str(it) for it in self.intent_types
            ],
            "subjects": list(self.subjects),
            "entities": list(self.entities),
            "comparison_targets": list(self.comparison_targets),
            "research_dimensions": list(self.research_dimensions),
            "explicit_constraints": list(self.explicit_constraints),
            "inferred_constraints": list(self.inferred_constraints),
            "freshness_requirement": (
                self.freshness_requirement.value
                if isinstance(self.freshness_requirement, FreshnessRequirement)
                else str(self.freshness_requirement)
            ),
            "temporal_scope": self.temporal_scope.to_dict() if self.temporal_scope else None,
            "geographic_scope": self.geographic_scope,
            "version_scope": self.version_scope.to_dict() if self.version_scope else None,
            "desired_output": (
                self.desired_output.value
                if isinstance(self.desired_output, DesiredOutput)
                else str(self.desired_output)
            ),
            "evidence_requirements": [e.to_dict() for e in self.evidence_requirements],
            "assumptions": list(self.assumptions),
            "ambiguities": [a.to_dict() for a in self.ambiguities],
            "clarification_required": self.clarification_required,
            "clarification_questions": [cq.to_dict() for cq in self.clarification_questions],
            "confidence": self.confidence.to_dict(),
            "source_request_id": self.source_request_id,
            "understanding_trace": list(self.understanding_trace),
            "correlation_id": self.correlation_id,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchIntent:
        """Construct a strongly-typed ResearchIntent from a serialized dictionary."""
        # Intent types
        intent_types: list[IntentType] = []
        for it_raw in data.get("intent_types", []):
            try:
                intent_types.append(IntentType(it_raw))
            except (ValueError, TypeError):
                pass
        if not intent_types:
            intent_types = [IntentType.DESCRIPTIVE]

        # Freshness requirement
        fr_raw = data.get("freshness_requirement", FreshnessRequirement.STATIC.value)
        try:
            freshness = FreshnessRequirement(fr_raw)
        except (ValueError, TypeError):
            freshness = FreshnessRequirement.STATIC

        # Desired output
        do_raw = data.get("desired_output", DesiredOutput.FACTUAL_ANSWER.value)
        try:
            desired_output = DesiredOutput(do_raw)
        except (ValueError, TypeError):
            desired_output = DesiredOutput.FACTUAL_ANSWER

        # Temporal scope
        temporal_scope = (
            TemporalScope.from_dict(data["temporal_scope"])
            if data.get("temporal_scope") is not None
            else None
        )

        # Version scope
        version_scope = (
            VersionScope.from_dict(data["version_scope"])
            if data.get("version_scope") is not None
            else None
        )

        # Evidence requirements
        evidence_reqs = [
            EvidenceRequirement.from_dict(e) if isinstance(e, dict) else e
            for e in data.get("evidence_requirements", [])
        ]

        # Ambiguities
        ambiguities = [
            Ambiguity.from_dict(a) if isinstance(a, dict) else a
            for a in data.get("ambiguities", [])
        ]

        # Clarification questions
        clarification_questions = [
            ClarificationQuestion.from_dict(cq) if isinstance(cq, dict) else cq
            for cq in data.get("clarification_questions", [])
        ]

        # Confidence
        conf_data = data.get("confidence", {})
        confidence = IntentConfidence.from_dict(conf_data) if isinstance(conf_data, dict) else IntentConfidence()

        return cls(
            intent_id=str(data.get("intent_id", new_id("intent"))),
            objective=str(data.get("objective", "")),
            intent_types=intent_types,
            subjects=list(data.get("subjects", [])),
            entities=list(data.get("entities", [])),
            comparison_targets=list(data.get("comparison_targets", [])),
            research_dimensions=list(data.get("research_dimensions", [])),
            explicit_constraints=list(data.get("explicit_constraints", [])),
            inferred_constraints=list(data.get("inferred_constraints", [])),
            freshness_requirement=freshness,
            temporal_scope=temporal_scope,
            geographic_scope=data.get("geographic_scope"),
            version_scope=version_scope,
            desired_output=desired_output,
            evidence_requirements=evidence_reqs,
            assumptions=list(data.get("assumptions", [])),
            ambiguities=ambiguities,
            clarification_required=bool(data.get("clarification_required", False)),
            clarification_questions=clarification_questions,
            confidence=confidence,
            source_request_id=str(data.get("source_request_id", "")),
            understanding_trace=list(data.get("understanding_trace", [])),
            correlation_id=str(data.get("correlation_id", str(uuid.uuid4()))),
            created_at=str(data.get("created_at", utc_now())),
            metadata=dict(data.get("metadata", {})),
        )

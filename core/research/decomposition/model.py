from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from core.research.contracts.intent import TemporalScope, VersionScope
from core.research.decomposition.types import (
    ResearchDependencyType,
    ResearchPriority,
    SubQuestionPriority,
    SubQuestionStatus,
    SubQuestionType,
)
from core.research.types import SourceType


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "subq") -> str:
    """Generate unique identifier with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@dataclass
class ResearchScope:
    """
    Scope boundaries constraining an individual sub-question's investigation.
    Reuses existing TemporalScope, VersionScope, and SourceType classifications.
    """
    entities: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    geographic_scope: Optional[str] = None
    temporal_scope: Optional[TemporalScope] = None
    version_scope: Optional[VersionScope] = None
    project_scope: list[str] = field(default_factory=list)
    source_expectations: list[SourceType] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": list(self.entities),
            "domains": list(self.domains),
            "geographic_scope": self.geographic_scope,
            "temporal_scope": self.temporal_scope.to_dict() if self.temporal_scope else None,
            "version_scope": self.version_scope.to_dict() if self.version_scope else None,
            "project_scope": list(self.project_scope),
            "source_expectations": [
                s.value if isinstance(s, SourceType) else str(s) for s in self.source_expectations
            ],
            "exclusions": list(self.exclusions),
            "constraints": list(self.constraints),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchScope:
        temp_scope = (
            TemporalScope.from_dict(data["temporal_scope"])
            if data.get("temporal_scope") is not None
            else None
        )
        ver_scope = (
            VersionScope.from_dict(data["version_scope"])
            if data.get("version_scope") is not None
            else None
        )

        st_list: list[SourceType] = []
        for s in data.get("source_expectations", []):
            try:
                st_list.append(SourceType(s))
            except (ValueError, TypeError):
                st_list.append(SourceType.OTHER)

        return cls(
            entities=list(data.get("entities", [])),
            domains=list(data.get("domains", [])),
            geographic_scope=data.get("geographic_scope"),
            temporal_scope=temp_scope,
            version_scope=ver_scope,
            project_scope=list(data.get("project_scope", [])),
            source_expectations=st_list,
            exclusions=list(data.get("exclusions", [])),
            constraints=list(data.get("constraints", [])),
        )


@dataclass
class ExpectedEvidence:
    """
    Structured expectation describing what category and quality of evidence
    would satisfy a sub-question.
    NOTE: This is an expectation, NOT evidence itself.
    """
    expectation_id: str = field(default_factory=lambda: new_id("exp-ev"))
    description: str = ""
    source_types: list[SourceType] = field(default_factory=list)
    min_independent_sources: int = 1
    mandatory: bool = True
    verification_method: Optional[str] = None
    confidence_requirement: float = 0.8

    def to_dict(self) -> dict[str, Any]:
        return {
            "expectation_id": self.expectation_id,
            "description": self.description,
            "source_types": [
                s.value if isinstance(s, SourceType) else str(s) for s in self.source_types
            ],
            "min_independent_sources": self.min_independent_sources,
            "mandatory": self.mandatory,
            "verification_method": self.verification_method,
            "confidence_requirement": self.confidence_requirement,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExpectedEvidence:
        st_list: list[SourceType] = []
        for s in data.get("source_types", []):
            try:
                st_list.append(SourceType(s))
            except (ValueError, TypeError):
                st_list.append(SourceType.OTHER)

        return cls(
            expectation_id=str(data.get("expectation_id", new_id("exp-ev"))),
            description=str(data.get("description", "")),
            source_types=st_list,
            min_independent_sources=int(data.get("min_independent_sources", 1)),
            mandatory=bool(data.get("mandatory", True)),
            verification_method=data.get("verification_method"),
            confidence_requirement=float(data.get("confidence_requirement", 0.8)),
        )


@dataclass
class AcceptanceCriteria:
    """
    Structured criteria describing when a sub-question can be considered sufficiently resolved.
    """
    criteria_id: str = field(default_factory=lambda: new_id("ac"))
    description: str = ""
    mandatory: bool = True
    verification_aspect: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "criteria_id": self.criteria_id,
            "description": self.description,
            "mandatory": self.mandatory,
            "verification_aspect": self.verification_aspect,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AcceptanceCriteria:
        return cls(
            criteria_id=str(data.get("criteria_id", new_id("ac"))),
            description=str(data.get("description", "")),
            mandatory=bool(data.get("mandatory", True)),
            verification_aspect=str(data.get("verification_aspect", "")),
            notes=str(data.get("notes", "")),
        )


@dataclass
class ResearchDependency:
    """
    Explicit directed dependency between two research sub-questions.
    prerequisite_id -> dependent_id (dependent requires prerequisite).
    """
    dependency_id: str = field(default_factory=lambda: new_id("dep"))
    prerequisite_id: str = ""
    dependent_id: str = ""
    dependency_type: ResearchDependencyType = ResearchDependencyType.PREREQUISITE
    rationale: str = ""
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dependency_id": self.dependency_id,
            "prerequisite_id": self.prerequisite_id,
            "dependent_id": self.dependent_id,
            "dependency_type": (
                self.dependency_type.value
                if isinstance(self.dependency_type, ResearchDependencyType)
                else str(self.dependency_type)
            ),
            "rationale": self.rationale,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchDependency:
        dt_raw = data.get("dependency_type", ResearchDependencyType.PREREQUISITE.value)
        try:
            dep_type = ResearchDependencyType(dt_raw)
        except (ValueError, TypeError):
            dep_type = ResearchDependencyType.PREREQUISITE

        return cls(
            dependency_id=str(data.get("dependency_id", new_id("dep"))),
            prerequisite_id=str(data.get("prerequisite_id", "")),
            dependent_id=str(data.get("dependent_id", "")),
            dependency_type=dep_type,
            rationale=str(data.get("rationale", "")),
            created_at=str(data.get("created_at", utc_now())),
        )


@dataclass
class SubQuestionProvenance:
    """
    Audit provenance linking a sub-question back to the root request, intent,
    and decomposition.
    """
    research_request_id: str = ""
    decomposition_id: str = ""
    research_intent_id: Optional[str] = None
    originating_requirement: str = ""
    parent_sub_question_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "research_request_id": self.research_request_id,
            "decomposition_id": self.decomposition_id,
            "research_intent_id": self.research_intent_id,
            "originating_requirement": self.originating_requirement,
            "parent_sub_question_id": self.parent_sub_question_id,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubQuestionProvenance:
        return cls(
            research_request_id=str(data.get("research_request_id", "")),
            decomposition_id=str(data.get("decomposition_id", "")),
            research_intent_id=data.get("research_intent_id"),
            originating_requirement=str(data.get("originating_requirement", "")),
            parent_sub_question_id=data.get("parent_sub_question_id"),
            created_at=str(data.get("created_at", utc_now())),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ResearchSubQuestion:
    """
    Internal investigative unit representing a decomposed piece of research inquiry.
    IMPORTANT: This is strictly an INTERNAL research planning concept.
    It must NOT be confused with the user-facing Human-In-The-Loop ResearchQuestion.
    """
    sub_question_id: str = field(default_factory=lambda: new_id("subq"))
    decomposition_id: str = ""
    parent_id: Optional[str] = None
    question: str = ""
    objective: str = ""
    sub_question_type: SubQuestionType = SubQuestionType.FACT_FINDING
    rationale: str = ""
    priority: SubQuestionPriority = SubQuestionPriority.MEDIUM
    scope: Optional[ResearchScope] = None
    required: bool = True
    status: SubQuestionStatus = SubQuestionStatus.PENDING
    dependencies: list[str] = field(default_factory=list)  # list of prerequisite sub_question_ids
    expected_evidence: list[ExpectedEvidence] = field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriteria] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    decomposition_depth: int = 1
    provenance: Optional[SubQuestionProvenance] = None
    trace: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_root(self) -> bool:
        """Return True if this is a top-level sub-question (no parent)."""
        return self.parent_id is None or self.decomposition_depth == 1

    def add_trace(self, entry: str) -> None:
        """Append timestamped trace entry."""
        self.trace.append(f"[{utc_now()}] {entry}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sub_question_id": self.sub_question_id,
            "decomposition_id": self.decomposition_id,
            "parent_id": self.parent_id,
            "question": self.question,
            "objective": self.objective,
            "sub_question_type": (
                self.sub_question_type.value
                if isinstance(self.sub_question_type, SubQuestionType)
                else str(self.sub_question_type)
            ),
            "rationale": self.rationale,
            "priority": (
                self.priority.value
                if isinstance(self.priority, SubQuestionPriority)
                else str(self.priority)
            ),
            "scope": self.scope.to_dict() if self.scope else None,
            "required": self.required,
            "status": (
                self.status.value
                if isinstance(self.status, SubQuestionStatus)
                else str(self.status)
            ),
            "dependencies": list(self.dependencies),
            "expected_evidence": [e.to_dict() for e in self.expected_evidence],
            "acceptance_criteria": [a.to_dict() for a in self.acceptance_criteria],
            "assumptions": list(self.assumptions),
            "ambiguities": list(self.ambiguities),
            "decomposition_depth": self.decomposition_depth,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "trace": list(self.trace),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchSubQuestion:
        # Resolve sub_question_type
        sq_raw = data.get("sub_question_type", SubQuestionType.FACT_FINDING.value)
        try:
            sq_type = SubQuestionType(sq_raw)
        except (ValueError, TypeError):
            sq_type = SubQuestionType.FACT_FINDING

        # Resolve priority
        p_raw = data.get("priority", SubQuestionPriority.MEDIUM.value)
        try:
            priority = SubQuestionPriority(p_raw)
        except (ValueError, TypeError):
            priority = SubQuestionPriority.MEDIUM

        # Resolve status
        s_raw = data.get("status", SubQuestionStatus.PENDING.value)
        try:
            status = SubQuestionStatus(s_raw)
        except (ValueError, TypeError):
            status = SubQuestionStatus.PENDING

        scope_data = data.get("scope")
        scope = ResearchScope.from_dict(scope_data) if isinstance(scope_data, dict) else None

        evidence_list = [
            ExpectedEvidence.from_dict(e)
            for e in data.get("expected_evidence", [])
            if isinstance(e, dict)
        ]

        criteria_list = [
            AcceptanceCriteria.from_dict(c)
            for c in data.get("acceptance_criteria", [])
            if isinstance(c, dict)
        ]

        prov_data = data.get("provenance")
        provenance = (
            SubQuestionProvenance.from_dict(prov_data)
            if isinstance(prov_data, dict)
            else None
        )

        return cls(
            sub_question_id=str(data.get("sub_question_id", new_id("subq"))),
            decomposition_id=str(data.get("decomposition_id", "")),
            parent_id=data.get("parent_id"),
            question=str(data.get("question", "")),
            objective=str(data.get("objective", "")),
            sub_question_type=sq_type,
            rationale=str(data.get("rationale", "")),
            priority=priority,
            scope=scope,
            required=bool(data.get("required", True)),
            status=status,
            dependencies=list(data.get("dependencies", [])),
            expected_evidence=evidence_list,
            acceptance_criteria=criteria_list,
            assumptions=list(data.get("assumptions", [])),
            ambiguities=list(data.get("ambiguities", [])),
            decomposition_depth=int(data.get("decomposition_depth", 1)),
            provenance=provenance,
            trace=list(data.get("trace", [])),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ResearchDecomposition:
    """
    Authoritative domain model representing a structured, multi-unit decomposition
    of a ResearchRequest and ResearchIntent.
    Links the parent research objective into a directed graph of ResearchSubQuestions
    with hierarchical depth, explicit dependencies, and audit lineage.
    """
    decomposition_id: str = field(default_factory=lambda: new_id("decomp"))
    research_request_id: str = ""
    research_intent_id: Optional[str] = None
    objective: str = ""
    root_question: str = ""
    sub_questions: list[ResearchSubQuestion] = field(default_factory=list)
    dependencies: list[ResearchDependency] = field(default_factory=list)
    unresolved_decisions: list[str] = field(default_factory=list)
    coverage_requirements: list[str] = field(default_factory=list)
    decomposition_confidence: float = 1.0
    decomposition_trace: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    version: int = 1
    max_depth_limit: int = 4
    metadata: dict[str, Any] = field(default_factory=dict)

    # -------------------------------------------------------------------------
    # Graph & Lookup Helpers
    # -------------------------------------------------------------------------
    def get_sub_question(self, sub_question_id: str) -> Optional[ResearchSubQuestion]:
        """Lookup sub-question by ID."""
        for sq in self.sub_questions:
            if sq.sub_question_id == sub_question_id:
                return sq
        return None

    def get_root_sub_questions(self) -> list[ResearchSubQuestion]:
        """Return all top-level sub-questions without parents."""
        return [sq for sq in self.sub_questions if sq.is_root()]

    def get_children(self, parent_id: str) -> list[ResearchSubQuestion]:
        """Return all direct child sub-questions for a given parent ID."""
        return [sq for sq in self.sub_questions if sq.parent_id == parent_id]

    def get_dependencies_for(self, sub_question_id: str) -> list[ResearchDependency]:
        """Return all explicit dependency records where sub_question_id is the dependent."""
        return [dep for dep in self.dependencies if dep.dependent_id == sub_question_id]

    def get_prerequisites(self, sub_question_id: str) -> list[ResearchSubQuestion]:
        """Return all sub-questions that must precede sub_question_id."""
        prereq_ids = {
            dep.prerequisite_id
            for dep in self.dependencies
            if dep.dependent_id == sub_question_id
        }
        # Also include any listed in sq.dependencies
        sq = self.get_sub_question(sub_question_id)
        if sq:
            prereq_ids.update(sq.dependencies)
        return [q for q in self.sub_questions if q.sub_question_id in prereq_ids]

    def get_dependents(self, sub_question_id: str) -> list[ResearchSubQuestion]:
        """Return all sub-questions that depend on sub_question_id."""
        dep_ids = {
            dep.dependent_id
            for dep in self.dependencies
            if dep.prerequisite_id == sub_question_id
        }
        for sq in self.sub_questions:
            if sub_question_id in sq.dependencies:
                dep_ids.add(sq.sub_question_id)
        return [q for q in self.sub_questions if q.sub_question_id in dep_ids]

    def max_depth(self) -> int:
        """Compute the maximum depth currently present across all sub-questions."""
        if not self.sub_questions:
            return 0
        return max(sq.decomposition_depth for sq in self.sub_questions)

    def add_sub_question(self, sub_question: ResearchSubQuestion) -> None:
        """Register a sub-question into the decomposition."""
        sub_question.decomposition_id = self.decomposition_id
        if sub_question.provenance and not sub_question.provenance.decomposition_id:
            sub_question.provenance.decomposition_id = self.decomposition_id
        if sub_question.provenance and not sub_question.provenance.research_request_id:
            sub_question.provenance.research_request_id = self.research_request_id
        self.sub_questions.append(sub_question)
        self.add_trace(f"Added sub-question '{sub_question.sub_question_id}' (depth: {sub_question.decomposition_depth})")

    def add_dependency(self, dependency: ResearchDependency) -> None:
        """Register a dependency into the decomposition."""
        self.dependencies.append(dependency)
        # Ensure dependent sub-question's dependencies list contains prerequisite_id
        dependent = self.get_sub_question(dependency.dependent_id)
        if dependent and dependency.prerequisite_id not in dependent.dependencies:
            dependent.dependencies.append(dependency.prerequisite_id)
        self.add_trace(f"Added dependency: {dependency.prerequisite_id} -> {dependency.dependent_id} ({dependency.dependency_type.value})")

    def add_trace(self, entry: str) -> None:
        """Append timestamped entry to the decomposition trace."""
        self.decomposition_trace.append(f"[{utc_now()}] {entry}")

    def plan_execution_order(self) -> Any:
        """Compute authoritative deterministic execution order plan for sub-questions."""
        from core.research.decomposition.ordering import SubQuestionOrderPlanner
        return SubQuestionOrderPlanner.plan_order(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decomposition_id": self.decomposition_id,
            "research_request_id": self.research_request_id,
            "research_intent_id": self.research_intent_id,
            "objective": self.objective,
            "root_question": self.root_question,
            "sub_questions": [sq.to_dict() for sq in self.sub_questions],
            "dependencies": [dep.to_dict() for dep in self.dependencies],
            "unresolved_decisions": list(self.unresolved_decisions),
            "coverage_requirements": list(self.coverage_requirements),
            "decomposition_confidence": self.decomposition_confidence,
            "decomposition_trace": list(self.decomposition_trace),
            "created_at": self.created_at,
            "version": self.version,
            "max_depth_limit": self.max_depth_limit,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchDecomposition:
        sub_questions = [
            ResearchSubQuestion.from_dict(sq)
            for sq in data.get("sub_questions", [])
            if isinstance(sq, dict)
        ]
        dependencies = [
            ResearchDependency.from_dict(dep)
            for dep in data.get("dependencies", [])
            if isinstance(dep, dict)
        ]

        return cls(
            decomposition_id=str(data.get("decomposition_id", new_id("decomp"))),
            research_request_id=str(data.get("research_request_id", "")),
            research_intent_id=data.get("research_intent_id"),
            objective=str(data.get("objective", "")),
            root_question=str(data.get("root_question", "")),
            sub_questions=sub_questions,
            dependencies=dependencies,
            unresolved_decisions=list(data.get("unresolved_decisions", [])),
            coverage_requirements=list(data.get("coverage_requirements", [])),
            decomposition_confidence=float(data.get("decomposition_confidence", 1.0)),
            decomposition_trace=list(data.get("decomposition_trace", [])),
            created_at=str(data.get("created_at", utc_now())),
            version=int(data.get("version", 1)),
            max_depth_limit=int(data.get("max_depth_limit", 4)),
            metadata=dict(data.get("metadata", {})),
        )

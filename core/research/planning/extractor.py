from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Optional
import uuid

from core.research.contracts.intent import (
    Ambiguity,
    EvidenceRequirement,
    ResearchIntent,
    TemporalScope,
    VersionScope,
)
from core.research.contracts.request import ResearchRequest
from core.research.types import (
    DesiredOutput,
    FreshnessRequirement,
    SourceType,
)


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ExtractionProvenance:
    """
    Provenance record tracking the origin, rule name, matched text, and confidence
    for a deterministically extracted request element.
    """
    element_type: str
    value: Any
    source_field: str
    matched_text: str
    rule_name: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_type": self.element_type,
            "value": self.value,
            "source_field": self.source_field,
            "matched_text": self.matched_text,
            "rule_name": self.rule_name,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractionProvenance:
        return cls(
            element_type=str(data.get("element_type", "")),
            value=data.get("value"),
            source_field=str(data.get("source_field", "")),
            matched_text=str(data.get("matched_text", "")),
            rule_name=str(data.get("rule_name", "")),
            confidence=float(data.get("confidence", 0.0)),
        )


@dataclass
class ExtractionUncertainty:
    """
    Explicit representation of missing, unconstrained, or underspecified elements.
    Prevents downstream components from silently inventing or hallucinating values.
    """
    field_name: str
    reason: str
    is_ambiguous: bool = False
    suggested_clarification: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "reason": self.reason,
            "is_ambiguous": self.is_ambiguous,
            "suggested_clarification": self.suggested_clarification,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractionUncertainty:
        return cls(
            field_name=str(data.get("field_name", "")),
            reason=str(data.get("reason", "")),
            is_ambiguous=bool(data.get("is_ambiguous", False)),
            suggested_clarification=data.get("suggested_clarification"),
        )


@dataclass
class ExtractedRequestElements:
    """
    Typed result containing explicit information deterministically extracted from a ResearchRequest.
    Preserves strict boundary between explicit facts and unconstrained/uncertain fields.
    """
    extraction_id: str = field(default_factory=lambda: f"ext-{uuid.uuid4().hex[:8]}")
    source_request_id: str = ""
    subjects: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    comparison_targets: list[str] = field(default_factory=list)
    research_dimensions: list[str] = field(default_factory=list)
    explicit_constraints: list[str] = field(default_factory=list)
    temporal_scope: Optional[TemporalScope] = None
    geographic_scope: Optional[str] = None
    version_scope: Optional[VersionScope] = None
    freshness_requirement: Optional[FreshnessRequirement] = None
    desired_output: Optional[DesiredOutput] = None
    evidence_requirements: list[EvidenceRequirement] = field(default_factory=list)
    uncertainties: list[ExtractionUncertainty] = field(default_factory=list)
    provenance: list[ExtractionProvenance] = field(default_factory=list)
    confidence: dict[str, float] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def has_uncertainty(self, field_name: str) -> bool:
        """Return True if an uncertainty record exists for the given field."""
        return any(u.field_name == field_name for u in self.uncertainties)

    def get_uncertainties(self, field_name: str) -> list[ExtractionUncertainty]:
        """Return all uncertainty records matching the specified field."""
        return [u for u in self.uncertainties if u.field_name == field_name]

    def apply_to_intent(self, intent: ResearchIntent) -> ResearchIntent:
        """
        Composes explicit extracted elements into an existing ResearchIntent instance
        without overwriting pre-existing fields, preserving provenance.
        """
        # 1. Deduplicating list extensions
        for sub in self.subjects:
            if sub not in intent.subjects:
                intent.subjects.append(sub)

        for ent in self.entities:
            if ent not in intent.entities:
                intent.entities.append(ent)

        for comp in self.comparison_targets:
            if comp not in intent.comparison_targets:
                intent.comparison_targets.append(comp)

        for dim in self.research_dimensions:
            if dim not in intent.research_dimensions:
                intent.research_dimensions.append(dim)

        for con in self.explicit_constraints:
            if con not in intent.explicit_constraints:
                intent.explicit_constraints.append(con)

        # 2. Scoped objects (populate if intent has None)
        if self.temporal_scope is not None and intent.temporal_scope is None:
            intent.temporal_scope = self.temporal_scope

        if self.geographic_scope is not None and intent.geographic_scope is None:
            intent.geographic_scope = self.geographic_scope

        if self.version_scope is not None and intent.version_scope is None:
            intent.version_scope = self.version_scope

        if self.freshness_requirement is not None and intent.freshness_requirement == FreshnessRequirement.STATIC:
            intent.freshness_requirement = self.freshness_requirement

        if self.desired_output is not None and intent.desired_output == DesiredOutput.FACTUAL_ANSWER:
            intent.desired_output = self.desired_output

        # 3. Evidence requirements
        for ev in self.evidence_requirements:
            if not any(e.description == ev.description for e in intent.evidence_requirements):
                intent.evidence_requirements.append(ev)

        # 4. Propagate blocking ambiguities
        for unc in self.uncertainties:
            if unc.is_ambiguous:
                amb = Ambiguity(
                    description=unc.reason,
                    impact=f"Unresolved ambiguity in {unc.field_name}",
                    affected_fields=[unc.field_name],
                    suggested_interpretation=unc.suggested_clarification,
                    blocking=True,
                )
                intent.ambiguities.append(amb)
                intent.clarification_required = True

        return intent

    def to_dict(self) -> dict[str, Any]:
        return {
            "extraction_id": self.extraction_id,
            "source_request_id": self.source_request_id,
            "subjects": list(self.subjects),
            "entities": list(self.entities),
            "comparison_targets": list(self.comparison_targets),
            "research_dimensions": list(self.research_dimensions),
            "explicit_constraints": list(self.explicit_constraints),
            "temporal_scope": self.temporal_scope.to_dict() if self.temporal_scope else None,
            "geographic_scope": self.geographic_scope,
            "version_scope": self.version_scope.to_dict() if self.version_scope else None,
            "freshness_requirement": self.freshness_requirement.value if self.freshness_requirement else None,
            "desired_output": self.desired_output.value if self.desired_output else None,
            "evidence_requirements": [e.to_dict() for e in self.evidence_requirements],
            "uncertainties": [u.to_dict() for u in self.uncertainties],
            "provenance": [p.to_dict() for p in self.provenance],
            "confidence": dict(self.confidence),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractedRequestElements:
        freshness = None
        if data.get("freshness_requirement"):
            try:
                freshness = FreshnessRequirement(data["freshness_requirement"])
            except ValueError:
                freshness = None

        desired_output = None
        if data.get("desired_output"):
            try:
                desired_output = DesiredOutput(data["desired_output"])
            except ValueError:
                desired_output = None

        temporal = TemporalScope.from_dict(data["temporal_scope"]) if data.get("temporal_scope") else None
        version = VersionScope.from_dict(data["version_scope"]) if data.get("version_scope") else None

        evidence_reqs = [
            EvidenceRequirement.from_dict(e) for e in data.get("evidence_requirements", [])
        ]
        uncertainties = [
            ExtractionUncertainty.from_dict(u) for u in data.get("uncertainties", [])
        ]
        provenance = [
            ExtractionProvenance.from_dict(p) for p in data.get("provenance", [])
        ]

        return cls(
            extraction_id=str(data.get("extraction_id", f"ext-{uuid.uuid4().hex[:8]}")),
            source_request_id=str(data.get("source_request_id", "")),
            subjects=list(data.get("subjects", [])),
            entities=list(data.get("entities", [])),
            comparison_targets=list(data.get("comparison_targets", [])),
            research_dimensions=list(data.get("research_dimensions", [])),
            explicit_constraints=list(data.get("explicit_constraints", [])),
            temporal_scope=temporal,
            geographic_scope=data.get("geographic_scope"),
            version_scope=version,
            freshness_requirement=freshness,
            desired_output=desired_output,
            evidence_requirements=evidence_reqs,
            uncertainties=uncertainties,
            provenance=provenance,
            confidence=dict(data.get("confidence", {})),
            created_at=str(data.get("created_at", utc_now())),
        )


class DeterministicRequestExtractor:
    """
    Deterministic extraction engine for Request Understanding.
    Extracts purely explicit elements present in a ResearchRequest.
    Never infers values or assumes default values silently.
    Preserves uncertainty for unconstrained fields.
    """

    MAX_DETERMINISTIC_CONFIDENCE = 0.95

    # Known standard software/library entities
    COMMON_ENTITIES = {
        "redis", "rabbitmq", "kafka", "postgresql", "postgres", "mysql", "sqlite",
        "mongodb", "elasticsearch", "cassandra", "dynamodb", "fastapi", "flask",
        "django", "express", "nestjs", "react", "vue", "angular", "next.js",
        "docker", "kubernetes", "k8s", "terraform", "ansible", "nginx", "envoy",
        "python", "rust", "golang", "go", "typescript", "javascript", "c++",
        "jwt", "oauth2", "grpc", "graphql", "rest", "prometheus", "grafana",
        "celery", "airflow", "clickhouse", "snowflake", "linux", "aws", "gcp", "azure",
        "memcached"
    }

    GENERIC_COMPARISON_PLACEHOLDERS = {
        "alternatives", "alternative", "competitors", "competitor",
        "others", "other", "options", "option", "rest", "anything"
    }

    # Standard research dimensions
    DIMENSION_KEYWORDS = {
        "performance": ["performance", "throughput", "latency", "benchmark", "benchmarks", "speed", "memory usage", "cpu usage"],
        "security": ["security", "vulnerability", "vulnerabilities", "auth", "authentication", "authorization", "encryption", "audit", "cve"],
        "scalability": ["scalability", "horizontal scaling", "concurrency", "clustering", "sharding", "load balancing"],
        "cost": ["cost", "pricing", "tco", "budget", "license", "licensing", "total cost of ownership"],
        "reliability": ["reliability", "availability", "fault tolerance", "failover", "resilience", "disaster recovery"],
        "maintainability": ["maintainability", "developer experience", "dx", "readability", "code complexity", "ease of use"],
        "compatibility": ["compatibility", "interoperability", "backward compatibility", "cross-platform", "portability"],
        "architecture": ["architecture", "design pattern", "system design", "component layout"],
    }

    def extract(self, request: ResearchRequest) -> ExtractedRequestElements:
        """
        Execute deterministic extraction across all explicit fields of a ResearchRequest.
        Returns a typed ExtractedRequestElements structure with provenance and uncertainty records.
        """
        if not isinstance(request, ResearchRequest):
            raise TypeError(f"Expected ResearchRequest instance, got {type(request).__name__}")

        provenance: list[ExtractionProvenance] = []
        uncertainties: list[ExtractionUncertainty] = []
        confidence: dict[str, float] = {}

        # 1. Extract Subjects
        subjects = self._extract_subjects(request, provenance)
        if subjects:
            confidence["subjects"] = min(0.85, self.MAX_DETERMINISTIC_CONFIDENCE)
        else:
            uncertainties.append(
                ExtractionUncertainty(
                    field_name="subjects",
                    reason="No explicit subject topic phrase isolated; request objective acts as general topic",
                    is_ambiguous=False,
                )
            )

        # 2. Extract Entities
        entities = self._extract_entities(request, provenance)
        if entities:
            confidence["entities"] = min(0.90, self.MAX_DETERMINISTIC_CONFIDENCE)
        else:
            uncertainties.append(
                ExtractionUncertainty(
                    field_name="entities",
                    reason="No specific technical entities, libraries, or standards detected in request",
                    is_ambiguous=False,
                )
            )

        # 3. Extract Comparison Targets
        comp_targets, is_comp_ambiguous = self._extract_comparison_targets(request, provenance)
        if comp_targets:
            confidence["comparison_targets"] = min(0.92, self.MAX_DETERMINISTIC_CONFIDENCE)
            # Ensure explicit comparison targets are also registered as entities
            for ct in comp_targets:
                if ct not in entities:
                    entities.append(ct)
        if is_comp_ambiguous:
            uncertainties.append(
                ExtractionUncertainty(
                    field_name="comparison_targets",
                    reason="Comparative inquiry detected but could not identify at least two explicit targets",
                    is_ambiguous=True,
                    suggested_clarification="Specify the second entity or alternative for comparison",
                )
            )

        # 4. Extract Research Dimensions
        dimensions = self._extract_research_dimensions(request, provenance)
        if dimensions:
            confidence["research_dimensions"] = min(0.88, self.MAX_DETERMINISTIC_CONFIDENCE)
        else:
            uncertainties.append(
                ExtractionUncertainty(
                    field_name="research_dimensions",
                    reason="No explicit evaluation dimensions (e.g. performance, security, cost) specified",
                    is_ambiguous=False,
                )
            )

        # 5. Extract Explicit Constraints
        constraints = self._extract_explicit_constraints(request, provenance)
        if constraints:
            confidence["explicit_constraints"] = min(0.95, self.MAX_DETERMINISTIC_CONFIDENCE)
        else:
            uncertainties.append(
                ExtractionUncertainty(
                    field_name="explicit_constraints",
                    reason="No explicit operational, architectural, or domain constraints specified",
                    is_ambiguous=False,
                )
            )

        # 6. Extract Temporal Scope
        temporal_scope = self._extract_temporal_scope(request, provenance)
        if temporal_scope:
            confidence["temporal_scope"] = min(0.90, self.MAX_DETERMINISTIC_CONFIDENCE)
        else:
            uncertainties.append(
                ExtractionUncertainty(
                    field_name="temporal_scope",
                    reason="No explicit temporal window, dates, or recency limits provided",
                    is_ambiguous=False,
                )
            )

        # 7. Extract Geographic Scope
        geographic_scope = self._extract_geographic_scope(request, provenance)
        if geographic_scope:
            confidence["geographic_scope"] = min(0.92, self.MAX_DETERMINISTIC_CONFIDENCE)
        else:
            uncertainties.append(
                ExtractionUncertainty(
                    field_name="geographic_scope",
                    reason="No explicit geographic, regional, or regulatory jurisdiction specified",
                    is_ambiguous=False,
                )
            )

        # 8. Extract Version Scope
        version_scope = self._extract_version_scope(request, provenance)
        if version_scope:
            confidence["version_scope"] = min(0.92, self.MAX_DETERMINISTIC_CONFIDENCE)
        else:
            uncertainties.append(
                ExtractionUncertainty(
                    field_name="version_scope",
                    reason="No explicit package, runtime, or framework versions specified",
                    is_ambiguous=False,
                )
            )

        # 9. Extract Freshness Requirement
        freshness_req = self._extract_freshness_requirement(request, provenance)
        if freshness_req:
            confidence["freshness_requirement"] = min(0.85, self.MAX_DETERMINISTIC_CONFIDENCE)
        else:
            uncertainties.append(
                ExtractionUncertainty(
                    field_name="freshness_requirement",
                    reason="No explicit freshness requirement stated; default STATIC may apply",
                    is_ambiguous=False,
                )
            )

        # 10. Extract Desired Output
        desired_output = self._extract_desired_output(request, provenance)
        if desired_output:
            confidence["desired_output"] = min(0.90, self.MAX_DETERMINISTIC_CONFIDENCE)
        else:
            uncertainties.append(
                ExtractionUncertainty(
                    field_name="desired_output",
                    reason="No explicit output format specified; default FACTUAL_ANSWER may apply",
                    is_ambiguous=False,
                )
            )

        # 11. Extract Evidence Requirements
        evidence_reqs = self._extract_evidence_requirements(request, provenance)
        if evidence_reqs:
            confidence["evidence_requirements"] = min(0.90, self.MAX_DETERMINISTIC_CONFIDENCE)
        else:
            uncertainties.append(
                ExtractionUncertainty(
                    field_name="evidence_requirements",
                    reason="No specific source types or evidence counts explicitly required",
                    is_ambiguous=False,
                )
            )

        return ExtractedRequestElements(
            source_request_id=request.request_id,
            subjects=subjects,
            entities=entities,
            comparison_targets=comp_targets,
            research_dimensions=dimensions,
            explicit_constraints=constraints,
            temporal_scope=temporal_scope,
            geographic_scope=geographic_scope,
            version_scope=version_scope,
            freshness_requirement=freshness_req,
            desired_output=desired_output,
            evidence_requirements=evidence_reqs,
            uncertainties=uncertainties,
            provenance=provenance,
            confidence=confidence,
        )

    # -------------------------------------------------------------------------
    # 1. Subjects Extraction
    # -------------------------------------------------------------------------

    def _extract_subjects(self, request: ResearchRequest, provenance: list[ExtractionProvenance]) -> list[str]:
        subjects: list[str] = []

        # From metadata
        if isinstance(request.metadata, dict):
            meta_subs = request.metadata.get("subjects") or request.metadata.get("subject")
            if meta_subs:
                if isinstance(meta_subs, list):
                    for s in meta_subs:
                        s_str = str(s).strip()
                        if s_str and s_str not in subjects:
                            subjects.append(s_str)
                            provenance.append(
                                ExtractionProvenance(
                                    element_type="subject",
                                    value=s_str,
                                    source_field="metadata.subjects",
                                    matched_text=s_str,
                                    rule_name="metadata_subject_rule",
                                    confidence=0.95,
                                )
                            )
                elif isinstance(meta_subs, str):
                    s_str = meta_subs.strip()
                    if s_str and s_str not in subjects:
                        subjects.append(s_str)
                        provenance.append(
                            ExtractionProvenance(
                                element_type="subject",
                                value=s_str,
                                source_field="metadata.subject",
                                matched_text=s_str,
                                rule_name="metadata_subject_rule",
                                confidence=0.95,
                            )
                        )

        # From objective / questions text patterns
        candidate_texts = [(request.objective, "objective")]
        for idx, q in enumerate(request.questions):
            candidate_texts.append((q, f"questions[{idx}]"))

        patterns = [
            (
                r"\bhow\s+(?:do\s+(?:i|we)|can\s+(?:i|we)|to)\s+(?:implement|build|create|setup|configure|write|wire|integrate|deploy|migrate)\s+([A-Za-z0-9_.\s-]{3,50}?)(?:\s+(?:in|using|with)\s+[A-Za-z0-9_.\s-]+|[?,.;]|$)",
                "action_subject_pattern",
            ),
            (
                r"\b(?:overview|summary|background|architecture|design)\s+of\s+([A-Za-z0-9_.\s-]{3,50}?)(?:[?,.;]|$)",
                "overview_subject_pattern",
            ),
            (
                r"\b(?:on|regarding|about)\s+([A-Za-z0-9_.\s-]{3,50}?)(?:[?,.;]|$)",
                "topic_preposition_subject_pattern",
            ),
        ]

        for text, source_field in candidate_texts:
            for pattern, rule_name in patterns:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    extracted = m.group(1).strip()
                    # Filter out stopwords or overly short phrases
                    if len(extracted) >= 3 and extracted.lower() not in {"the", "this", "that", "it", "them"}:
                        if extracted not in subjects:
                            subjects.append(extracted)
                            provenance.append(
                                ExtractionProvenance(
                                    element_type="subject",
                                    value=extracted,
                                    source_field=source_field,
                                    matched_text=m.group(0),
                                    rule_name=rule_name,
                                    confidence=0.85,
                                )
                            )

        return subjects

    # -------------------------------------------------------------------------
    # 2. Entities Extraction
    # -------------------------------------------------------------------------

    def _extract_entities(self, request: ResearchRequest, provenance: list[ExtractionProvenance]) -> list[str]:
        entities: list[str] = []

        # From metadata
        if isinstance(request.metadata, dict):
            meta_ents = request.metadata.get("entities") or request.metadata.get("target_entities")
            if meta_ents and isinstance(meta_ents, list):
                for e in meta_ents:
                    e_str = str(e).strip()
                    if e_str and e_str not in entities:
                        entities.append(e_str)
                        provenance.append(
                            ExtractionProvenance(
                                element_type="entity",
                                value=e_str,
                                source_field="metadata.entities",
                                matched_text=e_str,
                                rule_name="metadata_entity_rule",
                                confidence=0.95,
                            )
                        )

        candidate_texts = [(request.objective, "objective")]
        for idx, q in enumerate(request.questions):
            candidate_texts.append((q, f"questions[{idx}]"))
        for idx, c in enumerate(request.constraints):
            candidate_texts.append((c, f"constraints[{idx}]"))

        # Rule A: Formal standards (RFC, PEP, ISO, IEEE, ECMA)
        standard_pattern = r"\b(RFC\s*\d+|PEP\s*\d+|ISO\s*\d+|IEEE\s*\d+|ECMA\s*-\s*\d+)\b"

        # Rule B: Explicitly quoted or backticked terms
        quoted_pattern = r'["`\']([A-Za-z0-9_.-]{2,35})["`\']'

        # Rule C: PascalCase / CamelCase software identifiers
        pascal_pattern = r"\b([A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*))\b"

        for text, source_field in candidate_texts:
            # Check standards
            for m in re.finditer(standard_pattern, text, re.IGNORECASE):
                std_token = re.sub(r"\s+", " ", m.group(1)).strip().upper()
                if std_token not in entities:
                    entities.append(std_token)
                    provenance.append(
                        ExtractionProvenance(
                            element_type="entity",
                            value=std_token,
                            source_field=source_field,
                            matched_text=m.group(0),
                            rule_name="technical_standard_entity_rule",
                            confidence=0.95,
                        )
                    )

            # Check quotes
            for m in re.finditer(quoted_pattern, text):
                q_token = m.group(1).strip()
                if q_token.lower() not in {"true", "false", "none", "null", "the", "a", "an"}:
                    if q_token not in entities:
                        entities.append(q_token)
                        provenance.append(
                            ExtractionProvenance(
                                element_type="entity",
                                value=q_token,
                                source_field=source_field,
                                matched_text=m.group(0),
                                rule_name="quoted_identifier_entity_rule",
                                confidence=0.90,
                            )
                        )

            # Check PascalCase
            for m in re.finditer(pascal_pattern, text):
                p_token = m.group(1).strip()
                if p_token.lower() not in {"which", "where", "what", "when", "there", "their", "because", "should", "would", "could"}:
                    if p_token not in entities:
                        entities.append(p_token)
                        provenance.append(
                            ExtractionProvenance(
                                element_type="entity",
                                value=p_token,
                                source_field=source_field,
                                matched_text=m.group(0),
                                rule_name="pascal_case_entity_rule",
                                confidence=0.85,
                            )
                        )

            # Check Known Entities
            lower_text = text.lower()
            for ent in self.COMMON_ENTITIES:
                if re.search(rf"\b{re.escape(ent)}\b", lower_text):
                    # Canonicalize representation
                    if ent == "oauth2":
                        canonical = "OAuth2"
                    elif ent in {"jwt", "grpc", "rest", "aws", "gcp", "k8s"}:
                        canonical = ent.upper()
                    elif ent == "postgresql":
                        canonical = "PostgreSQL"
                    elif ent == "fastapi":
                        canonical = "FastAPI"
                    elif ent == "rabbitmq":
                        canonical = "RabbitMQ"
                    elif ent == "mongodb":
                        canonical = "MongoDB"
                    elif ent == "next.js":
                        canonical = "Next.js"
                    elif ent == "memcached":
                        canonical = "Memcached"
                    else:
                        canonical = ent.capitalize()

                    if canonical not in entities and ent not in [e.lower() for e in entities]:
                        entities.append(canonical)
                        provenance.append(
                            ExtractionProvenance(
                                element_type="entity",
                                value=canonical,
                                source_field=source_field,
                                matched_text=ent,
                                rule_name="known_software_entity_rule",
                                confidence=0.88,
                            )
                        )

        return entities

    # -------------------------------------------------------------------------
    # 3. Comparison Targets Extraction
    # -------------------------------------------------------------------------

    def _extract_comparison_targets(
        self, request: ResearchRequest, provenance: list[ExtractionProvenance]
    ) -> tuple[list[str], bool]:
        """
        Extract explicit comparison targets.
        Returns (targets, is_ambiguous).
        """
        targets: list[str] = []

        # From metadata
        if isinstance(request.metadata, dict):
            meta_comp = request.metadata.get("comparison_targets")
            if meta_comp and isinstance(meta_comp, list):
                for t in meta_comp:
                    t_str = str(t).strip()
                    if t_str and t_str not in targets:
                        targets.append(t_str)
                        provenance.append(
                            ExtractionProvenance(
                                element_type="comparison_target",
                                value=t_str,
                                source_field="metadata.comparison_targets",
                                matched_text=t_str,
                                rule_name="metadata_comparison_target_rule",
                                confidence=0.95,
                            )
                        )

        candidate_texts = [(request.objective, "objective")]
        for idx, q in enumerate(request.questions):
            candidate_texts.append((q, f"questions[{idx}]"))

        patterns = [
            # Pattern 1: X vs Y or X versus Y
            (
                r"\b([A-Za-z0-9_.-]+)\s+(?:vs\.?|versus)\s+([A-Za-z0-9_.-]+)\b",
                "versus_comparison_rule",
            ),
            # Pattern 2: compare X with/to/and Y
            (
                r"\bcompare\s+([A-Za-z0-9_.-]+)\s+(?:with|to|and)\s+([A-Za-z0-9_.-]+)\b",
                "compare_with_comparison_rule",
            ),
            # Pattern 3: differences/tradeoffs between X and Y
            (
                r"\b(?:differences?|tradeoffs?|pros\s+and\s+cons|distinction)\s+between\s+([A-Za-z0-9_.-]+)\s+and\s+([A-Za-z0-9_.-]+)\b",
                "between_and_comparison_rule",
            ),
            # Pattern 4: choose/between/pick: X or Y
            (
                r"\b(?:choose|pick|prefer|between|options?)\s*:?\s+([A-Za-z0-9_.-]+)\s+or\s+([A-Za-z0-9_.-]+)\b",
                "choice_or_comparison_rule",
            ),
        ]

        has_comparative_framing = False
        for text, source_field in candidate_texts:
            lower = text.lower()
            if any(w in lower for w in ["compare", "comparative", "versus", "vs", "differences between", "tradeoffs between"]):
                has_comparative_framing = True

            for pattern, rule_name in patterns:
                for m in re.finditer(pattern, text, re.IGNORECASE):
                    t1, t2 = m.group(1).strip(), m.group(2).strip()
                    for t in (t1, t2):
                        if len(t) >= 2 and t.lower() not in {"the", "a", "an", "this", "that", "and", "or"} and t.lower() not in self.GENERIC_COMPARISON_PLACEHOLDERS:
                            if t not in targets:
                                targets.append(t)
                                provenance.append(
                                    ExtractionProvenance(
                                        element_type="comparison_target",
                                        value=t,
                                        source_field=source_field,
                                        matched_text=m.group(0),
                                        rule_name=rule_name,
                                        confidence=0.90,
                                    )
                                )

        # Check ambiguity: comparative framing exists, but fewer than 2 targets were found
        is_ambiguous = has_comparative_framing and len(targets) < 2
        return targets, is_ambiguous

    # -------------------------------------------------------------------------
    # 4. Research Dimensions Extraction
    # -------------------------------------------------------------------------

    def _extract_research_dimensions(
        self, request: ResearchRequest, provenance: list[ExtractionProvenance]
    ) -> list[str]:
        dimensions: list[str] = []

        # From metadata
        if isinstance(request.metadata, dict):
            meta_dims = request.metadata.get("research_dimensions") or request.metadata.get("dimensions")
            if meta_dims and isinstance(meta_dims, list):
                for d in meta_dims:
                    d_str = str(d).strip().lower()
                    if d_str and d_str not in dimensions:
                        dimensions.append(d_str)
                        provenance.append(
                            ExtractionProvenance(
                                element_type="research_dimension",
                                value=d_str,
                                source_field="metadata.research_dimensions",
                                matched_text=d_str,
                                rule_name="metadata_dimension_rule",
                                confidence=0.95,
                            )
                        )

        candidate_texts = [(request.objective, "objective")]
        for idx, q in enumerate(request.questions):
            candidate_texts.append((q, f"questions[{idx}]"))
        for idx, c in enumerate(request.constraints):
            candidate_texts.append((c, f"constraints[{idx}]"))

        # Explicit list pattern: dimensions: X, Y, Z
        list_pattern = r"\b(?:dimensions?|axes|evaluate\s+(?:on|along|for))\s*:\s*([^\n.;]+)"
        for text, source_field in candidate_texts:
            m = re.search(list_pattern, text, re.IGNORECASE)
            if m:
                raw_list = m.group(1)
                items = re.split(r"[,/&]|(?:\s+and\s+)", raw_list)
                for item in items:
                    item_clean = item.strip().lower()
                    if len(item_clean) >= 3 and item_clean not in dimensions:
                        dimensions.append(item_clean)
                        provenance.append(
                            ExtractionProvenance(
                                element_type="research_dimension",
                                value=item_clean,
                                source_field=source_field,
                                matched_text=item.strip(),
                                rule_name="explicit_dimension_list_rule",
                                confidence=0.92,
                            )
                        )

            # Keyword-based dimension matching
            lower = text.lower()
            for dim, keywords in self.DIMENSION_KEYWORDS.items():
                for kw in keywords:
                    if re.search(rf"\b{re.escape(kw)}\b", lower):
                        if dim not in dimensions:
                            dimensions.append(dim)
                            provenance.append(
                                ExtractionProvenance(
                                    element_type="research_dimension",
                                    value=dim,
                                    source_field=source_field,
                                    matched_text=kw,
                                    rule_name="keyword_dimension_rule",
                                    confidence=0.85,
                                )
                            )
                        break

        return dimensions

    # -------------------------------------------------------------------------
    # 5. Explicit Constraints Extraction
    # -------------------------------------------------------------------------

    def _extract_explicit_constraints(
        self, request: ResearchRequest, provenance: list[ExtractionProvenance]
    ) -> list[str]:
        constraints: list[str] = []

        # 1. Direct request constraints
        for idx, c in enumerate(request.constraints):
            c_str = str(c).strip()
            if c_str and c_str not in constraints:
                constraints.append(c_str)
                provenance.append(
                    ExtractionProvenance(
                        element_type="explicit_constraint",
                        value=c_str,
                        source_field=f"constraints[{idx}]",
                        matched_text=c_str,
                        rule_name="request_constraint_list_rule",
                        confidence=0.95,
                    )
                )

        # 2. Scope constraints
        if request.scope.allowed_domains:
            entry = f"allowed_domains: {', '.join(request.scope.allowed_domains)}"
            if entry not in constraints:
                constraints.append(entry)
                provenance.append(
                    ExtractionProvenance(
                        element_type="explicit_constraint",
                        value=entry,
                        source_field="scope.allowed_domains",
                        matched_text=str(request.scope.allowed_domains),
                        rule_name="scope_allowed_domains_rule",
                        confidence=0.95,
                    )
                )

        if request.scope.excluded_domains:
            entry = f"excluded_domains: {', '.join(request.scope.excluded_domains)}"
            if entry not in constraints:
                constraints.append(entry)
                provenance.append(
                    ExtractionProvenance(
                        element_type="explicit_constraint",
                        value=entry,
                        source_field="scope.excluded_domains",
                        matched_text=str(request.scope.excluded_domains),
                        rule_name="scope_excluded_domains_rule",
                        confidence=0.95,
                    )
                )

        if request.scope.timeout_seconds != 300:
            entry = f"timeout_limit: {request.scope.timeout_seconds}s"
            if entry not in constraints:
                constraints.append(entry)
                provenance.append(
                    ExtractionProvenance(
                        element_type="explicit_constraint",
                        value=entry,
                        source_field="scope.timeout_seconds",
                        matched_text=str(request.scope.timeout_seconds),
                        rule_name="scope_timeout_rule",
                        confidence=0.95,
                    )
                )

        # 3. Imperative text directives in objective and questions
        candidate_texts = [(request.objective, "objective")]
        for idx, q in enumerate(request.questions):
            candidate_texts.append((q, f"questions[{idx}]"))

        imperative_patterns = [
            (
                r"\b((?:must\s+(?:not\s+)?|cannot\s+|restricted\s+to\s+|only\s+use\s+|under\s+\$|do\s+not\s+)[^\n;]+?)(?:\.\s|\.$|[;]|$)",
                "imperative_directive_rule",
            ),
        ]

        for text, source_field in candidate_texts:
            for pattern, rule_name in imperative_patterns:
                for m in re.finditer(pattern, text, re.IGNORECASE):
                    matched_clause = m.group(1).strip()
                    if matched_clause and matched_clause not in constraints:
                        constraints.append(matched_clause)
                        provenance.append(
                            ExtractionProvenance(
                                element_type="explicit_constraint",
                                value=matched_clause,
                                source_field=source_field,
                                matched_text=matched_clause,
                                rule_name=rule_name,
                                confidence=0.88,
                            )
                        )

        return constraints

    # -------------------------------------------------------------------------
    # 6. Temporal Scope Extraction
    # -------------------------------------------------------------------------

    def _extract_temporal_scope(
        self, request: ResearchRequest, provenance: list[ExtractionProvenance]
    ) -> Optional[TemporalScope]:
        # Check metadata
        if isinstance(request.metadata, dict):
            meta_temp = request.metadata.get("temporal_scope")
            if meta_temp and isinstance(meta_temp, dict):
                ts = TemporalScope.from_dict(meta_temp)
                provenance.append(
                    ExtractionProvenance(
                        element_type="temporal_scope",
                        value=ts.to_dict(),
                        source_field="metadata.temporal_scope",
                        matched_text=str(meta_temp),
                        rule_name="metadata_temporal_scope_rule",
                        confidence=0.95,
                    )
                )
                return ts

        # Check scope.recency_days
        if request.scope.recency_days is not None:
            ts = TemporalScope(
                recency_days=request.scope.recency_days,
                description=f"Evidence restricted to past {request.scope.recency_days} days",
            )
            provenance.append(
                ExtractionProvenance(
                    element_type="temporal_scope",
                    value=ts.to_dict(),
                    source_field="scope.recency_days",
                    matched_text=str(request.scope.recency_days),
                    rule_name="scope_recency_days_rule",
                    confidence=0.95,
                )
            )
            return ts

        # Check text expressions
        candidate_texts = [(request.objective, "objective")]
        for idx, q in enumerate(request.questions):
            candidate_texts.append((q, f"questions[{idx}]"))
        for idx, c in enumerate(request.constraints):
            candidate_texts.append((c, f"constraints[{idx}]"))

        for text, source_field in candidate_texts:
            # ISO date range: YYYY-MM-DD to YYYY-MM-DD
            m_iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\s+(?:to|until|-|through)\s+(\d{4}-\d{2}-\d{2})\b", text)
            if m_iso:
                ts = TemporalScope(
                    start_date=m_iso.group(1),
                    end_date=m_iso.group(2),
                    description=f"Date range from {m_iso.group(1)} to {m_iso.group(2)}",
                )
                provenance.append(
                    ExtractionProvenance(
                        element_type="temporal_scope",
                        value=ts.to_dict(),
                        source_field=source_field,
                        matched_text=m_iso.group(0),
                        rule_name="iso_date_range_rule",
                        confidence=0.92,
                    )
                )
                return ts

            # Relative days/months/years: past 30 days, last 6 months
            m_rel = re.search(r"\b(?:past|last)\s+(\d+)\s+(days?|weeks?|months?|years?)\b", text, re.IGNORECASE)
            if m_rel:
                count = int(m_rel.group(1))
                unit = m_rel.group(2).lower()
                days = count
                if "week" in unit:
                    days = count * 7
                elif "month" in unit:
                    days = count * 30
                elif "year" in unit:
                    days = count * 365

                ts = TemporalScope(
                    recency_days=days,
                    description=f"Within past {count} {unit}",
                )
                provenance.append(
                    ExtractionProvenance(
                        element_type="temporal_scope",
                        value=ts.to_dict(),
                        source_field=source_field,
                        matched_text=m_rel.group(0),
                        rule_name="relative_temporal_window_rule",
                        confidence=0.88,
                    )
                )
                return ts

            # Year range: 2022 to 2024
            m_yr = re.search(r"\b(20\d{2})\s*(?:-|to|until)\s*(20\d{2})\b", text)
            if m_yr:
                ts = TemporalScope(
                    start_date=f"{m_yr.group(1)}-01-01",
                    end_date=f"{m_yr.group(2)}-12-31",
                    description=f"Years {m_yr.group(1)} through {m_yr.group(2)}",
                )
                provenance.append(
                    ExtractionProvenance(
                        element_type="temporal_scope",
                        value=ts.to_dict(),
                        source_field=source_field,
                        matched_text=m_yr.group(0),
                        rule_name="year_range_rule",
                        confidence=0.90,
                    )
                )
                return ts

        return None

    # -------------------------------------------------------------------------
    # 7. Geographic Scope Extraction
    # -------------------------------------------------------------------------

    def _extract_geographic_scope(
        self, request: ResearchRequest, provenance: list[ExtractionProvenance]
    ) -> Optional[str]:
        # Check metadata
        if isinstance(request.metadata, dict):
            meta_geo = request.metadata.get("geographic_scope")
            if meta_geo:
                geo_str = str(meta_geo).strip()
                provenance.append(
                    ExtractionProvenance(
                        element_type="geographic_scope",
                        value=geo_str,
                        source_field="metadata.geographic_scope",
                        matched_text=geo_str,
                        rule_name="metadata_geographic_scope_rule",
                        confidence=0.95,
                    )
                )
                return geo_str

        candidate_texts = [(request.objective, "objective")]
        for idx, q in enumerate(request.questions):
            candidate_texts.append((q, f"questions[{idx}]"))
        for idx, c in enumerate(request.constraints):
            candidate_texts.append((c, f"constraints[{idx}]"))

        geo_mappings = [
            (r"\b(?:GDPR)\b", "EU (GDPR)"),
            (r"\b(?:CCPA|CPRA)\b", "US-CA (CCPA)"),
            (r"\b(?:HIPAA)\b", "US (HIPAA)"),
            (r"\b(?:EU|European\s+Union|EEA)\b", "EU"),
            (r"\b(?:US|USA|United\s+States)\b", "US"),
            (r"\b(?:UK|United\s+Kingdom)\b", "UK"),
            (r"\b(?:APAC|Asia-Pacific)\b", "APAC"),
            (r"\b(?:global|worldwide)\b", "GLOBAL"),
        ]

        for text, source_field in candidate_texts:
            for pattern, canonical in geo_mappings:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    provenance.append(
                        ExtractionProvenance(
                            element_type="geographic_scope",
                            value=canonical,
                            source_field=source_field,
                            matched_text=m.group(0),
                            rule_name="geographic_token_rule",
                            confidence=0.90,
                        )
                    )
                    return canonical

        return None

    # -------------------------------------------------------------------------
    # 8. Version Scope Extraction
    # -------------------------------------------------------------------------

    def _extract_version_scope(
        self, request: ResearchRequest, provenance: list[ExtractionProvenance]
    ) -> Optional[VersionScope]:
        # Check metadata
        if isinstance(request.metadata, dict):
            meta_ver = request.metadata.get("version_scope")
            if meta_ver and isinstance(meta_ver, dict):
                vs = VersionScope.from_dict(meta_ver)
                provenance.append(
                    ExtractionProvenance(
                        element_type="version_scope",
                        value=vs.to_dict(),
                        source_field="metadata.version_scope",
                        matched_text=str(meta_ver),
                        rule_name="metadata_version_scope_rule",
                        confidence=0.95,
                    )
                )
                return vs

        candidate_texts = [(request.objective, "objective")]
        for idx, q in enumerate(request.questions):
            candidate_texts.append((q, f"questions[{idx}]"))
        for idx, c in enumerate(request.constraints):
            candidate_texts.append((c, f"constraints[{idx}]"))

        # Version regex: e.g. "Python 3.12+", "Python >= 3.11", "v2.4.0", "PostgreSQL 16"
        version_pattern = r"\b([A-Za-z0-9_.-]+)\s+(?:version\s+)?([><=~^]{1,2}\s*\d+(?:\.\d+)*(?:\.\d+)?|\d+(?:\.\d+)*(?:\+)?)(?=[.,;:\s]|$)"

        for text, source_field in candidate_texts:
            m = re.search(version_pattern, text)
            if m:
                eco_candidate = m.group(1).strip()
                ver_candidate = m.group(2).strip()
                # Ignore non-tech words matching pattern accidentally
                if eco_candidate.lower() not in {"step", "part", "phase", "section", "chapter", "item", "rule"}:
                    specifier = ver_candidate if any(op in ver_candidate for op in [">", "<", "=", "~", "^", "+"]) else f"== {ver_candidate}"
                    vs = VersionScope(
                        target_version=ver_candidate.lstrip("v"),
                        version_specifier=specifier,
                        ecosystem=eco_candidate,
                        description=f"{eco_candidate} version {ver_candidate}",
                    )
                    provenance.append(
                        ExtractionProvenance(
                            element_type="version_scope",
                            value=vs.to_dict(),
                            source_field=source_field,
                            matched_text=m.group(0),
                            rule_name="ecosystem_version_pattern_rule",
                            confidence=0.90,
                        )
                    )
                    return vs

            # Standalone semver: v1.2.3 or 1.2.3
            m_v = re.search(r"\bv?(\d+\.\d+\.\d+)\b", text)
            if m_v:
                ver_str = m_v.group(1)
                vs = VersionScope(
                    target_version=ver_str,
                    version_specifier=f"== {ver_str}",
                    description=f"Version {ver_str}",
                )
                provenance.append(
                    ExtractionProvenance(
                        element_type="version_scope",
                        value=vs.to_dict(),
                        source_field=source_field,
                        matched_text=m_v.group(0),
                        rule_name="standalone_semver_rule",
                        confidence=0.85,
                    )
                )
                return vs

        return None

    # -------------------------------------------------------------------------
    # 9. Freshness Requirement Extraction
    # -------------------------------------------------------------------------

    def _extract_freshness_requirement(
        self, request: ResearchRequest, provenance: list[ExtractionProvenance]
    ) -> Optional[FreshnessRequirement]:
        # Check metadata
        if isinstance(request.metadata, dict):
            meta_fresh = request.metadata.get("freshness_requirement")
            if meta_fresh:
                try:
                    freq = FreshnessRequirement(meta_fresh)
                    provenance.append(
                        ExtractionProvenance(
                            element_type="freshness_requirement",
                            value=freq.value,
                            source_field="metadata.freshness_requirement",
                            matched_text=str(meta_fresh),
                            rule_name="metadata_freshness_rule",
                            confidence=0.95,
                        )
                    )
                    return freq
                except ValueError:
                    pass

        # Check scope.recency_days
        if request.scope.recency_days is not None:
            rd = request.scope.recency_days
            if rd <= 7:
                freq = FreshnessRequirement.CURRENT
            elif rd <= 30:
                freq = FreshnessRequirement.RECENT
            else:
                freq = FreshnessRequirement.STATIC

            provenance.append(
                ExtractionProvenance(
                    element_type="freshness_requirement",
                    value=freq.value,
                    source_field="scope.recency_days",
                    matched_text=str(rd),
                    rule_name="scope_recency_to_freshness_rule",
                    confidence=0.90,
                )
            )
            return freq

        # Check text cues
        candidate_texts = [(request.objective, "objective")]
        for idx, q in enumerate(request.questions):
            candidate_texts.append((q, f"questions[{idx}]"))

        for text, source_field in candidate_texts:
            lower = text.lower()
            if re.search(r"\b(?:real-time|live|breaking|today's|latest\s+news)\b", lower):
                freq = FreshnessRequirement.CURRENT
                provenance.append(
                    ExtractionProvenance(
                        element_type="freshness_requirement",
                        value=freq.value,
                        source_field=source_field,
                        matched_text="real-time cue",
                        rule_name="text_realtime_freshness_rule",
                        confidence=0.88,
                    )
                )
                return freq
            elif re.search(r"\b(?:recent|latest\s+version|up-to-date|current\s+state|latest\s+updates?)\b", lower):
                freq = FreshnessRequirement.RECENT
                provenance.append(
                    ExtractionProvenance(
                        element_type="freshness_requirement",
                        value=freq.value,
                        source_field=source_field,
                        matched_text="recent/latest cue",
                        rule_name="text_recent_freshness_rule",
                        confidence=0.85,
                    )
                )
                return freq
            elif re.search(r"\b(?:historical|evolution|timeline|history\s+of|over\s+time|since\s+inception)\b", lower):
                freq = FreshnessRequirement.TIME_RANGE
                provenance.append(
                    ExtractionProvenance(
                        element_type="freshness_requirement",
                        value=freq.value,
                        source_field=source_field,
                        matched_text="historical cue",
                        rule_name="text_historical_freshness_rule",
                        confidence=0.88,
                    )
                )
                return freq

        return None

    # -------------------------------------------------------------------------
    # 10. Desired Output Extraction
    # -------------------------------------------------------------------------

    def _extract_desired_output(
        self, request: ResearchRequest, provenance: list[ExtractionProvenance]
    ) -> Optional[DesiredOutput]:
        # Check metadata
        if isinstance(request.metadata, dict):
            meta_out = request.metadata.get("desired_output")
            if meta_out:
                try:
                    do = DesiredOutput(meta_out)
                    provenance.append(
                        ExtractionProvenance(
                            element_type="desired_output",
                            value=do.value,
                            source_field="metadata.desired_output",
                            matched_text=str(meta_out),
                            rule_name="metadata_desired_output_rule",
                            confidence=0.95,
                        )
                    )
                    return do
                except ValueError:
                    pass

        # Check required_output_format
        fmt = request.required_output_format.lower()
        if "matrix" in fmt or "comparison" in fmt:
            do = DesiredOutput.COMPARISON
            provenance.append(
                ExtractionProvenance(
                    element_type="desired_output",
                    value=do.value,
                    source_field="required_output_format",
                    matched_text=request.required_output_format,
                    rule_name="output_format_matrix_rule",
                    confidence=0.92,
                )
            )
            return do
        elif "guide" in fmt or "implementation" in fmt:
            do = DesiredOutput.IMPLEMENTATION_GUIDANCE
            provenance.append(
                ExtractionProvenance(
                    element_type="desired_output",
                    value=do.value,
                    source_field="required_output_format",
                    matched_text=request.required_output_format,
                    rule_name="output_format_guide_rule",
                    confidence=0.92,
                )
            )
            return do
        elif "decision" in fmt or "brief" in fmt:
            do = DesiredOutput.DECISION_BRIEF
            provenance.append(
                ExtractionProvenance(
                    element_type="desired_output",
                    value=do.value,
                    source_field="required_output_format",
                    matched_text=request.required_output_format,
                    rule_name="output_format_brief_rule",
                    confidence=0.92,
                )
            )
            return do
        elif "snippet" in fmt or "code" in fmt:
            do = DesiredOutput.IMPLEMENTATION_GUIDANCE
            provenance.append(
                ExtractionProvenance(
                    element_type="desired_output",
                    value=do.value,
                    source_field="required_output_format",
                    matched_text=request.required_output_format,
                    rule_name="output_format_snippet_rule",
                    confidence=0.92,
                )
            )
            return do
        elif "executive" in fmt or "summary" in fmt:
            do = DesiredOutput.SUMMARY
            provenance.append(
                ExtractionProvenance(
                    element_type="desired_output",
                    value=do.value,
                    source_field="required_output_format",
                    matched_text=request.required_output_format,
                    rule_name="output_format_summary_rule",
                    confidence=0.92,
                )
            )
            return do

        # Check text directives
        candidate_texts = [(request.objective, "objective")]
        for idx, q in enumerate(request.questions):
            candidate_texts.append((q, f"questions[{idx}]"))

        for text, source_field in candidate_texts:
            lower = text.lower()
            if re.search(r"\b(?:comparison\s+matrix|side-by-side\s+table)\b", lower):
                do = DesiredOutput.COMPARISON
                provenance.append(
                    ExtractionProvenance(
                        element_type="desired_output",
                        value=do.value,
                        source_field=source_field,
                        matched_text="comparison matrix",
                        rule_name="text_comparison_matrix_rule",
                        confidence=0.90,
                    )
                )
                return do
            elif re.search(r"\b(?:step-by-step\s+guide|implementation\s+guide|walkthrough)\b", lower):
                do = DesiredOutput.IMPLEMENTATION_GUIDANCE
                provenance.append(
                    ExtractionProvenance(
                        element_type="desired_output",
                        value=do.value,
                        source_field=source_field,
                        matched_text="implementation guide",
                        rule_name="text_implementation_guide_rule",
                        confidence=0.90,
                    )
                )
                return do
            elif re.search(r"\b(?:decision\s+brief|recommendation\s+brief)\b", lower):
                do = DesiredOutput.DECISION_BRIEF
                provenance.append(
                    ExtractionProvenance(
                        element_type="desired_output",
                        value=do.value,
                        source_field=source_field,
                        matched_text="decision brief",
                        rule_name="text_decision_brief_rule",
                        confidence=0.90,
                    )
                )
                return do
            elif re.search(r"\b(?:executive\s+summary)\b", lower):
                do = DesiredOutput.SUMMARY
                provenance.append(
                    ExtractionProvenance(
                        element_type="desired_output",
                        value=do.value,
                        source_field=source_field,
                        matched_text="executive summary",
                        rule_name="text_executive_summary_rule",
                        confidence=0.90,
                    )
                )
                return do

        return None

    # -------------------------------------------------------------------------
    # 11. Evidence Requirements Extraction
    # -------------------------------------------------------------------------

    def _extract_evidence_requirements(
        self, request: ResearchRequest, provenance: list[ExtractionProvenance]
    ) -> list[EvidenceRequirement]:
        requirements: list[EvidenceRequirement] = []

        # From scope.preferred_source_types
        if request.scope.preferred_source_types:
            req = EvidenceRequirement(
                description="Explicit preferred source types specified in request scope",
                source_types=list(request.scope.preferred_source_types),
                min_independent_sources=request.scope.min_evidence_per_question,
                mandatory=True,
            )
            requirements.append(req)
            provenance.append(
                ExtractionProvenance(
                    element_type="evidence_requirement",
                    value=req.to_dict(),
                    source_field="scope.preferred_source_types",
                    matched_text=str([s.value for s in request.scope.preferred_source_types]),
                    rule_name="scope_preferred_source_types_rule",
                    confidence=0.95,
                )
            )

        # From text directives (e.g. "official documentation only", "academic papers")
        candidate_texts = [(request.objective, "objective")]
        for idx, q in enumerate(request.questions):
            candidate_texts.append((q, f"questions[{idx}]"))
        for idx, c in enumerate(request.constraints):
            candidate_texts.append((c, f"constraints[{idx}]"))

        for text, source_field in candidate_texts:
            lower = text.lower()
            if re.search(r"\b(?:official\s+docs?|official\s+documentation)\b", lower):
                if not any(SourceType.OFFICIAL_DOCUMENTATION in r.source_types for r in requirements):
                    req = EvidenceRequirement(
                        description="Must consult official documentation",
                        source_types=[SourceType.OFFICIAL_DOCUMENTATION],
                        mandatory=True,
                    )
                    requirements.append(req)
                    provenance.append(
                        ExtractionProvenance(
                            element_type="evidence_requirement",
                            value=req.to_dict(),
                            source_field=source_field,
                            matched_text="official documentation",
                            rule_name="official_docs_evidence_rule",
                            confidence=0.90,
                        )
                    )

            if re.search(r"\b(?:academic\s+papers?|peer-reviewed)\b", lower):
                if not any(SourceType.ACADEMIC in r.source_types for r in requirements):
                    req = EvidenceRequirement(
                        description="Must consult peer-reviewed academic literature",
                        source_types=[SourceType.ACADEMIC],
                        mandatory=True,
                    )
                    requirements.append(req)
                    provenance.append(
                        ExtractionProvenance(
                            element_type="evidence_requirement",
                            value=req.to_dict(),
                            source_field=source_field,
                            matched_text="academic papers",
                            rule_name="academic_evidence_rule",
                            confidence=0.90,
                        )
                    )

        return requirements

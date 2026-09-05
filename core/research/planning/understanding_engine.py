from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Optional
import uuid

from core.errors import AutonomOSError
from core.inference.gateway import InferenceGateway
from core.inference.model import (
    InferenceMessage,
    InferenceRequest,
    InferenceResponse,
    ModelMetadata,
    ModelRequirement,
)
from core.inference.provider import BaseInferenceProvider
from core.inference.types import ModelCapability, RoutingProfile
from core.research.contracts.intent import (
    Ambiguity,
    ClarificationQuestion,
    EvidenceRequirement,
    IntentConfidence,
    ResearchIntent,
    TemporalScope,
    VersionScope,
)
from core.research.contracts.request import ResearchRequest
from core.research.planning.extractor import ExtractedRequestElements
from core.research.planning.intent_classifier import IntentClassificationResult
from core.research.types import (
    DesiredOutput,
    FreshnessRequirement,
    IntentType,
    SourceType,
)

logger = logging.getLogger("AutonomOS.Research.LLMUnderstandingEngine")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class UnderstandingError(AutonomOSError):
    """Raised when request understanding fails or receives unrecoverable provider errors."""

    def __init__(self, message: str, code: str = "UNDERSTANDING_ERROR", details: Optional[dict[str, Any]] = None):
        super().__init__(message, code=code, details=details)


# =============================================================================
# 1. Structured Understanding Proposal (Untrusted Model)
# =============================================================================

@dataclass
class ResearchIntentProposal:
    """
    Structured understanding proposal returned by the LLM.
    Acts as an untrusted analytical suggestion.
    Must undergo deterministic validation before conversion to authoritative ResearchIntent.
    """
    proposal_id: str = field(default_factory=lambda: f"prop-{uuid.uuid4().hex[:8]}")
    source_request_id: str = ""
    objective: str = ""
    intent_types: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    comparison_targets: list[str] = field(default_factory=list)
    research_dimensions: list[str] = field(default_factory=list)
    explicit_constraints: list[str] = field(default_factory=list)
    inferred_constraints: list[str] = field(default_factory=list)
    freshness_requirement: Optional[str] = None
    temporal_scope: Optional[dict[str, Any]] = None
    geographic_scope: Optional[str] = None
    version_scope: Optional[dict[str, Any]] = None
    desired_output: Optional[str] = None
    evidence_requirements: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    ambiguities: list[dict[str, Any]] = field(default_factory=list)
    clarification_required: bool = False
    clarification_questions: list[dict[str, Any]] = field(default_factory=list)
    confidence: dict[str, float] = field(default_factory=dict)
    model_used: str = ""
    provider_used: str = ""
    raw_response: str = ""
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "source_request_id": self.source_request_id,
            "objective": self.objective,
            "intent_types": list(self.intent_types),
            "subjects": list(self.subjects),
            "entities": list(self.entities),
            "comparison_targets": list(self.comparison_targets),
            "research_dimensions": list(self.research_dimensions),
            "explicit_constraints": list(self.explicit_constraints),
            "inferred_constraints": list(self.inferred_constraints),
            "freshness_requirement": self.freshness_requirement,
            "temporal_scope": self.temporal_scope,
            "geographic_scope": self.geographic_scope,
            "version_scope": self.version_scope,
            "desired_output": self.desired_output,
            "evidence_requirements": list(self.evidence_requirements),
            "assumptions": list(self.assumptions),
            "ambiguities": list(self.ambiguities),
            "clarification_required": self.clarification_required,
            "clarification_questions": list(self.clarification_questions),
            "confidence": dict(self.confidence),
            "model_used": self.model_used,
            "provider_used": self.provider_used,
            "raw_response": self.raw_response,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchIntentProposal:
        return cls(
            proposal_id=str(data.get("proposal_id", f"prop-{uuid.uuid4().hex[:8]}")),
            source_request_id=str(data.get("source_request_id", "")),
            objective=str(data.get("objective", "")),
            intent_types=[str(t) for t in data.get("intent_types", [])],
            subjects=[str(s) for s in data.get("subjects", [])],
            entities=[str(e) for e in data.get("entities", [])],
            comparison_targets=[str(c) for c in data.get("comparison_targets", [])],
            research_dimensions=[str(d) for d in data.get("research_dimensions", [])],
            explicit_constraints=[str(c) for c in data.get("explicit_constraints", [])],
            inferred_constraints=[str(c) for c in data.get("inferred_constraints", [])],
            freshness_requirement=data.get("freshness_requirement"),
            temporal_scope=data.get("temporal_scope"),
            geographic_scope=data.get("geographic_scope"),
            version_scope=data.get("version_scope"),
            desired_output=data.get("desired_output"),
            evidence_requirements=list(data.get("evidence_requirements", [])),
            assumptions=[str(a) for a in data.get("assumptions", [])],
            ambiguities=list(data.get("ambiguities", [])),
            clarification_required=bool(data.get("clarification_required", False)),
            clarification_questions=list(data.get("clarification_questions", [])),
            confidence=(
                {"overall": float(data["confidence"])}
                if isinstance(data.get("confidence"), (int, float))
                else {str(k): float(v) for k, v in data.get("confidence", {}).items() if isinstance(v, (int, float))}
                if isinstance(data.get("confidence"), dict)
                else {}
            ),
            model_used=str(data.get("model_used", "")),
            provider_used=str(data.get("provider_used", "")),
            raw_response=str(data.get("raw_response", "")),
            created_at=str(data.get("created_at", utc_now())),
        )


# =============================================================================
# 2. Proposal Validation Result
# =============================================================================

@dataclass
class ProposalValidationResult:
    """Outcome of deterministic validation performed on an untrusted proposal."""
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    proposal: Optional[ResearchIntentProposal] = None
    validated_intent: Optional[ResearchIntent] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "proposal": self.proposal.to_dict() if self.proposal else None,
            "validated_intent": self.validated_intent.to_dict() if self.validated_intent else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProposalValidationResult:
        proposal = (
            ResearchIntentProposal.from_dict(data["proposal"])
            if data.get("proposal")
            else None
        )
        validated_intent = (
            ResearchIntent.from_dict(data["validated_intent"])
            if data.get("validated_intent")
            else None
        )
        return cls(
            is_valid=bool(data.get("is_valid", False)),
            errors=list(data.get("errors", [])),
            warnings=list(data.get("warnings", [])),
            proposal=proposal,
            validated_intent=validated_intent,
        )


# =============================================================================
# 3. Deterministic Proposal Validator
# =============================================================================

class ProposalValidator:
    """
    Deterministic validator gating untrusted LLM proposals before acceptance.
    Enforces schema validity, enum constraints, required fields, bounds,
    separation of explicit vs inferred facts, and clarification consistency.
    """

    def validate(
        self,
        proposal: ResearchIntentProposal,
        deterministic_signals: Optional[IntentClassificationResult] = None,
        extracted_elements: Optional[ExtractedRequestElements] = None,
    ) -> ProposalValidationResult:
        if not isinstance(proposal, ResearchIntentProposal):
            return ProposalValidationResult(
                is_valid=False,
                errors=[f"Expected ResearchIntentProposal instance, got {type(proposal).__name__}"],
            )

        errors: list[str] = []
        warnings: list[str] = []

        # 1. Required field: objective
        if not proposal.objective or not proposal.objective.strip():
            errors.append("Proposal objective cannot be empty or whitespace.")

        # 2. Required field & Enum validation: intent_types
        valid_intents: list[IntentType] = []
        if not proposal.intent_types:
            errors.append("Proposal must specify at least one intent type.")
        else:
            for raw_it in proposal.intent_types:
                try:
                    it = IntentType(str(raw_it).strip().upper())
                    if it not in valid_intents:
                        valid_intents.append(it)
                except ValueError:
                    errors.append(f"Invalid intent_type: '{raw_it}'. Must be one of {[e.value for e in IntentType]}.")

        # 3. Enum validation: freshness_requirement
        validated_freshness = FreshnessRequirement.STATIC
        if proposal.freshness_requirement:
            try:
                validated_freshness = FreshnessRequirement(str(proposal.freshness_requirement).strip().upper())
            except ValueError:
                errors.append(
                    f"Invalid freshness_requirement: '{proposal.freshness_requirement}'. "
                    f"Must be one of {[e.value for e in FreshnessRequirement]}."
                )

        # 4. Enum validation: desired_output
        validated_output = DesiredOutput.FACTUAL_ANSWER
        if proposal.desired_output:
            try:
                validated_output = DesiredOutput(str(proposal.desired_output).strip().upper())
            except ValueError:
                errors.append(
                    f"Invalid desired_output: '{proposal.desired_output}'. "
                    f"Must be one of {[e.value for e in DesiredOutput]}."
                )

        # 5. Confidence score bounds [0.0, 1.0]
        for dimension, score in proposal.confidence.items():
            if not isinstance(score, (int, float)):
                errors.append(f"Confidence score for '{dimension}' must be numeric, got {type(score).__name__}.")
            elif score < 0.0 or score > 1.0:
                errors.append(f"Confidence score for '{dimension}' must be in [0.0, 1.0], got {score}.")

        # 6. Explicit vs Inferred constraint integrity (Assumptions must not become facts)
        clean_explicit = [c.strip() for c in proposal.explicit_constraints if c.strip()]
        clean_inferred = [c.strip() for c in proposal.inferred_constraints if c.strip()]

        # Prevent duplicate promotion: if an inferred item is identical to an explicit one, strip it from inferred
        deduped_inferred = [c for c in clean_inferred if c not in clean_explicit]

        # 7. Clarification and Ambiguity Consistency
        clarification_required = proposal.clarification_required
        has_questions = len(proposal.clarification_questions) > 0
        has_blocking_ambiguities = any(bool(a.get("blocking", False)) for a in proposal.ambiguities)

        if (has_questions or has_blocking_ambiguities) and not clarification_required:
            warnings.append(
                "Proposal specified clarification questions or blocking ambiguities but clarification_required was False. "
                "Automatically reconciled clarification_required to True."
            )
            clarification_required = True

        if clarification_required and not (has_questions or proposal.ambiguities):
            warnings.append(
                "Proposal marked clarification_required as True but provided no clarification questions or ambiguities."
            )

        # 8. Scope validation
        validated_temporal = None
        if proposal.temporal_scope:
            try:
                validated_temporal = TemporalScope.from_dict(proposal.temporal_scope)
                if validated_temporal.recency_days is not None and validated_temporal.recency_days < 0:
                    errors.append("TemporalScope recency_days cannot be negative.")
                if validated_temporal.start_date and validated_temporal.end_date:
                    if validated_temporal.start_date > validated_temporal.end_date:
                        warnings.append("TemporalScope start_date is after end_date; swapping dates.")
                        validated_temporal.start_date, validated_temporal.end_date = (
                            validated_temporal.end_date,
                            validated_temporal.start_date,
                        )
            except Exception as e:
                errors.append(f"Malformed temporal_scope dict: {e}")

        validated_version = None
        if proposal.version_scope:
            try:
                validated_version = VersionScope.from_dict(proposal.version_scope)
            except Exception as e:
                errors.append(f"Malformed version_scope dict: {e}")

        # If validation failed, return without constructing trusted ResearchIntent
        if errors:
            return ProposalValidationResult(
                is_valid=False,
                errors=errors,
                warnings=warnings,
                proposal=proposal,
                validated_intent=None,
            )

        # 9. Synthesize trusted ResearchIntent fusing LLM proposal + deterministic ground truth
        confidence_obj = IntentConfidence(
            objective=proposal.confidence.get("objective", 0.90),
            entities=proposal.confidence.get("entities", 0.90),
            dimensions=proposal.confidence.get("dimensions", 0.90),
            constraints=proposal.confidence.get("constraints", 0.90),
            assumptions=proposal.confidence.get("assumptions", 0.90),
        )

        # Build evidence requirements
        ev_reqs: list[EvidenceRequirement] = []
        for ev_raw in proposal.evidence_requirements:
            try:
                ev_reqs.append(EvidenceRequirement.from_dict(ev_raw))
            except Exception as e:
                warnings.append(f"Ignored malformed evidence_requirement: {e}")

        # Build ambiguities
        ambs: list[Ambiguity] = []
        for amb_raw in proposal.ambiguities:
            try:
                ambs.append(Ambiguity.from_dict(amb_raw))
            except Exception as e:
                warnings.append(f"Ignored malformed ambiguity: {e}")

        # Build clarification questions
        cqs: list[ClarificationQuestion] = []
        for cq_raw in proposal.clarification_questions:
            try:
                if isinstance(cq_raw, str):
                    cqs.append(ClarificationQuestion(question_text=cq_raw))
                elif isinstance(cq_raw, dict):
                    cqs.append(ClarificationQuestion.from_dict(cq_raw))
                elif isinstance(cq_raw, ClarificationQuestion):
                    cqs.append(cq_raw)
            except Exception as e:
                warnings.append(f"Ignored malformed clarification_question: {e}")

        # If blocking ambiguities exist but no questions provided, synthesize targeting questions
        if has_blocking_ambiguities and not cqs:
            for amb in ambiguities:
                if amb.blocking:
                    cqs.append(
                        ClarificationQuestion(
                            question_text=f"Clarification required: {amb.description}",
                            target_ambiguity_id=amb.ambiguity_id,
                        )
                    )

        # Deduplicate lists preserving order
        def _dedupe(items: list[str]) -> list[str]:
            seen = set()
            out = []
            for it in items:
                cleaned = it.strip()
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    out.append(cleaned)
            return out

        subjects = _dedupe(proposal.subjects)
        entities = _dedupe(proposal.entities)
        comparison_targets = _dedupe(proposal.comparison_targets)
        research_dimensions = _dedupe(proposal.research_dimensions)
        explicit_constraints = _dedupe(clean_explicit)
        inferred_constraints = _dedupe(deduped_inferred)
        assumptions = _dedupe(proposal.assumptions)

        # Incorporate deterministic extracted ground truth if provided
        if extracted_elements:
            for s in extracted_elements.subjects:
                if s not in subjects:
                    subjects.append(s)
            for e in extracted_elements.entities:
                if e not in entities:
                    entities.append(e)
            for ct in extracted_elements.comparison_targets:
                if ct not in comparison_targets:
                    comparison_targets.append(ct)
            for d in extracted_elements.research_dimensions:
                if d not in research_dimensions:
                    research_dimensions.append(d)
            for c in extracted_elements.explicit_constraints:
                if c not in explicit_constraints:
                    explicit_constraints.append(c)
            if not validated_temporal and extracted_elements.temporal_scope:
                validated_temporal = extracted_elements.temporal_scope
            if not validated_version and extracted_elements.version_scope:
                validated_version = extracted_elements.version_scope
            if not proposal.geographic_scope and extracted_elements.geographic_scope:
                proposal.geographic_scope = extracted_elements.geographic_scope

        # Incorporate deterministic classification corroboration if provided
        if deterministic_signals and deterministic_signals.primary_intent:
            if deterministic_signals.primary_intent not in valid_intents:
                valid_intents.insert(0, deterministic_signals.primary_intent)

        intent = ResearchIntent(
            objective=proposal.objective.strip(),
            intent_types=valid_intents,
            subjects=subjects,
            entities=entities,
            comparison_targets=comparison_targets,
            research_dimensions=research_dimensions,
            explicit_constraints=explicit_constraints,
            inferred_constraints=inferred_constraints,
            freshness_requirement=validated_freshness,
            temporal_scope=validated_temporal,
            geographic_scope=proposal.geographic_scope.strip() if proposal.geographic_scope else None,
            version_scope=validated_version,
            desired_output=validated_output,
            evidence_requirements=ev_reqs,
            assumptions=assumptions,
            ambiguities=ambs,
            clarification_required=clarification_required,
            clarification_questions=cqs,
            confidence=confidence_obj,
            source_request_id=proposal.source_request_id,
            metadata={
                "proposal_id": proposal.proposal_id,
                "model_used": proposal.model_used,
                "provider_used": proposal.provider_used,
            },
        )

        return ProposalValidationResult(
            is_valid=True,
            errors=errors,
            warnings=warnings,
            proposal=proposal,
            validated_intent=intent,
        )


# =============================================================================
# 4. LLM Understanding Engine
# =============================================================================

class LLMUnderstandingEngine:
    """
    LLM Understanding Engine for AutonomOS Research.
    Acts strictly as an ANALYST, analyzing normalized requests and deterministic signals
    to propose a structured ResearchIntentProposal.
    Enforces that proposals pass deterministic validation before being accepted.
    """

    SYSTEM_PROMPT = """You are an expert Research Analyst in the AutonomOS system.
Your sole job is to analyze the research request and structure the ResearchIntent understanding proposal in JSON format.

CRITICAL OPERATIONAL RULES:
1. You are an ANALYST, not the owner of Researcher state.
2. Do NOT invent facts.
3. Do NOT answer the research question.
4. Do NOT perform research.
5. Do NOT generate evidence.
6. Do NOT select crawlers.
7. Do NOT generate search queries.
8. Do NOT claim source credibility.
9. Do NOT execute tools.
10. Do NOT modify state.
11. Explicit information from the request must remain in explicit_constraints.
12. Inferred information MUST be strictly placed in inferred_constraints. Never convert an assumption into an explicit fact.
13. Identify ambiguity instead of guessing when ambiguity materially affects research.
14. Return strictly a single valid JSON object conforming to the schema below. No markdown fences, no conversational prose."""

    def __init__(
        self,
        gateway: Optional[InferenceGateway] = None,
        provider: Optional[BaseInferenceProvider] = None,
        model_id: str = "mock-analyst-model",
        validator: Optional[ProposalValidator] = None,
    ):
        self.gateway = gateway
        self.provider = provider
        self.model_id = model_id
        self.validator = validator or ProposalValidator()

    def understand(
        self,
        request: ResearchRequest,
        deterministic_signals: Optional[IntentClassificationResult] = None,
        extracted_elements: Optional[ExtractedRequestElements] = None,
    ) -> tuple[ResearchIntentProposal, ProposalValidationResult]:
        """
        Execute LLM understanding on a ResearchRequest enriched with deterministic signals.
        Returns the untrusted proposal and its deterministic validation result.
        """
        if not isinstance(request, ResearchRequest):
            raise TypeError(f"Expected ResearchRequest instance, got {type(request).__name__}")

        # 1. Format User Prompt with Request and Deterministic Signals
        user_prompt = self._build_user_prompt(request, deterministic_signals, extracted_elements)

        # 2. Build normalized InferenceRequest
        inf_req = InferenceRequest(
            request_id=f"inf-und-{uuid.uuid4().hex[:8]}",
            project_id=request.project_id,
            task_id=request.task_id,
            worker_id="researcher-understanding-engine",
            messages=[
                InferenceMessage(role="system", content=self.SYSTEM_PROMPT),
                InferenceMessage(role="user", content=user_prompt),
            ],
            requirements=ModelRequirement(
                required_capabilities={ModelCapability.TEXT_GENERATION, ModelCapability.STRUCTURED_OUTPUT},
                routing_profile=RoutingProfile.BEST_AVAILABLE,
            ),
            temperature=0.1,  # Low temperature for analytical structuring
            timeout_seconds=request.scope.timeout_seconds,
        )

        # 3. Execute inference via Gateway or Provider
        inf_resp = self._call_inference(inf_req)

        # 4. Parse JSON into ResearchIntentProposal
        proposal = self._parse_response_to_proposal(
            raw_content=inf_resp.content,
            request=request,
            model_used=inf_resp.model_used,
            provider_used=inf_resp.provider_used,
        )

        # 5. Validate proposal deterministically
        validation_result = self.validator.validate(
            proposal=proposal,
            deterministic_signals=deterministic_signals,
            extracted_elements=extracted_elements,
        )

        return proposal, validation_result

    def _call_inference(self, inf_req: InferenceRequest) -> InferenceResponse:
        """Call InferenceGateway or BaseInferenceProvider."""
        if self.gateway is not None:
            try:
                return self.gateway.execute(inf_req)
            except Exception as e:
                raise UnderstandingError(f"Inference gateway error during request understanding: {e}") from e

        if self.provider is not None:
            try:
                models = self.provider.list_models()
                model_meta = models[0] if models else ModelMetadata(
                    model_id=self.model_id,
                    provider_id=self.provider.provider_id,
                    display_name=self.model_id,
                    capabilities={ModelCapability.TEXT_GENERATION, ModelCapability.STRUCTURED_OUTPUT},
                )
                return self.provider.generate(inf_req, model_meta)
            except Exception as e:
                raise UnderstandingError(f"Inference provider error during request understanding: {e}") from e

        raise UnderstandingError("No InferenceGateway or BaseInferenceProvider configured for LLMUnderstandingEngine.")

    def _build_user_prompt(
        self,
        request: ResearchRequest,
        deterministic_signals: Optional[IntentClassificationResult],
        extracted_elements: Optional[ExtractedRequestElements],
    ) -> str:
        """Assemble structured prompt with request context and deterministic evidence."""
        prompt_lines = [
            "Analyze the following ResearchRequest and produce a ResearchIntentProposal JSON object.",
            "",
            "--- RESEARCH REQUEST ---",
            f"Objective: {request.objective}",
        ]
        if request.questions:
            prompt_lines.append("Sub-Questions:")
            for idx, q in enumerate(request.questions):
                prompt_lines.append(f"  {idx+1}. {q}")

        if request.constraints:
            prompt_lines.append("Explicit Constraints:")
            for c in request.constraints:
                prompt_lines.append(f"  - {c}")

        if request.scope.allowed_domains:
            prompt_lines.append(f"Allowed Domains: {request.scope.allowed_domains}")
        if request.scope.excluded_domains:
            prompt_lines.append(f"Excluded Domains: {request.scope.excluded_domains}")
        if request.scope.recency_days:
            prompt_lines.append(f"Recency Days Limit: {request.scope.recency_days}")
        prompt_lines.append(f"Required Output Format: {request.required_output_format}")

        if deterministic_signals:
            prompt_lines.extend([
                "",
                "--- DETERMINISTIC INTENT SIGNALS ---",
                f"Primary Intent Signal: {deterministic_signals.primary_intent.value if deterministic_signals.primary_intent else 'None'}",
                f"Secondary Intent Signals: {[s.value for s in deterministic_signals.secondary_intents]}",
                f"Is Ambiguous: {deterministic_signals.is_ambiguous}",
            ])
            if deterministic_signals.ambiguity_reason:
                prompt_lines.append(f"Ambiguity Reason: {deterministic_signals.ambiguity_reason}")

        if extracted_elements:
            prompt_lines.extend([
                "",
                "--- DETERMINISTIC EXTRACTED ELEMENTS ---",
                f"Subjects: {extracted_elements.subjects}",
                f"Entities: {extracted_elements.entities}",
                f"Comparison Targets: {extracted_elements.comparison_targets}",
                f"Research Dimensions: {extracted_elements.research_dimensions}",
                f"Explicit Constraints: {extracted_elements.explicit_constraints}",
            ])
            if extracted_elements.temporal_scope:
                prompt_lines.append(f"Temporal Scope: {extracted_elements.temporal_scope.to_dict()}")
            if extracted_elements.geographic_scope:
                prompt_lines.append(f"Geographic Scope: {extracted_elements.geographic_scope}")
            if extracted_elements.version_scope:
                prompt_lines.append(f"Version Scope: {extracted_elements.version_scope.to_dict()}")
            if extracted_elements.uncertainties:
                prompt_lines.append("Identified Uncertainties:")
                for u in extracted_elements.uncertainties:
                    prompt_lines.append(f"  - [{u.field_name}] {u.reason} (is_ambiguous={u.is_ambiguous})")

        prompt_lines.extend([
            "",
            "--- OUTPUT JSON SCHEMA ---",
            "Return a single JSON object with these keys:",
            "{",
            '  "objective": string,',
            '  "intent_types": ["DESCRIPTIVE" | "COMPARATIVE" | "EVALUATIVE" | "DIAGNOSTIC" | "IMPLEMENTATION" | "EXPLORATORY" | "VERIFICATION" | "HISTORICAL" | "TECHNICAL" | "DECISION_SUPPORT"],',
            '  "subjects": string[],',
            '  "entities": string[],',
            '  "comparison_targets": string[],',
            '  "research_dimensions": string[],',
            '  "explicit_constraints": string[],',
            '  "inferred_constraints": string[],',
            '  "freshness_requirement": "STATIC" | "CURRENT" | "RECENT" | "TIME_RANGE" | "POINT_IN_TIME" | "VERSION_SPECIFIC" | "UNKNOWN",',
            '  "temporal_scope": {"start_date": string|null, "end_date": string|null, "recency_days": int|null, "description": string} | null,',
            '  "geographic_scope": string | null,',
            '  "version_scope": {"target_version": string|null, "min_version": string|null, "max_version": string|null, "version_specifier": string|null, "ecosystem": string|null, "description": string} | null,',
            '  "desired_output": "FACTUAL_ANSWER" | "COMPARISON" | "RECOMMENDATION" | "TECHNICAL_EXPLANATION" | "IMPLEMENTATION_GUIDANCE" | "TIMELINE" | "ROOT_CAUSE" | "SUMMARY" | "DECISION_BRIEF",',
            '  "evidence_requirements": [{"description": string, "source_types": string[], "min_independent_sources": int}],',
            '  "assumptions": string[],',
            '  "ambiguities": [{"description": string, "impact": string, "affected_fields": string[], "blocking": bool}],',
            '  "clarification_required": bool,',
            '  "clarification_questions": [{"question_text": string, "options": string[], "default_assumption": string}],',
            '  "confidence": {"objective": float, "entities": float, "dimensions": float, "constraints": float, "assumptions": float}',
            "}",
        ])

        return "\n".join(prompt_lines)

    def _parse_response_to_proposal(
        self,
        raw_content: str,
        request: ResearchRequest,
        model_used: str,
        provider_used: str,
    ) -> ResearchIntentProposal:
        """Parse raw LLM response text into ResearchIntentProposal."""
        clean_json = raw_content.strip()

        # Strip markdown code fences if model returned ```json ... ```
        if clean_json.startswith("```"):
            clean_json = re.sub(r"^```(?:json)?\s*", "", clean_json)
            clean_json = re.sub(r"\s*```$", "", clean_json)
            clean_json = clean_json.strip()

        try:
            data = json.loads(clean_json)
        except Exception as e:
            logger.warning("Failed to parse LLM response as JSON: %s", e)
            # Return an invalid proposal that will fail validation with a clear error
            return ResearchIntentProposal(
                source_request_id=request.request_id,
                objective="",  # empty objective fails validation
                model_used=model_used,
                provider_used=provider_used,
                raw_response=raw_content,
                confidence={"objective": 0.0},
            )

        if not isinstance(data, dict):
            return ResearchIntentProposal(
                source_request_id=request.request_id,
                objective="",
                model_used=model_used,
                provider_used=provider_used,
                raw_response=raw_content,
            )

        # Populate proposal fields
        return ResearchIntentProposal(
            source_request_id=request.request_id,
            objective=str(data.get("objective", request.objective)),
            intent_types=[str(t) for t in data.get("intent_types", [])],
            subjects=[str(s) for s in data.get("subjects", [])],
            entities=[str(e) for e in data.get("entities", [])],
            comparison_targets=[str(c) for c in data.get("comparison_targets", [])],
            research_dimensions=[str(d) for d in data.get("research_dimensions", [])],
            explicit_constraints=[str(c) for c in data.get("explicit_constraints", [])],
            inferred_constraints=[str(c) for c in data.get("inferred_constraints", [])],
            freshness_requirement=data.get("freshness_requirement"),
            temporal_scope=data.get("temporal_scope") if isinstance(data.get("temporal_scope"), dict) else None,
            geographic_scope=data.get("geographic_scope"),
            version_scope=data.get("version_scope") if isinstance(data.get("version_scope"), dict) else None,
            desired_output=data.get("desired_output"),
            evidence_requirements=list(data.get("evidence_requirements", [])) if isinstance(data.get("evidence_requirements"), list) else [],
            assumptions=[str(a) for a in data.get("assumptions", []) if isinstance(a, str)],
            ambiguities=list(data.get("ambiguities", [])) if isinstance(data.get("ambiguities"), list) else [],
            clarification_required=bool(data.get("clarification_required", False)),
            clarification_questions=list(data.get("clarification_questions", [])) if isinstance(data.get("clarification_questions"), list) else [],
            confidence=(
                {"overall": float(data["confidence"])}
                if isinstance(data.get("confidence"), (int, float))
                else {str(k): float(v) for k, v in data.get("confidence", {}).items() if isinstance(v, (int, float))}
                if isinstance(data.get("confidence"), dict)
                else {}
            ),
            model_used=model_used,
            provider_used=provider_used,
            raw_response=raw_content,
        )

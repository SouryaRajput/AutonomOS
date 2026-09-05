from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import re
from typing import Any, Optional
import uuid

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
from core.research.contracts.intent import ResearchIntent
from core.research.contracts.request import ResearchRequest
from core.research.decomposition.constraints import DecompositionConstraintValidator
from core.research.decomposition.model import (
    ResearchDecomposition,
    ResearchDependency,
    ResearchSubQuestion,
    SubQuestionProvenance,
    new_id,
    utc_now,
)
from core.research.decomposition.normalizer import DecompositionNormalizer
from core.research.decomposition.policy import DecompositionPolicy
from core.research.decomposition.types import (
    ResearchDependencyType,
    SubQuestionPriority,
    SubQuestionStatus,
    SubQuestionType,
)
from core.research.decomposition.validator import DecompositionValidator

logger = logging.getLogger("AutonomOS.Research.SubQuestionGenerator")


class DecompositionGenerationStatus(str, Enum):
    """Outcome status of intelligent sub-question decomposition generation."""
    SUCCESS = "SUCCESS"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    INFERENCE_FAILED = "INFERENCE_FAILED"


@dataclass
class RawSubQuestionProposal:
    """
    Untrusted analytical sub-question proposed by the language model before validation.
    """
    temp_id: str = ""
    question: str = ""
    objective: str = ""
    sub_question_type: str = "FACT_FINDING"
    priority: str = "MEDIUM"
    required: bool = True
    dependencies: list[str] = field(default_factory=list)
    rationale: str = ""
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "temp_id": self.temp_id,
            "question": self.question,
            "objective": self.objective,
            "sub_question_type": self.sub_question_type,
            "priority": self.priority,
            "required": self.required,
            "dependencies": list(self.dependencies),
            "rationale": self.rationale,
            "assumptions": list(self.assumptions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RawSubQuestionProposal:
        return cls(
            temp_id=str(data.get("temp_id", "")),
            question=str(data.get("question", "")),
            objective=str(data.get("objective", "")),
            sub_question_type=str(data.get("sub_question_type", "FACT_FINDING")),
            priority=str(data.get("priority", "MEDIUM")),
            required=bool(data.get("required", True)),
            dependencies=list(data.get("dependencies", [])),
            rationale=str(data.get("rationale", "")),
            assumptions=list(data.get("assumptions", [])),
        )


@dataclass
class DecompositionProposal:
    """
    Untrusted decomposition proposal containing a raw collection of sub-questions
    and decomposition rationale produced by the language model.
    """
    sub_questions: list[RawSubQuestionProposal] = field(default_factory=list)
    decomposition_rationale: str = ""
    raw_response: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sub_questions": [sq.to_dict() for sq in self.sub_questions],
            "decomposition_rationale": self.decomposition_rationale,
            "raw_response": self.raw_response,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecompositionProposal:
        raw_sqs = [
            RawSubQuestionProposal.from_dict(sq)
            for sq in data.get("sub_questions", [])
            if isinstance(sq, dict)
        ]
        return cls(
            sub_questions=raw_sqs,
            decomposition_rationale=str(data.get("decomposition_rationale", "")),
            raw_response=str(data.get("raw_response", "")),
        )


@dataclass
class DecompositionGenerationResult:
    """
    Structured outcome of the intelligent sub-question generation pipeline.
    Contains either a validated ResearchDecomposition or failure details.
    """
    status: DecompositionGenerationStatus
    decomposition: Optional[ResearchDecomposition] = None
    proposal: Optional[DecompositionProposal] = None
    validation_result: Optional[Any] = None
    validation_issues: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    error_message: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value if isinstance(self.status, DecompositionGenerationStatus) else str(self.status),
            "decomposition": self.decomposition.to_dict() if self.decomposition else None,
            "proposal": self.proposal.to_dict() if self.proposal else None,
            "validation_result": self.validation_result.to_dict() if self.validation_result and hasattr(self.validation_result, "to_dict") else None,
            "validation_issues": list(self.validation_issues),
            "trace": list(self.trace),
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecompositionGenerationResult:
        st_raw = data.get("status", DecompositionGenerationStatus.VALIDATION_FAILED.value)
        try:
            status = DecompositionGenerationStatus(st_raw)
        except (ValueError, TypeError):
            status = DecompositionGenerationStatus.VALIDATION_FAILED

        d_raw = data.get("decomposition")
        decomp = ResearchDecomposition.from_dict(d_raw) if isinstance(d_raw, dict) else None

        p_raw = data.get("proposal")
        prop = DecompositionProposal.from_dict(p_raw) if isinstance(p_raw, dict) else None

        val_res = None
        vr_raw = data.get("validation_result")
        if isinstance(vr_raw, dict):
            try:
                from core.research.decomposition.refinement import DecompositionValidationResult
                val_res = DecompositionValidationResult.from_dict(vr_raw)
            except Exception:
                val_res = None

        return cls(
            status=status,
            decomposition=decomp,
            proposal=prop,
            validation_result=val_res,
            validation_issues=list(data.get("validation_issues", [])),
            trace=list(data.get("trace", [])),
            error_message=data.get("error_message"),
        )


class SubQuestionGenerator:
    """
    Bounded intelligence engine that proposes a small, structured set of useful
    internal research sub-questions for a ResearchIntent.
    Guarantees strict structured output, normalizes proposals, and validates them
    against deterministic policy constraints before returning.
    """

    SYSTEM_PROMPT = """You are the AutonomOS Research Decomposition Specialist.
Your role is to analyze a ResearchIntent and determine:
"What are the smallest meaningful research questions needed to investigate this objective?"

Guiding Principles:
1. Decompose into the SMALLEST meaningful set of distinct questions.
2. Question Count Guidelines:
   - Simple / factual requests: exactly 1 sub-question.
   - Moderately complex requests: 3 to 5 sub-questions.
   - Highly complex / multi-faceted requests: at most 8 sub-questions.
   - NEVER generate more than 8 sub-questions.
3. Sub-questions must be:
   - Directly useful to the objective
   - Non-overlapping (no duplicate or redundant questions)
   - Concrete and concise
   - Sufficient to support subsequent synthesis
4. Dependencies:
   - When a logical sequence exists (e.g. identify options -> benchmark performance -> recommend), specify prerequisite temp_ids in "dependencies".
   - Do NOT create dependency cycles or self-dependencies.
5. Priority:
   - Assign CRITICAL, HIGH, MEDIUM, or LOW to reflect investigative importance to the parent objective.
6. Required:
   - Mark essential questions required: true. At least 1 question MUST be marked required: true.

STRICT PROHIBITIONS:
- Do NOT answer the questions.
- Do NOT output execution instructions, crawler commands, or search queries.
- Do NOT repeat or merely rephrase the root objective or root question verbatim.
- Do NOT ask questions addressed to the user.
- Do NOT output conversational prose outside the JSON object.

OUTPUT FORMAT:
Respond with ONLY a valid JSON object matching this schema:
{
  "decomposition_rationale": "Concise summary of why this decomposition structure was chosen...",
  "sub_questions": [
    {
      "temp_id": "sq-1",
      "question": "What is the specific architectural pattern used for X?",
      "objective": "Document the structural patterns and contracts of X.",
      "sub_question_type": "FACT_FINDING" | "COMPARISON" | "EVALUATION" | "VERIFICATION" | "DIAGNOSTIC" | "ARCHITECTURAL" | "IMPLEMENTATION" | "CONSTRAINT_ANALYSIS" | "RISK_ANALYSIS" | "COST_ANALYSIS" | "PERFORMANCE_ANALYSIS" | "SECURITY_ANALYSIS" | "COMPATIBILITY_ANALYSIS" | "HISTORICAL" | "SYNTHESIS",
      "priority": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW",
      "required": true,
      "dependencies": [],
      "rationale": "Need baseline understanding before evaluation."
    }
  ]
}
"""

    def __init__(
        self,
        gateway: Optional[InferenceGateway] = None,
        provider: Optional[BaseInferenceProvider] = None,
        model_id: str = "decomposition-generator-model",
    ):
        self.gateway = gateway
        self.provider = provider
        self.model_id = model_id

    def generate(
        self,
        intent: ResearchIntent,
        request: Optional[ResearchRequest] = None,
        policy: Optional[DecompositionPolicy] = None,
    ) -> DecompositionGenerationResult:
        """
        Decompose a ResearchIntent into a validated, constrained ResearchDecomposition.
        """
        trace: list[str] = [f"[{utc_now()}] Initiating intelligent sub-question generation"]

        if intent is None or not getattr(intent, "objective", None) or not intent.objective.strip():
            trace.append("Invalid or empty ResearchIntent provided")
            return DecompositionGenerationResult(
                status=DecompositionGenerationStatus.VALIDATION_FAILED,
                error_message="ResearchIntent objective cannot be empty",
                validation_issues=["ResearchIntent objective is empty"],
                trace=trace,
            )

        active_policy = policy or DecompositionPolicy()

        # 1. Build Prompt & Format Inference Request
        user_prompt = self._build_prompt(intent=intent, request=request, policy=active_policy)
        trace.append(f"Submitting inference request to model '{self.model_id}' (target: {active_policy.target_sub_questions})")

        inf_req = InferenceRequest(
            request_id=f"inf-decomp-{uuid.uuid4().hex[:8]}",
            project_id=getattr(request, "project_id", "") or "default-project",
            task_id=getattr(request, "task_id", "") or f"decomp-{intent.intent_id}",
            worker_id="researcher-subquestion-generator",
            messages=[
                InferenceMessage(role="system", content=self.SYSTEM_PROMPT),
                InferenceMessage(role="user", content=user_prompt),
            ],
            requirements=ModelRequirement(
                required_capabilities={ModelCapability.TEXT_GENERATION, ModelCapability.STRUCTURED_OUTPUT},
                routing_profile=RoutingProfile.BEST_AVAILABLE,
            ),
            temperature=0.1,
            timeout_seconds=getattr(getattr(request, "scope", None), "timeout_seconds", 60.0),
        )

        # 2. Call Inference Provider
        try:
            response = self._call_inference(inf_req)
            raw_text = response.content if hasattr(response, "content") else getattr(response, "text", "")
        except Exception as e:
            logger.error("Inference provider failed during decomposition: %s", e)
            trace.append(f"Inference provider failure: {str(e)}")
            return DecompositionGenerationResult(
                status=DecompositionGenerationStatus.INFERENCE_FAILED,
                error_message=f"Inference provider failed: {str(e)}",
                trace=trace,
            )

        trace.append("Inference response received; parsing structured proposal")

        # 3. Parse Response into Untrusted DecompositionProposal
        proposal = self._parse_response(raw_text)
        from core.research.decomposition.refinement import DecompositionRefiner

        if proposal is None:
            trace.append("Failed to parse LLM response into structured DecompositionProposal")
            val_res = DecompositionRefiner.validate_and_refine(
                intent=intent,
                proposal=raw_text,
                request=request,
                policy=active_policy,
            )
            return DecompositionGenerationResult(
                status=DecompositionGenerationStatus.MALFORMED_OUTPUT,
                validation_result=val_res,
                validation_issues=[i.message for i in val_res.issues],
                error_message="Language model output could not be parsed into a valid decomposition JSON structure",
                trace=trace,
            )

        trace.append(f"Parsed {len(proposal.sub_questions)} raw sub-question proposals")

        # 4. Safe Deterministic Refinement & Full Validation Pipeline
        val_res = DecompositionRefiner.validate_and_refine(
            intent=intent,
            proposal=proposal,
            request=request,
            policy=active_policy,
        )
        trace.extend(val_res.trace)

        if not val_res.is_valid:
            trace.append(f"Candidate decomposition violated {len(val_res.issues)} constraint(s)")
            return DecompositionGenerationResult(
                status=DecompositionGenerationStatus.VALIDATION_FAILED,
                decomposition=val_res.decomposition,
                proposal=proposal,
                validation_result=val_res,
                validation_issues=[i.message for i in val_res.issues],
                error_message="Decomposition proposal failed deterministic constraints",
                trace=trace,
            )

        candidate = val_res.decomposition
        trace.append(f"Successfully generated and verified ResearchDecomposition with {len(candidate.sub_questions)} sub-questions")
        candidate.add_trace(f"Intelligently generated via '{self.model_id}'")

        return DecompositionGenerationResult(
            status=DecompositionGenerationStatus.SUCCESS,
            decomposition=candidate,
            proposal=proposal,
            validation_result=val_res,
            validation_issues=[],
            trace=trace,
        )

    # -------------------------------------------------------------------------
    # Prompt Construction
    # -------------------------------------------------------------------------
    def _build_prompt(
        self,
        intent: ResearchIntent,
        request: Optional[ResearchRequest],
        policy: DecompositionPolicy,
    ) -> str:
        sections: list[str] = []

        sections.append("## Research Objective")
        sections.append(f"**Objective**: {intent.objective}")

        root_q = getattr(intent, "root_question", None)
        if not root_q and request and request.questions:
            root_q = request.questions[0]
        if root_q:
            sections.append(f"**Root Question**: {root_q}")

        if intent.entities:
            sections.append(f"**Key Entities**: {', '.join(intent.entities)}")

        if intent.comparison_targets:
            sections.append(f"**Comparison Targets**: {', '.join(intent.comparison_targets)}")

        if intent.research_dimensions:
            sections.append(f"**Target Dimensions**: {', '.join(intent.research_dimensions)}")

        if intent.explicit_constraints:
            sections.append(f"**Known Constraints**:\n" + "\n".join(f"- {c}" for c in intent.explicit_constraints))

        if getattr(intent, "unresolved_ambiguities", None):
            ambs = [f"- {a.description}" for a in intent.unresolved_ambiguities if getattr(a, "description", None)]
            if ambs:
                sections.append(f"**Unresolved Ambiguities**:\n" + "\n".join(ambs))

        sections.append("## Decomposition Constraints")
        sections.append(
            f"- Target sub-questions range: {policy.target_min_sub_questions} to {policy.target_max_sub_questions}\n"
            f"- Hard maximum allowed: {policy.max_sub_questions}\n"
            f"- Minimum required: {policy.min_sub_questions}\n"
            "- Ensure questions are distinct, substantive, and directly advance the root objective."
        )

        sections.append("Generate the structured JSON decomposition proposal now:")

        return "\n\n".join(sections)

    # -------------------------------------------------------------------------
    # Candidate Graph Construction
    # -------------------------------------------------------------------------
    def _build_candidate_decomposition(
        self,
        proposal: DecompositionProposal,
        intent: ResearchIntent,
        request: Optional[ResearchRequest],
        trace: list[str],
    ) -> ResearchDecomposition:
        decomp_id = new_id("decomp")
        req_id = (
            getattr(request, "request_id", "")
            if request and getattr(request, "request_id", None)
            else (getattr(intent, "research_request_id", "") or new_id("req"))
        )

        root_q = getattr(intent, "root_question", "")
        if not root_q and request and request.questions:
            root_q = request.questions[0]
        if not root_q:
            root_q = intent.objective

        decomposition = ResearchDecomposition(
            decomposition_id=decomp_id,
            research_request_id=req_id,
            research_intent_id=getattr(intent, "intent_id", None),
            objective=intent.objective,
            root_question=root_q,
            decomposition_confidence=1.0,
            coverage_requirements=list(getattr(intent, "research_dimensions", [])),
        )

        # Map temp_ids to persistent sub_question_ids
        id_map: dict[str, str] = {}
        for idx, raw_sq in enumerate(proposal.sub_questions):
            persistent_id = new_id("subq")
            tid = raw_sq.temp_id.strip() if raw_sq.temp_id else f"sq-{idx+1}"
            id_map[tid] = persistent_id

        # Instantiate ResearchSubQuestion records
        for idx, raw_sq in enumerate(proposal.sub_questions):
            tid = raw_sq.temp_id.strip() if raw_sq.temp_id else f"sq-{idx+1}"
            persistent_id = id_map.get(tid, new_id("subq"))

            # Coerce SubQuestionType
            sq_type_val = raw_sq.sub_question_type.upper() if raw_sq.sub_question_type else "FACT_FINDING"
            try:
                sq_type = SubQuestionType(sq_type_val)
            except ValueError:
                sq_type = SubQuestionType.FACT_FINDING

            # Coerce Priority
            prio_val = raw_sq.priority.upper() if raw_sq.priority else "MEDIUM"
            try:
                priority = SubQuestionPriority(prio_val)
            except ValueError:
                priority = SubQuestionPriority.MEDIUM

            # Map dependencies from temp_ids to persistent_ids
            mapped_deps: list[str] = []
            for dep_ref in raw_sq.dependencies:
                mapped_dep = id_map.get(dep_ref.strip(), dep_ref.strip())
                if mapped_dep and mapped_dep != persistent_id and mapped_dep not in mapped_deps:
                    mapped_deps.append(mapped_dep)

            provenance = SubQuestionProvenance(
                research_request_id=req_id,
                decomposition_id=decomp_id,
                research_intent_id=getattr(intent, "intent_id", None),
                originating_requirement=intent.objective,
            )

            sub_q = ResearchSubQuestion(
                sub_question_id=persistent_id,
                decomposition_id=decomp_id,
                parent_id=None,
                question=raw_sq.question,
                objective=raw_sq.objective,
                sub_question_type=sq_type,
                rationale=raw_sq.rationale,
                priority=priority,
                required=raw_sq.required,
                status=SubQuestionStatus.PENDING,
                dependencies=mapped_deps,
                assumptions=list(raw_sq.assumptions),
                decomposition_depth=1,
                provenance=provenance,
            )
            decomposition.sub_questions.append(sub_q)

        # Create explicit ResearchDependency records
        seen_dep_edges: set[tuple[str, str]] = set()
        for sq in decomposition.sub_questions:
            for prereq_id in sq.dependencies:
                edge = (prereq_id, sq.sub_question_id)
                if edge not in seen_dep_edges:
                    seen_dep_edges.add(edge)
                    dep = ResearchDependency(
                        dependency_id=new_id("dep"),
                        prerequisite_id=prereq_id,
                        dependent_id=sq.sub_question_id,
                        dependency_type=ResearchDependencyType.PREREQUISITE,
                    )
                    decomposition.dependencies.append(dep)

        return decomposition

    # -------------------------------------------------------------------------
    # Response Parsing & Fallbacks
    # -------------------------------------------------------------------------
    def _parse_response(self, text: str) -> Optional[DecompositionProposal]:
        """Strip markdown fences and parse structured JSON into DecompositionProposal."""
        if not text or not text.strip():
            return None

        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                except json.JSONDecodeError:
                    return None
            else:
                return None

        if not isinstance(data, dict):
            return None

        if "sub_questions" not in data or not isinstance(data["sub_questions"], list):
            return None

        proposal = DecompositionProposal.from_dict(data)
        proposal.raw_response = text
        return proposal

    # -------------------------------------------------------------------------
    # Inference Execution
    # -------------------------------------------------------------------------
    def _call_inference(self, inf_req: InferenceRequest) -> InferenceResponse:
        """Execute request against Gateway or Provider."""
        if self.gateway is not None:
            return self.gateway.execute(inf_req)

        if self.provider is not None:
            models = self.provider.list_models()
            model_meta = models[0] if models else ModelMetadata(
                model_id=self.model_id,
                provider_id=self.provider.provider_id,
                display_name=self.model_id,
                capabilities={ModelCapability.TEXT_GENERATION, ModelCapability.STRUCTURED_OUTPUT},
            )
            return self.provider.generate(inf_req, model_meta)

        raise RuntimeError("No InferenceGateway or BaseInferenceProvider configured for SubQuestionGenerator.")


# Alias for explicit domain clarity
DecompositionGenerator = SubQuestionGenerator

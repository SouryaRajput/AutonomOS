from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
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
from core.research.contracts.intent import Ambiguity, ResearchIntent
from core.research.contracts.request import ResearchRequest
from core.research.question.model import (
    QuestionOption,
    QuestionProvenance,
    ResearchDecisionQuestion,
    ResearchQuestion,
    new_id,
    utc_now,
)
from core.research.question.state import QuestionTracker
from core.research.question.types import (
    DecisionType,
    QuestionImportance,
    QuestionState,
)
from core.research.question.validator import (
    QuestionValidationError,
    QuestionValidator,
)

logger = logging.getLogger("AutonomOS.Research.QuestionGenerator")


class QuestionGenerationStatus(str, Enum):
    """Outcome status of an intelligent research question generation query."""
    QUESTION_GENERATED = "QUESTION_GENERATED"
    NO_QUESTION_REQUIRED = "NO_QUESTION_REQUIRED"
    FAILED = "FAILED"


@dataclass
class QuestionProposal:
    """
    Untrusted analytical proposal produced by the LLM.
    Captures whether an unresolved material decision exists and proposes framing and options.
    """
    proposal_id: str = field(default_factory=lambda: f"qprop-{uuid.uuid4().hex[:8]}")
    has_unresolved_decision: bool = False
    decision_type: Optional[str] = None
    importance: Optional[str] = None
    question: Optional[str] = None
    context: Optional[str] = None
    impact: Optional[str] = None
    rationale: Optional[str] = None
    options: list[dict[str, Any]] = field(default_factory=list)
    recommended_option_id: Optional[str] = None
    recommendation_reason: Optional[str] = None
    unresolved_decision_id: Optional[str] = None
    raw_response: str = ""
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "has_unresolved_decision": self.has_unresolved_decision,
            "decision_type": self.decision_type,
            "importance": self.importance,
            "question": self.question,
            "context": self.context,
            "impact": self.impact,
            "rationale": self.rationale,
            "options": list(self.options),
            "recommended_option_id": self.recommended_option_id,
            "recommendation_reason": self.recommendation_reason,
            "unresolved_decision_id": self.unresolved_decision_id,
            "raw_response": self.raw_response,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuestionProposal:
        return cls(
            proposal_id=str(data.get("proposal_id", f"qprop-{uuid.uuid4().hex[:8]}")),
            has_unresolved_decision=bool(data.get("has_unresolved_decision", False)),
            decision_type=str(data["decision_type"]) if data.get("decision_type") is not None else None,
            importance=str(data["importance"]) if data.get("importance") is not None else None,
            question=str(data["question"]) if data.get("question") is not None else None,
            context=str(data["context"]) if data.get("context") is not None else None,
            impact=str(data["impact"]) if data.get("impact") is not None else None,
            rationale=str(data["rationale"]) if data.get("rationale") is not None else None,
            options=list(data.get("options", [])),
            recommended_option_id=str(data["recommended_option_id"]) if data.get("recommended_option_id") is not None else None,
            recommendation_reason=str(data["recommendation_reason"]) if data.get("recommendation_reason") is not None else None,
            unresolved_decision_id=str(data["unresolved_decision_id"]) if data.get("unresolved_decision_id") is not None else None,
            raw_response=str(data.get("raw_response", "")),
            created_at=str(data.get("created_at", utc_now())),
        )


@dataclass
class QuestionGenerationResult:
    """
    Authoritative result of the intelligent question generation decision.
    Contains either exactly one validated ResearchQuestion or NO_QUESTION_REQUIRED.
    """
    status: QuestionGenerationStatus
    question: Optional[ResearchQuestion] = None
    reason: str = ""
    remaining_budget: int = 5
    proposal: Optional[QuestionProposal] = None
    validation_issues: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value if isinstance(self.status, QuestionGenerationStatus) else str(self.status),
            "question": self.question.to_dict() if self.question else None,
            "reason": self.reason,
            "remaining_budget": self.remaining_budget,
            "proposal": self.proposal.to_dict() if self.proposal else None,
            "validation_issues": list(self.validation_issues),
            "trace": list(self.trace),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuestionGenerationResult:
        st_raw = data.get("status", QuestionGenerationStatus.NO_QUESTION_REQUIRED.value)
        try:
            status = QuestionGenerationStatus(st_raw)
        except (ValueError, TypeError):
            status = QuestionGenerationStatus.NO_QUESTION_REQUIRED

        q_raw = data.get("question")
        question = ResearchQuestion.from_dict(q_raw) if isinstance(q_raw, dict) else None

        prop_raw = data.get("proposal")
        proposal = QuestionProposal.from_dict(prop_raw) if isinstance(prop_raw, dict) else None

        return cls(
            status=status,
            question=question,
            reason=str(data.get("reason", "")),
            remaining_budget=int(data.get("remaining_budget", 0)),
            proposal=proposal,
            validation_issues=list(data.get("validation_issues", [])),
            trace=list(data.get("trace", [])),
        )


class QuestionGenerator:
    """
    Bounded intelligence engine that decides whether an unresolved decision requires
    user clarification and generates exactly ONE structured decision question.
    """

    SYSTEM_PROMPT = """You are the AutonomOS Research Decision Analyst.
Your role is to analyze a ResearchRequest and ResearchIntent to determine whether an UNRESOLVED, MATERIAL DECISION exists that must be clarified by the user.

Guiding Principle:
"Ask only when resolving an important uncertainty will materially improve or change the research."
The goal is NOT to interrogate the user. Do NOT ask about trivial details, aesthetic preferences, details the system can safely infer, or minor details that do not change the research plan.

Important Decision Categories (DecisionType):
- SCOPE: Ambiguity in the domain, entity range, or operational boundary
- COMPARISON_CRITERIA: Tradeoff priorities (performance vs cost vs maintainability)
- TECHNICAL_DIRECTION: Fundamental technology stack or paradigm choice
- ARCHITECTURAL_CONSTRAINT: Critical environmental or structural constraints
- PERFORMANCE_PRIORITY: Latency, throughput, scale requirements
- COST_PRIORITY: Budget, cloud infrastructure cost constraints
- SECURITY_PRIORITY: Compliance, encryption, auth requirements
- FRESHNESS: Recency requirements affecting source selection
- VERSION: Target runtime or framework version dependencies
- ENVIRONMENT: Deployment target (cloud, on-prem, edge, container)
- OUTPUT_EXPECTATION: Expected format or deliverable focus
- AMBIGUITY_RESOLUTION: Critical objective ambiguity that cannot be safely inferred
- OTHER_HIGH_IMPACT: Other decisions with substantial architectural consequences

Importance Levels:
- LOW: Trivial/minor details. NEVER generate a user question for LOW importance.
- MEDIUM: Secondary preferences. Usually resolved internally.
- HIGH: Materially impacts research plan or evaluation criteria. Eligible to ask.
- CRITICAL: Blocking ambiguity or directional choice. Must be resolved before proceeding.

Option Requirements:
- If an important decision exists, generate EXACTLY 2 to 4 structured options.
- Each option must have:
  - "option_id": unique identifier (e.g., "opt-1", "opt-2")
  - "label": concise name (e.g., "Prioritize Performance")
  - "description": clear explanation of this course of action
  - "rationale": why this option is attractive or relevant
- You may optionally suggest ONE recommended option by setting "recommended_option_id" and providing "recommendation_reason".

STRICT PROHIBITIONS:
- Do NOT generate more than 1 question.
- Do NOT generate follow-up questions.
- Do NOT generate more than 4 options.
- Do NOT execute tools or crawlers.
- Do NOT assume the user accepted your recommended option.

OUTPUT FORMAT:
You must respond with ONLY a valid JSON object matching this schema:
If NO important decision exists:
{
  "has_unresolved_decision": false,
  "rationale": "Explanation of why no question is needed..."
}

If an important decision DOES exist:
{
  "has_unresolved_decision": true,
  "unresolved_decision_id": "string",
  "decision_type": "SCOPE" | "COMPARISON_CRITERIA" | "TECHNICAL_DIRECTION" | "ARCHITECTURAL_CONSTRAINT" | "PERFORMANCE_PRIORITY" | "COST_PRIORITY" | "SECURITY_PRIORITY" | "FRESHNESS" | "VERSION" | "ENVIRONMENT" | "OUTPUT_EXPECTATION" | "AMBIGUITY_RESOLUTION" | "OTHER_HIGH_IMPACT",
  "importance": "HIGH" | "CRITICAL",
  "question": "Concise question for the user (10-500 chars)?",
  "context": "Context explaining why this decision matters...",
  "impact": "How this choice materially alters the research outcome...",
  "rationale": "Analytical rationale for formulating this question...",
  "options": [
    {
      "option_id": "opt-1",
      "label": "Short label",
      "description": "Option description",
      "rationale": "Rationale for option"
    },
    {
      "option_id": "opt-2",
      "label": "Short label",
      "description": "Option description",
      "rationale": "Rationale for option"
    }
  ],
  "recommended_option_id": "opt-1" (or null),
  "recommendation_reason": "Reason for recommendation..." (or null)
}
"""

    def __init__(
        self,
        gateway: Optional[InferenceGateway] = None,
        provider: Optional[BaseInferenceProvider] = None,
        model_id: str = "question-analyst-model",
    ):
        self.gateway = gateway
        self.provider = provider
        self.model_id = model_id

    def generate_question(
        self,
        request: ResearchRequest,
        intent: Optional[ResearchIntent] = None,
        tracker: Optional[QuestionTracker] = None,
        remaining_budget: Optional[int] = None,
        already_asked_questions: Optional[list[ResearchQuestion]] = None,
        already_answered_decisions: Optional[list[str]] = None,
        allow_medium: bool = False,
    ) -> QuestionGenerationResult:
        """
        Evaluate research context and generate at most ONE structured decision question
        if an unresolved HIGH or CRITICAL decision exists.
        """
        trace: list[str] = [f"[{utc_now()}] Initiating intelligent question generation"]

        # 1. Resolve remaining budget and active question invariants
        if tracker is not None:
            calc_budget = max(0, tracker.max_questions - tracker.total_questions_count)
            budget = calc_budget if remaining_budget is None else min(calc_budget, remaining_budget)
            asked = list(tracker.history)
            if tracker.active_question is not None:
                trace.append("Active question is already pending; enforcing one-at-a-time invariant")
                return QuestionGenerationResult(
                    status=QuestionGenerationStatus.NO_QUESTION_REQUIRED,
                    reason="Active question is already pending for this research task",
                    remaining_budget=budget,
                    trace=trace,
                )
        else:
            budget = 5 if remaining_budget is None else max(0, min(5, remaining_budget))
            asked = list(already_asked_questions or [])

        answered = list(already_answered_decisions or [])

        # Extract answered decisions from asked questions with answers
        for q in asked:
            if q.is_answered() and q.answer is not None:
                if q.answer.selected_option_id:
                    answered.append(f"{q.decision_type.value}:{q.answer.selected_option_id}")
                elif q.answer.custom_answer:
                    answered.append(f"{q.decision_type.value}:{q.answer.custom_answer}")
            elif q.is_skipped():
                answered.append(f"{q.decision_type.value}:SKIPPED")

        # 2. Hard budget check
        if budget <= 0:
            trace.append("Question budget exhausted (remaining: 0); skipping LLM generation")
            return QuestionGenerationResult(
                status=QuestionGenerationStatus.NO_QUESTION_REQUIRED,
                reason="Question budget exhausted for this research task (remaining: 0)",
                remaining_budget=0,
                trace=trace,
            )

        # 3. Formulate Prompt & Call Inference
        user_prompt = self._build_prompt(
            request=request,
            intent=intent,
            already_asked=asked,
            already_answered=answered,
            remaining_budget=budget,
        )

        trace.append(f"Submitting inference request to model '{self.model_id}' (budget: {budget})")

        inf_req = InferenceRequest(
            request_id=f"inf-qgen-{uuid.uuid4().hex[:8]}",
            project_id=getattr(request, "project_id", "") or "default-project",
            task_id=getattr(request, "task_id", "") or f"qgen-{request.request_id}",
            worker_id="researcher-question-generator",
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

        try:
            response = self._call_inference(inf_req)
            raw_text = response.content if hasattr(response, "content") else getattr(response, "text", "")
        except Exception as e:
            logger.error("Inference provider failed during question generation: %s", e)
            trace.append(f"Inference provider failure: {str(e)}")
            return QuestionGenerationResult(
                status=QuestionGenerationStatus.FAILED,
                reason=f"Inference failure: {str(e)}",
                remaining_budget=budget,
                trace=trace,
            )

        # 4. Parse JSON Response
        proposal = self._parse_response(raw_text)
        if proposal is None:
            trace.append("Failed to parse valid JSON proposal from LLM output")
            return QuestionGenerationResult(
                status=QuestionGenerationStatus.FAILED,
                reason="Malformed LLM output: unable to parse valid JSON proposal",
                remaining_budget=budget,
                trace=trace,
            )

        trace.append(
            f"Parsed proposal: has_decision={proposal.has_unresolved_decision}, "
            f"type={proposal.decision_type}, importance={proposal.importance}"
        )

        # 5. Evaluate Decision Requirement
        if not proposal.has_unresolved_decision:
            trace.append("No unresolved material decision detected by analyst")
            return QuestionGenerationResult(
                status=QuestionGenerationStatus.NO_QUESTION_REQUIRED,
                reason=proposal.rationale or "No unresolved material decision detected",
                remaining_budget=budget,
                proposal=proposal,
                trace=trace,
            )

        # 6. Deterministic Importance Policy Gate
        imp_raw = str(proposal.importance or "").upper()
        try:
            importance = QuestionImportance(imp_raw)
        except ValueError:
            trace.append(f"Invalid importance '{imp_raw}' proposed by LLM")
            return QuestionGenerationResult(
                status=QuestionGenerationStatus.FAILED,
                reason=f"Invalid importance level '{imp_raw}' proposed",
                remaining_budget=budget,
                proposal=proposal,
                validation_issues=[f"Invalid importance: {imp_raw}"],
                trace=trace,
            )

        if importance == QuestionImportance.LOW:
            trace.append("Proposed decision is LOW importance; rejected by user-facing policy")
            return QuestionGenerationResult(
                status=QuestionGenerationStatus.NO_QUESTION_REQUIRED,
                reason="Proposed decision has LOW importance; rejected by user-facing policy",
                remaining_budget=budget,
                proposal=proposal,
                trace=trace,
            )

        if importance == QuestionImportance.MEDIUM and not allow_medium:
            trace.append("Proposed decision is MEDIUM importance; rejected (allow_medium=False)")
            return QuestionGenerationResult(
                status=QuestionGenerationStatus.NO_QUESTION_REQUIRED,
                reason="Proposed decision has MEDIUM importance; resolved internally by policy",
                remaining_budget=budget,
                proposal=proposal,
                trace=trace,
            )

        # 7. Decision Type Validation
        dt_raw = str(proposal.decision_type or "").upper()
        try:
            decision_type = DecisionType(dt_raw)
        except ValueError:
            trace.append(f"Invalid decision_type '{dt_raw}' proposed by LLM")
            return QuestionGenerationResult(
                status=QuestionGenerationStatus.FAILED,
                reason=f"Invalid decision_type '{dt_raw}' proposed",
                remaining_budget=budget,
                proposal=proposal,
                validation_issues=[f"Invalid decision_type: {dt_raw}"],
                trace=trace,
            )

        # 8. Deterministic Deduplication against already asked / answered decisions
        if self._is_duplicate_decision(proposal, asked, answered):
            trace.append(f"Proposed decision '{decision_type.value}' matches an already asked or answered decision")
            return QuestionGenerationResult(
                status=QuestionGenerationStatus.NO_QUESTION_REQUIRED,
                reason=f"Decision '{decision_type.value}' has already been asked or answered",
                remaining_budget=budget,
                proposal=proposal,
                trace=trace,
            )

        # 9. Options Bounding (Strict 2 to 4 options for QuestionGenerator)
        raw_options = proposal.options or []
        if len(raw_options) < 2:
            trace.append(f"Proposed options count ({len(raw_options)}) is less than 2")
            return QuestionGenerationResult(
                status=QuestionGenerationStatus.FAILED,
                reason=f"Question proposal must have at least 2 options (found {len(raw_options)})",
                remaining_budget=budget,
                proposal=proposal,
                validation_issues=[f"Options count too low: {len(raw_options)}"],
                trace=trace,
            )

        if len(raw_options) > 4:
            trace.append(f"Proposed options count ({len(raw_options)}) exceeds maximum 4 options")
            return QuestionGenerationResult(
                status=QuestionGenerationStatus.FAILED,
                reason=f"Question proposal exceeds maximum 4 options (found {len(raw_options)})",
                remaining_budget=budget,
                proposal=proposal,
                validation_issues=[f"Options count too high: {len(raw_options)} > 4"],
                trace=trace,
            )

        # 10. Assemble Structured Options
        options: list[QuestionOption] = []
        rec_id = proposal.recommended_option_id
        for idx, opt_dict in enumerate(raw_options):
            oid = str(opt_dict.get("option_id", f"opt-{idx+1}"))
            is_rec = bool(rec_id and oid == rec_id)
            rec_reason = proposal.recommendation_reason if is_rec else None

            options.append(
                QuestionOption(
                    option_id=oid,
                    label=str(opt_dict.get("label", "")),
                    description=str(opt_dict.get("description", "")),
                    rationale=str(opt_dict.get("rationale", "")),
                    recommended=is_rec,
                    recommendation_reason=rec_reason,
                )
            )

        # 11. Build ResearchQuestion Candidate
        seq_num = (len(asked) + 1)
        question_candidate = ResearchQuestion(
            question_id=new_id("rq-dec"),
            research_request_id=request.request_id,
            sequence_number=seq_num,
            question=str(proposal.question or "").strip(),
            context=str(proposal.context or "").strip(),
            decision_type=decision_type,
            importance=importance,
            state=QuestionState.QUESTION_PENDING,
            options=options,
            custom_answer_allowed=True,
            required=True,
            impact=str(proposal.impact or "").strip(),
            rationale=str(proposal.rationale or "").strip(),
            provenance=QuestionProvenance(
                research_request_id=request.request_id,
                research_intent_id=getattr(intent, "intent_id", None),
                unresolved_decision_id=proposal.unresolved_decision_id,
                source_field="research_planning",
                rationale=str(proposal.rationale or ""),
            ),
        )

        # 12. Gate Candidate through QuestionValidator
        issues = QuestionValidator.validate_question(
            question=question_candidate,
            allow_medium=allow_medium,
            raise_on_error=False,
        )

        if issues:
            trace.append(f"Validation failed with {len(issues)} issues: {'; '.join(issues)}")
            return QuestionGenerationResult(
                status=QuestionGenerationStatus.FAILED,
                reason=f"Question validation failed with {len(issues)} issues",
                remaining_budget=budget,
                proposal=proposal,
                validation_issues=issues,
                trace=trace,
            )

        trace.append(
            f"Successfully generated ResearchQuestion '{question_candidate.question_id}' "
            f"(seq: {seq_num}, type: {decision_type.value}, importance: {importance.value})"
        )

        return QuestionGenerationResult(
            status=QuestionGenerationStatus.QUESTION_GENERATED,
            question=question_candidate,
            reason="Unresolved high-impact decision requires user clarification",
            remaining_budget=budget,
            proposal=proposal,
            trace=trace,
        )

    def _build_prompt(
        self,
        request: ResearchRequest,
        intent: Optional[ResearchIntent],
        already_asked: list[ResearchQuestion],
        already_answered: list[str],
        remaining_budget: int,
    ) -> str:
        """Construct structured user prompt for the decision analyst LLM."""
        sections: list[str] = [
            f"Research Request ID: {request.request_id}",
            f"Objective: {request.objective}",
        ]

        if request.questions:
            sections.append(f"Explicit Sub-Questions:\n" + "\n".join(f"- {q}" for q in request.questions))

        if request.constraints:
            sections.append(f"Explicit Constraints:\n" + "\n".join(f"- {c}" for c in request.constraints))

        if intent is not None:
            sections.append(
                f"Intent Types: {[t.value if isinstance(t, Enum) else str(t) for t in intent.intent_types]}\n"
                f"Subjects: {intent.subjects}\n"
                f"Entities: {intent.entities}\n"
                f"Comparison Targets: {intent.comparison_targets}\n"
                f"Research Dimensions: {intent.research_dimensions}\n"
                f"Inferred Constraints: {intent.inferred_constraints}\n"
                f"Assumptions: {intent.assumptions}"
            )
            if intent.ambiguities:
                sections.append(
                    "Identified Ambiguities:\n" + "\n".join(
                        f"- [{a.ambiguity_id}] {a.description} (Impact: {a.impact})"
                        for a in intent.ambiguities
                    )
                )

        if already_asked:
            sections.append(
                "Questions Already Asked in this Research Task:\n" + "\n".join(
                    f"- [{q.decision_type.value}] {q.question}" for q in already_asked
                )
            )

        if already_answered:
            sections.append(
                "Decisions Already Answered/Resolved:\n" + "\n".join(
                    f"- {ans}" for ans in already_answered
                )
            )

        sections.append(
            f"Remaining User Question Budget: {remaining_budget} (Max 5 per task)\n"
            f"Analyze whether an unresolved HIGH or CRITICAL decision exists. Output JSON only."
        )

        return "\n\n".join(sections)

    def _parse_response(self, text: str) -> Optional[QuestionProposal]:
        """Strip markdown fences and parse structured JSON into QuestionProposal."""
        if not text or not text.strip():
            return None

        cleaned = text.strip()
        # Strip markdown code blocks
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Fallback regex search for { ... }
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

        return QuestionProposal(
            has_unresolved_decision=bool(data.get("has_unresolved_decision", False)),
            decision_type=data.get("decision_type"),
            importance=data.get("importance"),
            question=data.get("question"),
            context=data.get("context"),
            impact=data.get("impact"),
            rationale=data.get("rationale"),
            options=list(data.get("options", [])),
            recommended_option_id=data.get("recommended_option_id"),
            recommendation_reason=data.get("recommendation_reason"),
            unresolved_decision_id=data.get("unresolved_decision_id"),
            raw_response=text,
        )

    def _is_duplicate_decision(
        self,
        proposal: QuestionProposal,
        asked: list[ResearchQuestion],
        answered: list[str],
    ) -> bool:
        """Deterministic check for whether the proposed decision has already been addressed."""
        dt = (proposal.decision_type or "").upper()
        prop_text = (proposal.question or "").strip().lower()
        dec_id = (proposal.unresolved_decision_id or "").strip().lower()

        # 1. Check against asked questions
        for q in asked:
            # Identical unresolved decision ID
            if dec_id and q.provenance and q.provenance.unresolved_decision_id:
                if dec_id == q.provenance.unresolved_decision_id.strip().lower():
                    return True
            # Same decision type AND substantially identical question text
            if q.decision_type.value == dt:
                q_text = q.question.strip().lower()
                if prop_text and (prop_text == q_text or prop_text in q_text or q_text in prop_text):
                    return True

        # 2. Check against answered records
        for ans in answered:
            ans_clean = ans.strip().lower()
            # Check decision type match when not differentiated by distinct decision ID
            if dt and (ans_clean == dt.lower() or ans_clean.startswith(f"{dt.lower()}:")):
                if not dec_id or dec_id in ans_clean:
                    return True
            if dec_id and (dec_id == ans_clean or f":{dec_id}" in ans_clean or f"{dec_id}:" in ans_clean):
                return True
            if prop_text and (prop_text in ans_clean or ans_clean in prop_text):
                return True

        return False

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

        raise RuntimeError("No InferenceGateway or BaseInferenceProvider configured for QuestionGenerator.")

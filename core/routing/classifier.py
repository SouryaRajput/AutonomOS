from __future__ import annotations

import json
import logging
import re
from typing import Optional
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
from core.routing.model import RoutingContext, RoutingProposal
from core.routing.signals import DeterministicSignalResult

logger = logging.getLogger("AutonomOS.LLMRoutingClassifier")


class RoutingClassificationError(AutonomOSError):
    """Raised when routing inference fails or unrecoverable provider errors occur."""
    def __init__(self, message: str, code: str = "ROUTING_CLASSIFICATION_ERROR"):
        super().__init__(message, code=code)


class LLMRoutingClassifier:
    """
    LLM-powered routing classifier.
    Produces untrusted RoutingProposal objects strictly as an analyst.
    Never mutates runtime state, activates workers, or launches crawlers.
    """

    SYSTEM_PROMPT = """You are the AutonomOS Request Routing Analyst.
Your sole responsibility is to classify incoming user or Manager requests into the most appropriate, cost-effective execution route.

Allowed Routes:
1. DIRECT_ACTION: Direct UI command, simple tool invocation, or immediate execution that does not require deep research or code modification (e.g., "Move that card to center", "Click the submit button", "View log file").
2. MANAGER_REASONING: High-level architectural reasoning, tradeoff evaluation between existing approaches, or policy arbitration (e.g., "Which of these two approaches is cleaner?", "Review our current design").
3. RESEARCH: External information gathering, web search, documentation crawling, competitive benchmarks, or evidence synthesis (e.g., "Compare PostgreSQL and MongoDB for our application", "What is the current price of X?").
4. SPECIALIZED_WORKER: Delegating a technical engineering task to a designated specialist worker (e.g., "Run the test suite" -> worker.tester, "Implement this function" -> worker.programmer).
5. CLARIFICATION: Ambiguous, underspecified, or deictic requests that cannot safely proceed without user clarification (e.g., "Move that there", "Fix it").
6. MULTI_STAGE: Compound requests requiring sequential execution phases, such as research followed by implementation (e.g., "Research the best database for our app and then update the project").

Routing Principles:
- Prefer the cheapest capable path that satisfies the request with sufficient confidence.
- Research is NOT the default route; only choose RESEARCH when external information or evidence synthesis is genuinely needed.
- If deictic words ("that", "there", "it") cannot be resolved from context, choose CLARIFICATION.

STRICT PROHIBITIONS:
- Do NOT execute tools.
- Do NOT activate workers or the Researcher.
- Do NOT spawn crawlers.
- Do NOT formulate research plans or search queries.
- Do NOT answer the research question.

Output Requirement:
You must respond with ONLY a valid JSON object with the following schema:
{
  "route": "DIRECT_ACTION" | "MANAGER_REASONING" | "RESEARCH" | "SPECIALIZED_WORKER" | "CLARIFICATION" | "MULTI_STAGE",
  "confidence": float (0.0 to 1.0),
  "reason": string,
  "requires_external_information": boolean,
  "requires_research_evidence": boolean,
  "requires_project_context": boolean,
  "requires_tool_execution": boolean,
  "target_worker": string or null,
  "suggested_stages": [{"stage": int, "route": string, "description": string}],
  "ambiguities": [{"description": string, "impact": string}],
  "clarification_required": boolean,
  "clarification_questions": [string]
}
"""

    def __init__(
        self,
        gateway: Optional[InferenceGateway] = None,
        provider: Optional[BaseInferenceProvider] = None,
        model_id: str = "routing-analyst-model",
    ):
        self.gateway = gateway
        self.provider = provider
        self.model_id = model_id

    def classify(
        self,
        text: str,
        context: Optional[RoutingContext] = None,
        signals: Optional[DeterministicSignalResult] = None,
    ) -> RoutingProposal:
        """Call inference provider to generate an untrusted RoutingProposal."""
        user_prompt = self._build_prompt(text, context, signals)

        inf_req = InferenceRequest(
            request_id=f"inf-route-{uuid.uuid4().hex[:8]}",
            project_id=context.project_id if context and context.project_id else "global",
            task_id="routing-task",
            worker_id="request-router",
            messages=[
                InferenceMessage(role="system", content=self.SYSTEM_PROMPT),
                InferenceMessage(role="user", content=user_prompt),
            ],
            requirements=ModelRequirement(
                required_capabilities={ModelCapability.TEXT_GENERATION, ModelCapability.STRUCTURED_OUTPUT},
                routing_profile=RoutingProfile.BEST_AVAILABLE,
            ),
            temperature=0.1,
            timeout_seconds=30,
        )

        inf_resp = self._call_inference(inf_req)
        return self._parse_response(inf_resp.content, model_used=inf_resp.model_used, provider_used=inf_resp.provider_used)

    def _call_inference(self, inf_req: InferenceRequest) -> InferenceResponse:
        """Execute request against Gateway or Provider."""
        if self.gateway is not None:
            try:
                return self.gateway.execute(inf_req)
            except Exception as e:
                raise RoutingClassificationError(f"Inference gateway error during routing: {e}") from e

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
                raise RoutingClassificationError(f"Inference provider error during routing: {e}") from e

        raise RoutingClassificationError("No InferenceGateway or BaseInferenceProvider configured for LLMRoutingClassifier.")

    def _build_prompt(
        self,
        text: str,
        context: Optional[RoutingContext] = None,
        signals: Optional[DeterministicSignalResult] = None,
    ) -> str:
        """Assemble structured prompt with request context and deterministic cues."""
        lines = [
            f"Classify the following request:\n\"{text}\"\n",
        ]

        if context:
            if context.available_workers:
                lines.append(f"Available Workers: {context.available_workers}")
            if context.known_entities:
                lines.append(f"Known Entities/Context: {context.known_entities}")
            if context.project_id:
                lines.append(f"Active Project ID: {context.project_id}")

        if signals and signals.matched_rules:
            lines.append(f"Deterministic Cues: {signals.matched_rules}")
            if signals.suggested_route:
                lines.append(f"Deterministic Prior Route: {signals.suggested_route.value} (confidence: {signals.confidence})")

        lines.append("\nReturn JSON only.")
        return "\n".join(lines)

    def _parse_response(self, raw_content: str, model_used: Optional[str] = None, provider_used: Optional[str] = None) -> RoutingProposal:
        """Parse raw model text into a RoutingProposal, stripping code fences."""
        cleaned = raw_content.strip()
        # Strip markdown fences
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            cleaned = cleaned.strip()

        # Extract JSON substring if surrounded by prose
        match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.warning(f"Malformed JSON in routing classifier response: {e}")
            return RoutingProposal(
                route="",
                confidence=0.0,
                reason=f"Malformed model response: {e}",
                raw_response=raw_content,
                model_used=model_used,
                provider_used=provider_used,
            )

        return RoutingProposal(
            route=str(data.get("route", "")),
            confidence=float(data.get("confidence", 0.5)),
            reason=str(data.get("reason", "")),
            requires_external_information=bool(data.get("requires_external_information", False)),
            requires_research_evidence=bool(data.get("requires_research_evidence", False)),
            requires_project_context=bool(data.get("requires_project_context", False)),
            requires_tool_execution=bool(data.get("requires_tool_execution", False)),
            target_worker=data.get("target_worker"),
            suggested_stages=list(data.get("suggested_stages", [])),
            ambiguities=list(data.get("ambiguities", [])),
            clarification_required=bool(data.get("clarification_required", False)),
            clarification_questions=[str(q) for q in data.get("clarification_questions", [])],
            model_used=model_used,
            provider_used=provider_used,
            raw_response=raw_content,
        )

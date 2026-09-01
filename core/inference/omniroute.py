from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from core.errors import AutonomOSError
from core.inference.model import (
    InferenceRequest,
    ModelMetadata,
    ModelRequirement,
    RoutingCandidate,
    RoutingDecision,
)
from core.inference.types import ModelCapability, ProviderHealthStatus, RoutingProfile

if TYPE_CHECKING:
    from core.inference.gateway import CircuitBreaker
    from core.inference.registry import ModelRegistry, ProviderRegistry

logger = logging.getLogger("AutonomOS.OmniRoute")


class NoProviderAvailableError(AutonomOSError):
    """Raised when no compatible provider/model is available to satisfy an inference request."""

    def __init__(self, request_id: str, rejected_reasons: dict[str, list[str]], rationale: str):
        details = "\n".join([f"- {m}: {', '.join(reasons)}" for m, reasons in rejected_reasons.items()])
        msg = f"No compatible inference provider available for request '{request_id}'.\nRationale: {rationale}\nRejected candidates:\n{details}"
        super().__init__("NO_PROVIDER_AVAILABLE", msg)
        self.request_id = request_id
        self.rejected_reasons = rejected_reasons
        self.rationale = rationale


class OmniRoute:
    """
    Deterministic Model & Provider Routing Engine.
    Filters, evaluates, scores, and selects optimal models based on explicit
    worker capabilities, constraints, latency, cost, and routing profiles.
    """

    @classmethod
    def evaluate_route(
        cls,
        request: InferenceRequest,
        model_registry: ModelRegistry,
        provider_registry: ProviderRegistry,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ) -> RoutingDecision:
        """
        Evaluate all available models and return an explainable, ranked RoutingDecision.
        """
        reqs = request.requirements
        all_models = model_registry.list_models()
        estimated_input_toks = request.estimated_input_tokens()

        eligible_candidates: list[RoutingCandidate] = []
        rejected_candidates: dict[str, list[str]] = {}

        for model in all_models:
            reasons: list[str] = []

            # 1. Provider registered & active
            try:
                provider = provider_registry.get_provider(model.provider_id)
            except Exception:
                rejected_candidates[model.model_id] = [f"Provider '{model.provider_id}' is not registered."]
                continue

            # 2. Circuit Breaker / Health Check
            if circuit_breaker and circuit_breaker.is_open(model.provider_id):
                rejected_candidates[model.model_id] = [f"Provider '{model.provider_id}' circuit breaker is OPEN."]
                continue

            if provider.health() == ProviderHealthStatus.UNAVAILABLE:
                rejected_candidates[model.model_id] = [f"Provider '{model.provider_id}' reports UNAVAILABLE."]
                continue

            # 3. Provider Exclusions & Model Exclusions
            if model.provider_id in reqs.excluded_providers:
                rejected_candidates[model.model_id] = [f"Provider '{model.provider_id}' explicitly excluded by request."]
                continue

            if model.model_id in reqs.excluded_models:
                rejected_candidates[model.model_id] = [f"Model '{model.model_id}' explicitly excluded by request."]
                continue

            # 4. Capability Compatibility Validation
            missing_caps = [c.value for c in reqs.required_capabilities if c not in model.capabilities]
            if missing_caps:
                rejected_candidates[model.model_id] = [f"Missing required capabilities: {missing_caps}"]
                continue

            # 5. Context Window Validation
            needed_context = reqs.minimum_context + estimated_input_toks
            if model.context_window < needed_context:
                rejected_candidates[model.model_id] = [
                    f"Insufficient context window ({model.context_window} < required {needed_context})."
                ]
                continue

            # 6. Cost Limit Validation
            estimated_cost = (estimated_input_toks / 1_000_000.0) * model.input_cost_per_million + (
                (request.max_output_tokens or 500) / 1_000_000.0
            ) * model.output_cost_per_million

            if reqs.maximum_cost is not None and estimated_cost > reqs.maximum_cost:
                rejected_candidates[model.model_id] = [
                    f"Estimated cost ${estimated_cost:.4f} exceeds maximum allowed ${reqs.maximum_cost:.4f}."
                ]
                continue

            # 7. Latency Limit Validation
            if reqs.maximum_latency_ms is not None and model.latency_p50_ms > reqs.maximum_latency_ms:
                rejected_candidates[model.model_id] = [
                    f"Expected latency {model.latency_p50_ms}ms exceeds maximum {reqs.maximum_latency_ms}ms."
                ]
                continue

            # Candidate is eligible! Compute score based on routing profile
            score, score_reasons = cls._compute_candidate_score(model, request, estimated_cost)
            eligible_candidates.append(
                RoutingCandidate(
                    model=model,
                    score=score,
                    estimated_cost=estimated_cost,
                    reasons=score_reasons,
                )
            )

        # Sort eligible candidates deterministically:
        # 1. Score (DESC)
        # 2. Quality Score (DESC)
        # 3. Model ID (ASC - tie breaker)
        eligible_candidates.sort(
            key=lambda c: (c.score, c.model.quality_score, -len(c.model.model_id), c.model.model_id),
            reverse=True,
        )

        selected_model: Optional[ModelMetadata] = eligible_candidates[0].model if eligible_candidates else None

        if selected_model:
            top_cand = eligible_candidates[0]
            rationale = (
                f"Selected model '{selected_model.model_id}' (Score: {top_cand.score:.1f}, "
                f"Profile: {reqs.routing_profile.value}, Reasons: {'; '.join(top_cand.reasons)})"
            )
        else:
            rationale = f"No eligible model satisfied all requirements ({len(rejected_candidates)} candidates rejected)."

        return RoutingDecision(
            request_id=request.request_id,
            selected_model=selected_model,
            candidate_scores=eligible_candidates,
            rejected_candidates=rejected_candidates,
            rationale=rationale,
        )

    @classmethod
    def _compute_candidate_score(
        cls,
        model: ModelMetadata,
        request: InferenceRequest,
        estimated_cost: float,
    ) -> tuple[float, list[str]]:
        reqs = request.requirements
        profile = reqs.routing_profile
        reasons: list[str] = []
        score = 0.0

        if profile == RoutingProfile.FREE_FIRST:
            if model.is_free_tier:
                score += 10000.0
                reasons.append("Free tier prioritized (+10000)")
            else:
                score += (100.0 / (model.input_cost_per_million + 0.01))
            score += model.quality_score * 5.0
            score -= model.latency_p50_ms / 50.0

        elif profile == RoutingProfile.CHEAPEST:
            unit_cost = model.input_cost_per_million + model.output_cost_per_million
            score += 1000.0 / (unit_cost + 0.001)
            score += model.quality_score * 2.0
            reasons.append(f"Lowest cost scored (${unit_cost:.2f}/M tokens)")

        elif profile == RoutingProfile.FASTEST:
            score += 10000.0 / (model.latency_p50_ms + 1.0)
            score += model.quality_score * 2.0
            reasons.append(f"Lowest latency scored ({model.latency_p50_ms:.0f}ms)")

        elif profile in (RoutingProfile.QUALITY_FIRST, RoutingProfile.BEST_AVAILABLE):
            score += model.quality_score * 20.0
            score -= model.input_cost_per_million * 2.0
            score -= model.latency_p50_ms / 100.0
            reasons.append(f"High quality score prioritized ({model.quality_score:.1f}/100)")

        elif profile == RoutingProfile.CODE:
            score += model.quality_score * 20.0
            if ModelCapability.CODE_GENERATION in model.capabilities:
                score += 500.0
                reasons.append("Specialized code generation capability (+500)")
            score -= model.input_cost_per_million

        elif profile == RoutingProfile.VISION:
            score += model.quality_score * 20.0
            if ModelCapability.VISION in model.capabilities:
                score += 1000.0
                reasons.append("Specialized vision capability (+1000)")

        elif profile == RoutingProfile.LONG_CONTEXT:
            score += (model.context_window / 1000.0) * 10.0
            score += model.quality_score * 5.0
            reasons.append(f"Large context window prioritized ({model.context_window} tokens)")

        # Preferred provider bonus
        if model.provider_id in reqs.preferred_providers:
            score += 500.0
            reasons.append(f"Preferred provider '{model.provider_id}' bonus (+500)")

        # Preferred model bonus
        if model.model_id in reqs.preferred_models:
            score += 300.0
            reasons.append(f"Preferred model '{model.model_id}' bonus (+300)")

        # Preferred capabilities bonus
        for pref_cap in reqs.preferred_capabilities:
            if pref_cap in model.capabilities:
                score += 150.0
                reasons.append(f"Preferred capability '{pref_cap.value}' bonus (+150)")

        return round(score, 2), reasons

    @classmethod
    def escalate_requirements_for_complexity(
        cls,
        base_requirements: ModelRequirement,
        is_failure_or_replan: bool = False,
        uncertainty_level: Optional[str] = None,
        task_risk: Optional[str] = None,
    ) -> ModelRequirement:
        """
        Dynamically escalates inference requirements and routing profiles when tasks fail verification,
        require architectural replanning, or carry high risk/uncertainty.
        """
        escalated_caps = set(base_requirements.required_capabilities)
        escalated_profile = base_requirements.routing_profile

        if is_failure_or_replan or uncertainty_level in ("HIGH_RISK_UNCERTAINTY", "NEEDS_INFORMATION") or task_risk in ("HIGH", "CRITICAL"):
            escalated_profile = RoutingProfile.QUALITY_FIRST
            escalated_caps.add(ModelCapability.REASONING)
            escalated_caps.add(ModelCapability.STRUCTURED_OUTPUT)

        return ModelRequirement(
            required_capabilities=escalated_caps,
            preferred_capabilities=base_requirements.preferred_capabilities,
            routing_profile=escalated_profile,
            minimum_context=base_requirements.minimum_context,
            maximum_cost=base_requirements.maximum_cost,
            maximum_latency_ms=None if is_failure_or_replan else base_requirements.maximum_latency_ms,
            preferred_providers=base_requirements.preferred_providers,
            excluded_providers=base_requirements.excluded_providers,
            preferred_models=base_requirements.preferred_models,
            excluded_models=base_requirements.excluded_models,
        )


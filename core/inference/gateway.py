from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Callable, Optional
import uuid

from core.errors import AutonomOSError
from core.events.model import Event
from core.events.types import EventSource, EventType
from core.inference.model import (
    Cost,
    InferenceRequest,
    InferenceResponse,
    ModelMetadata,
    RoutingDecision,
    Usage,
    utc_now,
)
from core.inference.omniroute import NoProviderAvailableError, OmniRoute
from core.inference.provider import BaseInferenceProvider
from core.inference.registry import ModelRegistry, ProviderRegistry
from core.inference.secrets import EnvSecretStore, SecretStore, redact_secret_text
from core.inference.types import (
    CostType,
    InferenceErrorCode,
    InferenceRequestStatus,
    ProviderHealthStatus,
)

logger = logging.getLogger("AutonomOS.InferenceGateway")


class CircuitBreaker:
    """
    In-memory Circuit Breaker preventing repeated calls to failing inference providers.
    Transitions: CLOSED -> OPEN (cooldown) -> HALF-OPEN -> CLOSED.
    """

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._consecutive_failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def is_open(self, provider_id: str) -> bool:
        until = self._open_until.get(provider_id, 0.0)
        now = time.time()
        if now < until:
            return True
        # If cooldown expired, transition to HALF-OPEN (allow test request)
        if until > 0.0:
            self._open_until.pop(provider_id, None)
        return False

    def record_success(self, provider_id: str) -> None:
        self._consecutive_failures[provider_id] = 0
        self._open_until.pop(provider_id, None)

    def record_failure(self, provider_id: str) -> None:
        count = self._consecutive_failures.get(provider_id, 0) + 1
        self._consecutive_failures[provider_id] = count
        if count >= self.failure_threshold:
            self._open_until[provider_id] = time.time() + self.cooldown_seconds
            logger.warning("Circuit breaker OPEN for provider '%s' for %.0fs", provider_id, self.cooldown_seconds)


class InferenceGateway:
    """
    Universal Inference Gateway (OmniRoute).
    Controls model selection, provider communication, fallback, retries,
    circuit breakers, usage tracking, and security credential isolation.
    """

    def __init__(
        self,
        provider_registry: Optional[ProviderRegistry] = None,
        model_registry: Optional[ModelRegistry] = None,
        secret_store: Optional[SecretStore] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        event_logger: Optional[Callable[..., Event]] = None,
        max_fallback_attempts: int = 3,
    ):
        self.providers = provider_registry or ProviderRegistry()
        self.models = model_registry or ModelRegistry()
        self.secrets = secret_store or EnvSecretStore()
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.event_logger = event_logger
        self.max_fallback_attempts = max_fallback_attempts

    def execute(
        self,
        request: InferenceRequest,
        causation_id: Optional[str] = None,
    ) -> InferenceResponse:
        """
        Execute an inference request through OmniRoute routing and fallback.
        """
        start_time = time.perf_counter()

        # 1. Emit INFERENCE_REQUESTED
        req_evt = self._emit_event(
            event_type=EventType.INFERENCE_REQUESTED,
            payload={
                "request_id": request.request_id,
                "project_id": request.project_id,
                "task_id": request.task_id,
                "worker_id": request.worker_id,
                "routing_profile": request.requirements.routing_profile.value,
                "required_capabilities": [c.value for c in request.requirements.required_capabilities],
                "messages_count": len(request.messages),
                "estimated_input_tokens": request.estimated_input_tokens(),
            },
            project_id=request.project_id,
            task_id=request.task_id,
            worker_id=request.worker_id,
            correlation_id=request.task_id,
            causation_id=causation_id,
        )
        current_causation = req_evt.event_id if req_evt else causation_id

        # 2. Evaluate Candidates with OmniRoute
        decision: RoutingDecision = OmniRoute.evaluate_route(
            request=request,
            model_registry=self.models,
            provider_registry=self.providers,
            circuit_breaker=self.circuit_breaker,
        )

        if not decision.candidate_scores or not decision.selected_model:
            raise NoProviderAvailableError(
                request_id=request.request_id,
                rejected_reasons=decision.rejected_candidates,
                rationale=decision.rationale,
            )

        # 3. Attempt Execution Across Candidates (OmniRoute Fallback)
        attempts = 0
        last_error: Optional[Exception] = None
        failed_providers_in_request: set[str] = set()

        for candidate in decision.candidate_scores:
            if attempts >= self.max_fallback_attempts:
                break

            model = candidate.model
            provider_id = model.provider_id

            # If this provider already failed during this request, skip remaining models on it
            if provider_id in failed_providers_in_request:
                continue

            attempts += 1

            try:
                provider = self.providers.get_provider(provider_id)
            except Exception as e:
                continue

            # Resolve API Key
            key_ref = self.providers.get_provider_key_ref(provider_id)
            secret_val = self.secrets.get_secret(key_ref) if key_ref else None

            # Emit INFERENCE_ROUTED
            self._emit_event(
                event_type=EventType.INFERENCE_ROUTED,
                payload={
                    "request_id": request.request_id,
                    "provider_id": provider_id,
                    "model_id": model.model_id,
                    "attempt": attempts,
                    "score": candidate.score,
                    "rationale": decision.rationale,
                },
                project_id=request.project_id,
                task_id=request.task_id,
                worker_id=request.worker_id,
                correlation_id=request.task_id,
                causation_id=current_causation,
            )

            # Emit INFERENCE_STARTED
            self._emit_event(
                event_type=EventType.INFERENCE_STARTED,
                payload={
                    "request_id": request.request_id,
                    "provider_id": provider_id,
                    "model_id": model.model_id,
                    "attempt": attempts,
                },
                project_id=request.project_id,
                task_id=request.task_id,
                worker_id=request.worker_id,
                correlation_id=request.task_id,
                causation_id=current_causation,
            )

            try:
                response = provider.generate(request=request, model=model, secret=secret_val)
                self.circuit_breaker.record_success(provider_id)

                # Redact sensitive secrets from output
                known_secrets = self.secrets.list_known_secret_values()
                if secret_val:
                    known_secrets.append(secret_val)
                response.content = redact_secret_text(response.content, known_secrets)

                # Emit INFERENCE_COMPLETED
                self._emit_event(
                    event_type=EventType.INFERENCE_COMPLETED,
                    payload={
                        "request_id": request.request_id,
                        "response_id": response.response_id,
                        "provider_id": provider_id,
                        "model_id": model.model_id,
                        "total_tokens": response.usage.total_tokens,
                        "total_cost": response.cost.total_cost,
                        "latency_ms": response.latency_ms,
                        "finish_reason": response.finish_reason,
                    },
                    project_id=request.project_id,
                    task_id=request.task_id,
                    worker_id=request.worker_id,
                    correlation_id=request.task_id,
                    causation_id=current_causation,
                )

                return response

            except Exception as err:
                last_error = err
                self.circuit_breaker.record_failure(provider_id)
                failed_providers_in_request.add(provider_id)

                # Clean error message without secrets
                clean_err_msg = redact_secret_text(str(err), self.secrets.list_known_secret_values())

                # Emit INFERENCE_FAILED
                self._emit_event(
                    event_type=EventType.INFERENCE_FAILED,
                    payload={
                        "request_id": request.request_id,
                        "provider_id": provider_id,
                        "model_id": model.model_id,
                        "attempt": attempts,
                        "error": clean_err_msg,
                    },
                    project_id=request.project_id,
                    task_id=request.task_id,
                    worker_id=request.worker_id,
                    correlation_id=request.task_id,
                    causation_id=current_causation,
                )

                # Emit INFERENCE_FALLBACK if there are more candidates
                if attempts < len(decision.candidate_scores) and attempts < self.max_fallback_attempts:
                    next_cand = decision.candidate_scores[attempts]
                    self._emit_event(
                        event_type=EventType.INFERENCE_FALLBACK,
                        payload={
                            "request_id": request.request_id,
                            "failed_provider": provider_id,
                            "failed_model": model.model_id,
                            "fallback_provider": next_cand.model.provider_id,
                            "fallback_model": next_cand.model.model_id,
                            "attempt": attempts + 1,
                        },
                        project_id=request.project_id,
                        task_id=request.task_id,
                        worker_id=request.worker_id,
                        correlation_id=request.task_id,
                        causation_id=current_causation,
                    )

        # All attempts exhausted
        raise NoProviderAvailableError(
            request_id=request.request_id,
            rejected_reasons={c.model.model_id: [f"Execution failed after attempt: {last_error}"] for c in decision.candidate_scores[:attempts]},
            rationale=f"All {attempts} candidate provider(s) failed during execution: {last_error}",
        )

    def _emit_event(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> Optional[Event]:
        if self.event_logger:
            # Ensure no raw secret leaked in event payload
            clean_payload = {}
            known_secrets = self.secrets.list_known_secret_values()
            for k, v in payload.items():
                if isinstance(v, str):
                    clean_payload[k] = redact_secret_text(v, known_secrets)
                else:
                    clean_payload[k] = v

            try:
                return self.event_logger(
                    event_type=event_type,
                    payload=clean_payload,
                    source=EventSource.RUNTIME,
                    project_id=project_id,
                    task_id=task_id,
                    worker_id=worker_id,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                )
            except TypeError:
                from core.events.model import new_event_id
                evt = Event(
                    event_id=new_event_id(),
                    event_type=event_type,
                    source=EventSource.RUNTIME,
                    payload=clean_payload,
                    project_id=project_id,
                    task_id=task_id,
                    worker_id=worker_id,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                    timestamp=utc_now(),
                )
                return self.event_logger(evt)
        return None

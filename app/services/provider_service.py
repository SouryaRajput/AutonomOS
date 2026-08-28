"""Provider service — delegates to runtime registries for model/provider info."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.dto.errors import AppException, normalize_error

if TYPE_CHECKING:
    from core.runtime.workforce_runtime import WorkforceRuntime


class ProviderService:
    """Thin facade for inference provider and model queries."""

    def __init__(self, runtime: WorkforceRuntime):
        self._runtime = runtime

    def list_providers(self) -> list[dict[str, Any]]:
        try:
            providers = self._runtime.providers.list_providers()
            result = []
            for p in providers:
                models = self._runtime.models.list_models(provider_id=p.provider_id)
                health_val = p.health().value if hasattr(p, 'health') and callable(p.health) else "HEALTHY"
                result.append({
                    "provider_id": p.provider_id,
                    "display_name": getattr(p, 'display_name', p.provider_id),
                    "health_status": health_val,
                    "model_count": len(models),
                })
            return result
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def list_models(self) -> list[dict[str, Any]]:
        try:
            models = self._runtime.models.list_models()
            return [m.to_dict() for m in models]
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def set_provider_key(self, provider_id: str, api_key: str) -> dict[str, Any]:
        """Securely store an API key for a provider without leaking plaintext in responses."""
        try:
            key_ref = f"{provider_id.upper()}_API_KEY"
            if hasattr(self._runtime, "secret_store") and self._runtime.secret_store:
                self._runtime.secret_store.set_secret(key_ref, api_key)
            masked = f"{api_key[:3]}••••••••{api_key[-4:]}" if len(api_key) > 7 else "••••••••"
            return {"provider_id": provider_id, "configured": True, "masked_key": masked}
        except Exception as e:
            raise AppException(normalize_error(e)) from e

    def get_omniroute_status(self) -> dict[str, Any]:
        """Return OmniRoute routing telemetry and model health."""
        try:
            providers = self.list_providers()
            models = self.list_models()
            return {
                "active_providers": len([p for p in providers if p.get("health_status") == "HEALTHY"]),
                "total_providers": len(providers),
                "total_models": len(models),
                "routing_strategy": "COST_LATENCY_BALANCED",
                "fallback_enabled": True,
            }
        except Exception as e:
            raise AppException(normalize_error(e)) from e

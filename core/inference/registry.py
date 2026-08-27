from __future__ import annotations

import logging
import threading
from typing import Optional

from core.errors import AutonomOSError
from core.inference.model import ModelMetadata
from core.inference.provider import BaseInferenceProvider
from core.inference.types import ModelCapability

logger = logging.getLogger("AutonomOS.InferenceRegistry")


class ProviderNotFoundError(AutonomOSError):
    """Raised when an inference provider is not registered."""
    def __init__(self, provider_id: str):
        super().__init__("PROVIDER_NOT_FOUND", f"Inference Provider '{provider_id}' is not registered.")
        self.provider_id = provider_id


class ModelNotFoundError(AutonomOSError):
    """Raised when a requested model is not found in the ModelRegistry."""
    def __init__(self, model_id: str):
        super().__init__("MODEL_NOT_FOUND", f"Model '{model_id}' is not registered.")
        self.model_id = model_id


class ProviderRegistry:
    """Registry for managing and discovering active inference providers."""

    def __init__(self):
        self._providers: dict[str, BaseInferenceProvider] = {}
        self._key_refs: dict[str, Optional[str]] = {}
        self._lock = threading.RLock()

    def register_provider(
        self,
        provider: BaseInferenceProvider,
        api_key_ref: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._providers[provider.provider_id] = provider
            self._key_refs[provider.provider_id] = api_key_ref
            logger.info("Registered provider '%s' (key_ref=%s)", provider.provider_id, api_key_ref)

    def unregister_provider(self, provider_id: str) -> bool:
        with self._lock:
            if provider_id in self._providers:
                del self._providers[provider_id]
                self._key_refs.pop(provider_id, None)
                return True
            return False

    def get_provider(self, provider_id: str) -> BaseInferenceProvider:
        with self._lock:
            if provider_id not in self._providers:
                raise ProviderNotFoundError(provider_id)
            return self._providers[provider_id]

    def get_provider_key_ref(self, provider_id: str) -> Optional[str]:
        with self._lock:
            return self._key_refs.get(provider_id)

    def list_providers(self) -> list[BaseInferenceProvider]:
        with self._lock:
            return list(self._providers.values())


class ModelRegistry:
    """Registry for discovering, indexing, and filtering models across providers."""

    def __init__(self):
        self._models: dict[str, ModelMetadata] = {}
        self._lock = threading.RLock()

    def register_model(self, model: ModelMetadata) -> None:
        with self._lock:
            self._models[model.model_id] = model

    def unregister_model(self, model_id: str) -> bool:
        with self._lock:
            if model_id in self._models:
                del self._models[model_id]
                return True
            return False

    def get_model(self, model_id: str) -> ModelMetadata:
        with self._lock:
            if model_id not in self._models:
                raise ModelNotFoundError(model_id)
            return self._models[model_id]

    def list_models(
        self,
        provider_id: Optional[str] = None,
        capability: Optional[ModelCapability] = None,
        min_context: Optional[int] = None,
    ) -> list[ModelMetadata]:
        with self._lock:
            res: list[ModelMetadata] = []
            for m in self._models.values():
                if not m.active:
                    continue
                if provider_id and m.provider_id != provider_id:
                    continue
                if capability and capability not in m.capabilities:
                    continue
                if min_context and m.context_window < min_context:
                    continue
                res.append(m)
            return res

    def sync_from_providers(self, providers: list[BaseInferenceProvider]) -> None:
        with self._lock:
            for p in providers:
                try:
                    for m in p.list_models():
                        self._models[m.model_id] = m
                except Exception as e:
                    logger.warning("Failed to sync models from provider '%s': %s", p.provider_id, e)

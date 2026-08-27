from __future__ import annotations

from abc import ABC, abstractmethod
import os
import re
from typing import Optional


class SecretStore(ABC):
    """Abstract interface for secure API key and credential resolution."""

    @abstractmethod
    def get_secret(self, key_ref: str) -> Optional[str]:
        """Resolve a secret value by reference (e.g. 'env:OPENAI_API_KEY')."""
        pass

    @abstractmethod
    def set_secret(self, key_ref: str, value: str) -> None:
        """Store or override a secret value."""
        pass

    @abstractmethod
    def list_known_secret_values(self) -> list[str]:
        """Return non-empty secret values for redaction filtering."""
        pass


class EnvSecretStore(SecretStore):
    """
    Default environment-variable backed SecretStore.
    Resolves keys like 'env:KEY_NAME' or 'KEY_NAME' directly from os.environ.
    """

    def __init__(self, overrides: Optional[dict[str, str]] = None):
        self._overrides: dict[str, str] = dict(overrides or {})

    def get_secret(self, key_ref: str) -> Optional[str]:
        if not key_ref:
            return None
        clean_ref = key_ref.strip()
        if clean_ref.startswith("env:"):
            clean_ref = clean_ref[4:]

        if clean_ref in self._overrides:
            return self._overrides[clean_ref]

        return os.environ.get(clean_ref)

    def set_secret(self, key_ref: str, value: str) -> None:
        clean_ref = key_ref.strip()
        if clean_ref.startswith("env:"):
            clean_ref = clean_ref[4:]
        self._overrides[clean_ref] = value

    def list_known_secret_values(self) -> list[str]:
        vals = set()
        for v in self._overrides.values():
            if len(v) >= 6:
                vals.add(v)
        # Add common env vars if present
        for env_k in ["OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"]:
            v = os.environ.get(env_k)
            if v and len(v) >= 6:
                vals.add(v)
        return list(vals)


def redact_secret_text(text: str, secrets: list[str]) -> str:
    """Safely redact secret tokens and API keys from a string."""
    if not text:
        return ""
    redacted = text
    for sec in secrets:
        if sec and len(sec) >= 6:
            redacted = redacted.replace(sec, "[REDACTED_API_KEY]")
    # Redact generic bearer token / api key patterns
    redacted = re.sub(r'(?i)(bearer\s+|api[_-]?key["\']?\s*[:=]\s*["\']?)[a-zA-Z0-9_\-\.]{8,}', r'\1[REDACTED_API_KEY]', redacted)
    return redacted

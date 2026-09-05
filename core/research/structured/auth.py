"""
Structured Data Authentication and Credential Isolation Layer (Phase 1 / Part 7 / Step 7).

Guarantees:
- Credential Reference Pattern: Crawler tasks, reports, provenance, and logs NEVER store raw secrets.
- Secrets are resolved just-in-time from the existing SecretStore (core.inference.secrets).
- Sensitive headers, URLs, exceptions, and audit trails are deterministically redacted.
- Strict isolation prevents credential leakage across task transitions, serialization, and events.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Optional

from core.inference.secrets import EnvSecretStore, SecretStore, redact_secret_text
from core.research.errors import (
    StructuredDataAuthenticationError,
    StructuredDataValidationError,
)
from core.research.search.security import (
    REDACTED_STR,
    SENSITIVE_HEADER_KEYS,
    SENSITIVE_QUERY_PARAMS,
    sanitize_headers,
    sanitize_url,
)


# -----------------------------------------------------------------------------
# Credential Types & Reference Model
# -----------------------------------------------------------------------------

class CredentialType(str, Enum):
    """Supported structured data authentication schemes."""
    API_KEY = "api_key"
    BEARER_TOKEN = "bearer_token"
    BASIC_AUTH = "basic_auth"
    OAUTH = "oauth"
    CUSTOM_HEADER = "custom_header"

    @classmethod
    def from_string(cls, value: str | CredentialType) -> CredentialType:
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise StructuredDataValidationError("credential_type", f"Must be string or CredentialType, got {type(value)}")
        val = value.strip().lower()
        for member in cls:
            if member.value == val:
                return member
        raise StructuredDataValidationError(
            "credential_type",
            f"Unsupported credential type '{value}'. Supported: {', '.join(m.value for m in cls)}",
        )


@dataclass(frozen=True)
class CredentialReference:
    """
    Logical descriptor referencing a secret stored in the secret manager.
    Enforces that CrawlerTask, CrawlerReport, and provenance NEVER contain raw secrets.
    """
    ref_id: str
    credential_type: CredentialType = CredentialType.BEARER_TOKEN
    secret_key_ref: str = ""
    header_name: Optional[str] = None
    query_param_name: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.ref_id or not isinstance(self.ref_id, str) or not self.ref_id.strip():
            raise StructuredDataValidationError("ref_id", "CredentialReference ref_id cannot be empty.")
        if not self.secret_key_ref or not isinstance(self.secret_key_ref, str) or not self.secret_key_ref.strip():
            raise StructuredDataValidationError("secret_key_ref", "CredentialReference secret_key_ref cannot be empty.")

        ctype = CredentialType.from_string(self.credential_type)
        object.__setattr__(self, "ref_id", self.ref_id.strip())
        object.__setattr__(self, "secret_key_ref", self.secret_key_ref.strip())
        object.__setattr__(self, "credential_type", ctype)

        if self.header_name:
            object.__setattr__(self, "header_name", self.header_name.strip())
        if self.query_param_name:
            object.__setattr__(self, "query_param_name", self.query_param_name.strip())

    def to_dict(self) -> dict[str, Any]:
        """Serialize reference. Guarantees zero raw credentials in output."""
        return {
            "ref_id": self.ref_id,
            "credential_type": self.credential_type.value,
            "secret_key_ref": self.secret_key_ref,
            "header_name": self.header_name,
            "query_param_name": self.query_param_name,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CredentialReference:
        if not isinstance(data, dict):
            raise StructuredDataValidationError("credential_ref", f"Must be a dict, got {type(data).__name__}")
        return cls(
            ref_id=str(data.get("ref_id", "")),
            credential_type=CredentialType.from_string(data.get("credential_type", CredentialType.BEARER_TOKEN.value)),
            secret_key_ref=str(data.get("secret_key_ref", "")),
            header_name=data.get("header_name"),
            query_param_name=data.get("query_param_name"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class ResolvedCredentials:
    """
    In-memory container for resolved authentication artifacts.
    Guarantees that string representations mask secrets.
    """
    ref_id: str
    headers: dict[str, str] = field(default_factory=dict)
    query_params: dict[str, str] = field(default_factory=dict)
    _secret_val: str = field(default="", repr=False)

    def __repr__(self) -> str:
        return f"ResolvedCredentials(ref_id='{self.ref_id}', headers=[PROTECTED], query_params=[PROTECTED])"

    def __str__(self) -> str:
        return self.__repr__()


# -----------------------------------------------------------------------------
# Credential Resolver
# -----------------------------------------------------------------------------

class CredentialResolver:
    """
    Resolves CredentialReference instances into runtime authentication artifacts
    using an injected SecretStore.
    """

    def __init__(self, secret_store: Optional[SecretStore] = None):
        self.secret_store: SecretStore = secret_store or EnvSecretStore()

    def resolve(self, cred_ref: CredentialReference) -> ResolvedCredentials:
        """
        Resolve secret from SecretStore and format into HTTP headers or query parameters.
        Raises StructuredDataAuthenticationError if secret is missing or empty.
        """
        raw_secret = self.secret_store.get_secret(cred_ref.secret_key_ref)
        if not raw_secret or not raw_secret.strip():
            raise StructuredDataAuthenticationError(
                target=cred_ref.ref_id,
                status_code=401,
                message=f"Authentication credential '{cred_ref.secret_key_ref}' not found in SecretStore.",
                details={"ref_id": cred_ref.ref_id, "secret_key_ref": cred_ref.secret_key_ref},
            )

        secret = raw_secret.strip()
        headers: dict[str, str] = {}
        query_params: dict[str, str] = {}

        if cred_ref.credential_type in (CredentialType.BEARER_TOKEN, CredentialType.OAUTH):
            h_name = cred_ref.header_name or "Authorization"
            token_val = secret if secret.lower().startswith("bearer ") else f"Bearer {secret}"
            headers[h_name] = token_val

        elif cred_ref.credential_type == CredentialType.BASIC_AUTH:
            h_name = cred_ref.header_name or "Authorization"
            if secret.lower().startswith("basic "):
                auth_val = secret
            else:
                encoded = base64.b64encode(secret.encode("utf-8")).decode("ascii")
                auth_val = f"Basic {encoded}"
            headers[h_name] = auth_val

        elif cred_ref.credential_type == CredentialType.API_KEY:
            if cred_ref.query_param_name:
                query_params[cred_ref.query_param_name] = secret
            else:
                h_name = cred_ref.header_name or "X-API-Key"
                headers[h_name] = secret

        elif cred_ref.credential_type == CredentialType.CUSTOM_HEADER:
            h_name = cred_ref.header_name or "X-API-Key"
            headers[h_name] = secret

        return ResolvedCredentials(
            ref_id=cred_ref.ref_id,
            headers=headers,
            query_params=query_params,
            _secret_val=secret,
        )


# -----------------------------------------------------------------------------
# Credential Redaction Utility
# -----------------------------------------------------------------------------

_BEARER_REGEX = re.compile(r"(?i)(bearer\s+)[a-zA-Z0-9_\-\.]{6,}")
_BASIC_REGEX = re.compile(r"(?i)(basic\s+)[a-zA-Z0-9+/=]{6,}")
_QUERY_AUTH_REGEX = re.compile(r"(?i)(api[-_]?key|token|secret|password|auth|access[-_]?token)[=:][\s\"']*[a-zA-Z0-9_\-\.]{6,}")


class CredentialRedactor:
    """
    Comprehensive secret redactor for logs, reports, provenance, and exceptions.
    """

    def __init__(
        self,
        secret_store: Optional[SecretStore] = None,
        additional_secrets: Optional[list[str]] = None,
    ):
        self.secret_store = secret_store
        self._additional_secrets = list(additional_secrets or [])

    def get_known_secrets(self) -> list[str]:
        """Collect all known secret values for exact match replacement."""
        known = set(self._additional_secrets)
        if self.secret_store:
            for s in self.secret_store.list_known_secret_values():
                if s and len(s) >= 4:
                    known.add(s)
        return [s for s in known if len(s) >= 4]

    def redact_text(self, text: str) -> str:
        """Sanitize secret values from arbitrary text."""
        if not text or not isinstance(text, str):
            return ""

        redacted = text
        for sec in self.get_known_secrets():
            redacted = redacted.replace(sec, REDACTED_STR)

        redacted = _BEARER_REGEX.sub(rf"\1{REDACTED_STR}", redacted)
        redacted = _BASIC_REGEX.sub(rf"\1{REDACTED_STR}", redacted)
        redacted = _QUERY_AUTH_REGEX.sub(rf"\1={REDACTED_STR}", redacted)
        return redacted

    def redact_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Scrub authorization tokens and known secrets from header dictionary."""
        sanitized = sanitize_headers(headers)
        # In addition to header key sanitization, check header values for known secrets
        known = self.get_known_secrets()
        if known:
            for k, v in sanitized.items():
                if v != REDACTED_STR:
                    sanitized[k] = self.redact_text(v)
        return sanitized

    def redact_url(self, url: str) -> str:
        """Strip embedded credentials and sensitive query parameters from URL."""
        cleaned = sanitize_url(url)
        return self.redact_text(cleaned)

    def redact_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Recursively scrub known secrets and sensitive keys from a dictionary."""
        out: dict[str, Any] = {}
        for k, v in data.items():
            k_low = str(k).lower().strip()
            if k_low in SENSITIVE_HEADER_KEYS or k_low in SENSITIVE_QUERY_PARAMS or "secret" in k_low or "password" in k_low:
                out[k] = REDACTED_STR
            elif isinstance(v, str):
                out[k] = self.redact_text(v)
            elif isinstance(v, dict):
                out[k] = self.redact_dict(v)
            elif isinstance(v, list):
                out[k] = [
                    self.redact_dict(item) if isinstance(item, dict)
                    else (self.redact_text(item) if isinstance(item, str) else item)
                    for item in v
                ]
            else:
                out[k] = v
        return out

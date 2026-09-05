from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any, Optional, Union
import urllib.parse

from core.research.errors import (
    SearchSecurityError,
    StructuredDataPolicyViolationError,
)
from core.research.search.security import (
    sanitize_headers,
    sanitize_secret,
    sanitize_url,
    validate_network_target,
)
from core.research.structured.models import (
    HttpMethod,
    StructuredDataLimits,
    canonical_json_dumps,
)

logger = logging.getLogger("AutonomOS.Research.Structured.Policy")

# Binary executable magic signatures
_EXECUTABLE_BINARY_MAGIC: tuple[bytes, ...] = (
    b"\x7fELF",           # Linux ELF binary
    b"MZ",                # Windows DOS / PE binary
    b"\xfe\xed\xfa\xce",  # Mach-O 32-bit
    b"\xfe\xed\xfa\xcf",  # Mach-O 64-bit
    b"\xce\xfa\xed\xfe",  # Mach-O 32-bit byte-swapped
    b"\xcf\xfa\xed\xfe",  # Mach-O 64-bit byte-swapped
    b"\xca\xfe\xba\xbe",  # Java Class bytecode / Mach-O universal
)

# Text patterns indicating executable scripts or command injection triggers
_EXECUTABLE_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<\s*script[^>]*>", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"data\s*:\s*text/html", re.IGNORECASE),
    re.compile(r"\b(?:eval|exec)\s*\(", re.IGNORECASE),
    re.compile(r"\b(?:os\.system|os\.popen|subprocess\.(?:Popen|run|call|check_output))\s*\(", re.IGNORECASE),
    re.compile(r"\b(?:__import__)\s*\(", re.IGNORECASE),
    re.compile(r"(?:/bin/sh|/bin/bash|cmd\.exe|powershell\.exe)", re.IGNORECASE),
)


@dataclass
class StructuredDataSecurityPolicy:
    """
    Security and resource validation policy governing structured data acquisition.
    Enforces that research/task input is strictly treated as untrusted data.
    Guarantees SSRF protection, method restrictions, query parameter bounds,
    header allowlisting, body size & content safety, and redirect containment.
    """
    allowed_schemes: set[str] = field(default_factory=lambda: {"http", "https"})
    allowed_methods: set[str] = field(default_factory=lambda: {"GET", "POST", "HEAD"})
    allowed_domains: Optional[set[str]] = None
    blocked_domains: set[str] = field(default_factory=set)
    allow_localhost: bool = False
    allow_mock: bool = False
    allow_cross_domain_redirects: bool = False
    allowed_headers: set[str] = field(
        default_factory=lambda: {
            "accept",
            "accept-encoding",
            "user-agent",
            "content-type",
            "authorization",
        }
    )
    enforce_header_allowlist: bool = True
    max_params_count: int = 50
    max_key_length: int = 256
    max_value_length: int = 4096
    max_total_query_length: int = 16384
    max_body_bytes: int = 1_000_000  # 1 MB
    allowed_body_content_types: set[str] = field(
        default_factory=lambda: {
            "application/json",
            "application/x-www-form-urlencoded",
            "text/plain",
            "application/xml",
            "text/xml",
        }
    )
    max_decompressed_bytes: int = 10_000_000  # 10 MB
    max_concurrency: int = 5
    default_timeout_seconds: float = 10.0
    max_timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        self.allowed_schemes = {s.lower().strip() for s in self.allowed_schemes}
        self.allowed_methods = {m.upper().strip() for m in self.allowed_methods}
        self.allowed_headers = {h.lower().strip() for h in self.allowed_headers}
        self.allowed_body_content_types = {ct.lower().strip() for ct in self.allowed_body_content_types}
        if self.allowed_domains is not None:
            self.allowed_domains = {d.lower().strip() for d in self.allowed_domains}
        self.blocked_domains = {d.lower().strip() for d in self.blocked_domains}

    # -------------------------------------------------------------------------
    # Validation Methods
    # -------------------------------------------------------------------------

    def validate_endpoint(self, url: str) -> str:
        """
        Validate URL syntax, allowed schemes, normalized hostname, domain scoping,
        and SSRF boundary constraints.
        
        Returns the normalized URL string.
        Raises StructuredDataPolicyViolationError if constraints are violated.
        """
        if not url or not isinstance(url, str) or not url.strip():
            raise StructuredDataPolicyViolationError(
                target=str(url),
                reason="Endpoint URL must be a non-empty string.",
            )

        clean_url = url.strip()
        if any(c in clean_url for c in ("\r", "\n", "\t", "\0")):
            raise StructuredDataPolicyViolationError(
                target=clean_url,
                reason="Endpoint URL contains illegal control or newline characters.",
            )

        try:
            parsed = urllib.parse.urlsplit(clean_url)
        except Exception as e:
            raise StructuredDataPolicyViolationError(
                target=clean_url,
                reason=f"Malformed URL: {e}",
            ) from e

        scheme = (parsed.scheme or "").lower().strip()
        if not scheme:
            raise StructuredDataPolicyViolationError(
                target=clean_url,
                reason="Missing URL scheme.",
            )

        if scheme == "mock":
            if not self.allow_mock:
                raise StructuredDataPolicyViolationError(
                    target=clean_url,
                    reason="Mock scheme 'mock' is not permitted by policy.",
                )
            return clean_url

        if scheme not in self.allowed_schemes:
            raise StructuredDataPolicyViolationError(
                target=clean_url,
                reason=f"URL scheme '{scheme}' is not permitted by policy. Allowed: {sorted(self.allowed_schemes)}",
            )

        # Disallow embedded credentials
        if parsed.username or parsed.password or ("@" in parsed.netloc):
            raise StructuredDataPolicyViolationError(
                target=clean_url,
                reason="URLs with embedded user credentials (user:pass@) are strictly prohibited.",
            )

        hostname = (parsed.hostname or "").lower().strip()
        if not hostname:
            raise StructuredDataPolicyViolationError(
                target=clean_url,
                reason="Missing or invalid hostname in URL.",
            )

        # Enforce SSRF boundaries
        try:
            validate_network_target(clean_url, allow_localhost=self.allow_localhost)
        except SearchSecurityError as e:
            raise StructuredDataPolicyViolationError(
                target=clean_url,
                reason=e.reason or str(e),
                details=getattr(e, "details", None),
            ) from e

        # Check explicit domain blocklist
        for bd in self.blocked_domains:
            if hostname == bd or hostname.endswith("." + bd):
                raise StructuredDataPolicyViolationError(
                    target=clean_url,
                    reason=f"Target host '{hostname}' matches blocked domain policy '{bd}'.",
                )

        # Check explicit domain allowlist
        if self.allowed_domains is not None:
            matched = any(
                hostname == ad or hostname.endswith("." + ad)
                for ad in self.allowed_domains
            )
            if not matched:
                raise StructuredDataPolicyViolationError(
                    target=clean_url,
                    reason=f"Target host '{hostname}' is not within permitted domains: {sorted(self.allowed_domains)}",
                )

        normalized_netloc = parsed.netloc.lower()
        return urllib.parse.urlunsplit((
            scheme,
            normalized_netloc,
            parsed.path,
            parsed.query,
            parsed.fragment,
        ))

    def validate_redirect(self, original_url: str, redirect_url: str) -> str:
        """
        Validate HTTP redirect target against original URL context.
        Enforces relative resolution, scheme downgrade prevention, and domain scope containment.
        
        Returns the resolved and validated target URL string.
        """
        if not original_url or not redirect_url:
            raise StructuredDataPolicyViolationError(
                target=str(redirect_url),
                reason="Both original_url and redirect_url must be non-empty.",
            )

        target_url = urllib.parse.urljoin(original_url, redirect_url)

        orig_parsed = urllib.parse.urlsplit(original_url)
        target_parsed = urllib.parse.urlsplit(target_url)

        orig_scheme = (orig_parsed.scheme or "").lower()
        target_scheme = (target_parsed.scheme or "").lower()

        # Prevent HTTPS to HTTP scheme downgrade
        if orig_scheme == "https" and target_scheme == "http":
            raise StructuredDataPolicyViolationError(
                target=target_url,
                reason="Insecure scheme downgrade from HTTPS to HTTP during redirect is prohibited.",
            )

        # Validate domain containment if cross-domain redirects are disallowed
        orig_host = (orig_parsed.hostname or "").lower()
        target_host = (target_parsed.hostname or "").lower()
        if not self.allow_cross_domain_redirects and self.allowed_domains is None:
            if orig_host and target_host:
                if not (target_host == orig_host or target_host.endswith("." + orig_host)):
                    raise StructuredDataPolicyViolationError(
                        target=target_url,
                        reason=f"Cross-domain redirect from '{orig_host}' to '{target_host}' is prohibited by policy.",
                    )

        # Full endpoint and SSRF validation on the redirect target
        return self.validate_endpoint(target_url)

    def validate_method(self, method: str | HttpMethod) -> HttpMethod:
        """
        Validate and normalize HTTP method against policy allowed methods.
        """
        if isinstance(method, HttpMethod):
            m_str = method.value.upper()
        elif isinstance(method, str):
            m_str = method.strip().upper()
        else:
            raise StructuredDataPolicyViolationError(
                target=str(method),
                reason=f"Invalid HTTP method type: {type(method).__name__}. Expected string or HttpMethod enum.",
            )

        if m_str not in self.allowed_methods:
            raise StructuredDataPolicyViolationError(
                target=m_str,
                reason=f"HTTP method '{m_str}' is not permitted by policy. Allowed: {sorted(self.allowed_methods)}",
            )

        try:
            return HttpMethod(m_str)
        except ValueError:
            raise StructuredDataPolicyViolationError(
                target=m_str,
                reason=f"HTTP method '{m_str}' is not a recognized HTTP method enum.",
            )

    def validate_query_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        Validate parameter count, key and value bounds, character restrictions,
        and total encoded size. Returns deterministically sorted dictionary.
        """
        if params is None:
            return {}
        if not isinstance(params, dict):
            raise StructuredDataPolicyViolationError(
                target="query_params",
                reason="query_params must be a dictionary.",
            )

        if len(params) > self.max_params_count:
            raise StructuredDataPolicyViolationError(
                target="query_params",
                reason=f"Query parameter count ({len(params)}) exceeds maximum allowed ({self.max_params_count}).",
            )

        for k, v in params.items():
            if not isinstance(k, str) or not k.strip():
                raise StructuredDataPolicyViolationError(
                    target=str(k),
                    reason="Query parameter key must be a non-empty string.",
                )
            if len(k) > self.max_key_length:
                raise StructuredDataPolicyViolationError(
                    target=k,
                    reason=f"Query parameter key '{k[:20]}...' exceeds maximum length of {self.max_key_length}.",
                )
            if any(c in k for c in ("\r", "\n", "\0")):
                raise StructuredDataPolicyViolationError(
                    target=k,
                    reason="Query parameter key contains forbidden control characters.",
                )

            # Validate parameter values
            if isinstance(v, (list, tuple)):
                for item in v:
                    if not isinstance(item, (str, int, float, bool)) and item is not None:
                        raise StructuredDataPolicyViolationError(
                            target=k,
                            reason=f"Query parameter '{k}' contains unsupported item type {type(item).__name__}.",
                        )
                    item_str = str(item) if item is not None else ""
                    if len(item_str) > self.max_value_length:
                        raise StructuredDataPolicyViolationError(
                            target=k,
                            reason=f"Query parameter '{k}' item exceeds maximum value length of {self.max_value_length}.",
                        )
                    if any(c in item_str for c in ("\r", "\n", "\0")):
                        raise StructuredDataPolicyViolationError(
                            target=k,
                            reason=f"Query parameter '{k}' item contains forbidden control characters.",
                        )
            elif isinstance(v, (str, int, float, bool)) or v is None:
                v_str = str(v) if v is not None else ""
                if len(v_str) > self.max_value_length:
                    raise StructuredDataPolicyViolationError(
                        target=k,
                        reason=f"Query parameter '{k}' value exceeds maximum length of {self.max_value_length}.",
                    )
                if any(c in v_str for c in ("\r", "\n", "\0")):
                    raise StructuredDataPolicyViolationError(
                        target=k,
                        reason=f"Query parameter '{k}' value contains forbidden control characters.",
                    )
            else:
                raise StructuredDataPolicyViolationError(
                    target=k,
                    reason=f"Query parameter '{k}' has unsupported complex type {type(v).__name__}.",
                )

        encoded = self.encode_query_params(params)
        if len(encoded) > self.max_total_query_length:
            raise StructuredDataPolicyViolationError(
                target="query_params",
                reason=f"Total encoded query length ({len(encoded)}) exceeds maximum allowed ({self.max_total_query_length}).",
            )

        return {k: params[k] for k in sorted(params.keys())}

    def encode_query_params(self, params: dict[str, Any]) -> str:
        """
        Deterministically encode query parameters sorted by key and value.
        Guarantees reproducible URL strings across runs.
        """
        if not params:
            return ""
        sorted_pairs: list[tuple[str, str]] = []
        for k in sorted(params.keys()):
            v = params[k]
            if isinstance(v, (list, tuple)):
                for item in sorted(str(x) if x is not None else "" for x in v):
                    sorted_pairs.append((k, item))
            elif isinstance(v, bool):
                sorted_pairs.append((k, "true" if v else "false"))
            elif v is not None:
                sorted_pairs.append((k, str(v)))
            else:
                sorted_pairs.append((k, ""))
        return urllib.parse.urlencode(sorted_pairs, doseq=True)

    def validate_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """
        Validate header names and values. Checks CRLF injection prevention
        and allowlist restrictions. Returns validated headers dictionary.
        """
        if headers is None:
            return {}
        if not isinstance(headers, dict):
            raise StructuredDataPolicyViolationError(
                target="headers",
                reason="Headers must be a dictionary.",
            )

        validated: dict[str, str] = {}
        for k, v in headers.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise StructuredDataPolicyViolationError(
                    target=str(k),
                    reason="Header keys and values must be strings.",
                )
            if any(c in k for c in ("\r", "\n", "\0")) or any(c in v for c in ("\r", "\n", "\0")):
                raise StructuredDataPolicyViolationError(
                    target=k,
                    reason="Header key or value contains forbidden newline/control characters (CRLF injection prevention).",
                )

            clean_k = k.strip()
            clean_v = v.strip()

            if self.enforce_header_allowlist:
                if clean_k.lower() not in self.allowed_headers:
                    raise StructuredDataPolicyViolationError(
                        target=clean_k,
                        reason=f"Header '{clean_k}' is not in the allowed headers list: {sorted(self.allowed_headers)}",
                    )

            validated[clean_k] = clean_v

        return validated

    def sanitize_headers_for_logging(self, headers: dict[str, str]) -> dict[str, str]:
        """
        Return a sanitized copy of headers with sensitive authorization and tokens redacted.
        Safe for event streams, logs, and crawler reports.
        """
        return sanitize_headers(headers)

    def validate_body(
        self,
        body: Any,
        content_type: Optional[str] = None,
    ) -> Union[str, bytes, dict[str, Any], None]:
        """
        Validate request body payload size, content-type, and absence of executable scripts.
        """
        if body is None:
            return None

        if isinstance(body, bytes):
            body_bytes = body
            size = len(body_bytes)
            text_repr = body.decode("utf-8", errors="ignore")
        elif isinstance(body, str):
            body_bytes = body.encode("utf-8")
            size = len(body_bytes)
            text_repr = body
        elif isinstance(body, (dict, list)):
            text_repr = canonical_json_dumps(body)
            body_bytes = text_repr.encode("utf-8")
            size = len(body_bytes)
        else:
            raise StructuredDataPolicyViolationError(
                target="body",
                reason=f"Unsupported request body type '{type(body).__name__}'.",
            )

        if size > self.max_body_bytes:
            raise StructuredDataPolicyViolationError(
                target="body",
                reason=f"Request body size ({size} bytes) exceeds maximum limit ({self.max_body_bytes} bytes).",
            )

        if content_type:
            ct_clean = content_type.split(";")[0].strip().lower()
            if ct_clean not in self.allowed_body_content_types:
                raise StructuredDataPolicyViolationError(
                    target="body",
                    reason=f"Request body Content-Type '{content_type}' is not permitted. Allowed: {sorted(self.allowed_body_content_types)}",
                )

        # Binary executable detection
        for magic in _EXECUTABLE_BINARY_MAGIC:
            if body_bytes.startswith(magic):
                raise StructuredDataPolicyViolationError(
                    target="body",
                    reason=f"Request body contains executable binary signature ({magic.hex()}).",
                )
        if body_bytes.startswith(b"#!"):
            raise StructuredDataPolicyViolationError(
                target="body",
                reason="Request body contains executable script shebang (#!).",
            )

        # Text executable / script injection detection
        for pat in _EXECUTABLE_TEXT_PATTERNS:
            if pat.search(text_repr):
                raise StructuredDataPolicyViolationError(
                    target="body",
                    reason=f"Request body contains forbidden script or executable execution pattern: '{pat.pattern}'.",
                )

        return body

    def validate_limits(self, limits: StructuredDataLimits) -> StructuredDataLimits:
        """
        Validate that requested resource limits do not exceed maximum policy limits.
        """
        if not isinstance(limits, StructuredDataLimits):
            raise StructuredDataPolicyViolationError(
                target="limits",
                reason=f"Expected StructuredDataLimits instance, got {type(limits).__name__}.",
            )

        if limits.timeout_seconds > self.max_timeout_seconds:
            raise StructuredDataPolicyViolationError(
                target="limits",
                reason=f"Requested timeout {limits.timeout_seconds}s exceeds maximum policy timeout {self.max_timeout_seconds}s.",
            )

        if limits.max_bytes > self.max_decompressed_bytes:
            raise StructuredDataPolicyViolationError(
                target="limits",
                reason=f"Requested max_bytes {limits.max_bytes} exceeds maximum decompressed size limit {self.max_decompressed_bytes}.",
            )

        return limits

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_schemes": sorted(self.allowed_schemes),
            "allowed_methods": sorted(self.allowed_methods),
            "allowed_domains": sorted(self.allowed_domains) if self.allowed_domains is not None else None,
            "blocked_domains": sorted(self.blocked_domains),
            "allow_localhost": self.allow_localhost,
            "allow_mock": self.allow_mock,
            "allow_cross_domain_redirects": self.allow_cross_domain_redirects,
            "allowed_headers": sorted(self.allowed_headers),
            "enforce_header_allowlist": self.enforce_header_allowlist,
            "max_params_count": self.max_params_count,
            "max_key_length": self.max_key_length,
            "max_value_length": self.max_value_length,
            "max_total_query_length": self.max_total_query_length,
            "max_body_bytes": self.max_body_bytes,
            "allowed_body_content_types": sorted(self.allowed_body_content_types),
            "max_decompressed_bytes": self.max_decompressed_bytes,
            "max_concurrency": self.max_concurrency,
            "default_timeout_seconds": self.default_timeout_seconds,
            "max_timeout_seconds": self.max_timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StructuredDataSecurityPolicy:
        allowed_domains_data = data.get("allowed_domains")
        allowed_domains = set(allowed_domains_data) if allowed_domains_data is not None else None
        return cls(
            allowed_schemes=set(data.get("allowed_schemes", ["http", "https"])),
            allowed_methods=set(data.get("allowed_methods", ["GET", "POST", "HEAD"])),
            allowed_domains=allowed_domains,
            blocked_domains=set(data.get("blocked_domains", [])),
            allow_localhost=bool(data.get("allow_localhost", False)),
            allow_mock=bool(data.get("allow_mock", False)),
            allow_cross_domain_redirects=bool(data.get("allow_cross_domain_redirects", False)),
            allowed_headers=set(data.get("allowed_headers", ["accept", "accept-encoding", "user-agent", "content-type", "authorization"])),
            enforce_header_allowlist=bool(data.get("enforce_header_allowlist", True)),
            max_params_count=int(data.get("max_params_count", 50)),
            max_key_length=int(data.get("max_key_length", 256)),
            max_value_length=int(data.get("max_value_length", 4096)),
            max_total_query_length=int(data.get("max_total_query_length", 16384)),
            max_body_bytes=int(data.get("max_body_bytes", 1_000_000)),
            allowed_body_content_types=set(data.get("allowed_body_content_types", ["application/json", "application/x-www-form-urlencoded", "text/plain", "application/xml", "text/xml"])),
            max_decompressed_bytes=int(data.get("max_decompressed_bytes", 10_000_000)),
            max_concurrency=int(data.get("max_concurrency", 5)),
            default_timeout_seconds=float(data.get("default_timeout_seconds", 10.0)),
            max_timeout_seconds=float(data.get("max_timeout_seconds", 60.0)),
        )

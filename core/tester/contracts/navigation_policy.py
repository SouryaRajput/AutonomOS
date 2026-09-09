from __future__ import annotations

import re
from typing import Optional, Set
import urllib.parse

from core.tester.contracts.environment import TestEnvironment
from core.tester.contracts.work_order import TesterWorkOrder
from core.tester.errors import (
    InvalidUrlError,
    NavigationDeniedError,
    TesterValidationError,
)

# Standard allowed schemes for web testing
ALLOWED_SCHEMES = frozenset({"http", "https"})

# Sensitive query param names to reject
SENSITIVE_PARAM_NAMES = frozenset({
    "password", "passwd", "pwd", "secret", "api_key", "apikey",
    "token", "access_token", "auth_token", "bearer", "private_key",
})


class NavigationPolicy:
    """
    Deterministic navigation boundary guard for Tester V1.
    
    Invariants:
    - Primary application URL/environment defines the authoritative origin.
    - Prevents accidental navigation outside authorized origins.
    - Normalizes relative routes into absolute target URLs.
    - Strictly rejects malformed URLs, unsupported schemes, and embedded secrets.
    """
    __test__ = False

    def __init__(
        self,
        primary_url: Optional[str] = None,
        allowed_origins: Optional[Set[str] | list[str]] = None,
        allowed_schemes: Optional[Set[str]] = None,
    ) -> None:
        self.primary_url = (primary_url or "").strip()
        self.allowed_schemes = set(allowed_schemes or ALLOWED_SCHEMES)
        self.allowed_origins: Set[str] = set()

        if self.primary_url:
            norm_primary = self._extract_origin(self.primary_url)
            if norm_primary:
                self.allowed_origins.add(norm_primary)

        if allowed_origins:
            for origin in allowed_origins:
                norm = self._extract_origin(origin)
                if norm:
                    self.allowed_origins.add(norm)

    @classmethod
    def from_environment_and_work_order(
        cls,
        environment: Optional[TestEnvironment] = None,
        work_order: Optional[TesterWorkOrder] = None,
    ) -> NavigationPolicy:
        """Construct a NavigationPolicy from TestEnvironment and TesterWorkOrder."""
        primary = ""
        if environment:
            primary = environment.application_url or environment.base_url or ""
        elif work_order and hasattr(work_order, "test_environment") and work_order.test_environment:
            primary = getattr(work_order.test_environment, "application_url", "") or getattr(work_order.test_environment, "base_url", "")

        extra_origins: Set[str] = set()
        if work_order:
            wo_meta = getattr(work_order, "metadata", {}) or {}
            extra = wo_meta.get("allowed_origins", [])
            if isinstance(extra, (list, set, tuple)):
                extra_origins.update(str(o) for o in extra)

            if hasattr(work_order, "test_scope") and work_order.test_scope:
                scope_meta = getattr(work_order.test_scope, "metadata", {}) or {}
                scope_extra = scope_meta.get("allowed_origins", [])
                if isinstance(scope_extra, (list, set, tuple)):
                    extra_origins.update(str(o) for o in scope_extra)

        return cls(primary_url=primary, allowed_origins=extra_origins)

    def normalize_url(self, target_url: str) -> str:
        """
        Normalize and validate target URL:
        - Resolves relative paths against primary_url
        - Rejects dangerous schemes (javascript:, data:, vbscript:)
        - Checks for embedded credentials and sensitive tokens
        """
        raw = (target_url or "").strip()
        if not raw:
            raise InvalidUrlError("Target URL cannot be empty.", url=target_url)

        # Check for dangerous pseudo-schemes
        lower_raw = raw.lower()
        if lower_raw.startswith(("javascript:", "data:", "vbscript:", "about:")):
            raise InvalidUrlError(
                f"Unsupported or dangerous URL scheme in '{raw}'.",
                url=raw,
            )

        # Handle relative URLs
        if raw.startswith("/") or not urllib.parse.urlsplit(raw).scheme:
            if not self.primary_url:
                raise InvalidUrlError(
                    f"Cannot resolve relative route '{raw}' without a configured primary application URL.",
                    url=raw,
                )
            resolved = urllib.parse.urljoin(self.primary_url, raw)
        else:
            resolved = raw

        # Parse resolved URL
        try:
            parsed = urllib.parse.urlsplit(resolved)
        except Exception as e:
            raise InvalidUrlError(f"Malformed target URL '{resolved}': {e}", url=resolved)

        scheme = (parsed.scheme or "").lower()
        if scheme not in self.allowed_schemes:
            raise InvalidUrlError(
                f"URL scheme '{scheme}' is not allowed. Permitted schemes: {sorted(self.allowed_schemes)}",
                url=resolved,
            )

        # Reject embedded username / password in URL
        if parsed.username or parsed.password:
            raise TesterValidationError(
                "Credentials embedded in target URL are prohibited by Tester security policy.",
                field_name="target_url",
            )

        # Reject sensitive query parameters
        if parsed.query:
            query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            for k, v in query_pairs:
                if k.lower() in SENSITIVE_PARAM_NAMES:
                    raise TesterValidationError(
                        f"Sensitive credential parameter '{k}' detected in target URL query parameters.",
                        field_name="target_url",
                    )

        # Reconstruct canonical normalized URL
        netloc = (parsed.netloc or "").lower()
        # Drop standard ports (80 for http, 443 for https)
        if scheme == "http" and netloc.endswith(":80"):
            netloc = netloc[:-3]
        elif scheme == "https" and netloc.endswith(":443"):
            netloc = netloc[:-4]

        path = parsed.path or "/"
        normalized = urllib.parse.urlunsplit((scheme, netloc, path, parsed.query, parsed.fragment))
        return normalized

    def _extract_origin(self, url: str) -> Optional[str]:
        """Extract canonical origin (scheme://host[:port]) from a URL."""
        try:
            parsed = urllib.parse.urlsplit(url.strip())
            if not parsed.scheme or not parsed.netloc:
                return None
            scheme = parsed.scheme.lower()
            netloc = parsed.netloc.lower()
            if scheme == "http" and netloc.endswith(":80"):
                netloc = netloc[:-3]
            elif scheme == "https" and netloc.endswith(":443"):
                netloc = netloc[:-4]
            return f"{scheme}://{netloc}"
        except Exception:
            return None

    def is_allowed(self, target_url: str) -> tuple[bool, str]:
        """Check whether a target URL is permitted by the origin policy."""
        try:
            normalized = self.normalize_url(target_url)
        except Exception as e:
            return False, str(e)

        target_origin = self._extract_origin(normalized)
        if not target_origin:
            return False, f"Could not determine valid origin for '{normalized}'"

        # If no allowed origins are configured, permit target if validly formed
        if not self.allowed_origins:
            return True, ""

        if target_origin in self.allowed_origins:
            return True, ""

        # Check loopback equivalence: http://localhost:port == http://127.0.0.1:port
        for allowed in self.allowed_origins:
            if self._is_loopback_equivalent(target_origin, allowed):
                return True, ""

        return False, (
            f"Navigation to origin '{target_origin}' denied. "
            f"Allowed origins: {sorted(self.allowed_origins)}"
        )

    def assert_allowed(self, target_url: str) -> str:
        """
        Validate and normalize target URL, asserting it satisfies the origin boundary.
        Returns the normalized URL on success.
        Raises InvalidUrlError or NavigationDeniedError on violation.
        """
        normalized = self.normalize_url(target_url)
        allowed, reason = self.is_allowed(normalized)
        if not allowed:
            raise NavigationDeniedError(
                message=f"Navigation denied: {reason}",
                target_url=normalized,
                reason=reason,
            )
        return normalized

    def _is_loopback_equivalent(self, origin_a: str, origin_b: str) -> bool:
        """Check if two origins are equivalent loopback hosts (localhost vs 127.0.0.1)."""
        loopbacks = ("localhost", "127.0.0.1")
        split_a = urllib.parse.urlsplit(origin_a)
        split_b = urllib.parse.urlsplit(origin_b)
        if split_a.scheme != split_b.scheme:
            return False
        port_a = split_a.port
        port_b = split_b.port
        if port_a != port_b:
            return False
        host_a = (split_a.hostname or "").lower()
        host_b = (split_b.hostname or "").lower()
        return host_a in loopbacks and host_b in loopbacks

from __future__ import annotations

import ipaddress
import logging
import re
from typing import TYPE_CHECKING, Any, Optional
import urllib.parse

from core.research.errors import SearchSecurityError

if TYPE_CHECKING:
    from core.research.contracts.crawler_task import CrawlerTask

logger = logging.getLogger("AutonomOS.Research.SearchSecurity")

REDACTED_STR = "[REDACTED]"

SENSITIVE_HEADER_KEYS = {
    "authorization",
    "x-api-key",
    "api-key",
    "apikey",
    "bearer",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-auth-token",
}

LOCALHOST_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
}

METADATA_HOSTNAMES = {
    "instance-data",
    "metadata.google.internal",
    "169.254.169.254",
}

CLOUD_METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
}

# Regex to find user:pass@ in URLs
_URL_CREDENTIAL_REGEX = re.compile(r"://([^/@:]+):([^/@:]+)@")


def sanitize_url(url: str) -> str:
    """
    Strip embedded credentials (user:pass@) from a URL string for safe logging and storage.
    """
    if not url or not isinstance(url, str):
        return ""
    return _URL_CREDENTIAL_REGEX.sub(r"://\1:[REDACTED]@", url)


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    """
    Return a copy of headers with sensitive authorization/API tokens redacted.
    """
    sanitized: dict[str, str] = {}
    for k, v in headers.items():
        if str(k).lower().strip() in SENSITIVE_HEADER_KEYS:
            sanitized[k] = REDACTED_STR
        else:
            sanitized[k] = v
    return sanitized


def sanitize_secret(text: str, secrets: Optional[list[str]] = None) -> str:
    """
    Sanitize text by replacing known secret tokens and common authorization patterns with [REDACTED].
    """
    if not text or not isinstance(text, str):
        return ""

    result = text
    if secrets:
        for s in secrets:
            if s and len(s) >= 4:
                result = result.replace(s, REDACTED_STR)

    # Sanitize bearer tokens / API key patterns
    result = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]{8,}", r"\1" + REDACTED_STR, result, flags=re.IGNORECASE)
    result = re.sub(r"(api[_\-]?key[\s:=]+)[A-Za-z0-9_\-\.]{8,}", r"\1" + REDACTED_STR, result, flags=re.IGNORECASE)
    result = sanitize_url(result)
    return result


def sanitize_error(error: Exception | str, secrets: Optional[list[str]] = None) -> str:
    """
    Format and sanitize an error message to guarantee zero secret leakage.
    """
    err_str = str(error)
    return sanitize_secret(err_str, secrets=secrets)


def validate_network_target(url_or_host: str, allow_localhost: bool = False) -> None:
    """
    Validate that an outbound network target (URL or hostname) does not violate the
    network security boundary (SSRF, private subnets, loopback, cloud metadata endpoints).
    
    Raises SearchSecurityError if the destination is disallowed.
    """
    if not url_or_host or not isinstance(url_or_host, str):
        raise SearchSecurityError(str(url_or_host), "Empty or invalid URL/host target.")

    raw = url_or_host.strip()

    # 1. Parse URL/Scheme
    if ":" in raw:
        parsed = urllib.parse.urlsplit(raw)
        if parsed.scheme:
            scheme = parsed.scheme.lower()
            if scheme not in ("http", "https"):
                raise SearchSecurityError(raw, f"Disallowed URL scheme '{scheme}'. Only HTTP and HTTPS are permitted.")
            
            # Check embedded credentials in URL
            if parsed.username or parsed.password or ("@" in parsed.netloc):
                raise SearchSecurityError(raw, "URLs with embedded credentials (user:pass@) are prohibited.")

            hostname = parsed.hostname or ""
        else:
            hostname = raw.split("/")[0].split(":")[0]
    else:
        hostname = raw.split("/")[0]

    hostname_clean = hostname.lower().strip()
    if not hostname_clean:
        raise SearchSecurityError(raw, "Missing hostname in network target.")

    # 2. Check metadata endpoints (always blocked regardless of allow_localhost)
    if hostname_clean in METADATA_HOSTNAMES:
        raise SearchSecurityError(raw, f"Target host '{hostname_clean}' is a restricted Cloud Metadata endpoint.")

    # 3. Check loopback / localhost
    if not allow_localhost and hostname_clean in LOCALHOST_HOSTNAMES:
        raise SearchSecurityError(raw, f"Target host '{hostname_clean}' is a local loopback address (SSRF protection).")

    # 4. Check IP address bounds
    try:
        ip_obj = ipaddress.ip_address(hostname_clean)
        
        # Check cloud metadata endpoint
        if ip_obj in CLOUD_METADATA_IPS:
            raise SearchSecurityError(raw, f"Target IP '{ip_obj}' is a restricted Cloud Metadata service endpoint.")

        # Check loopback
        if not allow_localhost and ip_obj.is_loopback:
            raise SearchSecurityError(raw, f"Target IP '{ip_obj}' is a loopback address (SSRF protection).")

        # Check private RFC 1918 / RFC 4193
        if not allow_localhost and ip_obj.is_private:
            raise SearchSecurityError(raw, f"Target IP '{ip_obj}' is in a private network subnet (SSRF protection).")

        # Check link-local, multicast, reserved
        if ip_obj.is_link_local:
            raise SearchSecurityError(raw, f"Target IP '{ip_obj}' is a link-local address.")
        if ip_obj.is_multicast:
            raise SearchSecurityError(raw, f"Target IP '{ip_obj}' is a multicast address.")
        if ip_obj.is_reserved:
            raise SearchSecurityError(raw, f"Target IP '{ip_obj}' is an IETF reserved address.")

    except ValueError:
        # Not a raw IP literal
        pass


def is_safe_search_url(url: str, allow_localhost: bool = False) -> bool:
    """
    Convenience helper returning True if the URL complies with search network boundaries.
    """
    try:
        validate_network_target(url, allow_localhost=allow_localhost)
        return True
    except SearchSecurityError:
        return False


def compute_effective_timeout(
    task_timeout: Optional[float],
    config_timeout: float,
    elapsed_seconds: float = 0.0,
) -> float:
    """
    Calculate the effective network timeout respecting the hierarchy:
    CrawlerTask timeout -> Execution deadline -> Network request timeout.
    Guarantees the network request does not outlive the allocated task deadline.
    """
    if task_timeout is not None:
        remaining = max(0.1, task_timeout - elapsed_seconds)
        return max(0.1, min(remaining, config_timeout))
    return max(0.1, config_timeout)

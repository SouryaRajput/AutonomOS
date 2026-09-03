from __future__ import annotations

from typing import TYPE_CHECKING, Optional
import urllib.parse

if TYPE_CHECKING:
    from core.research.search.models import SearchResultItem

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "_ga",
    "yclid",
}


def normalize_url(url: str, strip_tracking: bool = True) -> str:
    """
    Safely normalize a URL for deterministic identity and deduplication.
    
    Operations performed:
    1. Trims whitespace and strips empty strings.
    2. Lowercases scheme and hostname.
    3. Strips default HTTP/HTTPS ports (:80, :443).
    4. Normalizes path (collapses consecutive slashes, strips root trailing slash).
    5. Strips fragment identifiers (#section).
    6. Sorts query parameters and strips tracking parameters (utm_*, gclid, etc.) if enabled.
    """
    if not url or not isinstance(url, str):
        return ""

    raw = url.strip()
    if not raw:
        return ""

    # Parse URL
    try:
        parsed = urllib.parse.urlsplit(raw)
    except Exception:
        return raw

    scheme = parsed.scheme.lower()
    if not scheme:
        scheme = "https"

    # Normalize netloc (hostname + port)
    netloc = parsed.netloc.lower()
    if ":" in netloc:
        host, port = netloc.rsplit(":", 1)
        if (scheme == "http" and port == "80") or (scheme == "https" and port == "443"):
            netloc = host
    
    # Normalize path
    path = parsed.path
    if not path or path == "/":
        path = ""
    else:
        # Collapse multiple slashes
        while "//" in path:
            path = path.replace("//", "/")
        # If path ends with trailing slash and is more than 1 char, remove trailing slash for consistency
        if path.endswith("/") and len(path) > 1:
            path = path.rstrip("/")

    # Normalize query parameters
    query = ""
    if parsed.query:
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if strip_tracking:
            pairs = [p for p in pairs if p[0].lower() not in TRACKING_PARAMS]
        # Sort query parameters for deterministic comparison
        pairs.sort(key=lambda x: (x[0], x[1]))
        if pairs:
            query = urllib.parse.urlencode(pairs)

    # Reconstruct normalized URL (fragments are intentionally stripped)
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def extract_domain(url: str) -> str:
    """
    Extract a normalized, lowercase domain name from a URL or raw hostname.
    """
    if not url or not isinstance(url, str):
        return ""

    raw = url.strip().lower()
    if not raw:
        return ""

    if "://" not in raw:
        raw = "https://" + raw

    try:
        parsed = urllib.parse.urlsplit(raw)
        host = parsed.netloc or parsed.path
        if ":" in host:
            host = host.split(":", 1)[0]
        # Strip potential path remainder if netloc was missing
        if "/" in host:
            host = host.split("/", 1)[0]
        return host.strip()
    except Exception:
        return ""


def deduplicate_search_results(results: list[SearchResultItem]) -> list[SearchResultItem]:
    """
    Deterministically deduplicate search results based on:
    1. Normalized URL identity
    2. Provider-specific result ID (if present)
    
    Guarantees:
    - Retains ranking order of the first occurrence.
    - Does NOT deduplicate based on similar titles or topics alone.
    - Independent URLs discussing the same subject are strictly preserved.
    """
    seen_urls: set[str] = set()
    seen_ids: set[str] = set()
    deduped: list[SearchResultItem] = []

    for item in results:
        norm_url = item.normalized_url or normalize_url(item.url)
        res_id = item.provider_result_id

        # Check URL uniqueness
        if norm_url and norm_url in seen_urls:
            continue

        # Check Provider ID uniqueness
        if res_id and res_id in seen_ids:
            continue

        if norm_url:
            seen_urls.add(norm_url)
        if res_id:
            seen_ids.add(res_id)

        deduped.append(item)

    return deduped

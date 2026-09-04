from __future__ import annotations

from typing import Optional


class ResearchError(Exception):
    """Base exception for all research subsystem errors."""
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidStateTransitionError(ResearchError):
    """Raised when an invalid research lifecycle state transition is attempted."""
    def __init__(self, current_state: str, target_state: str, reason: str = ""):
        msg = f"Cannot transition research state from '{current_state}' to '{target_state}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg, {"current_state": current_state, "target_state": target_state, "reason": reason})
        self.current_state = current_state
        self.target_state = target_state


class InsufficientEvidenceError(ResearchError):
    """Raised when coverage check determines evidence is insufficient to answer required questions."""
    def __init__(self, question_ids: list[str], reason: str = ""):
        msg = f"Insufficient evidence for questions: {', '.join(question_ids)}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg, {"question_ids": question_ids, "reason": reason})
        self.question_ids = question_ids


class CrawlerExecutionError(ResearchError):
    """Raised when a crawler task fails or encounters an unhandled runtime exception."""
    def __init__(self, crawler_id: str, task_id: str, error_message: str):
        msg = f"Crawler '{crawler_id}' failed executing task '{task_id}': {error_message}"
        super().__init__(msg, {"crawler_id": crawler_id, "task_id": task_id, "error": error_message})
        self.crawler_id = crawler_id
        self.task_id = task_id


class CrawlerNotFoundError(ResearchError):
    """Raised when a requested crawler ID or capability matching crawler is not found."""
    def __init__(self, identifier: str):
        super().__init__(f"Crawler matching '{identifier}' not found.", {"identifier": identifier})


class CrawlerTaskNotFoundError(ResearchError):
    """Raised when a referenced crawler task cannot be found."""
    def __init__(self, task_id: str):
        super().__init__(f"Crawler task with ID '{task_id}' not found.", {"task_id": task_id})


class ProvenanceError(ResearchError):
    """Raised when evidence or report provenance verification fails."""
    def __init__(self, item_id: str, expected: str, actual: str):
        msg = f"Provenance mismatch for item '{item_id}': expected '{expected}', found '{actual}'"
        super().__init__(msg, {"item_id": item_id, "expected": expected, "actual": actual})


# Search-specific Errors
class SearchError(ResearchError):
    """Base exception for search-related errors."""
    pass


class SearchParameterValidationError(SearchError):
    """Raised when search parameters fail schema, boundary, or sanity validation."""
    def __init__(self, parameter: str, reason: str, details: Optional[dict] = None):
        msg = f"Invalid search parameter '{parameter}': {reason}"
        d = {"parameter": parameter, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, d)
        self.parameter = parameter
        self.reason = reason


class SearchProviderError(SearchError):
    """Raised when an external or internal search provider encounters an error."""
    def __init__(self, provider_id: str, message: str, status_code: Optional[int] = None, details: Optional[dict] = None):
        msg = f"Search provider '{provider_id}' failed: {message}"
        d = {"provider_id": provider_id, "status_code": status_code}
        if details:
            d.update(details)
        super().__init__(msg, d)
        self.provider_id = provider_id
        self.status_code = status_code


class SearchRateLimitError(SearchProviderError):
    """Raised when a search provider's rate limit or quota is exceeded."""
    def __init__(self, provider_id: str, retry_after_seconds: Optional[float] = None, details: Optional[dict] = None):
        msg = "Rate limit or quota exceeded"
        if retry_after_seconds:
            msg += f" (retry after {retry_after_seconds}s)"
        d = {"retry_after_seconds": retry_after_seconds}
        if details:
            d.update(details)
        super().__init__(provider_id, msg, status_code=429, details=d)
        self.retry_after_seconds = retry_after_seconds


class SearchAuthenticationError(SearchProviderError):
    """Raised when search provider credentials/API keys are invalid or missing."""
    def __init__(self, provider_id: str, message: str = "Invalid or missing API key", details: Optional[dict] = None):
        super().__init__(provider_id, message, status_code=401, details=details)


class SearchTimeoutError(SearchProviderError):
    """Raised when a search query times out at provider or network layer."""
    def __init__(self, provider_id: str, timeout_seconds: float, details: Optional[dict] = None):
        msg = f"Search query timed out after {timeout_seconds}s"
        d = {"timeout_seconds": timeout_seconds}
        if details:
            d.update(details)
        super().__init__(provider_id, msg, status_code=408, details=d)
        self.timeout_seconds = timeout_seconds


class SearchSecurityError(SearchError):
    """Raised when a URL, IP address, or host violates the network security boundary (SSRF, private network, metadata)."""
    def __init__(self, target: str, reason: str, details: Optional[dict] = None):
        msg = f"Search security violation for target '{target}': {reason}"
        d = {"target": target, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, d)
        self.target = target
        self.reason = reason


class SearchConfigurationError(SearchError):
    """Raised when search provider configuration is invalid or missing required attributes."""
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details)


# Fetch-specific Errors
class FetchError(ResearchError):
    """Base exception for web fetch errors."""
    pass


class FetchParameterValidationError(FetchError):
    """Raised when fetch parameters fail validation (empty URL, unsupported scheme, etc.)."""
    def __init__(self, parameter: str, reason: str, details: Optional[dict] = None):
        msg = f"Invalid fetch parameter '{parameter}': {reason}"
        d = {"parameter": parameter, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, d)
        self.parameter = parameter
        self.reason = reason


class FetchSecurityError(FetchError):
    """Raised when a fetch target violates SSRF or network policy."""
    def __init__(self, target: str, reason: str, details: Optional[dict] = None):
        msg = f"Fetch security violation for target '{target}': {reason}"
        d = {"target": target, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, d)
        self.target = target
        self.reason = reason


class FetchTimeoutError(FetchError):
    """Raised when an HTTP fetch times out."""
    def __init__(self, url: str, timeout_seconds: float, details: Optional[dict] = None):
        msg = f"Fetch request to '{url}' timed out after {timeout_seconds}s"
        d = {"url": url, "timeout_seconds": timeout_seconds}
        if details:
            d.update(details)
        super().__init__(msg, d)
        self.url = url
        self.timeout_seconds = timeout_seconds


class FetchHttpError(FetchError):
    """Raised when an HTTP request returns an error status code (4xx, 5xx)."""
    def __init__(self, url: str, status_code: int, message: str = "", headers: Optional[dict] = None, details: Optional[dict] = None):
        msg = f"HTTP {status_code} fetching '{url}'"
        if message:
            msg += f": {message}"
        d = {"url": url, "status_code": status_code, "headers": headers or {}}
        if details:
            d.update(details)
        super().__init__(msg, d)
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}


class FetchNetworkError(FetchError):
    """Raised on low-level transport errors (DNS, connection reset, SSL)."""
    def __init__(self, url: str, message: str, details: Optional[dict] = None):
        msg = f"Network error fetching '{url}': {message}"
        d = {"url": url, "network_error": message}
        if details:
            d.update(details)
        super().__init__(msg, d)
        self.url = url


class FetchContentExtractionError(FetchError):
    """Raised when content extraction fails."""
    def __init__(self, url: str, reason: str, details: Optional[dict] = None):
        msg = f"Content extraction failed for '{url}': {reason}"
        d = {"url": url, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, d)
        self.url = url
        self.reason = reason


class FetchRedirectError(FetchError):
    """Base exception for redirect failures."""
    def __init__(self, url: str, reason: str, details: Optional[dict] = None):
        msg = f"Redirect failure for '{url}': {reason}"
        d = {"url": url, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, d)
        self.url = url
        self.reason = reason


class FetchRedirectLimitError(FetchRedirectError):
    """Raised when redirect loop is detected or redirect count exceeds threshold."""
    def __init__(self, url: str, redirect_count: int, max_redirects: int, is_loop: bool = False, details: Optional[dict] = None):
        if is_loop:
            msg = f"Redirect loop detected at '{url}'"
        else:
            msg = f"Redirect limit exceeded ({redirect_count} > {max_redirects}) starting from '{url}'"
        d = {"url": url, "redirect_count": redirect_count, "max_redirects": max_redirects, "is_loop": is_loop}
        if details:
            d.update(details)
        super().__init__(url, msg, details=d)
        self.redirect_count = redirect_count
        self.max_redirects = max_redirects
        self.is_loop = is_loop


class FetchSizeLimitError(FetchError):
    """Raised when response body or stream exceeds maximum permitted bytes."""
    def __init__(self, url: str, size_bytes: int, max_bytes: int, details: Optional[dict] = None):
        msg = f"Response size for '{url}' ({size_bytes} bytes) exceeds maximum limit of {max_bytes} bytes"
        d = {"url": url, "size_bytes": size_bytes, "max_bytes": max_bytes}
        if details:
            d.update(details)
        super().__init__(msg, d)
        self.url = url
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes



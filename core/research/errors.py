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


# Documentation-specific Errors
class DocumentationError(ResearchError):
    """Base exception for documentation subsystem errors."""
    pass


class DocumentationValidationError(DocumentationError):
    """Raised when documentation source, page, or section fails validation."""
    def __init__(self, field_name: str, reason: str, details: Optional[dict] = None):
        msg = f"Invalid documentation model '{field_name}': {reason}"
        d = {"field": field_name, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, d)
        self.field_name = field_name
        self.reason = reason


# Repository-specific Errors
class RepositoryError(ResearchError):
    """Base exception for repository subsystem errors."""
    pass


class RepositoryValidationError(RepositoryError):
    """Raised when repository identity, revision, tree, file, or source material fails validation."""
    def __init__(self, field_name: str, reason: str, details: Optional[dict] = None):
        msg = f"Invalid repository model '{field_name}': {reason}"
        d = {"field": field_name, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, d)
        self.field_name = field_name
        self.reason = reason


class RepositoryProviderError(RepositoryError):
    """Base exception for repository data access provider failures."""
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details)


class RepositoryNotFoundError(RepositoryProviderError):
    """Raised when a repository cannot be found or resolved by URL / identifier."""
    def __init__(self, repo_target: str, message: str = "", details: Optional[dict] = None):
        msg = f"Repository not found: '{repo_target}'"
        if message:
            msg += f" ({message})"
        d = {"repo_target": repo_target}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.repo_target = repo_target


class RepositoryRevisionNotFoundError(RepositoryProviderError):
    """Raised when a specified revision (commit SHA, branch, tag) does not exist."""
    def __init__(self, repo_target: str, revision: str, message: str = "", details: Optional[dict] = None):
        msg = f"Revision '{revision}' not found for repository '{repo_target}'"
        if message:
            msg += f" ({message})"
        d = {"repo_target": repo_target, "revision": revision}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.repo_target = repo_target
        self.revision = revision


class RepositoryFileNotFoundError(RepositoryProviderError):
    """Raised when a requested file does not exist at the specified revision."""
    def __init__(self, repo_target: str, file_path: str, revision: Optional[str] = None, details: Optional[dict] = None):
        rev_str = f" at revision '{revision}'" if revision else ""
        msg = f"File '{file_path}' not found in repository '{repo_target}'{rev_str}"
        d = {"repo_target": repo_target, "file_path": file_path, "revision": revision}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.repo_target = repo_target
        self.file_path = file_path
        self.revision = revision


class RepositoryResourceLimitError(RepositoryProviderError):
    """Raised when an operation exceeds resource bounds (e.g. max_file_bytes, max_tree_depth, max_files)."""
    def __init__(self, resource_type: str, actual_value: int | float, max_limit: int | float, details: Optional[dict] = None):
        msg = f"Repository resource limit exceeded for {resource_type}: {actual_value} > {max_limit}"
        d = {"resource_type": resource_type, "actual_value": actual_value, "max_limit": max_limit}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.resource_type = resource_type
        self.actual_value = actual_value
        self.max_limit = max_limit


class RepositoryTimeoutError(RepositoryProviderError):
    """Raised when a repository provider operation times out."""
    def __init__(self, repo_target: str, operation: str, timeout_seconds: float, details: Optional[dict] = None):
        msg = f"Repository operation '{operation}' for '{repo_target}' timed out after {timeout_seconds}s"
        d = {"repo_target": repo_target, "operation": operation, "timeout_seconds": timeout_seconds}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.repo_target = repo_target
        self.operation = operation
        self.timeout_seconds = timeout_seconds


class RepositoryCancelledError(RepositoryProviderError):
    """Raised when a repository operation is cancelled."""
    def __init__(self, repo_target: str = "", operation: str = "", message: str = "", details: Optional[dict] = None):
        msg = f"Repository operation '{operation}' for '{repo_target}' was cancelled"
        if message:
            msg += f": {message}"
        d = {"repo_target": repo_target, "operation": operation}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.repo_target = repo_target
        self.operation = operation


class RepositoryAuthenticationError(RepositoryProviderError):
    """Raised when repository access is rejected due to invalid authentication or insufficient permissions."""
    def __init__(self, repo_target: str, message: str = "Authentication failed", details: Optional[dict] = None):
        msg = f"Repository authentication failure for '{repo_target}': {message}"
        d = {"repo_target": repo_target, "message": message}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.repo_target = repo_target


class RepositoryRateLimitError(RepositoryProviderError):
    """Raised when provider rate limits are exceeded."""
    def __init__(self, provider_id: str, retry_after_seconds: float = 30.0, details: Optional[dict] = None):
        msg = f"Repository provider '{provider_id}' rate limit exceeded (retry after {retry_after_seconds}s)"
        d = {"provider_id": provider_id, "retry_after_seconds": retry_after_seconds}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.provider_id = provider_id
        self.retry_after_seconds = retry_after_seconds


class RepositorySecurityError(RepositoryProviderError):
    """Raised when a repository target or path violates security policy (SSRF, traversal escaping root)."""
    def __init__(self, target_or_path: str, reason: str, details: Optional[dict] = None):
        msg = f"Repository security violation for '{target_or_path}': {reason}"
        d = {"target_or_path": target_or_path, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.target_or_path = target_or_path
        self.reason = reason


# Community-specific Errors
class CommunityError(ResearchError):
    """Base exception for community and discussion subsystem errors."""
    pass


class CommunityValidationError(CommunityError):
    """Raised when community context, discussion, post, or thread structure fails validation."""
    def __init__(self, field_name: str, reason: str, details: Optional[dict] = None):
        msg = f"Invalid community model '{field_name}': {reason}"
        d = {"field": field_name, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, d)
        self.field_name = field_name
        self.reason = reason


class CommunityProviderError(CommunityError):
    """Base exception for community and discussion data access provider failures."""
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, details)


class CommunityDiscussionNotFoundError(CommunityProviderError):
    """Raised when a requested discussion thread, forum post, or topic cannot be found or resolved."""
    def __init__(self, discussion_id: str, message: str = "", details: Optional[dict] = None):
        msg = f"Discussion not found: '{discussion_id}'"
        if message:
            msg += f" ({message})"
        d = {"discussion_id": discussion_id}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.discussion_id = discussion_id


class CommunityResourceLimitError(CommunityProviderError):
    """Raised when an operation exceeds community resource bounds (e.g. max_posts, max_depth, max_bytes)."""
    def __init__(self, resource_type: str, actual_value: int | float, max_limit: int | float, details: Optional[dict] = None):
        msg = f"Community resource limit exceeded for {resource_type}: {actual_value} > {max_limit}"
        d = {"resource_type": resource_type, "actual_value": actual_value, "max_limit": max_limit}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.resource_type = resource_type
        self.actual_value = actual_value
        self.max_limit = max_limit


class CommunityTimeoutError(CommunityProviderError):
    """Raised when a community provider operation times out."""
    def __init__(self, target: str, operation: str, timeout_seconds: float, details: Optional[dict] = None):
        msg = f"Community operation '{operation}' for '{target}' timed out after {timeout_seconds}s"
        d = {"target": target, "operation": operation, "timeout_seconds": timeout_seconds}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.target = target
        self.operation = operation
        self.timeout_seconds = timeout_seconds


class CommunityCancelledError(CommunityProviderError):
    """Raised when a community operation is cancelled by the caller or supervisor."""
    def __init__(self, target: str = "", operation: str = "", message: str = "", details: Optional[dict] = None):
        msg = f"Community operation '{operation}' for '{target}' was cancelled"
        if message:
            msg += f": {message}"
        d = {"target": target, "operation": operation}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.target = target
        self.operation = operation


class CommunityAuthenticationError(CommunityProviderError):
    """Raised when community access is rejected due to invalid authentication or private access."""
    def __init__(self, target: str, message: str = "Authentication failed or access restricted", details: Optional[dict] = None):
        msg = f"Community authentication / authorization failure for '{target}': {message}"
        d = {"target": target, "message": message}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.target = target


class CommunityRateLimitError(CommunityProviderError):
    """Raised when community platform rate limits or quotas are exceeded."""
    def __init__(self, provider_id: str, retry_after_seconds: float = 30.0, details: Optional[dict] = None):
        msg = f"Community provider '{provider_id}' rate limit exceeded (retry after {retry_after_seconds}s)"
        d = {"provider_id": provider_id, "retry_after_seconds": retry_after_seconds}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.provider_id = provider_id
        self.retry_after_seconds = retry_after_seconds


class CommunitySecurityError(CommunityProviderError):
    """Raised when a community target or URL violates security policies (SSRF, forbidden domain)."""
    def __init__(self, target: str, reason: str, details: Optional[dict] = None):
        msg = f"Community security violation for '{target}': {reason}"
        d = {"target": target, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.target = target
        self.reason = reason


# Structured Data Errors (Phase 1 / Part 7)
class StructuredDataError(ResearchError):
    """Base exception for structured data subsystem errors."""
    pass


class StructuredDataValidationError(StructuredDataError):
    """Raised when structured data source, request, response, record, or schema fails validation."""
    def __init__(self, field_name: str, reason: str, details: Optional[dict] = None):
        msg = f"Invalid structured data model '{field_name}': {reason}"
        d = {"field": field_name, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.field_name = field_name
        self.reason = reason


class StructuredDataLocationError(StructuredDataError):
    """Raised when a source path/location expression cannot be parsed or resolved against a payload."""
    def __init__(self, path: str, reason: str, details: Optional[dict] = None):
        msg = f"Source location error for '{path}': {reason}"
        d = {"path": path, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.path = path
        self.reason = reason


class StructuredDataLimitError(StructuredDataError):
    """Raised when structured data request limits, payload bounds, or nesting depths are exceeded."""
    def __init__(self, limit_name: str, actual_value: Any, max_allowed: Any, details: Optional[dict] = None):
        msg = f"Structured data limit '{limit_name}' exceeded: actual {actual_value} > allowed {max_allowed}"
        d = {"limit": limit_name, "actual": actual_value, "allowed": max_allowed}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.limit_name = limit_name
        self.actual_value = actual_value
        self.max_allowed = max_allowed


class StructuredDataProviderError(StructuredDataError):
    """Base exception for structured data provider and transport failures."""
    pass


class StructuredDataCancelledError(StructuredDataProviderError):
    """Raised when a structured data retrieval operation is cancelled by the caller."""
    def __init__(self, target: str = "", operation: str = "", message: str = "", details: Optional[dict] = None):
        msg = f"Structured data operation '{operation}' for '{target}' was cancelled"
        if message:
            msg += f": {message}"
        d = {"target": target, "operation": operation}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.target = target
        self.operation = operation


class StructuredDataTimeoutError(StructuredDataProviderError):
    """Raised when a structured data retrieval operation times out."""
    def __init__(self, target: str, operation: str = "request", timeout_seconds: float = 30.0, details: Optional[dict] = None):
        msg = f"Structured data operation '{operation}' for '{target}' timed out after {timeout_seconds}s"
        d = {"target": target, "operation": operation, "timeout_seconds": timeout_seconds}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.target = target
        self.operation = operation
        self.timeout_seconds = timeout_seconds


class StructuredDataAuthenticationError(StructuredDataProviderError):
    """Raised when access to a structured data endpoint is denied due to missing/invalid credentials (401)."""
    def __init__(self, target: str, status_code: int = 401, message: str = "Access denied", details: Optional[dict] = None):
        msg = f"Structured data authentication failure ({status_code}) for '{target}': {message}"
        d = {"target": target, "status_code": status_code, "message": message}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.target = target
        self.status_code = status_code


class StructuredDataAuthorizationError(StructuredDataAuthenticationError):
    """Raised when an authenticated client lacks permissions to access a resource (403 Forbidden)."""
    def __init__(self, target: str, status_code: int = 403, message: str = "Forbidden: Insufficient permissions", details: Optional[dict] = None):
        super().__init__(target=target, status_code=status_code, message=message, details=details)


class StructuredDataRateLimitError(StructuredDataProviderError):
    """Raised when an endpoint's rate limit is exceeded (HTTP 429)."""
    def __init__(
        self,
        provider_id: str,
        retry_after_seconds: float = 60.0,
        message: Optional[str] = None,
        details: Optional[dict] = None,
    ):
        msg = message or f"Structured data provider '{provider_id}' rate limit exceeded (retry after {retry_after_seconds}s)"
        d = {"provider_id": provider_id, "retry_after_seconds": retry_after_seconds}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.provider_id = provider_id
        self.retry_after_seconds = retry_after_seconds


class StructuredDataQuotaExceededError(StructuredDataRateLimitError):
    """Raised when a structured data provider or endpoint daily/monthly quota is fully exhausted."""
    def __init__(self, provider_id: str, message: str = "Resource quota exhausted", details: Optional[dict] = None):
        msg = f"Structured data provider '{provider_id}' quota exhausted: {message}"
        d = {"provider_id": provider_id, "quota_message": message}
        if details:
            d.update(details)
        super().__init__(provider_id=provider_id, retry_after_seconds=3600.0, message=msg, details=d)
        self.quota_message = message


class StructuredDataNotFoundError(StructuredDataProviderError):
    """Raised when a requested structured data resource or endpoint is not found (HTTP 404)."""
    def __init__(self, target: str, message: str = "Resource not found", details: Optional[dict] = None):
        msg = f"Structured data resource '{target}' not found: {message}"
        d = {"target": target, "message": message}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.target = target


class StructuredDataMalformedResponseError(StructuredDataProviderError):
    """Raised when response content cannot be parsed according to declared content type."""
    def __init__(self, target: str, content_type: str, reason: str, details: Optional[dict] = None):
        msg = f"Malformed structured data from '{target}' ({content_type}): {reason}"
        d = {"target": target, "content_type": content_type, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.target = target
        self.content_type = content_type
        self.reason = reason


class StructuredDataSecurityError(StructuredDataProviderError):
    """Raised when a target URL violates SSRF or security constraints."""
    def __init__(self, target: str, reason: str, details: Optional[dict] = None):
        msg = f"Structured data security policy violation for '{target}': {reason}"
        d = {"target": target, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.target = target
        self.reason = reason


class StructuredDataPolicyViolationError(StructuredDataSecurityError):
    """Raised when a request violates domain scope, method policy, header rules, or body limits."""
    pass


# Structured Data Query Errors (Phase 1 / Part 7.6)
class StructuredDataQueryError(StructuredDataError):
    """Base exception for deterministic structured query and filtering failures."""
    pass


class StructuredDataInvalidFilterError(StructuredDataQueryError):
    """Raised when a query filter specification is malformed or invalid."""
    def __init__(self, reason: str, details: Optional[dict] = None):
        msg = f"Invalid structured query filter: {reason}"
        super().__init__(msg, details=details)
        self.reason = reason


class StructuredDataInvalidOperatorError(StructuredDataQueryError):
    """Raised when an unsupported or mismatched filter operator is used."""
    def __init__(self, operator: str, reason: str, details: Optional[dict] = None):
        msg = f"Invalid structured query operator '{operator}': {reason}"
        d = {"operator": operator, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.operator = operator
        self.reason = reason


class StructuredDataInvalidFieldError(StructuredDataQueryError):
    """Raised when a query field name, path, or projection is invalid or dangerous."""
    def __init__(self, field_name: str, reason: str, details: Optional[dict] = None):
        msg = f"Invalid structured query field '{field_name}': {reason}"
        d = {"field": field_name, "reason": reason}
        if details:
            d.update(details)
        super().__init__(msg, details=d)
        self.field_name = field_name
        self.reason = reason



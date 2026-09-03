from __future__ import annotations

import json
import logging
import os
import socket
from typing import Any, Callable, Optional
import urllib.error
import urllib.parse
import urllib.request

from core.research.errors import (
    SearchAuthenticationError,
    SearchConfigurationError,
    SearchError,
    SearchParameterValidationError,
    SearchProviderError,
    SearchRateLimitError,
    SearchSecurityError,
    SearchTimeoutError,
)
from core.research.search.config import SearchConfig
from core.research.search.models import (
    SearchParameters,
    SearchResponse,
    SearchResultItem,
)
from core.research.search.normalization import (
    extract_domain,
    normalize_url,
)
from core.research.search.provider import SearchProvider
from core.research.search.security import (
    sanitize_error,
    sanitize_secret,
    validate_network_target,
)

logger = logging.getLogger("AutonomOS.Research.Providers.Tavily")


class TavilySearchProvider(SearchProvider):
    """
    Production SearchProvider communicating with the Tavily AI Search API.
    
    Guarantees:
    1. Zero external dependencies (uses standard library urllib).
    2. API keys and secrets are never leaked in logs, exceptions, or payloads.
    3. Respects network security boundary (SSRF protection).
    4. Deterministically maps HTTP failure codes (400, 401, 403, 404, 408, 429, 5xx) to structured SearchError taxonomy.
    """

    def __init__(
        self,
        config: Optional[SearchConfig] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        transport_fn: Optional[Callable[[urllib.request.Request, float], bytes]] = None,
    ):
        super().__init__(provider_id="tavily", name="Tavily AI Search Provider")
        self._config = config or SearchConfig.from_env()

        # Allow explicit parameter overrides
        self._api_key = api_key or self._config.api_key or os.getenv("TAVILY_API_KEY") or os.getenv("SEARCH_API_KEY")
        raw_url = base_url or self._config.base_url or os.getenv("TAVILY_BASE_URL") or "https://api.tavily.com"
        self._base_url = raw_url.rstrip("/")
        self._endpoint = self._base_url if self._base_url.endswith("/search") else f"{self._base_url}/search"
        self._timeout = float(timeout_seconds if timeout_seconds is not None else self._config.timeout_seconds)
        self._transport_fn = transport_fn  # Used for testing/mocking transport

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def execute_search(self, params: SearchParameters) -> SearchResponse:
        """
        Execute search query against Tavily HTTP API.
        """
        if not self._api_key:
            raise SearchAuthenticationError(
                self.provider_id,
                "Tavily API key is missing. Configure 'SEARCH_API_KEY' or 'TAVILY_API_KEY' environment variable.",
            )

        # 1. Network security pre-check on target endpoint
        validate_network_target(self._endpoint, allow_localhost=self._config.allow_localhost)

        # 2. Build structured request payload
        payload: dict[str, Any] = {
            "api_key": self._api_key,
            "query": params.query,
            "max_results": min(params.limit, 20),
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }

        if params.domain_allowlist:
            payload["include_domains"] = list(params.domain_allowlist)
        if params.domain_blocklist:
            payload["exclude_domains"] = list(params.domain_blocklist)
        if params.freshness:
            payload["time_range"] = params.freshness

        body_bytes = json.dumps(payload).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self._config.user_agent,
            **self._config.custom_headers,
        }

        req = urllib.request.Request(
            url=self._endpoint,
            data=body_bytes,
            headers=headers,
            method="POST",
        )

        # 3. Transport execution with timeout
        raw_bytes: bytes
        try:
            if self._transport_fn:
                raw_bytes = self._transport_fn(req, self._timeout)
            else:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    raw_bytes = resp.read()

        except urllib.error.HTTPError as err:
            err_body = ""
            try:
                err_body = err.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            sanitized_body = sanitize_secret(err_body, secrets=[self._api_key])

            # HTTP 400 / 422: Parameter validation error
            if err.code in (400, 422):
                raise SearchParameterValidationError(
                    "query",
                    f"Tavily API parameter error (HTTP {err.code}): {sanitized_body}",
                ) from err

            # HTTP 401 / 403: Authentication or Authorization failure
            if err.code in (401, 403):
                raise SearchAuthenticationError(
                    self.provider_id,
                    f"Tavily authentication failed (HTTP {err.code}): {sanitized_body}",
                ) from err

            # HTTP 404: Endpoint not found
            if err.code == 404:
                raise SearchProviderError(
                    self.provider_id,
                    f"Tavily endpoint '{self._endpoint}' not found (HTTP 404). Check SEARCH_BASE_URL configuration.",
                    status_code=404,
                ) from err

            # HTTP 408: Request Timeout
            if err.code == 408:
                raise SearchTimeoutError(
                    self.provider_id,
                    timeout_seconds=self._timeout,
                    details={"body": sanitized_body},
                ) from err

            # HTTP 429: Rate Limit Exceeded
            if err.code == 429:
                retry_after_val: Optional[float] = None
                raw_retry = err.headers.get("Retry-After") if err.headers else None
                if raw_retry:
                    try:
                        retry_after_val = float(raw_retry)
                    except (ValueError, TypeError):
                        retry_after_val = None

                raise SearchRateLimitError(
                    self.provider_id,
                    retry_after_seconds=retry_after_val,
                    details={"body": sanitized_body},
                ) from err

            # HTTP 5xx: Server/upstream error
            if 500 <= err.code < 600:
                raise SearchProviderError(
                    self.provider_id,
                    f"Tavily upstream server error (HTTP {err.code}): {sanitized_body}",
                    status_code=err.code,
                ) from err

            # Other HTTP codes
            raise SearchProviderError(
                self.provider_id,
                f"Tavily HTTP {err.code}: {sanitized_body}",
                status_code=err.code,
            ) from err

        except urllib.error.URLError as err:
            reason_str = str(err.reason)
            if isinstance(err.reason, (socket.timeout, TimeoutError)) or "timed out" in reason_str.lower():
                raise SearchTimeoutError(self.provider_id, timeout_seconds=self._timeout) from err
            sanitized_reason = sanitize_secret(reason_str, secrets=[self._api_key])
            raise SearchProviderError(
                self.provider_id,
                f"Network transport error: {sanitized_reason}",
            ) from err

        except (socket.timeout, TimeoutError) as err:
            raise SearchTimeoutError(self.provider_id, timeout_seconds=self._timeout) from err

        except Exception as err:
            if isinstance(err, SearchError):
                raise
            sanitized_msg = sanitize_secret(str(err), secrets=[self._api_key])
            raise SearchProviderError(self.provider_id, f"Unexpected request failure: {sanitized_msg}") from err

        # 4. Parse response JSON
        try:
            parsed_data = json.loads(raw_bytes.decode("utf-8"))
        except Exception as err:
            raise SearchProviderError(
                self.provider_id,
                f"Malformed JSON response from Tavily API: {err}",
            ) from err

        if not isinstance(parsed_data, dict):
            raise SearchProviderError(
                self.provider_id,
                f"Unexpected response format from Tavily API: expected object, got {type(parsed_data).__name__}",
            )

        raw_results = parsed_data.get("results")
        if raw_results is None:
            raw_results = []
        elif not isinstance(raw_results, list):
            raise SearchProviderError(
                self.provider_id,
                f"Unexpected results field from Tavily API: expected list, got {type(raw_results).__name__}",
            )

        # 5. Transform into normalized SearchResultItem objects
        normalized_results: list[SearchResultItem] = []
        for idx, item in enumerate(raw_results):
            if not isinstance(item, dict):
                continue

            raw_url = str(item.get("url") or "").strip()
            if not raw_url:
                continue

            raw_title = str(item.get("title") or f"Result {idx+1}").strip()
            raw_snippet = str(item.get("content") or item.get("snippet") or "").strip()
            pub_date = item.get("published_date")
            score = item.get("score")

            normalized_results.append(
                SearchResultItem(
                    title=raw_title,
                    url=raw_url,
                    original_url=raw_url,
                    normalized_url=normalize_url(raw_url),
                    snippet=raw_snippet,
                    domain=extract_domain(raw_url),
                    rank=idx + 1,
                    published_date=str(pub_date) if pub_date is not None else None,
                    provider=self.provider_id,
                    metadata={"score": score} if score is not None else {},
                )
            )

        return SearchResponse(
            query=params.query,
            results=normalized_results,
            total_results=len(normalized_results),
            provider=self.provider_id,
        )

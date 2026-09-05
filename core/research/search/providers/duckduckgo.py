"""
DuckDuckGo Keyless Search Provider for AutonomOS.

Provides live web search capabilities with zero external dependencies and zero API keys.
Scrapes and parses DuckDuckGo HTML endpoints (https://html.duckduckgo.com/html/),
extracts titles, URLs, and snippets, and automatically unwraps DuckDuckGo redirect
proxies (/l/?uddg=...) into verified destination URLs.
"""
from __future__ import annotations

from html.parser import HTMLParser
import logging
import os
import re
import socket
import time
from typing import Any, Callable, Optional
import urllib.error
import urllib.parse
import urllib.request

from core.research.errors import (
    SearchAuthenticationError,
    SearchError,
    SearchParameterValidationError,
    SearchProviderError,
    SearchRateLimitError,
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
    validate_network_target,
)

logger = logging.getLogger("AutonomOS.Research.Providers.DuckDuckGo")

DEFAULT_DDG_BASE_URL = "https://html.duckduckgo.com/html/"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def unquote_ddg_redirect(raw_url: str) -> str:
    """
    Unwrap DuckDuckGo redirect links (e.g. //duckduckgo.com/l/?uddg=<url_encoded>&...).
    Returns the decoded destination URL if uddg parameter exists, otherwise raw_url.
    """
    if not raw_url:
        return ""

    candidate = raw_url.strip()
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"

    if "uddg=" in candidate:
        try:
            parsed = urllib.parse.urlsplit(candidate)
            query_params = urllib.parse.parse_qs(parsed.query)
            if "uddg" in query_params and query_params["uddg"]:
                decoded = urllib.parse.unquote(query_params["uddg"][0])
                if decoded.startswith(("http://", "https://")):
                    return decoded
        except Exception:
            pass

    return candidate


class DDGHTMLResultParser(HTMLParser):
    """
    Robust HTML parser for DuckDuckGo HTML search results.
    Extracts structured results consisting of title, target URL, and snippet text.
    """

    def __init__(self):
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current_result: Optional[dict[str, str]] = None
        self._in_title_link = False
        self._in_snippet = False
        self._title_buffer: list[str] = []
        self._snippet_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]):
        attr_dict = {k.lower(): (v or "") for k, v in attrs}
        classes = attr_dict.get("class", "").split()

        # Check for start of a result block
        if tag == "div" and any("result__body" in c or "web-result" in c for c in classes):
            self._finalize_current_result()
            self._current_result = {"title": "", "url": "", "snippet": ""}

        # Title link: <a class="result__a" href="..."> or <a> inside <h2 class="result__title">
        if tag == "a" and any("result__a" in c or "result__url" in c for c in classes):
            href = attr_dict.get("href", "")
            if href:
                real_url = unquote_ddg_redirect(href)
                if self._current_result is None:
                    self._current_result = {"title": "", "url": "", "snippet": ""}
                if not self._current_result.get("url"):
                    self._current_result["url"] = real_url
            self._in_title_link = True
            self._title_buffer = []

        # Snippet block: <a class="result__snippet" ...> or <div class="result__snippet">
        if any("result__snippet" in c for c in classes):
            self._in_snippet = True
            self._snippet_buffer = []

    def handle_endtag(self, tag: str):
        if tag == "a" and self._in_title_link:
            self._in_title_link = False
            title_text = " ".join("".join(self._title_buffer).split()).strip()
            if self._current_result and title_text and not self._current_result.get("title"):
                self._current_result["title"] = title_text
            self._title_buffer = []

        if self._in_snippet and tag in ("a", "div", "span"):
            self._in_snippet = False
            snippet_text = " ".join("".join(self._snippet_buffer).split()).strip()
            if self._current_result and snippet_text and not self._current_result.get("snippet"):
                self._current_result["snippet"] = snippet_text
            self._snippet_buffer = []

        if tag == "div" and self._current_result:
            # Result container closed
            if self._current_result.get("url") and self._current_result.get("title"):
                self._finalize_current_result()

    def handle_data(self, data: str):
        if self._in_title_link:
            self._title_buffer.append(data)
        elif self._in_snippet:
            self._snippet_buffer.append(data)

    def _finalize_current_result(self):
        if self._current_result:
            url = self._current_result.get("url", "").strip()
            title = self._current_result.get("title", "").strip()
            snippet = self._current_result.get("snippet", "").strip()
            if url and title and url.startswith(("http://", "https://")):
                self.results.append({
                    "url": url,
                    "title": title,
                    "snippet": snippet,
                })
            self._current_result = None

    def close(self):
        super().close()
        self._finalize_current_result()


def parse_ddg_html(html_text: str) -> list[dict[str, str]]:
    """
    Parse DuckDuckGo HTML using the HTMLParser with regex fallback.
    """
    parser = DDGHTMLResultParser()
    try:
        parser.feed(html_text)
        parser.close()
        if parser.results:
            return parser.results
    except Exception as e:
        logger.debug(f"DDGHTMLResultParser error: {e}, falling back to regex")

    # Regex fallback for non-standard HTML variations
    fallback_results: list[dict[str, str]] = []
    # Match <a class="result__a" href="(...)">(...)</a>
    link_matches = re.finditer(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        html_text,
        re.DOTALL | re.IGNORECASE,
    )
    for m in link_matches:
        raw_href = m.group(1)
        raw_title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        url = unquote_ddg_redirect(raw_href)
        if url.startswith(("http://", "https://")) and raw_title:
            fallback_results.append({
                "url": url,
                "title": raw_title,
                "snippet": "",
            })

    return fallback_results


class DuckDuckGoSearchProvider(SearchProvider):
    """
    Production Keyless SearchProvider communicating with DuckDuckGo.
    
    Guarantees:
    1. Zero external dependencies (uses standard library urllib and html.parser).
    2. Zero API keys required for operation.
    3. Respects network security boundary (SSRF protection).
    4. Automatically decodes DuckDuckGo /l/?uddg= redirect URLs to target destinations.
    5. Deterministically maps HTTP failure codes to structured SearchError taxonomy.
    """

    def __init__(
        self,
        config: Optional[SearchConfig] = None,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        transport_fn: Optional[Callable[[urllib.request.Request, float], bytes]] = None,
    ):
        super().__init__(provider_id="duckduckgo", name="DuckDuckGo Keyless Search Provider")
        self._config = config or SearchConfig.from_env()

        raw_url = base_url or self._config.base_url or os.getenv("DUCKDUCKGO_BASE_URL") or DEFAULT_DDG_BASE_URL
        self._endpoint = raw_url.strip()
        self._timeout = float(timeout_seconds if timeout_seconds is not None else self._config.timeout_seconds)
        self._transport_fn = transport_fn
        self._user_agent = getattr(self._config, "user_agent", None) or DEFAULT_USER_AGENT

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def execute_search(self, params: SearchParameters) -> SearchResponse:
        """
        Execute search query against DuckDuckGo HTML endpoint.
        """
        # 1. Network security check
        validate_network_target(self._endpoint, allow_localhost=self._config.allow_localhost)

        # 2. Build HTTP request
        post_data = urllib.parse.urlencode({
            "q": params.query,
            "b": "",
            "kl": "us-en",
        }).encode("utf-8")

        headers = {
            "User-Agent": self._user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        req = urllib.request.Request(
            self._endpoint,
            data=post_data,
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
                    details={"body": err_body[:200]},
                ) from err

            if 500 <= err.code < 600:
                raise SearchProviderError(
                    self.provider_id,
                    f"DuckDuckGo upstream server error (HTTP {err.code}): {err_body[:200]}",
                    status_code=err.code,
                ) from err

            raise SearchProviderError(
                self.provider_id,
                f"DuckDuckGo HTTP {err.code}: {err_body[:200]}",
                status_code=err.code,
            ) from err

        except urllib.error.URLError as err:
            reason_str = str(err.reason)
            if isinstance(err.reason, (socket.timeout, TimeoutError)) or "timed out" in reason_str.lower():
                raise SearchTimeoutError(self.provider_id, timeout_seconds=self._timeout) from err
            raise SearchProviderError(
                self.provider_id,
                f"Network transport error: {reason_str}",
            ) from err

        except (socket.timeout, TimeoutError) as err:
            raise SearchTimeoutError(self.provider_id, timeout_seconds=self._timeout) from err

        except Exception as err:
            if isinstance(err, SearchError):
                raise
            raise SearchProviderError(self.provider_id, f"Unexpected request failure: {sanitize_error(err)}") from err

        # 4. Parse HTML and extract items
        html_text = raw_bytes.decode("utf-8", errors="replace")
        extracted_items = parse_ddg_html(html_text)

        # 5. Transform into normalized SearchResultItem objects
        normalized_results: list[SearchResultItem] = []
        for idx, item in enumerate(extracted_items):
            url = item.get("url", "").strip()
            title = item.get("title", f"Result {idx+1}").strip()
            snippet = item.get("snippet", "").strip()
            if not url:
                continue

            # Sequential relevance decay (1.0 -> 0.1)
            score = max(0.1, round(1.0 - (idx * 0.05), 2))

            normalized_results.append(
                SearchResultItem(
                    title=title,
                    url=url,
                    original_url=url,
                    normalized_url=normalize_url(url),
                    snippet=snippet,
                    domain=extract_domain(url),
                    rank=idx + 1,
                    provider=self.provider_id,
                    metadata={"score": score, "source": "html_scrape"},
                )
            )

        return SearchResponse(
            query=params.query,
            results=normalized_results,
            total_results=len(normalized_results),
            provider=self.provider_id,
        )

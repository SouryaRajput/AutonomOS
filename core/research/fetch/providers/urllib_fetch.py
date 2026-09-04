"""
Standard Library Urllib Fetch Provider (Phase 1 / Part 3 / Step 3).

Executes bounded HTTP/HTTPS web fetches using Python standard library urllib.request.
Enforces SSRF redirect validation on every hop, redirect loop/limit detection,
streaming size limits, decompression bounds (gzip/deflate), and robust charset decoding.
"""
from __future__ import annotations

import gzip
import logging
import socket
import ssl
import time
from typing import Optional
import urllib.error
import urllib.parse
import urllib.request
import zlib

from core.research.errors import (
    FetchHttpError,
    FetchNetworkError,
    FetchRedirectLimitError,
    FetchSecurityError,
    FetchSizeLimitError,
    FetchTimeoutError,
)
from core.research.fetch.config import FetchConfig
from core.research.fetch.models import FetchParameters, FetchResponse, utc_now
from core.research.fetch.provider import FetchProvider
from core.research.search.security import (
    compute_effective_timeout,
    sanitize_error,
    sanitize_headers,
    validate_network_target,
)

logger = logging.getLogger("AutonomOS.Research.UrllibFetchProvider")


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """
    HTTP redirect handler that enforces strict SSRF boundary validation on every hop,
    detects circular redirect loops, and bounds total redirect depth.
    """

    def __init__(self, initial_url: str, allow_localhost: bool = False, max_redirects: int = 5):
        super().__init__()
        self.initial_url = initial_url
        self.allow_localhost = allow_localhost
        self.max_redirects = max_redirects
        self.redirect_chain: list[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 1. Loop detection
        if newurl == self.initial_url or newurl in self.redirect_chain or newurl == req.full_url:
            raise FetchRedirectLimitError(
                url=newurl,
                redirect_count=len(self.redirect_chain) + 1,
                max_redirects=self.max_redirects,
                is_loop=True,
            )

        # 2. Count limit
        if len(self.redirect_chain) >= self.max_redirects:
            raise FetchRedirectLimitError(
                url=newurl,
                redirect_count=len(self.redirect_chain) + 1,
                max_redirects=self.max_redirects,
                is_loop=False,
            )

        # 3. Validate redirect target against SSRF boundaries
        try:
            validate_network_target(newurl, allow_localhost=self.allow_localhost)
        except Exception as sec_err:
            raise FetchSecurityError(
                newurl,
                f"Redirect destination violated network security policy: {sec_err}",
            ) from sec_err

        self.redirect_chain.append(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibFetchProvider(FetchProvider):
    """
    Production HTTP fetch provider using standard library urllib.
    """

    def __init__(self, provider_id: str = "urllib-fetch", config: Optional[FetchConfig] = None):
        super().__init__(provider_id=provider_id, config=config)

    def execute_fetch(self, params: FetchParameters) -> FetchResponse:
        effective_timeout = compute_effective_timeout(
            task_timeout=params.timeout_seconds,
            config_timeout=self.config.default_timeout_seconds,
            elapsed_seconds=0.0,
        )

        headers = {
            "User-Agent": self.config.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, identity",
        }
        # Merge user headers
        for k, v in params.headers.items():
            headers[k] = v

        req = urllib.request.Request(
            url=params.url,
            headers=headers,
            method="GET",
        )

        redirect_handler = SafeRedirectHandler(
            initial_url=params.url,
            allow_localhost=self.config.allow_localhost,
            max_redirects=params.max_redirects,
        )

        opener = urllib.request.build_opener(redirect_handler)

        start_time = time.perf_counter()
        try:
            with opener.open(req, timeout=effective_timeout) as resp:
                final_url = resp.geturl()
                status_code = resp.getcode()
                raw_headers = dict(resp.headers.items())
                content_type = resp.headers.get("Content-Type", "text/html")
                content_encoding = resp.headers.get("Content-Encoding", "").strip().lower()

                # Check Content-Length header early rejection
                cl_header = resp.headers.get("Content-Length")
                if cl_header and cl_header.strip().isdigit():
                    expected_length = int(cl_header.strip())
                    if expected_length > params.max_bytes:
                        raise FetchSizeLimitError(
                            url=params.url,
                            size_bytes=expected_length,
                            max_bytes=params.max_bytes,
                        )

                # Parse charset
                charset = "utf-8"
                if "charset=" in content_type.lower():
                    try:
                        raw_charset = content_type.lower().split("charset=")[1].split(";")[0].strip()
                        charset = raw_charset.replace('"', '').replace("'", "")
                    except Exception:
                        charset = "utf-8"

                # Stream response bytes with hard ceiling
                chunks = []
                total_bytes = 0
                chunk_size = 16384  # 16 KB

                while True:
                    read_len = min(chunk_size, params.max_bytes - total_bytes + 1)
                    chunk = resp.read(read_len)
                    if not chunk:
                        break
                    
                    total_bytes += len(chunk)
                    if total_bytes > params.max_bytes:
                        raise FetchSizeLimitError(
                            url=params.url,
                            size_bytes=total_bytes,
                            max_bytes=params.max_bytes,
                        )
                    chunks.append(chunk)

                raw_body = b"".join(chunks)

                # Handle Decompression
                if content_encoding == "gzip":
                    try:
                        decompressed = gzip.decompress(raw_body)
                    except Exception as decomp_err:
                        raise FetchNetworkError(params.url, f"Gzip decompression failed: {decomp_err}") from decomp_err
                    if len(decompressed) > params.max_bytes:
                        raise FetchSizeLimitError(
                            url=params.url,
                            size_bytes=len(decompressed),
                            max_bytes=params.max_bytes,
                        )
                    body_bytes = decompressed
                elif content_encoding == "deflate":
                    try:
                        decompressed = zlib.decompress(raw_body)
                    except Exception as decomp_err:
                        # Try raw deflate without header if zlib fails
                        try:
                            decompressed = zlib.decompress(raw_body, -zlib.MAX_WBITS)
                        except Exception:
                            raise FetchNetworkError(params.url, f"Deflate decompression failed: {decomp_err}") from decomp_err
                    if len(decompressed) > params.max_bytes:
                        raise FetchSizeLimitError(
                            url=params.url,
                            size_bytes=len(decompressed),
                            max_bytes=params.max_bytes,
                        )
                    body_bytes = decompressed
                else:
                    body_bytes = raw_body

                # Decode text with fallback
                try:
                    body_text = body_bytes.decode(charset, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    body_text = body_bytes.decode("utf-8", errors="replace")

                elapsed = round(time.perf_counter() - start_time, 4)

                return FetchResponse(
                    url=final_url,
                    original_url=params.url,
                    status_code=status_code,
                    headers=sanitize_headers(raw_headers),
                    content_type=content_type,
                    body_bytes=body_bytes,
                    body_text=body_text,
                    charset=charset,
                    bytes_fetched=len(body_bytes),
                    execution_time_seconds=elapsed,
                    redirect_chain=redirect_handler.redirect_chain,
                    retrieved_at=utc_now(),
                    provider_id=self.provider_id,
                )

        except urllib.error.HTTPError as e:
            sanitized_msg = sanitize_error(str(e))
            raw_err_headers = dict(e.headers.items()) if hasattr(e, "headers") and e.headers else {}
            raise FetchHttpError(
                url=params.url,
                status_code=e.code,
                message=sanitized_msg,
                headers=sanitize_headers(raw_err_headers),
            ) from e

        except (socket.timeout, TimeoutError) as e:
            raise FetchTimeoutError(params.url, effective_timeout) from e

        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise FetchTimeoutError(params.url, effective_timeout) from e
            sanitized_reason = sanitize_error(str(e.reason))
            raise FetchNetworkError(params.url, sanitized_reason) from e

        except ssl.SSLError as e:
            sanitized_ssl = sanitize_error(str(e))
            raise FetchNetworkError(params.url, f"SSL error: {sanitized_ssl}") from e

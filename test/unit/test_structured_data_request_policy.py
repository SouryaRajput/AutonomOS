"""
Unit tests for Structured Data Request Construction and Request Security Policy (Phase 1 / Part 7.3).

Tests verify that:
- Untrusted research/task input cannot execute arbitrary or dangerous network requests.
- Endpoint validation strictly enforces valid URL, allowed schemes, domain scoping, and SSRF barriers.
- HTTP methods are restricted and arbitrary methods are rejected.
- Query parameters are bounded and deterministically encoded.
- Request bodies enforce size limits, content types, and reject binary/script executables.
- Headers are allowlisted, CRLF injection is blocked, and credentials are redacted in logs.
- Redirects enforce relative resolution, scheme downgrade prevention, and domain containment.
- Resource limits (timeout, decompressed size) are bounded.
"""
from __future__ import annotations

import pytest

from core.research.contracts.crawler_task import CrawlerTask
from core.research.errors import (
    StructuredDataPolicyViolationError,
    StructuredDataSecurityError,
)
from core.research.structured import (
    HttpMethod,
    PaginationConfig,
    StructuredDataLimits,
    StructuredDataRequest,
    StructuredDataRequestBuilder,
    StructuredDataSecurityPolicy,
)


# -----------------------------------------------------------------------------
# 1. Builder Construction & Factory Tests
# -----------------------------------------------------------------------------

class TestStructuredDataRequestBuilder:
    """Verify fluent builder and factory instantiation."""

    def test_build_valid_request_with_defaults(self):
        builder = (
            StructuredDataRequestBuilder()
            .with_endpoint("https://api.github.com/repos/python/cpython")
            .with_method("GET")
            .with_query_param("sort", "stars")
            .with_header("Accept", "application/json")
        )
        req = builder.build()

        assert isinstance(req, StructuredDataRequest)
        assert req.endpoint_url == "https://api.github.com/repos/python/cpython"
        assert req.method == HttpMethod.GET
        assert req.query_params == {"sort": "stars"}
        assert req.headers == {"Accept": "application/json"}
        assert req.limits.timeout_seconds == 10.0
        assert req.request_id.startswith("sreq_")

    def test_from_task_factory(self):
        task = CrawlerTask(
            task_id="task-12345",
            request_id="req-111",
            plan_id="plan-222",
            question_id="q-333",
            query_or_target="https://api.example.com/v1/data",
            timeout_seconds=25,
            parameters={
                "method": "POST",
                "query_params": {"version": "2"},
                "headers": {"Content-Type": "application/json"},
                "body": {"filter": "active"},
            },
        )
        policy = StructuredDataSecurityPolicy(allowed_domains={"example.com"})
        builder = StructuredDataRequestBuilder.from_task(task, policy=policy)
        req = builder.build()

        assert req.request_id == "task-12345"
        assert req.endpoint_url == "https://api.example.com/v1/data"
        assert req.method == HttpMethod.POST
        assert req.query_params == {"version": "2"}
        assert req.headers == {"Content-Type": "application/json"}
        assert req.body == {"filter": "active"}
        assert req.limits.timeout_seconds == 25.0
        assert req.metadata["plan_id"] == "plan-222"
        assert req.metadata["task_id"] == "task-12345"

    def test_from_dict_factory(self):
        data = {
            "request_id": "custom-req-1",
            "endpoint_url": "https://api.example.com/v1/stats",
            "method": "GET",
            "query_params": {"limit": 10},
            "headers": {"Accept": "application/json"},
            "metadata": {"source": "test"},
        }
        builder = StructuredDataRequestBuilder.from_dict(data)
        req = builder.build()

        assert req.request_id == "custom-req-1"
        assert req.endpoint_url == "https://api.example.com/v1/stats"
        assert req.method == HttpMethod.GET
        assert req.query_params == {"limit": 10}
        assert req.metadata["source"] == "test"


# -----------------------------------------------------------------------------
# 2. Endpoint & Scheme Validation
# -----------------------------------------------------------------------------

class TestEndpointValidation:
    """Verify endpoint URL validation and scheme restrictions."""

    def test_empty_or_whitespace_url_raises(self):
        policy = StructuredDataSecurityPolicy()
        for empty_val in ("", "   ", None):
            with pytest.raises(StructuredDataPolicyViolationError) as exc_info:
                policy.validate_endpoint(empty_val)
            assert "Endpoint URL must be a non-empty string" in str(exc_info.value)

    def test_disallowed_schemes_rejected(self):
        policy = StructuredDataSecurityPolicy(allowed_schemes={"http", "https"})
        disallowed = [
            "ftp://files.example.com/data.csv",
            "file:///etc/passwd",
            "gopher://gopher.example.com/",
            "ldap://ldap.example.com/dc=example",
            "javascript:alert(1)",
        ]
        for url in disallowed:
            with pytest.raises(StructuredDataPolicyViolationError) as exc_info:
                policy.validate_endpoint(url)
            assert "is not permitted by policy" in str(exc_info.value) or "Disallowed URL scheme" in str(exc_info.value)

    def test_mock_scheme_handling(self):
        strict_policy = StructuredDataSecurityPolicy(allow_mock=False)
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            strict_policy.validate_endpoint("mock://api.example.com/test")
        assert "Mock scheme 'mock' is not permitted" in str(exc.value)

        mock_policy = StructuredDataSecurityPolicy(allow_mock=True)
        valid = mock_policy.validate_endpoint("mock://api.example.com/test")
        assert valid == "mock://api.example.com/test"

    def test_embedded_credentials_in_url_rejected(self):
        policy = StructuredDataSecurityPolicy()
        urls_with_creds = [
            "https://admin:secret123@api.example.com/data",
            "https://user@api.example.com/data",
            "http://token:@api.example.com/data",
        ]
        for url in urls_with_creds:
            with pytest.raises(StructuredDataPolicyViolationError) as exc:
                policy.validate_endpoint(url)
            assert "URLs with embedded user credentials" in str(exc.value) or "credentials" in str(exc.value)

    def test_control_characters_in_url_rejected(self):
        policy = StructuredDataSecurityPolicy()
        with pytest.raises(StructuredDataPolicyViolationError):
            policy.validate_endpoint("https://api.example.com/data\r\nInjected-Header: evil")


# -----------------------------------------------------------------------------
# 3. SSRF & Network Boundary Protection
# -----------------------------------------------------------------------------

class TestSSRFBoundaryProtection:
    """Verify that localhost, cloud metadata, and internal IPs are blocked."""

    def test_localhost_and_loopback_rejected_by_default(self):
        policy = StructuredDataSecurityPolicy(allow_localhost=False)
        loopbacks = [
            "http://localhost:8080/metrics",
            "http://127.0.0.1/status",
            "http://127.0.0.2/data",
            "http://[::1]/debug",
        ]
        for url in loopbacks:
            with pytest.raises(StructuredDataPolicyViolationError) as exc_info:
                policy.validate_endpoint(url)
            # Should be blocked for loopback / SSRF
            assert "loopback" in str(exc_info.value).lower() or "ssrf" in str(exc_info.value).lower()

    def test_cloud_metadata_always_rejected(self):
        # Cloud metadata must be blocked EVEN IF allow_localhost=True
        policy = StructuredDataSecurityPolicy(allow_localhost=True)
        metadata_targets = [
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://instance-data/latest/meta-data/",
        ]
        for url in metadata_targets:
            with pytest.raises(StructuredDataPolicyViolationError) as exc_info:
                policy.validate_endpoint(url)
            assert "metadata" in str(exc_info.value).lower()

    def test_private_subnets_rejected(self):
        policy = StructuredDataSecurityPolicy(allow_localhost=False)
        private_ips = [
            "http://10.0.0.1/config",
            "http://172.16.50.1/status",
            "http://192.168.1.1/admin",
        ]
        for url in private_ips:
            with pytest.raises(StructuredDataPolicyViolationError) as exc:
                policy.validate_endpoint(url)
            assert "private" in str(exc.value).lower()

    def test_internal_domain_suffixes_rejected(self):
        policy = StructuredDataSecurityPolicy(allow_localhost=False)
        internal_domains = [
            "http://service.corp/api",
            "http://cluster.internal/status",
            "http://router.local/admin",
            "http://node.lan/info",
        ]
        for url in internal_domains:
            with pytest.raises(StructuredDataPolicyViolationError) as exc:
                policy.validate_endpoint(url)
            assert "internal domain suffix" in str(exc.value).lower() or "restricted" in str(exc.value).lower()

    def test_decimal_ip_representation_rejected(self):
        policy = StructuredDataSecurityPolicy(allow_localhost=False)
        # 2130706433 is 127.0.0.1
        with pytest.raises(StructuredDataPolicyViolationError):
            policy.validate_endpoint("http://2130706433/admin")


# -----------------------------------------------------------------------------
# 4. Domain Scoping & Blocklists
# -----------------------------------------------------------------------------

class TestDomainScoping:
    """Verify domain allowlisting and blocklisting."""

    def test_domain_allowlist_enforcement(self):
        policy = StructuredDataSecurityPolicy(
            allowed_domains={"github.com", "api.github.com"}
        )
        # Matches exact allowed domain or subdomain
        assert policy.validate_endpoint("https://api.github.com/repos")
        assert policy.validate_endpoint("https://sub.api.github.com/v3")

        # Rejects unlisted domain
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_endpoint("https://api.gitlab.com/projects")
        assert "not within permitted domains" in str(exc.value)

        # Rejects lookalike domain
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_endpoint("https://evil-github.com/repos")
        assert "not within permitted domains" in str(exc.value)

    def test_domain_blocklist_enforcement(self):
        policy = StructuredDataSecurityPolicy(
            blocked_domains={"malicious-site.com", "sketchy.net"}
        )
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_endpoint("https://malicious-site.com/feed")
        assert "matches blocked domain policy" in str(exc.value)

        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_endpoint("https://api.sketchy.net/v1")
        assert "matches blocked domain policy" in str(exc.value)


# -----------------------------------------------------------------------------
# 5. HTTP Method Validation
# -----------------------------------------------------------------------------

class TestHttpMethodValidation:
    """Verify method allowlist and normalization."""

    def test_default_allowed_methods(self):
        policy = StructuredDataSecurityPolicy()
        assert policy.validate_method("GET") == HttpMethod.GET
        assert policy.validate_method("post") == HttpMethod.POST
        assert policy.validate_method("HEAD") == HttpMethod.HEAD
        assert policy.validate_method(HttpMethod.GET) == HttpMethod.GET

    def test_disallowed_methods_rejected(self):
        policy = StructuredDataSecurityPolicy(allowed_methods={"GET", "POST", "HEAD"})
        disallowed = ["DELETE", "PUT", "PATCH", "OPTIONS", "TRACE", "CONNECT", "CUSTOM"]
        for m in disallowed:
            with pytest.raises(StructuredDataPolicyViolationError) as exc:
                policy.validate_method(m)
            assert "is not permitted by policy" in str(exc.value)

    def test_invalid_type_raises(self):
        policy = StructuredDataSecurityPolicy()
        with pytest.raises(StructuredDataPolicyViolationError):
            policy.validate_method(12345)  # type: ignore


# -----------------------------------------------------------------------------
# 6. Query Parameters Validation & Deterministic Encoding
# -----------------------------------------------------------------------------

class TestQueryParamsValidation:
    """Verify query parameter bounds and deterministic encoding."""

    def test_query_param_bounds(self):
        policy = StructuredDataSecurityPolicy(
            max_params_count=3,
            max_key_length=10,
            max_value_length=20,
        )

        # Exceed parameter count
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_query_params({"a": 1, "b": 2, "c": 3, "d": 4})
        assert "parameter count" in str(exc.value).lower()

        # Exceed key length
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_query_params({"a_very_long_key_exceeding_10": "val"})
        assert "exceeds maximum length" in str(exc.value).lower()

        # Exceed value length
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_query_params({"short_key": "x" * 25})
        assert "exceeds maximum length" in str(exc.value).lower()

    def test_query_param_control_char_injection_blocked(self):
        policy = StructuredDataSecurityPolicy()
        with pytest.raises(StructuredDataPolicyViolationError):
            policy.validate_query_params({"key\r\n": "val"})

        with pytest.raises(StructuredDataPolicyViolationError):
            policy.validate_query_params({"key": "val\ninjected"})

    def test_query_param_unsupported_complex_type_blocked(self):
        policy = StructuredDataSecurityPolicy()
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_query_params({"nested": {"foo": "bar"}})
        assert "unsupported complex type" in str(exc.value).lower()

    def test_deterministic_encoding_order(self):
        policy = StructuredDataSecurityPolicy()
        params1 = {"b": "2", "a": "1", "c": ["z", "y"]}
        params2 = {"c": ["y", "z"], "a": "1", "b": "2"}

        encoded1 = policy.encode_query_params(params1)
        encoded2 = policy.encode_query_params(params2)

        # Keys and lists are deterministically sorted
        assert encoded1 == "a=1&b=2&c=y&c=z"
        assert encoded2 == "a=1&b=2&c=y&c=z"
        assert encoded1 == encoded2


# -----------------------------------------------------------------------------
# 7. Request Body Validation & Executable Content Rejection
# -----------------------------------------------------------------------------

class TestRequestBodyValidation:
    """Verify request body size limits, content types, and executable detection."""

    def test_valid_json_and_text_bodies(self):
        policy = StructuredDataSecurityPolicy()
        assert policy.validate_body({"query": "graphql { me { name } }"}, content_type="application/json")
        assert policy.validate_body('{"filter": "status=active"}', content_type="application/json")
        assert policy.validate_body("name=alice&age=30", content_type="application/x-www-form-urlencoded")
        assert policy.validate_body(None) is None

    def test_body_size_limit_exceeded(self):
        policy = StructuredDataSecurityPolicy(max_body_bytes=100)
        large_body = "x" * 101
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_body(large_body)
        assert "exceeds maximum limit" in str(exc.value)

    def test_disallowed_content_type_rejected(self):
        policy = StructuredDataSecurityPolicy(allowed_body_content_types={"application/json"})
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_body("<root></root>", content_type="application/xml")
        assert "Content-Type 'application/xml' is not permitted" in str(exc.value)

    def test_binary_executable_magic_rejected(self):
        policy = StructuredDataSecurityPolicy()

        # Linux ELF binary
        elf_body = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 20
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_body(elf_body)
        assert "executable binary signature" in str(exc.value)

        # Windows PE binary
        pe_body = b"MZ\x90\x00\x03\x00\x00\x00"
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_body(pe_body)
        assert "executable binary signature" in str(exc.value)

        # Unix shebang
        shebang_body = b"#!/bin/bash\nrm -rf /"
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_body(shebang_body)
        assert "executable script shebang" in str(exc.value)

    def test_script_and_command_injection_in_text_rejected(self):
        policy = StructuredDataSecurityPolicy()

        payloads = [
            '<script>alert("xss")</script>',
            '{"action": "javascript:evil()"}',
            '{"code": "eval(\'destroy\')"}',
            '{"run": "os.system(\'whoami\')"}',
            '{"cmd": "/bin/sh -c evil"}',
            '{"invoke": "__import__(\'os\')"}',
        ]
        for p in payloads:
            with pytest.raises(StructuredDataPolicyViolationError) as exc:
                policy.validate_body(p)
            assert "forbidden script or executable" in str(exc.value)


# -----------------------------------------------------------------------------
# 8. Headers Validation & Credential Redaction
# -----------------------------------------------------------------------------

class TestHeadersValidation:
    """Verify header allowlisting, CRLF protection, and credential logging safety."""

    def test_allowed_headers_accepted(self):
        policy = StructuredDataSecurityPolicy()
        headers = {
            "Accept": "application/json",
            "User-Agent": "AutonomOS-ResearchBot/1.0",
            "Authorization": "Bearer super-secret-key-12345",
        }
        validated = policy.validate_headers(headers)
        assert validated["Accept"] == "application/json"
        assert validated["Authorization"] == "Bearer super-secret-key-12345"

    def test_unauthorized_header_rejected_by_allowlist(self):
        policy = StructuredDataSecurityPolicy(enforce_header_allowlist=True)
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_headers({"X-Custom-Injected-Header": "dangerous"})
        assert "not in the allowed headers list" in str(exc.value)

    def test_crlf_header_injection_rejected(self):
        policy = StructuredDataSecurityPolicy()
        injections = [
            ({"Accept\r\nX-Injected": "true"}),
            ({"Accept": "application/json\r\nEvil-Header: true"}),
        ]
        for inj in injections:
            with pytest.raises(StructuredDataPolicyViolationError) as exc:
                policy.validate_headers(inj)
            assert "CRLF injection prevention" in str(exc.value)

    def test_sanitize_headers_for_logging_redacts_secrets(self):
        policy = StructuredDataSecurityPolicy()
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer super-secret-token",
            "X-Api-Key": "secret-api-key-999",
        }
        sanitized = policy.sanitize_headers_for_logging(headers)
        assert sanitized["Accept"] == "application/json"
        assert sanitized["Authorization"] == "[REDACTED]"
        assert sanitized["X-Api-Key"] == "[REDACTED]"
        assert "super-secret-token" not in sanitized.values()


# -----------------------------------------------------------------------------
# 9. Redirect Containment & Scheme Downgrade
# -----------------------------------------------------------------------------

class TestRedirectValidation:
    """Verify redirect scope containment and scheme downgrade prevention."""

    def test_relative_redirect_resolution(self):
        policy = StructuredDataSecurityPolicy()
        resolved = policy.validate_redirect(
            original_url="https://api.example.com/v1/items",
            redirect_url="/v2/items",
        )
        assert resolved == "https://api.example.com/v2/items"

    def test_scheme_downgrade_rejected(self):
        policy = StructuredDataSecurityPolicy()
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_redirect(
                original_url="https://api.example.com/secure",
                redirect_url="http://api.example.com/insecure",
            )
        assert "scheme downgrade from HTTPS to HTTP" in str(exc.value)

    def test_cross_domain_redirect_rejected_when_disallowed(self):
        policy = StructuredDataSecurityPolicy(allow_cross_domain_redirects=False)
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_redirect(
                original_url="https://api.example.com/data",
                redirect_url="https://evil.org/phish",
            )
        assert "prohibited by policy" in str(exc.value)

    def test_redirect_to_ssrf_destination_rejected(self):
        policy = StructuredDataSecurityPolicy()
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_redirect(
                original_url="https://api.example.com/redirect",
                redirect_url="http://169.254.169.254/latest/meta-data/",
            )
        assert "metadata" in str(exc.value).lower() or "scheme downgrade" in str(exc.value).lower()


# -----------------------------------------------------------------------------
# 10. Resource Limits & Policy Serialization
# -----------------------------------------------------------------------------

class TestResourceLimitsAndSerialization:
    """Verify timeout, max bytes bounds, and policy serialization."""

    def test_timeout_bounds(self):
        policy = StructuredDataSecurityPolicy(max_timeout_seconds=30.0)
        valid_limits = StructuredDataLimits(timeout_seconds=20.0)
        assert policy.validate_limits(valid_limits).timeout_seconds == 20.0

        excessive_limits = StructuredDataLimits(timeout_seconds=45.0)
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_limits(excessive_limits)
        assert "exceeds maximum policy timeout" in str(exc.value)

    def test_decompressed_bytes_bounds(self):
        policy = StructuredDataSecurityPolicy(max_decompressed_bytes=5_000_000)
        valid = StructuredDataLimits(max_bytes=1_000_000)
        assert policy.validate_limits(valid).max_bytes == 1_000_000

        excessive = StructuredDataLimits(max_bytes=10_000_000)
        with pytest.raises(StructuredDataPolicyViolationError) as exc:
            policy.validate_limits(excessive)
        assert "exceeds maximum decompressed size limit" in str(exc.value)

    def test_policy_to_dict_from_dict_roundtrip(self):
        policy = StructuredDataSecurityPolicy(
            allowed_schemes={"https"},
            allowed_methods={"GET", "POST"},
            allowed_domains={"api.example.com"},
            blocked_domains={"evil.com"},
            allow_localhost=True,
            max_body_bytes=500_000,
            max_timeout_seconds=45.0,
        )
        d = policy.to_dict()
        reconstructed = StructuredDataSecurityPolicy.from_dict(d)

        assert reconstructed.allowed_schemes == {"https"}
        assert reconstructed.allowed_methods == {"GET", "POST"}
        assert reconstructed.allowed_domains == {"api.example.com"}
        assert reconstructed.blocked_domains == {"evil.com"}
        assert reconstructed.allow_localhost is True
        assert reconstructed.max_body_bytes == 500_000
        assert reconstructed.max_timeout_seconds == 45.0

"""
Comprehensive Unit Tests for Structured Data Authentication, Credential Isolation,
and Rate-Limit Handling (Phase 1 / Part 7 / Step 7).

Covers:
- Credential reference resolution (API Key, Bearer, Basic Auth, OAuth, Custom Header)
- Missing and invalid credentials in SecretStore
- Authentication (401) and Authorization (403) failure discrimination
- Rate-limiting (429) and Retry-After header parsing (seconds and HTTP-dates)
- Bounded jittered backoff retries with RateLimitedStructuredDataProvider
- Max retry exhaustion and non-retryable quota exhaustion
- Responsive cancellation during retry sleep
- Complete secret redaction from text, headers, URLs, dictionaries, and exceptions
- Strict isolation ensuring CrawlerTask, CrawlerReport, and provenance contain zero raw secrets
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import time
import pytest

from core.inference.secrets import EnvSecretStore
from core.research.contracts.crawler_report import CrawlerReport
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import EvidenceProvenance
from core.research.errors import (
    StructuredDataAuthenticationError,
    StructuredDataAuthorizationError,
    StructuredDataCancelledError,
    StructuredDataQuotaExceededError,
    StructuredDataRateLimitError,
    StructuredDataValidationError,
)
from core.research.structured.auth import (
    CredentialRedactor,
    CredentialReference,
    CredentialResolver,
    CredentialType,
    ResolvedCredentials,
)
from core.research.structured.builder import StructuredDataRequestBuilder
from core.research.structured.fake_provider import FakeStructuredDataProvider
from core.research.structured.models import (
    HttpMethod,
    SourceLocation,
    StructuredDataRequest,
    StructuredRecord,
)
from core.research.structured.policy import StructuredDataSecurityPolicy
from core.research.structured.rate_limiter import (
    RateLimitConfig,
    RateLimitedStructuredDataProvider,
    parse_retry_after,
)


# -----------------------------------------------------------------------------
# 1. Credential Reference & Resolution Tests
# -----------------------------------------------------------------------------

class TestCredentialReferenceAndResolution:
    def test_credential_reference_creation_and_serialization(self):
        ref = CredentialReference(
            ref_id="github_token_ref",
            credential_type=CredentialType.BEARER_TOKEN,
            secret_key_ref="env:GITHUB_TOKEN",
            header_name="Authorization",
            metadata={"scope": "read:org"},
        )
        data = ref.to_dict()
        assert data["ref_id"] == "github_token_ref"
        assert data["credential_type"] == "bearer_token"
        assert data["secret_key_ref"] == "env:GITHUB_TOKEN"
        assert "secret" not in data  # No raw secret in serialization

        ref2 = CredentialReference.from_dict(data)
        assert ref2 == ref

    def test_credential_reference_validation(self):
        with pytest.raises(StructuredDataValidationError):
            CredentialReference(ref_id="", secret_key_ref="env:KEY")

        with pytest.raises(StructuredDataValidationError):
            CredentialReference(ref_id="valid", secret_key_ref="")

        with pytest.raises(StructuredDataValidationError):
            CredentialReference(ref_id="valid", secret_key_ref="env:KEY", credential_type="unsupported")

    def test_resolve_bearer_token(self):
        store = EnvSecretStore(overrides={"SECRET_BEARER": "super_secret_jwt_token_12345"})
        resolver = CredentialResolver(store)

        ref = CredentialReference(
            ref_id="jwt_auth",
            credential_type=CredentialType.BEARER_TOKEN,
            secret_key_ref="SECRET_BEARER",
        )
        resolved = resolver.resolve(ref)
        assert resolved.headers["Authorization"] == "Bearer super_secret_jwt_token_12345"
        assert resolved.query_params == {}
        # Masked in str/repr
        assert "super_secret" not in str(resolved)
        assert "[PROTECTED]" in str(resolved)

    def test_resolve_basic_auth_encodes_base64(self):
        store = EnvSecretStore(overrides={"BASIC_CREDS": "alice:secret_pass_789"})
        resolver = CredentialResolver(store)

        ref = CredentialReference(
            ref_id="basic_ref",
            credential_type=CredentialType.BASIC_AUTH,
            secret_key_ref="BASIC_CREDS",
        )
        resolved = resolver.resolve(ref)
        expected_b64 = base64.b64encode(b"alice:secret_pass_789").decode("ascii")
        assert resolved.headers["Authorization"] == f"Basic {expected_b64}"

    def test_resolve_api_key_header_and_query_param(self):
        store = EnvSecretStore(overrides={"API_KEY": "ak_live_abcdef123456"})
        resolver = CredentialResolver(store)

        # Header API Key
        ref_hdr = CredentialReference(
            ref_id="hdr_api",
            credential_type=CredentialType.API_KEY,
            secret_key_ref="API_KEY",
            header_name="X-Custom-Key",
        )
        res_hdr = resolver.resolve(ref_hdr)
        assert res_hdr.headers["X-Custom-Key"] == "ak_live_abcdef123456"
        assert res_hdr.query_params == {}

        # Query Parameter API Key
        ref_query = CredentialReference(
            ref_id="qry_api",
            credential_type=CredentialType.API_KEY,
            secret_key_ref="API_KEY",
            query_param_name="api_key",
        )
        res_qry = resolver.resolve(ref_query)
        assert res_qry.headers == {}
        assert res_qry.query_params["api_key"] == "ak_live_abcdef123456"

    def test_missing_secret_raises_authentication_error(self):
        store = EnvSecretStore(overrides={})
        resolver = CredentialResolver(store)

        ref = CredentialReference(
            ref_id="missing_ref",
            credential_type=CredentialType.BEARER_TOKEN,
            secret_key_ref="NON_EXISTENT_ENV_KEY",
        )
        with pytest.raises(StructuredDataAuthenticationError) as exc_info:
            resolver.resolve(ref)
        assert "not found in SecretStore" in str(exc_info.value)
        assert exc_info.value.status_code == 401


# -----------------------------------------------------------------------------
# 2. Secret Redaction & Isolation Tests
# -----------------------------------------------------------------------------

class TestSecretRedactionAndIsolation:
    def test_redact_text_with_known_secrets_and_bearer_patterns(self):
        store = EnvSecretStore(overrides={"MY_KEY": "super_secret_token_value_99"})
        redactor = CredentialRedactor(store)

        raw_log = "Error during request with Authorization: Bearer super_secret_token_value_99 and key super_secret_token_value_99"
        sanitized = redactor.redact_text(raw_log)

        assert "super_secret_token_value_99" not in sanitized
        assert "[REDACTED]" in sanitized

    def test_redact_headers_scrubs_auth_and_custom_keys(self):
        store = EnvSecretStore(overrides={"SECRET_TOKEN": "my_top_secret_val"})
        redactor = CredentialRedactor(store)

        headers = {
            "Authorization": "Bearer secret_xyz_123",
            "X-API-Key": "api_secret_456",
            "User-Agent": "AutonomOS/1.0",
            "Custom-Note": "Using token my_top_secret_val",
        }
        sanitized = redactor.redact_headers(headers)

        assert sanitized["Authorization"] == "[REDACTED]"
        assert sanitized["X-API-Key"] == "[REDACTED]"
        assert sanitized["User-Agent"] == "AutonomOS/1.0"
        assert "my_top_secret_val" not in sanitized["Custom-Note"]

    def test_redact_url_strips_embedded_credentials_and_query_secrets(self):
        redactor = CredentialRedactor()
        url = "https://user:password123@api.example.com/data?api_key=secret_param_value&page=1"
        sanitized = redactor.redact_url(url)

        assert "password123" not in sanitized
        assert "secret_param_value" not in sanitized
        assert "user:[REDACTED]@" in sanitized
        assert "api_key=%5BREDACTED%5D" in sanitized or "api_key=[REDACTED]" in sanitized
        assert "page=1" in sanitized

    def test_redact_dict_recursively_scrubs(self):
        redactor = CredentialRedactor(additional_secrets=["leaked_secret_val"])
        data = {
            "user": "alice",
            "token": "sensitive_token",
            "config": {
                "password": "secret_password",
                "notes": "Found leaked_secret_val in response",
            },
        }
        sanitized = redactor.redact_dict(data)

        assert sanitized["user"] == "alice"
        assert sanitized["token"] == "[REDACTED]"
        assert sanitized["config"]["password"] == "[REDACTED]"
        assert "leaked_secret_val" not in sanitized["config"]["notes"]

    def test_crawler_task_and_report_contain_zero_raw_secrets(self):
        # A CrawlerTask should only store CredentialReference, never raw secrets
        cred_ref = CredentialReference(
            ref_id="api_key_ref",
            credential_type=CredentialType.API_KEY,
            secret_key_ref="env:SAFE_KEY_NAME",
        )
        task = CrawlerTask(
            task_id="task-001",
            request_id="req-001",
            plan_id="plan-001",
            question_id="q-001",
            query_or_target="https://api.example.com/feed",
            parameters={"credential_ref": cred_ref.to_dict()},
        )
        task_dict = task.to_dict()
        assert "SAFE_KEY_NAME" in str(task_dict)
        # Ensure no raw secret injected
        assert "raw_secret" not in task_dict["parameters"]["credential_ref"]

        report = CrawlerReport(
            report_id="rep-001",
            crawler_task_id=task.task_id,
            crawler_id="crawler-1",
            request_id=task.request_id,
            metadata={"credential_ref": cred_ref.to_dict()},
        )
        report_dict = report.to_dict()
        assert "SAFE_KEY_NAME" in str(report_dict)

    def test_provenance_contains_no_raw_secrets(self):
        redactor = CredentialRedactor()
        safe_url = redactor.redact_url("https://key:super_secret@api.example.com/data?token=secret123")

        prov = EvidenceProvenance(
            request_id="req-prov",
            crawler_task_id="task-prov",
            crawler_id="crawler-prov",
            source_ref=safe_url,
        )
        rec = StructuredRecord(
            record_id="rec-1",
            value={"msg": "public data"},
            source_location=SourceLocation.parse("root"),
            provenance=prov,
        )
        rec_dict = rec.to_dict()
        assert "super_secret" not in str(rec_dict)
        assert "secret123" not in str(rec_dict)


# -----------------------------------------------------------------------------
# 3. Authentication & Authorization Failure Discrimination (401 vs 403)
# -----------------------------------------------------------------------------

class TestAuthFailureDiscrimination:
    def test_401_authentication_failure(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        provider.simulate_auth_error = True
        provider.simulated_auth_status = 401
        provider.simulated_auth_message = "Invalid API Key"

        req = StructuredDataRequest(request_id="r1", endpoint_url="https://api.example.com/protected")
        with pytest.raises(StructuredDataAuthenticationError) as exc_info:
            provider.execute_request(req)

        assert exc_info.value.status_code == 401
        assert "Invalid API Key" in str(exc_info.value)
        assert not isinstance(exc_info.value, StructuredDataAuthorizationError)

    def test_403_authorization_failure(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        provider.simulate_auth_error = True
        provider.simulated_auth_status = 403
        provider.simulated_auth_message = "Insufficient Permissions"

        req = StructuredDataRequest(request_id="r1", endpoint_url="https://api.example.com/admin")
        with pytest.raises(StructuredDataAuthorizationError) as exc_info:
            provider.execute_request(req)

        assert exc_info.value.status_code == 403
        assert isinstance(exc_info.value, StructuredDataAuthenticationError)
        assert "Insufficient Permissions" in str(exc_info.value)

    def test_fake_provider_required_header_verification(self):
        provider = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        provider.required_auth_headers = {"Authorization": "Bearer expected_secret_token"}
        provider.add_fixture(endpoint_url="https://api.example.com/secure", payload={"status": "ok"})

        # Request missing header fails 401
        req_missing = StructuredDataRequest(request_id="r_miss", endpoint_url="https://api.example.com/secure")
        with pytest.raises(StructuredDataAuthenticationError) as exc:
            provider.execute_request(req_missing)
        assert "Missing required authorization header" in str(exc.value)

        # Request with wrong header fails 401
        req_wrong = StructuredDataRequest(
            request_id="r_wrong",
            endpoint_url="https://api.example.com/secure",
            headers={"Authorization": "Bearer wrong_token"},
        )
        with pytest.raises(StructuredDataAuthenticationError) as exc:
            provider.execute_request(req_wrong)
        assert "Invalid credential in authorization header" in str(exc.value)

        # Request with correct header succeeds
        req_ok = StructuredDataRequest(
            request_id="r_ok",
            endpoint_url="https://api.example.com/secure",
            headers={"Authorization": "Bearer expected_secret_token"},
        )
        resp = provider.execute_request(req_ok)
        assert resp.status_code == 200
        assert resp.payload == {"status": "ok"}


# -----------------------------------------------------------------------------
# 4. Retry-After Header Parsing Tests
# -----------------------------------------------------------------------------

class TestRetryAfterParsing:
    def test_numeric_seconds(self):
        assert parse_retry_after("30") == 30.0
        assert parse_retry_after("2.5") == 2.5
        assert parse_retry_after("120", max_allowed_seconds=60.0) == 60.0  # Capped

    def test_empty_or_invalid_returns_default(self):
        assert parse_retry_after(None, default_seconds=10.0) == 10.0
        assert parse_retry_after("", default_seconds=10.0) == 10.0
        assert parse_retry_after("not-a-number-or-date", default_seconds=5.0) == 5.0

    def test_http_date_parsing(self):
        future_dt = datetime.now(timezone.utc) + timedelta(seconds=20)
        # Format as RFC 7231 HTTP-date (e.g. "Wed, 21 Oct 2026 07:28:00 GMT")
        http_date_str = future_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
        seconds = parse_retry_after(http_date_str, max_allowed_seconds=60.0)
        assert 15.0 <= seconds <= 25.0

    def test_past_http_date_returns_zero(self):
        past_dt = datetime.now(timezone.utc) - timedelta(seconds=20)
        http_date_str = past_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
        assert parse_retry_after(http_date_str) == 0.0


# -----------------------------------------------------------------------------
# 5. Rate Limiting & Bounded Backoff Retry Middleware Tests
# -----------------------------------------------------------------------------

class TestRateLimitingAndBoundedRetry:
    def test_single_rate_limit_recovers_on_retry(self):
        fake = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        fake.add_fixture(endpoint_url="https://api.example.com/stream", payload={"data": [1, 2, 3]})
        fake.rate_limit_countdown = 1  # Fails once with 429, then succeeds
        fake.simulated_retry_after = 0.05

        config = RateLimitConfig(
            max_retries=3,
            base_backoff_seconds=0.05,
            jitter=False,
            honor_retry_after=True,
        )
        provider = RateLimitedStructuredDataProvider(fake, config=config)

        req = StructuredDataRequest(request_id="r1", endpoint_url="https://api.example.com/stream")
        resp = provider.execute_request(req)

        assert resp.status_code == 200
        assert resp.payload == {"data": [1, 2, 3]}
        assert fake.rate_limit_countdown == 0

    def test_multiple_rate_limits_within_max_retries_recovers(self):
        fake = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        fake.add_fixture(endpoint_url="https://api.example.com/stream", payload={"data": "ok"})
        fake.rate_limit_countdown = 2  # Fails twice, then succeeds
        fake.simulated_retry_after = 0.02

        config = RateLimitConfig(max_retries=3, base_backoff_seconds=0.02, jitter=False)
        provider = RateLimitedStructuredDataProvider(fake, config=config)

        req = StructuredDataRequest(request_id="r2", endpoint_url="https://api.example.com/stream")
        resp = provider.execute_request(req)
        assert resp.status_code == 200
        assert resp.payload == {"data": "ok"}

    def test_max_retries_exceeded_raises_rate_limit_error(self):
        fake = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        fake.simulate_rate_limit = True  # Permanently rate limited
        fake.simulated_retry_after = 0.01

        config = RateLimitConfig(max_retries=2, base_backoff_seconds=0.01, jitter=False)
        provider = RateLimitedStructuredDataProvider(fake, config=config)

        req = StructuredDataRequest(request_id="r3", endpoint_url="https://api.example.com/stream")
        with pytest.raises(StructuredDataRateLimitError) as exc:
            provider.execute_request(req)

        assert "rate limit exceeded" in str(exc.value)

    def test_quota_exhausted_is_non_retryable(self):
        fake = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        fake.simulate_quota_exhausted = True
        fake.simulate_quota_message = "Monthly billing quota exhausted"

        config = RateLimitConfig(max_retries=5)
        provider = RateLimitedStructuredDataProvider(fake, config=config)

        req = StructuredDataRequest(request_id="r_quota", endpoint_url="https://api.example.com/quota")
        with pytest.raises(StructuredDataQuotaExceededError) as exc:
            provider.execute_request(req)

        assert "quota exhausted" in str(exc.value).lower()
        # Immediately halted without looping through retries

    def test_auth_errors_are_non_retryable(self):
        fake = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        fake.simulate_auth_error = True
        fake.simulated_auth_status = 401

        config = RateLimitConfig(max_retries=5)
        provider = RateLimitedStructuredDataProvider(fake, config=config)

        req = StructuredDataRequest(request_id="r_auth", endpoint_url="https://api.example.com/auth")
        with pytest.raises(StructuredDataAuthenticationError):
            provider.execute_request(req)


# -----------------------------------------------------------------------------
# 6. Cancellation Responsiveness During Backoff
# -----------------------------------------------------------------------------

class TestCancellationDuringBackoff:
    def test_cancellation_during_backoff_sleep_aborts_promptly(self):
        fake = FakeStructuredDataProvider(allow_localhost=True, populate_default_fixtures=False)
        fake.simulate_rate_limit = True
        fake.simulated_retry_after = 10.0  # Large backoff

        config = RateLimitConfig(max_retries=3, max_retry_after_seconds=10.0)
        provider = RateLimitedStructuredDataProvider(fake, config=config)

        # Cancel after 50 milliseconds
        cancel_time = time.time() + 0.05

        def check_cancelled():
            return time.time() >= cancel_time

        req = StructuredDataRequest(request_id="r_cancel", endpoint_url="https://api.example.com/sleep")

        start = time.time()
        with pytest.raises(StructuredDataCancelledError):
            provider.execute_request(req, is_cancelled=check_cancelled)
        elapsed = time.time() - start

        # Aborted within ~0.2s, well before the 10.0s backoff!
        assert elapsed < 1.0


# -----------------------------------------------------------------------------
# 7. Builder Integration Tests
# -----------------------------------------------------------------------------

class TestRequestBuilderWithCredentialRef:
    def test_builder_with_credential_ref(self):
        policy = StructuredDataSecurityPolicy(allowed_domains={"api.example.com"})
        cred_ref = CredentialReference(
            ref_id="builder_ref",
            credential_type=CredentialType.BEARER_TOKEN,
            secret_key_ref="env:MY_TOKEN",
        )

        builder = (
            StructuredDataRequestBuilder(policy)
            .with_endpoint("https://api.example.com/data")
            .with_credential_ref(cred_ref)
        )
        req = builder.build()

        assert req.credential_ref is not None
        assert req.credential_ref.ref_id == "builder_ref"
        assert req.credential_ref.secret_key_ref == "env:MY_TOKEN"

        # Roundtrip dict
        d = req.to_dict()
        assert d["credential_ref"]["ref_id"] == "builder_ref"
        req2 = StructuredDataRequest.from_dict(d)
        assert req2.credential_ref == cred_ref

    def test_builder_from_task_extracts_credential_ref(self):
        cred_ref = CredentialReference(
            ref_id="task_cred",
            credential_type=CredentialType.API_KEY,
            secret_key_ref="env:API_KEY",
        )
        task = CrawlerTask(
            task_id="t-1",
            request_id="r-1",
            plan_id="p-1",
            question_id="q-1",
            query_or_target="https://api.example.com/feed",
            parameters={"credential_ref": cred_ref.to_dict()},
        )
        builder = StructuredDataRequestBuilder.from_task(task)
        req = builder.build()

        assert req.credential_ref is not None
        assert req.credential_ref.ref_id == "task_cred"

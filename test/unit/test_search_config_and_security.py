"""
Unit tests for Search Provider Configuration and Network Security (Phase 1 / Part 2 / Step 3).

Verifies:
1. Missing configuration default values and env variable loading
2. Malformed configuration detection (invalid timeout, limits, malformed URLs)
3. Secret redaction and masking in configuration representations
4. Header and token sanitization (Authorization, x-api-key, Bearer)
5. Credential-bearing URL sanitization and rejection
6. Localhost and loopback address rejection (SSRF protection)
7. Private network (RFC 1918) IP rejection
8. Cloud metadata endpoint rejection (169.254.169.254 / instance-data)
9. Unsafe/disallowed URL scheme rejection (file://, ftp://, etc.)
10. Error message secret sanitization
11. Timeout hierarchy calculation (CrawlerTask timeout -> deadline -> network timeout)
"""
from __future__ import annotations

import unittest

from core.research.errors import (
    SearchConfigurationError,
    SearchSecurityError,
)
from core.research.search.config import SearchConfig
from core.research.search.security import (
    compute_effective_timeout,
    is_safe_search_url,
    sanitize_error,
    sanitize_headers,
    sanitize_secret,
    sanitize_url,
    validate_network_target,
)


class TestSearchConfigAndSecurity(unittest.TestCase):

    # 1. Missing / default configuration
    def test_01_default_configuration_and_env_loading(self):
        cfg = SearchConfig()
        self.assertEqual(cfg.provider_type, "mock")
        self.assertIsNone(cfg.base_url)
        self.assertIsNone(cfg.api_key)
        self.assertEqual(cfg.timeout_seconds, 15.0)
        self.assertEqual(cfg.max_retries, 2)
        self.assertEqual(cfg.default_limit, 5)
        self.assertTrue(cfg.safe_search)
        self.assertFalse(cfg.allow_localhost)

        # Environment loading
        mock_env = {
            "SEARCH_PROVIDER": "tavily",
            "SEARCH_BASE_URL": "https://api.tavily.com",
            "SEARCH_API_KEY": "tvly-secret-key-12345",
            "SEARCH_TIMEOUT_SECONDS": "20.5",
            "SEARCH_MAX_RETRIES": "3",
            "SEARCH_DEFAULT_LIMIT": "10",
            "SEARCH_SAFE_SEARCH": "false",
        }
        loaded = SearchConfig.from_env(env=mock_env)
        self.assertEqual(loaded.provider_type, "tavily")
        self.assertEqual(loaded.base_url, "https://api.tavily.com")
        self.assertEqual(loaded.api_key, "tvly-secret-key-12345")
        self.assertEqual(loaded.timeout_seconds, 20.5)
        self.assertEqual(loaded.max_retries, 3)
        self.assertEqual(loaded.default_limit, 10)
        self.assertFalse(loaded.safe_search)

    # 2. Malformed configuration detection
    def test_02_malformed_configuration_validation(self):
        # Invalid timeout
        with self.assertRaises(SearchConfigurationError):
            SearchConfig(timeout_seconds=0.0)

        with self.assertRaises(SearchConfigurationError):
            SearchConfig(timeout_seconds=-10.0)

        # Invalid retries
        with self.assertRaises(SearchConfigurationError):
            SearchConfig(max_retries=-1)
        with self.assertRaises(SearchConfigurationError):
            SearchConfig(max_retries=100)

        # Invalid limit
        with self.assertRaises(SearchConfigurationError):
            SearchConfig(default_limit=0)
        with self.assertRaises(SearchConfigurationError):
            SearchConfig(default_limit=150)

        # Invalid base_url scheme
        with self.assertRaises(SearchConfigurationError):
            SearchConfig(base_url="ftp://search.example.com")

        # Invalid empty provider type
        with self.assertRaises(SearchConfigurationError):
            SearchConfig(provider_type="")

    # 3. Secret redaction in configuration representations
    def test_03_secret_redaction_in_config(self):
        cfg = SearchConfig(
            provider_type="google",
            base_url="https://customsearch.googleapis.com",
            api_key="AIzaSySecretApiKey1234567890",
            custom_headers={"Authorization": "Bearer token123", "X-Custom": "public-val"},
        )

        safe_dict = cfg.to_safe_dict()
        self.assertNotIn("AIzaSySecretApiKey1234567890", str(safe_dict))
        self.assertEqual(safe_dict["api_key"], "AIz...890")
        self.assertEqual(safe_dict["custom_headers"]["Authorization"], "[REDACTED]")
        self.assertEqual(safe_dict["custom_headers"]["X-Custom"], "public-val")

        repr_str = repr(cfg)
        self.assertNotIn("AIzaSySecretApiKey1234567890", repr_str)
        self.assertNotIn("token123", repr_str)

    # 4. Header and token sanitization
    def test_04_header_and_token_sanitization(self):
        headers = {
            "Authorization": "Bearer secret_jwt_token_here",
            "X-Api-Key": "my-top-secret-api-key",
            "api-key": "another-key",
            "Content-Type": "application/json",
            "User-Agent": "AutonomOS/1.0",
        }
        sanitized = sanitize_headers(headers)
        self.assertEqual(sanitized["Authorization"], "[REDACTED]")
        self.assertEqual(sanitized["X-Api-Key"], "[REDACTED]")
        self.assertEqual(sanitized["api-key"], "[REDACTED]")
        self.assertEqual(sanitized["Content-Type"], "application/json")
        self.assertEqual(sanitized["User-Agent"], "AutonomOS/1.0")

    # 5. Credential-bearing URL sanitization
    def test_05_url_credential_sanitization(self):
        url_with_auth = "https://user:my_secret_pass@api.searxng.org/search?q=test"
        sanitized = sanitize_url(url_with_auth)
        self.assertEqual(sanitized, "https://user:[REDACTED]@api.searxng.org/search?q=test")
        self.assertNotIn("my_secret_pass", sanitized)

        # Network validator must explicitly reject credential-bearing URLs
        with self.assertRaises(SearchSecurityError) as ctx:
            validate_network_target("https://admin:pass123@api.search.com")
        self.assertIn("embedded credentials", str(ctx.exception))

    # 6. Localhost and loopback address rejection (SSRF)
    def test_06_localhost_and_loopback_rejection(self):
        localhost_targets = [
            "http://localhost:8080/search",
            "https://localhost/api",
            "http://127.0.0.1:5000",
            "http://127.0.0.2/query",
            "http://[::1]/search",
        ]
        for target in localhost_targets:
            self.assertFalse(is_safe_search_url(target, allow_localhost=False))
            with self.assertRaises(SearchSecurityError):
                validate_network_target(target, allow_localhost=False)

        # When explicitly allowed for local dev
        self.assertTrue(is_safe_search_url("http://localhost:8080/search", allow_localhost=True))
        self.assertTrue(is_safe_search_url("http://127.0.0.1:8080/search", allow_localhost=True))

    # 7. Private network (RFC 1918) IP rejection
    def test_07_private_network_rejection(self):
        private_ips = [
            "http://10.0.0.1/search",
            "http://10.254.1.5:8080",
            "https://172.16.0.1/api",
            "https://172.31.255.255/query",
            "http://192.168.1.1/search",
            "http://192.168.0.100:9200",
        ]
        for target in private_ips:
            self.assertFalse(is_safe_search_url(target))
            with self.assertRaises(SearchSecurityError):
                validate_network_target(target)

    # 8. Cloud metadata endpoint rejection
    def test_08_cloud_metadata_endpoint_rejection(self):
        metadata_targets = [
            "http://169.254.169.254/latest/meta-data/",
            "http://169.254.169.254/computeMetadata/v1/",
            "http://instance-data/latest/meta-data/",
            "http://metadata.google.internal/computeMetadata/v1/",
        ]
        for target in metadata_targets:
            # Metadata must be rejected even if allow_localhost is accidentally True
            self.assertFalse(is_safe_search_url(target, allow_localhost=True))
            with self.assertRaises(SearchSecurityError):
                validate_network_target(target, allow_localhost=True)

    # 9. Unsafe/disallowed URL scheme rejection
    def test_09_unsafe_url_scheme_rejection(self):
        disallowed_schemes = [
            "file:///etc/passwd",
            "ftp://files.example.com/search",
            "gopher://gopher.example.com",
            "javascript:alert(1)",
            "data:text/html,<html></html>",
        ]
        for target in disallowed_schemes:
            self.assertFalse(is_safe_search_url(target))
            with self.assertRaises(SearchSecurityError):
                validate_network_target(target)

    # 10. Error message secret sanitization
    def test_10_error_message_secret_sanitization(self):
        raw_err = "HTTP 401: Unauthorized for key 'sk-proj-super-secret-key-12345678' with Bearer eyJhbGciOiJIUzI1NiJ9.token"
        sanitized = sanitize_error(raw_err, secrets=["sk-proj-super-secret-key-12345678"])
        self.assertNotIn("sk-proj-super-secret-key-12345678", sanitized)
        self.assertIn("[REDACTED]", sanitized)

    # 11. Timeout calculation hierarchy
    def test_11_timeout_hierarchy_calculation(self):
        # 1. Config timeout is smaller than task timeout
        self.assertEqual(
            compute_effective_timeout(task_timeout=60.0, config_timeout=15.0, elapsed_seconds=0.0),
            15.0,
        )

        # 2. Remaining task timeout is smaller than config timeout
        self.assertEqual(
            compute_effective_timeout(task_timeout=10.0, config_timeout=15.0, elapsed_seconds=2.0),
            8.0,
        )

        # 3. Task timeout nearly exhausted (enforces minimum 0.1s floor)
        self.assertEqual(
            compute_effective_timeout(task_timeout=5.0, config_timeout=15.0, elapsed_seconds=5.0),
            0.1,
        )

        # 4. No task timeout specified (defaults to config timeout)
        self.assertEqual(
            compute_effective_timeout(task_timeout=None, config_timeout=15.0),
            15.0,
        )


if __name__ == "__main__":
    unittest.main()

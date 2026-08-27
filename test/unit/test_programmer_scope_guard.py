from __future__ import annotations

import unittest

from workers.programmer.model import ProgrammingScope
from workers.programmer.scope import ScopeGuard


class TestProgrammerScopeGuard(unittest.TestCase):
    """Unit tests verifying filesystem scope enforcement and path validation."""

    def setUp(self):
        self.scope = ProgrammingScope(
            allowed_paths=["src/auth/*", "tests/unit/test_auth.py", "config/auth.json"],
            excluded_paths=[".git", ".autonomos", "src/auth/secrets.py"],
            max_files_modified=3,
        )

    def test_allowed_path_passes(self):
        valid, msg = ScopeGuard.validate_path("src/auth/service.py", self.scope)
        self.assertTrue(valid)

        valid, msg = ScopeGuard.validate_path("tests/unit/test_auth.py", self.scope)
        self.assertTrue(valid)

    def test_out_of_scope_path_rejected(self):
        valid, msg = ScopeGuard.validate_path("src/payments/stripe.py", self.scope)
        self.assertFalse(valid)
        self.assertIn("outside allowed task scope", msg)

    def test_excluded_path_rejected(self):
        valid, msg = ScopeGuard.validate_path("src/auth/secrets.py", self.scope)
        self.assertFalse(valid)
        self.assertIn("matches excluded scope rule", msg)

        valid, msg = ScopeGuard.validate_path(".git/config", self.scope)
        self.assertFalse(valid)

    def test_path_traversal_rejected(self):
        valid, msg = ScopeGuard.validate_path("../../etc/passwd", self.scope)
        self.assertFalse(valid)
        self.assertIn("Path traversal", msg)

        valid, msg = ScopeGuard.validate_path("src/auth/../../outside.py", self.scope)
        self.assertFalse(valid)

    def test_system_path_rejected(self):
        valid, msg = ScopeGuard.validate_path("/etc/hosts", self.scope)
        self.assertFalse(valid)
        self.assertIn("strictly prohibited", msg)

    def test_change_count_limit(self):
        valid, msg = ScopeGuard.validate_change_count(2, self.scope)
        self.assertTrue(valid)

        valid, msg = ScopeGuard.validate_change_count(4, self.scope)
        self.assertFalse(valid)
        self.assertIn("exceeds allowed limit", msg)


if __name__ == "__main__":
    unittest.main()

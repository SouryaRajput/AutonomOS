from __future__ import annotations

import unittest

from core.research.contracts.request import ResearchRequest, ResearchScope
from core.research.planning.normalizer import (
    NormalizedResearchRequest,
    ResearchRequestNormalizer,
)
from core.research.types import ResearchMode, SourceType


class TestResearchRequestNormalizer(unittest.TestCase):
    """Unit tests for Phase 2 Step 2.1.2 Deterministic Request Normalization."""

    def setUp(self):
        self.normalizer = ResearchRequestNormalizer()

    # -------------------------------------------------------------------------
    # 1. Normal Input & Subclass Compatibility
    # -------------------------------------------------------------------------

    def test_normal_input_passthrough(self):
        """Verify normal, clean request is normalized without altering semantic content."""
        req = ResearchRequest(
            request_id="req-clean-01",
            project_id="proj-1",
            task_id="task-1",
            objective="Analyze authentication flow in local project",
            questions=["Where is the auth handler defined?"],
            constraints=["Use only local repository"],
        )
        norm = self.normalizer.normalize(req)

        self.assertIsInstance(norm, NormalizedResearchRequest)
        self.assertIsInstance(norm, ResearchRequest)
        self.assertEqual(norm.request_id, "req-clean-01")
        self.assertEqual(norm.objective, "Analyze authentication flow in local project")
        self.assertEqual(norm.questions, ["Where is the auth handler defined?"])
        self.assertEqual(norm.constraints, ["Use only local repository"])
        self.assertTrue(norm.is_normalized)
        self.assertEqual(norm.raw_request_snapshot["request_id"], "req-clean-01")

    def test_subclass_serialization_compatibility(self):
        """Verify NormalizedResearchRequest to_dict and from_dict preserve provenance."""
        req = ResearchRequest(
            request_id="req-ser-01",
            project_id="proj-ser",
            task_id="task-ser",
            objective="Verify serialization roundtrip",
        )
        norm = self.normalizer.normalize(req)
        data = norm.to_dict()

        self.assertTrue(data["is_normalized"])
        self.assertIn("raw_request_snapshot", data)
        self.assertIn("normalization_actions", data)

        restored = NormalizedResearchRequest.from_dict(data)
        self.assertEqual(restored.request_id, "req-ser-01")
        self.assertEqual(restored.objective, "Verify serialization roundtrip")
        self.assertTrue(restored.is_normalized)
        self.assertEqual(restored.raw_request_snapshot, norm.raw_request_snapshot)

    # -------------------------------------------------------------------------
    # 2. Whitespace & Text Noise Cleanup
    # -------------------------------------------------------------------------

    def test_whitespace_and_noise_cleanup(self):
        """Verify multiline objectives with tabs, newlines, and trailing spaces are collapsed."""
        req = ResearchRequest(
            request_id="  req-ws-01  ",
            project_id="  proj-ws  ",
            task_id="  task-ws  ",
            objective="   Audit   system   \n\t  security \r\n   architecture   ",
            questions=["   What   is \t the token   expiry?   "],
            constraints=["   Enforce \n JWT validation   "],
        )
        norm = self.normalizer.normalize(req)

        self.assertEqual(norm.request_id, "req-ws-01")
        self.assertEqual(norm.project_id, "proj-ws")
        self.assertEqual(norm.task_id, "task-ws")
        self.assertEqual(norm.objective, "Audit system security architecture")
        self.assertEqual(norm.questions, ["What is the token expiry?"])
        self.assertEqual(norm.constraints, ["Enforce JWT validation"])
        self.assertIn("normalized_objective_whitespace", norm.normalization_actions)

    def test_empty_question_and_noise_filtering(self):
        """Verify empty strings and whitespace-only questions are removed."""
        req = ResearchRequest(
            request_id="req-q-01",
            project_id="proj-1",
            task_id="task-1",
            objective="Valid objective",
            questions=["   ", "What is the database schema?", "\t\n", ""],
        )
        norm = self.normalizer.normalize(req)

        self.assertEqual(norm.questions, ["What is the database schema?"])
        self.assertIn("removed_empty_question", norm.normalization_actions)

    # -------------------------------------------------------------------------
    # 3. Safe Duplicate Removal
    # -------------------------------------------------------------------------

    def test_duplicate_question_removal_preserving_order(self):
        """Verify duplicate questions are removed case-insensitively while preserving first occurrence."""
        req = ResearchRequest(
            request_id="req-dup-01",
            project_id="proj-1",
            task_id="task-1",
            objective="Valid objective",
            questions=[
                "How does retry logic work?",
                "What is the timeout?",
                "HOW DOES RETRY LOGIC WORK?",
                "how does retry logic work?   ",
                "What is the maximum concurrency?",
            ],
        )
        norm = self.normalizer.normalize(req)

        self.assertEqual(
            norm.questions,
            [
                "How does retry logic work?",
                "What is the timeout?",
                "What is the maximum concurrency?",
            ],
        )
        self.assertTrue(any("removed_duplicate_question" in a for a in norm.normalization_actions))

    def test_constraint_normalization_and_bullet_stripping(self):
        """Verify bullet markers are stripped and duplicate constraints are removed."""
        req = ResearchRequest(
            request_id="req-c-01",
            project_id="proj-1",
            task_id="task-1",
            objective="Valid objective",
            constraints=[
                "- Python 3.12 compatibility only",
                "* Zero network access during crawl",
                "• No modifications to repository",
                "1. Python 3.12 compatibility only",
                "   zero network access during crawl   ",
                "   ",
            ],
        )
        norm = self.normalizer.normalize(req)

        self.assertEqual(
            norm.constraints,
            [
                "Python 3.12 compatibility only",
                "Zero network access during crawl",
                "No modifications to repository",
            ],
        )
        self.assertIn("removed_empty_constraint", norm.normalization_actions)
        self.assertTrue(any("removed_duplicate_constraint" in a for a in norm.normalization_actions))

    # -------------------------------------------------------------------------
    # 4. Domain & Scope Normalization
    # -------------------------------------------------------------------------

    def test_domain_normalization_and_lexicographical_sorting(self):
        """Verify domains are stripped of protocols, paths, ports, and sorted deterministically."""
        req = ResearchRequest(
            request_id="req-dom-01",
            project_id="proj-1",
            task_id="task-1",
            objective="Valid objective",
            scope=ResearchScope(
                allowed_domains=[
                    "https://docs.python.org/3/",
                    "HTTP://GITHUB.COM:443/repo",
                    "docs.python.org",
                    "api.example.com:80",
                ],
                excluded_domains=[
                    "https://BAD-SITE.ORG/spam",
                    "bad-site.org",
                ],
            ),
        )
        norm = self.normalizer.normalize(req)

        self.assertEqual(norm.scope.allowed_domains, ["api.example.com", "docs.python.org", "github.com"])
        self.assertEqual(norm.scope.excluded_domains, ["bad-site.org"])

    def test_domain_collision_exclusion_priority(self):
        """Verify that when a domain appears in both allowed and excluded lists, exclusion takes precedence."""
        req = ResearchRequest(
            request_id="req-coll-01",
            project_id="proj-1",
            task_id="task-1",
            objective="Valid objective",
            scope=ResearchScope(
                allowed_domains=["github.com", "reddit.com", "python.org"],
                excluded_domains=["reddit.com"],
            ),
        )
        norm = self.normalizer.normalize(req)

        self.assertIn("github.com", norm.scope.allowed_domains)
        self.assertIn("python.org", norm.scope.allowed_domains)
        self.assertNotIn("reddit.com", norm.scope.allowed_domains)
        self.assertIn("reddit.com", norm.scope.excluded_domains)
        self.assertIn("resolved_domain_conflict_in_favor_of_exclusion", norm.normalization_actions)

    def test_preferred_source_types_deduplication(self):
        """Verify preferred_source_types removes duplicates while preserving order."""
        req = ResearchRequest(
            request_id="req-st-01",
            project_id="proj-1",
            task_id="task-1",
            objective="Valid objective",
            scope=ResearchScope(
                preferred_source_types=[
                    SourceType.OFFICIAL_DOCUMENTATION,
                    SourceType.REPOSITORY,
                    SourceType.OFFICIAL_DOCUMENTATION,
                    SourceType.COMMUNITY,
                ]
            ),
        )
        norm = self.normalizer.normalize(req)

        self.assertEqual(
            norm.scope.preferred_source_types,
            [
                SourceType.OFFICIAL_DOCUMENTATION,
                SourceType.REPOSITORY,
                SourceType.COMMUNITY,
            ],
        )

    def test_recency_days_normalization(self):
        """Verify non-positive recency_days is normalized to None."""
        req_neg = ResearchRequest(
            request_id="req-rec-01",
            project_id="p1",
            task_id="t1",
            objective="Obj",
            scope=ResearchScope(recency_days=-5),
        )
        norm_neg = self.normalizer.normalize(req_neg)
        self.assertIsNone(norm_neg.scope.recency_days)
        self.assertIn("normalized_non_positive_recency_days_to_none", norm_neg.normalization_actions)

        req_zero = ResearchRequest(
            request_id="req-rec-02",
            project_id="p1",
            task_id="t1",
            objective="Obj",
            scope=ResearchScope(recency_days=0),
        )
        norm_zero = self.normalizer.normalize(req_zero)
        self.assertIsNone(norm_zero.scope.recency_days)

        req_pos = ResearchRequest(
            request_id="req-rec-03",
            project_id="p1",
            task_id="t1",
            objective="Obj",
            scope=ResearchScope(recency_days=30),
        )
        norm_pos = self.normalizer.normalize(req_pos)
        self.assertEqual(norm_pos.scope.recency_days, 30)

    def test_numeric_bounds_clamping(self):
        """Verify resource bounds in scope are clamped to valid positive integers."""
        req = ResearchRequest(
            request_id="req-bounds-01",
            project_id="p1",
            task_id="t1",
            objective="Obj",
            scope=ResearchScope(
                max_crawlers=-2,
                max_searches=0,
                timeout_seconds=2,  # min is 10s
                cost_limit=-1.0,
            ),
        )
        norm = self.normalizer.normalize(req)

        self.assertEqual(norm.scope.max_crawlers, 1)
        self.assertEqual(norm.scope.max_searches, 1)
        self.assertEqual(norm.scope.timeout_seconds, 10)
        self.assertEqual(norm.scope.cost_limit, 0.0)

    def test_priority_clamping(self):
        """Verify priority is clamped to [1, 100]."""
        req_low = ResearchRequest(request_id="r1", project_id="p1", task_id="t1", objective="Obj", priority=-5)
        norm_low = self.normalizer.normalize(req_low)
        self.assertEqual(norm_low.priority, 1)

        req_high = ResearchRequest(request_id="r2", project_id="p1", task_id="t1", objective="Obj", priority=500)
        norm_high = self.normalizer.normalize(req_high)
        self.assertEqual(norm_high.priority, 100)

    def test_output_format_normalization(self):
        """Verify required_output_format is trimmed and lowercased."""
        req = ResearchRequest(
            request_id="r1",
            project_id="p1",
            task_id="t1",
            objective="Obj",
            required_output_format="  MARKDOWN REPORT  ",
        )
        norm = self.normalizer.normalize(req)
        self.assertEqual(norm.required_output_format, "markdown_report")

    # -------------------------------------------------------------------------
    # 5. Temporal, Version & Geographic Metadata Normalization
    # -------------------------------------------------------------------------

    def test_temporal_normalization_iso_formatting(self):
        """Verify YYYY-MM-DD strings are formatted to standard UTC ISO dates."""
        req = ResearchRequest(
            request_id="r-time-01",
            project_id="p1",
            task_id="t1",
            objective="Obj",
            metadata={"start_date": "2024-01-15", "end_date": "2024-06-30"},
        )
        norm = self.normalizer.normalize(req)
        self.assertEqual(norm.metadata["start_date"], "2024-01-15T00:00:00Z")
        self.assertEqual(norm.metadata["end_date"], "2024-06-30T00:00:00Z")

    def test_temporal_inverted_range_correction(self):
        """Verify inverted start and end dates are swapped deterministically."""
        req = ResearchRequest(
            request_id="r-time-02",
            project_id="p1",
            task_id="t1",
            objective="Obj",
            metadata={"start_date": "2024-12-31", "end_date": "2024-01-01"},
        )
        norm = self.normalizer.normalize(req)
        self.assertEqual(norm.metadata["start_date"], "2024-01-01T00:00:00Z")
        self.assertEqual(norm.metadata["end_date"], "2024-12-31T00:00:00Z")
        self.assertTrue(any("swapped_inverted_temporal_range" in a for a in norm.normalization_actions))

    def test_version_normalization_strips_v_prefix(self):
        """Verify leading v/V prefix before digits is stripped from version metadata."""
        req = ResearchRequest(
            request_id="r-v-01",
            project_id="p1",
            task_id="t1",
            objective="Obj",
            metadata={
                "target_version": "v3.12.2",
                "min_version": "V3.10",
                "version_specifier": ">= 3.10 , < 3.13",
                "ecosystem": "PYTHON",
            },
        )
        norm = self.normalizer.normalize(req)
        self.assertEqual(norm.metadata["target_version"], "3.12.2")
        self.assertEqual(norm.metadata["min_version"], "3.10")
        self.assertEqual(norm.metadata["version_specifier"], ">=3.10,<3.13")
        self.assertEqual(norm.metadata["ecosystem"], "python")

    def test_geographic_scope_uppercased(self):
        """Verify 2-letter country codes in metadata are converted to uppercase."""
        req = ResearchRequest(
            request_id="r-geo-01",
            project_id="p1",
            task_id="t1",
            objective="Obj",
            metadata={"geographic_scope": "us", "region": "eu"},
        )
        norm = self.normalizer.normalize(req)
        self.assertEqual(norm.metadata["geographic_scope"], "US")
        self.assertEqual(norm.metadata["region"], "EU")

    def test_context_references_deduplication(self):
        """Verify duplicate context references are cleaned and deduplicated."""
        req = ResearchRequest(
            request_id="r-ref-01",
            project_id="p1",
            task_id="t1",
            objective="Obj",
            context_references=[
                {"id": "ref-1", "path": "src/models.py", "note": "  User model  "},
                {"id": "ref-2", "path": "src/service.py"},
                {"id": "ref-1", "path": "src/models.py"},  # Duplicate ID
            ],
        )
        norm = self.normalizer.normalize(req)
        self.assertEqual(len(norm.context_references), 2)
        self.assertEqual(norm.context_references[0]["id"], "ref-1")
        self.assertEqual(norm.context_references[0]["note"], "User model")
        self.assertEqual(norm.context_references[1]["id"], "ref-2")

    # -------------------------------------------------------------------------
    # 6. Missing Optional Fields Handling
    # -------------------------------------------------------------------------

    def test_missing_optional_fields_handling(self):
        """Verify missing optional fields are given safe canonical defaults."""
        req = ResearchRequest(
            request_id="r-opt-01",
            project_id="",
            task_id="",
            objective="Test optional fields",
            scope=None,  # Missing scope
            constraints=[],
            metadata={},
        )
        norm = self.normalizer.normalize(req)
        self.assertIsNotNone(norm.scope)
        self.assertEqual(norm.scope.max_crawlers, 5)
        self.assertEqual(norm.constraints, [])
        self.assertTrue(norm.metadata["_normalized"])

    # -------------------------------------------------------------------------
    # 7. Invalid Input Rejection
    # -------------------------------------------------------------------------

    def test_invalid_input_empty_objective_raises(self):
        """Verify empty or whitespace-only objective raises ValueError."""
        with self.assertRaises(ValueError):
            req_empty = ResearchRequest(request_id="r1", project_id="p1", task_id="t1", objective="")
            self.normalizer.normalize(req_empty)

        with self.assertRaises(ValueError):
            req_ws = ResearchRequest(request_id="r2", project_id="p1", task_id="t1", objective="   \t\n  ")
            self.normalizer.normalize(req_ws)

    def test_invalid_input_none_request_raises(self):
        """Verify passing None or non-ResearchRequest raises TypeError."""
        with self.assertRaises(TypeError):
            self.normalizer.normalize(None)

        with self.assertRaises(TypeError):
            self.normalizer.normalize("not a request")

    # -------------------------------------------------------------------------
    # 8. Strict Idempotence: normalize(normalize(x)) == normalize(x)
    # -------------------------------------------------------------------------

    def test_strict_idempotence(self):
        """Verify normalize(normalize(req)) is identical to normalize(req)."""
        req = ResearchRequest(
            request_id="  req-idem-01  ",
            project_id="  proj-idem  ",
            task_id="  task-idem  ",
            objective="   Compare \t ASGI  and   WSGI  frameworks   ",
            questions=[
                "  What is ASGI?  ",
                "what is asgi?",
                "What is WSGI?",
            ],
            scope=ResearchScope(
                allowed_domains=["HTTP://DOCS.PYTHON.ORG:80/lib", "docs.python.org"],
                excluded_domains=["spam.org"],
                recency_days=-1,
                max_crawlers=10,
            ),
            constraints=[
                "- Zero external dependencies",
                "* Standard library only",
                "- zero external dependencies",
            ],
            required_output_format="  MARKDOWN_REPORT  ",
            priority=150,
            metadata={
                "target_version": "v3.12",
                "start_date": "2024-06-01",
                "end_date": "2024-01-01",
                "geographic_scope": "us",
                "ecosystem": "PYTHON",
            },
        )

        norm1 = self.normalizer.normalize(req)
        norm2 = self.normalizer.normalize(norm1)

        # 1. Object equality
        self.assertEqual(norm1.request_id, norm2.request_id)
        self.assertEqual(norm1.project_id, norm2.project_id)
        self.assertEqual(norm1.task_id, norm2.task_id)
        self.assertEqual(norm1.objective, norm2.objective)
        self.assertEqual(norm1.questions, norm2.questions)
        self.assertEqual(norm1.constraints, norm2.constraints)
        self.assertEqual(norm1.scope.to_dict(), norm2.scope.to_dict())
        self.assertEqual(norm1.required_output_format, norm2.required_output_format)
        self.assertEqual(norm1.priority, norm2.priority)
        self.assertEqual(norm1.metadata["target_version"], norm2.metadata["target_version"])
        self.assertEqual(norm1.metadata["start_date"], norm2.metadata["start_date"])
        self.assertEqual(norm1.metadata["end_date"], norm2.metadata["end_date"])
        self.assertEqual(norm1.metadata["geographic_scope"], norm2.metadata["geographic_scope"])
        self.assertEqual(norm1.metadata["ecosystem"], norm2.metadata["ecosystem"])
        self.assertEqual(norm1.normalization_actions, norm2.normalization_actions)
        self.assertEqual(norm1.raw_request_snapshot, norm2.raw_request_snapshot)

        # 2. Dictionary serialization equality
        self.assertEqual(norm1.to_dict(), norm2.to_dict())


if __name__ == "__main__":
    unittest.main()

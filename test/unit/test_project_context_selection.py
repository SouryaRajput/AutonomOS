"""
Unit Test Suite for Deterministic Project Context Selection (Phase 1 / Part 8 / Step 4).

Verifies:
- Relevant file scoring & selection
- Irrelevant file exclusion
- Equal-ranking candidates & deterministic multi-stage tie-breaking
- Repeated-run determinism (identical scores, order, and materials)
- Source, test, doc, config, and manifest selection
- No-result case (empty query or no matches)
- Resource limits (max_files, max_total_bytes, max_file_size, max_directories, max_symbols, max_depth)
- Traversal protection & path security
- Sensitive file shielding (.env, keys)
- CrawlerTask integration & constraints parsing
- Conversion to ProjectContext, RawSourceReference, and EvidenceItem
"""
from __future__ import annotations

import unittest

from core.research.contracts.crawler_task import CrawlerTask
from core.research.errors import (
    ProjectCancelledError,
    ProjectSecurityError,
    ProjectValidationError,
)
from core.research.project import (
    CandidateProjectFile,
    FakeProjectWorkspaceProvider,
    ProjectContextSelector,
    ProjectRelevanceScorer,
    ProjectSelectionOptions,
    ProjectSelectionQuery,
    SelectedProjectContext,
    classify_project_file_category,
)


class TestProjectContextSelection(unittest.TestCase):
    """Test suite for deterministic project context selection."""

    def setUp(self):
        self.provider = FakeProjectWorkspaceProvider(
            root_path="/app/test-repo",
            provider_id="test-proj-001",
        )
        self.scorer = ProjectRelevanceScorer()
        self.selector = ProjectContextSelector(scorer=self.scorer)

    def test_query_creation_and_search_term_extraction(self):
        """Test search term extraction with camelCase, snake_case, and hyphen splitting."""
        q = ProjectSelectionQuery.from_input(
            topic="inspect UserAuthenticationService and jwt_helper",
            keywords=["database-pool", "OAuth2Client"],
            symbol_names=["verify_token"],
        )
        terms = q.extract_search_terms()
        self.assertIn("user", terms)
        self.assertIn("authentication", terms)
        self.assertIn("service", terms)
        self.assertIn("jwt", terms)
        self.assertIn("helper", terms)
        self.assertIn("database", terms)
        self.assertIn("pool", terms)
        self.assertIn("oauth2", terms)
        self.assertIn("client", terms)
        self.assertIn("verify", terms)
        self.assertIn("token", terms)

    def test_options_validation(self):
        """Test validation of selection budget parameters."""
        opts = ProjectSelectionOptions()
        self.assertEqual(opts.max_files, 20)
        self.assertEqual(opts.max_total_bytes, 2_000_000)

        with self.assertRaises(ProjectValidationError):
            ProjectSelectionOptions(max_files=0)

        with self.assertRaises(ProjectValidationError):
            ProjectSelectionOptions(max_total_bytes=-100)

        with self.assertRaises(ProjectValidationError):
            ProjectSelectionOptions(max_file_size=0)

        with self.assertRaises(ProjectValidationError):
            ProjectSelectionOptions(max_directories=0)

        with self.assertRaises(ProjectValidationError):
            ProjectSelectionOptions(max_symbols=-1)

        with self.assertRaises(ProjectValidationError):
            ProjectSelectionOptions(timeout_seconds=0)

    def test_file_category_classification(self):
        """Verify discrete categorization of project files."""
        self.assertEqual(classify_project_file_category("src/main.py"), "source")
        self.assertEqual(classify_project_file_category("tests/test_main.py"), "test")
        self.assertEqual(classify_project_file_category("docs/architecture.md"), "doc")
        self.assertEqual(classify_project_file_category("README.md"), "doc")
        self.assertEqual(classify_project_file_category("config/settings.json"), "config")
        self.assertEqual(classify_project_file_category("pyproject.toml"), "manifest")
        self.assertEqual(classify_project_file_category("package.json"), "manifest")

    def test_relevant_files_scoring_and_selection(self):
        """Verify high-relevance files are scored and selected."""
        query = ProjectSelectionQuery.from_input(
            topic="main entrypoint and helper utilities",
            keywords=["main", "helpers"],
        )
        result = self.selector.select(self.provider, query=query)

        self.assertIsInstance(result, SelectedProjectContext)
        self.assertGreater(result.selected_count, 0)

        selected_paths = result.selected_paths
        # src/main.py and src/utils/helpers.py should be top ranked
        self.assertIn("src/main.py", selected_paths)
        self.assertIn("src/utils/helpers.py", selected_paths)

        # Confirm content was retrieved into source_materials
        mat_paths = [m.file_path for m in result.source_materials]
        self.assertIn("src/main.py", mat_paths)
        self.assertIn("src/utils/helpers.py", mat_paths)

    def test_irrelevant_files_exclusion(self):
        """Verify files not matching query terms receive low scores or are skipped."""
        query = ProjectSelectionQuery.from_input(
            topic="blockchain ledger cryptography zero knowledge",
            min_score=1.0,
        )
        result = self.selector.select(self.provider, query=query)
        self.assertEqual(result.selected_count, 0)
        self.assertEqual(len(result.source_materials), 0)

    def test_equal_ranking_and_deterministic_tie_breaking(self):
        """Verify deterministic tie-breaking for equal-scoring candidates."""
        # Create a custom provider with multiple equal-scoring files
        custom_files = {
            "src/z_equal.py": b"def foo(): pass\n",
            "src/a_equal.py": b"def foo(): pass\n",
            "src/m_equal.py": b"def foo(): pass\n",
        }
        prov = FakeProjectWorkspaceProvider(
            root_path="/app/test-repo",
            custom_files=custom_files,
        )
        query = ProjectSelectionQuery.from_input(
            topic="equal candidates",
            keywords=["equal"],
        )
        result = self.selector.select(prov, query=query)
        selected_paths = [c.file.relative_path for c in result.selected_files if "equal.py" in c.file.relative_path]

        # Lexicographical ordering must break the tie: a_equal.py < m_equal.py < z_equal.py
        expected = ["src/a_equal.py", "src/m_equal.py", "src/z_equal.py"]
        self.assertEqual(selected_paths, expected)

    def test_repeated_run_determinism(self):
        """Verify that multiple consecutive runs yield identical scores and order."""
        query = ProjectSelectionQuery.from_input(
            topic="engine helpers tests settings",
            keywords=["engine", "helpers", "test"],
        )
        result1 = self.selector.select(self.provider, query=query)
        result2 = self.selector.select(self.provider, query=query)

        self.assertEqual(result1.selected_paths, result2.selected_paths)
        self.assertEqual(len(result1.source_materials), len(result2.source_materials))
        for m1, m2 in zip(result1.source_materials, result2.source_materials):
            self.assertEqual(m1.file_path, m2.file_path)
            self.assertEqual(m1.content_hash, m2.content_hash)
            self.assertEqual(m1.material_id, m2.material_id)

    def test_source_test_doc_category_selection(self):
        """Verify category filtering constraint."""
        # Query targeting only documentation
        doc_query = ProjectSelectionQuery.from_input(
            topic="architecture and documentation",
            keywords=["architecture"],
            target_categories=["doc"],
        )
        result = self.selector.select(self.provider, query=doc_query)
        for c in result.selected_files:
            self.assertEqual(c.category, "doc")

        # Query targeting only test files
        test_query = ProjectSelectionQuery.from_input(
            topic="main unit tests",
            keywords=["main"],
            target_categories=["test"],
        )
        result_tests = self.selector.select(self.provider, query=test_query)
        for c in result_tests.selected_files:
            self.assertEqual(c.category, "test")

    def test_configuration_and_manifest_selection(self):
        """Verify configuration and manifest files are scored, selected, and recorded."""
        query = ProjectSelectionQuery.from_input(
            topic="settings and package dependencies",
            keywords=["settings", "package"],
            target_categories=["config", "manifest"],
        )
        result = self.selector.select(self.provider, query=query)
        selected_paths = result.selected_paths
        self.assertTrue(
            any("settings.json" in p or "package.json" in p for p in selected_paths)
        )
        self.assertGreater(len(result.configurations), 0)

    def test_no_result_case(self):
        """Verify clean handling when no files match."""
        query = ProjectSelectionQuery.from_input(topic="nonexistent_xyz_topic_123")
        result = self.selector.select(self.provider, query=query)
        self.assertEqual(result.selected_count, 0)
        self.assertEqual(len(result.source_materials), 0)
        self.assertFalse(result.is_truncated)

    def test_max_files_limit_truncation(self):
        """Verify selection truncates at max_files budget."""
        query = ProjectSelectionQuery.from_input(topic="src test docs config")
        opts = ProjectSelectionOptions(max_files=2)
        result = self.selector.select(self.provider, query=query, options=opts)

        self.assertLessEqual(result.selected_count, 2)
        self.assertTrue(result.is_truncated)
        self.assertTrue(any("max_files" in r for r in result.truncation_reasons))

    def test_max_total_bytes_limit_truncation(self):
        """Verify total byte budget limits retrieved material."""
        query = ProjectSelectionQuery.from_input(topic="src test docs config")
        opts = ProjectSelectionOptions(max_total_bytes=300)  # Very small byte ceiling
        result = self.selector.select(self.provider, query=query, options=opts)

        self.assertLessEqual(result.total_bytes_fetched, 300)
        self.assertTrue(result.is_truncated)
        self.assertTrue(any("max_total_bytes" in r for r in result.truncation_reasons))

    def test_max_file_size_limit_bounding(self):
        """Verify large files exceeding individual limit are bounded."""
        query = ProjectSelectionQuery.from_input(
            topic="dataset",
            keywords=["dataset", "large"],
        )
        opts = ProjectSelectionOptions(max_file_size=2000)
        result = self.selector.select(self.provider, query=query, options=opts)

        for mat in result.source_materials:
            self.assertLessEqual(mat.size_bytes, 2000)

    def test_symbol_extraction_budget(self):
        """Verify symbol extraction parses code symbols up to max_symbols budget."""
        query = ProjectSelectionQuery.from_input(
            topic="helpers and main core engine",
            keywords=["helpers", "engine"],
        )
        opts = ProjectSelectionOptions(max_symbols=5, extract_symbols=True)
        result = self.selector.select(self.provider, query=query, options=opts)

        self.assertLessEqual(len(result.symbols), 5)
        if result.symbols:
            first_sym = result.symbols[0]
            self.assertTrue(first_sym.name)
            self.assertTrue(first_sym.file_path)

    def test_structural_context_preservation(self):
        """Verify structural context preserves ancestor directory hierarchy."""
        query = ProjectSelectionQuery.from_input(
            topic="helpers",
            keywords=["helpers"],
        )
        result = self.selector.select(self.provider, query=query)
        self.assertIsNotNone(result.structural_context)

        # src/utils/helpers.py has ancestors 'src' and 'src/utils'
        dir_paths = [d.path for d in result.structural_context.directories]
        self.assertIn("src", dir_paths)
        self.assertIn("src/utils", dir_paths)

    def test_sensitive_file_shielding(self):
        """Verify that secret-bearing files (.env, keys) are NEVER selected or retrieved."""
        # Query specifically asking for env / secrets
        query = ProjectSelectionQuery.from_input(
            topic="environment variables and credentials secret key",
            keywords=["env", "credentials", "secret"],
        )
        result = self.selector.select(self.provider, query=query)

        for c in result.selected_files:
            self.assertFalse(c.file.relative_path.startswith(".env"))
            self.assertFalse("credentials" in c.file.relative_path.lower())

        for mat in result.source_materials:
            self.assertFalse(mat.file_path.startswith(".env"))

    def test_traversal_protection(self):
        """Verify paths attempting traversal or root escape are rejected."""
        with self.assertRaises(ProjectSecurityError):
            ProjectSelectionQuery.from_input(
                target_paths=["../../etc/passwd"],
            )

        with self.assertRaises(ProjectSecurityError):
            ProjectSelectionQuery.from_input(
                target_paths=["src/..%2f..%2fetc/passwd"],
            )

    def test_from_crawler_task_integration(self):
        """Verify query extraction from a CrawlerTask."""
        task = CrawlerTask(
            task_id="task-001",
            request_id="req-001",
            plan_id="plan-001",
            question_id="q-001",
            query_or_target="authentication token validation",
            objective="Inspect auth token helpers",
            constraints=["path:src/utils", "category:source", "lang:python"],
            parameters={
                "keywords": ["jwt", "token"],
                "min_score": 2.0,
            },
        )
        query = ProjectSelectionQuery.from_crawler_task(task)
        self.assertEqual(query.raw_topic, "Inspect auth token helpers")
        self.assertIn("src/utils", query.target_paths)
        self.assertIn("source", query.target_categories)
        self.assertIn("python", query.target_languages)
        self.assertIn("jwt", query.keywords)
        self.assertEqual(query.min_score, 2.0)

    def test_to_project_context_and_evidence_conversion(self):
        """Verify conversion to ProjectContext, RawSourceReference, and EvidenceItem."""
        query = ProjectSelectionQuery.from_input(
            topic="main entrypoint",
            keywords=["main"],
        )
        result = self.selector.select(self.provider, query=query)
        ctx = result.to_project_context()

        self.assertEqual(ctx.identity.project_root, "/app/test-repo")
        self.assertTrue(ctx.identity.project_id.startswith("proj-"))
        self.assertGreater(len(ctx.source_materials), 0)

        # Check RawSourceReference conversion
        raw_refs = result.to_raw_source_references()
        self.assertGreater(len(raw_refs), 0)
        self.assertTrue(raw_refs[0].url_or_ref.startswith("project://"))

        # Check EvidenceItem conversion
        evidence = result.to_evidence_items(
            request_id="req-123",
            crawler_task_id="ctask-456",
            crawler_id="crawler-proj-01",
        )
        self.assertGreater(len(evidence), 0)
        self.assertTrue(evidence[0].evidence_id.startswith("ev-proj-"))
        self.assertEqual(evidence[0].provenance.crawler_id, "crawler-proj-01")

    def test_cancellation_responsiveness(self):
        """Verify selection respects cancellation signal."""
        query = ProjectSelectionQuery.from_input(topic="src main helpers")
        is_cancelled = lambda: True

        with self.assertRaises(ProjectCancelledError):
            self.selector.select(self.provider, query=query, is_cancelled=is_cancelled)


if __name__ == "__main__":
    unittest.main()

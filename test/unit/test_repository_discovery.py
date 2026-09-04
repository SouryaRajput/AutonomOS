"""
Unit Tests for Repository Discovery Engine (Phase 1 / Part 5 / Step 3).

Tests:
1. RepositoryDiscoveryOptions and helper categorizers
2. Small repository discovery and inventory
3. README ranking and root preference
4. Manifest identification across multiple language ecosystems
5. Mixed language detection and primary language inference
6. Duplicate path elimination and normalization
7. Unusual filenames, spaces, and Unicode handling
8. Binary vs text classification
9. Deep tree discovery and max depth bounds
10. Oversized tree bounds and truncation flags
11. Missing repository error handling (RepositoryNotFoundError)
12. Provider failure and timeout propagation
13. Cancellation predicate handling (RepositoryCancelledError)
14. Local filesystem fixture discovery and serialization roundtrip
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from core.research.errors import (
    RepositoryCancelledError,
    RepositoryFileNotFoundError,
    RepositoryNotFoundError,
    RepositoryProviderError,
    RepositoryResourceLimitError,
    RepositoryRevisionNotFoundError,
    RepositorySecurityError,
    RepositoryTimeoutError,
    RepositoryValidationError,
)
from core.research.repo.discovery import (
    DiscoveredRepository,
    RepositoryDiscoveryEngine,
    RepositoryDiscoveryOptions,
    classify_manifest_ecosystem,
    is_documentation_file,
    is_manifest_file,
    is_readme_file,
)
from core.research.repo.local_provider import LocalRepositoryProvider
from core.research.repo.mock_provider import MockRepositoryProvider
from core.research.repo.models import (
    RepoVersionCategory,
    RepoVersionContext,
    RepositoryFile,
    RepositoryIdentity,
    RepositoryProviderType,
    RepositoryRevision,
    RepositorySource,
    RepositoryTree,
)


class TestRepositoryDiscovery(unittest.TestCase):
    """
    Comprehensive verification of the RepositoryDiscoveryEngine and structural cataloging.
    """

    def setUp(self):
        self.mock_provider = MockRepositoryProvider(provider_id="mock-discovery-provider")
        self.engine = RepositoryDiscoveryEngine(provider=self.mock_provider)

        # Standard sample repository
        self.sample_ident = RepositoryIdentity(
            repo_id="repo-autonomos-core",
            url="https://github.com/autonomos-org/core.git",
            provider_type=RepositoryProviderType.GITHUB,
            owner="autonomos-org",
            name="core",
            full_name="autonomos-org/core",
            default_branch="main",
            description="AutonomOS Core Platform",
            primary_language="python",
        )
        self.mock_provider.register_repository(self.sample_ident)

        self.sample_rev = RepositoryRevision(
            commit_sha="c0ffee0102030405060708090a0b0c0d0e0f1011",
            branch="main",
            version_context=RepoVersionContext.from_branch("main"),
        )
        self.mock_provider.register_revision("repo-autonomos-core", self.sample_rev, branch="main")

    # -------------------------------------------------------------------------
    # 1. Options & Manifest Helpers
    # -------------------------------------------------------------------------

    def test_01_discovery_options_and_manifest_helpers(self):
        """Verify RepositoryDiscoveryOptions and helper categorizers."""
        opts = RepositoryDiscoveryOptions(
            max_files=500,
            max_directories=50,
            max_depth=6,
            timeout_seconds=20.0,
        )
        self.assertEqual(opts.max_files, 500)
        self.assertEqual(opts.max_directories, 50)
        self.assertEqual(opts.max_depth, 6)
        self.assertEqual(opts.timeout_seconds, 20.0)

        # Validation checks
        with self.assertRaises(RepositoryValidationError):
            RepositoryDiscoveryOptions(max_files=0)
        with self.assertRaises(RepositoryValidationError):
            RepositoryDiscoveryOptions(max_depth=-1)

        # Helper tests
        self.assertTrue(is_readme_file("README.md"))
        self.assertTrue(is_readme_file("docs/readme.rst"))
        self.assertFalse(is_readme_file("src/app.py"))

        self.assertTrue(is_manifest_file("package.json"))
        self.assertTrue(is_manifest_file("pyproject.toml"))
        self.assertTrue(is_manifest_file("Cargo.toml"))
        self.assertTrue(is_manifest_file("go.mod"))
        self.assertTrue(is_manifest_file("pom.xml"))
        self.assertTrue(is_manifest_file("Dockerfile"))
        self.assertFalse(is_manifest_file("main.py"))

        self.assertEqual(classify_manifest_ecosystem("package.json"), "javascript")
        self.assertEqual(classify_manifest_ecosystem("pyproject.toml"), "python")
        self.assertEqual(classify_manifest_ecosystem("Cargo.toml"), "rust")
        self.assertEqual(classify_manifest_ecosystem("go.mod"), "go")
        self.assertEqual(classify_manifest_ecosystem("pom.xml"), "java")
        self.assertEqual(classify_manifest_ecosystem("Dockerfile"), "docker")

        self.assertTrue(is_documentation_file("docs/architecture.md"))
        self.assertTrue(is_documentation_file("guide.rst"))
        self.assertFalse(is_documentation_file("src/core.py"))

    # -------------------------------------------------------------------------
    # 2. Small Repository Discovery
    # -------------------------------------------------------------------------

    def test_02_small_repository_discovery(self):
        """Verify discovery of a small multi-file repository with README and manifest."""
        self.mock_provider.register_file("repo-autonomos-core", "main", "README.md", "# Core Repo\nOverview.")
        self.mock_provider.register_file("repo-autonomos-core", "main", "pyproject.toml", "[tool.poetry]\nname='core'")
        self.mock_provider.register_file("repo-autonomos-core", "main", "src/app.py", "def main(): pass")
        self.mock_provider.register_file("repo-autonomos-core", "main", "src/utils.py", "def helper(): pass")

        result = self.engine.discover("repo-autonomos-core")

        self.assertIsInstance(result, DiscoveredRepository)
        self.assertEqual(result.identity.repo_id, "repo-autonomos-core")
        self.assertEqual(result.revision.commit_sha, self.sample_rev.commit_sha)
        self.assertEqual(result.total_files, 4)
        self.assertFalse(result.is_truncated)

        # Check README and manifest getters
        primary_readme = result.get_primary_readme()
        self.assertIsNotNone(primary_readme)
        self.assertEqual(primary_readme.path, "README.md")

        primary_manifest = result.get_primary_manifest()
        self.assertIsNotNone(primary_manifest)
        self.assertEqual(primary_manifest.path, "pyproject.toml")

        # File lookup
        app_file = result.get_file("src/app.py")
        self.assertIsNotNone(app_file)
        self.assertEqual(app_file.language, "python")

    # -------------------------------------------------------------------------
    # 3. README Ranking
    # -------------------------------------------------------------------------

    def test_03_readme_ranking_and_multiple_readmes(self):
        """Verify that root README takes precedence over nested READMEs."""
        self.mock_provider.register_file("repo-autonomos-core", "main", "docs/README.md", "# Docs README")
        self.mock_provider.register_file("repo-autonomos-core", "main", "README.md", "# Root README")
        self.mock_provider.register_file("repo-autonomos-core", "main", "packages/pkg-a/README.rst", "Subpackage")

        result = self.engine.discover("repo-autonomos-core")

        self.assertEqual(len(result.readme_files), 3)
        # Root README must be first
        self.assertEqual(result.readme_files[0].path, "README.md")
        self.assertEqual(result.get_primary_readme().path, "README.md")

    # -------------------------------------------------------------------------
    # 4. Manifests Across Multiple Ecosystems
    # -------------------------------------------------------------------------

    def test_04_manifest_identification_across_ecosystems(self):
        """Verify identification and ecosystem grouping for polyglot repositories."""
        self.mock_provider.register_file("repo-autonomos-core", "main", "pyproject.toml", "[tool.pytest]")
        self.mock_provider.register_file("repo-autonomos-core", "main", "requirements.txt", "numpy>=1.20")
        self.mock_provider.register_file("repo-autonomos-core", "main", "package.json", '{"name": "frontend"}')
        self.mock_provider.register_file("repo-autonomos-core", "main", "Cargo.toml", '[package]\nname="native"')
        self.mock_provider.register_file("repo-autonomos-core", "main", "go.mod", "module autonomos/agent")
        self.mock_provider.register_file("repo-autonomos-core", "main", "Dockerfile", "FROM python:3.11")

        result = self.engine.discover("repo-autonomos-core")

        self.assertEqual(len(result.manifest_files), 6)

        py_manifests = result.get_manifests_by_ecosystem("python")
        self.assertEqual(len(py_manifests), 2)
        py_paths = [m.path for m in py_manifests]
        self.assertIn("pyproject.toml", py_paths)
        self.assertIn("requirements.txt", py_paths)

        rust_manifests = result.get_manifests_by_ecosystem("rust")
        self.assertEqual(len(rust_manifests), 1)
        self.assertEqual(rust_manifests[0].path, "Cargo.toml")

        js_manifests = result.get_manifests_by_ecosystem("javascript")
        self.assertEqual(len(js_manifests), 1)
        self.assertEqual(js_manifests[0].path, "package.json")

    # -------------------------------------------------------------------------
    # 5. Language Breakdown & Primary Language Inference
    # -------------------------------------------------------------------------

    def test_05_mixed_languages_and_primary_language_inference(self):
        """Verify language distribution calculations and primary language detection."""
        # Register a polyglot repo with no explicit primary language in identity
        poly_ident = RepositoryIdentity(
            repo_id="repo-polyglot",
            url="https://github.com/autonomos-org/poly.git",
            default_branch="main",
        )
        self.mock_provider.register_repository(poly_ident)
        rev = RepositoryRevision(commit_sha="11223344556677889900aabbccddeeff00112233", branch="main")
        self.mock_provider.register_revision("repo-polyglot", rev, branch="main")

        # 3 TypeScript files
        self.mock_provider.register_file("repo-polyglot", "main", "src/ui/app.tsx", "const App = () => null;")
        self.mock_provider.register_file("repo-polyglot", "main", "src/ui/header.tsx", "const H = () => null;")
        self.mock_provider.register_file("repo-polyglot", "main", "src/ui/footer.ts", "export const F = 1;")
        # 1 Python file
        self.mock_provider.register_file("repo-polyglot", "main", "scripts/build.py", "print('build')")

        result = self.engine.discover("repo-polyglot")

        self.assertEqual(result.detected_languages.get("typescript"), 3)
        self.assertEqual(result.detected_languages.get("python"), 1)
        self.assertEqual(result.primary_language, "typescript")

    # -------------------------------------------------------------------------
    # 6. Duplicate Path Elimination & Normalization
    # -------------------------------------------------------------------------

    def test_06_duplicate_paths_and_normalization(self):
        """Verify that unclean or duplicate paths are normalized and deduplicated."""
        self.mock_provider.register_file("repo-autonomos-core", "main", "src/core.py", "x = 1")
        self.mock_provider.register_file("repo-autonomos-core", "main", "./src/core.py", "x = 1")

        result = self.engine.discover("repo-autonomos-core")
        self.assertEqual(result.total_files, 1)
        self.assertEqual(result.tree.files[0].path, "src/core.py")

    # -------------------------------------------------------------------------
    # 7. Unusual Filenames & Unicode
    # -------------------------------------------------------------------------

    def test_07_unusual_filenames_and_special_characters(self):
        """Verify handling of spaces, unicode, multiple dots, and hidden files."""
        self.mock_provider.register_file("repo-autonomos-core", "main", "docs/résumé & notes.md", "Content")
        self.mock_provider.register_file("repo-autonomos-core", "main", "data/archive.tar.gz", b"\x1f\x8b\x08")
        self.mock_provider.register_file("repo-autonomos-core", "main", "config/.env.example", "KEY=VAL")

        result = self.engine.discover("repo-autonomos-core")
        self.assertEqual(result.total_files, 3)

        doc_file = result.get_file("docs/résumé & notes.md")
        self.assertIsNotNone(doc_file)
        self.assertEqual(doc_file.filename, "résumé & notes.md")

    # -------------------------------------------------------------------------
    # 8. Binary vs Text Classification
    # -------------------------------------------------------------------------

    def test_08_binary_and_text_classification(self):
        """Verify is_binary flags in discovered trees."""
        self.mock_provider.register_file("repo-autonomos-core", "main", "assets/logo.png", b"\x89PNG\r\n\x1a\n")
        self.mock_provider.register_file("repo-autonomos-core", "main", "src/main.py", "print('hello')")

        result = self.engine.discover("repo-autonomos-core")

        png_file = result.get_file("assets/logo.png")
        self.assertIsNotNone(png_file)
        self.assertTrue(png_file.is_binary)

        py_file = result.get_file("src/main.py")
        self.assertIsNotNone(py_file)
        self.assertFalse(py_file.is_binary)

    # -------------------------------------------------------------------------
    # 9. Deep Trees and Max Depth Limits
    # -------------------------------------------------------------------------

    def test_09_deep_trees_and_max_depth_bounds(self):
        """Verify truncation when repository tree depth exceeds max_depth."""
        self.mock_provider.register_file("repo-autonomos-core", "main", "a/b/c/d/e/f/g/file.txt", "Deep")

        # Discovery with max_depth=3 (provider get_tree raises or engine marks truncated)
        opts = RepositoryDiscoveryOptions(max_depth=3)
        with self.assertRaises(RepositoryResourceLimitError):
            self.engine.discover("repo-autonomos-core", options=opts)

    # -------------------------------------------------------------------------
    # 10. Oversized Tree Limits
    # -------------------------------------------------------------------------

    def test_10_oversized_trees_and_max_files_bounds(self):
        """Verify resource limits when total files exceeds max_files."""
        for i in range(10):
            self.mock_provider.register_file("repo-autonomos-core", "main", f"file_{i}.txt", f"Content {i}")

        opts = RepositoryDiscoveryOptions(max_files=5)
        with self.assertRaises(RepositoryResourceLimitError):
            self.engine.discover("repo-autonomos-core", options=opts)

    # -------------------------------------------------------------------------
    # 11. Missing Repository Handling
    # -------------------------------------------------------------------------

    def test_11_missing_repository_handling(self):
        """Verify explicit RepositoryNotFoundError on unknown target."""
        with self.assertRaises(RepositoryNotFoundError) as ctx:
            self.engine.discover("non-existent-repository")
        self.assertEqual(ctx.exception.repo_target, "non-existent-repository")

    # -------------------------------------------------------------------------
    # 12. Failure & Timeout Propagation
    # -------------------------------------------------------------------------

    def test_12_provider_failure_and_timeouts(self):
        """Verify timeouts and provider exceptions are propagated."""
        timeout_prov = MockRepositoryProvider(simulate_timeout=True)
        timeout_engine = RepositoryDiscoveryEngine(provider=timeout_prov)

        with self.assertRaises(RepositoryTimeoutError):
            timeout_engine.discover("repo-autonomos-core")

        err_prov = MockRepositoryProvider(simulate_error=ConnectionResetError("Remote reset"))
        err_engine = RepositoryDiscoveryEngine(provider=err_prov)

        with self.assertRaises(ConnectionResetError):
            err_engine.discover("repo-autonomos-core")

    # -------------------------------------------------------------------------
    # 13. Cancellation Handling
    # -------------------------------------------------------------------------

    def test_13_cancellation_handling(self):
        """Verify cancellation predicate halts discovery immediately."""
        is_cancelled = lambda: True
        with self.assertRaises(RepositoryCancelledError):
            self.engine.discover("repo-autonomos-core", is_cancelled=is_cancelled)

    # -------------------------------------------------------------------------
    # 14. Local Filesystem Fixture Discovery & Roundtrip
    # -------------------------------------------------------------------------

    def test_14_local_fixture_directory_discovery(self):
        """Verify RepositoryDiscoveryEngine with LocalRepositoryProvider against real temp files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create files
            with open(os.path.join(temp_dir, "README.md"), "w", encoding="utf-8") as f:
                f.write("# Disk Repo\nDiscovered from filesystem.")
            with open(os.path.join(temp_dir, "package.json"), "w", encoding="utf-8") as f:
                f.write('{"name": "disk-pkg", "version": "1.0.0"}')

            os.makedirs(os.path.join(temp_dir, "src", "components"), exist_ok=True)
            with open(os.path.join(temp_dir, "src", "index.ts"), "w", encoding="utf-8") as f:
                f.write("export const ready = true;\n")
            with open(os.path.join(temp_dir, "src", "components", "button.tsx"), "w", encoding="utf-8") as f:
                f.write("export const Button = () => null;\n")

            local_provider = LocalRepositoryProvider(base_directory=temp_dir)
            disk_engine = RepositoryDiscoveryEngine(provider=local_provider)

            result = disk_engine.discover(temp_dir)

            self.assertEqual(result.identity.provider_type, RepositoryProviderType.LOCAL_GIT)
            self.assertEqual(result.total_files, 4)
            self.assertIsNotNone(result.get_primary_readme())
            self.assertEqual(result.get_primary_readme().filename, "README.md")
            self.assertIsNotNone(result.get_primary_manifest())
            self.assertEqual(result.get_primary_manifest().filename, "package.json")

            # Test to_source_model conversion
            source_model = result.to_source_model()
            self.assertIsInstance(source_model, RepositorySource)
            self.assertEqual(source_model.identity.repo_id, result.identity.repo_id)

            # Test to_dict and from_dict roundtrip
            data_dict = result.to_dict()
            restored = DiscoveredRepository.from_dict(data_dict)
            self.assertEqual(result.identity.repo_id, restored.identity.repo_id)
            self.assertEqual(result.total_files, restored.total_files)
            self.assertEqual(len(result.readme_files), len(restored.readme_files))
            self.assertEqual(len(result.manifest_files), len(restored.manifest_files))


if __name__ == "__main__":
    unittest.main()

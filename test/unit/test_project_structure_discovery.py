"""
Unit Test Suite for Project Structure Discovery (Phase 1 / Part 8 / Step 3).

Tests:
1. Options and bounds validation & serialization roundtrips.
2. Manifest, test, doc, generated, and source root classification heuristics.
3. Normal project structure discovery.
4. Nested directory hierarchy discovery.
5. Mixed language / polyglot discovery and metrics.
6. Generated and ignored file exclusion.
7. Hidden and sensitive file shielding (.env, credentials).
8. Binary file classification without large content reads.
9. Symlink resolution and containment.
10. Deep tree depth limit truncation.
11. Huge tree file and directory count limit truncation.
12. Total byte limit truncation.
13. Security traversal rejection.
14. Provider failure propagation.
15. Timeout propagation.
16. Cancellation predicate responsiveness.
17. Deterministic lexicographical ordering.
18. DiscoveredProjectStructure lossless serialization roundtrip.
19. Real filesystem discovery with LocalProjectWorkspaceProvider.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from core.research.errors import (
    ProjectCancelledError,
    ProjectProviderError,
    ProjectSecurityError,
    ProjectTimeoutError,
    ProjectValidationError,
)
from core.research.project import (
    DiscoveredProjectStructure,
    FakeProjectWorkspaceProvider,
    LocalProjectWorkspaceProvider,
    ProjectDiscoveryOptions,
    ProjectStructureDiscoverer,
    classify_manifest_ecosystem,
    detect_source_roots,
    is_documentation_file_path,
    is_generated_file_path,
    is_manifest_path,
    is_test_file_path,
)


class TestProjectStructureDiscovery(unittest.TestCase):
    """Test suite verifying ProjectStructureDiscoverer engine, classification, limits, and security."""

    def setUp(self):
        self.provider = FakeProjectWorkspaceProvider()
        self.discoverer = ProjectStructureDiscoverer()

    # -------------------------------------------------------------------------
    # 1. Options Validation & Serialization
    # -------------------------------------------------------------------------

    def test_options_defaults_and_validation(self):
        opts = ProjectDiscoveryOptions()
        self.assertEqual(opts.max_depth, 15)
        self.assertEqual(opts.max_files, 2000)
        self.assertEqual(opts.max_directories, 500)
        self.assertEqual(opts.max_total_bytes, 50_000_000)
        self.assertEqual(opts.max_file_size, 5_000_000)
        self.assertEqual(opts.timeout_seconds, 30.0)
        self.assertEqual(opts.concurrency_limit, 1)
        self.assertTrue(opts.read_manifest_metadata)

        d = opts.to_dict()
        restored = ProjectDiscoveryOptions.from_dict(d)
        self.assertEqual(restored.max_files, opts.max_files)
        self.assertEqual(restored.ignored_names, opts.ignored_names)

        with self.assertRaises(ProjectValidationError):
            ProjectDiscoveryOptions(max_depth=0)
        with self.assertRaises(ProjectValidationError):
            ProjectDiscoveryOptions(max_files=-1)
        with self.assertRaises(ProjectValidationError):
            ProjectDiscoveryOptions(max_directories=0)
        with self.assertRaises(ProjectValidationError):
            ProjectDiscoveryOptions(max_total_bytes=0)
        with self.assertRaises(ProjectValidationError):
            ProjectDiscoveryOptions(max_file_size=0)
        with self.assertRaises(ProjectValidationError):
            ProjectDiscoveryOptions(timeout_seconds=0)
        with self.assertRaises(ProjectValidationError):
            ProjectDiscoveryOptions(concurrency_limit=0)

    # -------------------------------------------------------------------------
    # 2. Classification Heuristics
    # -------------------------------------------------------------------------

    def test_manifest_classification(self):
        self.assertTrue(is_manifest_path("pyproject.toml"))
        self.assertTrue(is_manifest_path("package.json"))
        self.assertTrue(is_manifest_path("Cargo.toml"))
        self.assertTrue(is_manifest_path("go.mod"))
        self.assertTrue(is_manifest_path("pom.xml"))
        self.assertTrue(is_manifest_path("src/App.csproj"))
        self.assertTrue(is_manifest_path(".github/workflows/ci.yml"))
        self.assertFalse(is_manifest_path("src/main.py"))

        self.assertEqual(classify_manifest_ecosystem("pyproject.toml"), "python")
        self.assertEqual(classify_manifest_ecosystem("package.json"), "javascript")
        self.assertEqual(classify_manifest_ecosystem("tsconfig.json"), "typescript")
        self.assertEqual(classify_manifest_ecosystem("Cargo.toml"), "rust")
        self.assertEqual(classify_manifest_ecosystem("go.mod"), "go")
        self.assertEqual(classify_manifest_ecosystem("pom.xml"), "java")
        self.assertEqual(classify_manifest_ecosystem("build.gradle.kts"), "kotlin")
        self.assertEqual(classify_manifest_ecosystem(".github/workflows/ci.yml"), "ci")
        self.assertIsNone(classify_manifest_ecosystem("src/main.py"))

    def test_test_file_classification(self):
        self.assertTrue(is_test_file_path("tests/test_main.py"))
        self.assertTrue(is_test_file_path("tests/unit/test_helpers.py"))
        self.assertTrue(is_test_file_path("src/core_test.go"))
        self.assertTrue(is_test_file_path("src/components/App.test.tsx"))
        self.assertTrue(is_test_file_path("spec/models/user_spec.rb"))
        self.assertFalse(is_test_file_path("src/main.py"))
        self.assertFalse(is_test_file_path("README.md"))

    def test_documentation_file_classification(self):
        self.assertTrue(is_documentation_file_path("README.md"))
        self.assertTrue(is_documentation_file_path("CONTRIBUTING.md"))
        self.assertTrue(is_documentation_file_path("LICENSE"))
        self.assertTrue(is_documentation_file_path("docs/architecture.md"))
        self.assertTrue(is_documentation_file_path("documentation/guide.rst"))
        self.assertFalse(is_documentation_file_path("src/main.py"))

    def test_generated_file_classification(self):
        self.assertTrue(is_generated_file_path("static/bundle.min.js"))
        self.assertTrue(is_generated_file_path("styles/app.min.css"))
        self.assertTrue(is_generated_file_path("engine.pyc"))
        self.assertTrue(is_generated_file_path("module.o"))
        self.assertTrue(is_generated_file_path("libtool.so"))
        self.assertFalse(is_generated_file_path("src/main.py"))

    def test_source_root_detection(self):
        # 1. Standard src root
        dirs = ["src", "src/utils", "tests", "docs"]
        files = ["src/main.py", "src/utils/helpers.py", "tests/test_main.py"]
        self.assertEqual(detect_source_roots(dirs, files), ["src"])

        # 2. Standard lib root
        dirs = ["lib", "lib/sub", "tests"]
        files = ["lib/mod.py"]
        self.assertEqual(detect_source_roots(dirs, files), ["lib"])

        # 3. Flat layout (code at project root)
        dirs = ["docs"]
        files = ["main.py", "helpers.py", "README.md"]
        self.assertEqual(detect_source_roots(dirs, files), ["."])

    # -------------------------------------------------------------------------
    # 3. Normal Project Structure Discovery
    # -------------------------------------------------------------------------

    def test_normal_project_discovery(self):
        discovered = self.discoverer.discover(self.provider)

        self.assertFalse(discovered.is_truncated)
        self.assertIsNone(discovered.truncation_reason)
        self.assertGreater(discovered.total_files, 0)
        self.assertGreater(discovered.total_directories, 0)
        self.assertGreater(discovered.total_bytes, 0)
        self.assertGreater(discovered.total_lines, 0)

        # Source roots
        self.assertIn("src", discovered.source_roots)

        # Test directories
        self.assertTrue(any("tests" in d for d in discovered.test_directories))

        # Documentation directories
        self.assertTrue(any("docs" in d for d in discovered.documentation_directories))

        # Configuration directories
        self.assertTrue(any("config" in d for d in discovered.configuration_directories))

        # Manifests
        manifest_paths = [f.relative_path for f in discovered.manifest_files]
        self.assertIn("pyproject.toml", manifest_paths)
        self.assertIn("package.json", manifest_paths)

        # Documentation files
        doc_paths = [f.relative_path for f in discovered.documentation_files]
        self.assertIn("README.md", doc_paths)
        self.assertIn("docs/architecture.md", doc_paths)

        # Test files
        test_paths = [f.relative_path for f in discovered.test_files]
        self.assertIn("tests/test_main.py", test_paths)
        self.assertIn("tests/unit/test_helpers.py", test_paths)

        # Languages
        self.assertEqual(discovered.primary_language, "python")
        self.assertIn("python", discovered.detected_languages)

        # Manifest metadata extracted safely
        manifest_meta = discovered.metadata.get("manifest_metadata", {})
        self.assertIn("pyproject.toml", manifest_meta)
        self.assertEqual(manifest_meta["pyproject.toml"]["ecosystem"], "python")

    # -------------------------------------------------------------------------
    # 4. Nested Directory Hierarchy Discovery
    # -------------------------------------------------------------------------

    def test_nested_structure_discovery(self):
        discovered = self.discoverer.discover(self.provider)

        # Deep subsystem file from default fixtures
        deep_file = discovered.structure.get_file("src/core/subsystem/deep/module.py")
        self.assertIsNotNone(deep_file)
        self.assertEqual(deep_file.filename, "module.py")

        # Nested directory chain exists
        self.assertIsNotNone(discovered.structure.get_directory("src/core/subsystem/deep"))
        self.assertIsNotNone(discovered.structure.get_directory("src/core/subsystem"))
        self.assertIsNotNone(discovered.structure.get_directory("src/core"))

    # -------------------------------------------------------------------------
    # 5. Mixed Languages / Polyglot Discovery
    # -------------------------------------------------------------------------

    def test_mixed_languages_discovery(self):
        custom_files = {
            "src/core/engine.rs": 'fn main() { println!("Rust"); }\n',
            "src/web/client.ts": 'export const client = "TS";\n',
            "src/service/api.go": 'package main\nfunc main() {}\n',
            "Cargo.toml": '[package]\nname = "rust-core"\nversion = "0.1.0"\n',
            "go.mod": "module autonomos.ai/service\n",
        }
        prov = FakeProjectWorkspaceProvider(custom_files=custom_files)
        discovered = self.discoverer.discover(prov)

        self.assertIn("python", discovered.detected_languages)
        self.assertIn("rust", discovered.detected_languages)
        self.assertIn("typescript", discovered.detected_languages)
        self.assertIn("go", discovered.detected_languages)

        # File types
        self.assertIn(".rs", discovered.file_types)
        self.assertIn(".ts", discovered.file_types)
        self.assertIn(".go", discovered.file_types)
        self.assertIn(".py", discovered.file_types)

    # -------------------------------------------------------------------------
    # 6. Generated & Ignored Files Exclusion
    # -------------------------------------------------------------------------

    def test_generated_and_ignored_files_exclusion(self):
        custom_files = {
            "dist/bundle.js": "bundle code\n",
            "static/app.min.js": "minified code\n",
            "src/cache.pyc": b"bytecode",
            ".env": "SECRET=123",
            "node_modules/lib/index.js": "module\n",
        }
        prov = FakeProjectWorkspaceProvider(custom_files=custom_files)
        discovered = self.discoverer.discover(prov)

        all_paths = [f.relative_path for f in discovered.structure.files]
        self.assertNotIn("dist/bundle.js", all_paths)
        self.assertNotIn("static/app.min.js", all_paths)
        self.assertNotIn("src/cache.pyc", all_paths)
        self.assertNotIn(".env", all_paths)
        self.assertNotIn("node_modules/lib/index.js", all_paths)

    # -------------------------------------------------------------------------
    # 7. Binary Files Classification
    # -------------------------------------------------------------------------

    def test_binary_files_classification(self):
        discovered = self.discoverer.discover(self.provider)

        logo_file = discovered.structure.get_file("assets/logo.png")
        self.assertIsNotNone(logo_file)
        self.assertTrue(logo_file.is_binary)
        self.assertEqual(logo_file.line_count, 0)
        self.assertGreater(logo_file.size_bytes, 0)

        wasm_file = discovered.structure.get_file("bin/tool.wasm")
        self.assertIsNotNone(wasm_file)
        self.assertTrue(wasm_file.is_binary)

    # -------------------------------------------------------------------------
    # 8. Symlink Resolution and Containment
    # -------------------------------------------------------------------------

    def test_symlinks_resolution_and_containment(self):
        discovered = self.discoverer.discover(self.provider)

        # Internal valid symlink is discovered
        self.assertIsNotNone(discovered.structure.get_file("docs/readme_link.md"))

        # Escaping symlink is omitted from valid files
        self.assertIsNone(discovered.structure.get_file("escaped_link.txt"))

    # -------------------------------------------------------------------------
    # 9. Bounded Traversal Limits (Depth, Files, Directories, Bytes)
    # -------------------------------------------------------------------------

    def test_deep_tree_depth_limit(self):
        opts = ProjectDiscoveryOptions(max_depth=2)
        discovered = self.discoverer.discover(self.provider, options=opts)

        # File at depth 5 is pruned
        self.assertIsNone(discovered.structure.get_file("src/core/subsystem/deep/module.py"))
        # File at depth 2 remains
        self.assertIsNotNone(discovered.structure.get_file("src/main.py"))

    def test_huge_tree_file_limit_truncation(self):
        opts = ProjectDiscoveryOptions(max_files=4)
        discovered = self.discoverer.discover(self.provider, options=opts)

        self.assertTrue(discovered.is_truncated)
        self.assertIn("File count limit", discovered.truncation_reason or "")
        self.assertLessEqual(discovered.total_files, 4)

    def test_huge_tree_directory_limit_truncation(self):
        opts = ProjectDiscoveryOptions(max_directories=2)
        discovered = self.discoverer.discover(self.provider, options=opts)

        self.assertTrue(discovered.is_truncated)
        self.assertIn("Directory count limit", discovered.truncation_reason or "")
        self.assertLessEqual(discovered.total_directories, 2)

    def test_total_bytes_limit_truncation(self):
        # Set total byte limit below data/large_dataset.csv size
        opts = ProjectDiscoveryOptions(max_total_bytes=5000)
        discovered = self.discoverer.discover(self.provider, options=opts)

        self.assertTrue(discovered.is_truncated)
        self.assertIn("Total byte limit", discovered.truncation_reason or "")
        self.assertLessEqual(discovered.total_bytes, 5000 + 2000)

    # -------------------------------------------------------------------------
    # 10. Security & Traversal Rejection
    # -------------------------------------------------------------------------

    def test_traversal_attempts_security(self):
        with self.assertRaises(ProjectSecurityError):
            self.provider.list_dir(subpath="../outside")
        with self.assertRaises(ProjectSecurityError):
            self.provider.list_dir(subpath="%2e%2e/outside")

    # -------------------------------------------------------------------------
    # 11. Failure, Timeout, Cancellation
    # -------------------------------------------------------------------------

    def test_provider_failure_handling(self):
        prov = FakeProjectWorkspaceProvider(simulate_error=RuntimeError("Storage pool failure"))
        with self.assertRaises(ProjectProviderError):
            self.discoverer.discover(prov)

    def test_timeout_handling(self):
        prov = FakeProjectWorkspaceProvider(simulate_timeout=True)
        with self.assertRaises(ProjectTimeoutError):
            self.discoverer.discover(prov)

    def test_cancellation_propagation(self):
        with self.assertRaises(ProjectCancelledError):
            self.discoverer.discover(self.provider, is_cancelled=lambda: True)

    # -------------------------------------------------------------------------
    # 12. Deterministic Lexicographical Ordering
    # -------------------------------------------------------------------------

    def test_deterministic_ordering(self):
        disc1 = self.discoverer.discover(self.provider)
        disc2 = self.discoverer.discover(self.provider)

        files1 = [f.relative_path for f in disc1.structure.files]
        files2 = [f.relative_path for f in disc2.structure.files]
        self.assertEqual(files1, files2)
        self.assertEqual(files1, sorted(files1))

        dirs1 = [d.path for d in disc1.structure.directories]
        dirs2 = [d.path for d in disc2.structure.directories]
        self.assertEqual(dirs1, dirs2)
        self.assertEqual(dirs1, sorted(dirs1))

        self.assertEqual(list(disc1.detected_languages.keys()), list(disc2.detected_languages.keys()))
        self.assertEqual(list(disc1.file_types.keys()), list(disc2.file_types.keys()))

    # -------------------------------------------------------------------------
    # 13. Lossless Serialization Roundtrip
    # -------------------------------------------------------------------------

    def test_discovered_structure_serialization_roundtrip(self):
        discovered = self.discoverer.discover(self.provider)
        d = discovered.to_dict()
        restored = DiscoveredProjectStructure.from_dict(d)

        self.assertEqual(restored.identity.project_id, discovered.identity.project_id)
        self.assertEqual(restored.total_files, discovered.total_files)
        self.assertEqual(restored.total_directories, discovered.total_directories)
        self.assertEqual(restored.source_roots, discovered.source_roots)
        self.assertEqual(restored.test_directories, discovered.test_directories)
        self.assertEqual(restored.primary_language, discovered.primary_language)
        self.assertEqual(restored.detected_languages, discovered.detected_languages)
        self.assertEqual(restored.file_types, discovered.file_types)
        self.assertEqual(len(restored.manifest_files), len(discovered.manifest_files))

    # -------------------------------------------------------------------------
    # 14. Real Filesystem Discovery (LocalProjectWorkspaceProvider)
    # -------------------------------------------------------------------------

    def test_local_filesystem_discovery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a realistic project layout
            src_dir = os.path.join(tmpdir, "src", "core")
            tests_dir = os.path.join(tmpdir, "tests", "unit")
            docs_dir = os.path.join(tmpdir, "docs")
            os.makedirs(src_dir, exist_ok=True)
            os.makedirs(tests_dir, exist_ok=True)
            os.makedirs(docs_dir, exist_ok=True)

            with open(os.path.join(src_dir, "app.py"), "w", encoding="utf-8") as f:
                f.write('"""Main app."""\n')
            with open(os.path.join(tests_dir, "test_app.py"), "w", encoding="utf-8") as f:
                f.write('"""Test app."""\n')
            with open(os.path.join(docs_dir, "guide.md"), "w", encoding="utf-8") as f:
                f.write("# Guide\n")
            with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as f:
                f.write("# Project README\n")
            with open(os.path.join(tmpdir, "pyproject.toml"), "w", encoding="utf-8") as f:
                f.write('[project]\nname = "local-discovered-project"\nversion = "1.0.0"\n')

            # Sensitive file should be shielded
            with open(os.path.join(tmpdir, ".env"), "w", encoding="utf-8") as f:
                f.write("SECRET_KEY=999\n")

            local_prov = LocalProjectWorkspaceProvider(root_path=tmpdir)
            discovered = self.discoverer.discover(local_prov)

            self.assertFalse(discovered.is_truncated)
            self.assertEqual(discovered.source_roots, ["src"])
            self.assertIn("tests", discovered.test_directories)
            self.assertIn("docs", discovered.documentation_directories)

            manifest_paths = [f.relative_path for f in discovered.manifest_files]
            self.assertIn("pyproject.toml", manifest_paths)

            doc_paths = [f.relative_path for f in discovered.documentation_files]
            self.assertIn("README.md", doc_paths)
            self.assertIn("docs/guide.md", doc_paths)

            test_paths = [f.relative_path for f in discovered.test_files]
            self.assertIn("tests/unit/test_app.py", test_paths)

            # Sensitive file must not be in discovered files
            all_files = [f.relative_path for f in discovered.structure.files]
            self.assertNotIn(".env", all_files)


if __name__ == "__main__":
    unittest.main()

"""
Unit Test Suite for Project Workspace Provider (Phase 1 / Part 8 / Step 2).

Tests:
1. ProjectFetchLimits, ProjectTreeParams, ProjectFileParams validation.
2. Root containment and directory traversal rejection.
3. Symlink target validation and escape rejection.
4. Deterministic fixture verification (sources, tests, docs, manifests, configs, binaries, large files).
5. Missing file handling (ProjectFileNotFoundError).
6. Inaccessible / permissions failures (ProjectAccessError).
7. Malformed file handling (ProjectMalformedFileError).
8. Provider failures (ProjectProviderError).
9. Resource ceilings (max_tree_files, max_tree_depth, max_file_size, max_file_bytes).
10. Timeout propagation (ProjectTimeoutError).
11. Cancellation predicate propagation (ProjectCancelledError).
12. Sensitive file blocking (.env, secrets) and config masking.
13. VCS metadata retrieval gating (allow_vcs=False vs True).
14. LocalProjectWorkspaceProvider real filesystem tests with root containment.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from core.research.errors import (
    ProjectAccessError,
    ProjectCancelledError,
    ProjectFileNotFoundError,
    ProjectMalformedFileError,
    ProjectProviderError,
    ProjectResourceLimitError,
    ProjectSecurityError,
    ProjectTimeoutError,
    ProjectValidationError,
)
from core.research.project import (
    FakeProjectWorkspaceProvider,
    LocalProjectWorkspaceProvider,
    ProjectFetchLimits,
    ProjectFileParams,
    ProjectTreeParams,
    ProjectVCSState,
)
from core.research.repo.models import LineRange


class TestProjectWorkspaceProvider(unittest.TestCase):
    """Test suite covering ProjectWorkspaceProvider contracts, fixtures, security, and limits."""

    def setUp(self):
        self.provider = FakeProjectWorkspaceProvider()

    # -------------------------------------------------------------------------
    # 1. Parameter & Limit Validation
    # -------------------------------------------------------------------------

    def test_fetch_limits_defaults_and_validation(self):
        limits = ProjectFetchLimits()
        self.assertEqual(limits.max_file_bytes, 1_000_000)
        self.assertEqual(limits.max_tree_depth, 15)
        self.assertEqual(limits.max_tree_files, 2000)
        self.assertEqual(limits.max_file_size, 5_000_000)
        self.assertEqual(limits.timeout_seconds, 30.0)

        d = limits.to_dict()
        self.assertIn("max_file_bytes", d)
        restored = ProjectFetchLimits.from_dict(d)
        self.assertEqual(restored.max_file_bytes, limits.max_file_bytes)

        # Invalid bounds
        with self.assertRaises(ProjectValidationError):
            ProjectFetchLimits(max_file_bytes=0)
        with self.assertRaises(ProjectValidationError):
            ProjectFetchLimits(max_tree_depth=-1)
        with self.assertRaises(ProjectValidationError):
            ProjectFetchLimits(max_tree_files=0)
        with self.assertRaises(ProjectValidationError):
            ProjectFetchLimits(max_file_size=-100)
        with self.assertRaises(ProjectValidationError):
            ProjectFetchLimits(timeout_seconds=0)

    def test_tree_params_validation(self):
        params = ProjectTreeParams(subpath="src", max_depth=5, max_files=100, timeout_seconds=10.0)
        self.assertEqual(params.subpath, "src")
        self.assertEqual(params.max_depth, 5)

        with self.assertRaises(ProjectValidationError):
            ProjectTreeParams(max_depth=0)
        with self.assertRaises(ProjectValidationError):
            ProjectTreeParams(max_files=-5)
        with self.assertRaises(ProjectValidationError):
            ProjectTreeParams(timeout_seconds=0)

    def test_file_params_validation(self):
        params = ProjectFileParams(file_path="src/main.py", line_range=LineRange(1, 10), max_bytes=5000)
        self.assertEqual(params.file_path, "src/main.py")

        with self.assertRaises(ProjectValidationError):
            ProjectFileParams(file_path="")
        with self.assertRaises(ProjectValidationError):
            ProjectFileParams(file_path="src/main.py", max_bytes=0)
        with self.assertRaises(ProjectValidationError):
            ProjectFileParams(file_path="src/main.py", timeout_seconds=-1.0)

    # -------------------------------------------------------------------------
    # 2. Identity & Root Resolution
    # -------------------------------------------------------------------------

    def test_fake_provider_identify_root(self):
        identity = self.provider.identify_root()
        self.assertTrue(identity.project_id.startswith("proj-"))
        self.assertEqual(identity.name, "sample-project")
        self.assertEqual(identity.project_type.value, "python")
        self.assertIn("python", identity.languages)
        self.assertEqual(identity.version, "1.2.0")

    # -------------------------------------------------------------------------
    # 3. Deterministic Fixture Structure & list_dir
    # -------------------------------------------------------------------------

    def test_fake_provider_list_dir_structure(self):
        structure = self.provider.list_dir()
        self.assertFalse(structure.truncated)
        self.assertGreater(structure.total_files, 0)
        self.assertGreater(structure.total_directories, 0)

        # Expected source, test, doc, and manifest files
        self.assertIn("src/main.py", structure.file_paths)
        self.assertIn("src/utils/helpers.py", structure.file_paths)
        self.assertIn("src/core/engine.py", structure.file_paths)
        self.assertIn("tests/test_main.py", structure.file_paths)
        self.assertIn("README.md", structure.file_paths)
        self.assertIn("docs/architecture.md", structure.file_paths)
        self.assertIn("pyproject.toml", structure.file_paths)
        self.assertIn("package.json", structure.file_paths)
        self.assertIn("assets/logo.png", structure.file_paths)

        # Expected directories
        self.assertIn("src", structure.directory_paths)
        self.assertIn("src/utils", structure.directory_paths)
        self.assertIn("src/core", structure.directory_paths)
        self.assertIn("tests", structure.directory_paths)
        self.assertIn("docs", structure.directory_paths)

        # Ignored and sensitive files MUST NOT be listed
        self.assertNotIn(".env", structure.file_paths)
        self.assertNotIn("node_modules/mock-lib/index.js", structure.file_paths)
        self.assertNotIn("__pycache__/engine.cpython-312.pyc", structure.file_paths)

    def test_fake_provider_list_dir_subpath(self):
        structure = self.provider.list_dir(subpath="src/utils")
        self.assertIn("src/utils/helpers.py", structure.file_paths)
        self.assertNotIn("src/main.py", structure.file_paths)
        self.assertNotIn("README.md", structure.file_paths)

    # -------------------------------------------------------------------------
    # 4. File Metadata & Content Retrieval
    # -------------------------------------------------------------------------

    def test_fake_provider_file_metadata(self):
        meta = self.provider.get_file_metadata("src/main.py")
        self.assertEqual(meta.path, "src/main.py")
        self.assertEqual(meta.filename, "main.py")
        self.assertEqual(meta.extension, ".py")
        self.assertEqual(meta.language.lower(), "python")
        self.assertFalse(meta.is_binary)
        self.assertGreater(meta.size_bytes, 0)
        self.assertGreater(meta.line_count, 0)
        self.assertEqual(len(meta.content_hash), 64)

        # Binary file metadata
        bin_meta = self.provider.get_file_metadata("assets/logo.png")
        self.assertTrue(bin_meta.is_binary)
        self.assertEqual(bin_meta.extension, ".png")
        self.assertEqual(bin_meta.line_count, 0)

    def test_fake_provider_file_content_and_slicing(self):
        # Full content
        mat = self.provider.get_file_content("src/main.py")
        self.assertEqual(mat.file_path, "src/main.py")
        self.assertIn("class App:", mat.content)
        self.assertFalse(mat.is_binary)
        self.assertIsNone(mat.line_range)

        # Sliced content with LineRange
        sliced = self.provider.get_file_content("src/main.py", line_range=LineRange(1, 3))
        self.assertIsNotNone(sliced.line_range)
        self.assertEqual(sliced.line_range.start_line, 1)
        self.assertEqual(sliced.line_range.end_line, 3)
        self.assertIn("Main application entry point.", sliced.content)
        self.assertNotIn("class App:", sliced.content)

        # Binary content retrieval (empty content string, is_binary=True)
        bin_mat = self.provider.get_file_content("assets/logo.png")
        self.assertTrue(bin_mat.is_binary)
        self.assertEqual(bin_mat.content, "")
        self.assertGreater(bin_mat.size_bytes, 0)

    # -------------------------------------------------------------------------
    # 5. Root Containment & Traversal Security
    # -------------------------------------------------------------------------

    def test_root_containment_relative_traversal_rejection(self):
        traversal_attempts = [
            "../outside.txt",
            "../../etc/passwd",
            "src/../../outside.py",
            "....//....//secret",
        ]
        for attempt in traversal_attempts:
            with self.assertRaises(ProjectSecurityError):
                self.provider.get_file_metadata(attempt)
            with self.assertRaises(ProjectSecurityError):
                self.provider.get_file_content(attempt)
            with self.assertRaises(ProjectSecurityError):
                self.provider.list_dir(subpath=attempt)

    def test_root_containment_encoded_traversal_rejection(self):
        encoded_attempts = [
            "%2e%2e/outside.py",
            "src/%2e%2e/secret",
            "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        ]
        for attempt in encoded_attempts:
            with self.assertRaises(ProjectSecurityError):
                self.provider.get_file_metadata(attempt)

    def test_root_containment_absolute_path_outside_root(self):
        outside_absolute = [
            "/etc/passwd",
            "/var/log/syslog",
            "/tmp/secret.json",
        ]
        for abs_p in outside_absolute:
            with self.assertRaises(ProjectSecurityError):
                self.provider.get_file_metadata(abs_p)

    def test_root_containment_absolute_path_inside_root(self):
        # Absolute path starting within root_path should resolve safely
        inside_abs = "/fake/workspace/sample-project/src/main.py"
        meta = self.provider.get_file_metadata(inside_abs)
        self.assertEqual(meta.path, "src/main.py")

    # -------------------------------------------------------------------------
    # 6. Symlink Security & Resolution
    # -------------------------------------------------------------------------

    def test_escaping_symlink_rejection(self):
        # escaped_link.txt points to ../../etc/passwd
        with self.assertRaises(ProjectSecurityError):
            self.provider.get_file_metadata("escaped_link.txt")
        with self.assertRaises(ProjectSecurityError):
            self.provider.get_file_content("escaped_link.txt")

    def test_valid_internal_symlink_resolution(self):
        # docs/readme_link.md points to README.md
        mat = self.provider.get_file_content("docs/readme_link.md")
        self.assertIn("Sample repository workspace", mat.content)

    def test_broken_symlink_handling(self):
        # docs/broken_link.md points to nonexistent file
        with self.assertRaises(ProjectFileNotFoundError):
            self.provider.get_file_content("docs/broken_link.md")

    # -------------------------------------------------------------------------
    # 7. Sensitive File Blocking & Config Masking
    # -------------------------------------------------------------------------

    def test_sensitive_file_blocking(self):
        sensitive_targets = [
            ".env",
            ".env.local",
            ".env.production",
            "id_rsa",
            "id_ed25519",
            "server.key",
            "cert.pem",
            "credentials.json",
        ]
        for target in sensitive_targets:
            with self.assertRaises(ProjectSecurityError):
                self.provider.get_file_metadata(target)
            with self.assertRaises(ProjectSecurityError):
                self.provider.get_file_content(target)

    def test_project_metadata_masks_secrets(self):
        meta = self.provider.get_project_metadata()
        self.assertIn("settings", meta)
        settings = meta["settings"]
        # Database password and API key in settings.json must be redacted
        self.assertEqual(settings.get("api_key"), "[REDACTED]")
        db_conf = settings.get("database", {})
        self.assertEqual(db_conf.get("password"), "[REDACTED]")
        self.assertEqual(db_conf.get("user"), "app_user")

    # -------------------------------------------------------------------------
    # 8. VCS Metadata Access Control
    # -------------------------------------------------------------------------

    def test_vcs_metadata_gating(self):
        # Without explicit permission (allow_vcs=False), must return None
        self.assertIsNone(self.provider.get_vcs_metadata(allow_vcs=False))

        # With explicit permission (allow_vcs=True), returns sanitized VCS context
        vcs = self.provider.get_vcs_metadata(allow_vcs=True)
        self.assertIsNotNone(vcs)
        self.assertEqual(vcs.branch, "main")
        self.assertEqual(vcs.revision, "a1b2c3d4e5f6789012345678901234567890abcd")
        self.assertIn("github.com/autonomos/sample-project.git", vcs.remote_url)
        self.assertFalse(vcs.is_dirty)

    # -------------------------------------------------------------------------
    # 9. Simulation Hooks & Error Conditions
    # -------------------------------------------------------------------------

    def test_missing_file_error(self):
        with self.assertRaises(ProjectFileNotFoundError):
            self.provider.get_file_metadata("does/not/exist.py")
        with self.assertRaises(ProjectFileNotFoundError):
            self.provider.get_file_content("does/not/exist.py")

    def test_inaccessible_file_simulation(self):
        prov = FakeProjectWorkspaceProvider(inaccessible_paths={"src/main.py", "config"})
        with self.assertRaises(ProjectAccessError):
            prov.get_file_metadata("src/main.py")
        with self.assertRaises(ProjectAccessError):
            prov.get_file_content("src/main.py")
        with self.assertRaises(ProjectAccessError):
            prov.list_dir(subpath="config")

    def test_malformed_file_simulation(self):
        prov = FakeProjectWorkspaceProvider(malformed_paths={"src/core/engine.py"})
        with self.assertRaises(ProjectMalformedFileError):
            prov.get_file_content("src/core/engine.py")

    def test_provider_failure_simulation(self):
        prov = FakeProjectWorkspaceProvider(simulate_error=RuntimeError("Storage pool failure"))
        with self.assertRaises(ProjectProviderError):
            prov.identify_root()
        with self.assertRaises(ProjectProviderError):
            prov.list_dir()

    def test_timeout_simulation(self):
        # Global timeout
        prov = FakeProjectWorkspaceProvider(simulate_timeout=True)
        with self.assertRaises(ProjectTimeoutError):
            prov.identify_root()

        # Per-operation timeout
        prov_ops = FakeProjectWorkspaceProvider(simulate_timeout_ops={"get_file_content"})
        prov_ops.identify_root()  # Should succeed
        with self.assertRaises(ProjectTimeoutError):
            prov_ops.get_file_content("src/main.py")

    def test_cancellation_simulation(self):
        # Via simulate_cancelled flag
        prov = FakeProjectWorkspaceProvider(simulate_cancelled=True)
        with self.assertRaises(ProjectCancelledError):
            prov.list_dir()

        # Via is_cancelled predicate
        with self.assertRaises(ProjectCancelledError):
            self.provider.get_file_content("src/main.py", is_cancelled=lambda: True)

    # -------------------------------------------------------------------------
    # 10. Resource Limits
    # -------------------------------------------------------------------------

    def test_resource_limits_tree_files_exceeded(self):
        limits = ProjectFetchLimits(max_tree_files=3)
        prov = FakeProjectWorkspaceProvider(limits=limits)
        with self.assertRaises(ProjectResourceLimitError):
            prov.list_dir()

    def test_resource_limits_tree_depth_pruning(self):
        limits = ProjectFetchLimits(max_tree_depth=2)
        prov = FakeProjectWorkspaceProvider(limits=limits)
        structure = prov.list_dir()
        # Depth 4 file should be pruned
        self.assertNotIn("src/core/subsystem/deep/module.py", structure.file_paths)
        # Depth 2 file should remain
        self.assertIn("src/main.py", structure.file_paths)

    def test_resource_limits_file_bytes_exceeded(self):
        # Content of large_dataset.csv is > 1 MB, default max_file_bytes is 1 MB
        with self.assertRaises(ProjectResourceLimitError):
            self.provider.get_file_content("data/large_dataset.csv")

    def test_resource_limits_max_file_size_exceeded(self):
        limits = ProjectFetchLimits(max_file_size=500)
        prov = FakeProjectWorkspaceProvider(limits=limits)
        # src/main.py is larger than 500 bytes (or large_dataset.csv)
        with self.assertRaises(ProjectResourceLimitError):
            prov.get_file_content("data/large_dataset.csv")

    # -------------------------------------------------------------------------
    # 11. LocalProjectWorkspaceProvider Real Filesystem Tests
    # -------------------------------------------------------------------------

    def test_local_provider_operations_and_containment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create project structure
            src_dir = os.path.join(tmpdir, "src")
            os.makedirs(src_dir, exist_ok=True)
            main_py = os.path.join(src_dir, "main.py")
            with open(main_py, "w", encoding="utf-8") as f:
                f.write('"""Real main file."""\nprint("Hello World")\n')

            pyproj = os.path.join(tmpdir, "pyproject.toml")
            with open(pyproj, "w", encoding="utf-8") as f:
                f.write('[project]\nname = "temp-project"\nversion = "0.5.0"\n')

            # Create sensitive file
            env_file = os.path.join(tmpdir, ".env")
            with open(env_file, "w", encoding="utf-8") as f:
                f.write("SECRET_KEY=12345\n")

            # Create internal symlink
            link_path = os.path.join(tmpdir, "link_to_main.py")
            os.symlink(main_py, link_path)

            # Create external escaping file & escaping symlink
            with tempfile.TemporaryDirectory() as outside_tmpdir:
                outside_file = os.path.join(outside_tmpdir, "outside.txt")
                with open(outside_file, "w", encoding="utf-8") as f:
                    f.write("secret outside data\n")

                escaping_link = os.path.join(tmpdir, "escaping_link.txt")
                os.symlink(outside_file, escaping_link)

                # Initialize local provider
                local_prov = LocalProjectWorkspaceProvider(root_path=tmpdir)

                # 1. Identify root
                ident = local_prov.identify_root()
                self.assertEqual(ident.name, os.path.basename(tmpdir))
                self.assertEqual(ident.project_type.value, "python")
                self.assertEqual(ident.version, "0.5.0")

                # 2. List directory
                tree = local_prov.list_dir()
                self.assertIn("src/main.py", tree.file_paths)
                self.assertIn("pyproject.toml", tree.file_paths)
                self.assertNotIn(".env", tree.file_paths)
                # Escaping symlink should not be listed as valid file
                self.assertNotIn("escaping_link.txt", tree.file_paths)

                # 3. Get metadata
                meta = local_prov.get_file_metadata("src/main.py")
                self.assertEqual(meta.filename, "main.py")
                self.assertEqual(meta.language.lower(), "python")

                # 4. Get content
                mat = local_prov.get_file_content("src/main.py", line_range=LineRange(1, 1))
                self.assertEqual(mat.content.strip(), '"""Real main file."""')

                # 5. Accessing .env raises ProjectSecurityError
                with self.assertRaises(ProjectSecurityError):
                    local_prov.get_file_metadata(".env")
                with self.assertRaises(ProjectSecurityError):
                    local_prov.get_file_content(".env")

                # 6. Escaping traversal raises ProjectSecurityError
                with self.assertRaises(ProjectSecurityError):
                    local_prov.get_file_metadata("../outside.txt")

                # 7. Escaping symlink access raises ProjectSecurityError
                with self.assertRaises(ProjectSecurityError):
                    local_prov.get_file_content("escaping_link.txt")

                # 8. Missing file raises ProjectFileNotFoundError
                with self.assertRaises(ProjectFileNotFoundError):
                    local_prov.get_file_content("missing.py")

    def test_local_provider_vcs_metadata_safe_inspection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a mock .git directory with HEAD and config (zero git CLI execution)
            git_dir = os.path.join(tmpdir, ".git")
            os.makedirs(os.path.join(git_dir, "refs", "heads"), exist_ok=True)

            with open(os.path.join(git_dir, "HEAD"), "w", encoding="utf-8") as f:
                f.write("ref: refs/heads/feature-workspace\n")

            with open(os.path.join(git_dir, "refs", "heads", "feature-workspace"), "w", encoding="utf-8") as f:
                f.write("deadbeef1234567890abcdef1234567890abcdef\n")

            with open(os.path.join(git_dir, "config"), "w", encoding="utf-8") as f:
                f.write(
                    '[core]\n\trepositoryformatversion = 0\n'
                    '[remote "origin"]\n\turl = https://user:pass@github.com/autonomos/safe-repo.git\n'
                )

            local_prov = LocalProjectWorkspaceProvider(root_path=tmpdir)

            # allow_vcs=False returns None
            self.assertIsNone(local_prov.get_vcs_metadata(allow_vcs=False))

            # allow_vcs=True returns parsed and sanitized VCS metadata
            vcs = local_prov.get_vcs_metadata(allow_vcs=True)
            self.assertIsNotNone(vcs)
            self.assertEqual(vcs.branch, "feature-workspace")
            self.assertEqual(vcs.revision, "deadbeef1234567890abcdef1234567890abcdef")
            # Password in URL must be redacted
            self.assertNotIn("pass", vcs.remote_url)
            self.assertIn("[REDACTED]", vcs.remote_url)


if __name__ == "__main__":
    unittest.main()

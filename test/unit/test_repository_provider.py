"""
Unit Tests for Repository Provider Abstraction and Implementations (Phase 1 / Part 5 / Step 2).

Tests:
1. RepositoryFetchLimits and parameter validation contracts
2. MockRepositoryProvider identity resolution and metadata
3. Revision, branch, and tag resolution
4. Tree retrieval and subpath filtering
5. File content retrieval and line range slicing
6. Explicit missing repository error (RepositoryNotFoundError)
7. Explicit missing revision error (RepositoryRevisionNotFoundError)
8. Explicit missing file error (RepositoryFileNotFoundError)
9. Bounded resource limits enforcement (RepositoryResourceLimitError)
10. Cancellation and timeout simulation (RepositoryCancelledError, RepositoryTimeoutError)
11. Authentication, rate limit, and generic failure simulations
12. LocalRepositoryProvider fixture directory operations
13. Local filesystem traversal guards and security protections
14. Provenance and EvidenceItem contract interoperability
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from core.research.errors import (
    RepositoryAuthenticationError,
    RepositoryCancelledError,
    RepositoryError,
    RepositoryFileNotFoundError,
    RepositoryNotFoundError,
    RepositoryProviderError,
    RepositoryRateLimitError,
    RepositoryResourceLimitError,
    RepositoryRevisionNotFoundError,
    RepositorySecurityError,
    RepositoryTimeoutError,
    RepositoryValidationError,
)
from core.research.repo.local_provider import LocalRepositoryProvider
from core.research.repo.mock_provider import MockRepositoryProvider
from core.research.repo.models import (
    LineRange,
    RepoVersionCategory,
    RepoVersionContext,
    RepositoryDirectory,
    RepositoryFile,
    RepositoryIdentity,
    RepositoryProviderType,
    RepositoryRevision,
    RepositorySourceMaterial,
    RepositoryTree,
    compute_sha256,
)
from core.research.repo.provider import (
    RepositoryFetchLimits,
    RepositoryFileParams,
    RepositoryProvider,
    RepositoryTreeParams,
)
from core.research.types import FactClassification, ResearchConfidence, SourceType


class TestRepositoryProvider(unittest.TestCase):
    """
    Comprehensive verification of repository providers, failure modes, and boundaries.
    """

    def setUp(self):
        self.mock_provider = MockRepositoryProvider(provider_id="test-mock-provider")

        # Setup standard sample repository in mock provider
        self.sample_ident = RepositoryIdentity(
            repo_id="repo-autonomos-engine",
            url="https://github.com/autonomos-org/engine.git",
            provider_type=RepositoryProviderType.GITHUB,
            owner="autonomos-org",
            name="engine",
            full_name="autonomos-org/engine",
            default_branch="main",
            description="Autonomous multi-agent operating engine",
            primary_language="python",
        )
        self.mock_provider.register_repository(self.sample_ident)

        self.rev_main = RepositoryRevision(
            commit_sha="a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
            branch="main",
            version_context=RepoVersionContext.from_branch("main"),
            commit_message="Initial stable release commit",
            author="Architect <lead@autonomos.org>",
        )
        self.mock_provider.register_revision(
            repo_target="repo-autonomos-engine",
            revision=self.rev_main,
            branch="main",
            tag="v1.0.0",
        )

        self.mock_provider.register_file(
            repo_target="repo-autonomos-engine",
            revision_str="main",
            file_path="README.md",
            content="# AutonomOS Engine\nHigh performance agent platform.\n\n## Getting Started\nRun tests.",
        )
        self.mock_provider.register_file(
            repo_target="repo-autonomos-engine",
            revision_str="main",
            file_path="src/engine/core.py",
            content="class Engine:\n    def __init__(self):\n        self.running = True\n\n    def step(self):\n        pass\n",
        )
        self.mock_provider.register_file(
            repo_target="repo-autonomos-engine",
            revision_str="main",
            file_path="src/engine/utils.py",
            content="def compute_hash(data: str) -> str:\n    return 'fake_hash'\n",
        )

    # -------------------------------------------------------------------------
    # 1. Parameter Contracts & Fetch Limits
    # -------------------------------------------------------------------------

    def test_01_provider_interface_and_limits_validation(self):
        """Verify RepositoryFetchLimits and parameter contracts validation."""
        limits = RepositoryFetchLimits(
            max_file_bytes=500_000,
            max_tree_depth=5,
            max_tree_files=200,
            timeout_seconds=15.0,
        )
        self.assertEqual(limits.max_file_bytes, 500_000)
        self.assertEqual(limits.max_tree_depth, 5)
        self.assertEqual(limits.max_tree_files, 200)
        self.assertEqual(limits.timeout_seconds, 15.0)

        # Serialization roundtrip
        ldict = limits.to_dict()
        restored_limits = RepositoryFetchLimits.from_dict(ldict)
        self.assertEqual(limits, restored_limits)

        # Validation errors on invalid limits
        with self.assertRaises(RepositoryValidationError):
            RepositoryFetchLimits(max_file_bytes=0)
        with self.assertRaises(RepositoryValidationError):
            RepositoryFetchLimits(max_tree_depth=-1)
        with self.assertRaises(RepositoryValidationError):
            RepositoryFetchLimits(max_tree_files=0)
        with self.assertRaises(RepositoryValidationError):
            RepositoryFetchLimits(timeout_seconds=-5.0)

        # Parameter objects validation
        tparams = RepositoryTreeParams(repo="my-repo", subpath="src", max_depth=3)
        self.assertEqual(tparams.subpath, "src")
        with self.assertRaises(RepositoryValidationError):
            RepositoryTreeParams(repo="")

        fparams = RepositoryFileParams(repo="my-repo", file_path="main.py", max_bytes=1000)
        self.assertEqual(fparams.file_path, "main.py")
        with self.assertRaises(RepositoryValidationError):
            RepositoryFileParams(repo="my-repo", file_path="")

    # -------------------------------------------------------------------------
    # 2. Mock Provider Identity & Metadata
    # -------------------------------------------------------------------------

    def test_02_mock_provider_registration_and_resolution(self):
        """Verify MockRepositoryProvider identity resolution and metadata fetching."""
        # 1. Resolve by repo_id
        ident1 = self.mock_provider.resolve_identity("repo-autonomos-engine")
        self.assertEqual(ident1.repo_id, "repo-autonomos-engine")
        self.assertEqual(ident1.owner, "autonomos-org")
        self.assertEqual(ident1.name, "engine")

        # 2. Resolve by full URL
        ident2 = self.mock_provider.resolve_identity("https://github.com/autonomos-org/engine.git")
        self.assertEqual(ident2.repo_id, "repo-autonomos-engine")

        # 3. Resolve by full_name
        ident3 = self.mock_provider.resolve_identity("autonomos-org/engine")
        self.assertEqual(ident3.repo_id, "repo-autonomos-engine")

        # 4. Get metadata
        meta = self.mock_provider.get_metadata(self.sample_ident)
        self.assertEqual(meta.description, "Autonomous multi-agent operating engine")
        self.assertEqual(meta.primary_language, "python")

    # -------------------------------------------------------------------------
    # 3. Revisions, Branches, and Tags
    # -------------------------------------------------------------------------

    def test_03_mock_provider_revisions_and_branches(self):
        """Verify revision, branch, and tag resolution against registered data."""
        # 1. Default branch resolution
        rev_def = self.mock_provider.get_revision("repo-autonomos-engine")
        self.assertEqual(rev_def.commit_sha, self.rev_main.commit_sha)
        self.assertEqual(rev_def.branch, "main")

        # 2. Branch name resolution
        rev_branch = self.mock_provider.get_revision("repo-autonomos-engine", revision="main")
        self.assertEqual(rev_branch.commit_sha, self.rev_main.commit_sha)

        # 3. Tag name resolution
        rev_tag = self.mock_provider.get_revision("repo-autonomos-engine", revision="v1.0.0")
        self.assertEqual(rev_tag.commit_sha, self.rev_main.commit_sha)

        # 4. Direct SHA resolution
        rev_sha = self.mock_provider.get_revision(
            "repo-autonomos-engine",
            revision="a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
        )
        self.assertEqual(rev_sha.commit_sha, self.rev_main.commit_sha)

    # -------------------------------------------------------------------------
    # 4. Tree Retrieval & Subpath Queries
    # -------------------------------------------------------------------------

    def test_04_mock_provider_tree_queries_and_subpath(self):
        """Verify bounded tree retrieval and subpath filtering."""
        # 1. Full tree
        full_tree = self.mock_provider.get_tree("repo-autonomos-engine", revision="main")
        self.assertEqual(full_tree.total_files, 3)
        self.assertIsNotNone(full_tree.get_file("README.md"))
        self.assertIsNotNone(full_tree.get_file("src/engine/core.py"))
        self.assertIsNotNone(full_tree.get_file("src/engine/utils.py"))

        # 2. Subpath tree under src/engine
        sub_tree = self.mock_provider.get_tree(
            "repo-autonomos-engine",
            revision="main",
            subpath="src/engine",
        )
        self.assertEqual(sub_tree.total_files, 2)
        self.assertIsNone(sub_tree.get_file("README.md"))
        self.assertIsNotNone(sub_tree.get_file("src/engine/core.py"))
        self.assertIsNotNone(sub_tree.get_file("src/engine/utils.py"))

    # -------------------------------------------------------------------------
    # 5. File Retrieval & Line Slicing
    # -------------------------------------------------------------------------

    def test_05_mock_provider_file_retrieval_and_line_slicing(self):
        """Verify file content retrieval, line range slicing, and checksums."""
        # 1. Full file
        material = self.mock_provider.get_file_content(
            repo="repo-autonomos-engine",
            file_path="src/engine/core.py",
            revision="main",
        )
        self.assertIsInstance(material, RepositorySourceMaterial)
        self.assertEqual(material.file_path, "src/engine/core.py")
        self.assertEqual(material.language, "python")
        self.assertIn("class Engine:", material.content)
        self.assertEqual(material.line_range, None)
        self.assertEqual(material.checksum, compute_sha256(material.raw_bytes))

        # 2. LineRange slice: lines 2 to 4
        range_slice = LineRange(start_line=2, end_line=4)
        sliced_mat = self.mock_provider.get_file_content(
            repo="repo-autonomos-engine",
            file_path="src/engine/core.py",
            revision="main",
            line_range=range_slice,
        )
        self.assertEqual(sliced_mat.line_range, range_slice)
        expected_sliced_text = "    def __init__(self):\n        self.running = True\n\n"
        self.assertEqual(sliced_mat.content, expected_sliced_text)
        self.assertEqual(sliced_mat.checksum, compute_sha256(expected_sliced_text.encode("utf-8")))

    # -------------------------------------------------------------------------
    # 6. Explicit Missing Repository Handling
    # -------------------------------------------------------------------------

    def test_06_mock_provider_missing_repo_error(self):
        """Verify explicit RepositoryNotFoundError when repo does not exist."""
        with self.assertRaises(RepositoryNotFoundError) as ctx:
            self.mock_provider.get_metadata("non-existent-repo-99")
        self.assertEqual(ctx.exception.repo_target, "non-existent-repo-99")

        with self.assertRaises(RepositoryNotFoundError):
            self.mock_provider.get_tree("non-existent-repo-99")

        with self.assertRaises(RepositoryNotFoundError):
            self.mock_provider.get_file_content("non-existent-repo-99", "file.py")

    # -------------------------------------------------------------------------
    # 7. Explicit Missing Revision Handling
    # -------------------------------------------------------------------------

    def test_07_mock_provider_missing_revision_error(self):
        """Verify explicit RepositoryRevisionNotFoundError on non-existent branches or tags."""
        with self.assertRaises(RepositoryRevisionNotFoundError) as ctx:
            self.mock_provider.get_revision("repo-autonomos-engine", revision="feature/non-existent")
        self.assertEqual(ctx.exception.repo_target, "repo-autonomos-engine")
        self.assertEqual(ctx.exception.revision, "feature/non-existent")

        with self.assertRaises(RepositoryRevisionNotFoundError):
            self.mock_provider.get_tree("repo-autonomos-engine", revision="bad-sha-12345")

        with self.assertRaises(RepositoryRevisionNotFoundError):
            self.mock_provider.get_file_content(
                "repo-autonomos-engine",
                file_path="README.md",
                revision="v99.0.0",
            )

    # -------------------------------------------------------------------------
    # 8. Explicit Missing File Handling
    # -------------------------------------------------------------------------

    def test_08_mock_provider_missing_file_error(self):
        """Verify explicit RepositoryFileNotFoundError on missing file path."""
        with self.assertRaises(RepositoryFileNotFoundError) as ctx:
            self.mock_provider.get_file_content(
                repo="repo-autonomos-engine",
                file_path="src/missing_module.py",
                revision="main",
            )
        self.assertEqual(ctx.exception.file_path, "src/missing_module.py")
        self.assertEqual(ctx.exception.repo_target, "repo-autonomos-engine")

    # -------------------------------------------------------------------------
    # 9. Resource Limits Enforcement
    # -------------------------------------------------------------------------

    def test_09_mock_provider_resource_limits(self):
        """Verify resource limits for file bytes and tree depth."""
        # 1. File size limit
        with self.assertRaises(RepositoryResourceLimitError) as ctx:
            self.mock_provider.get_file_content(
                repo="repo-autonomos-engine",
                file_path="README.md",
                revision="main",
                max_bytes=10,  # README.md is ~80 bytes
            )
        self.assertEqual(ctx.exception.resource_type, "file_bytes")

        # 2. Tree file count limit
        with self.assertRaises(RepositoryResourceLimitError) as ctx:
            self.mock_provider.get_tree(
                repo="repo-autonomos-engine",
                revision="main",
                max_files=1,  # Tree has 3 files
            )
        self.assertEqual(ctx.exception.resource_type, "tree_files")

        # 3. Tree depth limit
        with self.assertRaises(RepositoryResourceLimitError) as ctx:
            self.mock_provider.get_tree(
                repo="repo-autonomos-engine",
                revision="main",
                max_depth=1,  # Tree depth is 3 (src/engine/core.py)
            )
        self.assertEqual(ctx.exception.resource_type, "tree_depth")

    # -------------------------------------------------------------------------
    # 10. Cancellation & Timeout Simulation
    # -------------------------------------------------------------------------

    def test_10_mock_provider_cancellation_and_timeouts(self):
        """Verify cancellation predicate and timeout simulation."""
        # 1. Dynamic cancellation callable
        is_cancelled = lambda: True
        with self.assertRaises(RepositoryCancelledError) as ctx:
            self.mock_provider.get_file_content(
                repo="repo-autonomos-engine",
                file_path="README.md",
                revision="main",
                is_cancelled=is_cancelled,
            )
        self.assertEqual(ctx.exception.operation, "get_file_content")

        # 2. Simulated global timeout
        timeout_provider = MockRepositoryProvider(simulate_timeout=True)
        with self.assertRaises(RepositoryTimeoutError) as ctx:
            timeout_provider.resolve_identity("repo-test")
        self.assertEqual(ctx.exception.operation, "resolve_identity")

        # 3. Simulated operation-specific timeout
        op_timeout_prov = MockRepositoryProvider(simulate_timeout_ops={"get_tree"})
        op_timeout_prov.register_repository(self.sample_ident)
        # resolve succeeds
        ident = op_timeout_prov.resolve_identity("repo-autonomos-engine")
        self.assertIsNotNone(ident)
        # get_tree fails with timeout
        with self.assertRaises(RepositoryTimeoutError) as ctx:
            op_timeout_prov.get_tree("repo-autonomos-engine")
        self.assertEqual(ctx.exception.operation, "get_tree")

    # -------------------------------------------------------------------------
    # 11. Rate Limits, Authentication & Custom Error Simulation
    # -------------------------------------------------------------------------

    def test_11_mock_provider_rate_limit_and_auth_errors(self):
        """Verify rate limit, auth errors, and arbitrary exception simulation."""
        # 1. Auth error
        auth_prov = MockRepositoryProvider(simulate_auth_error=True)
        with self.assertRaises(RepositoryAuthenticationError):
            auth_prov.get_metadata("any-repo")

        # 2. Rate limit error
        rate_prov = MockRepositoryProvider(simulate_rate_limit=True)
        with self.assertRaises(RepositoryRateLimitError) as ctx:
            rate_prov.get_metadata("any-repo")
        self.assertEqual(ctx.exception.retry_after_seconds, 30.0)

        # 3. Custom exception simulation
        custom_prov = MockRepositoryProvider(simulate_error=ConnectionResetError("Socket broken"))
        with self.assertRaises(ConnectionResetError):
            custom_prov.get_revision("any-repo")

    # -------------------------------------------------------------------------
    # 12. Local Filesystem Provider Operations
    # -------------------------------------------------------------------------

    def test_12_local_filesystem_provider_basic_operations(self):
        """Verify LocalRepositoryProvider against temporary fixture directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create nested directory tree
            os.makedirs(os.path.join(temp_dir, "pkg", "subpkg"), exist_ok=True)

            with open(os.path.join(temp_dir, "README.md"), "w", encoding="utf-8") as f:
                f.write("# Local Fixture Repo\nTesting local provider.")

            with open(os.path.join(temp_dir, "pkg", "mod.py"), "w", encoding="utf-8") as f:
                f.write("def add(a: int, b: int) -> int:\n    return a + b\n")

            with open(os.path.join(temp_dir, "pkg", "subpkg", "deep.py"), "w", encoding="utf-8") as f:
                f.write("# Deep module\nVAL = 42\n")

            # Provider instantiated
            local_prov = LocalRepositoryProvider(base_directory=temp_dir)

            # 1. Resolve identity
            ident = local_prov.resolve_identity(temp_dir)
            self.assertEqual(ident.provider_type, RepositoryProviderType.LOCAL_GIT)
            self.assertTrue(ident.url.startswith("file://"))

            # 2. Get revision
            rev = local_prov.get_revision(temp_dir)
            self.assertEqual(rev.branch, "main")
            self.assertTrue(len(rev.commit_sha) > 0)

            # 3. Get tree
            tree = local_prov.get_tree(temp_dir)
            self.assertEqual(tree.total_files, 3)
            self.assertIsNotNone(tree.get_file("README.md"))
            self.assertIsNotNone(tree.get_file("pkg/mod.py"))
            self.assertIsNotNone(tree.get_file("pkg/subpkg/deep.py"))

            # 4. Get file content with LineRange
            line_range = LineRange(start_line=1, end_line=2)
            mat = local_prov.get_file_content(
                repo=temp_dir,
                file_path="pkg/mod.py",
                line_range=line_range,
            )
            self.assertEqual(mat.file_path, "pkg/mod.py")
            self.assertEqual(mat.language, "python")
            self.assertEqual(mat.content, "def add(a: int, b: int) -> int:\n    return a + b\n")
            self.assertEqual(mat.checksum, compute_sha256(mat.raw_bytes))

    # -------------------------------------------------------------------------
    # 13. Local Filesystem Traversal Guards & Security
    # -------------------------------------------------------------------------

    def test_13_local_filesystem_provider_traversal_security(self):
        """Verify LocalRepositoryProvider guards against directory traversal exploits."""
        with tempfile.TemporaryDirectory() as temp_dir:
            local_prov = LocalRepositoryProvider(base_directory=temp_dir)

            # 1. Non-existent root directory
            with self.assertRaises(RepositoryNotFoundError):
                local_prov.resolve_identity(os.path.join(temp_dir, "does_not_exist"))

            # 2. Traversal escaping root
            with self.assertRaises((RepositorySecurityError, RepositoryValidationError)):
                local_prov.get_file_content(temp_dir, "../../etc/passwd")

            with self.assertRaises((RepositorySecurityError, RepositoryValidationError)):
                local_prov.get_file_content(temp_dir, "../escape.py")

            # 3. Missing file inside root
            with self.assertRaises(RepositoryFileNotFoundError):
                local_prov.get_file_content(temp_dir, "non_existent.txt")

    # -------------------------------------------------------------------------
    # 14. Provenance & Evidence Interoperability
    # -------------------------------------------------------------------------

    def test_14_provenance_and_raw_source_interoperability(self):
        """Verify RepositorySourceMaterial integrates seamlessly with RawSourceReference and EvidenceItem."""
        mat = self.mock_provider.get_file_content(
            repo="repo-autonomos-engine",
            file_path="src/engine/core.py",
            revision="main",
            line_range=LineRange(start_line=1, end_line=3),
        )

        # 1. Convert to RawSourceReference
        raw_ref = mat.to_raw_source_reference()
        self.assertEqual(raw_ref.source_type, SourceType.REPOSITORY)
        self.assertTrue(raw_ref.url_or_ref.startswith("https://github.com/autonomos-org/engine"))
        self.assertIn("/blob/", raw_ref.url_or_ref)
        self.assertEqual(raw_ref.checksum, mat.checksum)

        # 2. Convert to EvidenceItems
        evidence_items = mat.to_evidence_items(
            request_id="req-901",
            crawler_task_id="ctask-501",
            crawler_id="crawler.repo.01",
            question_id="q-101",
        )
        self.assertEqual(len(evidence_items), 1)
        item = evidence_items[0]
        self.assertEqual(item.source_type, SourceType.REPOSITORY)
        self.assertEqual(item.classification, FactClassification.SOURCE_CLAIM)
        self.assertEqual(item.confidence, ResearchConfidence.SUPPORTED)
        self.assertEqual(item.reliability_score, 0.9)
        self.assertEqual(item.provenance.crawler_task_id, "ctask-501")
        self.assertEqual(item.provenance.crawler_id, "crawler.repo.01")
        self.assertEqual(item.provenance.question_id, "q-101")
        self.assertEqual(item.provenance.source_ref, mat.source_ref)


if __name__ == "__main__":
    unittest.main()

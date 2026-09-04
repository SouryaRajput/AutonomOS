"""
Unit Tests for Phase 1 / Part 5 / Step 6: Repository Versioning and Provenance Hardening.

Verifies:
1. Same file across two commits (distinct content, checksum, snippet_id, source_ref).
2. Branch differences and isolation.
3. Tag / release version differences and version context.
4. Missing revision raises truthful RepositoryRevisionNotFoundError without fallback.
5. Missing revision in RepositoryCrawler returns truthful FAILED report status.
6. Complete EvidenceProvenance and parent lineage preservation.
7. Hash stability and determinism on identical content.
8. Changed content produces changed SHA-256 hash; unchanged produces identical hash.
9. Mixed-revision prevention across interleaved queries.
10. Local repository provider revision context and provenance.
11. Structured code file symbol evidence provenance and revision isolation.
12. Deterministic repeated execution across iterations.
"""
import hashlib
import os
import shutil
import tempfile
import unittest

from core.research.contracts.crawler_report import CrawlerReportStatus
from core.research.contracts.crawler_task import CrawlerTask
from core.research.crawler.repository import RepositoryCrawler
from core.research.errors import (
    RepositoryNotFoundError,
    RepositoryRevisionNotFoundError,
)
from core.research.repo.discovery import RepositoryDiscoveryEngine
from core.research.repo.local_provider import LocalRepositoryProvider
from core.research.repo.mock_provider import MockRepositoryProvider
from core.research.repo.models import (
    LineRange,
    RepoVersionCategory,
    RepoVersionContext,
    RepositoryFile,
    RepositoryIdentity,
    RepositoryProviderType,
    RepositoryRevision,
    RepositorySourceMaterial,
    RepositoryTree,
    compute_sha256,
)
from core.research.repo.structure import CodeStructureExtractor
from core.research.repo.targeted import (
    RepoTopicQuery,
    TargetedRepositoryEngine,
)
from core.research.types import (
    CrawlerCapability,
    CrawlerStatus,
    FactClassification,
    ResearchConfidence,
    SourceType,
)


class TestRepositoryVersioningAndProvenance(unittest.TestCase):
    """
    Comprehensive verification suite for repository versioning, provenance,
    cryptographic hashing, and strict revision isolation.
    """

    def setUp(self):
        self.provider = MockRepositoryProvider(provider_id="mock-prov-v6")
        self.ident = RepositoryIdentity(
            repo_id="repo-auth-service",
            url="https://github.com/autonomos-org/auth-service.git",
            provider_type=RepositoryProviderType.GITHUB,
            owner="autonomos-org",
            name="auth-service",
            default_branch="main",
        )
        self.provider.register_repository(self.ident)

    # -------------------------------------------------------------------------
    # 1. Same File Across Two Commits
    # -------------------------------------------------------------------------

    def test_01_same_file_across_two_commits(self):
        """Verify that the same file across two commits produces distinct content, checksum, snippet_id, and source_ref."""
        commit_a_sha = "1111111111111111111111111111111111111111"
        commit_b_sha = "2222222222222222222222222222222222222222"

        rev_a = RepositoryRevision(
            commit_sha=commit_a_sha,
            branch="main",
            author="Dev A",
            commit_message="Initial auth",
            version_context=RepoVersionContext.from_commit(commit_a_sha),
        )
        rev_b = RepositoryRevision(
            commit_sha=commit_b_sha,
            branch="main",
            author="Dev B",
            commit_message="Add secret validation",
            version_context=RepoVersionContext.from_commit(commit_b_sha),
        )

        self.provider.register_revision("repo-auth-service", rev_a)
        self.provider.register_revision("repo-auth-service", rev_b)

        content_v1 = "def authenticate(token: str) -> bool:\n    return True\n"
        content_v2 = "def authenticate(token: str, secret: str) -> bool:\n    return verify_jwt(token, secret)\n"

        self.provider.register_file("repo-auth-service", commit_a_sha, "src/auth/jwt.py", content_v1)
        self.provider.register_file("repo-auth-service", commit_b_sha, "src/auth/jwt.py", content_v2)

        # Retrieve file at commit A
        mat_a = self.provider.get_file_content("repo-auth-service", "src/auth/jwt.py", revision=commit_a_sha)
        # Retrieve file at commit B
        mat_b = self.provider.get_file_content("repo-auth-service", "src/auth/jwt.py", revision=commit_b_sha)

        # 1. Check content distinctness
        self.assertEqual(mat_a.content, content_v1)
        self.assertEqual(mat_b.content, content_v2)
        self.assertNotEqual(mat_a.content, mat_b.content)

        # 2. Check checksums
        self.assertEqual(mat_a.content_checksum, compute_sha256(content_v1))
        self.assertEqual(mat_b.content_checksum, compute_sha256(content_v2))
        self.assertNotEqual(mat_a.content_checksum, mat_b.content_checksum)

        # 3. Check snippet_ids
        self.assertNotEqual(mat_a.snippet_id, mat_b.snippet_id)

        # 4. Check source_refs
        self.assertIn(commit_a_sha, mat_a.source_ref)
        self.assertIn(commit_b_sha, mat_b.source_ref)
        self.assertEqual(mat_a.source_ref, f"https://github.com/autonomos-org/auth-service/blob/{commit_a_sha}/src/auth/jwt.py")
        self.assertEqual(mat_b.source_ref, f"https://github.com/autonomos-org/auth-service/blob/{commit_b_sha}/src/auth/jwt.py")

        # 5. Check EvidenceItems
        ev_a = mat_a.to_evidence_items(request_id="req-1", crawler_task_id="ctask-1", crawler_id="c-1")[0]
        ev_b = mat_b.to_evidence_items(request_id="req-1", crawler_task_id="ctask-1", crawler_id="c-1")[0]

        self.assertNotEqual(ev_a.evidence_id, ev_b.evidence_id)
        self.assertNotEqual(ev_a.checksum, ev_b.checksum)
        self.assertEqual(ev_a.provenance.source_ref, mat_a.source_ref)
        self.assertEqual(ev_b.provenance.source_ref, mat_b.source_ref)
        self.assertEqual(ev_a.metadata["commit_sha"], commit_a_sha)
        self.assertEqual(ev_b.metadata["commit_sha"], commit_b_sha)

    # -------------------------------------------------------------------------
    # 2. Branch Differences & Isolation
    # -------------------------------------------------------------------------

    def test_02_branch_differences_and_isolation(self):
        """Verify that branch queries return strictly branch-specific trees and revisions."""
        sha_main = "aaaa00000000000000000000000000000000aaaa"
        sha_feat = "bbbb00000000000000000000000000000000bbbb"

        rev_main = RepositoryRevision(
            commit_sha=sha_main,
            branch="main",
            version_context=RepoVersionContext.from_branch("main"),
        )
        rev_feat = RepositoryRevision(
            commit_sha=sha_feat,
            branch="feature/oauth",
            version_context=RepoVersionContext.from_branch("feature/oauth"),
        )

        self.provider.register_revision("repo-auth-service", rev_main, branch="main")
        self.provider.register_revision("repo-auth-service", rev_feat, branch="feature/oauth")

        self.provider.register_file("repo-auth-service", "main", "config.py", "ENV='production'\n")
        self.provider.register_file("repo-auth-service", "feature/oauth", "config.py", "ENV='staging'\n")
        self.provider.register_file("repo-auth-service", "feature/oauth", "src/oauth.py", "def oauth(): pass\n")

        discovery = RepositoryDiscoveryEngine(provider=self.provider)

        # Discover main
        disc_main = discovery.discover("repo-auth-service", revision="main")
        self.assertEqual(disc_main.revision.commit_sha, sha_main)
        self.assertEqual(disc_main.revision.branch, "main")
        self.assertEqual(disc_main.revision.version_context.category, RepoVersionCategory.BRANCH)
        self.assertEqual(disc_main.tree.total_files, 1)
        self.assertIsNone(disc_main.tree.get_file("src/oauth.py"))

        # Discover feature/oauth
        disc_feat = discovery.discover("repo-auth-service", revision="feature/oauth")
        self.assertEqual(disc_feat.revision.commit_sha, sha_feat)
        self.assertEqual(disc_feat.revision.branch, "feature/oauth")
        self.assertEqual(disc_feat.tree.total_files, 2)
        self.assertIsNotNone(disc_feat.tree.get_file("src/oauth.py"))

    # -------------------------------------------------------------------------
    # 3. Tag / Release Version Differences
    # -------------------------------------------------------------------------

    def test_03_tag_and_release_version_differences(self):
        """Verify tag-based revisions maintain explicit TAG version context."""
        sha_v1 = "1000000000000000000000000000000000000001"
        sha_v2 = "2000000000000000000000000000000000000002"

        rev_v1 = RepositoryRevision(
            commit_sha=sha_v1,
            tag="v1.0.0",
            version_context=RepoVersionContext.tag("v1.0.0"),
        )
        rev_v2 = RepositoryRevision(
            commit_sha=sha_v2,
            tag="v2.0.0",
            version_context=RepoVersionContext.tag("v2.0.0"),
        )

        self.provider.register_revision("repo-auth-service", rev_v1, tag="v1.0.0")
        self.provider.register_revision("repo-auth-service", rev_v2, tag="v2.0.0")

        self.provider.register_file("repo-auth-service", "v1.0.0", "version.py", "__version__ = '1.0.0'\n")
        self.provider.register_file("repo-auth-service", "v2.0.0", "version.py", "__version__ = '2.0.0'\n")

        mat_v1 = self.provider.get_file_content("repo-auth-service", "version.py", revision="v1.0.0")
        mat_v2 = self.provider.get_file_content("repo-auth-service", "version.py", revision="v2.0.0")

        self.assertEqual(mat_v1.revision.version_context.category, RepoVersionCategory.TAG)
        self.assertEqual(mat_v1.revision.version_context.version_string, "v1.0.0")
        self.assertEqual(mat_v2.revision.version_context.category, RepoVersionCategory.TAG)
        self.assertEqual(mat_v2.revision.version_context.version_string, "v2.0.0")

        raw_ref1 = mat_v1.to_raw_source_reference()
        raw_ref2 = mat_v2.to_raw_source_reference()

        self.assertEqual(raw_ref1.metadata["commit_sha"], sha_v1)
        self.assertEqual(raw_ref1.metadata["tag"], "v1.0.0")
        self.assertEqual(raw_ref2.metadata["commit_sha"], sha_v2)
        self.assertEqual(raw_ref2.metadata["tag"], "v2.0.0")

    # -------------------------------------------------------------------------
    # 4. Missing Revision Raises Truthful Error
    # -------------------------------------------------------------------------

    def test_04_missing_revision_raises_truthful_error(self):
        """Verify that requesting a missing revision raises RepositoryRevisionNotFoundError without silent fallback."""
        rev = RepositoryRevision(
            commit_sha="abcdef0123456789abcdef0123456789abcdef01",
            branch="main",
        )
        self.provider.register_revision("repo-auth-service", rev, branch="main")

        # 1. Missing branch
        with self.assertRaises(RepositoryRevisionNotFoundError) as ctx:
            self.provider.get_revision("repo-auth-service", revision="non-existent-branch")
        self.assertEqual(ctx.exception.revision, "non-existent-branch")

        # 2. Missing tag in get_file_content
        with self.assertRaises(RepositoryRevisionNotFoundError) as ctx:
            self.provider.get_file_content("repo-auth-service", "README.md", revision="v99.0.0")
        self.assertEqual(ctx.exception.revision, "v99.0.0")

        # 3. Discovery with missing revision
        discovery = RepositoryDiscoveryEngine(provider=self.provider)
        with self.assertRaises(RepositoryRevisionNotFoundError) as ctx:
            discovery.discover("repo-auth-service", revision="non-existent-sha")
        self.assertEqual(ctx.exception.revision, "non-existent-sha")

    # -------------------------------------------------------------------------
    # 5. Missing Revision in RepositoryCrawler Fails Truthfully
    # -------------------------------------------------------------------------

    def test_05_missing_revision_in_repository_crawler_fails_truthfully(self):
        """Verify that RepositoryCrawler returns a truthful FAILED report when revision is missing."""
        crawler = RepositoryCrawler(provider=self.provider)
        task = CrawlerTask(
            task_id="ctask-missing-rev-01",
            request_id="req-101",
            plan_id="plan-201",
            question_id="q-301",
            query_or_target="https://github.com/autonomos-org/auth-service.git",
            required_capability=CrawlerCapability.REPOSITORY_INSPECTION,
            parameters={
                "topic": "authentication",
                "revision": "non-existent-rev-999",
            },
        )

        report = crawler.execute_crawler_task(task)

        self.assertEqual(report.status, CrawlerReportStatus.FAILED)
        self.assertEqual(crawler.status, CrawlerStatus.FAILED)
        self.assertIn("Revision 'non-existent-rev-999' not found", report.error_message)
        self.assertEqual(len(report.raw_sources), 0)
        self.assertEqual(len(report.extracted_evidence), 0)

    # -------------------------------------------------------------------------
    # 6. Complete Evidence Provenance & Lineage
    # -------------------------------------------------------------------------

    def test_06_complete_evidence_provenance_and_lineage(self):
        """Verify full lineage preservation across CrawlerReport, RawSourceReference, and EvidenceItem."""
        commit_sha = "abcdef0123456789abcdef0123456789abcdef01"
        rev = RepositoryRevision(
            commit_sha=commit_sha,
            branch="main",
            author="Dev Provenance",
            commit_message="Security updates",
        )
        self.provider.register_revision("repo-auth-service", rev, branch="main")
        py_content = "class TokenValidator:\n    def validate(self): return True\n"
        self.provider.register_file("repo-auth-service", "main", "src/auth/validator.py", py_content)

        crawler = RepositoryCrawler(provider=self.provider)
        task = CrawlerTask(
            task_id="ctask-lineage-01",
            request_id="req-lineage-100",
            plan_id="plan-lineage-200",
            question_id="q-lineage-300",
            correlation_id="corr-lineage-400",
            query_or_target="https://github.com/autonomos-org/auth-service.git",
            required_capability=CrawlerCapability.REPOSITORY_INSPECTION,
            parameters={"topic": "validator", "revision": "main"},
        )

        report = crawler.execute_crawler_task(task)

        self.assertEqual(report.status, CrawlerReportStatus.SUCCESS)
        self.assertEqual(report.request_id, "req-lineage-100")
        self.assertEqual(report.plan_id, "plan-lineage-200")
        self.assertEqual(report.question_id, "q-lineage-300")
        self.assertEqual(report.correlation_id, "corr-lineage-400")

        # Verify RawSourceReference
        raw_src = report.raw_sources[0]
        self.assertEqual(raw_src.source_type, SourceType.REPOSITORY)
        self.assertEqual(raw_src.checksum, compute_sha256(py_content))
        self.assertEqual(raw_src.metadata["commit_sha"], commit_sha)
        self.assertEqual(raw_src.metadata["repo_url"], "https://github.com/autonomos-org/auth-service.git")
        self.assertEqual(raw_src.metadata["provider_type"], "github")

        # Verify EvidenceItem
        ev = report.extracted_evidence[0]
        self.assertEqual(ev.source_type, SourceType.REPOSITORY)
        self.assertEqual(ev.classification, FactClassification.SOURCE_CLAIM)
        self.assertEqual(ev.confidence, ResearchConfidence.SUPPORTED)
        self.assertEqual(ev.checksum, compute_sha256(py_content))
        self.assertEqual(ev.provenance.request_id, "req-lineage-100")
        self.assertEqual(ev.provenance.crawler_task_id, "ctask-lineage-01")
        self.assertEqual(ev.provenance.crawler_id, crawler.crawler_id)
        self.assertEqual(ev.provenance.question_id, "q-lineage-300")
        self.assertEqual(ev.provenance.correlation_id, "corr-lineage-400")
        self.assertEqual(ev.provenance.source_ref, raw_src.url_or_ref)

    # -------------------------------------------------------------------------
    # 7. Hash Stability & Determinism
    # -------------------------------------------------------------------------

    def test_07_hash_stability_and_determinism(self):
        """Verify SHA-256 hash stability across 100 repeated executions on identical content."""
        content = "const JWT_SECRET = 'super-secret-key';\nexport default JWT_SECRET;\n"
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()

        for _ in range(100):
            computed = compute_sha256(content)
            self.assertEqual(computed, expected)

    # -------------------------------------------------------------------------
    # 8. Changed Content Produces Changed Hash
    # -------------------------------------------------------------------------

    def test_08_changed_content_produces_changed_hash(self):
        """Verify that modifying a single character changes the SHA-256 hash, while unchanged preserves it."""
        c1 = "function calculateFee(amount) { return amount * 0.05; }"
        c2 = "function calculateFee(amount) { return amount * 0.06; }"

        h1_a = compute_sha256(c1)
        h1_b = compute_sha256(c1)
        h2 = compute_sha256(c2)

        self.assertEqual(h1_a, h1_b)
        self.assertNotEqual(h1_a, h2)

    # -------------------------------------------------------------------------
    # 9. Mixed-Revision Prevention
    # -------------------------------------------------------------------------

    def test_09_mixed_revision_prevention(self):
        """Verify interleaved targeted engine queries against distinct revisions never leak cross-revision data."""
        sha_v1 = "1111000000000000000000000000000000001111"
        sha_v2 = "2222000000000000000000000000000000002222"

        rev_v1 = RepositoryRevision(commit_sha=sha_v1, branch="release-1.0")
        rev_v2 = RepositoryRevision(commit_sha=sha_v2, branch="release-2.0")

        self.provider.register_revision("repo-auth-service", rev_v1, branch="release-1.0")
        self.provider.register_revision("repo-auth-service", rev_v2, branch="release-2.0")

        self.provider.register_file("repo-auth-service", "release-1.0", "src/auth.py", "# v1 auth\n")
        self.provider.register_file("repo-auth-service", "release-2.0", "src/auth.py", "# v2 auth with MFA\n")

        engine = TargetedRepositoryEngine(provider=self.provider)
        query = RepoTopicQuery(raw_topic="auth")

        # Interleave 10 queries
        for i in range(10):
            target_rev = "release-1.0" if (i % 2 == 0) else "release-2.0"
            expected_sha = sha_v1 if (i % 2 == 0) else sha_v2
            expected_str = "# v1 auth\n" if (i % 2 == 0) else "# v2 auth with MFA\n"

            res = engine.crawl_targeted(
                target="repo-auth-service",
                query=query,
                revision=target_rev,
            )

            self.assertEqual(len(res.materials_retrieved), 1)
            mat = res.materials_retrieved[0]
            self.assertEqual(mat.revision.commit_sha, expected_sha)
            self.assertEqual(mat.content, expected_str)
            self.assertEqual(mat.content_checksum, compute_sha256(expected_str))

    # -------------------------------------------------------------------------
    # 10. Local Repository Provider Revision Context
    # -------------------------------------------------------------------------

    def test_10_local_repository_provider_revision_context(self):
        """Verify LocalRepositoryProvider correctly constructs deterministic revision context and snippet_ids."""
        temp_dir = tempfile.mkdtemp(prefix="autonomos_repo_prov_")
        try:
            auth_dir = os.path.join(temp_dir, "src", "auth")
            os.makedirs(auth_dir, exist_ok=True)
            file_path = os.path.join(auth_dir, "session.py")
            code = "class Session:\n    pass\n"
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code)

            local_provider = LocalRepositoryProvider(base_directory=temp_dir)
            ident = local_provider.resolve_identity(temp_dir)
            self.assertEqual(ident.provider_type, RepositoryProviderType.LOCAL_GIT)

            # Query tag revision
            rev_tag = local_provider.get_revision(temp_dir, revision="v1.5.0")
            self.assertEqual(rev_tag.version_context.category, RepoVersionCategory.TAG)
            self.assertEqual(rev_tag.version_context.version_string, "v1.5.0")

            # Query branch revision
            rev_branch = local_provider.get_revision(temp_dir, revision="feature/session")
            self.assertEqual(rev_branch.version_context.category, RepoVersionCategory.BRANCH)
            self.assertEqual(rev_branch.version_context.version_string, "feature/session")

            # Retrieve content
            mat = local_provider.get_file_content(temp_dir, "src/auth/session.py", revision="v1.5.0")
            self.assertEqual(mat.content, code)
            self.assertEqual(mat.content_checksum, compute_sha256(code))
            self.assertEqual(mat.revision.tag, "v1.5.0")
            self.assertIn(mat.revision.commit_sha, mat.source_ref)
            self.assertIn("src/auth/session.py", mat.source_ref)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 11. Structured Code File Symbol Evidence Provenance & Revision Isolation
    # -------------------------------------------------------------------------

    def test_11_structured_code_file_symbol_evidence_provenance_isolation(self):
        """Verify StructuredCodeFile creates revision-isolated evidence items with non-colliding IDs and source_refs."""
        py_code = """class Authenticator:
    def authenticate(self, user: str) -> bool:
        return True
"""
        struct_a = CodeStructureExtractor.extract_structure(
            file_path="src/auth/authenticator.py",
            content=py_code,
            language="python",
            repository_id="repo-auth-service",
            revision="commit-aaaa-1111",
        )

        struct_b = CodeStructureExtractor.extract_structure(
            file_path="src/auth/authenticator.py",
            content=py_code,
            language="python",
            repository_id="repo-auth-service",
            revision="commit-bbbb-2222",
        )

        evs_a = struct_a.to_evidence_items(
            request_id="req-1",
            crawler_task_id="ctask-1",
            crawler_id="c-1",
            repo_url="https://github.com/autonomos-org/auth-service",
        )

        evs_b = struct_b.to_evidence_items(
            request_id="req-1",
            crawler_task_id="ctask-1",
            crawler_id="c-1",
            repo_url="https://github.com/autonomos-org/auth-service",
        )

        self.assertEqual(len(evs_a), 1)  # Class Authenticator
        self.assertEqual(len(evs_b), 1)

        # Check evidence ID revision isolation
        self.assertNotEqual(evs_a[0].evidence_id, evs_b[0].evidence_id)
        self.assertIn("commit-a", evs_a[0].evidence_id)
        self.assertIn("commit-b", evs_b[0].evidence_id)

        # Check source_ref revision isolation
        self.assertIn("commit-aaaa-1111", evs_a[0].provenance.source_ref)
        self.assertIn("commit-bbbb-2222", evs_b[0].provenance.source_ref)

    # -------------------------------------------------------------------------
    # 12. Deterministic Repeated Execution
    # -------------------------------------------------------------------------

    def test_12_deterministic_repeated_execution(self):
        """Verify that extracting and serializing materials across 10 iterations produces 100% identical outputs."""
        sha = "9999999999999999999999999999999999999999"
        rev = RepositoryRevision(commit_sha=sha, branch="main")
        self.provider.register_revision("repo-auth-service", rev, branch="main")
        self.provider.register_file("repo-auth-service", "main", "src/auth/jwt.py", "def token(): pass\n")

        crawler = RepositoryCrawler(provider=self.provider)
        task = CrawlerTask(
            task_id="ctask-repeat-01",
            request_id="req-repeat-100",
            plan_id="plan-repeat-200",
            question_id="q-repeat-300",
            query_or_target="https://github.com/autonomos-org/auth-service.git",
            required_capability=CrawlerCapability.REPOSITORY_INSPECTION,
            parameters={"topic": "jwt token", "revision": "main"},
        )

        def normalize_dict(d):
            if isinstance(d, dict):
                cleaned = {}
                for k, v in d.items():
                    if k in ("report_id", "execution_time_seconds", "retrieved_at", "captured_at", "fetched_at", "discovered_at", "created_at", "updated_at"):
                        continue
                    cleaned[k] = normalize_dict(v)
                return cleaned
            elif isinstance(d, list):
                return [normalize_dict(x) for x in d]
            return d

        baseline_dict = normalize_dict(crawler.execute_crawler_task(task).to_dict())

        for _ in range(10):
            crawler.reset_status()
            current_dict = normalize_dict(crawler.execute_crawler_task(task).to_dict())
            self.assertEqual(current_dict, baseline_dict)


if __name__ == "__main__":
    unittest.main()

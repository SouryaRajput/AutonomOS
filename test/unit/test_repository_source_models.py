"""
Unit Tests for Repository Source and Structural Domain Models (Phase 1 / Part 5 / Step 1).

Verifies:
1. Repository Provider Type Detection & Parsing (GitHub, GitLab, Bitbucket, Local Git, Generic Git, Archive, Unknown)
2. Repository Version Context (tag, branch, commit, latest, stable, unknown) and Immutability
3. Repository Identity Model, URL Parsing, Validation, and Serialization
4. Repository Revision Model, Commits, Branches, Tags, Timestamps, and Alignment
5. LineRange Model, Boundaries, Overlaps, Membership, and Validation
6. RepositoryFile Metadata, Path Normalization, Language Detection, Binary Classification, Checksums
7. Deterministic Language Detection for Code & Config Files
8. Binary File Classification
9. RepositoryDirectory Model and Child Path Tracking
10. RepositoryTree Hierarchy, Depth Tracking, Lookups, Truncation, and Bounds
11. RepositorySourceMaterial (Code Snippets, Line Ranges, Checksums, Source URI generation)
12. Conversion to RawSourceReference (SourceType.REPOSITORY, Checksums, Metadata)
13. Conversion to EvidenceItem with Full EvidenceProvenance Lineage
14. RepositorySource Root Aggregate Model (Tree, Snippets, Serialization)
15. Validation Errors on Invalid Inputs and Root Path Traversal Attempts
16. Edge Cases (Large content, Deep paths, Special characters)
"""
from __future__ import annotations

import unittest
import uuid

from core.research.contracts.crawler_report import RawSourceReference
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.errors import RepositoryError, RepositoryValidationError
from core.research.repo.models import (
    EXTENSION_LANGUAGE_MAP,
    KNOWN_BINARY_EXTENSIONS,
    LineRange,
    RepoVersionCategory,
    RepoVersionContext,
    RepositoryDirectory,
    RepositoryFile,
    RepositoryIdentity,
    RepositoryProviderType,
    RepositoryRevision,
    RepositorySource,
    RepositorySourceMaterial,
    RepositoryTree,
    compute_sha256,
    detect_file_language,
    is_known_binary_extension,
    normalize_repo_path,
)
from core.research.types import FactClassification, ResearchConfidence, SourceType


class TestRepositorySourceModels(unittest.TestCase):
    """
    Comprehensive unit test suite for repository-domain data models.
    """

    # -------------------------------------------------------------------------
    # 1. Repository Provider Type Detection
    # -------------------------------------------------------------------------

    def test_01_provider_type_detection_from_urls(self):
        """Verify deterministic repository provider inference from URLs and paths."""
        cases = [
            ("https://github.com/torvalds/linux", RepositoryProviderType.GITHUB),
            ("https://github.com/facebook/react.git", RepositoryProviderType.GITHUB),
            ("git@github.com:rust-lang/rust.git", RepositoryProviderType.GITHUB),
            ("https://gitlab.com/gitlab-org/gitlab", RepositoryProviderType.GITLAB),
            ("git@gitlab.com:gnome/gimp.git", RepositoryProviderType.GITLAB),
            ("https://bitbucket.org/atlassian/local-stack", RepositoryProviderType.BITBUCKET),
            ("file:///Users/dev/projects/my-repo", RepositoryProviderType.LOCAL_GIT),
            ("/var/repos/embedded-sys.git", RepositoryProviderType.LOCAL_GIT),
            ("https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git", RepositoryProviderType.GENERIC_GIT),
            ("git@custom-git-server.internal:team/repo.git", RepositoryProviderType.GENERIC_GIT),
            ("https://example.com/downloads/v1.0.tar.gz", RepositoryProviderType.ARCHIVE),
            ("https://example.com/releases/bundle.zip", RepositoryProviderType.ARCHIVE),
            ("ftp://unsupported.host/repo", RepositoryProviderType.UNKNOWN),
            ("", RepositoryProviderType.UNKNOWN),
        ]

        for url, expected_provider in cases:
            provider = RepositoryProviderType.from_url(url)
            self.assertEqual(provider, expected_provider, f"Failed provider detection for '{url}'")

    # -------------------------------------------------------------------------
    # 2. Version Context Factories & Immutability
    # -------------------------------------------------------------------------

    def test_02_version_context_factories_and_immutability(self):
        """Verify RepoVersionContext factories, categories, and serialization."""
        # 1. Unknown
        vc_unk = RepoVersionContext.unknown()
        self.assertEqual(vc_unk.category, RepoVersionCategory.UNKNOWN)
        self.assertIsNone(vc_unk.version_string)
        self.assertFalse(vc_unk.is_default)

        # 2. Latest
        vc_lat = RepoVersionContext.latest()
        self.assertEqual(vc_lat.category, RepoVersionCategory.LATEST)
        self.assertEqual(vc_lat.version_string, "latest")
        self.assertTrue(vc_lat.is_default)

        # 3. Stable
        vc_stb = RepoVersionContext.stable("2.0.0")
        self.assertEqual(vc_stb.category, RepoVersionCategory.STABLE)
        self.assertEqual(vc_stb.version_string, "2.0.0")

        # 4. Tag
        vc_tag = RepoVersionContext.tag("v1.4.2")
        self.assertEqual(vc_tag.category, RepoVersionCategory.TAG)
        self.assertEqual(vc_tag.version_string, "v1.4.2")

        # 5. Branch
        vc_br = RepoVersionContext.branch("feature/async-pipeline")
        self.assertEqual(vc_br.category, RepoVersionCategory.BRANCH)
        self.assertEqual(vc_br.version_string, "feature/async-pipeline")

        # 6. Commit
        vc_sha = RepoVersionContext.commit("4b825dc642cb6eb9a060e54bf8d69288fbee4904")
        self.assertEqual(vc_sha.category, RepoVersionCategory.COMMIT)
        self.assertEqual(vc_sha.version_string, "4b825dc642cb6eb9a060e54bf8d69288fbee4904")

        # 7. Serialization roundtrip
        dict_data = vc_tag.to_dict()
        vc_restored = RepoVersionContext.from_dict(dict_data)
        self.assertEqual(vc_tag, vc_restored)

        # 8. Validation errors on empty tag/branch/commit
        with self.assertRaises(RepositoryValidationError):
            RepoVersionContext.tag("")
        with self.assertRaises(RepositoryValidationError):
            RepoVersionContext.branch("   ")
        with self.assertRaises(RepositoryValidationError):
            RepoVersionContext.commit("")

    # -------------------------------------------------------------------------
    # 3. Repository Identity Model & URL Parsing
    # -------------------------------------------------------------------------

    def test_03_repository_identity_instantiation_and_validation(self):
        """Verify RepositoryIdentity instantiation, URL parsing, and validation."""
        # 1. Direct instantiation
        ident = RepositoryIdentity(
            repo_id="repo-autonomos",
            url="https://github.com/autonomos-org/autonomos-core",
            owner="autonomos-org",
            name="autonomos-core",
            default_branch="main",
            is_private=False,
            description="Autonomous multi-agent operating system",
            primary_language="python",
        )
        self.assertEqual(ident.provider_type, RepositoryProviderType.GITHUB)
        self.assertEqual(ident.full_name, "autonomos-org/autonomos-core")

        # 2. Parsing from URL via factory
        parsed_ident = RepositoryIdentity.from_url(
            url="https://github.com/pallets/flask.git",
            default_branch="main",
        )
        self.assertEqual(parsed_ident.provider_type, RepositoryProviderType.GITHUB)
        self.assertEqual(parsed_ident.owner, "pallets")
        self.assertEqual(parsed_ident.name, "flask")
        self.assertEqual(parsed_ident.full_name, "pallets/flask")
        self.assertEqual(parsed_ident.default_branch, "main")

        # 3. Parsing SSH format
        ssh_ident = RepositoryIdentity.from_url("git@gitlab.com:kicad/code/kicad.git")
        self.assertEqual(ssh_ident.provider_type, RepositoryProviderType.GITLAB)
        self.assertEqual(ssh_ident.owner, "kicad/code")
        self.assertEqual(ssh_ident.name, "kicad")

        # 4. Serialization roundtrip
        ident_dict = ident.to_dict()
        ident_restored = RepositoryIdentity.from_dict(ident_dict)
        self.assertEqual(ident.repo_id, ident_restored.repo_id)
        self.assertEqual(ident.url, ident_restored.url)
        self.assertEqual(ident.full_name, ident_restored.full_name)
        self.assertEqual(ident.provider_type, ident_restored.provider_type)

        # 5. Validation failures
        with self.assertRaises(RepositoryValidationError):
            RepositoryIdentity(repo_id="", url="https://github.com/foo/bar")
        with self.assertRaises(RepositoryValidationError):
            RepositoryIdentity(repo_id="r1", url="")
        with self.assertRaises(RepositoryValidationError):
            RepositoryIdentity.from_url("")

    # -------------------------------------------------------------------------
    # 4. Repository Revision Model
    # -------------------------------------------------------------------------

    def test_04_repository_revision_and_alignment(self):
        """Verify RepositoryRevision tracking, commit context, and auto-alignment."""
        # 1. Revision with tag
        rev_tag = RepositoryRevision(
            tag="v3.2.1",
            author="Linus Torvalds <torvalds@kernel.org>",
            commit_message="Release v3.2.1",
        )
        self.assertEqual(rev_tag.version_context.category, RepoVersionCategory.TAG)
        self.assertEqual(rev_tag.version_context.version_string, "v3.2.1")
        self.assertEqual(rev_tag.author, "Linus Torvalds <torvalds@kernel.org>")

        # 2. Revision with branch
        rev_br = RepositoryRevision(branch="develop")
        self.assertEqual(rev_br.version_context.category, RepoVersionCategory.BRANCH)
        self.assertEqual(rev_br.version_context.version_string, "develop")

        # 3. Revision with commit SHA
        rev_sha = RepositoryRevision(commit_sha="e3b0c44298fc1c149afbf4c8996fb92427ae41e4")
        self.assertEqual(rev_sha.version_context.category, RepoVersionCategory.COMMIT)
        self.assertEqual(rev_sha.version_context.version_string, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4")

        # 4. Default snapshot
        rev_def = RepositoryRevision.default()
        self.assertTrue(rev_def.retrieved_at)
        self.assertEqual(rev_def.version_context.category, RepoVersionCategory.UNKNOWN)

        # 5. Serialization roundtrip
        dict_data = rev_tag.to_dict()
        rev_restored = RepositoryRevision.from_dict(dict_data)
        self.assertEqual(rev_tag.tag, rev_restored.tag)
        self.assertEqual(rev_tag.version_context, rev_restored.version_context)
        self.assertEqual(rev_tag.author, rev_restored.author)

    # -------------------------------------------------------------------------
    # 5. Line Range Model & Validations
    # -------------------------------------------------------------------------

    def test_05_line_range_validation_and_methods(self):
        """Verify LineRange validation, line counts, containment, and overlap semantics."""
        lr = LineRange(start_line=10, end_line=25)
        self.assertEqual(lr.start_line, 10)
        self.assertEqual(lr.end_line, 25)
        self.assertEqual(lr.line_count, 16)  # 25 - 10 + 1 = 16

        # Single line range
        single = LineRange(start_line=5, end_line=5)
        self.assertEqual(single.line_count, 1)

        # Membership containment
        self.assertTrue(lr.contains(10))
        self.assertTrue(lr.contains(20))
        self.assertTrue(lr.contains(25))
        self.assertFalse(lr.contains(9))
        self.assertFalse(lr.contains(26))

        # Overlap checks
        self.assertTrue(lr.overlaps(LineRange(start_line=20, end_line=30)))
        self.assertTrue(lr.overlaps(LineRange(start_line=1, end_line=10)))
        self.assertTrue(lr.overlaps(LineRange(start_line=12, end_line=18)))
        self.assertFalse(lr.overlaps(LineRange(start_line=1, end_line=9)))
        self.assertFalse(lr.overlaps(LineRange(start_line=26, end_line=50)))

        # Serialization roundtrip
        lr_dict = lr.to_dict()
        self.assertEqual(lr_dict["line_count"], 16)
        lr_restored = LineRange.from_dict(lr_dict)
        self.assertEqual(lr, lr_restored)

        # Validation errors
        with self.assertRaises(RepositoryValidationError):
            LineRange(start_line=0, end_line=10)
        with self.assertRaises(RepositoryValidationError):
            LineRange(start_line=-5, end_line=10)
        with self.assertRaises(RepositoryValidationError):
            LineRange(start_line=20, end_line=10)

    # -------------------------------------------------------------------------
    # 6. Repository File Model & Path Normalization
    # -------------------------------------------------------------------------

    def test_06_repository_file_metadata_and_normalization(self):
        """Verify RepositoryFile metadata, normalization, checksums, and structural references."""
        # 1. Path normalization & auto-fields
        file_node = RepositoryFile(
            path="src/submodule/../core/./engine.ts",
            size_bytes=4096,
            line_count=120,
            file_references=["src/core/types.ts", "src/utils/logger.ts"],
        )
        self.assertEqual(file_node.path, "src/core/engine.ts")
        self.assertEqual(file_node.filename, "engine.ts")
        self.assertEqual(file_node.extension, ".ts")
        self.assertEqual(file_node.parent_path, "src/core")
        self.assertEqual(file_node.language, "typescript")
        self.assertFalse(file_node.is_binary)
        self.assertEqual(file_node.file_references, ["src/core/types.ts", "src/utils/logger.ts"])

        # 2. Root-level file
        root_file = RepositoryFile(path="README.md", size_bytes=500)
        self.assertEqual(root_file.path, "README.md")
        self.assertEqual(root_file.filename, "README.md")
        self.assertIsNone(root_file.parent_path)
        self.assertEqual(root_file.language, "markdown")

        # 3. Serialization roundtrip
        file_dict = file_node.to_dict()
        file_restored = RepositoryFile.from_dict(file_dict)
        self.assertEqual(file_node.path, file_restored.path)
        self.assertEqual(file_node.filename, file_restored.filename)
        self.assertEqual(file_node.language, file_restored.language)
        self.assertEqual(file_node.file_references, file_restored.file_references)

        # 4. Validation errors
        with self.assertRaises(RepositoryValidationError):
            RepositoryFile(path="")
        with self.assertRaises(RepositoryValidationError):
            RepositoryFile(path="src/main.rs", size_bytes=-1)
        with self.assertRaises(RepositoryValidationError):
            RepositoryFile(path="src/main.rs", line_count=-5)

        # 5. Directory traversal guard
        with self.assertRaises(RepositoryValidationError):
            RepositoryFile(path="../../etc/passwd")

    # -------------------------------------------------------------------------
    # 7. Language Detection & Binary Classifications
    # -------------------------------------------------------------------------

    def test_07_language_detection_and_binary_classifications(self):
        """Verify deterministic programming language detection and binary classification."""
        lang_cases = [
            ("main.py", "python"),
            ("lib/utils.rs", "rust"),
            ("server.go", "go"),
            ("client.ts", "typescript"),
            ("index.js", "javascript"),
            ("App.tsx", "typescript"),
            ("Main.java", "java"),
            ("Kernel.cpp", "cpp"),
            ("header.h", "c"),
            ("styles.css", "css"),
            ("index.html", "html"),
            ("query.sql", "sql"),
            ("config.yaml", "yaml"),
            ("schema.json", "json"),
            ("Cargo.toml", "toml"),
            ("Dockerfile", "dockerfile"),
            ("dockerfile", "dockerfile"),
            ("Makefile", "makefile"),
            ("CMakeLists.txt", "cmake"),
            ("script.sh", "bash"),
            ("pipeline.dart", "dart"),
            ("archive.unknownext", None),
        ]

        for filename, expected_lang in lang_cases:
            detected = detect_file_language(filename)
            self.assertEqual(detected, expected_lang, f"Language detection failed for '{filename}'")

        # Binary extensions
        binary_cases = [
            ("asset.png", True),
            ("photo.jpg", True),
            ("manual.pdf", True),
            ("bundle.zip", True),
            ("library.so", True),
            ("native.dylib", True),
            ("module.wasm", True),
            ("compiled.pyc", True),
            ("code.py", False),
            ("README.md", False),
            ("data.json", False),
        ]

        for filename, expected_binary in binary_cases:
            is_bin = is_known_binary_extension(filename)
            self.assertEqual(is_bin, expected_binary, f"Binary classification failed for '{filename}'")

    # -------------------------------------------------------------------------
    # 8. Repository Directory Model
    # -------------------------------------------------------------------------

    def test_08_repository_directory_model(self):
        """Verify RepositoryDirectory model, paths, parent-child relationships, and serialization."""
        dir_node = RepositoryDirectory(
            path="src/components/ui",
            child_dir_paths=["src/components/ui/buttons", "src/components/ui/dialogs"],
            child_file_paths=["src/components/ui/theme.ts", "src/components/ui/index.ts"],
        )
        self.assertEqual(dir_node.path, "src/components/ui")
        self.assertEqual(dir_node.name, "ui")
        self.assertEqual(dir_node.parent_path, "src/components")
        self.assertEqual(len(dir_node.child_dir_paths), 2)
        self.assertEqual(len(dir_node.child_file_paths), 2)

        # Root directory
        root_dir = RepositoryDirectory(path="")
        self.assertEqual(root_dir.path, "")
        self.assertEqual(root_dir.name, "")
        self.assertIsNone(root_dir.parent_path)

        # Serialization roundtrip
        dict_data = dir_node.to_dict()
        restored = RepositoryDirectory.from_dict(dict_data)
        self.assertEqual(dir_node.path, restored.path)
        self.assertEqual(dir_node.name, restored.name)
        self.assertEqual(dir_node.parent_path, restored.parent_path)
        self.assertEqual(dir_node.child_dir_paths, restored.child_dir_paths)

    # -------------------------------------------------------------------------
    # 9. Repository Tree Hierarchical Lookups & Bounds
    # -------------------------------------------------------------------------

    def test_09_repository_tree_hierarchical_lookups_and_bounds(self):
        """Verify RepositoryTree construction, addition, lookups, depth calculation, and truncation."""
        tree = RepositoryTree(root_path="")

        d_src = RepositoryDirectory(path="src", name="src")
        d_core = RepositoryDirectory(path="src/core", name="core", parent_path="src")
        d_utils = RepositoryDirectory(path="src/utils", name="utils", parent_path="src")

        f_root = RepositoryFile(path="package.json", size_bytes=512)
        f_engine = RepositoryFile(path="src/core/engine.ts", size_bytes=2048)
        f_logger = RepositoryFile(path="src/utils/logger.ts", size_bytes=1024)

        tree.add_directory(d_src)
        tree.add_directory(d_core)
        tree.add_directory(d_utils)
        tree.add_file(f_root)
        tree.add_file(f_engine)
        tree.add_file(f_logger)

        # Totals and depth
        self.assertEqual(tree.total_files, 3)
        self.assertEqual(tree.total_directories, 3)
        self.assertEqual(tree.max_depth_reached, 3)  # 'src/core/engine.ts' has depth 3

        # Lookup helpers
        self.assertIsNotNone(tree.get_file("src/core/engine.ts"))
        self.assertIsNone(tree.get_file("nonexistent.py"))
        self.assertIsNotNone(tree.get_directory("src/utils"))
        self.assertIsNone(tree.get_directory("src/missing"))

        # Children lookup under 'src'
        sub_dirs, sub_files = tree.get_children("src")
        self.assertEqual(len(sub_dirs), 2)  # 'src/core' and 'src/utils'
        self.assertEqual(len(sub_files), 0)

        # Children lookup under root ''
        root_dirs, root_files = tree.get_children("")
        self.assertEqual(len(root_dirs), 1)  # 'src'
        self.assertEqual(len(root_files), 1)  # 'package.json'

        # Truncation flags
        tree.is_truncated = True
        tree.truncation_reason = "File count exceeded max_files limit of 5000"

        # Serialization roundtrip
        tree_dict = tree.to_dict()
        tree_restored = RepositoryTree.from_dict(tree_dict)
        self.assertEqual(tree.total_files, tree_restored.total_files)
        self.assertEqual(tree.total_directories, tree_restored.total_directories)
        self.assertEqual(tree.max_depth_reached, tree_restored.max_depth_reached)
        self.assertTrue(tree_restored.is_truncated)
        self.assertEqual(tree_restored.truncation_reason, "File count exceeded max_files limit of 5000")

    # -------------------------------------------------------------------------
    # 10. Repository Source Material & Snippets
    # -------------------------------------------------------------------------

    def test_10_repository_source_material_snippet(self):
        """Verify RepositorySourceMaterial snippet modeling, hashes, line ranges, and source URI generation."""
        ident = RepositoryIdentity(
            repo_id="repo-demo",
            url="https://github.com/example-org/graphics-engine",
            owner="example-org",
            name="graphics-engine",
        )
        rev = RepositoryRevision(
            commit_sha="7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b",
            branch="main",
            version_context=RepoVersionContext.branch("main"),
        )
        code_content = """export class WebGPURenderer {
    private device: GPUDevice;

    async initialize(): Promise<void> {
        const adapter = await navigator.gpu.requestAdapter();
        this.device = await adapter.requestDevice();
    }
}"""

        material = RepositorySourceMaterial(
            snippet_id="snip-renderer-init",
            repository_identity=ident,
            revision=rev,
            file_path="src/renderers/webgpu.ts",
            line_range=LineRange(start_line=1, end_line=8),
            content=code_content,
        )

        self.assertEqual(material.file_path, "src/renderers/webgpu.ts")
        self.assertEqual(material.language, "typescript")
        self.assertEqual(material.content_checksum, compute_sha256(code_content))
        self.assertEqual(
            material.source_ref,
            "https://github.com/example-org/graphics-engine/blob/7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b/src/renderers/webgpu.ts#L1-L8",
        )

        # Serialization roundtrip
        mat_dict = material.to_dict()
        mat_restored = RepositorySourceMaterial.from_dict(mat_dict)
        self.assertEqual(material.snippet_id, mat_restored.snippet_id)
        self.assertEqual(material.file_path, mat_restored.file_path)
        self.assertEqual(material.content_checksum, mat_restored.content_checksum)
        self.assertEqual(material.line_range, mat_restored.line_range)

        # Validation errors
        with self.assertRaises(RepositoryValidationError):
            RepositorySourceMaterial(
                snippet_id="",
                repository_identity=ident,
                revision=rev,
                file_path="src/main.ts",
            )
        with self.assertRaises(RepositoryValidationError):
            RepositorySourceMaterial(
                snippet_id="snip-1",
                repository_identity=ident,
                revision=rev,
                file_path="",
            )

    # -------------------------------------------------------------------------
    # 11. Conversion to RawSourceReference (CrawlerReport Integration)
    # -------------------------------------------------------------------------

    def test_11_conversion_to_raw_source_reference(self):
        """Verify seamless conversion of RepositorySourceMaterial to RawSourceReference."""
        ident = RepositoryIdentity(
            repo_id="repo-react",
            url="https://github.com/facebook/react",
            owner="facebook",
            name="react",
        )
        rev = RepositoryRevision(tag="v18.2.0")
        snippet = RepositorySourceMaterial(
            snippet_id="snip-hooks-01",
            repository_identity=ident,
            revision=rev,
            file_path="packages/react/src/ReactHooks.js",
            content="export function useState(initialState) { return resolveDispatcher().useState(initialState); }",
            line_range=LineRange(start_line=20, end_line=22),
        )

        raw_ref = snippet.to_raw_source_reference()
        self.assertIsInstance(raw_ref, RawSourceReference)
        self.assertEqual(raw_ref.source_type, SourceType.REPOSITORY)
        self.assertEqual(raw_ref.publisher, "facebook")
        self.assertEqual(raw_ref.checksum, snippet.content_checksum)
        self.assertIn("packages/react/src/ReactHooks.js", raw_ref.url_or_ref)
        self.assertEqual(raw_ref.metadata["repo_id"], "repo-react")
        self.assertEqual(raw_ref.metadata["file_path"], "packages/react/src/ReactHooks.js")
        self.assertEqual(raw_ref.metadata["line_range"]["start_line"], 20)

    # -------------------------------------------------------------------------
    # 12. Conversion to EvidenceItem with Full Provenance Lineage
    # -------------------------------------------------------------------------

    def test_12_conversion_to_evidence_items_with_provenance(self):
        """Verify conversion of RepositorySourceMaterial to EvidenceItem collections carrying complete provenance."""
        ident = RepositoryIdentity(
            repo_id="repo-compiler",
            url="https://github.com/rust-lang/rust",
            owner="rust-lang",
            name="rust",
        )
        rev = RepositoryRevision(commit_sha="0123456789abcdef0123456789abcdef01234567")
        snippet = RepositorySourceMaterial(
            snippet_id="snip-borrowck-01",
            repository_identity=ident,
            revision=rev,
            file_path="compiler/rustc_borrowck/src/lib.rs",
            content="pub fn check_crate(tcx: TyCtxt<'_>) { ... }",
            line_range=LineRange(start_line=50, end_line=55),
        )

        evidence_items = snippet.to_evidence_items(
            request_id="req-investigate-borrowck",
            crawler_task_id="ctask-inspect-repo-01",
            crawler_id="crawler.repository.01",
            question_id="q-rust-borrowck",
            correlation_id="corr-trace-repo-888",
        )

        self.assertEqual(len(evidence_items), 1)
        ev = evidence_items[0]
        self.assertIsInstance(ev, EvidenceItem)
        self.assertEqual(ev.classification, FactClassification.SOURCE_CLAIM)
        self.assertEqual(ev.source_type, SourceType.REPOSITORY)
        self.assertEqual(ev.confidence, ResearchConfidence.SUPPORTED)
        self.assertEqual(ev.checksum, snippet.content_checksum)

        # Provenance verification
        prov = ev.provenance
        self.assertEqual(prov.request_id, "req-investigate-borrowck")
        self.assertEqual(prov.crawler_task_id, "ctask-inspect-repo-01")
        self.assertEqual(prov.crawler_id, "crawler.repository.01")
        self.assertEqual(prov.question_id, "q-rust-borrowck")
        self.assertEqual(prov.correlation_id, "corr-trace-repo-888")
        self.assertEqual(prov.source_ref, snippet.source_ref)

    # -------------------------------------------------------------------------
    # 13. RepositorySource Root Aggregate Model
    # -------------------------------------------------------------------------

    def test_13_repository_source_root_aggregate(self):
        """Verify RepositorySource root aggregate encapsulation, aggregation methods, and serialization."""
        ident = RepositoryIdentity.from_url("https://github.com/tokio-rs/tokio")
        rev = RepositoryRevision(branch="master")
        tree = RepositoryTree()
        tree.add_file(RepositoryFile(path="Cargo.toml", size_bytes=1200))
        tree.add_file(RepositoryFile(path="tokio/src/lib.rs", size_bytes=8192))

        snip1 = RepositorySourceMaterial(
            snippet_id="snip-cargo",
            repository_identity=ident,
            revision=rev,
            file_path="Cargo.toml",
            content="[package]\nname = \"tokio\"\nversion = \"1.36.0\"",
        )
        snip2 = RepositorySourceMaterial(
            snippet_id="snip-lib",
            repository_identity=ident,
            revision=rev,
            file_path="tokio/src/lib.rs",
            content="pub mod runtime;\npub mod task;",
        )

        repo_source = RepositorySource(
            identity=ident,
            revision=rev,
            tree=tree,
            source_materials=[snip1, snip2],
        )

        self.assertEqual(repo_source.total_materials_count, 2)
        self.assertEqual(repo_source.total_files_count, 2)

        # Aggregate raw references
        raw_refs = repo_source.to_raw_source_references()
        self.assertEqual(len(raw_refs), 2)
        for ref in raw_refs:
            self.assertEqual(ref.source_type, SourceType.REPOSITORY)

        # Aggregate evidence items
        ev_items = repo_source.to_evidence_items(
            request_id="req-tokio",
            crawler_task_id="ctask-tokio",
            crawler_id="crawler.repo.01",
        )
        self.assertEqual(len(ev_items), 2)

        # Serialization roundtrip
        source_dict = repo_source.to_dict()
        restored_source = RepositorySource.from_dict(source_dict)
        self.assertEqual(repo_source.identity.repo_id, restored_source.identity.repo_id)
        self.assertEqual(repo_source.total_materials_count, restored_source.total_materials_count)
        self.assertEqual(repo_source.total_files_count, restored_source.total_files_count)

    # -------------------------------------------------------------------------
    # 14. Edge Cases: Large Snippets, Deep Paths, Special Characters
    # -------------------------------------------------------------------------

    def test_14_edge_cases_and_error_handling(self):
        """Verify resilience against deep paths, large payloads, binary blobs, and special characters."""
        ident = RepositoryIdentity(
            repo_id="repo-edge-cases",
            url="https://github.com/test-org/unicode-and-blobs",
            owner="test-org",
            name="unicode-and-blobs",
        )
        rev = RepositoryRevision(commit_sha="abcdef1234567890abcdef1234567890abcdef12")

        # 1. Deep path
        deep_path = "a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/q/r/s/t/u/v/w/x/y/z/module.py"
        file_deep = RepositoryFile(path=deep_path, size_bytes=100)
        self.assertEqual(file_deep.path, deep_path)
        self.assertEqual(file_deep.parent_path, "a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/q/r/s/t/u/v/w/x/y/z")

        # 2. Large content snippet
        huge_code = "# Autogenerated Large File\n" + "def process_data(x):\n    return x * 2\n" * 5000
        snip_huge = RepositorySourceMaterial(
            snippet_id="snip-huge",
            repository_identity=ident,
            revision=rev,
            file_path="generated/large.py",
            content=huge_code,
        )
        raw_ref = snip_huge.to_raw_source_reference()
        self.assertEqual(len(raw_ref.content_snippet), 2000)  # Bounded preview in raw source

        # 3. Binary asset material
        binary_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        snip_bin = RepositorySourceMaterial(
            snippet_id="snip-bin-icon",
            repository_identity=ident,
            revision=rev,
            file_path="assets/logo.png",
            raw_bytes=binary_data,
            is_binary=True,
        )
        self.assertTrue(snip_bin.is_binary)
        self.assertEqual(snip_bin.content_checksum, compute_sha256(binary_data))
        raw_bin_ref = snip_bin.to_raw_source_reference()
        self.assertIn("[Binary content: assets/logo.png]", raw_bin_ref.content_snippet)

        # 4. Unicode & emoji in paths, author names, commit messages
        rev_unicode = RepositoryRevision(
            commit_sha="1111222233334444555566667777888899990000",
            author="山田太郎 <taro@example.co.jp>",
            commit_message="🐛 Fix memory leak in WebGPU compute pipeline 🚀",
        )
        snip_unicode = RepositorySourceMaterial(
            snippet_id="snip-utf8",
            repository_identity=ident,
            revision=rev_unicode,
            file_path="src/国際化/設定.json",
            content='{\n  "greeting": "こんにちは、世界！"\n}',
        )
        self.assertEqual(snip_unicode.file_path, "src/国際化/設定.json")
        self.assertEqual(snip_unicode.language, "json")
        self.assertEqual(rev_unicode.author, "山田太郎 <taro@example.co.jp>")


if __name__ == "__main__":
    unittest.main()

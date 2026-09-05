"""
Unit Test Suite for Project Context Models (Phase 1 / Part 8 / Step 1).

Tests domain models representing:
1. ProjectIdentity
2. ProjectStructure & ProjectDirectory
3. ProjectFile
4. ProjectSourceMaterial
5. ProjectSymbol
6. ProjectConfigurationMetadata
7. ProjectDependencyMetadata
8. ProjectVCSContext
9. ProjectContext (root aggregate container)
10. Path normalization, security bounds, secret masking, and CrawlerReport integration
"""
from __future__ import annotations

import unittest
import uuid

from core.research.contracts.crawler_report import RawSourceReference
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.errors import (
    ProjectSecurityError,
    ProjectValidationError,
    RepositoryValidationError,
)
from core.research.project import (
    ProjectConfigType,
    ProjectConfigurationMetadata,
    ProjectContext,
    ProjectDependencyMetadata,
    ProjectDependencyType,
    ProjectDirectory,
    ProjectFile,
    ProjectIdentity,
    ProjectSourceMaterial,
    ProjectStructure,
    ProjectSymbol,
    ProjectType,
    ProjectVCSContext,
    ProjectVCSState,
    mask_sensitive_config,
    normalize_project_path,
)
from core.research.repo.models import LineRange, compute_sha256
from core.research.repo.structure import SymbolKind
from core.research.types import FactClassification, ResearchConfidence, SourceType


class TestProjectContextModels(unittest.TestCase):
    """Test suite verifying all ProjectContext domain models and contracts."""

    # -------------------------------------------------------------------------
    # 1. Path Normalization & Security Bounds
    # -------------------------------------------------------------------------

    def test_normalize_project_path_valid(self):
        """Valid relative paths are normalized to POSIX format."""
        self.assertEqual(normalize_project_path("src/core/models.py"), "src/core/models.py")
        self.assertEqual(normalize_project_path("/src/core/models.py/"), "src/core/models.py")
        self.assertEqual(normalize_project_path("src\\core\\models.py"), "src/core/models.py")
        self.assertEqual(normalize_project_path("src/core/../core/models.py"), "src/core/models.py")
        self.assertEqual(normalize_project_path(""), "")
        self.assertEqual(normalize_project_path("   "), "")
        self.assertEqual(normalize_project_path("."), "")

    def test_normalize_project_path_security_rejections(self):
        """Directory traversal and hostile injection patterns are rejected."""
        # Root escape
        with self.assertRaises(ProjectSecurityError):
            normalize_project_path("../outside.py")
        with self.assertRaises(ProjectSecurityError):
            normalize_project_path("..")
        with self.assertRaises(ProjectSecurityError):
            normalize_project_path("src/../../outside.py")

        # Null byte injection
        with self.assertRaises(ProjectSecurityError):
            normalize_project_path("src/models.py\x00.evil")

        # Control characters
        with self.assertRaises(ProjectSecurityError):
            normalize_project_path("src/models\n.py")

        # Encoded directory traversal
        with self.assertRaises(ProjectSecurityError):
            normalize_project_path("src/%2e%2e/outside.py")

        # Non-string input
        with self.assertRaises(ProjectValidationError):
            normalize_project_path(None)  # type: ignore
        with self.assertRaises(ProjectValidationError):
            normalize_project_path(12345)  # type: ignore

    # -------------------------------------------------------------------------
    # 2. ProjectIdentity
    # -------------------------------------------------------------------------

    def test_project_identity_valid_and_defaults(self):
        """ProjectIdentity validates fields and extracts name from root."""
        ident = ProjectIdentity(
            project_id="proj-autonomos-01",
            project_root="/workspace/AutonomOS",
            project_type=ProjectType.PYTHON,
            languages=["python", "Python", "typescript"],
            version="1.0.0-beta",
            description="Autonomous operating system workforce",
        )

        self.assertEqual(ident.project_id, "proj-autonomos-01")
        self.assertEqual(ident.name, "AutonomOS")
        self.assertEqual(ident.project_type, ProjectType.PYTHON)
        # Deduplicated lowercase languages
        self.assertEqual(ident.languages, ["python", "typescript"])
        self.assertEqual(ident.version, "1.0.0-beta")

        # Lossless serialization roundtrip
        d = ident.to_dict()
        reconstructed = ProjectIdentity.from_dict(d)
        self.assertEqual(ident.project_id, reconstructed.project_id)
        self.assertEqual(ident.project_root, reconstructed.project_root)
        self.assertEqual(ident.project_type, reconstructed.project_type)
        self.assertEqual(ident.languages, reconstructed.languages)
        self.assertEqual(ident.version, reconstructed.version)

    def test_project_identity_validation_errors(self):
        """Empty ID or root raises ProjectValidationError."""
        with self.assertRaises(ProjectValidationError):
            ProjectIdentity(project_id="", project_root="/workspace")

        with self.assertRaises(ProjectValidationError):
            ProjectIdentity(project_id="p-1", project_root="")

        with self.assertRaises(ProjectValidationError):
            ProjectIdentity.from_dict("invalid_non_dict")  # type: ignore

    # -------------------------------------------------------------------------
    # 3. ProjectFile
    # -------------------------------------------------------------------------

    def test_project_file_metadata_and_detection(self):
        """ProjectFile normalizes paths, detects language, extension, and binary flag."""
        f_py = ProjectFile(
            relative_path="src/research/crawler.py",
            size_bytes=1024,
            line_count=45,
        )
        self.assertEqual(f_py.relative_path, "src/research/crawler.py")
        self.assertEqual(f_py.filename, "crawler.py")
        self.assertEqual(f_py.extension, ".py")
        self.assertEqual(f_py.language, "python")
        self.assertFalse(f_py.is_binary)
        self.assertEqual(f_py.parent_path, "src/research")

        f_bin = ProjectFile(
            relative_path="assets/logo.png",
            size_bytes=40960,
        )
        self.assertEqual(f_bin.extension, ".png")
        self.assertTrue(f_bin.is_binary)

        # Lossless serialization roundtrip
        d = f_py.to_dict()
        reconstructed = ProjectFile.from_dict(d)
        self.assertEqual(f_py.relative_path, reconstructed.relative_path)
        self.assertEqual(f_py.size_bytes, reconstructed.size_bytes)
        self.assertEqual(f_py.line_count, reconstructed.line_count)
        self.assertEqual(f_py.language, reconstructed.language)

    def test_project_file_validation_errors(self):
        """Negative size or line counts raise ProjectValidationError."""
        with self.assertRaises(ProjectValidationError):
            ProjectFile(relative_path="valid.py", size_bytes=-1)

        with self.assertRaises(ProjectValidationError):
            ProjectFile(relative_path="valid.py", line_count=-5)

        with self.assertRaises(ProjectValidationError):
            ProjectFile(relative_path="")

    # -------------------------------------------------------------------------
    # 4. ProjectDirectory & ProjectStructure
    # -------------------------------------------------------------------------

    def test_project_structure_hierarchy_and_queries(self):
        """ProjectStructure tracks directories, files, depth, and child queries."""
        structure = ProjectStructure(root_path="workspace")

        dir_src = ProjectDirectory(path="src", child_dir_paths=["src/core"], child_file_paths=["src/main.py"])
        dir_core = ProjectDirectory(path="src/core", parent_path="src", child_file_paths=["src/core/engine.py"])
        file_main = ProjectFile(relative_path="src/main.py", size_bytes=200)
        file_engine = ProjectFile(relative_path="src/core/engine.py", size_bytes=500)

        structure.add_directory(dir_src)
        structure.add_directory(dir_core)
        structure.add_file(file_main)
        structure.add_file(file_engine)

        self.assertEqual(structure.total_directories, 2)
        self.assertEqual(structure.total_files, 2)
        self.assertGreaterEqual(structure.max_depth_reached, 2)

        # Query lookups
        self.assertIsNotNone(structure.get_file("src/main.py"))
        self.assertIsNotNone(structure.get_file("src\\core\\engine.py"))  # Normalizes backslashes
        self.assertIsNone(structure.get_file("nonexistent.py"))

        self.assertIsNotNone(structure.get_directory("src/core"))
        self.assertIsNone(structure.get_directory("nonexistent_dir"))

        # Query children
        child_dirs, child_files = structure.get_children("src")
        self.assertEqual(len(child_dirs), 1)
        self.assertEqual(child_dirs[0].path, "src/core")
        self.assertEqual(len(child_files), 1)
        self.assertEqual(child_files[0].relative_path, "src/main.py")

        # Lossless serialization roundtrip
        d = structure.to_dict()
        reconstructed = ProjectStructure.from_dict(d)
        self.assertEqual(structure.total_files, reconstructed.total_files)
        self.assertEqual(structure.total_directories, reconstructed.total_directories)
        self.assertEqual(len(reconstructed.files), 2)
        self.assertEqual(len(reconstructed.directories), 2)

    # -------------------------------------------------------------------------
    # 5. ProjectSourceMaterial & CrawlerReport Contracts
    # -------------------------------------------------------------------------

    def test_project_source_material_contracts(self):
        """ProjectSourceMaterial computes hashes and converts to RawSourceReference & EvidenceItem."""
        content = "def calculate_velocity(x, t):\n    return x / t\n"
        mat = ProjectSourceMaterial(
            material_id="mat-test-101",
            file_path="physics/motion.py",
            content=content,
            line_range=LineRange(start_line=1, end_line=2),
        )

        self.assertEqual(mat.language, "python")
        self.assertTrue(len(mat.content_hash) > 0)
        self.assertEqual(mat.content_hash, compute_sha256(content))
        self.assertEqual(mat.source_ref, "project://physics/motion.py#L1-L2")

        # Convert to RawSourceReference
        raw_ref = mat.to_raw_source_reference()
        self.assertIsInstance(raw_ref, RawSourceReference)
        self.assertEqual(raw_ref.source_type, SourceType.PRIMARY_SOURCE)
        self.assertEqual(raw_ref.url_or_ref, mat.source_ref)
        self.assertEqual(raw_ref.checksum, mat.content_hash)
        self.assertIn("calculate_velocity", raw_ref.content_snippet)

        # Convert to EvidenceItem collection
        ev_list = mat.to_evidence_items(
            request_id="req-test-01",
            crawler_task_id="task-test-01",
            crawler_id="crawler.project.test",
            question_id="q-vel",
        )
        self.assertEqual(len(ev_list), 1)
        ev = ev_list[0]
        self.assertIsInstance(ev, EvidenceItem)
        self.assertEqual(ev.classification, FactClassification.FACT)
        self.assertEqual(ev.confidence, ResearchConfidence.WELL_SUPPORTED)
        self.assertEqual(ev.source_type, SourceType.PRIMARY_SOURCE)
        self.assertEqual(ev.checksum, mat.content_hash)
        self.assertEqual(ev.provenance.request_id, "req-test-01")
        self.assertEqual(ev.provenance.crawler_task_id, "task-test-01")
        self.assertEqual(ev.provenance.source_ref, mat.source_ref)

        # Lossless serialization roundtrip
        d = mat.to_dict()
        reconstructed = ProjectSourceMaterial.from_dict(d)
        self.assertEqual(mat.material_id, reconstructed.material_id)
        self.assertEqual(mat.file_path, reconstructed.file_path)
        self.assertEqual(mat.content, reconstructed.content)
        self.assertEqual(mat.content_hash, reconstructed.content_hash)
        self.assertEqual(mat.line_range.start_line, reconstructed.line_range.start_line)

    def test_project_source_material_deterministic_id(self):
        """Material IDs are generated deterministically."""
        id1 = ProjectSourceMaterial.generate_material_id("proj-1", "src/file.py", LineRange(1, 10))
        id2 = ProjectSourceMaterial.generate_material_id("proj-1", "src/file.py", LineRange(1, 10))
        id3 = ProjectSourceMaterial.generate_material_id("proj-1", "src/file.py", LineRange(1, 20))
        self.assertEqual(id1, id2)
        self.assertNotEqual(id1, id3)

    # -------------------------------------------------------------------------
    # 6. ProjectSymbol
    # -------------------------------------------------------------------------

    def test_project_symbol_metadata_and_relationships(self):
        """ProjectSymbol captures name, kind, line bounds, and structural links."""
        sym = ProjectSymbol(
            name="ResearchAgent",
            symbol_type=SymbolKind.CLASS,
            file_path="src/agents/researcher.py",
            line_range=LineRange(start_line=25, end_line=120),
            parent_symbol=None,
            structural_relationships=["extends BaseAgent", "implements IWorker"],
            signature="class ResearchAgent(BaseAgent, IWorker)",
            docstring="Primary autonomous research workforce agent.",
            visibility="public",
        )

        self.assertEqual(sym.name, "ResearchAgent")
        self.assertEqual(sym.symbol_type, SymbolKind.CLASS)
        self.assertEqual(sym.file_path, "src/agents/researcher.py")
        self.assertEqual(sym.line_range.line_count, 96)
        self.assertIn("extends BaseAgent", sym.structural_relationships)

        # Lossless serialization roundtrip
        d = sym.to_dict()
        reconstructed = ProjectSymbol.from_dict(d)
        self.assertEqual(sym.name, reconstructed.name)
        self.assertEqual(sym.symbol_type, reconstructed.symbol_type)
        self.assertEqual(sym.file_path, reconstructed.file_path)
        self.assertEqual(sym.structural_relationships, reconstructed.structural_relationships)

    def test_project_symbol_validation_errors(self):
        """Empty symbol name raises ProjectValidationError."""
        with self.assertRaises(ProjectValidationError):
            ProjectSymbol(name="")

    # -------------------------------------------------------------------------
    # 7. ProjectConfigurationMetadata & Secret Masking
    # -------------------------------------------------------------------------

    def test_project_configuration_metadata_and_secret_masking(self):
        """ProjectConfigurationMetadata masks secrets in safe_metadata."""
        raw_meta = {
            "project_name": "AutonomOS",
            "version": "0.9.1",
            "api_key_secret": "super_secret_token_12345",
            "db_password": "super_secret_password",
            "nested_config": {
                "auth_token": "token_abc_xyz",
                "safe_flag": True,
            },
            "upstream_repo": "https://user:password123@git.example.com/repo.git",
        }

        config = ProjectConfigurationMetadata(
            config_path="config/settings.json",
            config_type=ProjectConfigType.GENERIC,
            safe_metadata=raw_meta,
            content_hash=compute_sha256("settings_json_content"),
        )

        self.assertEqual(config.config_path, "config/settings.json")
        self.assertEqual(config.safe_metadata["project_name"], "AutonomOS")
        self.assertEqual(config.safe_metadata["api_key_secret"], "[REDACTED]")
        self.assertEqual(config.safe_metadata["db_password"], "[REDACTED]")
        self.assertEqual(config.safe_metadata["nested_config"]["auth_token"], "[REDACTED]")
        self.assertEqual(config.safe_metadata["nested_config"]["safe_flag"], True)
        self.assertNotIn("password123", config.safe_metadata["upstream_repo"])
        self.assertIn("[REDACTED]", config.safe_metadata["upstream_repo"])

        # Lossless serialization roundtrip
        d = config.to_dict()
        reconstructed = ProjectConfigurationMetadata.from_dict(d)
        self.assertEqual(config.config_path, reconstructed.config_path)
        self.assertEqual(config.config_type, reconstructed.config_type)
        self.assertEqual(config.safe_metadata, reconstructed.safe_metadata)

    # -------------------------------------------------------------------------
    # 8. ProjectDependencyMetadata
    # -------------------------------------------------------------------------

    def test_project_dependency_metadata(self):
        """ProjectDependencyMetadata validates dependencies and dev scope."""
        dep_runtime = ProjectDependencyMetadata(
            name="pydantic",
            version_spec="^2.6.0",
            manifest_source="pyproject.toml",
            dependency_type=ProjectDependencyType.RUNTIME,
            ecosystem="pypi",
        )
        self.assertEqual(dep_runtime.name, "pydantic")
        self.assertEqual(dep_runtime.version_spec, "^2.6.0")
        self.assertFalse(dep_runtime.is_dev)

        dep_dev = ProjectDependencyMetadata(
            name="pytest",
            version_spec=">=8.0.0",
            manifest_source="pyproject.toml",
            dependency_type=ProjectDependencyType.DEV,
            ecosystem="pypi",
        )
        self.assertTrue(dep_dev.is_dev)

        # Lossless serialization roundtrip
        d = dep_runtime.to_dict()
        reconstructed = ProjectDependencyMetadata.from_dict(d)
        self.assertEqual(dep_runtime.name, reconstructed.name)
        self.assertEqual(dep_runtime.version_spec, reconstructed.version_spec)
        self.assertEqual(dep_runtime.dependency_type, reconstructed.dependency_type)
        self.assertEqual(dep_runtime.is_dev, reconstructed.is_dev)

    def test_project_dependency_validation_errors(self):
        """Empty dependency name raises ProjectValidationError."""
        with self.assertRaises(ProjectValidationError):
            ProjectDependencyMetadata(name="")

    # -------------------------------------------------------------------------
    # 9. ProjectVCSContext
    # -------------------------------------------------------------------------

    def test_project_vcs_context_and_sanitization(self):
        """ProjectVCSContext tracks branch, commit, cleanliness, and strips URL secrets."""
        vcs = ProjectVCSContext(
            branch="feature/project-context",
            revision="a1b2c3d4e5f67890",
            working_tree_state=ProjectVCSState.DIRTY,
            tag="v0.9.0",
            author="Developer <dev@autonomos.ai>",
            remote_url="https://oauth2:ghp_secrettoken123@github.com/SouryaRajput/AutonomOS.git",
        )

        self.assertEqual(vcs.branch, "feature/project-context")
        self.assertEqual(vcs.revision, "a1b2c3d4e5f67890")
        self.assertEqual(vcs.working_tree_state, ProjectVCSState.DIRTY)
        self.assertTrue(vcs.is_dirty)
        self.assertNotIn("ghp_secrettoken123", vcs.remote_url)
        self.assertIn("[REDACTED]", vcs.remote_url)

        # Lossless serialization roundtrip
        d = vcs.to_dict()
        reconstructed = ProjectVCSContext.from_dict(d)
        self.assertEqual(vcs.branch, reconstructed.branch)
        self.assertEqual(vcs.revision, reconstructed.revision)
        self.assertEqual(vcs.working_tree_state, reconstructed.working_tree_state)
        self.assertEqual(vcs.is_dirty, reconstructed.is_dirty)

    # -------------------------------------------------------------------------
    # 10. ProjectContext Root Aggregate
    # -------------------------------------------------------------------------

    def test_project_context_aggregate_and_crawler_report_conversion(self):
        """ProjectContext aggregates all project facets and converts into CrawlerReport artifacts."""
        identity = ProjectIdentity(
            project_id="proj-e2e-01",
            project_root="/workspace/AutonomOS",
            project_type=ProjectType.PYTHON,
            languages=["python"],
        )
        structure = ProjectStructure(root_path=".")
        file1 = ProjectFile(relative_path="core/engine.py", size_bytes=500, line_count=20)
        structure.add_file(file1)

        mat = ProjectSourceMaterial(
            material_id="mat-e2e-01",
            file_path="core/engine.py",
            content="class Engine:\n    pass\n",
            line_range=LineRange(1, 2),
        )

        sym = ProjectSymbol(
            name="Engine",
            symbol_type=SymbolKind.CLASS,
            file_path="core/engine.py",
            line_range=LineRange(1, 2),
        )

        cfg = ProjectConfigurationMetadata(
            config_path="pyproject.toml",
            config_type=ProjectConfigType.PYTHON_PYPROJECT,
            safe_metadata={"tool": {"poetry": {"name": "autonomos"}}},
        )

        dep = ProjectDependencyMetadata(
            name="pydantic",
            version_spec="^2.6.0",
            manifest_source="pyproject.toml",
        )

        vcs = ProjectVCSContext(
            branch="main",
            revision="11223344",
            working_tree_state=ProjectVCSState.CLEAN,
        )

        ctx = ProjectContext(
            identity=identity,
            structure=structure,
            source_materials=[mat],
            symbols=[sym],
            configurations=[cfg],
            dependencies=[dep],
            vcs=vcs,
        )

        # Invariant checks
        self.assertEqual(len(ctx.source_materials), 1)
        self.assertEqual(len(ctx.symbols), 1)
        self.assertEqual(len(ctx.configurations), 1)
        self.assertEqual(len(ctx.dependencies), 1)
        self.assertEqual(ctx.vcs.branch, "main")

        # Convert to CrawlerReport raw sources
        raw_sources = ctx.to_raw_source_references()
        self.assertEqual(len(raw_sources), 1)
        self.assertEqual(raw_sources[0].source_type, SourceType.PRIMARY_SOURCE)
        self.assertEqual(raw_sources[0].url_or_ref, "project://core/engine.py#L1-L2")

        # Convert to EvidenceItem collection
        evidence_items = ctx.to_evidence_items(
            request_id="req-e2e",
            crawler_task_id="task-e2e",
            crawler_id="crawler.project",
        )
        self.assertEqual(len(evidence_items), 1)
        self.assertEqual(evidence_items[0].source_type, SourceType.PRIMARY_SOURCE)
        self.assertEqual(evidence_items[0].classification, FactClassification.FACT)

        # Lossless serialization roundtrip
        d = ctx.to_dict()
        reconstructed = ProjectContext.from_dict(d)
        self.assertEqual(ctx.identity.project_id, reconstructed.identity.project_id)
        self.assertEqual(len(reconstructed.source_materials), 1)
        self.assertEqual(len(reconstructed.symbols), 1)
        self.assertEqual(len(reconstructed.configurations), 1)
        self.assertEqual(len(reconstructed.dependencies), 1)
        self.assertEqual(reconstructed.vcs.branch, "main")

    # -------------------------------------------------------------------------
    # 11. Enums and Fallback Parsing
    # -------------------------------------------------------------------------

    def test_enums_and_fallback_parsing(self):
        """String parsing with case-insensitivity and safe unknown fallback."""
        self.assertEqual(ProjectType.from_string("python"), ProjectType.PYTHON)
        self.assertEqual(ProjectType.from_string("TypeScript"), ProjectType.TYPESCRIPT)
        self.assertEqual(ProjectType.from_string("unknown_lang"), ProjectType.UNKNOWN)
        self.assertEqual(ProjectType.from_string(12345), ProjectType.UNKNOWN)  # type: ignore

        self.assertEqual(ProjectConfigType.from_string("npm_package"), ProjectConfigType.NPM_PACKAGE)
        self.assertEqual(ProjectConfigType.from_string("unknown_config"), ProjectConfigType.GENERIC)

        self.assertEqual(ProjectDependencyType.from_string("dev"), ProjectDependencyType.DEV)
        self.assertEqual(ProjectDependencyType.from_string("unknown_dep"), ProjectDependencyType.RUNTIME)

        self.assertEqual(ProjectVCSState.from_string("clean"), ProjectVCSState.CLEAN)
        self.assertEqual(ProjectVCSState.from_string("dirty"), ProjectVCSState.DIRTY)
        self.assertEqual(ProjectVCSState.from_string("unknown_state"), ProjectVCSState.UNKNOWN)

    # -------------------------------------------------------------------------
    # 12. Invalid LineRange Bounds (Reused from Repo)
    # -------------------------------------------------------------------------

    def test_line_range_bounds_validation(self):
        """LineRange enforces 1-indexed, positive, start <= end bounds."""
        with self.assertRaises(RepositoryValidationError):
            LineRange(start_line=0, end_line=10)

        with self.assertRaises(RepositoryValidationError):
            LineRange(start_line=10, end_line=5)


if __name__ == "__main__":
    unittest.main()

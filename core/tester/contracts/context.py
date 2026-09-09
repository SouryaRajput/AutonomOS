from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from core.tester.contracts.identifiers import (
    new_execution_id,
    validate_execution_id,
    validate_work_order_id,
)
from core.tester.errors import TesterLineageError, TesterValidationError
from core.tester.types import (
    ChangeCategory,
    FactStatus,
    PresenceStatus,
    TestSurface,
)

logger = logging.getLogger("AutonomOS.TestContext")


def utc_now() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PresenceAssessment:
    """
    Epistemic assessment of an architectural layer or component presence.
    Strictly distinguishes directly verified facts from inferred or unknown data.
    """
    __test__ = False
    status: PresenceStatus = PresenceStatus.UNKNOWN
    confidence: FactStatus = FactStatus.UNKNOWN
    source: Optional[str] = None
    rationale: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = PresenceStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = PresenceStatus.UNKNOWN
        if isinstance(self.confidence, str):
            try:
                self.confidence = FactStatus(self.confidence.upper())
            except (ValueError, KeyError):
                self.confidence = FactStatus.UNKNOWN

    @property
    def is_present(self) -> bool:
        return self.status == PresenceStatus.PRESENT

    @property
    def is_absent(self) -> bool:
        return self.status == PresenceStatus.ABSENT

    @property
    def is_unknown(self) -> bool:
        return self.status == PresenceStatus.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "confidence": self.confidence.value,
            "source": self.source,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PresenceAssessment:
        return cls(
            status=PresenceStatus(data.get("status", PresenceStatus.UNKNOWN.value)),
            confidence=FactStatus(data.get("confidence", FactStatus.UNKNOWN.value)),
            source=data.get("source"),
            rationale=data.get("rationale"),
        )


@dataclass
class SurfaceAssessment:
    """
    Assessment of an individual testable surface of the product under test.
    Records existence/presence without deciding execution applicability in Phase 3.1.
    """
    __test__ = False
    surface: TestSurface
    status: PresenceStatus = PresenceStatus.UNKNOWN
    confidence: FactStatus = FactStatus.UNKNOWN
    source: Optional[str] = None
    rationale: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.surface, str):
            try:
                self.surface = TestSurface(self.surface.upper())
            except (ValueError, KeyError):
                raise TesterValidationError(f"Invalid test surface: {self.surface}", field_name="surface")
        if isinstance(self.status, str):
            try:
                self.status = PresenceStatus(self.status.upper())
            except (ValueError, KeyError):
                self.status = PresenceStatus.UNKNOWN
        if isinstance(self.confidence, str):
            try:
                self.confidence = FactStatus(self.confidence.upper())
            except (ValueError, KeyError):
                self.confidence = FactStatus.UNKNOWN

    @property
    def is_available(self) -> bool:
        return self.status == PresenceStatus.PRESENT

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface.value,
            "status": self.status.value,
            "confidence": self.confidence.value,
            "source": self.source,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SurfaceAssessment:
        return cls(
            surface=TestSurface(data.get("surface", TestSurface.UI.value)),
            status=PresenceStatus(data.get("status", PresenceStatus.UNKNOWN.value)),
            confidence=FactStatus(data.get("confidence", FactStatus.UNKNOWN.value)),
            source=data.get("source"),
            rationale=data.get("rationale"),
        )


@dataclass
class TestContext:
    """
    Deterministic domain model capturing the factual test context for a Tester execution.
    
    Core Invariants:
    1. Understand -> Classify -> Plan -> Execute: Establishes what exists and changed before planning.
    2. Strict No-Execution Invariant: Assembling context performs zero browser actions, interactions, or testing.
    3. Epistemic Rigor: Clearly distinguishes FACT from INFERENCE and UNKNOWN. Inferences are never represented as facts.
    4. Immutable Lineage: Preserves ManagerTask -> TesterWorkOrder -> TesterExecution causal chain.
    5. Provenance Integrity: Records sources for architectural presence and changes.
    """
    __test__ = False
    project_id: str
    work_order_id: str
    execution_id: str
    source_revision: Optional[str] = None
    product_artifact_id: Optional[str] = None
    project_type: Optional[str] = None

    # Architectural Presence
    frontend: PresenceAssessment = field(default_factory=PresenceAssessment)
    backend: PresenceAssessment = field(default_factory=PresenceAssessment)
    api: PresenceAssessment = field(default_factory=PresenceAssessment)
    database: PresenceAssessment = field(default_factory=PresenceAssessment)

    # Change Tracking
    changed_files: list[str] = field(default_factory=list)
    changed_modules: list[str] = field(default_factory=list)
    changed_components: list[str] = field(default_factory=list)
    changed_routes: list[str] = field(default_factory=list)
    changed_apis: list[str] = field(default_factory=list)
    changed_configuration: list[str] = field(default_factory=list)
    change_categories: list[ChangeCategory] = field(default_factory=list)

    # Test Surfaces
    test_surfaces: list[SurfaceAssessment] = field(default_factory=list)
    existing_tests: list[str] = field(default_factory=list)
    runtime_environment: Optional[str] = None

    # Constraints, Dependencies & Risks
    constraints: list[str] = field(default_factory=list)
    known_dependencies: list[str] = field(default_factory=list)
    known_risks: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)

    # Provenance & Trace
    provenance: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate identity integrity and non-empty lineage."""
        validate_work_order_id(self.work_order_id)
        validate_execution_id(self.execution_id)
        if not self.project_id or not self.project_id.strip():
            raise TesterLineageError("TestContext requires a valid non-empty project_id.")

    @property
    def has_frontend_changes(self) -> bool:
        return ChangeCategory.FRONTEND in self.change_categories

    @property
    def has_backend_changes(self) -> bool:
        return ChangeCategory.BACKEND in self.change_categories

    @property
    def has_api_changes(self) -> bool:
        return ChangeCategory.API in self.change_categories

    @property
    def has_database_changes(self) -> bool:
        return ChangeCategory.DATABASE in self.change_categories

    @property
    def has_configuration_changes(self) -> bool:
        return ChangeCategory.CONFIGURATION in self.change_categories

    @property
    def is_test_only(self) -> bool:
        return ChangeCategory.TEST_ONLY in self.change_categories and len(self.change_categories) == 1

    def get_surface_assessment(self, surface: TestSurface) -> Optional[SurfaceAssessment]:
        """Retrieve assessment for a specific test surface."""
        for s in self.test_surfaces:
            if s.surface == surface:
                return s
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "work_order_id": self.work_order_id,
            "execution_id": self.execution_id,
            "source_revision": self.source_revision,
            "product_artifact_id": self.product_artifact_id,
            "project_type": self.project_type,
            "frontend": self.frontend.to_dict(),
            "backend": self.backend.to_dict(),
            "api": self.api.to_dict(),
            "database": self.database.to_dict(),
            "changed_files": list(self.changed_files),
            "changed_modules": list(self.changed_modules),
            "changed_components": list(self.changed_components),
            "changed_routes": list(self.changed_routes),
            "changed_apis": list(self.changed_apis),
            "changed_configuration": list(self.changed_configuration),
            "change_categories": [c.value for c in self.change_categories],
            "test_surfaces": [s.to_dict() for s in self.test_surfaces],
            "existing_tests": list(self.existing_tests),
            "runtime_environment": self.runtime_environment,
            "constraints": list(self.constraints),
            "known_dependencies": list(self.known_dependencies),
            "known_risks": list(self.known_risks),
            "unknowns": list(self.unknowns),
            "provenance": dict(self.provenance),
            "trace": dict(self.trace),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestContext:
        cats = []
        for c in data.get("change_categories", []):
            try:
                cats.append(ChangeCategory(c.upper()))
            except (ValueError, KeyError):
                cats.append(ChangeCategory.UNKNOWN)

        surfaces = [SurfaceAssessment.from_dict(s) for s in data.get("test_surfaces", [])]

        return cls(
            project_id=str(data.get("project_id", "")),
            work_order_id=str(data.get("work_order_id", "")),
            execution_id=str(data.get("execution_id", "")),
            source_revision=data.get("source_revision"),
            product_artifact_id=data.get("product_artifact_id"),
            project_type=data.get("project_type"),
            frontend=PresenceAssessment.from_dict(data.get("frontend", {})),
            backend=PresenceAssessment.from_dict(data.get("backend", {})),
            api=PresenceAssessment.from_dict(data.get("api", {})),
            database=PresenceAssessment.from_dict(data.get("database", {})),
            changed_files=list(data.get("changed_files", [])),
            changed_modules=list(data.get("changed_modules", [])),
            changed_components=list(data.get("changed_components", [])),
            changed_routes=list(data.get("changed_routes", [])),
            changed_apis=list(data.get("changed_apis", [])),
            changed_configuration=list(data.get("changed_configuration", [])),
            change_categories=cats,
            test_surfaces=surfaces,
            existing_tests=list(data.get("existing_tests", [])),
            runtime_environment=data.get("runtime_environment"),
            constraints=list(data.get("constraints", [])),
            known_dependencies=list(data.get("known_dependencies", [])),
            known_risks=list(data.get("known_risks", [])),
            unknowns=list(data.get("unknowns", [])),
            provenance=dict(data.get("provenance", {})),
            trace=dict(data.get("trace", {})),
            created_at=str(data.get("created_at", utc_now())),
        )


class TestContextBuilder:
    """
    Deterministic context builder for Tester V1 Phase 3.1.
    
    Assembles factual test context from available inputs without launching browsers,
    performing actions, running tests, or mutating product/source code.
    """
    __test__ = False

    # Path pattern matching sets
    FRONTEND_DIR_PATTERNS = {"frontend", "client", "ui", "web", "app", "static", "templates", "styles", "components", "views"}
    FRONTEND_EXTENSIONS = {".html", ".htm", ".css", ".scss", ".sass", ".less", ".jsx", ".tsx", ".vue", ".svelte"}

    BACKEND_DIR_PATTERNS = {"backend", "server", "services", "controllers", "handlers", "core", "domain", "pkg", "internal"}
    BACKEND_EXTENSIONS = {".py", ".go", ".rs", ".java", ".rb", ".php", ".cs", ".scala", ".kt"}

    API_DIR_PATTERNS = {"api", "apis", "endpoints", "routes", "controllers", "handlers", "openapi", "swagger", "graphql"}
    API_EXTENSIONS = {".openapi.json", ".openapi.yaml", ".graphql"}

    DATABASE_DIR_PATTERNS = {"db", "database", "migrations", "alembic", "models", "entities", "prisma"}
    DATABASE_EXTENSIONS = {".sql", ".prisma"}

    CONFIG_NAMES = {
        "config.yaml", "config.yml", "config.json", "settings.py", "settings.json",
        "application.yml", "application.yaml", "application.properties",
    }
    CONFIG_PREFIXES = {".env"}

    BUILD_NAMES = {
        "makefile", "dockerfile", "docker-compose.yml", "docker-compose.yaml",
        "webpack.config.js", "vite.config.ts", "vite.config.js", "tsconfig.json",
        "rollup.config.js", "babel.config.json",
    }

    DEPENDENCY_NAMES = {
        "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
        "requirements.txt", "pipfile", "pipfile.lock", "poetry.lock", "pyproject.toml",
        "cargo.toml", "cargo.lock", "go.mod", "go.sum", "pom.xml", "build.gradle",
    }

    DOC_DIR_PATTERNS = {"docs", "doc"}
    DOC_EXTENSIONS = {".md", ".rst", ".txt"}

    TEST_DIR_PATTERNS = {"test", "tests", "spec", "specs"}

    def build(
        self,
        work_order: Any,
        execution: Optional[Any] = None,
        programmer_result: Optional[Any] = None,
        product_artifact: Optional[Any] = None,
        change_set: Optional[Any] = None,
        project_context: Optional[dict[str, Any]] = None,
        project_memory: Optional[dict[str, Any]] = None,
        environment: Optional[Any] = None,
        known_test_metadata: Optional[dict[str, Any]] = None,
        files_manifest: Optional[Sequence[str]] = None,
        target_routes: Optional[Sequence[str]] = None,
        target_components: Optional[Sequence[str]] = None,
        **kwargs: Any,
    ) -> TestContext:
        """
        Build an authoritative, deterministic TestContext from available inputs.
        """
        # 1. Lineage & Identity Extraction
        proj_id = getattr(work_order, "project_id", "")
        wo_id = getattr(work_order, "work_order_id", "")
        exec_id = getattr(execution, "execution_id", "") if execution else ""
        if not exec_id and hasattr(work_order, "execution_id"):
            exec_id = getattr(work_order, "execution_id", "")
        if not exec_id:
            exec_id = new_execution_id()

        # Verify lineage alignment if both work_order and execution are provided
        if execution is not None:
            if hasattr(execution, "validate_lineage"):
                execution.validate_lineage(work_order=work_order)
            elif execution.project_id != proj_id or execution.work_order_id != wo_id:
                raise TesterLineageError(
                    f"Lineage mismatch between execution (proj={execution.project_id}, wo={execution.work_order_id}) "
                    f"and work_order (proj={proj_id}, wo={wo_id})."
                )

        # 2. Source Revision & Artifact ID
        source_rev: Optional[str] = None
        artifact_id: Optional[str] = None
        artifact_meta: dict[str, Any] = {}
        unknowns: list[str] = []
        provenance: dict[str, Any] = {}

        # Revision extraction
        if hasattr(work_order, "source_revision") and work_order.source_revision:
            source_rev = str(work_order.source_revision)
            provenance["source_revision"] = "work_order.source_revision"
        elif programmer_result and hasattr(programmer_result, "metadata"):
            rev_val = programmer_result.metadata.get("git_revision") or programmer_result.metadata.get("source_revision")
            if rev_val:
                source_rev = str(rev_val)
                provenance["source_revision"] = "programmer_result.metadata"

        if not source_rev and change_set and hasattr(change_set, "resulting_revision"):
            rr = getattr(change_set, "resulting_revision", None)
            if rr:
                source_rev = getattr(rr, "commit_hash", str(rr))
                provenance["source_revision"] = "change_set.resulting_revision"

        if not source_rev:
            unknowns.append("source_revision is unspecified")

        # Product Artifact extraction
        if product_artifact is not None:
            if isinstance(product_artifact, str):
                artifact_id = product_artifact
                provenance["product_artifact"] = "work_order.product_artifact"
            elif hasattr(product_artifact, "artifact_id"):
                artifact_id = str(product_artifact.artifact_id)
                artifact_meta = getattr(product_artifact, "build_metadata", {}) or {}
                provenance["product_artifact"] = "product_artifact.artifact_id"
            elif isinstance(product_artifact, dict):
                artifact_id = str(product_artifact.get("artifact_id", product_artifact.get("id", "")))
                artifact_meta = product_artifact.get("build_metadata", {}) or {}
                provenance["product_artifact"] = "product_artifact_dict"
        elif hasattr(work_order, "product_artifact") and work_order.product_artifact:
            artifact_id = str(work_order.product_artifact)
            provenance["product_artifact"] = "work_order.product_artifact"
        else:
            unknowns.append("product_artifact is unspecified")

        # 3. Change Set & File Extraction
        changed_files_set: set[str] = set()

        if files_manifest:
            changed_files_set.update(files_manifest)
            provenance["changed_files"] = "files_manifest"
        if programmer_result is not None:
            fc = getattr(programmer_result, "files_changed", []) or []
            fcr = getattr(programmer_result, "files_created", []) or []
            fd = getattr(programmer_result, "files_deleted", []) or []
            changed_files_set.update(fc)
            changed_files_set.update(fcr)
            changed_files_set.update(fd)
            if not provenance.get("changed_files"):
                provenance["changed_files"] = "programmer_result"
        elif change_set is not None:
            fc = getattr(change_set, "files_changed", []) or []
            fcr = getattr(change_set, "files_created", []) or []
            fd = getattr(change_set, "files_deleted", []) or []
            changed_files_set.update(fc)
            changed_files_set.update(fcr)
            changed_files_set.update(fd)
            if not provenance.get("changed_files"):
                provenance["changed_files"] = "change_set"
        elif hasattr(work_order, "metadata") and work_order.metadata:
            wo_files = (
                work_order.metadata.get("changed_files")
                or work_order.metadata.get("files_changed")
                or work_order.metadata.get("files", [])
            )
            if isinstance(wo_files, (list, set, tuple)):
                changed_files_set.update(wo_files)
                if not provenance.get("changed_files"):
                    provenance["changed_files"] = "work_order.metadata"

        changed_files = sorted(list(changed_files_set))
        if not changed_files and programmer_result is None and change_set is None and not files_manifest:
            unknowns.append("changed_files could not be determined from inputs")

        # 4. Deterministic Change Classification
        change_categories: list[ChangeCategory] = []
        changed_modules: set[str] = set()
        changed_components: set[str] = set()
        changed_routes: set[str] = set()
        changed_apis: set[str] = set()
        changed_config: set[str] = set()

        # Check explicit metadata overrides if provided by ProgrammerResult
        if programmer_result and hasattr(programmer_result, "metadata") and "change_categories" in programmer_result.metadata:
            raw_cats = programmer_result.metadata["change_categories"]
            for rc in raw_cats:
                try:
                    cat_val = ChangeCategory(str(rc).upper())
                    if cat_val not in change_categories:
                        change_categories.append(cat_val)
                except (ValueError, KeyError):
                    pass

        # Classify by inspecting changed files
        is_frontend_detected = False
        is_backend_detected = False
        is_api_detected = False
        is_db_detected = False
        is_config_detected = False
        is_build_detected = False
        is_dep_detected = False
        is_doc_detected = False
        is_infra_detected = False
        all_test_files = True if changed_files else False

        for fpath in changed_files:
            p = Path(fpath)
            parts_lower = [part.lower() for part in p.parts]
            fname_lower = p.name.lower()
            ext_lower = p.suffix.lower()

            is_file_test = self._is_test_file(parts_lower, fname_lower)
            if not is_file_test:
                all_test_files = False

            # Frontend
            if any(d in self.FRONTEND_DIR_PATTERNS for d in parts_lower) or ext_lower in self.FRONTEND_EXTENSIONS:
                is_frontend_detected = True
                if "components" in parts_lower or "views" in parts_lower:
                    changed_components.add(p.stem)

            # Backend
            if any(d in self.BACKEND_DIR_PATTERNS for d in parts_lower) or (ext_lower in self.BACKEND_EXTENSIONS and not is_file_test):
                is_backend_detected = True
                if len(p.parts) > 1:
                    changed_modules.add(p.parts[0])

            # API
            if any(d in self.API_DIR_PATTERNS for d in parts_lower) or ext_lower in self.API_EXTENSIONS or "api" in fname_lower:
                is_api_detected = True
                is_backend_detected = True
                changed_apis.add(p.stem)

            # Database
            if any(d in self.DATABASE_DIR_PATTERNS for d in parts_lower) or ext_lower in self.DATABASE_EXTENSIONS:
                is_db_detected = True

            # Configuration
            if fname_lower in self.CONFIG_NAMES or any(fname_lower.startswith(pfx) for pfx in self.CONFIG_PREFIXES):
                is_config_detected = True
                changed_config.add(p.name)

            # Build
            if fname_lower in self.BUILD_NAMES:
                is_build_detected = True

            # Dependencies
            if fname_lower in self.DEPENDENCY_NAMES:
                is_dep_detected = True
                is_build_detected = True

            # Documentation
            if any(d in self.DOC_DIR_PATTERNS for d in parts_lower) or (ext_lower in self.DOC_EXTENSIONS and any(fname_lower.startswith(prefix) for prefix in ("readme", "changelog", "license"))):
                is_doc_detected = True

            # Infrastructure
            if ".github" in parts_lower or "k8s" in parts_lower or "terraform" in parts_lower or "helm" in parts_lower:
                is_infra_detected = True

        # Append classified categories
        if all_test_files and changed_files:
            if ChangeCategory.TEST_ONLY not in change_categories:
                change_categories.append(ChangeCategory.TEST_ONLY)
        else:
            if is_frontend_detected and ChangeCategory.FRONTEND not in change_categories:
                change_categories.append(ChangeCategory.FRONTEND)
            if is_api_detected and ChangeCategory.API not in change_categories:
                change_categories.append(ChangeCategory.API)
            if is_backend_detected and ChangeCategory.BACKEND not in change_categories:
                change_categories.append(ChangeCategory.BACKEND)
            if is_db_detected and ChangeCategory.DATABASE not in change_categories:
                change_categories.append(ChangeCategory.DATABASE)
            if is_config_detected and ChangeCategory.CONFIGURATION not in change_categories:
                change_categories.append(ChangeCategory.CONFIGURATION)
            if is_build_detected and ChangeCategory.BUILD not in change_categories:
                change_categories.append(ChangeCategory.BUILD)
            if is_dep_detected and ChangeCategory.DEPENDENCY not in change_categories:
                change_categories.append(ChangeCategory.DEPENDENCY)
            if is_infra_detected and ChangeCategory.INFRASTRUCTURE not in change_categories:
                change_categories.append(ChangeCategory.INFRASTRUCTURE)
            if is_doc_detected and ChangeCategory.DOCUMENTATION not in change_categories:
                change_categories.append(ChangeCategory.DOCUMENTATION)

        if not change_categories:
            change_categories.append(ChangeCategory.UNKNOWN)

        # 5. Extract Routes from Scope or Environment
        if hasattr(work_order, "test_scope") and work_order.test_scope:
            routes = getattr(work_order.test_scope, "routes", []) or []
            changed_routes.update(routes)
        if target_routes:
            changed_routes.update(target_routes)
        if target_components:
            changed_components.update(target_components)

        # 6. Epistemic Architectural Presence Assessment
        frontend_pres = self._assess_frontend_presence(work_order, environment, is_frontend_detected, changed_files)
        backend_pres = self._assess_backend_presence(work_order, artifact_meta, is_backend_detected, changed_files)
        api_pres = self._assess_api_presence(work_order, artifact_meta, is_api_detected, changed_files)
        db_pres = self._assess_database_presence(work_order, artifact_meta, is_db_detected, changed_files)

        # 7. Test Surfaces Identification
        test_surfaces = self._identify_test_surfaces(
            frontend_pres=frontend_pres,
            backend_pres=backend_pres,
            api_pres=api_pres,
            db_pres=db_pres,
            work_order=work_order,
            change_categories=change_categories,
        )

        # 8. Constraints, Dependencies & Risks
        constraints: list[str] = []
        known_deps: list[str] = []
        known_risks: list[str] = []

        if hasattr(work_order, "constraints") and work_order.constraints:
            constraints.extend(work_order.constraints)

        if hasattr(work_order, "time_budget") and work_order.time_budget:
            constraints.append(f"time_budget={work_order.time_budget}s")

        if programmer_result and hasattr(programmer_result, "risks") and programmer_result.risks:
            known_risks.extend(programmer_result.risks)

        # Environment reference
        runtime_env: Optional[str] = None
        if environment:
            runtime_env = getattr(environment, "environment_type", None)
            if hasattr(runtime_env, "value"):
                runtime_env = runtime_env.value
        elif hasattr(work_order, "test_environment") and work_order.test_environment:
            runtime_env = str(work_order.test_environment)

        # Existing tests extraction
        existing_tests: list[str] = []
        if known_test_metadata and "tests" in known_test_metadata:
            existing_tests.extend(known_test_metadata["tests"])
        elif programmer_result and hasattr(programmer_result, "test_results"):
            for tr in getattr(programmer_result, "test_results", []):
                t_name = getattr(tr, "test_name", str(tr))
                if t_name:
                    existing_tests.append(t_name)

        return TestContext(
            project_id=proj_id,
            work_order_id=wo_id,
            execution_id=exec_id,
            source_revision=source_rev,
            product_artifact_id=artifact_id,
            project_type=self._determine_project_type(frontend_pres, backend_pres, api_pres),
            frontend=frontend_pres,
            backend=backend_pres,
            api=api_pres,
            database=db_pres,
            changed_files=changed_files,
            changed_modules=sorted(list(changed_modules)),
            changed_components=sorted(list(changed_components)),
            changed_routes=sorted(list(changed_routes)),
            changed_apis=sorted(list(changed_apis)),
            changed_configuration=sorted(list(changed_config)),
            change_categories=change_categories,
            test_surfaces=test_surfaces,
            existing_tests=sorted(list(set(existing_tests))),
            runtime_environment=runtime_env,
            constraints=sorted(list(set(constraints))),
            known_dependencies=sorted(list(set(known_deps))),
            known_risks=sorted(list(set(known_risks))),
            unknowns=sorted(list(set(unknowns))),
            provenance=provenance,
            trace={
                "builder": "TestContextBuilder",
                "version": "1.0.0",
                "phase": "3.1",
            },
            created_at=utc_now(),
        )

    def _is_test_file(self, parts_lower: list[str], fname_lower: str) -> bool:
        """Deterministic test file pattern match."""
        if any(d in self.TEST_DIR_PATTERNS for d in parts_lower):
            return True
        if fname_lower.startswith("test_") or fname_lower.endswith("_test.py"):
            return True
        if ".spec." in fname_lower or ".test." in fname_lower:
            return True
        return False

    def _assess_frontend_presence(
        self,
        work_order: Any,
        environment: Any,
        is_detected: bool,
        changed_files: list[str],
    ) -> PresenceAssessment:
        # 1. Direct Factual Evidence from WorkOrder routes or environment
        if hasattr(work_order, "test_scope") and getattr(work_order.test_scope, "routes", None):
            return PresenceAssessment(
                status=PresenceStatus.PRESENT,
                confidence=FactStatus.FACT,
                source="work_order.test_scope.routes",
                rationale="Authorized routes specified in work order scope.",
            )

        if environment and getattr(environment, "environment_type", None) == "BROWSER":
            return PresenceAssessment(
                status=PresenceStatus.PRESENT,
                confidence=FactStatus.FACT,
                source="environment.environment_type",
                rationale="Browser test environment configured.",
            )

        # 2. Inferred from changed files
        if is_detected:
            return PresenceAssessment(
                status=PresenceStatus.PRESENT,
                confidence=FactStatus.INFERENCE,
                source="changed_files",
                rationale="Frontend source files or web templates detected in changes.",
            )

        # 3. Inferred absent if changed files exist but none are frontend
        if changed_files and not is_detected:
            return PresenceAssessment(
                status=PresenceStatus.ABSENT,
                confidence=FactStatus.INFERENCE,
                source="changed_files",
                rationale="No frontend files detected among modified changes.",
            )

        # 4. Unknown
        return PresenceAssessment(
            status=PresenceStatus.UNKNOWN,
            confidence=FactStatus.UNKNOWN,
            source=None,
            rationale="Insufficient information to determine frontend presence.",
        )

    def _assess_backend_presence(
        self,
        work_order: Any,
        artifact_meta: dict[str, Any],
        is_detected: bool,
        changed_files: list[str],
    ) -> PresenceAssessment:
        if artifact_meta.get("backend") is True or artifact_meta.get("has_backend") is True:
            return PresenceAssessment(
                status=PresenceStatus.PRESENT,
                confidence=FactStatus.FACT,
                source="product_artifact.build_metadata",
                rationale="Product artifact metadata specifies backend presence.",
            )

        if is_detected:
            return PresenceAssessment(
                status=PresenceStatus.PRESENT,
                confidence=FactStatus.INFERENCE,
                source="changed_files",
                rationale="Backend source files or server modules detected in changes.",
            )

        if changed_files and not is_detected:
            return PresenceAssessment(
                status=PresenceStatus.ABSENT,
                confidence=FactStatus.INFERENCE,
                source="changed_files",
                rationale="No backend files detected among modified changes.",
            )

        return PresenceAssessment(
            status=PresenceStatus.UNKNOWN,
            confidence=FactStatus.UNKNOWN,
            source=None,
            rationale="Insufficient information to determine backend presence.",
        )

    def _assess_api_presence(
        self,
        work_order: Any,
        artifact_meta: dict[str, Any],
        is_detected: bool,
        changed_files: list[str],
    ) -> PresenceAssessment:
        if hasattr(work_order, "test_scope") and getattr(work_order.test_scope, "routes", None):
            routes = work_order.test_scope.routes
            if any(r.startswith("/api") or r.startswith("/v1") or r.startswith("/v2") for r in routes):
                return PresenceAssessment(
                    status=PresenceStatus.PRESENT,
                    confidence=FactStatus.FACT,
                    source="work_order.test_scope.routes",
                    rationale="API routes explicitly declared in work order scope.",
                )

        if is_detected:
            return PresenceAssessment(
                status=PresenceStatus.PRESENT,
                confidence=FactStatus.INFERENCE,
                source="changed_files",
                rationale="API endpoint definitions or schema files detected in changes.",
            )

        if changed_files and not is_detected:
            return PresenceAssessment(
                status=PresenceStatus.ABSENT,
                confidence=FactStatus.INFERENCE,
                source="changed_files",
                rationale="No API definitions detected among modified changes.",
            )

        return PresenceAssessment(
            status=PresenceStatus.UNKNOWN,
            confidence=FactStatus.UNKNOWN,
            source=None,
            rationale="Insufficient information to determine API presence.",
        )

    def _assess_database_presence(
        self,
        work_order: Any,
        artifact_meta: dict[str, Any],
        is_detected: bool,
        changed_files: list[str],
    ) -> PresenceAssessment:
        if artifact_meta.get("database") is True or artifact_meta.get("has_database") is True:
            return PresenceAssessment(
                status=PresenceStatus.PRESENT,
                confidence=FactStatus.FACT,
                source="product_artifact.build_metadata",
                rationale="Product artifact metadata specifies database presence.",
            )

        if is_detected:
            return PresenceAssessment(
                status=PresenceStatus.PRESENT,
                confidence=FactStatus.INFERENCE,
                source="changed_files",
                rationale="Database migration files, queries, or ORM schemas detected in changes.",
            )

        if changed_files and not is_detected:
            return PresenceAssessment(
                status=PresenceStatus.ABSENT,
                confidence=FactStatus.INFERENCE,
                source="changed_files",
                rationale="No database files detected among modified changes.",
            )

        return PresenceAssessment(
            status=PresenceStatus.UNKNOWN,
            confidence=FactStatus.UNKNOWN,
            source=None,
            rationale="Insufficient information to determine database presence.",
        )

    def _identify_test_surfaces(
        self,
        frontend_pres: PresenceAssessment,
        backend_pres: PresenceAssessment,
        api_pres: PresenceAssessment,
        db_pres: PresenceAssessment,
        work_order: Any,
        change_categories: list[ChangeCategory],
    ) -> list[SurfaceAssessment]:
        """
        Record which test surfaces appear to exist based on layer presence and WorkOrder scopes.
        Does NOT decide execution applicability in Phase 3.1.
        """
        surfaces: list[SurfaceAssessment] = []

        # UI & Navigation & Interaction
        if frontend_pres.is_present:
            surfaces.append(SurfaceAssessment(
                surface=TestSurface.UI,
                status=PresenceStatus.PRESENT,
                confidence=frontend_pres.confidence,
                source=frontend_pres.source,
                rationale="Frontend layer is present.",
            ))
            surfaces.append(SurfaceAssessment(
                surface=TestSurface.NAVIGATION,
                status=PresenceStatus.PRESENT,
                confidence=frontend_pres.confidence,
                source=frontend_pres.source,
                rationale="Navigation routes supported by frontend.",
            ))
            surfaces.append(SurfaceAssessment(
                surface=TestSurface.USER_INTERACTION,
                status=PresenceStatus.PRESENT,
                confidence=frontend_pres.confidence,
                source=frontend_pres.source,
                rationale="User interaction controls present in UI.",
            ))
        elif frontend_pres.is_absent:
            surfaces.append(SurfaceAssessment(surface=TestSurface.UI, status=PresenceStatus.ABSENT, confidence=frontend_pres.confidence))
            surfaces.append(SurfaceAssessment(surface=TestSurface.NAVIGATION, status=PresenceStatus.ABSENT, confidence=frontend_pres.confidence))
            surfaces.append(SurfaceAssessment(surface=TestSurface.USER_INTERACTION, status=PresenceStatus.ABSENT, confidence=frontend_pres.confidence))

        # API
        if api_pres.is_present:
            surfaces.append(SurfaceAssessment(
                surface=TestSurface.API,
                status=PresenceStatus.PRESENT,
                confidence=api_pres.confidence,
                source=api_pres.source,
                rationale="API routes or endpoints present.",
            ))
        elif api_pres.is_absent:
            surfaces.append(SurfaceAssessment(surface=TestSurface.API, status=PresenceStatus.ABSENT, confidence=api_pres.confidence))

        # Business Logic
        if backend_pres.is_present:
            surfaces.append(SurfaceAssessment(
                surface=TestSurface.BUSINESS_LOGIC,
                status=PresenceStatus.PRESENT,
                confidence=backend_pres.confidence,
                source=backend_pres.source,
                rationale="Backend domain services or business logic modules present.",
            ))
        elif backend_pres.is_absent:
            surfaces.append(SurfaceAssessment(surface=TestSurface.BUSINESS_LOGIC, status=PresenceStatus.ABSENT, confidence=backend_pres.confidence))

        # Data Flow
        if db_pres.is_present or api_pres.is_present:
            surfaces.append(SurfaceAssessment(
                surface=TestSurface.DATA_FLOW,
                status=PresenceStatus.PRESENT,
                confidence=FactStatus.INFERENCE,
                source="db_or_api_presence",
                rationale="Data persistence or API transport available.",
            ))

        # Integration
        if (frontend_pres.is_present and backend_pres.is_present) or (api_pres.is_present and backend_pres.is_present):
            surfaces.append(SurfaceAssessment(
                surface=TestSurface.INTEGRATION,
                status=PresenceStatus.PRESENT,
                confidence=FactStatus.INFERENCE,
                source="multi_layer_presence",
                rationale="Multiple architectural layers co-exist for end-to-end integration.",
            ))

        return surfaces

    def _determine_project_type(
        self,
        frontend: PresenceAssessment,
        backend: PresenceAssessment,
        api: PresenceAssessment,
    ) -> str:
        if frontend.is_present and (backend.is_present or api.is_present):
            return "FULL_STACK"
        if frontend.is_present:
            return "FRONTEND"
        if api.is_present:
            return "API_SERVICE"
        if backend.is_present:
            return "BACKEND"
        return "UNKNOWN"

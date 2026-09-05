"""
Deterministic Project Context Selection Engine (Phase 1 / Part 8 / Step 4).

Implements deterministic selection of project material relevant to a CrawlerTask
or research question:
- Multi-signal relevance scoring (file path, filename, directory, extension,
  manifest references, doc headings, symbol names, related tests, task constraints)
- Deterministic ranking and multi-stage tie-breaking for 100% reproducibility
- Explicit resource ceilings (max_files, max_total_bytes, max_file_size,
  max_directories, max_symbols, max_depth, timeout)
- Preservation of structural context around selected files
- Bounded content retrieval (zero unbounded loads)
- Strict security (root containment, symlink escape rejection, sensitive file shielding)
- Zero LLM, zero semantic inference, zero project modification, zero execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import posixpath
import re
import time
from typing import Any, Callable, Optional
import uuid

from core.research.contracts.crawler_report import RawSourceReference
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.errors import (
    ProjectAccessError,
    ProjectCancelledError,
    ProjectContextError,
    ProjectFileNotFoundError,
    ProjectProviderError,
    ProjectResourceLimitError,
    ProjectSecurityError,
    ProjectTimeoutError,
    ProjectValidationError,
)
from core.research.project.discovery import (
    DiscoveredProjectStructure,
    ProjectDiscoveryOptions,
    ProjectStructureDiscoverer,
    classify_manifest_ecosystem,
    is_documentation_file_path,
    is_manifest_path,
    is_test_file_path,
)
from core.research.project.models import (
    LineRange,
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
    ProjectVCSContext,
    SymbolKind,
    mask_sensitive_config,
    normalize_project_path,
)
from core.research.project.provider import (
    IGNORED_PROJECT_NAMES,
    ProjectWorkspaceProvider,
    is_sensitive_project_path,
)
from core.research.repo.models import compute_sha256, detect_file_language, utc_now
from core.research.repo.structure import CodeParsingStatus, CodeStructureExtractor, StructuredCodeFile

logger = logging.getLogger("AutonomOS.Research.ProjectSelector")

CONFIG_EXTENSIONS = {
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".conf",
    ".config",
    ".xml",
    ".properties",
}

CATEGORY_PRIORITY: dict[str, int] = {
    "source": 0,
    "config": 1,
    "manifest": 2,
    "doc": 3,
    "test": 4,
}


def classify_project_file_category(path: str) -> str:
    """
    Classify a normalized relative project file path into a discrete functional category:
    'manifest', 'doc', 'test', 'config', or 'source'.
    """
    norm = normalize_project_path(path)
    if is_manifest_path(norm):
        return "manifest"
    if is_documentation_file_path(norm):
        return "doc"
    if is_test_file_path(norm):
        return "test"
    _, ext = posixpath.splitext(norm)
    if ext.lower() in CONFIG_EXTENSIONS:
        return "config"
    return "source"


# -----------------------------------------------------------------------------
# 1. Candidate Model
# -----------------------------------------------------------------------------

@dataclass
class CandidateProjectFile:
    """
    Scored project file candidate evaluated for deterministic retrieval.
    """
    file: ProjectFile
    score: float
    matched_terms: list[str] = field(default_factory=list)
    category: str = "source"  # source, test, doc, config, manifest
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.file.relative_path,
            "filename": self.file.filename,
            "score": round(self.score, 3),
            "category": self.category,
            "matched_terms": list(self.matched_terms),
            "reasons": list(self.reasons),
            "size_bytes": self.file.size_bytes,
            "language": self.file.language,
        }


# -----------------------------------------------------------------------------
# 2. Query Specification
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class ProjectSelectionQuery:
    """
    Structured query specification for deterministic project context selection.
    """
    raw_topic: str = ""
    keywords: list[str] = field(default_factory=list)
    target_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)
    target_languages: list[str] = field(default_factory=list)
    target_categories: list[str] = field(default_factory=list)
    symbol_names: list[str] = field(default_factory=list)
    min_score: float = 1.0
    constraints: list[str] = field(default_factory=list)

    @classmethod
    def from_input(
        cls,
        topic: str = "",
        keywords: Optional[list[str]] = None,
        target_paths: Optional[list[str]] = None,
        exclude_paths: Optional[list[str]] = None,
        target_languages: Optional[list[str]] = None,
        target_categories: Optional[list[str]] = None,
        symbol_names: Optional[list[str]] = None,
        min_score: float = 1.0,
        constraints: Optional[list[str]] = None,
    ) -> ProjectSelectionQuery:
        """Create a sanitized, normalized ProjectSelectionQuery."""
        norm_targets = [normalize_project_path(p) for p in (target_paths or []) if p and p.strip()]
        norm_excludes = [normalize_project_path(p) for p in (exclude_paths or []) if p and p.strip()]
        kws = [k.strip() for k in (keywords or []) if k and isinstance(k, str) and k.strip()]
        langs = [l.strip().lower() for l in (target_languages or []) if l and isinstance(l, str) and l.strip()]
        cats = [c.strip().lower() for c in (target_categories or []) if c and isinstance(c, str) and c.strip()]
        syms = [s.strip() for s in (symbol_names or []) if s and isinstance(s, str) and s.strip()]
        cstrs = [c.strip() for c in (constraints or []) if c and isinstance(c, str) and c.strip()]

        return cls(
            raw_topic=(topic or "").strip(),
            keywords=kws,
            target_paths=norm_targets,
            exclude_paths=norm_excludes,
            target_languages=langs,
            target_categories=cats,
            symbol_names=syms,
            min_score=max(0.0, float(min_score)),
            constraints=cstrs,
        )

    @classmethod
    def from_crawler_task(cls, task: CrawlerTask) -> ProjectSelectionQuery:
        """Extract a structured ProjectSelectionQuery from a CrawlerTask instance."""
        topic = task.objective or task.query_or_target
        params = task.parameters or {}

        kws: list[str] = list(params.get("keywords", []))
        targets: list[str] = list(params.get("target_paths", params.get("paths", [])))
        excludes: list[str] = list(params.get("exclude_paths", params.get("excludes", [])))
        langs: list[str] = list(params.get("target_languages", params.get("languages", [])))
        cats: list[str] = list(params.get("target_categories", params.get("categories", [])))
        syms: list[str] = list(params.get("symbol_names", params.get("symbols", [])))
        min_sc = float(params.get("min_score", 1.0))

        # Extract path/language hints from task constraints if formatted like "path:..." or "lang:..."
        for constraint in task.constraints:
            clow = constraint.lower()
            if clow.startswith("path:"):
                targets.append(constraint[5:].strip())
            elif clow.startswith("lang:") or clow.startswith("language:"):
                colon_idx = constraint.index(":")
                langs.append(constraint[colon_idx + 1:].strip())
            elif clow.startswith("category:"):
                cats.append(constraint[9:].strip())
            elif clow.startswith("exclude:"):
                excludes.append(constraint[8:].strip())

        return cls.from_input(
            topic=topic,
            keywords=kws,
            target_paths=targets,
            exclude_paths=excludes,
            target_languages=langs,
            target_categories=cats,
            symbol_names=syms,
            min_score=min_sc,
            constraints=list(task.constraints),
        )

    def extract_search_terms(self) -> list[str]:
        """
        Tokenize raw topic, keywords, and symbol names into normalized search terms.
        Splits camelCase, snake_case, dot, and hyphen delimiters.
        Deterministic order with deduplication.
        """
        terms: list[str] = []

        def add_term(t: str) -> None:
            clean = t.strip().lower()
            if len(clean) >= 2 and clean not in terms:
                terms.append(clean)

        def process_token_source(text: str) -> None:
            if not text:
                return
            s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
            clean = re.sub(r"[^a-zA-Z0-9_\-./]", " ", s1)
            for word in clean.split():
                add_term(word)
                for sub in re.split(r"[-_./]", word):
                    add_term(sub)
                    m = re.match(r"^([a-zA-Z]+)([0-9]+)$", sub)
                    if m:
                        add_term(m.group(1))

        # 1. Process raw topic
        process_token_source(self.raw_topic)

        # 2. Process explicit keywords
        for kw in (self.keywords or []):
            process_token_source(kw)

        # 3. Process explicit symbol names
        for sym in (self.symbol_names or []):
            process_token_source(sym)

        return terms


# -----------------------------------------------------------------------------
# 3. Selection Options / Budgets
# -----------------------------------------------------------------------------

@dataclass
class ProjectSelectionOptions:
    """
    Explicit bounds and configuration budgets for deterministic project context selection.
    """
    max_files: int = 20                       # Maximum number of files selected
    max_total_bytes: int = 2_000_000          # 2 MB aggregate fetched content limit
    max_file_size: int = 500_000              # 500 KB individual file content limit
    max_directories: int = 50                 # Maximum structural context directories
    max_symbols: int = 100                    # Maximum symbols extracted across files
    max_depth: int = 15                       # Maximum directory depth
    timeout_seconds: float = 30.0             # Overall execution timeout
    include_structural_context: bool = True   # Preserve parent hierarchy of selected files
    extract_symbols: bool = True              # Extract lightweight AST symbols from code
    read_manifest_refs: bool = True           # Check manifests for entrypoints/references
    read_doc_headings: bool = True            # Check README/docs headings for references
    concurrency_limit: int = 4                # Worker concurrency bounds

    def __post_init__(self):
        if self.max_files <= 0:
            raise ProjectValidationError("max_files", f"max_files must be > 0 (got {self.max_files}).")
        if self.max_total_bytes <= 0:
            raise ProjectValidationError("max_total_bytes", f"max_total_bytes must be > 0 (got {self.max_total_bytes}).")
        if self.max_file_size <= 0:
            raise ProjectValidationError("max_file_size", f"max_file_size must be > 0 (got {self.max_file_size}).")
        if self.max_directories <= 0:
            raise ProjectValidationError("max_directories", f"max_directories must be > 0 (got {self.max_directories}).")
        if self.max_symbols < 0:
            raise ProjectValidationError("max_symbols", f"max_symbols must be >= 0 (got {self.max_symbols}).")
        if self.max_depth <= 0:
            raise ProjectValidationError("max_depth", f"max_depth must be > 0 (got {self.max_depth}).")
        if self.timeout_seconds <= 0:
            raise ProjectValidationError("timeout_seconds", f"timeout_seconds must be > 0 (got {self.timeout_seconds}).")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_files": self.max_files,
            "max_total_bytes": self.max_total_bytes,
            "max_file_size": self.max_file_size,
            "max_directories": self.max_directories,
            "max_symbols": self.max_symbols,
            "max_depth": self.max_depth,
            "timeout_seconds": self.timeout_seconds,
            "include_structural_context": self.include_structural_context,
            "extract_symbols": self.extract_symbols,
            "read_manifest_refs": self.read_manifest_refs,
            "read_doc_headings": self.read_doc_headings,
            "concurrency_limit": self.concurrency_limit,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectSelectionOptions:
        if not isinstance(data, dict):
            raise ProjectValidationError("data", f"Expected dict, got {type(data).__name__}.")
        return cls(
            max_files=int(data.get("max_files", 20)),
            max_total_bytes=int(data.get("max_total_bytes", 2_000_000)),
            max_file_size=int(data.get("max_file_size", 500_000)),
            max_directories=int(data.get("max_directories", 50)),
            max_symbols=int(data.get("max_symbols", 100)),
            max_depth=int(data.get("max_depth", 15)),
            timeout_seconds=float(data.get("timeout_seconds", 30.0)),
            include_structural_context=bool(data.get("include_structural_context", True)),
            extract_symbols=bool(data.get("extract_symbols", True)),
            read_manifest_refs=bool(data.get("read_manifest_refs", True)),
            read_doc_headings=bool(data.get("read_doc_headings", True)),
            concurrency_limit=int(data.get("concurrency_limit", 4)),
        )


# -----------------------------------------------------------------------------
# 4. Deterministic Relevance Scorer
# -----------------------------------------------------------------------------

class ProjectRelevanceScorer:
    """
    Deterministic scoring engine evaluating project files against selection queries.
    Multi-signal evaluation + deterministic multi-stage tie-breaking.
    """

    def score_candidates(
        self,
        files: list[ProjectFile],
        query: ProjectSelectionQuery,
        primary_language: Optional[str] = None,
        manifest_references: Optional[set[str]] = None,
        doc_heading_references: Optional[set[str]] = None,
    ) -> list[CandidateProjectFile]:
        """
        Score all candidate files against query and return sorted list using deterministic tie-breaking.
        """
        terms = query.extract_search_terms()
        manifest_refs = manifest_references or set()
        doc_refs = doc_heading_references or set()

        candidates: list[CandidateProjectFile] = []

        # Pass 1: Initial scoring per file
        for f in files:
            norm_path = f.relative_path

            # Strict security & exclusion checks
            if is_sensitive_project_path(norm_path):
                continue

            parts = norm_path.split("/")
            if any(part in IGNORED_PROJECT_NAMES for part in parts):
                continue

            if query.exclude_paths:
                if any(norm_path == exp or norm_path.startswith(exp + "/") for exp in query.exclude_paths):
                    continue

            cat = classify_project_file_category(norm_path)
            if query.target_categories and cat not in query.target_categories:
                continue

            if query.target_paths:
                is_under_target = any(
                    norm_path == tp or norm_path.startswith(tp + "/")
                    for tp in query.target_paths
                )
                if not is_under_target:
                    continue

            if query.target_languages and f.language:
                if f.language.lower() not in query.target_languages and cat not in ("manifest", "doc"):
                    continue

            score, matched_terms, reasons = self._score_single_file(
                f=f,
                query=query,
                terms=terms,
                cat=cat,
                primary_language=primary_language,
                manifest_refs=manifest_refs,
                doc_refs=doc_refs,
            )

            if score >= query.min_score:
                candidates.append(
                    CandidateProjectFile(
                        file=f,
                        score=score,
                        matched_terms=matched_terms,
                        category=cat,
                        reasons=reasons,
                    )
                )

        # Pass 2: Related test boosting
        # If candidate source file has score >= 5.0, boost matching test files
        source_stems = {
            posixpath.splitext(c.file.filename)[0].lower()
            for c in candidates
            if c.category == "source" and c.score >= 5.0
        }

        if source_stems:
            for c in candidates:
                if c.category == "test":
                    test_stem = posixpath.splitext(c.file.filename)[0].lower()
                    for s_stem in source_stems:
                        if s_stem in test_stem:
                            c.score += 3.0
                            c.reasons.append(f"Related test for candidate '{s_stem}' (+3.0)")
                            break

        # Pass 3: Related source boosting from test
        # If candidate test has high score (>= 5.0), boost matching source file
        test_stems = {
            posixpath.splitext(c.file.filename)[0].lower().replace("test_", "").replace("_test", "").replace(".test", ""): c.file.relative_path
            for c in candidates
            if c.category == "test" and c.score >= 5.0
        }
        if test_stems:
            for c in candidates:
                if c.category == "source":
                    s_stem = posixpath.splitext(c.file.filename)[0].lower()
                    if s_stem in test_stems:
                        c.score += 2.0
                        c.reasons.append("Source file covered by matching test candidate (+2.0)")

        # Deterministic multi-stage tie-breaking:
        # 1. Higher score first (-score)
        # 2. Functional category priority (source < config < manifest < doc < test)
        # 3. Shallower directory depth first (path.count('/'))
        # 4. Shorter path length
        # 5. Lexicographical path ascending
        candidates.sort(
            key=lambda c: (
                -c.score,
                CATEGORY_PRIORITY.get(c.category, 99),
                c.file.relative_path.count("/"),
                len(c.file.relative_path),
                c.file.relative_path,
            )
        )
        return candidates

    def _score_single_file(
        self,
        f: ProjectFile,
        query: ProjectSelectionQuery,
        terms: list[str],
        cat: str,
        primary_language: Optional[str],
        manifest_refs: set[str],
        doc_refs: set[str],
    ) -> tuple[float, list[str], list[str]]:
        score = 0.0
        matched_terms: list[str] = []
        reasons: list[str] = []

        norm_path = f.relative_path.lower()
        filename_lower = f.filename.lower()
        stem, ext = posixpath.splitext(filename_lower)

        # Signal 1: Target path match bonus
        if query.target_paths:
            for tp in query.target_paths:
                tp_low = tp.lower()
                if norm_path == tp_low or norm_path.startswith(tp_low + "/"):
                    score += 15.0
                    reasons.append(f"Explicit target path match '{tp}' (+15.0)")
                    break

        # Signal 2: Term matching against filename, stem, and directory segments
        for term in terms:
            term_matched = False

            # Exact filename or stem match
            if term == stem or term == filename_lower:
                score += 10.0
                term_matched = True
                reasons.append(f"Exact filename/stem match '{term}' (+10.0)")
            elif term in stem or term in filename_lower:
                score += 5.0
                term_matched = True
                reasons.append(f"Substring filename match '{term}' (+5.0)")

            # Path directory segment match
            segments = [p for p in norm_path.split("/")[:-1] if p]
            if any(term == seg for seg in segments):
                score += 3.0
                term_matched = True
                reasons.append(f"Exact directory match '{term}' (+3.0)")
            elif any(term in seg for seg in segments):
                score += 1.5
                term_matched = True
                reasons.append(f"Substring directory match '{term}' (+1.5)")

            if term_matched:
                matched_terms.append(term)

        # Signal 3: Symbol names match
        if query.symbol_names:
            for sym in query.symbol_names:
                sym_low = sym.lower()
                if sym_low == stem:
                    score += 5.0
                    reasons.append(f"Symbol exact match to file stem '{sym}' (+5.0)")
                elif sym_low in stem:
                    score += 2.5
                    reasons.append(f"Symbol substring match to file stem '{sym}' (+2.5)")

        # Signal 4: Category weighting (only if terms or target paths matched)
        if matched_terms or query.target_paths:
            if cat == "source":
                score += 3.0
                reasons.append("Source code category bonus (+3.0)")
            elif cat == "config":
                score += 2.0
                reasons.append("Configuration category bonus (+2.0)")
            elif cat == "doc":
                score += 2.0
                reasons.append("Documentation category bonus (+2.0)")
            elif cat == "manifest":
                score += 1.5
                reasons.append("Manifest category bonus (+1.5)")
            elif cat == "test":
                score += 2.0
                reasons.append("Test category bonus (+2.0)")

        # Signal 5: Language match bonus
        if query.target_languages and f.language:
            if f.language.lower() in query.target_languages:
                score += 3.0
                reasons.append(f"Target language match '{f.language}' (+3.0)")
        elif primary_language and f.language and matched_terms:
            if f.language.lower() == primary_language.lower():
                score += 1.0
                reasons.append(f"Project primary language match '{f.language}' (+1.0)")

        # Signal 6: Manifest references
        if (matched_terms or query.target_paths or query.symbol_names) and (f.relative_path in manifest_refs or f.filename in manifest_refs):
            score += 4.0
            reasons.append("Referenced in project manifest (+4.0)")

        # Signal 7: Documentation heading references
        if (matched_terms or query.target_paths or query.symbol_names) and (stem in doc_refs or f.filename in doc_refs):
            score += 3.0
            reasons.append("Mentioned in documentation headings (+3.0)")

        return score, matched_terms, reasons


# -----------------------------------------------------------------------------
# 5. Selected Context Container
# -----------------------------------------------------------------------------

@dataclass
class SelectedProjectContext:
    """
    Consolidated output container for deterministic project context selection.
    Includes selected files, bounded source materials, extracted symbols,
    configurations, structural context, and conversion to standard ProjectContext.
    """
    query: ProjectSelectionQuery
    options: ProjectSelectionOptions
    identity: ProjectIdentity
    selected_files: list[CandidateProjectFile] = field(default_factory=list)
    skipped_candidates: list[CandidateProjectFile] = field(default_factory=list)
    source_materials: list[ProjectSourceMaterial] = field(default_factory=list)
    symbols: list[ProjectSymbol] = field(default_factory=list)
    structural_context: Optional[ProjectStructure] = None
    configurations: list[ProjectConfigurationMetadata] = field(default_factory=list)
    dependencies: list[ProjectDependencyMetadata] = field(default_factory=list)
    vcs: Optional[ProjectVCSContext] = None
    total_bytes_fetched: int = 0
    duration_seconds: float = 0.0
    is_truncated: bool = False
    truncation_reasons: list[str] = field(default_factory=list)
    file_failures: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def selected_count(self) -> int:
        return len(self.selected_files)

    @property
    def selected_paths(self) -> list[str]:
        return [c.file.relative_path for c in self.selected_files]

    def to_project_context(self) -> ProjectContext:
        """
        Convert selected context into standard ProjectContext aggregate container.
        """
        ctx = ProjectContext(
            identity=self.identity,
            structure=self.structural_context,
            source_materials=list(self.source_materials),
            symbols=list(self.symbols),
            configurations=list(self.configurations),
            dependencies=list(self.dependencies),
            vcs=self.vcs,
            metadata={
                "selected_files_count": len(self.selected_files),
                "skipped_candidates_count": len(self.skipped_candidates),
                "total_bytes_fetched": self.total_bytes_fetched,
                "duration_seconds": self.duration_seconds,
                "is_truncated": self.is_truncated,
                "truncation_reasons": list(self.truncation_reasons),
                "file_failures": list(self.file_failures),
                "query_topic": self.query.raw_topic,
                "query_keywords": self.query.keywords,
            },
        )
        return ctx

    def to_raw_source_references(self) -> list[RawSourceReference]:
        """Convert all retrieved project source materials into standard RawSourceReferences."""
        return [mat.to_raw_source_reference() for mat in self.source_materials]

    def to_evidence_items(
        self,
        request_id: str,
        crawler_task_id: str,
        crawler_id: str,
        question_id: str = "",
        correlation_id: str = "",
    ) -> list[EvidenceItem]:
        """Convert all retrieved project source materials into standard EvidenceItems."""
        ctx = self.to_project_context()
        return ctx.to_evidence_items(
            request_id=request_id,
            crawler_task_id=crawler_task_id,
            crawler_id=crawler_id,
            question_id=question_id,
            correlation_id=correlation_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "query": {
                "raw_topic": self.query.raw_topic,
                "keywords": self.query.keywords,
                "target_paths": self.query.target_paths,
                "target_languages": self.query.target_languages,
                "target_categories": self.query.target_categories,
                "symbol_names": self.query.symbol_names,
                "min_score": self.query.min_score,
            },
            "selected_count": len(self.selected_files),
            "selected_files": [f.to_dict() for f in self.selected_files],
            "skipped_count": len(self.skipped_candidates),
            "materials_count": len(self.source_materials),
            "symbols_count": len(self.symbols),
            "configurations_count": len(self.configurations),
            "total_bytes_fetched": self.total_bytes_fetched,
            "duration_seconds": round(self.duration_seconds, 4),
            "is_truncated": self.is_truncated,
            "truncation_reasons": list(self.truncation_reasons),
            "structural_context": self.structural_context.to_dict() if self.structural_context else None,
            "metadata": dict(self.metadata),
        }


# -----------------------------------------------------------------------------
# 6. Project Context Selector Engine
# -----------------------------------------------------------------------------

class ProjectContextSelector:
    """
    Coordinator engine for deterministic project context selection.
    Integrates ProjectWorkspaceProvider, ProjectStructureDiscoverer,
    deterministic relevance scoring, bounded content retrieval,
    AST symbol extraction, and structural context preservation.
    """

    def __init__(
        self,
        scorer: Optional[ProjectRelevanceScorer] = None,
        discoverer: Optional[ProjectStructureDiscoverer] = None,
    ):
        self.scorer = scorer or ProjectRelevanceScorer()
        self.discoverer = discoverer or ProjectStructureDiscoverer()

    def select(
        self,
        provider: ProjectWorkspaceProvider,
        query: ProjectSelectionQuery,
        options: Optional[ProjectSelectionOptions] = None,
        discovered: Optional[DiscoveredProjectStructure] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> SelectedProjectContext:
        """
        Execute deterministic project context selection against a workspace provider.
        """
        start_time = time.time()
        opts = options or ProjectSelectionOptions()

        provider.check_cancellation(is_cancelled, "project_context_selection", provider.root_path)

        # 1. Discover physical project structure if not provided
        if discovered is None:
            disc_opts = ProjectDiscoveryOptions(
                max_depth=opts.max_depth,
                max_files=min(5000, max(2000, opts.max_files * 10)),
                max_directories=min(1000, max(500, opts.max_directories * 10)),
                timeout_seconds=opts.timeout_seconds,
                concurrency_limit=opts.concurrency_limit,
            )
            discovered = self.discoverer.discover(
                provider=provider,
                options=disc_opts,
                is_cancelled=is_cancelled,
            )

        identity = discovered.identity
        structure = discovered.structure

        # 2. Extract manifest references and doc headings if enabled
        manifest_refs: set[str] = set()
        if opts.read_manifest_refs and discovered.manifest_files:
            manifest_refs = self._extract_manifest_references(
                provider=provider,
                manifest_files=discovered.manifest_files[:5],
                max_bytes=16_384,
                is_cancelled=is_cancelled,
            )

        doc_refs: set[str] = set()
        if opts.read_doc_headings and discovered.documentation_files:
            doc_refs = self._extract_doc_headings(
                provider=provider,
                doc_files=discovered.documentation_files[:5],
                max_bytes=16_384,
                is_cancelled=is_cancelled,
            )

        # 3. Determine primary language
        primary_lang = None
        if discovered.languages:
            # First key is top language in deterministically sorted dict
            primary_lang = next(iter(discovered.languages.keys()), None)

        # 4. Score all candidate files
        provider.check_cancellation(is_cancelled, "score_candidates", provider.root_path)
        candidates = self.scorer.score_candidates(
            files=structure.files,
            query=query,
            primary_language=primary_lang,
            manifest_references=manifest_refs,
            doc_heading_references=doc_refs,
        )

        # 5. Cap selected candidates to max_files budget
        selected_candidates = candidates[:opts.max_files]
        skipped_candidates = candidates[opts.max_files:]

        is_truncated = False
        truncation_reasons: list[str] = []

        if len(candidates) > opts.max_files:
            is_truncated = True
            truncation_reasons.append(f"Candidate file count ({len(candidates)}) exceeded max_files budget ({opts.max_files}).")

        # 6. Retrieve bounded content for selected files
        source_materials: list[ProjectSourceMaterial] = []
        symbols: list[ProjectSymbol] = []
        configurations: list[ProjectConfigurationMetadata] = []
        file_failures: list[dict[str, Any]] = []
        total_bytes_fetched = 0

        for candidate in selected_candidates:
            provider.check_cancellation(is_cancelled, "retrieve_content", candidate.file.relative_path)

            # Check timeout
            elapsed = time.time() - start_time
            if elapsed >= opts.timeout_seconds:
                is_truncated = True
                truncation_reasons.append(f"Selection timed out after {elapsed:.2f}s (budget: {opts.timeout_seconds}s).")
                break

            # Check cumulative byte budget
            remaining_bytes = opts.max_total_bytes - total_bytes_fetched
            if remaining_bytes <= 0:
                is_truncated = True
                if not any("max_total_bytes" in r for r in truncation_reasons):
                    truncation_reasons.append(f"Total fetched bytes ({total_bytes_fetched}) reached max_total_bytes budget ({opts.max_total_bytes}).")
                break

            if candidate.file.size_bytes > remaining_bytes:
                is_truncated = True
                if not any("max_total_bytes" in r for r in truncation_reasons):
                    truncation_reasons.append(f"Total fetched bytes ({total_bytes_fetched}) reached max_total_bytes budget ({opts.max_total_bytes}).")

            # Bound individual file read
            file_fetch_limit = min(
                opts.max_file_size,
                remaining_bytes,
            )

            try:
                mat = provider.get_file_content(
                    file_path=candidate.file.relative_path,
                    max_bytes=file_fetch_limit,
                    timeout_seconds=max(0.5, opts.timeout_seconds - elapsed),
                    is_cancelled=is_cancelled,
                )
                source_materials.append(mat)
                total_bytes_fetched += mat.size_bytes

                # Extract configuration metadata if classified as config or manifest
                if candidate.category in ("config", "manifest") and not mat.is_binary:
                    if "pyproject.toml" in mat.file_path:
                        cfg_type = ProjectConfigType.PYTHON_PYPROJECT
                    elif "package.json" in mat.file_path:
                        cfg_type = ProjectConfigType.NPM_PACKAGE
                    elif "Cargo.toml" in mat.file_path:
                        cfg_type = ProjectConfigType.RUST_CARGO
                    elif "go.mod" in mat.file_path:
                        cfg_type = ProjectConfigType.GO_MOD
                    elif "Dockerfile" in mat.file_path:
                        cfg_type = ProjectConfigType.DOCKERFILE
                    else:
                        cfg_type = ProjectConfigType.GENERIC
                    safe_meta = {
                        "path": mat.file_path,
                        "category": candidate.category,
                        "size_bytes": mat.size_bytes,
                        "line_count": mat.line_count,
                    }
                    cfg = ProjectConfigurationMetadata(
                        config_path=mat.file_path,
                        config_type=cfg_type,
                        safe_metadata=safe_meta,
                        content_hash=mat.content_hash,
                        provenance=mat.provenance,
                    )
                    configurations.append(cfg)

                # Extract lightweight AST symbols if enabled and within symbol budget
                if (
                    opts.extract_symbols
                    and len(symbols) < opts.max_symbols
                    and not mat.is_binary
                    and mat.content
                    and candidate.category in ("source", "test")
                ):
                    extracted_syms = self._extract_file_symbols(
                        file_path=mat.file_path,
                        content=mat.content,
                        language=mat.language,
                        max_symbols_remaining=opts.max_symbols - len(symbols),
                    )
                    symbols.extend(extracted_syms)

            except ProjectResourceLimitError as e:
                is_truncated = True
                truncation_reasons.append(f"Resource limit reading '{candidate.file.relative_path}': {e}")
                file_failures.append({"file_path": candidate.file.relative_path, "error": str(e)})
            except (ProjectFileNotFoundError, ProjectProviderError, ProjectAccessError) as e:
                logger.warning(f"Error fetching selected file '{candidate.file.relative_path}': {e}")
                file_failures.append({"file_path": candidate.file.relative_path, "error": str(e)})

        if total_bytes_fetched >= opts.max_total_bytes and not any("max_total_bytes" in r for r in truncation_reasons):
            is_truncated = True
            truncation_reasons.append(f"Total fetched bytes ({total_bytes_fetched}) reached max_total_bytes budget ({opts.max_total_bytes}).")

        # 7. Construct structural context preserving parent directories
        structural_context = None
        if opts.include_structural_context:
            structural_context = self._build_structural_context(
                selected_files=[c.file for c in selected_candidates],
                all_directories=structure.directories,
                all_files=structure.files,
                max_directories=opts.max_directories,
                root_path=identity.project_root,
            )

        duration = time.time() - start_time

        return SelectedProjectContext(
            query=query,
            options=opts,
            identity=identity,
            selected_files=selected_candidates,
            skipped_candidates=skipped_candidates,
            source_materials=source_materials,
            symbols=symbols,
            structural_context=structural_context,
            configurations=configurations,
            dependencies=[],
            vcs=None,
            total_bytes_fetched=total_bytes_fetched,
            duration_seconds=duration,
            is_truncated=is_truncated,
            truncation_reasons=truncation_reasons,
            file_failures=file_failures,
            metadata={
                "discovered_files_count": len(structure.files),
                "candidates_scored_count": len(candidates),
                "primary_language": primary_lang,
            },
        )

    def _extract_manifest_references(
        self,
        provider: ProjectWorkspaceProvider,
        manifest_files: list[ProjectFile],
        max_bytes: int = 16_384,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> set[str]:
        """Scan manifest files boundedly for entrypoints and path references."""
        refs: set[str] = set()
        for mf in manifest_files:
            try:
                mat = provider.get_file_content(
                    file_path=mf.relative_path,
                    max_bytes=max_bytes,
                    timeout_seconds=2.0,
                    is_cancelled=is_cancelled,
                )
                if not mat.content or mat.is_binary:
                    continue

                # Find path-like strings in manifest (e.g. "main": "src/index.js", "packages/...")
                matches = re.findall(r'["\']([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+)["\']', mat.content)
                for m in matches:
                    norm = m.replace("\\", "/").strip("/")
                    if norm:
                        refs.add(norm)
                        refs.add(posixpath.basename(norm))
            except Exception:
                pass
        return refs

    def _extract_doc_headings(
        self,
        provider: ProjectWorkspaceProvider,
        doc_files: list[ProjectFile],
        max_bytes: int = 16_384,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> set[str]:
        """Scan markdown/doc files boundedly for headings and referenced stems."""
        refs: set[str] = set()
        for df in doc_files:
            try:
                mat = provider.get_file_content(
                    file_path=df.relative_path,
                    max_bytes=max_bytes,
                    timeout_seconds=2.0,
                    is_cancelled=is_cancelled,
                )
                if not mat.content or mat.is_binary:
                    continue

                for line in mat.content.splitlines()[:100]:
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        heading_text = stripped.lstrip("#").strip().lower()
                        for word in re.findall(r"[a-zA-Z0-9_\-]+", heading_text):
                            if len(word) >= 2:
                                refs.add(word)
            except Exception:
                pass
        return refs

    def _extract_file_symbols(
        self,
        file_path: str,
        content: str,
        language: Optional[str],
        max_symbols_remaining: int,
    ) -> list[ProjectSymbol]:
        """
        Extract AST-based code symbols (classes, functions, constants) without LLM.
        Capped by max_symbols_remaining.
        """
        if max_symbols_remaining <= 0 or not content:
            return []

        symbols: list[ProjectSymbol] = []
        try:
            struct: StructuredCodeFile = CodeStructureExtractor.extract_structure(
                file_path=file_path,
                content=content,
                language=language,
                max_parse_bytes=len(content.encode("utf-8")),
            )

            # 1. Classes
            for c in struct.classes:
                if len(symbols) >= max_symbols_remaining:
                    break
                symbols.append(
                    ProjectSymbol(
                        name=c.name,
                        symbol_type=SymbolKind.INTERFACE if c.is_interface else SymbolKind.CLASS,
                        file_path=file_path,
                        line_range=c.line_range,
                        signature=f"class {c.name}" + (f"({', '.join(c.bases)})" if c.bases else ""),
                        docstring=c.docstring,
                    )
                )

            # 2. Functions & Methods
            for fn in struct.functions:
                if len(symbols) >= max_symbols_remaining:
                    break
                symbols.append(
                    ProjectSymbol(
                        name=fn.name,
                        symbol_type=SymbolKind.METHOD if fn.is_method else (SymbolKind.ASYNC_FUNCTION if fn.is_async else SymbolKind.FUNCTION),
                        file_path=file_path,
                        line_range=fn.line_range,
                        parent_symbol=fn.parent_class,
                        signature=fn.signature or f"def {fn.name}()",
                        docstring=fn.docstring,
                    )
                )

            # 3. Top-level Constants
            for const in struct.constants:
                if len(symbols) >= max_symbols_remaining:
                    break
                symbols.append(
                    ProjectSymbol(
                        name=const.name,
                        symbol_type=SymbolKind.CONSTANT,
                        file_path=file_path,
                        line_range=const.line_range,
                        signature=f"{const.name}: {const.type_annotation}" if const.type_annotation else const.name,
                    )
                )

        except Exception as e:
            logger.debug(f"Symbol extraction skipped for '{file_path}': {e}")

        return symbols

    def _build_structural_context(
        self,
        selected_files: list[ProjectFile],
        all_directories: list[ProjectDirectory],
        all_files: list[ProjectFile],
        max_directories: int,
        root_path: str,
    ) -> ProjectStructure:
        """
        Preserve ancestor directory hierarchy and immediate structural context around selected files.
        """
        needed_dir_paths: set[str] = set()

        for f in selected_files:
            parent = f.parent_path
            while parent:
                needed_dir_paths.add(parent)
                parent = posixpath.dirname(parent) if "/" in parent else ""

        # Deterministically select matching directory nodes up to max_directories
        matching_dirs: list[ProjectDirectory] = []
        sorted_all_dirs = sorted(all_directories, key=lambda d: d.path)

        for d in sorted_all_dirs:
            if d.path in needed_dir_paths:
                matching_dirs.append(d)
                if len(matching_dirs) >= max_directories:
                    break

        return ProjectStructure(
            root_path=root_path,
            directories=matching_dirs,
            files=list(selected_files),
            total_files=len(selected_files),
            total_directories=len(matching_dirs),
        )

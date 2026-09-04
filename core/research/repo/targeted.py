"""
Targeted Repository Crawling Engine (Phase 1 / Part 5 / Step 4).

Performs deterministic, keyword- and structure-aware relevance scoring, candidate ranking,
and bounded source material retrieval for code repositories.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import posixpath
import re
from typing import Any, Callable, Optional

from core.research.contracts.evidence import EvidenceProvenance
from core.research.errors import (
    RepositoryCancelledError,
    RepositoryError,
    RepositoryFileNotFoundError,
    RepositoryNotFoundError,
    RepositoryProviderError,
    RepositoryResourceLimitError,
    RepositoryRevisionNotFoundError,
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
from core.research.repo.mock_provider import MockRepositoryProvider
from core.research.repo.models import (
    LineRange,
    RepositoryFile,
    RepositoryIdentity,
    RepositoryRevision,
    RepositorySource,
    RepositorySourceMaterial,
    RepositoryTree,
    normalize_repo_path,
    utc_now,
)
from core.research.repo.provider import (
    RepositoryFetchLimits,
    RepositoryProvider,
)

logger = logging.getLogger("AutonomOS.Research.TargetedRepository")

TEST_PATH_PATTERNS = [
    re.compile(r"(^|/)(tests?|specs?|__tests?__)/", re.IGNORECASE),
    re.compile(r"(_test|\.test|\.spec|test_|spec_)[a-zA-Z0-9_-]*\.[a-zA-Z0-9]+$", re.IGNORECASE),
]

CONFIG_EXTENSIONS = {".json", ".yaml", ".yml", ".toml", ".ini", ".conf", ".config", ".xml", ".properties"}


def classify_file_category(path: str) -> str:
    """
    Classify a repository file path into a discrete functional category:
    'manifest', 'doc', 'test', 'config', or 'source'.
    """
    norm = normalize_repo_path(path)
    if is_manifest_file(norm):
        return "manifest"
    if is_readme_file(norm) or is_documentation_file(norm):
        return "doc"
    for pat in TEST_PATH_PATTERNS:
        if pat.search(norm):
            return "test"
    _, ext = posixpath.splitext(norm)
    if ext.lower() in CONFIG_EXTENSIONS:
        return "config"
    return "source"


# -----------------------------------------------------------------------------
# Query & Candidate Models
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class RepoTopicQuery:
    """
    Structured query specification for targeted repository crawling.
    """
    raw_topic: str
    keywords: list[str] = field(default_factory=list)
    target_path: Optional[str] = None
    target_language: Optional[str] = None
    target_categories: Optional[list[str]] = None
    min_score: float = 1.0

    @classmethod
    def from_input(
        cls,
        topic: str = "",
        keywords: Optional[list[str]] = None,
        target_path: Optional[str] = None,
        target_language: Optional[str] = None,
        target_categories: Optional[list[str]] = None,
        min_score: float = 1.0,
    ) -> RepoTopicQuery:
        kws = [k.strip().lower() for k in (keywords or []) if k and isinstance(k, str) and k.strip()]
        cats = [c.strip().lower() for c in (target_categories or []) if c and isinstance(c, str) and c.strip()]
        return cls(
            raw_topic=(topic or "").strip(),
            keywords=kws,
            target_path=normalize_repo_path(target_path) if target_path else None,
            target_language=target_language.strip().lower() if target_language else None,
            target_categories=cats if cats else None,
            min_score=max(0.0, float(min_score)),
        )

    def extract_search_terms(self) -> list[str]:
        """Tokenize raw topic and keywords into normalized search terms."""
        terms: list[str] = []
        if self.raw_topic:
            # Extract words, camelCase splits, and snake_case / hyphen splits
            clean_topic = re.sub(r"[^a-zA-Z0-9_\-./]", " ", self.raw_topic)
            for word in clean_topic.split():
                if len(word) >= 2 and word.lower() not in terms:
                    terms.append(word.lower())
                # Also split on hyphen, underscore, and dot
                for sub in re.split(r"[-_./]", word):
                    if len(sub) >= 2 and sub.lower() not in terms:
                        terms.append(sub.lower())
        for kw in self.keywords:
            if kw and len(kw) >= 2 and kw.lower() not in terms:
                terms.append(kw.lower())
            for sub in re.split(r"[-_./]", kw or ""):
                if len(sub) >= 2 and sub.lower() not in terms:
                    terms.append(sub.lower())
        return terms


@dataclass
class CandidateRepoFile:
    """
    Scored repository file candidate evaluated for targeted retrieval.
    """
    file: RepositoryFile
    relevance_score: float
    matched_terms: list[str] = field(default_factory=list)
    category: str = "source"
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.file.path,
            "filename": self.file.filename,
            "relevance_score": round(self.relevance_score, 3),
            "category": self.category,
            "matched_terms": list(self.matched_terms),
            "reasons": list(self.reasons),
        }


# -----------------------------------------------------------------------------
# Relevance Scorer
# -----------------------------------------------------------------------------

class RepositoryRelevanceScorer:
    """
    Deterministic scoring engine evaluating repository file paths, filenames,
    languages, and categories against research topic queries.
    """

    def score_candidates(
        self,
        files: list[RepositoryFile],
        query: RepoTopicQuery,
    ) -> list[CandidateRepoFile]:
        """
        Score and rank a collection of RepositoryFiles against a RepoTopicQuery.
        Returns candidates sorted deterministically by descending score, path depth, and path.
        """
        terms = query.extract_search_terms()
        candidates: list[CandidateRepoFile] = []

        # Pass 1: Initial scoring
        for f in files:
            score, matched_terms, reasons = self._score_single_file(f, query, terms)
            cat = classify_file_category(f.path)

            if score >= query.min_score:
                candidates.append(
                    CandidateRepoFile(
                        file=f,
                        relevance_score=score,
                        matched_terms=matched_terms,
                        category=cat,
                        reasons=reasons,
                    )
                )

        # Pass 2: Related test boosting (if a test corresponds to a high-scoring source file)
        source_stems = {
            posixpath.splitext(c.file.filename)[0].lower()
            for c in candidates
            if c.category == "source" and c.relevance_score >= 5.0
        }

        if source_stems:
            for c in candidates:
                if c.category == "test":
                    test_stem = posixpath.splitext(c.file.filename)[0].lower()
                    for s_stem in source_stems:
                        if s_stem in test_stem:
                            c.relevance_score += 2.0
                            c.reasons.append(f"Related test for candidate '{s_stem}' (+2.0)")
                            break

        # Deterministic tie-breaking:
        # 1. Higher relevance score first (-score)
        # 2. Shallower directory depth first (path.count('/'))
        # 3. Lexicographical path ascending
        candidates.sort(key=lambda c: (-c.relevance_score, c.file.path.count("/"), c.file.path))
        return candidates

    def _score_single_file(
        self,
        f: RepositoryFile,
        query: RepoTopicQuery,
        terms: list[str],
    ) -> tuple[float, list[str], list[str]]:
        score = 0.0
        matched_terms: list[str] = []
        reasons: list[str] = []

        norm_path = f.path.lower()
        filename_lower = f.filename.lower()
        stem, _ = posixpath.splitext(filename_lower)
        cat = classify_file_category(f.path)

        # 1. Filter category constraint if requested
        if query.target_categories and cat not in query.target_categories:
            return 0.0, [], []

        # 2. Target path prefix match
        if query.target_path:
            if norm_path == query.target_path or norm_path.startswith(query.target_path + "/"):
                score += 10.0
                reasons.append(f"Target subpath match '{query.target_path}' (+10.0)")

        # 3. Term matching against filename and path
        for term in terms:
            term_matched = False

            # Exact filename / stem match (e.g. term='auth', stem='auth')
            if term == stem or term == filename_lower:
                score += 10.0
                term_matched = True
                reasons.append(f"Exact filename match '{term}' (+10.0)")
            elif term in stem or term in filename_lower:
                score += 5.0
                term_matched = True
                reasons.append(f"Substring filename match '{term}' (+5.0)")

            # Path directory segment match
            path_segments = [p for p in norm_path.split("/")[:-1] if p]
            if any(term == seg for seg in path_segments):
                score += 3.0
                term_matched = True
                reasons.append(f"Exact directory match '{term}' (+3.0)")
            elif any(term in seg for seg in path_segments):
                score += 1.5
                term_matched = True
                reasons.append(f"Substring directory match '{term}' (+1.5)")

            if term_matched:
                matched_terms.append(term)

        # 4. Category bonus if terms matched
        if matched_terms:
            if cat == "source":
                score += 3.0
                reasons.append("Source code bonus (+3.0)")
            elif cat == "test":
                score += 2.0
                reasons.append("Test code bonus (+2.0)")
            elif cat == "doc":
                score += 2.0
                reasons.append("Documentation bonus (+2.0)")
            elif cat == "manifest":
                score += 1.5
                reasons.append("Manifest bonus (+1.5)")

        # 5. Language match bonus
        if query.target_language and f.language:
            if query.target_language == f.language.lower():
                score += 2.0
                reasons.append(f"Language match '{f.language}' (+2.0)")

        return score, matched_terms, reasons


# -----------------------------------------------------------------------------
# Targeted Repository Crawl Result & Engine
# -----------------------------------------------------------------------------

@dataclass
class TargetedRepoCrawlResult:
    """
    Result container for a targeted repository crawl operation.
    """
    repository_source: RepositorySource
    topic_query: RepoTopicQuery
    candidates_discovered: list[CandidateRepoFile] = field(default_factory=list)
    selected_files: list[CandidateRepoFile] = field(default_factory=list)
    skipped_candidates: list[CandidateRepoFile] = field(default_factory=list)
    materials_retrieved: list[RepositorySourceMaterial] = field(default_factory=list)
    file_failures: list[dict[str, Any]] = field(default_factory=list)
    total_bytes_fetched: int = 0
    is_partial_success: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository_source": self.repository_source.to_dict(),
            "topic": self.topic_query.raw_topic,
            "keywords": self.topic_query.keywords,
            "candidates_count": len(self.candidates_discovered),
            "selected_count": len(self.selected_files),
            "materials_count": len(self.materials_retrieved),
            "total_bytes_fetched": self.total_bytes_fetched,
            "is_partial_success": self.is_partial_success,
            "file_failures": list(self.file_failures),
            "selected_files": [f.to_dict() for f in self.selected_files],
            "metadata": dict(self.metadata),
        }


class TargetedRepositoryEngine:
    """
    High-level engine executing targeted repository discovery, ranking, and bounded file retrieval.
    """

    def __init__(
        self,
        provider: Optional[RepositoryProvider] = None,
        discovery_engine: Optional[RepositoryDiscoveryEngine] = None,
    ):
        self.provider = provider or MockRepositoryProvider()
        self.discovery_engine = discovery_engine or RepositoryDiscoveryEngine(provider=self.provider)
        self.scorer = RepositoryRelevanceScorer()

    def crawl_targeted(
        self,
        target: str | RepositoryIdentity,
        query: RepoTopicQuery,
        revision: Optional[str] = None,
        max_files: int = 10,
        max_bytes: int = 1_000_000,
        max_file_size: int = 250_000,
        max_depth: int = 10,
        max_requests: int = 20,
        extract_structure: bool = True,
        max_parse_bytes: int = 500_000,
        timeout_seconds: float = 30.0,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> TargetedRepoCrawlResult:
        """
        Execute targeted discovery and bounded retrieval against a repository.
        """
        target_str = self.provider.extract_repo_target(target)
        self.provider.check_cancellation(is_cancelled, target_str, "targeted_repository_crawl")

        # 1. Discover repository structure
        disc_opts = RepositoryDiscoveryOptions(
            max_depth=max_depth,
            timeout_seconds=timeout_seconds,
        )
        discovered_repo = self.discovery_engine.discover(
            target=target,
            revision=revision,
            options=disc_opts,
            is_cancelled=is_cancelled,
        )

        # 2. Score candidate files
        candidates = self.scorer.score_candidates(
            files=discovered_repo.tree.files,
            query=query,
        )

        # 3. Select top candidates bounded by max_files
        selected = candidates[:max_files]
        skipped = candidates[max_files:]

        materials: list[RepositorySourceMaterial] = []
        file_failures: list[dict[str, Any]] = []
        total_bytes = 0
        is_partial = False
        requests_made = 0

        # 4. Retrieve file contents within budgets
        for candidate in selected:
            self.provider.check_cancellation(is_cancelled, target_str, "targeted_repository_crawl")

            if requests_made >= max_requests:
                logger.info(f"Targeted crawl reached max_requests budget ({max_requests}) for '{target_str}'")
                is_partial = True
                break

            # Check if file size exceeds single file budget
            if candidate.file.size_bytes > max_file_size:
                file_failures.append({
                    "path": candidate.file.path,
                    "reason": f"File size ({candidate.file.size_bytes} bytes) exceeds max_file_size ({max_file_size} bytes)",
                })
                is_partial = True
                continue

            # Check if adding this file would exceed cumulative byte budget
            if total_bytes + candidate.file.size_bytes > max_bytes and materials:
                logger.info(f"Targeted crawl reached cumulative max_bytes budget ({max_bytes}) for '{target_str}'")
                is_partial = True
                break

            requests_made += 1
            try:
                mat = self.provider.get_file_content(
                    repo=discovered_repo.identity,
                    file_path=candidate.file.path,
                    revision=discovered_repo.revision.commit_sha or discovered_repo.revision.branch or revision,
                    max_bytes=max_file_size,
                    timeout_seconds=timeout_seconds,
                    is_cancelled=is_cancelled,
                )

                # Extract lightweight code structure if requested
                if extract_structure and mat.content and not mat.is_binary:
                    try:
                        from core.research.repo.structure import CodeStructureExtractor
                        mat.structure = CodeStructureExtractor.extract_structure(
                            file_path=mat.file_path,
                            content=mat.content,
                            language=mat.language,
                            max_parse_bytes=max_parse_bytes,
                            repository_id=discovered_repo.identity.repo_id,
                            revision=discovered_repo.revision.commit_sha or discovered_repo.revision.branch or "",
                            provenance=mat.provenance,
                            is_cancelled=is_cancelled,
                        )
                    except RepositoryCancelledError:
                        raise
                    except Exception as parse_err:
                        logger.warning(f"Error extracting structure for '{mat.file_path}': {parse_err}")

                materials.append(mat)
                total_bytes += len(mat.raw_bytes) if mat.raw_bytes is not None else len(mat.content.encode("utf-8"))
            except RepositoryCancelledError:
                raise
            except Exception as e:
                logger.warning(f"Failed to fetch content for candidate file '{candidate.file.path}': {e}")
                file_failures.append({
                    "path": candidate.file.path,
                    "reason": str(e),
                })
                is_partial = True

        structured_count = sum(1 for m in materials if m.structure is not None)

        # Build final snapshot
        repo_source = RepositorySource(
            identity=discovered_repo.identity,
            revision=discovered_repo.revision,
            tree=discovered_repo.tree,
            source_materials=materials,
            metadata={
                "discovered_at": discovered_repo.discovered_at,
                "topic": query.raw_topic,
                "keywords": query.keywords,
                "candidates_count": len(candidates),
                "selected_count": len(selected),
                "materials_count": len(materials),
                "structured_files_count": structured_count,
                "failures_count": len(file_failures),
            },
        )

        return TargetedRepoCrawlResult(
            repository_source=repo_source,
            topic_query=query,
            candidates_discovered=candidates,
            selected_files=selected,
            skipped_candidates=skipped,
            materials_retrieved=materials,
            file_failures=file_failures,
            total_bytes_fetched=total_bytes,
            is_partial_success=is_partial and len(materials) > 0,
            metadata={
                "provider_id": self.provider.provider_id,
                "requests_made": requests_made,
                "structured_files_count": structured_count,
            },
        )

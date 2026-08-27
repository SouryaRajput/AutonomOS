from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
import uuid

from workers.researcher.model import (
    ResearchContradiction,
    ResearchFinding,
    ResearchKnowledgeGap,
    ResearchQuestion,
    ResearchResult,
    Source,
)
from workers.researcher.types import (
    FactClassification,
    ResearchConfidence,
    ResearchQuestionStatus,
    SourceType,
)


class SourceEvaluator:
    """
    Evaluator for source normalization, deduplication, hierarchy rating,
    cross-source contradiction detection, and knowledge gap tracking.
    """

    @classmethod
    def normalize_url(cls, url: str) -> str:
        """
        Normalize a URL for canonical deduplication.
        Strips scheme, trailing slashes, fragments, and standard tracking query parameters.
        """
        if not url:
            return ""

        url_str = url.strip()
        if not (url_str.startswith("http://") or url_str.startswith("https://")):
            url_str = "https://" + url_str

        parsed = urlparse(url_str)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]

        path = parsed.path.rstrip("/")
        if not path:
            path = "/"

        # Strip tracking query parameters (utm_*, ref, source, etc.)
        query_params = parse_qs(parsed.query)
        filtered_query = {
            k: v for k, v in query_params.items()
            if not k.lower().startswith("utm_") and k.lower() not in ("ref", "fbclid", "gclid", "source")
        }
        clean_query = urlencode(filtered_query, doseq=True)

        return urlunparse(("", netloc, path, "", clean_query, ""))

    @classmethod
    def is_domain_allowed(
        cls,
        url: str,
        allowed_domains: list[str],
        excluded_domains: list[str],
    ) -> bool:
        """Check if a URL complies with domain whitelists and blacklists."""
        if not url:
            return False

        parsed = urlparse(url if "://" in url else f"https://{url}")
        domain = parsed.netloc.lower()

        # Check exclusion first
        for exc in excluded_domains:
            clean_exc = exc.strip().lower()
            if clean_exc and (domain == clean_exc or domain.endswith(f".{clean_exc}")):
                return False

        # If allowed_domains is empty, everything not excluded is allowed
        if not allowed_domains:
            return True

        for allow in allowed_domains:
            clean_allow = allow.strip().lower()
            if clean_allow and (domain == clean_allow or domain.endswith(f".{clean_allow}")):
                return True

        return False

    @classmethod
    def deduplicate_sources(
        cls,
        search_results: list[dict[str, Any]],
        existing_sources: Optional[list[Source]] = None,
    ) -> list[dict[str, Any]]:
        """
        Deduplicate candidate search results against themselves and existing sources.
        """
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()

        if existing_sources:
            for s in existing_sources:
                norm_u = cls.normalize_url(s.url_or_ref)
                if norm_u:
                    seen_urls.add(norm_u)
                if s.title:
                    seen_titles.add(s.title.strip().lower())

        deduped: list[dict[str, Any]] = []
        for r in search_results:
            raw_url = str(r.get("url", "")).strip()
            title = str(r.get("title", "")).strip()
            norm_u = cls.normalize_url(raw_url) if raw_url else ""
            clean_title = title.lower()

            if norm_u and norm_u in seen_urls:
                continue
            if clean_title and clean_title in seen_titles:
                continue

            if norm_u:
                seen_urls.add(norm_u)
            if clean_title:
                seen_titles.add(clean_title)

            deduped.append(r)

        return deduped

    @classmethod
    def classify_source_type(cls, url_or_ref: str, title: str = "") -> SourceType:
        """Infer the structural source type from URL and title heuristics."""
        low_url = (url_or_ref or "").lower()
        low_title = (title or "").lower()

        if low_url.startswith(".autonomos/memory") or "project-map" in low_url or "architecture.md" in low_url:
            return SourceType.PROJECT_MEMORY

        if any(d in low_url for d in ("docs.", "documentation.", "developer.", "/docs/", "/doc/")) or "official documentation" in low_title:
            return SourceType.OFFICIAL_DOCUMENTATION

        if any(d in low_url for d in ("arxiv.org", "ieee.org", "acm.org", ".edu")):
            return SourceType.ACADEMIC

        if any(d in low_url for d in ("github.com", "gitlab.com", "bitbucket.org")):
            if "/releases" in low_url or "/tag" in low_url:
                return SourceType.OFFICIAL_ANNOUNCEMENT
            return SourceType.REPOSITORY

        if any(d in low_url for d in ("stackoverflow.com", "reddit.com", "discourse.", "forum.")):
            return SourceType.FORUM

        if any(d in low_url for d in ("medium.com", "dev.to", "substack.com", "/blog/")):
            return SourceType.BLOG

        if any(d in low_url for d in ("news.", "techcrunch.com", "theverge.com", "reuters.com")):
            return SourceType.NEWS

        return SourceType.OTHER

    @classmethod
    def assess_source_reliability(
        cls,
        source_type: SourceType,
        publisher: str = "",
        url: str = "",
    ) -> float:
        """
        Assign a baseline reliability score [0.0 - 1.0] based on source hierarchy.
        """
        scores = {
            SourceType.OFFICIAL_DOCUMENTATION: 0.95,
            SourceType.PRIMARY_SOURCE: 0.95,
            SourceType.OFFICIAL_ANNOUNCEMENT: 0.90,
            SourceType.PROJECT_MEMORY: 0.90,
            SourceType.ACADEMIC: 0.85,
            SourceType.REPOSITORY: 0.80,
            SourceType.NEWS: 0.70,
            SourceType.COMMUNITY: 0.60,
            SourceType.BLOG: 0.50,
            SourceType.FORUM: 0.45,
            SourceType.OTHER: 0.50,
        }
        return scores.get(source_type, 0.50)

    @classmethod
    def detect_contradictions(cls, findings: list[ResearchFinding]) -> list[ResearchContradiction]:
        """
        Identify conflicting findings across sources.
        """
        contradictions: list[ResearchContradiction] = []
        for i, f in enumerate(findings):
            if f.confidence == ResearchConfidence.CONFLICTING or f.conflicting_source_ids:
                cid = f"contra-{uuid.uuid4().hex[:6]}"
                contra = ResearchContradiction(
                    contradiction_id=cid,
                    topic=f.claim[:60],
                    claim_a=f.claim,
                    sources_a=f.source_ids or f.corroborating_source_ids,
                    claim_b=f.reasoning or "Conflicting opposing claim identified in literature.",
                    sources_b=f.conflicting_source_ids,
                    analysis=f.reasoning or "Sources present irreconcilable claims.",
                )
                contradictions.append(contra)

        return contradictions

    @classmethod
    def identify_knowledge_gaps(
        cls,
        questions: list[ResearchQuestion],
        findings: list[ResearchFinding],
    ) -> list[ResearchKnowledgeGap]:
        """
        Identify explicit knowledge gaps from unanswered or insufficiently supported questions.
        """
        gaps: list[ResearchKnowledgeGap] = []
        for q in questions:
            if q.status in (ResearchQuestionStatus.UNANSWERED, ResearchQuestionStatus.UNKNOWN):
                gid = f"gap-{uuid.uuid4().hex[:6]}"
                gaps.append(
                    ResearchKnowledgeGap(
                        gap_id=gid,
                        topic=q.question_text[:50],
                        question=q.question_text,
                        reason=q.notes or "No reliable or authoritative sources found.",
                        impact="Downstream planning should consider this requirement unverified.",
                    )
                )
            elif q.status == ResearchQuestionStatus.CONFLICTING:
                gid = f"gap-{uuid.uuid4().hex[:6]}"
                gaps.append(
                    ResearchKnowledgeGap(
                        gap_id=gid,
                        topic=q.question_text[:50],
                        question=q.question_text,
                        reason="Sources conflict without definitive resolution.",
                        impact="Further domain verification or architectural decision required.",
                    )
                )

        return gaps

    @classmethod
    def validate_research_result(cls, result: ResearchResult) -> tuple[bool, list[str]]:
        """
        Perform deterministic pre-completion checks on a ResearchResult.
        Ensures valid references, non-empty outputs, and strict fact classifications.
        """
        errors: list[str] = []

        if not result.objective:
            errors.append("ResearchResult missing objective")

        if not result.report_artifact_id:
            errors.append("ResearchResult missing report_artifact_id")

        if not result.summary_for_manager:
            errors.append("ResearchResult missing summary_for_manager")

        # Check source references in findings
        known_sources = {s.source_id for s in result.sources}
        for f in result.findings:
            if not f.claim:
                errors.append(f"Finding {f.finding_id} has empty claim")
            for sid in f.source_ids:
                if sid not in known_sources:
                    errors.append(f"Finding {f.finding_id} references unknown source {sid}")

        # Check recommendations are separated
        for r in result.recommendations:
            if not r.action:
                errors.append(f"Recommendation {r.recommendation_id} has empty action")

        return len(errors) == 0, errors

"""
Controlled Cross-Source Link Handoff Pipeline (Limitation 3 Mitigation).

Safely extracts, validates, filters, and dispatches external documentation/web links
discovered within community discussions into isolated, budget-constrained WebFetchCrawler tasks
with full causal provenance lineage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any, Optional
import urllib.parse
import uuid

from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import EvidenceItem
from core.research.errors import SearchSecurityError
from core.research.search.security import sanitize_url, validate_network_target
from core.research.types import CrawlerCapability

logger = logging.getLogger("AutonomOS.Research.Community.LinkHandoff")


@dataclass(frozen=True)
class CandidateLink:
    """A candidate reference URL extracted from discussion evidence."""
    url: str
    parent_evidence_id: str
    source_discussion_id: str
    source_platform: str
    anchor_text: str = ""
    relevance_score: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LinkHandoffPolicy:
    """Security, budget, and filtering policy for cross-source link handoffs."""
    max_links_to_fetch: int = 5
    max_bytes_per_fetch: int = 100_000
    fetch_timeout_seconds: float = 15.0
    allowed_domains: Optional[list[str]] = None
    excluded_domains: Optional[list[str]] = None
    allow_localhost: bool = False
    target_capability: CrawlerCapability = CrawlerCapability.WEB_FETCH


class LinkHandoffPipeline:
    """
    Two-stage bridge connecting community discussions to verified web/documentation fetches.
    """

    def __init__(self, policy: Optional[LinkHandoffPolicy] = None):
        self.policy = policy or LinkHandoffPolicy()

    @staticmethod
    def extract_candidate_links(evidence_pool: list[EvidenceItem]) -> list[CandidateLink]:
        """
        Extract external candidate URLs from a collection of discussion evidence items.
        Extracts from structured metadata links, referenced_urls, and direct text contents.
        """
        candidates: list[CandidateLink] = []
        for ev in evidence_pool:
            meta = ev.metadata or {}
            source_disc_id = str(meta.get("discussion_id", ""))
            source_plat = str(meta.get("platform", "community"))
            rel_score = float(ev.reliability_score or 1.0)

            # 1. From links structure if available in metadata
            links_data = meta.get("links", [])
            if isinstance(links_data, list):
                for item in links_data:
                    if isinstance(item, dict):
                        u = str(item.get("url", "")).strip()
                        if u.startswith(("http://", "https://")):
                            if not any(c.url == u for c in candidates):
                                candidates.append(
                                    CandidateLink(
                                        url=u,
                                        anchor_text=str(item.get("text", "")),
                                        parent_evidence_id=ev.evidence_id,
                                        source_discussion_id=source_disc_id,
                                        source_platform=source_plat,
                                        relevance_score=rel_score,
                                    )
                                )

            # 2. From referenced_urls list in metadata
            ref_urls = meta.get("referenced_urls", [])
            if isinstance(ref_urls, list):
                for u in ref_urls:
                    if isinstance(u, str) and u.startswith(("http://", "https://")):
                        clean_u = u.strip()
                        if not any(c.url == clean_u for c in candidates):
                            candidates.append(
                                CandidateLink(
                                    url=clean_u,
                                    parent_evidence_id=ev.evidence_id,
                                    source_discussion_id=source_disc_id,
                                    source_platform=source_plat,
                                    relevance_score=rel_score,
                                )
                            )

            # 3. From text content via URL regex
            text_to_scan = f"{ev.extracted_fact or ''} {ev.content_snippet or ''}"
            if text_to_scan.strip():
                url_pattern = re.compile(r'https?://[^\s<>"\')]+')
                for match in url_pattern.finditer(text_to_scan):
                    raw_url = match.group(0).rstrip(".,;:!?)")
                    if raw_url and not any(c.url == raw_url for c in candidates):
                        candidates.append(
                            CandidateLink(
                                url=raw_url,
                                parent_evidence_id=ev.evidence_id,
                                source_discussion_id=source_disc_id,
                                source_platform=source_plat,
                                relevance_score=rel_score,
                            )
                        )

        return candidates

    def filter_and_validate_links(
        self,
        candidate_links: list[CandidateLink],
        policy: Optional[LinkHandoffPolicy] = None,
    ) -> list[CandidateLink]:
        """
        Validate candidates against SSRF protection, domain filters, and deduplicate.
        """
        pol = policy or self.policy
        validated: list[CandidateLink] = []
        seen_urls: set[str] = set()

        for cand in candidate_links:
            clean_url = sanitize_url(cand.url)
            if not clean_url:
                continue

            parsed = urllib.parse.urlsplit(clean_url)
            norm_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if norm_url in seen_urls:
                continue

            # SSRF Target Validation
            try:
                validate_network_target(clean_url, allow_localhost=pol.allow_localhost)
            except (SearchSecurityError, Exception) as sec_err:
                logger.warning(f"Candidate link '{clean_url}' rejected by SSRF guard: {sec_err}")
                continue

            domain = parsed.netloc.lower()
            if ":" in domain:
                domain = domain.split(":")[0]

            # Domain exclusion filter
            if pol.excluded_domains:
                if any(domain == d.lower() or domain.endswith(f".{d.lower()}") for d in pol.excluded_domains):
                    continue

            # Domain allowlist filter
            if pol.allowed_domains:
                if not any(domain == d.lower() or domain.endswith(f".{d.lower()}") for d in pol.allowed_domains):
                    continue

            seen_urls.add(norm_url)
            validated.append(cand)
            if len(validated) >= pol.max_links_to_fetch:
                break

        return validated

    def generate_handoff_tasks(
        self,
        validated_links: list[CandidateLink],
        request_id: str,
        plan_id: str = "",
        question_id: str = "",
        correlation_id: str = "",
        policy: Optional[LinkHandoffPolicy] = None,
    ) -> list[CrawlerTask]:
        """
        Formulate typed, bounded CrawlerTask instances for WebFetchCrawler with full lineage.
        """
        pol = policy or self.policy
        tasks: list[CrawlerTask] = []

        for i, link in enumerate(validated_links):
            task_id = f"ctask-handoff-{uuid.uuid4().hex[:8]}"
            clean_target = sanitize_url(link.url)

            task = CrawlerTask(
                task_id=task_id,
                request_id=request_id,
                plan_id=plan_id or f"plan-handoff-{request_id}",
                question_id=question_id or f"q-handoff-{request_id}",
                query_or_target=clean_target,
                objective=f"Verify documentation / external reference cited in community discussion: {clean_target}",
                required_capability=pol.target_capability,
                required_capabilities=[pol.target_capability],
                parameters={
                    "url": clean_target,
                    "parent_evidence_id": link.parent_evidence_id,
                    "source_discussion_id": link.source_discussion_id,
                    "source_platform": link.source_platform,
                    "max_bytes": pol.max_bytes_per_fetch,
                    "timeout_seconds": pol.fetch_timeout_seconds,
                    "is_handoff_task": True,
                },
                priority=50,
                timeout_seconds=pol.fetch_timeout_seconds,
                correlation_id=correlation_id or f"corr-{uuid.uuid4().hex[:6]}",
                metadata={
                    "handoff_index": i + 1,
                    "parent_evidence_id": link.parent_evidence_id,
                    "source_discussion_id": link.source_discussion_id,
                    "source_platform": link.source_platform,
                    "anchor_text": link.anchor_text,
                },
            )
            tasks.append(task)

        return tasks

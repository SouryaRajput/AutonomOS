"""
Integration Test Suite for Advanced Community Crawler Features:
1. Dynamic Subtree Expansion & Megathread Deep-Dive (Limitation 2 Mitigation)
2. Controlled Cross-Source Link Handoff Pipeline (Limitation 3 Mitigation)
3. Stack Exchange Discussion Provider Integration
"""
from __future__ import annotations

import gzip
import json
import logging
import unittest
import urllib.request
import uuid

from core.models import Task, WorkerManifest, WorkerOutput
from core.research.community.fake_provider import FakeDiscussionProvider
from core.research.community.link_handoff import (
    CandidateLink,
    LinkHandoffPipeline,
    LinkHandoffPolicy,
)
from core.research.community.models import (
    AccessStatus,
    CommunityContext,
    CommunityPlatform,
    Discussion,
    DiscussionPost,
    DiscussionStatus,
    EngagementMetrics,
)
from core.research.community.providers.stack_exchange import StackExchangeDiscussionProvider
from core.research.community.retriever import DiscussionThreadRetriever
from core.research.contracts.crawler_report import CrawlerReportStatus
from core.research.contracts.crawler_task import CrawlerTask
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.crawler.community import CommunityCrawler
from core.research.crawler.web_fetch import WebFetchCrawler
from core.research.types import CrawlerCapability, CrawlerStatus, FactClassification


class TestAdvancedCommunityFeatures(unittest.TestCase):
    """Integration test suite covering subtree expansion, link handoff, and Stack Exchange provider."""

    def setUp(self):
        self.request_id = "req-adv-comm-001"
        self.plan_id = "plan-adv-comm-001"
        self.question_id = "q-adv-comm-001"

    # =========================================================================
    # Test 1: Subtree Expansion in CommunityCrawler
    # =========================================================================
    def test_community_crawler_subtree_expansion(self):
        """Verify CommunityCrawler expands subtrees on demand when expand_subtrees=True."""
        provider = FakeDiscussionProvider()
        
        # Build a deep discussion in the fake provider
        ctx = CommunityContext(
            platform=CommunityPlatform.REDDIT,
            community_id="r/architecture",
            community_name="Architecture",
            source_url="https://reddit.com/r/architecture",
        )
        disc = Discussion(
            discussion_id="disc-deep-tree",
            community_context=ctx,
            title="Deep architectural discussion on microkernels",
            url="https://reddit.com/r/architecture/comments/deep_tree",
            status=DiscussionStatus.OPEN,
        )
        disc.add_post(
            DiscussionPost(
                post_id="post-root",
                discussion_id="disc-deep-tree",
                content="What are the key trade-offs of microkernels?",
                is_root=True,
                depth=0,
            )
        )
        disc.add_post(
            DiscussionPost(
                post_id="comm-1",
                discussion_id="disc-deep-tree",
                content="IPC latency is the primary challenge.",
                parent_id="post-root",
                depth=1,
                engagement=EngagementMetrics(score=100, upvotes=100),
            )
        )
        # Deep descendant posts
        disc.add_post(
            DiscussionPost(
                post_id="comm-1-1",
                discussion_id="disc-deep-tree",
                content="Modern L4 microkernels achieve sub-microsecond IPC.",
                parent_id="comm-1",
                depth=2,
                engagement=EngagementMetrics(score=80, upvotes=80),
            )
        )
        disc.add_post(
            DiscussionPost(
                post_id="comm-1-1-1",
                discussion_id="disc-deep-tree",
                content="seL4 uses formal verification to guarantee integrity.",
                parent_id="comm-1-1",
                depth=3,
                engagement=EngagementMetrics(score=50, upvotes=50),
            )
        )
        provider.add_discussion(disc)

        crawler = CommunityCrawler(
            crawler_id="crawler-subtree-exp",
            provider=provider,
        )

        task = CrawlerTask(
            task_id="ctask-subtree-01",
            request_id=self.request_id,
            plan_id=self.plan_id,
            question_id=self.question_id,
            query_or_target="microkernels",
            required_capability=CrawlerCapability.COMMUNITY_CRAWL,
            parameters={
                "expand_subtrees": True,
                "subtree_root_id": "comm-1",
                "max_comments": 10,
                "max_depth": 5,
            },
        )

        report = crawler.execute(task)

        self.assertEqual(report.status, CrawlerReportStatus.SUCCESS)
        self.assertGreaterEqual(len(report.extracted_evidence), 1)
        # Verify that seL4 comment (deep descendant) is captured in the extracted evidence
        all_text = " ".join(f"{e.extracted_fact} {e.content_snippet}" for e in report.extracted_evidence)
        self.assertIn("seL4", all_text)
        self.assertIn("Modern L4 microkernels", all_text)

    # =========================================================================
    # Test 2: Stack Exchange Provider with CommunityCrawler
    # =========================================================================
    def test_stack_exchange_provider_with_community_crawler(self):
        """Verify CommunityCrawler using StackExchangeDiscussionProvider with transport mock."""
        search_payload = {
            "items": [
                {
                    "question_id": 424242,
                    "title": "How does epoll differ from select in Linux?",
                    "body": "Can someone explain the performance difference between epoll and select?",
                    "owner": {"display_name": "kernel_enthusiast"},
                    "score": 250,
                    "answer_count": 1,
                    "is_answered": True,
                    "creation_date": 1750000000,
                    "link": "https://stackoverflow.com/questions/424242",
                    "tags": ["linux", "networking", "c"],
                }
            ]
        }
        question_payload = {
            "items": [
                {
                    "question_id": 424242,
                    "title": "How does epoll differ from select in Linux?",
                    "body": "Can someone explain the performance difference between epoll and select?",
                    "owner": {"display_name": "kernel_enthusiast"},
                    "score": 250,
                    "answer_count": 1,
                    "is_answered": True,
                    "creation_date": 1750000000,
                    "link": "https://stackoverflow.com/questions/424242",
                    "tags": ["linux", "networking", "c"],
                }
            ]
        }
        answers_payload = {
            "items": [
                {
                    "answer_id": 424243,
                    "body": "epoll uses O(1) event notification via kernel callback trees instead of O(N) linear scanning.",
                    "owner": {"display_name": "systems_expert"},
                    "score": 380,
                    "is_accepted": True,
                    "creation_date": 1750000500,
                }
            ]
        }
        comments_payload = {"items": []}

        def mock_transport(req: urllib.request.Request, timeout: float) -> bytes:
            url = req.full_url
            if "/search" in url:
                data = search_payload
            elif "/answers" in url:
                data = answers_payload
            elif "/comments" in url:
                data = comments_payload
            elif "/questions/424242" in url:
                data = question_payload
            else:
                data = {"items": []}
            return gzip.compress(json.dumps(data).encode("utf-8"))

        se_provider = StackExchangeDiscussionProvider(
            default_site="stackoverflow",
            transport_fn=mock_transport,
        )

        crawler = CommunityCrawler(
            crawler_id="crawler-se-integration",
            provider=se_provider,
        )

        task = CrawlerTask(
            task_id="ctask-se-01",
            request_id=self.request_id,
            plan_id=self.plan_id,
            question_id=self.question_id,
            query_or_target="epoll differ select",
            required_capability=CrawlerCapability.COMMUNITY_CRAWL,
            parameters={
                "community": "stackoverflow",
                "include_replies": True,
            },
        )

        report = crawler.execute(task)

        self.assertEqual(report.status, CrawlerReportStatus.SUCCESS)
        self.assertGreaterEqual(len(report.extracted_evidence), 1)

        # Verify security invariants on extracted evidence
        for ev in report.extracted_evidence:
            # External discussion is strictly untrusted claim
            self.assertEqual(ev.classification, FactClassification.SOURCE_CLAIM)
            self.assertIn("stackoverflow", ev.provenance.source_ref)

    # =========================================================================
    # Test 3: Link Handoff Pipeline End-to-End
    # =========================================================================
    def test_link_handoff_pipeline_end_to_end(self):
        """
        Verify LinkHandoffPipeline:
        1. Extracts external documentation links from discussion evidence
        2. Filters with SSRF and domain policies
        3. Formulates child CrawlerTasks for WebFetchCrawler
        4. Maintains causal parent_evidence_id provenance lineage
        """
        pipeline = LinkHandoffPipeline()

        # Evidence containing valid doc link and SSRF-forbidden link
        evidence = [
            EvidenceItem(
                evidence_id="ev-disc-101",
                provenance=EvidenceProvenance(
                    request_id=self.request_id,
                    crawler_task_id="ctask-1",
                    crawler_id="crawler-comm-1",
                    source_ref="https://reddit.com/r/python",
                ),
                extracted_fact=(
                    "For official asyncio specifications, refer to https://docs.python.org/3/library/asyncio.html . "
                    "Also do not miss the internal dashboard at http://169.254.169.254/latest/meta-data/"
                ),
                classification=FactClassification.SOURCE_CLAIM,
                metadata={
                    "discussion_id": "disc-py-async",
                    "platform": "reddit",
                },
            )
        ]

        # 1. Extract candidates
        candidates = pipeline.extract_candidate_links(evidence)
        self.assertEqual(len(candidates), 2)

        # 2. Filter & Validate with Policy
        policy = LinkHandoffPolicy(
            allowed_domains=["docs.python.org"],
            max_links_to_fetch=3,
        )
        validated_links = pipeline.filter_and_validate_links(candidates, policy=policy)
        
        # SSRF forbidden 169.254.169.254 rejected, only docs.python.org kept
        self.assertEqual(len(validated_links), 1)
        self.assertEqual(validated_links[0].url, "https://docs.python.org/3/library/asyncio.html")
        self.assertEqual(validated_links[0].parent_evidence_id, "ev-disc-101")

        # 3. Generate child WebFetchCrawler tasks
        handoff_tasks = pipeline.generate_handoff_tasks(
            validated_links=validated_links,
            request_id=self.request_id,
            plan_id=self.plan_id,
            question_id=self.question_id,
            policy=policy,
        )

        self.assertEqual(len(handoff_tasks), 1)
        handoff_task = handoff_tasks[0]
        self.assertEqual(handoff_task.required_capability, CrawlerCapability.WEB_FETCH)
        self.assertEqual(handoff_task.query_or_target, "https://docs.python.org/3/library/asyncio.html")
        self.assertEqual(handoff_task.parameters["parent_evidence_id"], "ev-disc-101")
        self.assertEqual(handoff_task.parameters["source_discussion_id"], "disc-py-async")
        self.assertEqual(handoff_task.parameters["source_platform"], "reddit")
        self.assertTrue(handoff_task.parameters["is_handoff_task"])

        # 4. Verify WebFetchCrawler compatibility with generated handoff task
        web_crawler = WebFetchCrawler(crawler_id="crawler-web-handoff")
        self.assertTrue(web_crawler.has_capability(CrawlerCapability.WEB_FETCH))
        self.assertEqual(handoff_task.priority, 50)


if __name__ == "__main__":
    unittest.main()

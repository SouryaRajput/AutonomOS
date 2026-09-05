"""
Unit tests for Controlled Cross-Source Link Handoff Pipeline (Limitation 3 Mitigation).
"""
import unittest

from core.research.community.link_handoff import (
    CandidateLink,
    LinkHandoffPipeline,
    LinkHandoffPolicy,
)
from core.research.contracts.evidence import EvidenceItem, EvidenceProvenance
from core.research.types import CrawlerCapability, FactClassification


class TestLinkHandoffPipeline(unittest.TestCase):

    def setUp(self):
        self.pipeline = LinkHandoffPipeline()
        self.prov = EvidenceProvenance(
            request_id="req-123",
            crawler_task_id="ctask-101",
            crawler_id="crawler.community.1",
            question_id="q-1",
        )

    def test_extract_candidate_links(self):
        ev1 = EvidenceItem(
            evidence_id="ev-101",
            provenance=self.prov,
            extracted_fact="Asyncio TaskGroup simplifies exception handling.",
            classification=FactClassification.SOURCE_CLAIM,
            metadata={
                "discussion_id": "disc-42",
                "platform": "reddit",
                "referenced_urls": [
                    "https://docs.python.org/3/library/asyncio-task.html#task-groups",
                    "https://github.com/python/cpython/issues/123",
                ],
                "links": [
                    {"url": "https://docs.python.org/3/library/asyncio-task.html#task-groups", "text": "Python Docs"},
                    {"url": "https://peps.python.org/pep-0654/", "text": "PEP 654"},
                ],
            },
        )

        candidates = self.pipeline.extract_candidate_links([ev1])
        self.assertGreaterEqual(len(candidates), 3)

        urls = [c.url for c in candidates]
        self.assertIn("https://docs.python.org/3/library/asyncio-task.html#task-groups", urls)
        self.assertIn("https://peps.python.org/pep-0654/", urls)

        # Check lineage metadata preserved
        pep_cand = next(c for c in candidates if "pep-0654" in c.url)
        self.assertEqual(pep_cand.parent_evidence_id, "ev-101")
        self.assertEqual(pep_cand.source_discussion_id, "disc-42")
        self.assertEqual(pep_cand.source_platform, "reddit")

    def test_filter_and_validate_links_ssrf_rejection(self):
        candidates = [
            CandidateLink(
                url="http://169.254.169.254/latest/meta-data/",
                parent_evidence_id="ev-1",
                source_discussion_id="disc-1",
                source_platform="reddit",
            ),
            CandidateLink(
                url="http://127.0.0.1:8080/admin",
                parent_evidence_id="ev-1",
                source_discussion_id="disc-1",
                source_platform="reddit",
            ),
            CandidateLink(
                url="https://docs.python.org/3/",
                parent_evidence_id="ev-1",
                source_discussion_id="disc-1",
                source_platform="reddit",
            ),
        ]

        validated = self.pipeline.filter_and_validate_links(candidates)
        # SSRF targets must be strictly rejected
        self.assertEqual(len(validated), 1)
        self.assertEqual(validated[0].url, "https://docs.python.org/3/")

    def test_domain_filtering_and_deduplication(self):
        candidates = [
            CandidateLink(
                url="https://docs.python.org/3/library/asyncio.html",
                parent_evidence_id="ev-1",
                source_discussion_id="disc-1",
                source_platform="reddit",
            ),
            CandidateLink(
                url="https://docs.python.org/3/library/asyncio.html",  # duplicate
                parent_evidence_id="ev-2",
                source_discussion_id="disc-2",
                source_platform="reddit",
            ),
            CandidateLink(
                url="https://evil-tracker.com/pixel",
                parent_evidence_id="ev-3",
                source_discussion_id="disc-3",
                source_platform="reddit",
            ),
        ]

        policy = LinkHandoffPolicy(
            allowed_domains=["docs.python.org"],
            excluded_domains=["evil-tracker.com"],
        )

        validated = self.pipeline.filter_and_validate_links(candidates, policy=policy)
        self.assertEqual(len(validated), 1)
        self.assertEqual(validated[0].url, "https://docs.python.org/3/library/asyncio.html")

    def test_generate_handoff_tasks(self):
        validated = [
            CandidateLink(
                url="https://docs.python.org/3/library/asyncio.html",
                parent_evidence_id="ev-101",
                source_discussion_id="disc-42",
                source_platform="reddit",
                anchor_text="Python Asyncio Docs",
            )
        ]

        tasks = self.pipeline.generate_handoff_tasks(
            validated_links=validated,
            request_id="req-123",
            correlation_id="corr-789",
        )

        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task.required_capabilities, [CrawlerCapability.WEB_FETCH])
        self.assertEqual(task.target, "https://docs.python.org/3/library/asyncio.html")
        self.assertEqual(task.parameters["parent_evidence_id"], "ev-101")
        self.assertEqual(task.parameters["source_discussion_id"], "disc-42")
        self.assertTrue(task.parameters["is_handoff_task"])
        self.assertEqual(task.metadata["parent_evidence_id"], "ev-101")


if __name__ == "__main__":
    unittest.main()

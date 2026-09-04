"""
Unit tests for Discussion Provider Abstraction and FakeDiscussionProvider (Phase 1 / Part 6 / Step 2).
"""
import unittest

from core.research.community.fake_provider import FakeDiscussionProvider
from core.research.community.models import (
    AccessStatus,
    CommunityContext,
    CommunityPlatform,
    Discussion,
    DiscussionPost,
    DiscussionStatus,
    EngagementMetrics,
    ThreadOrdering,
)
from core.research.community.provider import (
    DiscussionCommentsParams,
    DiscussionFetchLimits,
    DiscussionRetrievalParams,
    DiscussionSearchParams,
    DiscussionSearchResponse,
)
from core.research.errors import (
    CommunityAuthenticationError,
    CommunityCancelledError,
    CommunityDiscussionNotFoundError,
    CommunityProviderError,
    CommunityRateLimitError,
    CommunityTimeoutError,
    CommunityValidationError,
)


class TestDiscussionProviderContracts(unittest.TestCase):
    def test_fetch_limits_defaults_and_validation(self):
        limits = DiscussionFetchLimits()
        self.assertEqual(limits.max_posts_per_discussion, 200)
        self.assertEqual(limits.max_depth, 10)
        self.assertEqual(limits.max_search_results, 50)
        self.assertEqual(limits.max_content_bytes, 500_000)
        self.assertEqual(limits.timeout_seconds, 30.0)

        # Validation of invalid values
        with self.assertRaises(CommunityValidationError):
            DiscussionFetchLimits(max_posts_per_discussion=0)

        with self.assertRaises(CommunityValidationError):
            DiscussionFetchLimits(max_depth=-1)

        with self.assertRaises(CommunityValidationError):
            DiscussionFetchLimits(max_search_results=0)

        with self.assertRaises(CommunityValidationError):
            DiscussionFetchLimits(timeout_seconds=-5.0)

    def test_search_params_validation(self):
        params = DiscussionSearchParams(
            query="python async",
            platform=CommunityPlatform.REDDIT,
            community_id="r/Python",
            limit=5,
        )
        self.assertEqual(params.query, "python async")
        self.assertEqual(params.platform, CommunityPlatform.REDDIT)
        self.assertEqual(params.limit, 5)

        with self.assertRaises(CommunityValidationError):
            DiscussionSearchParams(query="")

        with self.assertRaises(CommunityValidationError):
            DiscussionSearchParams(query="test", limit=0)

        with self.assertRaises(CommunityValidationError):
            DiscussionSearchParams(query="test", timeout_seconds=-1.0)

    def test_retrieval_params_validation(self):
        params = DiscussionRetrievalParams(
            discussion_id="disc-123",
            max_comments=20,
            max_depth=3,
        )
        self.assertEqual(params.discussion_id, "disc-123")
        self.assertEqual(params.max_comments, 20)

        with self.assertRaises(CommunityValidationError):
            DiscussionRetrievalParams(discussion_id="")

        with self.assertRaises(CommunityValidationError):
            DiscussionRetrievalParams(discussion_id="d1", max_comments=-5)

    def test_comments_params_validation(self):
        params = DiscussionCommentsParams(
            discussion_id="disc-123",
            parent_id="post-456",
            ordering=ThreadOrdering.THREADED_DFS,
        )
        self.assertEqual(params.parent_id, "post-456")
        self.assertEqual(params.ordering, ThreadOrdering.THREADED_DFS)

        with self.assertRaises(CommunityValidationError):
            DiscussionCommentsParams(discussion_id="")

    def test_search_response_serialization(self):
        resp = DiscussionSearchResponse(
            query="test query",
            results=[],
            total_found=0,
            execution_time_seconds=0.0123,
            provider="test-provider",
            platform=CommunityPlatform.REDDIT,
        )
        d = resp.to_dict()
        self.assertEqual(d["query"], "test query")
        self.assertEqual(d["platform"], "reddit")
        self.assertEqual(d["total_found"], 0)

        resp2 = DiscussionSearchResponse.from_dict(d)
        self.assertEqual(resp2.query, "test query")
        self.assertEqual(resp2.platform, CommunityPlatform.REDDIT)


class TestFakeDiscussionProviderSearch(unittest.TestCase):
    def setUp(self):
        self.provider = FakeDiscussionProvider()

    def test_search_all_discussions_by_keyword(self):
        params = DiscussionSearchParams(query="TaskGroup")
        resp = self.provider.search_discussions(params)
        self.assertGreaterEqual(resp.total_found, 2)
        # Should match both reddit-py-101 and so-python-301
        found_ids = {d.discussion_id for d in resp.results}
        self.assertIn("reddit-py-101", found_ids)
        self.assertIn("so-python-301", found_ids)

    def test_search_with_platform_filter(self):
        params = DiscussionSearchParams(
            query="TaskGroup",
            platform=CommunityPlatform.STACK_EXCHANGE,
        )
        resp = self.provider.search_discussions(params)
        self.assertEqual(resp.total_found, 1)
        self.assertEqual(resp.results[0].discussion_id, "so-python-301")

    def test_search_with_community_filter(self):
        params = DiscussionSearchParams(
            query="Server Components",
            community_id="facebook/react",
        )
        resp = self.provider.search_discussions(params)
        self.assertEqual(resp.total_found, 1)
        self.assertEqual(resp.results[0].discussion_id, "gh-react-201")

    def test_search_with_tag_filter(self):
        params = DiscussionSearchParams(
            query="Server Components",
            tags=["rsc"],
        )
        resp = self.provider.search_discussions(params)
        # Only gh-react-201 has tag 'rsc' and matches Server Components
        self.assertEqual(resp.total_found, 1)
        self.assertEqual(resp.results[0].discussion_id, "gh-react-201")

    def test_search_with_status_filter(self):
        params = DiscussionSearchParams(
            query="Python",
            status=DiscussionStatus.RESOLVED,
        )
        resp = self.provider.search_discussions(params)
        self.assertTrue(all(d.status == DiscussionStatus.RESOLVED for d in resp.results))

    def test_search_with_date_filters(self):
        # All fixtures were created around Aug 2026
        params_after = DiscussionSearchParams(query="async", after_date="2026-08-26T00:00:00Z")
        resp_after = self.provider.search_discussions(params_after)
        self.assertTrue(all(d.created_at >= "2026-08-26T00:00:00Z" for d in resp_after.results))

        params_before = DiscussionSearchParams(query="async", before_date="2026-08-22T00:00:00Z")
        resp_before = self.provider.search_discussions(params_before)
        self.assertTrue(all(d.created_at <= "2026-08-22T00:00:00Z" for d in resp_before.results))

    def test_search_empty_results(self):
        params = DiscussionSearchParams(query="non_existent_unmatched_term_xyz_123")
        resp = self.provider.search_discussions(params)
        self.assertEqual(resp.total_found, 0)
        self.assertEqual(len(resp.results), 0)

    def test_search_excludes_private_communities(self):
        params = DiscussionSearchParams(query="Confidential notes")
        resp = self.provider.search_discussions(params)
        # disc-priv-501 is in a private community, so search must not leak it
        self.assertEqual(resp.total_found, 0)


class TestFakeDiscussionProviderRetrieval(unittest.TestCase):
    def setUp(self):
        self.provider = FakeDiscussionProvider()

    def test_get_discussion_full_thread(self):
        params = DiscussionRetrievalParams(discussion_id="reddit-py-101")
        disc = self.provider.get_discussion(params)

        self.assertEqual(disc.discussion_id, "reddit-py-101")
        self.assertEqual(disc.title, "How to structure a large async Python application with TaskGroup?")
        self.assertEqual(disc.root_post.post_id, "post-root-101")
        self.assertEqual(disc.total_posts(), 4)  # root + 3 replies
        self.assertEqual(disc.total_replies(), 3)
        self.assertEqual(disc.thread_structure.get_thread_depth(), 2)

    def test_get_discussion_by_url(self):
        params = DiscussionRetrievalParams(discussion_id="https://github.com/facebook/react/discussions/201")
        disc = self.provider.get_discussion(params)
        self.assertEqual(disc.discussion_id, "gh-react-201")

    def test_get_discussion_without_comments(self):
        params = DiscussionRetrievalParams(
            discussion_id="reddit-py-101",
            include_comments=False,
        )
        disc = self.provider.get_discussion(params)
        self.assertEqual(disc.total_posts(), 1)
        self.assertEqual(disc.total_replies(), 0)

    def test_get_discussion_bounded_depth_and_comments(self):
        params = DiscussionRetrievalParams(
            discussion_id="reddit-py-101",
            max_depth=1,  # Excludes nested post at depth 2
        )
        disc = self.provider.get_discussion(params)
        self.assertEqual(disc.thread_structure.get_thread_depth(), 1)
        self.assertEqual(disc.total_posts(), 3)  # root + 2 direct replies

    def test_get_discussion_not_found(self):
        params = DiscussionRetrievalParams(discussion_id="non-existent-disc-999")
        with self.assertRaises(CommunityDiscussionNotFoundError):
            self.provider.get_discussion(params)

    def test_get_private_discussion_access_denied(self):
        params = DiscussionRetrievalParams(discussion_id="disc-priv-501")
        with self.assertRaises(CommunityAuthenticationError):
            self.provider.get_discussion(params)


class TestFakeDiscussionProviderComments(unittest.TestCase):
    def setUp(self):
        self.provider = FakeDiscussionProvider()

    def test_get_comments_all(self):
        params = DiscussionCommentsParams(discussion_id="reddit-py-101")
        comments = self.provider.get_comments(params)
        self.assertEqual(len(comments), 3)  # 3 replies excluding root
        post_ids = [c.post_id for c in comments]
        self.assertIn("post-rep-101-1", post_ids)
        self.assertIn("post-nest-101-1", post_ids)
        self.assertIn("post-rep-101-2", post_ids)

    def test_get_comments_by_parent_subtree(self):
        params = DiscussionCommentsParams(
            discussion_id="reddit-py-101",
            parent_id="post-rep-101-1",
        )
        comments = self.provider.get_comments(params)
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].post_id, "post-nest-101-1")

    def test_get_comments_ordering_dfs(self):
        params = DiscussionCommentsParams(
            discussion_id="reddit-py-101",
            ordering=ThreadOrdering.THREADED_DFS,
        )
        comments = self.provider.get_comments(params)
        self.assertEqual(len(comments), 3)

    def test_get_comments_not_found_discussion(self):
        params = DiscussionCommentsParams(discussion_id="missing-disc")
        with self.assertRaises(CommunityDiscussionNotFoundError):
            self.provider.get_comments(params)


class TestCommunityMetadata(unittest.TestCase):
    def setUp(self):
        self.provider = FakeDiscussionProvider()

    def test_get_registered_community_metadata(self):
        meta = self.provider.get_community_metadata("r/Python")
        self.assertEqual(meta.community_id, "r/Python")
        self.assertEqual(meta.platform, CommunityPlatform.REDDIT)
        self.assertEqual(meta.access_status, AccessStatus.PUBLIC)

    def test_get_private_community_metadata_denied(self):
        with self.assertRaises(CommunityAuthenticationError):
            self.provider.get_community_metadata("r/internal_confidential")

    def test_get_unregistered_public_community_fallback(self):
        meta = self.provider.get_community_metadata("r/golang", platform=CommunityPlatform.REDDIT)
        self.assertEqual(meta.community_id, "r/golang")
        self.assertEqual(meta.platform, CommunityPlatform.REDDIT)


class TestFaultInjectionAndErrorDiscrimination(unittest.TestCase):
    def setUp(self):
        self.provider = FakeDiscussionProvider()

    def test_simulated_provider_failure(self):
        self.provider.simulate_failure = True
        self.provider.simulated_failure_message = "Database connection dropped"

        with self.assertRaises(CommunityProviderError) as ctx:
            self.provider.get_discussion(DiscussionRetrievalParams(discussion_id="reddit-py-101"))
        self.assertIn("Database connection dropped", str(ctx.exception))

    def test_simulated_timeout(self):
        self.provider.simulate_timeout = True

        with self.assertRaises(CommunityTimeoutError) as ctx:
            self.provider.search_discussions(DiscussionSearchParams(query="async"))
        self.assertEqual(ctx.exception.operation, "search_discussions")

    def test_cancellation_propagation(self):
        is_cancelled = lambda: True

        with self.assertRaises(CommunityCancelledError) as ctx:
            self.provider.get_discussion(
                DiscussionRetrievalParams(discussion_id="reddit-py-101"),
                is_cancelled=is_cancelled,
            )
        self.assertEqual(ctx.exception.operation, "get_discussion")

    def test_simulated_rate_limit(self):
        self.provider.simulate_rate_limit = True

        with self.assertRaises(CommunityRateLimitError) as ctx:
            self.provider.get_comments(DiscussionCommentsParams(discussion_id="reddit-py-101"))
        self.assertEqual(ctx.exception.retry_after_seconds, 30.0)

    def test_simulated_auth_failure(self):
        self.provider.simulate_auth_error = True

        with self.assertRaises(CommunityAuthenticationError):
            self.provider.get_discussion(DiscussionRetrievalParams(discussion_id="reddit-py-101"))

    def test_simulated_partial_retrieval(self):
        self.provider.simulate_partial_retrieval = True
        disc = self.provider.get_discussion(DiscussionRetrievalParams(discussion_id="reddit-py-101"))
        self.assertTrue(disc.metadata.get("is_partial"))
        self.assertIn("partial_reason", disc.metadata)


class TestMissingMetadataAndDeletedContent(unittest.TestCase):
    def setUp(self):
        self.provider = FakeDiscussionProvider()

    def test_discussion_with_missing_author_and_deleted_comment(self):
        params = DiscussionRetrievalParams(discussion_id="disc-incomplete-401")
        disc = self.provider.get_discussion(params)

        self.assertIsNone(disc.author_id)
        self.assertEqual(disc.status, DiscussionStatus.UNKNOWN)
        self.assertEqual(disc.tags, [])
        self.assertIsNone(disc.engagement.score)

        comments = self.provider.get_comments(DiscussionCommentsParams(discussion_id="disc-incomplete-401"))
        self.assertEqual(len(comments), 1)
        deleted_c = comments[0]
        self.assertEqual(deleted_c.content, "[deleted by user]")
        self.assertTrue(deleted_c.metadata.get("is_deleted"))


if __name__ == "__main__":
    unittest.main()

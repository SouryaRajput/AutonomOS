"""
Unit tests for Dynamic Subtree Expansion (Limitation 2 Mitigation).
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
)
from core.research.community.retriever import DiscussionThreadRetriever


class TestSubtreeExpansion(unittest.TestCase):

    def setUp(self):
        self.provider = FakeDiscussionProvider()
        self.retriever = DiscussionThreadRetriever(provider=self.provider)

        # Set up a test discussion with a deep branch
        self.comm_ctx = CommunityContext(
            platform=CommunityPlatform.REDDIT,
            community_id="r/python",
            community_name="Python",
            source_url="https://reddit.com/r/python",
        )
        self.disc = Discussion(
            discussion_id="disc-subtree-test",
            community_context=self.comm_ctx,
            title="Async architecture test",
            url="https://reddit.com/r/python/comments/123",
            status=DiscussionStatus.OPEN,
        )

        root = DiscussionPost(
            post_id="post-root",
            discussion_id="disc-subtree-test",
            content="Root submission",
            is_root=True,
            depth=0,
        )
        self.disc.add_post(root)

        # Comment 1
        c1 = DiscussionPost(
            post_id="comm-1",
            discussion_id="disc-subtree-test",
            content="Level 1 comment",
            parent_id="post-root",
            depth=1,
            engagement=EngagementMetrics(score=50),
        )
        self.disc.add_post(c1)

        # Child of Comment 1
        c1_1 = DiscussionPost(
            post_id="comm-1-1",
            discussion_id="disc-subtree-test",
            content="Level 2 reply",
            parent_id="comm-1",
            depth=2,
            engagement=EngagementMetrics(score=25),
        )
        self.disc.add_post(c1_1)

        # Child of Child
        c1_1_1 = DiscussionPost(
            post_id="comm-1-1-1",
            discussion_id="disc-subtree-test",
            content="Level 3 deep reply",
            parent_id="comm-1-1",
            depth=3,
            engagement=EngagementMetrics(score=10),
        )
        self.disc.add_post(c1_1_1)

        self.provider.add_discussion(self.disc)

    def test_provider_retrieve_comment_subtree(self):
        sub_posts = self.provider.retrieve_comment_subtree(
            discussion_id="disc-subtree-test",
            root_comment_id="comm-1",
            max_depth=5,
        )
        # Should contain comm-1-1 and comm-1-1-1
        self.assertEqual(len(sub_posts), 2)
        post_ids = [p.post_id for p in sub_posts]
        self.assertIn("comm-1-1", post_ids)
        self.assertIn("comm-1-1-1", post_ids)

    def test_retriever_expand_discussion_subtree(self):
        # Create an initially partial discussion that only has root and comm-1
        shallow_disc = Discussion(
            discussion_id="disc-subtree-test",
            community_context=self.comm_ctx,
            title="Async architecture test",
            url="https://reddit.com/r/python/comments/123",
            status=DiscussionStatus.OPEN,
        )
        shallow_disc.add_post(
            DiscussionPost(
                post_id="post-root",
                discussion_id="disc-subtree-test",
                content="Root submission",
                is_root=True,
                depth=0,
            )
        )
        shallow_disc.add_post(
            DiscussionPost(
                post_id="comm-1",
                discussion_id="disc-subtree-test",
                content="Level 1 comment",
                parent_id="post-root",
                depth=1,
            )
        )

        self.assertEqual(shallow_disc.total_posts(), 2)

        # Expand subtree for comm-1
        outcome = self.retriever.expand_discussion_subtree(
            discussion=shallow_disc,
            root_comment_id="comm-1",
            max_comments=10,
            max_depth=5,
        )

        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.total_posts_retrieved, 4)
        self.assertEqual(shallow_disc.total_posts(), 4)
        self.assertTrue(shallow_disc.thread_structure.has_post("comm-1-1"))
        self.assertTrue(shallow_disc.thread_structure.has_post("comm-1-1-1"))
        self.assertEqual(outcome.metadata["posts_added"], 2)


if __name__ == "__main__":
    unittest.main()

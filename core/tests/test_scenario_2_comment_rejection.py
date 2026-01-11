"""
Integration test for Scenario 2: Comment sanitization rejection flow

Tests the flow where owner rejects sanitized comment 3 times,
then publishes without comment.
"""
from django.test import TestCase
from unittest.mock import patch, MagicMock

from core.models import User, Collaboration, Rating, ConversationState
from core.services import RatingService, LLMService
from core.handlers.conversation_handler import ConversationHandler
from core.tests.test_utils import BaseIntegrationTest, TestFixtures


class TestScenario2CommentRejection(BaseIntegrationTest):
    """Test Scenario 2: Comment rejection and limit handling"""
    
    def setUp(self):
        super().setUp()
        self.conversation_handler = ConversationHandler()
        self.rating_service = RatingService()
        
        # Create a confirmed collaboration
        self.collaboration = self.fixtures.create_collaboration(
            owner=self.owner,
            driver=self.driver,
            state="ELIGIBLE_FOR_RATING"
        )
    
    @patch('core.services.llm_service.LLMService.sanitize_comment')
    def test_comment_rejection_three_times(self, mock_llm_sanitize):
        """
        Test that owner can reject sanitized comment 3 times,
        then must publish without comment or cancel
        """
        # Mock LLM sanitization (returns same result each time)
        mock_llm_sanitize.return_value = ("Sanitized comment", True)
        
        # Create rating with comment
        rating = self.fixtures.create_rating(
            collaboration=self.collaboration,
            comment_raw="Original comment with PII",
            is_published=False
        )
        
        # Simulate sanitization (normally done during rating creation)
        sanitized, success = self.rating_service.sanitize_comment(rating)
        self.assertTrue(success)
        
        # First rejection
        state = self.fixtures.create_conversation_state(
            user=self.owner,
            state="OWNER_RATE_REVIEW_SANITIZED"
        )
        
        # Owner rejects (1st time)
        state, payload = self.conversation_handler.process_message(
            phone_number=self.owner.phone_number,
            message_text="2"  # Reject
        )
        
        rating.refresh_from_db()
        self.assertEqual(rating.sanitization_rejections_count, 1)
        self.assertEqual(state, "OWNER_RATE_ENTER_COMMENT")
        
        # Re-enter comment and sanitize again
        rating.comment_raw = "Updated comment"
        rating.save()
        self.rating_service.sanitize_comment(rating)
        
        # Owner rejects (2nd time)
        state = self.fixtures.create_conversation_state(
            user=self.owner,
            state="OWNER_RATE_REVIEW_SANITIZED"
        )
        
        state, payload = self.conversation_handler.handle_message(
            phone_number=self.owner.phone_number,
            message_text="2"  # Reject
        )
        
        rating.refresh_from_db()
        self.assertEqual(rating.sanitization_rejections_count, 2)
        
        # Re-enter comment and sanitize again
        rating.comment_raw = "Another updated comment"
        rating.save()
        self.rating_service.sanitize_comment(rating)
        
        # Owner rejects (3rd time) - should hit limit
        state = self.fixtures.create_conversation_state(
            user=self.owner,
            state="OWNER_RATE_REVIEW_SANITIZED"
        )
        
        state, payload = self.conversation_handler.handle_message(
            phone_number=self.owner.phone_number,
            message_text="2"  # Reject
        )
        
        rating.refresh_from_db()
        self.assertEqual(rating.sanitization_rejections_count, 3)
        
        # Should transition to limit reached state
        self.assertEqual(state, "OWNER_RATE_LIMIT_REACHED")
        self.assert_message_payload_valid(payload)
        self.assertIn("3 fois", payload.body)
        
        # Owner chooses to publish without comment
        state, payload = self.conversation_handler.process_message(
            phone_number=self.owner.phone_number,
            message_text="1"  # Publish without comment
        )
        
        rating.refresh_from_db()
        self.assertTrue(rating.is_published)
        self.assertIsNone(rating.comment_published)
        self.assertIsNotNone(rating.published_at)
    
    def test_rejection_limit_enforced(self):
        """Test that rejection limit is strictly enforced"""
        rating = self.fixtures.create_rating(
            collaboration=self.collaboration,
            comment_raw="Test comment",
            is_published=False
        )
        
        # Set rejection count to 3
        rating.sanitization_rejections_count = 3
        rating.save()
        
        # Try to reject again - should not be allowed
        state = self.fixtures.create_conversation_state(
            user=self.owner,
            state="OWNER_RATE_REVIEW_SANITIZED"
        )
        
        state, payload = self.conversation_handler.handle_message(
            phone_number=self.owner.phone_number,
            message_text="2"  # Reject
        )
        
        # Should transition to limit reached, not allow another rejection
        self.assertEqual(state, "OWNER_RATE_LIMIT_REACHED")
        rating.refresh_from_db()
        self.assertEqual(rating.sanitization_rejections_count, 3)  # Not incremented

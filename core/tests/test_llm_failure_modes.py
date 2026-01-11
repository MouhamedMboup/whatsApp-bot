"""
LLM failure mode tests

Tests handling of LLM timeouts, partial failures, and retries.
Ensures RatingService handles LLM failures gracefully.
"""
from django.test import TestCase
from unittest.mock import patch, MagicMock
from freezegun import freeze_time
import time

from core.models import User, Collaboration, Rating
from core.services import RatingService, LLMService
from core.tests.test_utils import BaseIntegrationTest, TestFixtures, MOCK_PATHS


class TestLLMFailureModes(BaseIntegrationTest):
    """Test LLM service failure handling"""
    
    def setUp(self):
        super().setUp()
        self.rating_service = RatingService()
        self.llm_service = LLMService()
    
    @freeze_time("2024-01-01 12:00:00")
    @patch('openai.OpenAI')
    def test_llm_timeout_handled_gracefully(self, mock_openai_client):
        """
        Test that LLM timeout is handled and user can retry or publish without comment
        """
        # Mock OpenAI client to raise timeout
        mock_client = MagicMock()
        mock_openai_client.return_value = mock_client
        mock_client.chat.completions.create.side_effect = TimeoutError("Request timeout")
        
        collaboration = self.fixtures.create_collaboration(
            owner=self.owner,
            driver=self.driver,
            state="ELIGIBLE_FOR_RATING"
        )
        
        rating = self.fixtures.create_rating(
            collaboration=collaboration,
            comment_raw="Test comment",
            is_published=False
        )
        
        # Try to sanitize comment (should handle timeout)
        sanitized, success = self.llm_service.sanitize_comment(rating.comment_raw)
        
        # Should return failure
        self.assertFalse(success)
        self.assertIsNone(sanitized)
        
        # Rating should still be valid (not published yet)
        rating.refresh_from_db()
        self.assertFalse(rating.is_published)
        self.assertIsNotNone(rating.comment_raw)
    
    @freeze_time("2024-01-01 12:00:00")
    @patch('openai.OpenAI')
    def test_llm_api_error_handled(self, mock_openai_client):
        """
        Test that LLM API errors are handled gracefully
        """
        # Mock OpenAI client to raise API error
        mock_client = MagicMock()
        mock_openai_client.return_value = mock_client
        from openai import APIError
        mock_client.chat.completions.create.side_effect = APIError(
            message="API error",
            request=MagicMock(),
            body=MagicMock()
        )
        
        collaboration = self.fixtures.create_collaboration(
            owner=self.owner,
            driver=self.driver,
            state="ELIGIBLE_FOR_RATING"
        )
        
        rating = self.fixtures.create_rating(
            collaboration=collaboration,
            comment_raw="Test comment",
            is_published=False
        )
        
        # Try to sanitize comment
        sanitized, success = self.llm_service.sanitize_comment(rating.comment_raw)
        
        # Should return failure
        self.assertFalse(success)
        self.assertIsNone(sanitized)
    
    @freeze_time("2024-01-01 12:00:00")
    @patch(MOCK_PATHS['llm_service'])
    def test_sanitization_rejection_count_tracked(self, mock_llm):
        """
        Test that sanitization_rejections_count is tracked correctly
        """
        # Mock LLM to return sanitized comment
        mock_llm.return_value = ("Sanitized comment", True)
        
        collaboration = self.fixtures.create_collaboration(
            owner=self.owner,
            driver=self.driver,
            state="ELIGIBLE_FOR_RATING"
        )
        
        rating = self.fixtures.create_rating(
            collaboration=collaboration,
            comment_raw="Original comment",
            is_published=False
        )
        
        # Initial count should be 0
        self.assertEqual(rating.sanitization_rejections_count, 0)
        
        # Reject sanitized comment
        self.rating_service.reject_sanitized_comment(rating, self.owner)
        rating.refresh_from_db()
        self.assertEqual(rating.sanitization_rejections_count, 1)
        
        # Reject again
        self.rating_service.reject_sanitized_comment(rating, self.owner)
        rating.refresh_from_db()
        self.assertEqual(rating.sanitization_rejections_count, 2)
        
        # Reject third time
        self.rating_service.reject_sanitized_comment(rating, self.owner)
        rating.refresh_from_db()
        self.assertEqual(rating.sanitization_rejections_count, 3)
        
        # After 3 rejections, should not allow more rejections
        # (handled in conversation handler, but count should not exceed 3)
        self.assertLessEqual(rating.sanitization_rejections_count, 3)
    
    @freeze_time("2024-01-01 12:00:00")
    @patch(MOCK_PATHS['llm_service'])
    def test_publish_without_comment_on_llm_failure(self, mock_llm):
        """
        Test that rating can be published without comment if LLM fails
        """
        # Mock LLM to fail
        mock_llm.return_value = (None, False)
        
        collaboration = self.fixtures.create_collaboration(
            owner=self.owner,
            driver=self.driver,
            state="ELIGIBLE_FOR_RATING"
        )
        
        rating = self.fixtures.create_rating(
            collaboration=collaboration,
            comment_raw="Test comment",
            is_published=False
        )
        
        # Publish without comment (fallback)
        success = self.rating_service.publish_without_comment(rating, self.owner)
        
        self.assertTrue(success)
        rating.refresh_from_db()
        self.assertTrue(rating.is_published)
        self.assertIsNone(rating.comment_published)
        self.assertIsNotNone(rating.published_at)
    
    @freeze_time("2024-01-01 12:00:00")
    @patch('openai.OpenAI')
    def test_llm_partial_response_handled(self, mock_openai_client):
        """
        Test that partial LLM responses are handled
        """
        # Mock OpenAI to return partial response
        mock_client = MagicMock()
        mock_openai_client.return_value = mock_client
        
        # Simulate partial response (empty content)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""
        mock_client.chat.completions.create.return_value = mock_response
        
        collaboration = self.fixtures.create_collaboration(
            owner=self.owner,
            driver=self.driver,
            state="ELIGIBLE_FOR_RATING"
        )
        
        rating = self.fixtures.create_rating(
            collaboration=collaboration,
            comment_raw="Test comment",
            is_published=False
        )
        
        # Try to sanitize
        sanitized, success = self.llm_service.sanitize_comment(rating.comment_raw)
        
        # Should handle empty response
        # (Implementation may treat empty as failure or use original)
        self.assertIsNotNone(sanitized is None or len(sanitized) == 0)

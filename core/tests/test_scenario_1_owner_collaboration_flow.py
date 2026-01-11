"""
Integration test for Scenario 1: Owner creates complete collaboration flow

Tests the full happy path:
1. Owner declares collaboration
2. Driver confirms
3. Owner rates driver
4. Comment sanitization flow
5. Reputation update triggered by events
"""
from datetime import datetime, timedelta
from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch, MagicMock
from freezegun import freeze_time

from core.models import User, Collaboration, Rating, ConversationState, DriverProfile
from core.services import (
    ConversationService, CollaborationService, RatingService,
    EventHandlerService, ReputationService, LLMService
)
from core.handlers.conversation_handler import ConversationHandler
from core.tests.test_utils import BaseIntegrationTest, TestFixtures, MOCK_PATHS


class TestScenario1OwnerCollaborationFlow(BaseIntegrationTest):
    """Test Scenario 1: Complete collaboration and rating flow"""
    
    def setUp(self):
        super().setUp()
        self.conversation_handler = ConversationHandler()
        self.conversation_service = ConversationService()
        self.collaboration_service = CollaborationService()
        self.rating_service = RatingService()
        self.event_handler = EventHandlerService()
        self.reputation_service = ReputationService()
    
    @patch('core.services.llm_service.LLMService.sanitize_comment')
    def test_complete_collaboration_flow(self, mock_llm_sanitize):
        """
        Test the complete flow from collaboration declaration to rating publication
        """
        # Mock LLM sanitization
        mock_llm_sanitize.return_value = ("Excellent chauffeur, très ponctuel et respectueux du véhicule.", True)
        
        # Step 1: Owner declares collaboration
        # Initial state: OWNER_MAIN_MENU
        state, payload = self.conversation_handler.process_message(
            phone_number=self.owner.phone_number,
            message_text="2"  # Declare collaboration
        )
        
        self.assertEqual(state, "OWNER_DECLARE_COLLABORATION")
        self.assert_message_payload_valid(payload)
        self.assertIn("DÉCLARER UNE COLLABORATION", payload.title)
        
        # Step 2: Owner enters driver phone number
        state, payload = self.conversation_handler.process_message(
            phone_number=self.owner.phone_number,
            message_text="+221771234567"
        )
        
        self.assertEqual(state, "OWNER_DECLARE_COLLABORATION_DATES")
        self.assert_message_payload_valid(payload)
        
        # Verify temp_data contains driver_id
        conversation_state = ConversationState.objects.get(user=self.owner)
        driver_id = self.conversation_service.get_temp_data(conversation_state, "driver_id")
        self.assertIsNotNone(driver_id)
        self.assertEqual(int(driver_id), self.driver.id)
        
        # Step 3: Owner enters collaboration dates
        start_date = timezone.now().date()
        end_date = start_date + timedelta(days=7)
        date_str = f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}"
        
        state, payload = self.conversation_handler.process_message(
            phone_number=self.owner.phone_number,
            message_text=date_str
        )
        
        self.assertEqual(state, "OWNER_MAIN_MENU")
        self.assert_message_payload_valid(payload)
        
        # Verify collaboration was created
        collaboration = Collaboration.objects.filter(
            owner=self.owner,
            driver=self.driver
        ).first()
        self.assertIsNotNone(collaboration)
        self.assertEqual(collaboration.state, "PENDING_CONFIRMATION")
        self.assertEqual(collaboration.duration_days, 8)
        
        # Step 4: Driver confirms collaboration
        # Driver navigates to confirmations
        state, payload = self.conversation_handler.process_message(
            phone_number=self.driver.phone_number,
            message_text="3"  # View collaborations
        )
        
        # Driver selects collaboration to confirm
        state, payload = self.conversation_handler.process_message(
            phone_number=self.driver.phone_number,
            message_text="1"  # Select first collaboration
        )
        
        # Driver confirms
        state, payload = self.conversation_handler.process_message(
            phone_number=self.driver.phone_number,
            message_text="1"  # Confirm
        )
        
        # Verify collaboration is confirmed
        collaboration.refresh_from_db()
        self.assertEqual(collaboration.state, "ELIGIBLE_FOR_RATING")
        self.assertIsNotNone(collaboration.confirmed_at)
        
        # Step 5: Owner rates the collaboration
        # Owner navigates to rate
        state, payload = self.conversation_handler.process_message(
            phone_number=self.owner.phone_number,
            message_text="3"  # View collaborations
        )
        
        # Owner selects collaboration to rate
        state, payload = self.conversation_handler.process_message(
            phone_number=self.owner.phone_number,
            message_text="1"  # Select first collaboration
        )
        
        # Owner enters scores
        scores = ["4", "5", "4", "5"]  # Punctuality, Vehicle, Client, Reliability
        for score in scores:
            state, payload = self.conversation_handler.process_message(
                phone_number=self.owner.phone_number,
                message_text=score
            )
        
        # Owner chooses to add comment
        state, payload = self.conversation_handler.process_message(
            phone_number=self.owner.phone_number,
            message_text="1"  # Yes, add comment
        )
        
        # Owner enters comment
        comment = "Excellent chauffeur, très ponctuel et respectueux du véhicule!"
        state, payload = self.conversation_handler.process_message(
            phone_number=self.owner.phone_number,
            message_text=comment
        )
        
        # Verify rating was created
        rating = Rating.objects.filter(collaboration=collaboration).first()
        self.assertIsNotNone(rating)
        self.assertEqual(rating.comment_raw, comment)
        self.assertFalse(rating.is_published)
        
        # Verify LLM sanitization was called
        mock_llm_sanitize.assert_called_once()
        
        # Step 6: Owner approves sanitized comment
        state, payload = self.conversation_handler.process_message(
            phone_number=self.owner.phone_number,
            message_text="1"  # Approve
        )
        
        # Verify rating is published
        rating.refresh_from_db()
        self.assertTrue(rating.is_published)
        self.assertIsNotNone(rating.published_at)
        
        # Step 7: Verify reputation was updated
        driver_profile = DriverProfile.objects.get(user=self.driver)
        reputation = self.reputation_service.get_reputation(driver_profile, force_recompute=False)
        
        self.assertIsNotNone(reputation)
        self.assertIn("score_median", reputation)
        self.assertGreater(reputation["score_median"], 0)
        self.assertFalse(reputation.get("is_stale", True))
    
    @freeze_time("2024-01-01 12:00:00")
    def test_collaboration_duration_validation(self):
        """Test that collaboration must be at least 7 days"""
        # Create state for date entry
        self.fixtures.create_conversation_state(
            user=self.owner,
            state="OWNER_DECLARE_COLLABORATION_DATES"
        )
        
        # Set temp_data with driver_id
        state = ConversationState.objects.get(user=self.owner)
        self.conversation_service.set_temp_data(state, "driver_id", str(self.driver.id))
        
        # Try to create collaboration with < 7 days
        start_date = timezone.now().date()
        end_date = start_date + timedelta(days=4)  # Only 5 days
        date_str = f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}"
        
        state, payload = self.conversation_handler.process_message(
            phone_number=self.owner.phone_number,
            message_text=date_str
        )
        
        # Should remain in same state with error message
        self.assertEqual(state, "OWNER_DECLARE_COLLABORATION_DATES")
        self.assertIn("7 jours", payload.body)
        
        # Verify no collaboration was created
        collaboration_count = Collaboration.objects.filter(
            owner=self.owner,
            driver=self.driver
        ).count()
        self.assertEqual(collaboration_count, 0)

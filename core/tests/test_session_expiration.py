"""
Session expiration tests

Tests handling of expired conversation states.
"""
from django.test import TestCase
from freezegun import freeze_time
from datetime import timedelta

from core.models import User, ConversationState
from core.services import ConversationService
from core.handlers.conversation_handler import ConversationHandler
from core.tests.test_utils import BaseIntegrationTest, TestFixtures


class TestSessionExpiration(BaseIntegrationTest):
    """Test session expiration handling"""
    
    def setUp(self):
        super().setUp()
        self.conversation_service = ConversationService()
        self.handler = ConversationHandler()
    
    @freeze_time("2024-01-01 12:00:00")
    def test_expired_session_returns_to_menu(self):
        """
        Test that expired session (>30 min) returns user to main menu
        """
        # Create state 31 minutes ago
        with freeze_time("2024-01-01 11:29:00"):
            state = self.fixtures.create_conversation_state(
                user=self.owner,
                state="OWNER_DECLARE_COLLABORATION_DATES",
                expected_input_type="TEXT"
            )
        
        # Now it's 12:00:00 (31 minutes later)
        # Process message should detect expiration
        state_result, payload = self.handler.process_message(
            phone_number=self.owner.phone_number,
            message_text="01/01/2024 - 08/01/2024"
        )
        
        # Should return to main menu
        self.assertEqual(state_result, "OWNER_MAIN_MENU")
        
        # Old state should be deleted
        old_state = ConversationState.objects.filter(
            user=self.owner,
            current_state="OWNER_DECLARE_COLLABORATION_DATES"
        ).first()
        self.assertIsNone(old_state)
    
    @freeze_time("2024-01-01 12:00:00")
    def test_active_session_not_expired(self):
        """
        Test that active session (<30 min) is not expired
        """
        # Create state 15 minutes ago
        with freeze_time("2024-01-01 11:45:00"):
            state = self.fixtures.create_conversation_state(
                user=self.owner,
                state="OWNER_DECLARE_COLLABORATION",
                expected_input_type="TEXT"
            )
        
        # Now it's 12:00:00 (15 minutes later)
        # Session should still be active
        is_expired = self.conversation_service.is_expired(state)
        self.assertFalse(is_expired)
    
    @freeze_time("2024-01-01 12:00:00")
    def test_expired_state_deleted(self):
        """
        Test that expired state is deleted when detected
        """
        # Create expired state
        with freeze_time("2024-01-01 11:29:00"):
            state = self.fixtures.create_conversation_state(
                user=self.owner,
                state="OWNER_DECLARE_COLLABORATION_DATES",
                expected_input_type="TEXT"
            )
            state_id = state.id
        
        # Verify state exists
        self.assertIsNotNone(ConversationState.objects.filter(id=state_id).first())
        
        # Process message (should detect and delete expired state)
        self.handler.process_message(
            phone_number=self.owner.phone_number,
            message_text="test"
        )
        
        # State should be deleted
        self.assertIsNone(ConversationState.objects.filter(id=state_id).first())

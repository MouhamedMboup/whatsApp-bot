"""
Integration test for Scenario 6: Invalid input handling with retry

Tests the system's handling of invalid inputs:
- Invalid input detection
- Retry mechanism (3 attempts)
- Return to menu after 3 failures
"""
from django.test import TestCase
from freezegun import freeze_time

from core.models import User, ConversationState
from core.services import ConversationService
from core.handlers.conversation_handler import ConversationHandler
from core.tests.test_utils import BaseIntegrationTest, TestFixtures


class TestScenario6InvalidInput(BaseIntegrationTest):
    """Test Scenario 6: Invalid input retry and error handling"""
    
    def setUp(self):
        super().setUp()
        self.conversation_handler = ConversationHandler()
        self.conversation_service = ConversationService()
    
    @freeze_time("2024-01-01 12:00:00")
    def test_invalid_input_retry_three_times(self):
        """
        Test that invalid input is retried 3 times before returning to menu
        """
        # Create state expecting score input (1-5)
        state = self.fixtures.create_conversation_state(
            user=self.owner,
            state="OWNER_RATE_COLLECT_SCORES",
            expected_input_type="TEXT",
            allowed_values=["1", "2", "3", "4", "5"]
        )
        
        # First invalid input
        state, payload = self.conversation_handler.process_message(
            phone_number=self.owner.phone_number,
            message_text="6"  # Invalid (out of range)
        )
        
        conversation_state = ConversationState.objects.get(user=self.owner)
        self.assertEqual(conversation_state.invalid_input_count, 1)
        self.assertEqual(state, "OWNER_RATE_COLLECT_SCORES")  # Same state
        
        # Second invalid input
        state, payload = self.conversation_handler.handle_message(
            phone_number=self.owner.phone_number,
            message_text="abc"  # Invalid (not a number)
        )
        
        conversation_state.refresh_from_db()
        self.assertEqual(conversation_state.invalid_input_count, 2)
        self.assertEqual(state, "OWNER_RATE_COLLECT_SCORES")  # Same state
        
        # Third invalid input - should return to menu
        state, payload = self.conversation_handler.handle_message(
            phone_number=self.owner.phone_number,
            message_text="10"  # Invalid (out of range)
        )
        
        conversation_state.refresh_from_db()
        self.assertEqual(conversation_state.invalid_input_count, 3)
        self.assertEqual(state, "OWNER_MAIN_MENU")  # Returned to menu
        
        # Verify state was reset
        new_state = ConversationState.objects.filter(user=self.owner).first()
        if new_state:
            self.assertEqual(new_state.current_state, "OWNER_MAIN_MENU")
    
    def test_valid_input_resets_counter(self):
        """Test that valid input resets the invalid input counter"""
        state = self.fixtures.create_conversation_state(
            user=self.owner,
            state="OWNER_RATE_COLLECT_SCORES",
            expected_input_type="TEXT",
            allowed_values=["1", "2", "3", "4", "5"]
        )
        
        # Two invalid inputs
        self.conversation_handler.handle_message(user=self.owner, message_text="6")
        self.conversation_handler.handle_message(user=self.owner, message_text="7")
        
        conversation_state = ConversationState.objects.get(user=self.owner)
        self.assertEqual(conversation_state.invalid_input_count, 2)
        
        # Valid input should reset counter
        state, payload = self.conversation_handler.handle_message(
            phone_number=self.owner.phone_number,
            message_text="4"  # Valid
        )
        
        conversation_state.refresh_from_db()
        self.assertEqual(conversation_state.invalid_input_count, 0)
    
    def test_menu_choice_validation(self):
        """Test that menu choices are validated against allowed_values"""
        state = self.fixtures.create_conversation_state(
            user=self.owner,
            state="OWNER_MAIN_MENU",
            expected_input_type="MENU_CHOICE",
            allowed_values=["1", "2", "3", "4"]
        )
        
        # Invalid menu choice
        state, payload = self.conversation_handler.handle_message(
            phone_number=self.owner.phone_number,
            message_text="5"  # Not in allowed values
        )
        
        conversation_state = ConversationState.objects.get(user=self.owner)
        self.assertEqual(conversation_state.invalid_input_count, 1)
        self.assertEqual(state, "OWNER_MAIN_MENU")  # Same state
        
        # Valid menu choice
        state, payload = self.conversation_handler.handle_message(
            phone_number=self.owner.phone_number,
            message_text="2"  # Valid
        )
        
        conversation_state.refresh_from_db()
        self.assertEqual(conversation_state.invalid_input_count, 0)

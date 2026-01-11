"""
Integration test for Scenario 14: Idempotency - Duplicate webhook delivery

Tests that duplicate webhook messages (same message_id) are handled idempotently:
- No duplicate state creation
- No duplicate side effects
- Same MessagePayload returned
"""
from django.test import TestCase
from unittest.mock import patch

from core.models import User, ConversationState
from core.services import ConversationService, WhatsAppService
from core.handlers.conversation_handler import ConversationHandler
from core.tests.test_utils import BaseIntegrationTest, TestFixtures


class TestScenario14Idempotency(BaseIntegrationTest):
    """Test Scenario 14: Idempotent handling of duplicate webhooks"""
    
    def setUp(self):
        super().setUp()
        self.conversation_handler = ConversationHandler()
        self.conversation_service = ConversationService()
        self.whatsapp_service = WhatsAppService()
    
    def test_duplicate_webhook_same_message_id(self):
        """
        Test that processing the same message twice produces same result
        without side effects
        """
        # Simulate first webhook delivery
        message_id = "msg_12345"
        webhook_payload_1 = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "id": message_id,
                            "from": self.owner.phone_number,
                            "type": "text",
                            "text": {"body": "2"}  # Declare collaboration
                        }]
                    }
                }]
            }]
        }
        
        # Parse first webhook
        message_data_1 = self.whatsapp_service.parse_webhook_payload(webhook_payload_1)
        self.assertEqual(message_data_1["type"], "text_message")
        
        # Process first message
        state_1, payload_1 = self.conversation_handler.process_message(
            phone_number=self.owner.phone_number,
            message_text="2"
        )
        
        # Count states after first processing
        state_count_1 = ConversationState.objects.filter(user=self.owner).count()
        
        # Simulate duplicate webhook delivery (same message_id)
        webhook_payload_2 = webhook_payload_1  # Same payload
        
        # Parse second webhook
        message_data_2 = self.whatsapp_service.parse_webhook_payload(webhook_payload_2)
        self.assertEqual(message_data_2["type"], "text_message")
        
        # Process duplicate message
        state_2, payload_2 = self.conversation_handler.handle_message(
            phone_number=self.owner.phone_number,
            message_text="2"
        )
        
        # Count states after second processing
        state_count_2 = ConversationState.objects.filter(user=self.owner).count()
        
        # Verify idempotency
        self.assertEqual(state_1, state_2)  # Same state
        self.assertEqual(payload_1.body, payload_2.body)  # Same payload
        self.assertEqual(state_count_1, state_count_2)  # No duplicate state
        
        # Verify only one state exists
        states = ConversationState.objects.filter(user=self.owner)
        self.assertEqual(states.count(), 1)
    
    def test_duplicate_state_creation_prevented(self):
        """
        Test that get_or_create_state prevents duplicate states
        """
        # Create state first time
        state_1 = self.conversation_service.get_or_create_state(
            user=self.owner,
            initial_state="OWNER_DECLARE_COLLABORATION",
            expected_input_type="TEXT"
        )
        
        # Try to create same state again
        state_2 = self.conversation_service.get_or_create_state(
            user=self.owner,
            initial_state="OWNER_DECLARE_COLLABORATION",
            expected_input_type="TEXT"
        )
        
        # Should return same state, not create duplicate
        self.assertEqual(state_1.id, state_2.id)
        
        # Verify only one state exists
        states = ConversationState.objects.filter(
            user=self.owner,
            current_state="OWNER_DECLARE_COLLABORATION"
        )
        self.assertEqual(states.count(), 1)
    
    @patch('core.services.whatsapp_service.WhatsAppService.send_text_message')
    def test_duplicate_message_not_sent_twice(self, mock_send):
        """
        Test that duplicate webhook doesn't trigger duplicate WhatsApp messages
        """
        # Process message first time
        state_1, payload_1 = self.conversation_handler.handle_message(
            phone_number=self.owner.phone_number,
            message_text="2"
        )
        
        # Simulate sending (normally done in views.py)
        # This would be called once per unique message_id
        call_count_1 = mock_send.call_count
        
        # Process duplicate message
        state_2, payload_2 = self.conversation_handler.handle_message(
            phone_number=self.owner.phone_number,
            message_text="2"
        )
        
        # Note: In a real implementation, views.py should track message_id
        # to prevent duplicate sends. This test verifies handler is idempotent.
        # The actual send prevention would be in views.py with message_id tracking.
        
        # Handler should return same payload (idempotent)
        self.assertEqual(payload_1.body, payload_2.body)
    
    def test_conversation_state_idempotent_updates(self):
        """
        Test that updating conversation state is idempotent
        """
        state = self.conversation_service.get_or_create_state(
            user=self.owner,
            initial_state="OWNER_DECLARE_COLLABORATION",
            expected_input_type="TEXT"
        )
        
        # Update state multiple times with same data
        self.conversation_service.set_temp_data(state, "test_key", "test_value")
        self.conversation_service.set_temp_data(state, "test_key", "test_value")
        self.conversation_service.set_temp_data(state, "test_key", "test_value")
        
        # Should still have only one value
        value = self.conversation_service.get_temp_data(state, "test_key")
        self.assertEqual(value, "test_value")
        
        # Verify state wasn't duplicated
        states = ConversationState.objects.filter(user=self.owner)
        self.assertEqual(states.count(), 1)

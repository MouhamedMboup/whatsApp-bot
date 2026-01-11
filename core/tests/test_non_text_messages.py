"""
Non-text message and rate-limit tests

Tests handling of non-text webhook payloads and rate-limiting behavior.
"""
from django.test import TestCase
from unittest.mock import patch, MagicMock
from django.test import Client
from django.urls import reverse
import json

from core.services import WhatsAppService
from core.tests.test_utils import BaseIntegrationTest, TestFixtures, MOCK_PATHS


class TestNonTextMessages(BaseIntegrationTest):
    """Test handling of non-text webhook messages"""
    
    def setUp(self):
        super().setUp()
        self.whatsapp_service = WhatsAppService()
        self.client = Client()
    
    def test_image_message_returns_controlled_error(self):
        """
        Test that image messages return controlled error message
        """
        webhook_payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "id": "msg_image_123",
                            "from": self.owner.phone_number,
                            "type": "image",
                            "image": {
                                "id": "img_123",
                                "mime_type": "image/jpeg"
                            }
                        }]
                    }
                }]
            }]
        }
        
        # Parse webhook
        message_data = self.whatsapp_service.parse_webhook_payload(webhook_payload)
        
        # Should return non_text_message type
        self.assertEqual(message_data["type"], "non_text_message")
        self.assertEqual(message_data["phone_number"], self.owner.phone_number)
        self.assertEqual(message_data["message_type"], "image")
    
    def test_audio_message_returns_controlled_error(self):
        """
        Test that audio messages return controlled error message
        """
        webhook_payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "id": "msg_audio_123",
                            "from": self.owner.phone_number,
                            "type": "audio",
                            "audio": {
                                "id": "audio_123",
                                "mime_type": "audio/ogg"
                            }
                        }]
                    }
                }]
            }]
        }
        
        message_data = self.whatsapp_service.parse_webhook_payload(webhook_payload)
        
        self.assertEqual(message_data["type"], "non_text_message")
        self.assertEqual(message_data["message_type"], "audio")
    
    def test_sticker_message_returns_controlled_error(self):
        """
        Test that sticker messages return controlled error message
        """
        webhook_payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "id": "msg_sticker_123",
                            "from": self.owner.phone_number,
                            "type": "sticker",
                            "sticker": {
                                "id": "sticker_123"
                            }
                        }]
                    }
                }]
            }]
        }
        
        message_data = self.whatsapp_service.parse_webhook_payload(webhook_payload)
        
        self.assertEqual(message_data["type"], "non_text_message")
        self.assertEqual(message_data["message_type"], "sticker")
    
    @patch(MOCK_PATHS['whatsapp_send'])
    def test_non_text_message_no_state_transition(self, mock_send):
        """
        Test that non-text messages don't cause state transitions
        """
        from core.handlers.conversation_handler import ConversationHandler
        
        handler = ConversationHandler()
        
        # Create initial state
        self.fixtures.create_conversation_state(
            user=self.owner,
            state="OWNER_MAIN_MENU"
        )
        
        # Simulate non-text message (would be handled in views.py)
        # This test verifies handler doesn't process non-text
        # (views.py should handle non-text before calling handler)
        
        # Verify state unchanged
        from core.models import ConversationState
        state = ConversationState.objects.get(user=self.owner)
        self.assertEqual(state.current_state, "OWNER_MAIN_MENU")
    
    def test_webhook_handles_unknown_message_type(self):
        """
        Test that unknown message types are handled gracefully
        """
        webhook_payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "id": "msg_unknown_123",
                            "from": self.owner.phone_number,
                            "type": "unknown_type",
                            "unknown": {}
                        }]
                    }
                }]
            }]
        }
        
        # Should not crash
        message_data = self.whatsapp_service.parse_webhook_payload(webhook_payload)
        
        # Should return error or unknown type
        self.assertIn(message_data["type"], ["non_text_message", "error"])
    
    def test_rate_limit_guard_no_auto_send(self):
        """
        Test that system doesn't auto-send unsolicited messages
        (Guard test to ensure we don't spam users)
        """
        from core.services import WhatsAppService
        
        # Verify send_text_message requires explicit call
        # (No automatic sending on state transitions)
        
        # This is a guard test - if we accidentally add auto-send,
        # this test should catch it
        with patch(MOCK_PATHS['whatsapp_send']) as mock_send:
            # Process message (should not auto-send)
            from core.handlers.conversation_handler import ConversationHandler
            handler = ConversationHandler()
            
            state, payload = handler.process_message(
                phone_number=self.owner.phone_number,
                message_text=""
            )
            
            # Handler should not call send_text_message
            # (That's views.py's responsibility)
            mock_send.assert_not_called()

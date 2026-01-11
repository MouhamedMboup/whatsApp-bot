"""
MessagePayload contract tests

Ensures all handlers return valid MessagePayload structures
that follow the contract (title/body/options conventions).
"""
from django.test import TestCase
from core.handlers.conversation_handler import ConversationHandler, MessagePayload
from core.tests.test_utils import BaseIntegrationTest


class TestMessagePayloadContract(BaseIntegrationTest):
    """Test that all handlers return valid MessagePayload structures"""
    
    def setUp(self):
        super().setUp()
        self.handler = ConversationHandler()
    
    def test_main_menu_payload_contract(self):
        """Test that main menu returns valid payload"""
        state, payload = self.handler.process_message(
            phone_number=self.owner.phone_number,
            message_text=""
        )
        
        self.assert_message_payload_contract(payload)
        self.assertIsNotNone(payload.options)
        self.assertGreater(len(payload.options), 0)
    
    def test_all_options_follow_convention(self):
        """Test that all menu options follow number+text convention"""
        state, payload = self.handler.process_message(
            phone_number=self.owner.phone_number,
            message_text=""
        )
        
        if payload.options:
            for option in payload.options:
                self.assertIn('number', option)
                self.assertIn('text', option)
                # Number should be valid menu choice
                self.assertIsInstance(option['number'], str)
                self.assertTrue(option['number'].isdigit() or option['number'] == '0')
    
    def test_text_only_payload_valid(self):
        """Test that text-only payloads (no options) are valid"""
        # Create a state that returns text-only message
        from core.tests.test_utils import TestFixtures
        state = TestFixtures.create_conversation_state(
            user=self.owner,
            state="OWNER_DECLARE_COLLABORATION",
            expected_input_type="TEXT"
        )
        
        state, payload = self.handler.process_message(
            phone_number=self.owner.phone_number,
            message_text="test"
        )
        
        self.assert_message_payload_contract(payload)
        # Text-only messages don't need options
        self.assertTrue(hasattr(payload, 'body') or hasattr(payload, 'title'))
    
    def test_menu_payload_has_valid_options(self):
        """Test that menu payloads have valid option numbers"""
        state, payload = self.handler.process_message(
            phone_number=self.owner.phone_number,
            message_text=""
        )
        
        if payload.options:
            # Collect all option numbers
            numbers = [opt['number'] for opt in payload.options]
            
            # Should not have duplicates
            self.assertEqual(len(numbers), len(set(numbers)), "Option numbers must be unique")
            
            # Should be sequential or at least valid
            for num in numbers:
                self.assertTrue(num.isdigit() or num == '0', f"Invalid option number: {num}")


class TestMessagePayloadStructure(TestCase):
    """Test MessagePayload class structure"""
    
    def test_message_payload_creation(self):
        """Test creating MessagePayload with all fields"""
        payload = MessagePayload(
            title="Test Title",
            body="Test Body",
            options=[{"number": "1", "text": "Option 1"}],
            footer="Test Footer"
        )
        
        self.assertEqual(payload.title, "Test Title")
        self.assertEqual(payload.body, "Test Body")
        self.assertEqual(len(payload.options), 1)
        self.assertEqual(payload.footer, "Test Footer")
    
    def test_message_payload_minimal(self):
        """Test creating MessagePayload with minimal fields"""
        payload = MessagePayload(body="Test")
        
        self.assertEqual(payload.body, "Test")
        self.assertIsNone(payload.title)
        self.assertIsNone(payload.options)
        self.assertIsNone(payload.footer)
    
    def test_message_payload_options_structure(self):
        """Test that options follow correct structure"""
        payload = MessagePayload(
            body="Test",
            options=[
                {"number": "1", "text": "First"},
                {"number": "2", "text": "Second"}
            ]
        )
        
        self.assertEqual(len(payload.options), 2)
        self.assertEqual(payload.options[0]["number"], "1")
        self.assertEqual(payload.options[0]["text"], "First")

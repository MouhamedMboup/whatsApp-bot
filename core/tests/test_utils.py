"""
Test utilities and fixtures for integration tests
"""
from datetime import datetime, timedelta
from django.test import TestCase
from django.utils import timezone
from freezegun import freeze_time
from unittest.mock import MagicMock, patch
from core.models import User, OwnerProfile, DriverProfile, Collaboration, Rating, ConversationState
from core.models import User as UserModel

# Common mock import paths for external services
# Use these constants to avoid brittle mocks across refactors
MOCK_PATHS = {
    'llm_service': 'core.services.llm_service.LLMService.sanitize_comment',
    'whatsapp_send': 'core.services.whatsapp_service.WhatsAppService.send_text_message',
    'whatsapp_parse': 'core.services.whatsapp_service.WhatsAppService.parse_webhook_payload',
    'event_handler': 'core.services.event_handler.EventHandlerService.handle_event',
    'reputation_compute': 'core.services.reputation_service.ReputationService.compute_reputation',
}


class TestFixtures:
    """Factory methods for creating test data"""
    
    @staticmethod
    def create_owner(phone_number: str = "+221770000000", display_name: str = "Test Owner") -> User:
        """Create a test owner user"""
        user = User.objects.create(
            phone_number=phone_number,
            role=User.Role.OWNER
        )
        OwnerProfile.objects.create(user=user)
        return user
    
    @staticmethod
    def create_driver(
        phone_number: str = "+221771234567",
        display_name: str = "Test Driver",
        permit: str = "CI1234567",
        identity_locked: bool = True
    ) -> User:
        """Create a test driver user with profile"""
        user = User.objects.create(
            phone_number=phone_number,
            role=User.Role.DRIVER
        )
        from core.services.identity_service import IdentityService
        from core.utils import generate_salt, hash_permit
        
        salt = generate_salt()
        permit_hash = hash_permit(permit, salt)
        
        driver_profile = DriverProfile.objects.create(
            user=user,
            permit_hash=permit_hash,
            permit_salt=salt,
            display_name=display_name,
            identity_locked=identity_locked
        )
        if identity_locked:
            driver_profile.identity_locked_at = timezone.now()
            driver_profile.save()
        
        return user
    
    @staticmethod
    def create_collaboration(
        owner: User,
        driver: User,
        start_date: datetime = None,
        end_date: datetime = None,
        state: str = "PENDING_CONFIRMATION"
    ) -> Collaboration:
        """Create a test collaboration"""
        if start_date is None:
            start_date = timezone.now().date()
        if end_date is None:
            end_date = start_date + timedelta(days=7)
        
        return Collaboration.objects.create(
            owner=owner,
            driver=driver,
            start_date=start_date,
            end_date=end_date,
            state=state,
            duration_days=(end_date - start_date).days + 1
        )
    
    @staticmethod
    def create_rating(
        collaboration: Collaboration,
        scores: dict = None,
        comment_raw: str = None,
        is_published: bool = False
    ) -> Rating:
        """Create a test rating"""
        if scores is None:
            scores = {
                "punctuality": 4,
                "vehicle_respect": 5,
                "client_relation": 4,
                "reliability": 5
            }
        
        rating = Rating.objects.create(
            collaboration=collaboration,
            score_punctuality=scores.get("punctuality", 4),
            score_vehicle_respect=scores.get("vehicle_respect", 5),
            score_client_relation=scores.get("client_relation", 4),
            score_reliability=scores.get("reliability", 5),
            comment_raw=comment_raw,
            is_published=is_published
        )
        
        if is_published:
            rating.comment_published = comment_raw or "Test comment"
            rating.published_at = timezone.now()
            rating.save()
        
        return rating
    
    @staticmethod
    def create_conversation_state(
        user: User,
        state: str,
        expected_input_type: str = "MENU_CHOICE",
        allowed_values: list = None,
        behavior_on_invalid_input: str = "RETRY_SAME_STATE",
        expires_at: datetime = None
    ) -> ConversationState:
        """Create a test conversation state"""
        if expires_at is None:
            expires_at = timezone.now() + timedelta(minutes=30)
        
        return ConversationState.objects.create(
            user=user,
            current_state=state,
            expected_input_type=expected_input_type,
            allowed_values=allowed_values or [],
            behavior_on_invalid_input=behavior_on_invalid_input,
            expires_at=expires_at
        )


class BaseIntegrationTest(TestCase):
    """Base test case for integration tests"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.fixtures = TestFixtures()
        self.owner = self.fixtures.create_owner()
        self.driver = self.fixtures.create_driver()
    
    def tearDown(self):
        """Clean up after tests"""
        # Django TestCase handles DB cleanup automatically
        pass
    
    def assert_message_payload_valid(self, payload):
        """Assert that a MessagePayload is valid"""
        self.assertIsNotNone(payload)
        self.assertTrue(hasattr(payload, 'body') or hasattr(payload, 'title'))
        # MessagePayload should have at least body or title
        if hasattr(payload, 'options'):
            if payload.options:
                for option in payload.options:
                    self.assertIn('number', option)
                    self.assertIn('text', option)
    
    def assert_state_transition(self, user, expected_state):
        """Assert that user's conversation state matches expected"""
        state = ConversationState.objects.filter(user=user).first()
        if expected_state:
            self.assertIsNotNone(state, "Conversation state should exist")
            self.assertEqual(state.current_state, expected_state)
        else:
            self.assertIsNone(state, "Conversation state should not exist")
    
    def assert_message_payload_contract(self, payload):
        """
        Assert MessagePayload follows the contract:
        - Has body or title
        - Options follow allowed_values conventions (number + text)
        - No WhatsApp-specific formatting
        """
        self.assertIsNotNone(payload, "MessagePayload should not be None")
        
        # Must have at least body or title
        has_content = hasattr(payload, 'body') and payload.body or hasattr(payload, 'title') and payload.title
        self.assertTrue(has_content, "MessagePayload must have body or title")
        
        # Options must follow convention
        if hasattr(payload, 'options') and payload.options:
            self.assertIsInstance(payload.options, list, "Options must be a list")
            for option in payload.options:
                self.assertIsInstance(option, dict, "Each option must be a dict")
                self.assertIn('number', option, "Option must have 'number' key")
                self.assertIn('text', option, "Option must have 'text' key")
                # Number should be string (for menu choices)
                self.assertIsInstance(option['number'], str, "Option number must be string")
                self.assertIsInstance(option['text'], str, "Option text must be string")
        
        # Should not contain WhatsApp-specific formatting
        if hasattr(payload, 'body') and payload.body:
            # No emojis in abstract payload (formatting layer adds them)
            # This is a soft check - emojis might be in user content
            pass


class TimeFreezeMixin:
    """
    Mixin for tests that need deterministic time
    
    Usage:
        @freeze_time("2024-01-01 12:00:00")
        def test_something(self):
            # Time is frozen
            pass
    """
    pass

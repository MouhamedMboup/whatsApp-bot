"""
Integration test for Scenario 3: Driver creates profile and locks identity

Tests the complete driver profile creation flow including:
- Display name entry
- Permit entry and validation
- Permit confirmation
- Identity locking (irreversible)
"""
from django.test import TestCase
from django.utils import timezone

from core.models import User, DriverProfile, ConversationState
from core.services import IdentityService, ConversationService
from core.handlers.conversation_handler import ConversationHandler
from core.tests.test_utils import BaseIntegrationTest, TestFixtures


class TestScenario3DriverProfileCreation(BaseIntegrationTest):
    """Test Scenario 3: Driver profile creation and identity locking"""
    
    def setUp(self):
        super().setUp()
        # Create a new driver without profile
        self.new_driver = User.objects.create(
            phone_number="+221779999999",
            role=User.Role.DRIVER
        )
        self.conversation_handler = ConversationHandler()
        self.identity_service = IdentityService()
        self.conversation_service = ConversationService()
    
    def test_complete_profile_creation_flow(self):
        """
        Test the complete flow from profile creation to identity locking
        """
        # Step 1: Driver selects "Create profile"
        state, payload = self.conversation_handler.process_message(
            phone_number=self.new_driver.phone_number,
            message_text="1"  # Create profile
        )
        
        self.assertEqual(state, "DRIVER_CREATE_PROFILE")
        self.assert_message_payload_valid(payload)
        
        # Step 2: Driver confirms to continue
        state, payload = self.conversation_handler.process_message(
            phone_number=self.new_driver.phone_number,
            message_text="1"  # Yes, continue
        )
        
        self.assertEqual(state, "DRIVER_ENTER_DISPLAY_NAME")
        self.assert_message_payload_valid(payload)
        
        # Step 3: Driver enters display name
        display_name = "John Doe"
        state, payload = self.conversation_handler.process_message(
            phone_number=self.new_driver.phone_number,
            message_text=display_name
        )
        
        self.assertEqual(state, "DRIVER_ENTER_PERMIT")
        
        # Verify temp_data contains display_name
        conversation_state = ConversationState.objects.get(user=self.new_driver)
        stored_name = self.conversation_service.get_temp_data(conversation_state, "display_name")
        self.assertEqual(stored_name, display_name)
        
        # Step 4: Driver enters permit
        permit = "CI1234567"
        state, payload = self.conversation_handler.process_message(
            phone_number=self.new_driver.phone_number,
            message_text=permit
        )
        
        self.assertEqual(state, "DRIVER_CONFIRM_PERMIT")
        self.assert_message_payload_valid(payload)
        
        # Verify permit is masked in confirmation
        self.assertIn("****", payload.body)
        
        # Verify temp_data contains normalized permit
        conversation_state.refresh_from_db()
        stored_permit = self.conversation_service.get_temp_data(conversation_state, "permit_normalized")
        self.assertEqual(stored_permit, permit.upper())
        
        # Step 5: Driver confirms permit
        state, payload = self.conversation_handler.process_message(
            phone_number=self.new_driver.phone_number,
            message_text="1"  # Confirm
        )
        
        self.assertEqual(state, "DRIVER_LOCK_IDENTITY")
        
        # Verify driver profile was created
        driver_profile = DriverProfile.objects.filter(user=self.new_driver).first()
        self.assertIsNotNone(driver_profile)
        self.assertEqual(driver_profile.display_name, display_name)
        self.assertFalse(driver_profile.identity_locked)  # Not locked yet
        
        # Verify permit is hashed
        self.assertIsNotNone(driver_profile.permit_hash)
        self.assertIsNotNone(driver_profile.permit_salt)
        self.assertNotEqual(driver_profile.permit_hash, permit)  # Should be hashed
        
        # Step 6: Driver locks identity
        state, payload = self.conversation_handler.process_message(
            phone_number=self.new_driver.phone_number,
            message_text="1"  # Lock identity
        )
        
        self.assertEqual(state, "DRIVER_MAIN_MENU")
        
        # Verify identity is locked
        driver_profile.refresh_from_db()
        self.assertTrue(driver_profile.identity_locked)
        self.assertIsNotNone(driver_profile.identity_locked_at)
    
    def test_permit_already_exists(self):
        """Test that duplicate permit is rejected"""
        # Create driver with existing permit
        existing_driver = self.fixtures.create_driver(permit="CI1234567")
        
        # New driver tries to use same permit
        state = self.fixtures.create_conversation_state(
            user=self.new_driver,
            state="DRIVER_ENTER_PERMIT"
        )
        
        state, payload = self.conversation_handler.process_message(
            phone_number=self.new_driver.phone_number,
            message_text="CI1234567"
        )
        
        # Should remain in same state with error
        self.assertEqual(state, "DRIVER_ENTER_PERMIT")
        self.assertIn("déjà", payload.body.lower())
        
        # Verify no profile was created
        profile_count = DriverProfile.objects.filter(user=self.new_driver).count()
        self.assertEqual(profile_count, 0)
    
    def test_identity_lock_irreversible(self):
        """Test that once identity is locked, it cannot be unlocked"""
        # Create driver with locked identity
        driver_profile = self.fixtures.create_driver(identity_locked=True)
        
        # Try to unlock (should not be possible via service)
        # IdentityService should not have an unlock method, but verify the field
        driver_profile.refresh_from_db()
        self.assertTrue(driver_profile.identity_locked)
        
        # Verify no unlock method exists in IdentityService
        self.assertFalse(hasattr(self.identity_service, 'unlock_identity'))
    
    def test_permit_normalization(self):
        """Test that permit numbers are normalized correctly"""
        # Test various permit formats
        test_cases = [
            ("ci1234567", "CI1234567"),  # Lowercase
            ("CI1234567", "CI1234567"),  # Already uppercase
            ("ci 123 4567", "CI1234567"),  # With spaces
            ("ci-123-4567", "CI1234567"),  # With dashes
        ]
        
        for input_permit, expected_normalized in test_cases:
            normalized, is_valid, error = self.identity_service.normalize_and_validate_permit(input_permit)
            if is_valid:
                self.assertEqual(normalized, expected_normalized)

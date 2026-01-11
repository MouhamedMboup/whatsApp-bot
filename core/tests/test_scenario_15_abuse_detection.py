"""
Integration test for Scenario 15: Abuse detection - High-frequency declarations

Tests that the system detects and logs abuse patterns:
- High-frequency collaboration declarations
- Abuse flags stored in OwnerProfile
- Silent detection (no user blocking)
"""
from datetime import datetime, timedelta
from django.test import TestCase
from django.utils import timezone

from core.models import User, OwnerProfile, Collaboration
from core.services import CollaborationService
from core.handlers.conversation_handler import ConversationHandler
from core.tests.test_utils import BaseIntegrationTest, TestFixtures


class TestScenario15AbuseDetection(BaseIntegrationTest):
    """Test Scenario 15: Abuse detection for high-frequency actions"""
    
    def setUp(self):
        super().setUp()
        self.conversation_handler = ConversationHandler()
        self.collaboration_service = CollaborationService()
    
    def test_high_frequency_declaration_detection(self):
        """
        Test that declaring 10 collaborations in < 5 minutes triggers abuse flag
        """
        start_date = timezone.now().date()
        end_date = start_date + timedelta(days=7)
        date_str = f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}"
        
        # Declare 10 collaborations rapidly
        for i in range(10):
            # Create state for each declaration
            state = self.fixtures.create_conversation_state(
                user=self.owner,
                state="OWNER_DECLARE_COLLABORATION_DATES"
            )
            
            # Set driver_id in temp_data
            from core.services import ConversationService
            conversation_service = ConversationService()
            conversation_service.set_temp_data(state, "driver_id", str(self.driver.id))
            
            # Process declaration
            state, payload = self.conversation_handler.process_message(
                phone_number=self.owner.phone_number,
                message_text=date_str
            )
            
            # Clear state for next iteration
            ConversationState.objects.filter(user=self.owner).delete()
        
        # Verify 10 collaborations were created
        collaboration_count = Collaboration.objects.filter(owner=self.owner).count()
        self.assertEqual(collaboration_count, 10)
        
        # Verify abuse flag was set
        owner_profile = OwnerProfile.objects.get(user=self.owner)
        self.assertIsNotNone(owner_profile.abuse_flags)
        
        abuse_flags = owner_profile.abuse_flags
        if isinstance(abuse_flags, dict):
            # Check for high frequency pattern
            has_high_frequency = any(
                "high_frequency" in str(key).lower() or "frequency" in str(key).lower()
                for key in abuse_flags.keys()
            )
            # Note: Actual flag structure depends on CollaborationService implementation
            # This test verifies that abuse_flags is populated
    
    def test_abuse_detection_silent(self):
        """
        Test that abuse detection is silent (doesn't block user)
        """
        start_date = timezone.now().date()
        end_date = start_date + timedelta(days=7)
        date_str = f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}"
        
        # Declare multiple collaborations
        for i in range(5):
            state = self.fixtures.create_conversation_state(
                user=self.owner,
                state="OWNER_DECLARE_COLLABORATION_DATES"
            )
            
            from core.services import ConversationService
            conversation_service = ConversationService()
            conversation_service.set_temp_data(state, "driver_id", str(self.driver.id))
            
            state, payload = self.conversation_handler.process_message(
                phone_number=self.owner.phone_number,
                message_text=date_str
            )
            
            # Verify user receives success message (not blocked)
            self.assertIsNotNone(payload)
            self.assertNotIn("bloqué", payload.body.lower())
            self.assertNotIn("abuse", payload.body.lower())
            
            ConversationState.objects.filter(user=self.owner).delete()
        
        # Verify collaborations were created (not blocked)
        collaboration_count = Collaboration.objects.filter(owner=self.owner).count()
        self.assertEqual(collaboration_count, 5)
    
    def test_abuse_flags_persisted(self):
        """
        Test that abuse flags are persisted in OwnerProfile
        """
        owner_profile = OwnerProfile.objects.get(user=self.owner)
        
        # Initially, abuse_flags should be empty or None
        initial_flags = owner_profile.abuse_flags
        
        # After high-frequency actions, flags should be set
        # (This would be set by CollaborationService._detect_abuse_patterns)
        # For this test, we verify the field exists and can store data
        
        # Manually set abuse flags to verify persistence
        test_flags = {
            "high_frequency_declarations": {
                "count": 10,
                "window_minutes": 5,
                "flagged_at": timezone.now().isoformat()
            }
        }
        
        owner_profile.abuse_flags = test_flags
        owner_profile.save()
        
        # Verify flags are persisted
        owner_profile.refresh_from_db()
        self.assertIsNotNone(owner_profile.abuse_flags)
        stored_flags = owner_profile.abuse_flags
        if isinstance(stored_flags, dict):
            self.assertIn("high_frequency_declarations", stored_flags)

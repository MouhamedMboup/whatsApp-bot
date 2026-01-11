"""
Domain event assertion tests

Tests that domain events (RATING_CREATED, REPUTATION_UPDATED) are emitted
with expected payloads and that EventHandlerService reacts correctly.
"""
from django.test import TestCase
from unittest.mock import patch, MagicMock, call
from freezegun import freeze_time

from core.models import User, Collaboration, Rating, DriverProfile
from core.services import (
    RatingService, ReputationService, EventHandlerService,
    RatingCreatedEvent, ReputationUpdatedEvent
)
from core.tests.test_utils import BaseIntegrationTest, TestFixtures, MOCK_PATHS


class TestDomainEvents(BaseIntegrationTest):
    """Test domain event emission and handling"""
    
    def setUp(self):
        super().setUp()
        self.rating_service = RatingService()
        self.reputation_service = ReputationService()
        self.event_handler = EventHandlerService()
    
    @freeze_time("2024-01-01 12:00:00")
    @patch(MOCK_PATHS['llm_service'])
    @patch('core.services.rating_service.RatingService._emit_event')
    def test_rating_created_event_emitted(self, mock_emit_event, mock_llm):
        """
        Test that RATING_CREATED event is emitted with correct payload
        """
        mock_llm.return_value = ("Sanitized", True)
        
        collaboration = self.fixtures.create_collaboration(
            owner=self.owner,
            driver=self.driver,
            state="ELIGIBLE_FOR_RATING"
        )
        
        success, rating, error = self.rating_service.create_rating(
            owner=self.owner,
            collaboration=collaboration,
            scores={"punctuality": 4, "vehicle_respect": 5, "client_relation": 4, "reliability": 5},
            comment_raw="Great driver!"
        )
        
        self.assertTrue(success)
        
        # Verify event was emitted
        mock_emit_event.assert_called_once()
        
        # Get the event that was emitted
        call_args = mock_emit_event.call_args
        event = call_args[0][0]  # First positional argument
        
        # Verify event type and payload
        self.assertIsInstance(event, RatingCreatedEvent)
        self.assertEqual(event.rating_id, rating.id)
        self.assertEqual(event.collaboration_id, collaboration.id)
        self.assertEqual(event.driver_id, self.driver.id)
        self.assertIsNotNone(event.timestamp)
    
    @freeze_time("2024-01-01 12:00:00")
    @patch(MOCK_PATHS['llm_service'])
    @patch('core.services.event_handler.EventHandlerService.handle_rating_published')
    def test_rating_published_triggers_event_handler(self, mock_handler, mock_llm):
        """
        Test that rating publication triggers EventHandlerService
        """
        mock_llm.return_value = ("Sanitized", True)
        
        collaboration = self.fixtures.create_collaboration(
            owner=self.owner,
            driver=self.driver,
            state="ELIGIBLE_FOR_RATING"
        )
        
        rating = self.fixtures.create_rating(
            collaboration=collaboration,
            comment_raw="Great driver!",
            is_published=False
        )
        
        # Publish rating
        success = self.rating_service.approve_sanitized_comment(rating, self.owner)
        self.assertTrue(success)
        
        # Verify event handler was called
        mock_handler.assert_called_once_with(rating)
    
    @freeze_time("2024-01-01 12:00:00")
    @patch('core.services.reputation_service.ReputationService._emit_event')
    @patch(MOCK_PATHS['reputation_compute'])
    def test_reputation_updated_event_emitted(self, mock_compute, mock_emit_event):
        """
        Test that REPUTATION_UPDATED event is emitted with correct payload
        """
        mock_compute.return_value = {
            "score_median": 4.5,
            "state": "ESTABLISHED",
            "is_stale": False,
            "calculated_at": "2024-01-01T12:00:00Z"
        }
        
        driver_profile = DriverProfile.objects.get(user=self.driver)
        
        # Create some ratings to trigger reputation calculation
        collaboration = self.fixtures.create_collaboration(
            owner=self.owner,
            driver=self.driver,
            state="ELIGIBLE_FOR_RATING"
        )
        
        rating = self.fixtures.create_rating(
            collaboration=collaboration,
            is_published=True
        )
        
        # Trigger reputation calculation
        reputation = self.reputation_service.compute_reputation(
            driver_profile,
            force_recompute=True
        )
        
        # Verify event was emitted
        mock_emit_event.assert_called()
        
        # Get the last event
        if mock_emit_event.call_count > 0:
            call_args = mock_emit_event.call_args
            event = call_args[0][0]  # First positional argument
            
            # Verify event type and payload
            self.assertIsInstance(event, ReputationUpdatedEvent)
            self.assertEqual(event.driver_profile_id, driver_profile.id)
            self.assertEqual(event.score_median, 4.5)
            self.assertEqual(event.state, "ESTABLISHED")
            self.assertIsNotNone(event.timestamp)
    
    @freeze_time("2024-01-01 12:00:00")
    @patch(MOCK_PATHS['reputation_compute'])
    def test_event_handler_triggers_reputation_recalculation(self, mock_compute):
        """
        Test that EventHandlerService triggers reputation recalculation
        """
        mock_compute.return_value = {
            "score_median": 4.5,
            "state": "ESTABLISHED",
            "is_stale": False
        }
        
        driver_profile = DriverProfile.objects.get(user=self.driver)
        collaboration = self.fixtures.create_collaboration(
            owner=self.owner,
            driver=self.driver,
            state="ELIGIBLE_FOR_RATING"
        )
        
        rating = self.fixtures.create_rating(
            collaboration=collaboration,
            is_published=True
        )
        
        # Handle rating published event
        self.event_handler.handle_rating_published(rating)
        
        # Verify reputation was recalculated
        mock_compute.assert_called_once_with(driver_profile, force_recompute=True)
    
    def test_event_payload_minimal(self):
        """
        Test that events contain minimal required payload
        """
        collaboration = self.fixtures.create_collaboration(
            owner=self.owner,
            driver=self.driver,
            state="ELIGIBLE_FOR_RATING"
        )
        
        rating = self.fixtures.create_rating(
            collaboration=collaboration,
            is_published=False
        )
        
        # Create event
        event = RatingCreatedEvent(
            rating_id=rating.id,
            collaboration_id=collaboration.id,
            driver_id=self.driver.id
        )
        
        # Verify minimal payload
        self.assertIsNotNone(event.rating_id)
        self.assertIsNotNone(event.collaboration_id)
        self.assertIsNotNone(event.driver_id)
        self.assertIsNotNone(event.timestamp)

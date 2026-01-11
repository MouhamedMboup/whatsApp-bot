"""
Concurrency and race condition tests

Tests concurrent actions to ensure DB constraints and service validation
prevent duplicates and maintain data integrity.
"""
from django.test import TestCase
from django.db import transaction
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch
from freezegun import freeze_time

from core.models import User, Collaboration, Rating
from core.services import RatingService, CollaborationService
from core.tests.test_utils import BaseIntegrationTest, TestFixtures, MOCK_PATHS


class TestConcurrency(BaseIntegrationTest):
    """Test concurrent operations for race conditions"""
    
    def setUp(self):
        super().setUp()
        self.rating_service = RatingService()
        self.collaboration_service = CollaborationService()
    
    @freeze_time("2024-01-01 12:00:00")
    @patch(MOCK_PATHS['llm_service'])
    def test_concurrent_rating_creation_prevented(self, mock_llm):
        """
        Test that concurrent attempts to create rating for same collaboration
        are prevented by DB constraint and service validation
        """
        mock_llm.return_value = ("Sanitized", True)
        
        collaboration = self.fixtures.create_collaboration(
            owner=self.owner,
            driver=self.driver,
            state="ELIGIBLE_FOR_RATING"
        )
        
        scores = {"punctuality": 4, "vehicle_respect": 5, "client_relation": 4, "reliability": 5}
        
        def create_rating(attempt_num):
            """Attempt to create rating"""
            try:
                success, rating, error = self.rating_service.create_rating(
                    owner=self.owner,
                    collaboration=collaboration,
                    scores=scores,
                    comment_raw=f"Comment {attempt_num}"
                )
                return (success, rating, error, attempt_num)
            except Exception as e:
                return (False, None, str(e), attempt_num)
        
        # Attempt to create rating concurrently (5 attempts)
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(create_rating, i) for i in range(5)]
            results = [f.result() for f in as_completed(futures)]
        
        # Only one should succeed
        successful = [r for r in results if r[0]]
        self.assertEqual(len(successful), 1, "Only one rating should be created")
        
        # Verify only one rating exists in DB
        rating_count = Rating.objects.filter(collaboration=collaboration).count()
        self.assertEqual(rating_count, 1, "Only one rating should exist in database")
        
        # Verify failed attempts have appropriate error
        failed = [r for r in results if not r[0]]
        for success, rating, error, attempt_num in failed:
            self.assertIsNotNone(error)
            # Error should indicate rating already exists or constraint violation
    
    @freeze_time("2024-01-01 12:00:00")
    def test_concurrent_collaboration_declaration(self):
        """
        Test that concurrent collaboration declarations are handled correctly
        """
        start_date = "01/01/2024"
        end_date = "08/01/2024"
        
        def declare_collaboration(attempt_num):
            """Attempt to declare collaboration"""
            try:
                success, collaboration, error = self.collaboration_service.declare_collaboration(
                    owner=self.owner,
                    driver=self.driver,
                    start_date=start_date,
                    end_date=end_date
                )
                return (success, collaboration, error, attempt_num)
            except Exception as e:
                return (False, None, str(e), attempt_num)
        
        # Attempt to declare collaboration concurrently (3 attempts)
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(declare_collaboration, i) for i in range(3)]
            results = [f.result() for f in as_completed(futures)]
        
        # All should succeed (multiple collaborations allowed)
        successful = [r for r in results if r[0]]
        self.assertEqual(len(successful), 3, "All collaborations should be created")
        
        # Verify all collaborations exist
        collaboration_count = Collaboration.objects.filter(
            owner=self.owner,
            driver=self.driver
        ).count()
        self.assertEqual(collaboration_count, 3)
    
    @freeze_time("2024-01-01 12:00:00")
    @patch(MOCK_PATHS['reputation_compute'])
    def test_concurrent_reputation_recalculation(self, mock_compute):
        """
        Test that concurrent reputation recalculations are handled correctly
        (should be idempotent)
        """
        mock_compute.return_value = {
            "score_median": 4.5,
            "state": "ESTABLISHED",
            "is_stale": False
        }
        
        from core.services import ReputationService
        reputation_service = ReputationService()
        
        driver_profile = self.driver.driverprofile
        
        # Create multiple ratings
        for i in range(3):
            collaboration = self.fixtures.create_collaboration(
                owner=self.owner,
                driver=self.driver,
                state="ELIGIBLE_FOR_RATING"
            )
            self.fixtures.create_rating(
                collaboration=collaboration,
                is_published=True
            )
        
        def recalculate_reputation(attempt_num):
            """Attempt to recalculate reputation"""
            try:
                result = reputation_service.compute_reputation(
                    driver_profile,
                    force_recompute=True
                )
                return (True, result, None, attempt_num)
            except Exception as e:
                return (False, None, str(e), attempt_num)
        
        # Attempt concurrent recalculations (5 attempts)
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(recalculate_reputation, i) for i in range(5)]
            results = [f.result() for f in as_completed(futures)]
        
        # All should succeed (idempotent)
        successful = [r for r in results if r[0]]
        self.assertEqual(len(successful), 5, "All recalculations should succeed")
        
        # All should return same result (idempotent)
        results_data = [r[1] for r in successful]
        first_result = results_data[0]
        for result in results_data[1:]:
            self.assertEqual(result["score_median"], first_result["score_median"])
            self.assertEqual(result["state"], first_result["state"])

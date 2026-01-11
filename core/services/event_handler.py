"""
Service pour gérer les événements domaine

Contraintes strictes:
- Idempotent (peut être appelé plusieurs fois avec le même résultat)
- Pas d'appels WhatsApp
- Pas de changements d'état de conversation
- Pas de nouvelles décisions métier
- Actions déterministes uniquement (recalcul réputation, mise à jour cache)
"""
import logging
from typing import Dict, Any
from django.db import transaction

from ..models import DriverProfile, Collaboration, Rating
from .reputation_service import ReputationService, ReputationUpdatedEvent
from .rating_service import RatingCreatedEvent, DomainEvent

logger = logging.getLogger(__name__)


class EventHandlerService:
    """Service pour traiter les événements domaine"""
    
    @staticmethod
    @transaction.atomic
    def handle_event(event: DomainEvent) -> None:
        """
        Traite un événement domaine de manière idempotente
        
        Args:
            event: Événement domaine à traiter
        """
        event_type = event.event_type
        
        if event_type == "RATING_CREATED":
            EventHandlerService._handle_rating_created(event)
        elif event_type == "REPUTATION_UPDATED":
            # Événement de réputation - juste log (pas de réaction en chaîne)
            logger.debug(f"Réputation mise à jour: {event.data}")
        else:
            logger.warning(f"Type d'événement non géré: {event_type}")
    
    @staticmethod
    def _handle_rating_created(event: RatingCreatedEvent) -> None:
        """
        Traite l'événement RATING_CREATED
        
        Actions:
        - Invalide le cache de réputation du driver
        - Ne recalcule pas immédiatement (recalcul à la demande)
        """
        try:
            data = event.data
            driver_id = data.get("driver_id")
            
            if not driver_id:
                logger.warning("RATING_CREATED sans driver_id")
                return
            
            # Récupérer le driver profile
            try:
                from ..models import User
                driver = User.objects.get(id=driver_id)
                
                if not hasattr(driver, 'driver_profile'):
                    logger.debug(f"Driver {driver_id} n'a pas de profil - pas de réputation à invalider")
                    return
                
                driver_profile = driver.driver_profile
                
                # Invalider le cache (marquer comme stale)
                ReputationService.invalidate_cache(driver_profile)
                
                logger.info(
                    f"Cache de réputation invalidé pour driver {driver.phone_number} "
                    f"(événement: RATING_CREATED, rating_id: {data.get('rating_id')})"
                )
                
            except User.DoesNotExist:
                logger.warning(f"Driver {driver_id} introuvable pour RATING_CREATED")
                
        except Exception as e:
            logger.error(f"Erreur lors du traitement de RATING_CREATED: {str(e)}", exc_info=True)
    
    @staticmethod
    @transaction.atomic
    def handle_rating_published(rating: Rating) -> None:
        """
        Traite la publication d'un rating
        
        Actions:
        - Recalcule la réputation du driver (force_recompute=True)
        - Idempotent: peut être appelé plusieurs fois
        
        Args:
            rating: Rating publié
        """
        try:
            driver = rating.collaboration.driver
            
            if not hasattr(driver, 'driver_profile'):
                logger.debug(f"Driver {driver.id} n'a pas de profil - pas de réputation à recalculer")
                return
            
            driver_profile = driver.driver_profile
            
            # Recalculer la réputation (force_recompute pour garantir cohérence)
            success, reputation_data, error = ReputationService.compute_reputation(
                driver_profile,
                force_recompute=True
            )
            
            if success:
                logger.info(
                    f"Réputation recalculée pour driver {driver.phone_number} "
                    f"après publication rating {rating.id}: "
                    f"score={reputation_data.get('score_median')}, "
                    f"state={reputation_data.get('state')}"
                )
            else:
                logger.error(
                    f"Erreur lors du recalcul de réputation pour driver {driver.phone_number}: {error}"
                )
                
        except Exception as e:
            logger.error(f"Erreur lors du traitement de rating publié: {str(e)}", exc_info=True)
    
    @staticmethod
    @transaction.atomic
    def handle_driver_response(rating: Rating) -> None:
        """
        Traite la réponse d'un driver à un rating
        
        Actions:
        - Recalcule la réputation du driver (force_recompute=True)
        - Idempotent: peut être appelé plusieurs fois
        
        Args:
            rating: Rating avec réponse driver
        """
        try:
            driver = rating.collaboration.driver
            
            if not hasattr(driver, 'driver_profile'):
                logger.debug(f"Driver {driver.id} n'a pas de profil - pas de réputation à recalculer")
                return
            
            driver_profile = driver.driver_profile
            
            # Recalculer la réputation
            success, reputation_data, error = ReputationService.compute_reputation(
                driver_profile,
                force_recompute=True
            )
            
            if success:
                logger.info(
                    f"Réputation recalculée pour driver {driver.phone_number} "
                    f"après réponse à rating {rating.id}: "
                    f"score={reputation_data.get('score_median')}, "
                    f"state={reputation_data.get('state')}"
                )
            else:
                logger.error(
                    f"Erreur lors du recalcul de réputation pour driver {driver.phone_number}: {error}"
                )
                
        except Exception as e:
            logger.error(f"Erreur lors du traitement de réponse driver: {str(e)}", exc_info=True)
    
    @staticmethod
    @transaction.atomic
    def handle_collaboration_confirmed(collaboration: Collaboration) -> None:
        """
        Traite la confirmation d'une collaboration
        
        Actions:
        - Invalide le cache de réputation du driver
        - Ne recalcule pas immédiatement (recalcul à la demande)
        
        Args:
            collaboration: Collaboration confirmée
        """
        try:
            driver = collaboration.driver
            
            if not hasattr(driver, 'driver_profile'):
                logger.debug(f"Driver {driver.id} n'a pas de profil - pas de réputation à invalider")
                return
            
            driver_profile = driver.driver_profile
            
            # Invalider le cache (marquer comme stale)
            ReputationService.invalidate_cache(driver_profile)
            
            logger.info(
                f"Cache de réputation invalidé pour driver {driver.phone_number} "
                f"après confirmation collaboration {collaboration.id}"
            )
            
        except Exception as e:
            logger.error(f"Erreur lors du traitement de collaboration confirmée: {str(e)}", exc_info=True)

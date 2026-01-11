"""
Service pour la gestion des ratings (évaluations)

Contraintes strictes:
- Un seul rating par collaboration (enforced)
- Validation d'éligibilité via CollaborationService uniquement
- Stockage des scores bruts
- Envoi des commentaires à LLMService pour sanitisation
- Émission d'événements domaine (RATING_CREATED)
- Déterministe, testable, sans suppositions transport
"""
import logging
from typing import Dict, Optional, Tuple
from django.db import transaction
from django.utils import timezone

from ..models import User, Rating, Collaboration, ConsentLog
from .collaboration_service import CollaborationService

logger = logging.getLogger(__name__)


# Événements domaine
class DomainEvent:
    """Base class pour les événements domaine"""
    def __init__(self, event_type: str, data: Dict):
        self.event_type = event_type
        self.data = data
        self.timestamp = timezone.now()
    
    def to_dict(self) -> Dict:
        """Convertit l'événement en dict pour sérialisation"""
        return {
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp.isoformat()
        }


class RatingCreatedEvent(DomainEvent):
    """Événement émis lors de la création d'un rating"""
    def __init__(self, rating_id: int, collaboration_id: int, owner_id: int, driver_id: int):
        super().__init__(
            "RATING_CREATED",
            {
                "rating_id": rating_id,
                "collaboration_id": collaboration_id,
                "owner_id": owner_id,
                "driver_id": driver_id
            }
        )


class RatingService:
    """Service pour gérer les ratings"""
    
    # Maximum de rejets de sanitisation
    MAX_SANITIZATION_REJECTIONS = 3
    
    # Callback pour émettre des événements (peut être surchargé pour tests)
    _event_emitter = None
    
    @staticmethod
    def set_event_emitter(emitter_func):
        """
        Définit une fonction pour émettre des événements
        
        Args:
            emitter_func: Fonction qui prend un DomainEvent et l'émet
        """
        RatingService._event_emitter = emitter_func
    
    @staticmethod
    def _emit_event(event: DomainEvent) -> None:
        """
        Émet un événement domaine
        
        Args:
            event: Événement à émettre
        """
        if RatingService._event_emitter:
            RatingService._event_emitter(event)
        else:
            # Log par défaut si pas d'emitter configuré
            logger.info(f"DOMAIN_EVENT: {event.event_type} | {event.to_dict()}")
    
    @staticmethod
    @transaction.atomic
    def create_rating(
        owner: User,
        collaboration: Collaboration,
        score_punctuality: int = None,
        score_vehicle_respect: int = None,
        score_client_relation: int = None,
        score_reliability: int = None,
        scores: Optional[Dict[str, int]] = None,
        comment_raw: Optional[str] = None
    ) -> Tuple[bool, Optional[Rating], str]:
        """
        Crée un nouveau rating
        
        Args:
            owner: Utilisateur owner
            collaboration: Collaboration à noter
            score_punctuality: Score ponctualité (1-5) - ou utiliser scores dict
            score_vehicle_respect: Score respect véhicule (1-5)
            score_client_relation: Score relation client (1-5)
            score_reliability: Score fiabilité (1-5)
            scores: Dict avec scores (alternative to individual params)
            comment_raw: Commentaire brut (optionnel)
        
        Returns:
            Tuple (success, rating, error_message)
        """
        try:
            # Support both individual scores and scores dict
            if scores:
                score_punctuality = scores.get('punctuality', score_punctuality)
                score_vehicle_respect = scores.get('vehicle_respect', score_vehicle_respect)
                score_client_relation = scores.get('client_relation', score_client_relation)
                score_reliability = scores.get('reliability', score_reliability)
            
            if not all([score_punctuality, score_vehicle_respect, score_client_relation, score_reliability]):
                return False, None, "Tous les scores sont requis"
            
            # Refresh collaboration from DB to ensure we have latest state (important for concurrency)
            try:
                collaboration = Collaboration.objects.get(id=collaboration.id)
            except Collaboration.DoesNotExist:
                return False, None, "Collaboration introuvable"
            
            # Validation d'éligibilité via CollaborationService uniquement
            can_rate, reason = CollaborationService.can_rate_collaboration(owner, collaboration)
            if not can_rate:
                return False, None, reason
            
            # Vérifier qu'il n'y a pas déjà un rating
            # Check if rating already exists (DB unique constraint on collaboration_id prevents duplicates)
            # This check + DB constraint ensures exactly one rating is created under concurrency
            existing_rating = Rating.objects.filter(collaboration=collaboration).first()
            if existing_rating:
                return False, None, "Cette collaboration a déjà été notée"
            
            # Valider les scores
            scores = {
                'punctuality': score_punctuality,
                'vehicle_respect': score_vehicle_respect,
                'client_relation': score_client_relation,
                'reliability': score_reliability
            }
            
            for name, score in scores.items():
                if not (1 <= score <= 5):
                    return False, None, f"Score {name} invalide: {score} (doit être entre 1 et 5)"
            
            # Créer le rating (DB unique constraint on collaboration_id will prevent duplicates)
            # Under concurrency, multiple threads may pass the check above, but DB constraint ensures only one succeeds
            try:
                rating = Rating.objects.create(
                    collaboration=collaboration,
                    score_punctuality=score_punctuality,
                    score_vehicle_respect=score_vehicle_respect,
                    score_client_relation=score_client_relation,
                    score_reliability=score_reliability,
                    comment_raw=comment_raw if comment_raw else None,
                    is_published=False
                )
            except Exception as e:
                # If creation fails due to unique constraint or IntegrityError, rating already exists
                from django.db import IntegrityError
                if isinstance(e, IntegrityError) or 'unique' in str(e).lower() or 'duplicate' in str(e).lower() or 'already exists' in str(e).lower():
                    return False, None, "Cette collaboration a déjà été notée"
                # Re-raise other exceptions
                logger.error(f"Erreur lors de la création du rating: {str(e)}")
                raise
            
            # Marquer la collaboration comme notée
            CollaborationService.mark_as_rated(collaboration)
            
            # Émettre l'événement domaine
            event = RatingCreatedEvent(
                rating_id=rating.id,
                collaboration_id=collaboration.id,
                owner_id=owner.id,
                driver_id=collaboration.driver.id
            )
            RatingService._emit_event(event)
            
            # Traiter l'événement immédiatement (idempotent)
            from .event_handler import EventHandlerService
            EventHandlerService.handle_event(event)
            
            logger.info(
                f"Rating créé: {rating.id} pour collaboration {collaboration.id} "
                f"par owner {owner.phone_number}"
            )
            
            return True, rating, ""
            
        except Exception as e:
            logger.error(f"Erreur lors de la création du rating: {str(e)}")
            return False, None, f"Erreur lors de la création: {str(e)}"
    
    @staticmethod
    def sanitize_comment(
        rating: Rating,
        llm_service
    ) -> Tuple[bool, Optional[str], str]:
        """
        Envoie le commentaire brut à LLMService pour sanitisation
        
        Args:
            rating: Rating avec comment_raw
            llm_service: Instance de LLMService
        
        Returns:
            Tuple (success, sanitized_comment, error_message)
        """
        if not rating.comment_raw:
            return False, None, "Aucun commentaire brut à sanitiser"
        
        try:
            # Appeler LLMService pour sanitisation
            success, sanitized, error = llm_service.sanitize_comment(rating.comment_raw)
            
            if success:
                # Stocker le commentaire sanitisé
                rating.comment_sanitized = sanitized
                rating.save(update_fields=['comment_sanitized'])
                
                logger.info(f"Commentaire sanitisé pour rating {rating.id}")
                return True, sanitized, ""
            else:
                return False, None, error or "Erreur lors de la sanitisation"
                
        except Exception as e:
            logger.error(f"Erreur lors de la sanitisation: {str(e)}")
            return False, None, f"Erreur lors de la sanitisation: {str(e)}"
    
    @staticmethod
    @transaction.atomic
    def approve_sanitized_comment(
        rating: Rating,
        owner: User
    ) -> Tuple[bool, str]:
        """
        Approuve le commentaire sanitisé et le publie
        
        Args:
            rating: Rating avec comment_sanitized
            owner: Utilisateur owner (pour validation)
        
        Returns:
            Tuple (success, error_message)
        """
        try:
            # Vérifier que c'est bien l'owner
            if rating.collaboration.owner != owner:
                return False, "Vous n'êtes pas autorisé à approuver ce rating"
            
            if not rating.comment_sanitized:
                return False, "Aucun commentaire sanitisé à approuver"
            
            # Publier le commentaire
            rating.comment_published = rating.comment_sanitized
            rating.is_published = True
            rating.published_at = timezone.now()
            rating.save(update_fields=['comment_published', 'is_published', 'published_at'])
            
            # Enregistrer le consentement
            ConsentLog.objects.create(
                user=owner,
                action=ConsentLog.Action.RATING_PUBLICATION,
                context={'rating_id': rating.id, 'collaboration_id': rating.collaboration.id}
            )
            
            # Traiter la publication (recalcul réputation)
            from .event_handler import EventHandlerService
            EventHandlerService.handle_rating_published(rating)
            
            logger.info(f"Rating {rating.id} publié avec commentaire par {owner.phone_number}")
            return True, ""
            
        except Exception as e:
            logger.error(f"Erreur lors de l'approbation: {str(e)}")
            return False, f"Erreur lors de l'approbation: {str(e)}"
    
    @staticmethod
    @transaction.atomic
    def reject_sanitized_comment(
        rating: Rating,
        owner: User
    ) -> Tuple[bool, str, bool]:
        """
        Rejette le commentaire sanitisé
        
        Args:
            rating: Rating avec comment_sanitized
            owner: Utilisateur owner
        
        Returns:
            Tuple (success, error_message, can_retry)
            can_retry: True si l'owner peut modifier le commentaire, False si limite atteinte
        """
        try:
            # Vérifier que c'est bien l'owner
            if rating.collaboration.owner != owner:
                return False, "Vous n'êtes pas autorisé à rejeter ce rating", False
            
            # Incrémenter le compteur de rejets
            rating.sanitization_rejections_count += 1
            rating.save(update_fields=['sanitization_rejections_count'])
            
            # Vérifier si limite atteinte
            if rating.sanitization_rejections_count >= RatingService.MAX_SANITIZATION_REJECTIONS:
                # Limite atteinte - ne peut plus modifier
                logger.info(
                    f"Limite de rejets atteinte pour rating {rating.id} "
                    f"({rating.sanitization_rejections_count} rejets)"
                )
                return True, "", False
            
            # Peut encore modifier
            return True, "", True
            
        except Exception as e:
            logger.error(f"Erreur lors du rejet: {str(e)}")
            return False, f"Erreur lors du rejet: {str(e)}", False
    
    @staticmethod
    @transaction.atomic
    def update_raw_comment(
        rating: Rating,
        owner: User,
        new_comment_raw: str
    ) -> Tuple[bool, str]:
        """
        Met à jour le commentaire brut (après rejet)
        
        Args:
            rating: Rating à mettre à jour
            owner: Utilisateur owner
            new_comment_raw: Nouveau commentaire brut
        
        Returns:
            Tuple (success, error_message)
        """
        try:
            # Vérifier que c'est bien l'owner
            if rating.collaboration.owner != owner:
                return False, "Vous n'êtes pas autorisé à modifier ce rating"
            
            # Vérifier que la limite n'est pas atteinte
            if rating.sanitization_rejections_count >= RatingService.MAX_SANITIZATION_REJECTIONS:
                return False, "Limite de modifications atteinte (3 rejets maximum)"
            
            # Mettre à jour le commentaire brut et réinitialiser le sanitisé
            rating.comment_raw = new_comment_raw
            rating.comment_sanitized = None
            rating.save(update_fields=['comment_raw', 'comment_sanitized'])
            
            logger.info(f"Commentaire brut mis à jour pour rating {rating.id}")
            return True, ""
            
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour: {str(e)}")
            return False, f"Erreur lors de la mise à jour: {str(e)}"
    
    @staticmethod
    @transaction.atomic
    def publish_without_comment(
        rating: Rating,
        owner: User
    ) -> Tuple[bool, str]:
        """
        Publie le rating sans commentaire (après 3 rejets)
        
        Args:
            rating: Rating à publier
            owner: Utilisateur owner
        
        Returns:
            Tuple (success, error_message)
        """
        try:
            # Vérifier que c'est bien l'owner
            if rating.collaboration.owner != owner:
                return False, "Vous n'êtes pas autorisé à publier ce rating"
            
            # Vérifier que la limite est atteinte
            if rating.sanitization_rejections_count < RatingService.MAX_SANITIZATION_REJECTIONS:
                return False, "Vous pouvez encore modifier le commentaire"
            
            # Publier sans commentaire
            rating.comment_published = None
            rating.is_published = True
            rating.published_at = timezone.now()
            rating.save(update_fields=['comment_published', 'is_published', 'published_at'])
            
            # Enregistrer le consentement
            ConsentLog.objects.create(
                user=owner,
                action=ConsentLog.Action.RATING_PUBLICATION,
                context={
                    'rating_id': rating.id,
                    'collaboration_id': rating.collaboration.id,
                    'published_without_comment': True
                }
            )
            
            # Traiter la publication (recalcul réputation)
            from .event_handler import EventHandlerService
            EventHandlerService.handle_rating_published(rating)
            
            logger.info(f"Rating {rating.id} publié sans commentaire par {owner.phone_number}")
            return True, ""
            
        except Exception as e:
            logger.error(f"Erreur lors de la publication: {str(e)}")
            return False, f"Erreur lors de la publication: {str(e)}"
    
    @staticmethod
    def get_rating_by_id(rating_id: int) -> Optional[Rating]:
        """Récupère un rating par ID"""
        try:
            return Rating.objects.get(id=rating_id)
        except Rating.DoesNotExist:
            return None
    
    @staticmethod
    def get_rating_by_collaboration(collaboration: Collaboration) -> Optional[Rating]:
        """Récupère le rating d'une collaboration (s'il existe)"""
        try:
            return collaboration.rating
        except Rating.DoesNotExist:
            return None
    
    @staticmethod
    def get_rating_summary(rating: Rating) -> Dict:
        """
        Retourne un résumé d'un rating pour affichage
        
        Args:
            rating: Rating
        
        Returns:
            Dict avec informations du rating
        """
        return {
            "id": rating.id,
            "collaboration_id": rating.collaboration.id,
            "scores": {
                "punctuality": rating.score_punctuality,
                "vehicle_respect": rating.score_vehicle_respect,
                "client_relation": rating.score_client_relation,
                "reliability": rating.score_reliability,
                "median": rating.get_median_score()
            },
            "has_comment": rating.comment_published is not None,
            "is_published": rating.is_published,
            "sanitization_rejections_count": rating.sanitization_rejections_count,
            "created_at": rating.created_at.isoformat() if rating.created_at else None,
            "published_at": rating.published_at.isoformat() if rating.published_at else None,
        }

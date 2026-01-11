"""
Service pour le calcul de réputation des drivers

Contraintes strictes:
- Utilise MÉDIANE (pas moyenne)
- Pondération par durée de collaboration et récence (décroissance)
- Ignore complètement les commentaires
- Recomputable (idempotent)
- Ne modifie jamais les ratings (lecture seule)
- Émet des événements REPUTATION_UPDATED
- Pas de logique transport, pas d'AI, pas d'input utilisateur
"""
import logging
import statistics
from typing import Dict, Optional, Tuple, List
from datetime import timedelta
from django.db import transaction
from django.utils import timezone

from ..models import DriverProfile, Collaboration, Rating
from .rating_service import DomainEvent

logger = logging.getLogger(__name__)


class ReputationUpdatedEvent(DomainEvent):
    """Événement émis lors de la mise à jour de réputation"""
    def __init__(self, driver_profile_id: int, score_median: float, state: str):
        super().__init__(
            "REPUTATION_UPDATED",
            {
                "driver_profile_id": driver_profile_id,
                "score_median": score_median,
                "state": state
            }
        )


class ReputationService:
    """Service pour calculer la réputation des drivers"""
    
    # Seuils pour les états de réputation
    ESTABLISHED_THRESHOLD = 2  # 2-5 collaborations
    CONFIRMED_THRESHOLD = 5    # 5+ collaborations
    
    # Paramètres de pondération
    RECENT_DAYS = 90  # Collaborations récentes (≤90 jours)
    LONG_DURATION_DAYS = 30  # Collaborations longues (≥30 jours)
    RECENT_WEIGHT = 1.5  # Poids pour collaborations récentes
    LONG_WEIGHT = 1.3  # Poids pour collaborations longues
    NORMAL_WEIGHT = 1.0  # Poids normal
    
    # Décroissance pour récence (exponentielle)
    RECENCY_DECAY_HALFLIFE_DAYS = 60  # Moitié du poids après 60 jours
    
    # Callback pour émettre des événements
    _event_emitter = None
    
    @staticmethod
    def set_event_emitter(emitter_func):
        """Définit une fonction pour émettre des événements"""
        ReputationService._event_emitter = emitter_func
    
    @staticmethod
    def _emit_event(event: DomainEvent) -> None:
        """Émet un événement domaine"""
        if ReputationService._event_emitter:
            ReputationService._event_emitter(event)
        else:
            logger.info(f"DOMAIN_EVENT: {event.event_type} | {event.to_dict()}")
    
    @staticmethod
    @transaction.atomic
    def compute_reputation(
        driver_profile: DriverProfile,
        force_recompute: bool = False
    ) -> Tuple[bool, Dict, str]:
        """
        Calcule la réputation d'un driver
        
        Idempotent: peut être appelé plusieurs fois avec le même résultat
        
        Args:
            driver_profile: Profil driver
            force_recompute: Forcer le recalcul même si cache valide
        
        Returns:
            Tuple (success, reputation_data, error_message)
            reputation_data: {
                "score_median": float,
                "state": str,
                "calculated_at": str,
                "collaborations_count": int,
                "ratings_count": int
            }
        """
        try:
            # Vérifier le cache si pas de force_recompute
            if not force_recompute:
                cached = ReputationService._get_cached_reputation(driver_profile)
                if cached and not ReputationService._is_cache_stale(cached):
                    return True, cached, ""
            
            # Récupérer toutes les collaborations confirmées ou notées
            collaborations = Collaboration.objects.filter(
                driver=driver_profile.user,
                state__in=[
                    Collaboration.State.CONFIRMED,
                    Collaboration.State.ELIGIBLE_FOR_RATING,
                    Collaboration.State.RATED
                ]
            ).select_related('rating').order_by('-confirmed_at')
            
            # Filtrer les collaborations avec ratings publiés uniquement
            rated_collaborations = [
                collab for collab in collaborations
                if hasattr(collab, 'rating') and collab.rating.is_published
            ]
            
            if not rated_collaborations:
                # Pas de ratings - réputation NEW
                reputation_data = {
                    "score_median": 0.0,
                    "state": DriverProfile.ReputationState.NEW,
                    "calculated_at": timezone.now().isoformat(),
                    "collaborations_count": 0,
                    "ratings_count": 0,
                    "is_stale": False
                }
            else:
                # Calculer la réputation
                reputation_data = ReputationService._calculate_reputation(
                    rated_collaborations,
                    len(collaborations)
                )
            
            # Mettre à jour le cache
            driver_profile.reputation_cached = reputation_data
            driver_profile.save(update_fields=['reputation_cached'])
            
            # Émettre l'événement
            event = ReputationUpdatedEvent(
                driver_profile_id=driver_profile.id,
                score_median=reputation_data["score_median"],
                state=reputation_data["state"]
            )
            ReputationService._emit_event(event)
            
            logger.info(
                f"Réputation calculée pour driver {driver_profile.user.phone_number}: "
                f"score={reputation_data['score_median']:.2f}, "
                f"state={reputation_data['state']}, "
                f"ratings={reputation_data['ratings_count']}"
            )
            
            return True, reputation_data, ""
            
        except Exception as e:
            logger.error(f"Erreur lors du calcul de réputation: {str(e)}")
            return False, {}, f"Erreur lors du calcul: {str(e)}"
    
    @staticmethod
    def _calculate_reputation(
        rated_collaborations: List[Collaboration],
        total_collaborations_count: int
    ) -> Dict:
        """
        Calcule la réputation à partir des collaborations notées
        
        Args:
            rated_collaborations: Liste des collaborations avec ratings publiés
            total_collaborations_count: Nombre total de collaborations confirmées
        
        Returns:
            Dict avec les données de réputation
        """
        # Extraire les scores globaux (moyenne des 4 critères par rating)
        weighted_scores = []
        
        for collab in rated_collaborations:
            rating = collab.rating
            
            # Score global = moyenne des 4 critères
            score_global = (
                rating.score_punctuality +
                rating.score_vehicle_respect +
                rating.score_client_relation +
                rating.score_reliability
            ) / 4.0
            
            # Calculer le poids
            weight = ReputationService._calculate_weight(collab, rating)
            
            # Ajouter le score pondéré (répété selon le poids)
            # Pour la médiane, on répète le score selon le poids
            # (approximation: on arrondit le poids pour avoir des entiers)
            weight_int = max(1, int(round(weight)))
            weighted_scores.extend([score_global] * weight_int)
        
        # Calculer la médiane
        if weighted_scores:
            score_median = statistics.median(weighted_scores)
        else:
            score_median = 0.0
        
        # Déterminer l'état
        state = ReputationService._determine_state(
            total_collaborations_count,
            rated_collaborations,
            score_median
        )
        
        return {
            "score_median": round(score_median, 2),
            "state": state,
            "calculated_at": timezone.now().isoformat(),
            "collaborations_count": total_collaborations_count,
            "ratings_count": len(rated_collaborations),
            "is_stale": False
        }
    
    @staticmethod
    def _calculate_weight(
        collaboration: Collaboration,
        rating: Rating
    ) -> float:
        """
        Calcule le poids d'un rating selon durée et récence
        
        Args:
            collaboration: Collaboration
            rating: Rating
        
        Returns:
            Poids (float)
        """
        weight = ReputationService.NORMAL_WEIGHT
        
        # Pondération par durée
        if collaboration.duration_days and collaboration.duration_days >= ReputationService.LONG_DURATION_DAYS:
            weight *= ReputationService.LONG_WEIGHT
        
        # Pondération par récence (décroissance exponentielle)
        if rating.published_at:
            days_ago = (timezone.now() - rating.published_at).days
            
            if days_ago <= ReputationService.RECENT_DAYS:
                # Collaborations récentes pèsent plus
                weight *= ReputationService.RECENT_WEIGHT
            else:
                # Décroissance exponentielle
                # Poids = RECENT_WEIGHT * 2^(-days_ago / HALFLIFE)
                decay_factor = 2.0 ** (-days_ago / ReputationService.RECENCY_DECAY_HALFLIFE_DAYS)
                weight *= ReputationService.RECENT_WEIGHT * decay_factor
        
        return weight
    
    @staticmethod
    def _determine_state(
        total_collaborations_count: int,
        rated_collaborations: List[Collaboration],
        score_median: float
    ) -> str:
        """
        Détermine l'état de réputation
        
        Args:
            total_collaborations_count: Nombre total de collaborations
            rated_collaborations: Collaborations notées
            score_median: Score médian
        
        Returns:
            État de réputation (NEW, ESTABLISHED, CONFIRMED, REBUILDING)
        """
        # REBUILDING: Tendance négative récente (dernière collaboration ≤30 jours avec score < 3.0)
        if rated_collaborations:
            latest_collab = rated_collaborations[0]  # Déjà trié par -confirmed_at
            latest_rating = latest_collab.rating
            
            if latest_rating.published_at:
                days_since_latest = (timezone.now() - latest_rating.published_at).days
                
                if days_since_latest <= 30:
                    latest_score = (
                        latest_rating.score_punctuality +
                        latest_rating.score_vehicle_respect +
                        latest_rating.score_client_relation +
                        latest_rating.score_reliability
                    ) / 4.0
                    
                    if latest_score < 3.0:
                        return DriverProfile.ReputationState.REBUILDING
        
        # États selon nombre de collaborations
        if total_collaborations_count == 0:
            return DriverProfile.ReputationState.NEW
        elif total_collaborations_count == 1:
            return DriverProfile.ReputationState.NEW
        elif total_collaborations_count < ReputationService.CONFIRMED_THRESHOLD:
            return DriverProfile.ReputationState.ESTABLISHED
        else:
            return DriverProfile.ReputationState.CONFIRMED
    
    @staticmethod
    def _get_cached_reputation(driver_profile: DriverProfile) -> Optional[Dict]:
        """Récupère la réputation en cache"""
        return driver_profile.reputation_cached
    
    @staticmethod
    def _is_cache_stale(cached_data: Dict) -> bool:
        """
        Vérifie si le cache est stale (>24h)
        
        Args:
            cached_data: Données en cache
        
        Returns:
            True si stale, False sinon
        """
        if not cached_data or not cached_data.get("calculated_at"):
            return True
        
        try:
            from datetime import datetime
            calculated_at = datetime.fromisoformat(cached_data["calculated_at"].replace('Z', '+00:00'))
            if timezone.is_aware(calculated_at):
                calculated_at = timezone.make_naive(calculated_at)
            age_hours = (timezone.now() - timezone.make_aware(calculated_at)).total_seconds() / 3600
            return age_hours > 24
        except Exception:
            return True
    
    @staticmethod
    def get_reputation(
        driver_profile: DriverProfile,
        force_recompute: bool = False
    ) -> Dict:
        """
        Récupère la réputation (avec cache)
        
        Args:
            driver_profile: Profil driver
            force_recompute: Forcer le recalcul
        
        Returns:
            Dict avec les données de réputation
        """
        success, reputation_data, error = ReputationService.compute_reputation(
            driver_profile, force_recompute
        )
        
        if success:
            return reputation_data
        else:
            # Retourner cache même si erreur de recalcul
            cached = ReputationService._get_cached_reputation(driver_profile)
            if cached:
                return cached
            # Sinon, retourner valeurs par défaut
            return {
                "score_median": 0.0,
                "state": DriverProfile.ReputationState.NEW,
                "calculated_at": timezone.now().isoformat(),
                "collaborations_count": 0,
                "ratings_count": 0,
                "is_stale": True
            }
    
    @staticmethod
    def invalidate_cache(driver_profile: DriverProfile) -> None:
        """
        Invalide le cache de réputation (marque comme stale)
        
        Args:
            driver_profile: Profil driver
        """
        if driver_profile.reputation_cached:
            driver_profile.reputation_cached["is_stale"] = True
            driver_profile.save(update_fields=['reputation_cached'])
            logger.info(f"Cache invalidé pour driver {driver_profile.user.phone_number}")

"""
Service pour la gestion des collaborations entre Owner et Driver
"""
import logging
from typing import Dict, Optional, Tuple, List
from datetime import date, timedelta
from django.db import transaction
from django.utils import timezone

from django.db import models
from ..models import User, Collaboration, OwnerProfile, ConsentLog

logger = logging.getLogger(__name__)


class CollaborationService:
    """Service pour gérer les collaborations"""
    
    # Durée minimale requise pour éligibilité au rating (jours)
    MIN_DURATION_DAYS = 7
    
    @staticmethod
    @transaction.atomic
    def declare_collaboration(
        owner: User,
        driver: User,
        start_date: date,
        end_date: date
    ) -> Tuple[bool, Optional[Collaboration], str]:
        """
        Déclare une nouvelle collaboration par un owner
        
        Args:
            owner: Utilisateur owner
            driver: Utilisateur driver
            start_date: Date de début
            end_date: Date de fin
        
        Returns:
            Tuple (success, collaboration, error_message)
        """
        try:
            # Validation de base
            if owner.role != User.Role.OWNER:
                return False, None, "L'utilisateur n'est pas un owner"
            
            if driver.role != User.Role.DRIVER:
                return False, None, "L'utilisateur cible n'est pas un driver"
            
            if owner == driver:
                return False, None, "Un owner ne peut pas collaborer avec lui-même"
            
            # Valider les dates
            if start_date > end_date:
                return False, None, "La date de début doit être antérieure à la date de fin"
            
            # Calculer la durée
            duration_days = (end_date - start_date).days + 1  # Inclusif
            
            if duration_days < CollaborationService.MIN_DURATION_DAYS:
                return False, None, f"La durée minimale est de {CollaborationService.MIN_DURATION_DAYS} jours"
            
            # Vérifier s'il existe déjà une collaboration active entre ces deux utilisateurs
            existing = Collaboration.objects.filter(
                owner=owner,
                driver=driver,
                state__in=[
                    Collaboration.State.DECLARED,
                    Collaboration.State.PENDING_CONFIRMATION,
                    Collaboration.State.CONFIRMED,
                    Collaboration.State.ELIGIBLE_FOR_RATING
                ]
            ).first()
            
            if existing:
                return False, None, "Une collaboration active existe déjà entre ces utilisateurs"
            
            # Créer la collaboration
            collaboration = Collaboration.objects.create(
                owner=owner,
                driver=driver,
                state=Collaboration.State.DECLARED,
                start_date=start_date,
                end_date=end_date,
                duration_days=duration_days
            )
            
            # Mettre à jour OwnerProfile
            owner_profile, _ = OwnerProfile.objects.get_or_create(user=owner)
            owner_profile.collaborations_declared_count += 1
            owner_profile.last_declaration_at = timezone.now()
            owner_profile.save(update_fields=['collaborations_declared_count', 'last_declaration_at'])
            
            # Transition vers PENDING_CONFIRMATION
            CollaborationService._transition_to_pending_confirmation(collaboration)
            
            # Détection d'abus (silencieuse)
            CollaborationService._detect_abuse_patterns(owner_profile)
            
            logger.info(
                f"Collaboration déclarée: {owner.phone_number} -> {driver.phone_number} "
                f"({start_date} à {end_date}, {duration_days} jours)"
            )
            
            return True, collaboration, ""
            
        except Exception as e:
            logger.error(f"Erreur lors de la déclaration de collaboration: {str(e)}")
            return False, None, f"Erreur lors de la déclaration: {str(e)}"
    
    @staticmethod
    def _transition_to_pending_confirmation(collaboration: Collaboration) -> None:
        """Transition DECLARED → PENDING_CONFIRMATION"""
        collaboration.state = Collaboration.State.PENDING_CONFIRMATION
        collaboration.save(update_fields=['state'])
    
    @staticmethod
    @transaction.atomic
    def confirm_collaboration(
        driver: User,
        collaboration: Collaboration
    ) -> Tuple[bool, str]:
        """
        Confirme une collaboration par le driver
        
        Args:
            driver: Utilisateur driver
            collaboration: Collaboration à confirmer
        
        Returns:
            Tuple (success, error_message)
        """
        try:
            # Validation
            if driver != collaboration.driver:
                return False, "Vous n'êtes pas autorisé à confirmer cette collaboration"
            
            if collaboration.state != Collaboration.State.PENDING_CONFIRMATION:
                return False, f"La collaboration n'est pas en attente de confirmation (état actuel: {collaboration.state})"
            
            # Vérifier que le driver a un profil et que l'identité est verrouillée
            if not hasattr(driver, 'driver_profile'):
                return False, "Vous devez créer un profil avant de confirmer une collaboration"
            
            if not driver.driver_profile.identity_locked:
                return False, "Vous devez verrouiller votre identité avant de confirmer une collaboration"
            
            # Transition vers CONFIRMED
            collaboration.state = Collaboration.State.CONFIRMED
            collaboration.confirmed_at = timezone.now()
            collaboration.save(update_fields=['state', 'confirmed_at'])
            
            # Enregistrer le consentement
            ConsentLog.objects.create(
                user=driver,
                action=ConsentLog.Action.COLLABORATION_CONFIRMATION,
                context={'collaboration_id': collaboration.id}
            )
            
            # Vérifier si éligible pour rating (≥7 jours)
            if collaboration.duration_days >= CollaborationService.MIN_DURATION_DAYS:
                CollaborationService._transition_to_eligible_for_rating(collaboration)
            
            # Traiter la confirmation (invalidation cache réputation)
            from .event_handler import EventHandlerService
            EventHandlerService.handle_collaboration_confirmed(collaboration)
            
            logger.info(
                f"Collaboration confirmée: {collaboration.id} par {driver.phone_number}"
            )
            
            return True, ""
            
        except Exception as e:
            logger.error(f"Erreur lors de la confirmation: {str(e)}")
            return False, f"Erreur lors de la confirmation: {str(e)}"
    
    @staticmethod
    def _transition_to_eligible_for_rating(collaboration: Collaboration) -> None:
        """Transition CONFIRMED → ELIGIBLE_FOR_RATING"""
        if collaboration.state == Collaboration.State.CONFIRMED:
            if collaboration.duration_days >= CollaborationService.MIN_DURATION_DAYS:
                collaboration.state = Collaboration.State.ELIGIBLE_FOR_RATING
                collaboration.save(update_fields=['state'])
                logger.info(f"Collaboration {collaboration.id} éligible pour rating")
    
    @staticmethod
    @transaction.atomic
    def mark_as_rated(collaboration: Collaboration) -> bool:
        """
        Marque une collaboration comme notée
        
        Args:
            collaboration: Collaboration à marquer
        
        Returns:
            True si succès, False sinon
        """
        try:
            if collaboration.state != Collaboration.State.ELIGIBLE_FOR_RATING:
                logger.warning(
                    f"Tentative de marquer collaboration {collaboration.id} comme notée "
                    f"alors qu'elle est en état {collaboration.state}"
                )
                return False
            
            collaboration.state = Collaboration.State.RATED
            collaboration.save(update_fields=['state'])
            
            logger.info(f"Collaboration {collaboration.id} marquée comme notée")
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors du marquage comme notée: {str(e)}")
            return False
    
    @staticmethod
    def get_eligible_collaborations_for_rating(owner: User) -> List[Collaboration]:
        """
        Récupère les collaborations éligibles pour rating d'un owner
        
        Args:
            owner: Utilisateur owner
        
        Returns:
            Liste des collaborations éligibles
        """
        return Collaboration.objects.filter(
            owner=owner,
            state=Collaboration.State.ELIGIBLE_FOR_RATING
        ).order_by('-confirmed_at')
    
    @staticmethod
    def get_pending_confirmations_for_driver(driver: User) -> List[Collaboration]:
        """
        Récupère les collaborations en attente de confirmation d'un driver
        
        Args:
            driver: Utilisateur driver
        
        Returns:
            Liste des collaborations en attente
        """
        return Collaboration.objects.filter(
            driver=driver,
            state=Collaboration.State.PENDING_CONFIRMATION
        ).order_by('-declared_at')
    
    @staticmethod
    def get_collaboration_by_id(collaboration_id: int) -> Optional[Collaboration]:
        """Récupère une collaboration par ID"""
        try:
            return Collaboration.objects.get(id=collaboration_id)
        except Collaboration.DoesNotExist:
            return None
    
    @staticmethod
    def can_rate_collaboration(
        owner: User,
        collaboration: Collaboration
    ) -> Tuple[bool, str]:
        """
        Vérifie si un owner peut noter une collaboration
        
        Args:
            owner: Utilisateur owner
            collaboration: Collaboration à vérifier
        
        Returns:
            Tuple (can_rate, reason)
        """
        if collaboration.owner != owner:
            return False, "Vous n'êtes pas l'owner de cette collaboration"
        
        if collaboration.state != Collaboration.State.ELIGIBLE_FOR_RATING:
            return False, f"La collaboration n'est pas éligible pour rating (état: {collaboration.state})"
        
        if collaboration.duration_days < CollaborationService.MIN_DURATION_DAYS:
            return False, f"La durée minimale de {CollaborationService.MIN_DURATION_DAYS} jours n'est pas atteinte"
        
        # Vérifier si déjà notée
        if hasattr(collaboration, 'rating'):
            return False, "Cette collaboration a déjà été notée"
        
        # Vérifier que le driver a verrouillé son identité
        if not hasattr(collaboration.driver, 'driver_profile'):
            return False, "Le driver n'a pas de profil"
        
        if not collaboration.driver.driver_profile.identity_locked:
            return False, "L'identité du driver n'est pas verrouillée"
        
        return True, ""
    
    @staticmethod
    def _detect_abuse_patterns(owner_profile: OwnerProfile) -> None:
        """
        Détecte les patterns d'abus (silencieux, non-bloquant)
        
        Patterns détectés:
        - Spam: >3 déclarations en <24h
        - Paires répétées: même owner-driver >2 fois en <30 jours
        """
        from datetime import timedelta
        
        flags = owner_profile.abuse_flags or {}
        
        # Pattern 1: Spam de collaborations
        recent_declarations = Collaboration.objects.filter(
            owner=owner_profile.user,
            declared_at__gte=timezone.now() - timedelta(hours=24)
        ).count()
        
        if recent_declarations > 3:
            flags['spam_collaborations'] = True
            flags['spam_collaborations_count'] = recent_declarations
            flags['spam_collaborations_detected_at'] = timezone.now().isoformat()
            logger.info(
                f"Pattern spam détecté pour owner {owner_profile.user.phone_number}: "
                f"{recent_declarations} déclarations en 24h"
            )
        
        # Pattern 2: Paires répétées
        # Compter les collaborations avec les mêmes drivers dans les 30 derniers jours
        recent_collaborations = Collaboration.objects.filter(
            owner=owner_profile.user,
            declared_at__gte=timezone.now() - timedelta(days=30)
        ).values('driver').annotate(count=models.Count('id'))
        
        for item in recent_collaborations:
            if item['count'] > 2:
                driver_id = item['driver']
                if 'repeated_pairs' not in flags:
                    flags['repeated_pairs'] = []
                flags['repeated_pairs'].append({
                    'driver_id': driver_id,
                    'count': item['count'],
                    'detected_at': timezone.now().isoformat()
                })
                logger.info(
                    f"Pattern paires répétées détecté pour owner {owner_profile.user.phone_number}: "
                    f"driver {driver_id} déclaré {item['count']} fois en 30 jours"
                )
        
        # Mettre à jour les flags (silencieux, non-bloquant)
        if flags:
            owner_profile.abuse_flags = flags
            owner_profile.save(update_fields=['abuse_flags'])
    
    @staticmethod
    def get_collaboration_summary(collaboration: Collaboration) -> Dict:
        """
        Retourne un résumé d'une collaboration pour affichage
        
        Args:
            collaboration: Collaboration
        
        Returns:
            Dict avec informations de la collaboration
        """
        return {
            "id": collaboration.id,
            "owner_phone": collaboration.owner.phone_number,
            "driver_phone": collaboration.driver.phone_number,
            "state": collaboration.state,
            "start_date": collaboration.start_date.isoformat() if collaboration.start_date else None,
            "end_date": collaboration.end_date.isoformat() if collaboration.end_date else None,
            "duration_days": collaboration.duration_days,
            "declared_at": collaboration.declared_at.isoformat() if collaboration.declared_at else None,
            "confirmed_at": collaboration.confirmed_at.isoformat() if collaboration.confirmed_at else None,
            "is_rated": hasattr(collaboration, 'rating'),
        }

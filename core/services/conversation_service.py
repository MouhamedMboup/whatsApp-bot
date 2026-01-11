"""
Service de gestion des états de conversation (framework-agnostic)

Ce service gère uniquement la persistance et la logique des états de conversation.
Il ne dépend pas de Django ni de WhatsApp - il retourne des structures de données abstraites.
"""
import logging
from typing import Dict, Optional, Tuple, Any
from datetime import timedelta
from django.utils import timezone
from django.db import transaction

from ..models import User, ConversationState

logger = logging.getLogger(__name__)


class ConversationService:
    """
    Service de gestion des états de conversation
    
    Framework-agnostic: retourne des structures de données abstraites,
    ne fait aucune supposition sur le format des messages ou le transport.
    """
    
    # Timeout par défaut: 30 minutes
    DEFAULT_TIMEOUT_MINUTES = 30
    
    @staticmethod
    def get_or_create_state(
        user: User,
        initial_state: str,
        expected_input_type: str,
        allowed_values: Optional[list] = None,
        behavior_on_invalid_input: str = "RETRY_SAME_STATE",
        timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES
    ) -> ConversationState:
        """
        Récupère ou crée un état de conversation pour un utilisateur
        
        Args:
            user: Utilisateur (User model)
            initial_state: Nom de l'état initial
            expected_input_type: Type d'entrée attendu (MENU_CHOICE, CONFIRMATION, etc.)
            allowed_values: Valeurs acceptées (liste de strings/ints)
            behavior_on_invalid_input: Comportement sur entrée invalide
            timeout_minutes: Timeout en minutes
        
        Returns:
            ConversationState créé ou existant
        """
        state, created = ConversationState.objects.get_or_create(
            user=user,
            defaults={
                "current_state": initial_state,
                "temp_data": {},
                "expected_input_type": expected_input_type,
                "allowed_values": allowed_values or [],
                "invalid_input_count": 0,
                "behavior_on_invalid_input": behavior_on_invalid_input,
                "expires_at": timezone.now() + timedelta(minutes=timeout_minutes)
            }
        )
        
        if not created:
            # Mettre à jour l'état existant
            state.current_state = initial_state
            state.temp_data = {}
            state.expected_input_type = expected_input_type
            state.allowed_values = allowed_values or []
            state.invalid_input_count = 0
            state.behavior_on_invalid_input = behavior_on_invalid_input
            state.expires_at = timezone.now() + timedelta(minutes=timeout_minutes)
            state.save()
        
        return state
    
    @staticmethod
    def get_current_state(user: User) -> Optional[ConversationState]:
        """
        Récupère l'état de conversation actuel d'un utilisateur
        
        Args:
            user: Utilisateur
        
        Returns:
            ConversationState ou None si aucun état
        """
        try:
            return ConversationState.objects.filter(user=user).latest('last_updated')
        except ConversationState.DoesNotExist:
            return None
    
    @staticmethod
    def update_state(
        state: ConversationState,
        new_state: Optional[str] = None,
        temp_data: Optional[Dict] = None,
        expected_input_type: Optional[str] = None,
        allowed_values: Optional[list] = None,
        behavior_on_invalid_input: Optional[str] = None,
        reset_invalid_count: bool = False,
        extend_timeout: bool = True,
        timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES
    ) -> ConversationState:
        """
        Met à jour un état de conversation
        
        Args:
            state: État à mettre à jour
            new_state: Nouvel état (optionnel)
            temp_data: Nouvelles données temporaires (fusionnées avec existantes si dict)
            expected_input_type: Nouveau type d'entrée attendu
            allowed_values: Nouvelles valeurs autorisées
            behavior_on_invalid_input: Nouveau comportement
            reset_invalid_count: Réinitialiser le compteur d'erreurs
            extend_timeout: Étendre le timeout
            timeout_minutes: Nouveau timeout si extend_timeout=True
        
        Returns:
            ConversationState mis à jour
        """
        if new_state:
            state.current_state = new_state
        
        if temp_data is not None:
            if isinstance(temp_data, dict) and state.temp_data:
                # Fusionner avec les données existantes
                state.temp_data = {**state.temp_data, **temp_data}
            else:
                state.temp_data = temp_data
        
        if expected_input_type:
            state.expected_input_type = expected_input_type
        
        if allowed_values is not None:
            state.allowed_values = allowed_values
        
        if behavior_on_invalid_input:
            state.behavior_on_invalid_input = behavior_on_invalid_input
        
        if reset_invalid_count:
            state.invalid_input_count = 0
        
        if extend_timeout:
            state.expires_at = timezone.now() + timedelta(minutes=timeout_minutes)
        
        state.save()
        return state
    
    @staticmethod
    def increment_invalid_input(state: ConversationState) -> ConversationState:
        """
        Incrémente le compteur d'entrées invalides
        
        Args:
            state: État à mettre à jour
        
        Returns:
            ConversationState mis à jour
        """
        state.invalid_input_count += 1
        state.save()
        return state
    
    @staticmethod
    def is_expired(state: ConversationState) -> bool:
        """
        Vérifie si un état a expiré
        
        Args:
            state: État à vérifier
        
        Returns:
            True si expiré, False sinon
        """
        return state.is_expired()
    
    @staticmethod
    def delete_state(state: ConversationState) -> None:
        """
        Supprime un état de conversation
        
        Args:
            state: État à supprimer
        """
        state.delete()
    
    @staticmethod
    def get_temp_data(state: ConversationState, key: str, default: Any = None) -> Any:
        """
        Récupère une valeur des données temporaires
        
        Args:
            state: État de conversation
            key: Clé à récupérer
            default: Valeur par défaut
        
        Returns:
            Valeur ou default
        """
        return state.temp_data.get(key, default)
    
    @staticmethod
    def set_temp_data(state: ConversationState, key: str, value: Any) -> ConversationState:
        """
        Définit une valeur dans les données temporaires
        
        Args:
            state: État de conversation
            key: Clé à définir
            value: Valeur
        
        Returns:
            ConversationState mis à jour
        """
        if not isinstance(state.temp_data, dict):
            state.temp_data = {}
        
        state.temp_data[key] = value
        state.save()
        return state
    
    @staticmethod
    def clear_temp_data(state: ConversationState) -> ConversationState:
        """
        Efface toutes les données temporaires
        
        Args:
            state: État de conversation
        
        Returns:
            ConversationState mis à jour
        """
        state.temp_data = {}
        state.save()
        return state
    
    @staticmethod
    def transition_to_state(
        user: User,
        new_state: str,
        expected_input_type: str,
        allowed_values: Optional[list] = None,
        behavior_on_invalid_input: str = "RETRY_SAME_STATE",
        temp_data: Optional[Dict] = None,
        timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES
    ) -> ConversationState:
        """
        Transition vers un nouvel état
        
        Args:
            user: Utilisateur
            new_state: Nouvel état
            expected_input_type: Type d'entrée attendu
            allowed_values: Valeurs autorisées
            behavior_on_invalid_input: Comportement sur erreur
            temp_data: Données temporaires initiales
            timeout_minutes: Timeout
        
        Returns:
            Nouvel état créé
        """
        # Supprimer l'ancien état s'il existe
        old_state = ConversationService.get_current_state(user)
        if old_state:
            ConversationService.delete_state(old_state)
        
        # Créer le nouvel état
        return ConversationService.get_or_create_state(
            user=user,
            initial_state=new_state,
            expected_input_type=expected_input_type,
            allowed_values=allowed_values,
            behavior_on_invalid_input=behavior_on_invalid_input,
            timeout_minutes=timeout_minutes
        )
    
    @staticmethod
    def get_state_info(state: ConversationState) -> Dict[str, Any]:
        """
        Retourne les informations d'un état sous forme de dict abstrait
        
        Utilisé pour passer l'état au conversation handler sans dépendance framework
        
        Args:
            state: État de conversation
        
        Returns:
            Dict avec les informations de l'état
        """
        return {
            "current_state": state.current_state,
            "expected_input_type": state.expected_input_type,
            "allowed_values": state.allowed_values or [],
            "invalid_input_count": state.invalid_input_count,
            "behavior_on_invalid_input": state.behavior_on_invalid_input,
            "temp_data": state.temp_data or {},
            "expires_at": state.expires_at.isoformat() if state.expires_at else None,
            "is_expired": state.is_expired()
        }
    
    @staticmethod
    def cleanup_expired_states(older_than_hours: int = 24) -> int:
        """
        Nettoie les états expirés plus anciens que X heures
        
        Args:
            older_than_hours: Supprimer les états expirés depuis plus de X heures
        
        Returns:
            Nombre d'états supprimés
        """
        cutoff = timezone.now() - timedelta(hours=older_than_hours)
        deleted_count, _ = ConversationState.objects.filter(
            expires_at__lt=cutoff
        ).delete()
        
        logger.info(f"Supprimé {deleted_count} états de conversation expirés")
        return deleted_count

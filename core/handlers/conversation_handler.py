"""
Conversation Handler - Orchestrateur principal du flux conversationnel

Responsabilités:
- Recevoir input parsé (de WhatsAppService)
- Lire/écrire état via ConversationService
- Appeler services métier (Identity, Collaboration, Rating)
- Retourner next_state et message_payload abstrait

Contraintes strictes:
- Pas de persistance directe
- Pas de formatage WhatsApp
- Pas d'appels API Meta
- Logique déterministe uniquement
- Machine à états explicite
"""
import logging
import re
from typing import Dict, Optional, Tuple, List, Any
from django.db.models import Q
from django.db import transaction

from ..models import User, ConversationState, DriverProfile
from ..services.conversation_service import ConversationService
from ..services.identity_service import IdentityService
from ..services.collaboration_service import CollaborationService
from ..services.rating_service import RatingService
from ..services.llm_service import LLMService
from ..services.reputation_service import ReputationService
from ..services.trust_reputation_service import TrustReputationService

logger = logging.getLogger(__name__)


class MessagePayload:
    """
    Structure abstraite pour les messages (sans formatage)
    
    Utilisé pour passer les messages au formatage WhatsApp
    sans dépendance directe sur le format WhatsApp
    """
    
    def __init__(
        self,
        title: Optional[str] = None,
        body: str = "",
        options: Optional[List[Dict[str, str]]] = None,
        footer: Optional[str] = None
    ):
        """
        Args:
            title: Titre optionnel du message
            body: Corps principal du message
            options: Liste d'options menu [{number: "1", text: "Option 1"}, ...]
            footer: Texte optionnel en bas
        """
        self.title = title
        self.body = body
        self.options = options or []
        self.footer = footer
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dict pour sérialisation"""
        return {
            "title": self.title,
            "body": self.body,
            "options": self.options,
            "footer": self.footer
        }


class ConversationHandler:
    """
    Handler principal pour les conversations
    
    Machine à états explicite et déterministe
    """
    
    # Regex pour validation E.164
    E164_REGEX = re.compile(r'^\+[1-9]\d{1,14}$')
    
    # Timeout par défaut
    DEFAULT_TIMEOUT_MINUTES = 30
    
    def __init__(self):
        self.conversation_service = ConversationService()
        self.identity_service = IdentityService()
        self.collaboration_service = CollaborationService()
        self.rating_service = RatingService()
        self.llm_service = LLMService()
        self.reputation_service = ReputationService()
        self.trust_reputation_service = TrustReputationService()

        # New trust-focused flow states (owner-only, permit-first)
        self.TRUST_FLOW_STATES = {
            "INITIAL",
            "VERIFY_ENTER_PERMIT",
            "VERIFY_RESULT",
            "VERIFY_DETAILS",
            "REPORT_CONFIRM_OWNER",
            "REPORT_ENTER_PERMIT",
            "REPORT_ENTER_PHONE",
            "REPORT_CONFIRM_IDENTITY",
            "REPORT_DURATION",
            "REPORT_MAIN_PROBLEM",
            "REPORT_OTHER_TEXT",
            "REPORT_SEVERITY",
            "REPORT_RECOMMEND",
            "REPORT_CONFIRM_LEGAL",
            "INFO_SYSTEM",
            "DEACTIVATED",
        }
    
    def _log_transition(
        self,
        user: User,
        previous_state: str,
        input_text: str,
        next_state: str
    ) -> None:
        """
        Log centralisé pour toutes les transitions d'état
        
        Format structuré pour debugging, abuse detection, analytics
        
        Args:
            user: Utilisateur
            previous_state: État précédent
            input_text: Input utilisateur (tronqué à 50 chars)
            next_state: Nouvel état
        """
        from django.utils import timezone
        
        timestamp = timezone.now().isoformat()
        input_truncated = input_text[:50] if input_text else ""
        
        logger.info(
            f"STATE_TRANSITION | timestamp={timestamp} | "
            f"user={user.phone_number} | "
            f"previous_state={previous_state} | "
            f"input={input_truncated} | "
            f"next_state={next_state}"
        )
    
    def _execute_transition(
        self,
        user: User,
        current_state: ConversationState,
        next_state_name: str,
        expected_input_type: str,
        allowed_values: Optional[list] = None,
        behavior_on_invalid_input: str = "RETRY_SAME_STATE",
        temp_data: Optional[Dict] = None
    ) -> ConversationState:
        """
        Exécute une transition d'état de manière atomique
        
        Une seule transition par appel - garantit l'atomicité
        
        Args:
            user: Utilisateur
            current_state: État actuel
            next_state_name: Nom du nouvel état
            expected_input_type: Type d'input attendu
            allowed_values: Valeurs autorisées
            behavior_on_invalid_input: Comportement sur erreur
            temp_data: Données temporaires
        
        Returns:
            Nouvel état créé
        """
        previous_state_name = current_state.current_state if current_state else "NONE"
        
        # Log de la transition
        self._log_transition(
            user,
            previous_state_name,
            "",  # Input sera loggé par l'appelant
            next_state_name
        )
        
        # Transition atomique
        new_state = self.conversation_service.transition_to_state(
            user=user,
            new_state=next_state_name,
            expected_input_type=expected_input_type,
            allowed_values=allowed_values,
            behavior_on_invalid_input=behavior_on_invalid_input,
            temp_data=temp_data
        )
        
        return new_state
    
    def process_message(
        self,
        phone_number: str,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """
        Point d'entrée principal pour traiter un message
        
        Args:
            phone_number: Numéro de téléphone (E.164)
            message_text: Texte du message
        
        Returns:
            Tuple (next_state, message_payload)
        """
        try:
            # Récupérer ou créer l'utilisateur
            user = self._get_or_create_user(phone_number)

            # Telegram (and some clients) send "/start" as the first interaction button.
            # Treat it as a hard reset to the welcome screen, even if the user already has a state.
            normalized = (message_text or "").strip()
            if normalized.startswith("/start"):
                # Allow reactivation for deactivated accounts
                if not user.is_active:
                    user.is_active = True
                    user.save(update_fields=["is_active"])
                existing_state = self.conversation_service.get_current_state(user)
                if existing_state:
                    self.conversation_service.delete_state(existing_state)
                state = self.conversation_service.get_or_create_state(
                    user=user,
                    initial_state="INITIAL",
                    expected_input_type=ConversationState.InputType.MENU_CHOICE,
                    allowed_values=["1", "2", "3"],
                    behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE
                )
                return self._handle_initial(user, state, "")

            # If account is deactivated, show a simple reactivation screen
            if not user.is_active:
                state = self.conversation_service.get_or_create_state(
                    user=user,
                    initial_state="DEACTIVATED",
                    expected_input_type=ConversationState.InputType.MENU_CHOICE,
                    allowed_values=["1", "2"],
                    behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
                )
                # allow quick reactivate keyword
                if normalized in ["1", "reactiver", "réactiver", "reactivate"]:
                    user.is_active = True
                    user.save(update_fields=["is_active"])
                    self.conversation_service.delete_state(state)
                    return self.process_message(phone_number, "/start")
                return self._handle_deactivated(user, state, normalized)
            
            # Récupérer l'état actuel
            state = self.conversation_service.get_current_state(user)
            
            # Vérifier expiration
            if state and self.conversation_service.is_expired(state):
                # Retour au menu principal selon le rôle
                return self._handle_expired_session(user)

            # Replace legacy role-based flow: if user still has an old OWNER_* / DRIVER_* state, reset to new menu.
            if state and state.current_state and state.current_state.startswith(("OWNER_", "DRIVER_")):
                self.conversation_service.delete_state(state)
                state = None
            
            # Si pas d'état, créer état INITIAL
            if not state:
                state = self.conversation_service.get_or_create_state(
                    user=user,
                    initial_state="INITIAL",
                    expected_input_type=ConversationState.InputType.MENU_CHOICE,
                    allowed_values=["1", "2", "3"],
                    behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE
                )
                # First contact: always show the welcome prompt.
                # Users usually start with "/start", "hello", etc. — don't treat that as invalid input.
                return self._handle_initial(user, state, "")
            
            # Router vers le handler d'état approprié
            previous_state = state.current_state if state else "NONE"
            next_state, message_payload = self._route_to_state_handler(
                user, state, message_text
            )
            
            # Log de la transition (fait dans _execute_transition si transition)
            # On log aussi ici pour les cas sans transition explicite
            if next_state != previous_state:
                self._log_transition(user, previous_state, message_text, next_state)
            
            return next_state, message_payload
            
        except Exception as e:
            logger.error(f"Erreur dans process_message: {str(e)}", exc_info=True)
            # En cas d'erreur, retourner au menu principal selon le rôle
            try:
                # Get or create state for error recovery
                state = self.conversation_service.get_or_create_state(
                    user, "INITIAL", "MENU_CHOICE", [1, 2, 3], "RETRY_SAME_STATE"
                )
                if user.role == User.Role.OWNER:
                    return self._handle_owner_main_menu(user, state, "")
                elif user.role == User.Role.DRIVER:
                    return self._handle_driver_main_menu(user, state, "")
                else:
                    return self._handle_initial(user, state, "")
            except Exception as e2:
                # Si même le menu principal échoue, créer un état minimal et retourner INITIAL
                logger.error(f"Erreur lors du retour au menu: {str(e2)}", exc_info=True)
                state = self.conversation_service.get_or_create_state(
                    user, "INITIAL", "MENU_CHOICE", [1, 2, 3], "RETRY_SAME_STATE"
                )
                return self._handle_initial(user, state, "")
    
    def _get_or_create_user(self, phone_number: str) -> User:
        """Récupère ou crée un utilisateur"""
        user, created = User.objects.get_or_create(
            phone_number=phone_number,
            defaults={"role": User.Role.OWNER}  # Par défaut OWNER, sera changé si nécessaire
        )
        # If this is a WhatsApp identifier, also store it as contact_phone_number for searching.
        if user.phone_number.startswith("+") and not user.contact_phone_number:
            user.contact_phone_number = user.phone_number
            user.save(update_fields=["contact_phone_number"])
        return user
    
    def _handle_expired_session(self, user: User) -> Tuple[str, MessagePayload]:
        """Gère une session expirée - retourne au menu principal (nouveau flow trust)"""
        # Supprimer l'ancien état
        old_state = self.conversation_service.get_current_state(user)
        if old_state:
            self.conversation_service.delete_state(old_state)

        new_state = self.conversation_service.get_or_create_state(
            user=user,
            initial_state="INITIAL",
            expected_input_type=ConversationState.InputType.MENU_CHOICE,
            allowed_values=["1", "2", "3"],
            behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
        )
        return self._handle_initial(user, new_state, "")
    
    def _get_state_handler(self, state_name: str):
        """Get handler for a state name"""
        # Mapper chaque état vers son handler
        state_handlers = {
            "INITIAL": self._handle_initial,
            "DEACTIVATED": self._handle_deactivated,
            # Trust-focused flow
            "VERIFY_ENTER_PERMIT": self._handle_verify_enter_permit,
            "VERIFY_RESULT": self._handle_verify_result,
            "VERIFY_DETAILS": self._handle_verify_details,
            "REPORT_CONFIRM_OWNER": self._handle_report_confirm_owner,
            "REPORT_ENTER_PERMIT": self._handle_report_enter_permit,
            "REPORT_ENTER_PHONE": self._handle_report_enter_phone,
            "REPORT_CONFIRM_IDENTITY": self._handle_report_confirm_identity,
            "REPORT_DURATION": self._handle_report_duration,
            "REPORT_MAIN_PROBLEM": self._handle_report_main_problem,
            "REPORT_OTHER_TEXT": self._handle_report_other_text,
            "REPORT_SEVERITY": self._handle_report_severity,
            "REPORT_RECOMMEND": self._handle_report_recommend,
            "REPORT_CONFIRM_LEGAL": self._handle_report_confirm_legal,
            "INFO_SYSTEM": self._handle_info_system,
            "OWNER_MAIN_MENU": self._handle_owner_main_menu,
            "OWNER_VERIFY_DRIVER": self._handle_owner_verify_driver,
            "OWNER_SEARCH_BY_PHONE": self._handle_owner_search_by_phone,
            "OWNER_PHONE_NOT_FOUND": self._handle_owner_phone_not_found,
            "OWNER_SEARCH_BY_PERMIT": self._handle_owner_search_by_permit,
            "OWNER_INVITE_DRIVER": self._handle_owner_invite_driver,
            "OWNER_VIEW_PROFILE_NOT_LOCKED": self._handle_owner_view_profile_not_locked,
            "OWNER_VIEW_PROFILE_NEW": self._handle_owner_view_profile_new,
            "OWNER_VIEW_PROFILE_WITH_HISTORY": self._handle_owner_view_profile_with_history,
            "OWNER_VIEW_FULL_PROFILE": self._handle_owner_view_full_profile,
            "OWNER_DECLARE_COLLABORATION": self._handle_owner_declare_collaboration,
            "OWNER_DECLARE_COLLABORATION_DATES": self._handle_owner_declare_collaboration_dates,
            "OWNER_VIEW_COLLABORATIONS": self._handle_owner_view_collaborations,
            "OWNER_RATE_SELECT_COLLABORATION": self._handle_owner_rate_select_collaboration,
            "OWNER_RATE_COLLECT_SCORES": self._handle_owner_rate_collect_scores,
            "OWNER_RATE_COLLECT_COMMENT": self._handle_owner_rate_collect_comment,
            "OWNER_RATE_ENTER_COMMENT": self._handle_owner_rate_enter_comment,
            "OWNER_RATE_REVIEW_SANITIZED": self._handle_owner_rate_review_sanitized,
            "OWNER_RATE_LIMIT_REACHED": self._handle_owner_rate_limit_reached,
            "DRIVER_MAIN_MENU": self._handle_driver_main_menu,
            "DRIVER_CREATE_PROFILE": self._handle_driver_create_profile,
            "DRIVER_ENTER_PHONE": self._handle_driver_enter_phone,
            "DRIVER_ENTER_DISPLAY_NAME": self._handle_driver_enter_display_name,
            "DRIVER_ENTER_PERMIT": self._handle_driver_enter_permit,
            "DRIVER_CONFIRM_PERMIT": self._handle_driver_confirm_permit,
            "DRIVER_LOCK_IDENTITY": self._handle_driver_lock_identity,
            "DRIVER_VIEW_REPUTATION": self._handle_driver_view_reputation,
            "DRIVER_CONFIRM_COLLABORATION": self._handle_driver_confirm_collaboration,
            "DRIVER_CONFIRM_COLLABORATION_DETAIL": self._handle_driver_confirm_collaboration_detail,
            "DRIVER_VIEW_PROFILE": self._handle_driver_view_profile,
            "DRIVER_UPDATE_DISPLAY_NAME": self._handle_driver_update_display_name,
            "DRIVER_DELETE_PROFILE_CONFIRM": self._handle_driver_delete_profile_confirm,
            "DRIVER_RESPOND_TO_REVIEW": self._handle_driver_respond_to_review,
            "DRIVER_ENTER_RESPONSE": self._handle_driver_enter_response,
        }
        return state_handlers.get(state_name)

    def _handle_deactivated(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Simple screen for deactivated users."""
        if not message_text:
            return "DEACTIVATED", MessagePayload(
                title="COMPTE DÉSACTIVÉ",
                body="Votre profil a été désactivé.\n\n1️⃣ Réactiver\n2️⃣ Quitter\n\nVous pouvez aussi taper /start.",
                options=[
                    {"number": "1", "text": "Réactiver"},
                    {"number": "2", "text": "Quitter"},
                ],
            )
        if message_text == "1":
            user.is_active = True
            user.save(update_fields=["is_active"])
            self.conversation_service.delete_state(state)
            return self.process_message(user.phone_number, "/start")
        if message_text == "2":
            if state:
                self.conversation_service.delete_state(state)
            return "END", MessagePayload(body="Au revoir !")
        return self._handle_invalid_input(user, state, "Choix invalide")
    
    def _route_to_state_handler(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """
        Route vers le handler d'état approprié
        
        Machine à états explicite - chaque état a son handler
        """
        current_state = state.current_state
        
        handler = self._get_state_handler(current_state)
        if not handler:
            logger.error(f"Handler non trouvé pour l'état: {current_state}")
            # État inconnu - retourner au menu principal selon le rôle
            # Create a new state for main menu
            if user.role == User.Role.OWNER:
                new_state = self.conversation_service.get_or_create_state(
                    user, "OWNER_MAIN_MENU", "MENU_CHOICE", [1, 2, 3, 4], "RETRY_SAME_STATE"
                )
                return self._handle_owner_main_menu(user, new_state, "")
            elif user.role == User.Role.DRIVER:
                new_state = self.conversation_service.get_or_create_state(
                    user, "DRIVER_MAIN_MENU", "MENU_CHOICE", [1, 2, 3], "RETRY_SAME_STATE"
                )
                return self._handle_driver_main_menu(user, new_state, "")
            else:
                new_state = self.conversation_service.get_or_create_state(
                    user, "INITIAL", "MENU_CHOICE", [1, 2, 3], "RETRY_SAME_STATE"
                )
                return self._handle_initial(user, new_state, "")
        
        # Valider l'input avant de router
        is_valid, error_message = self._validate_input(state, message_text)
        
        if not is_valid:
            return self._handle_invalid_input(user, state, error_message)
        
        # Appeler le handler d'état
        next_state, message_payload = handler(user, state, message_text)
        
        # Les handlers individuels sont responsables de mettre à jour l'état
        # via ConversationService.transition_to_state() ou update_state()
        # On ne fait rien ici pour éviter les doubles mises à jour
        
        return next_state, message_payload
    
    def _validate_input(
        self,
        state: ConversationState,
        message_text: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Valide l'input selon expected_input_type
        
        Retourne (is_valid, error_message)
        """
        expected_type = state.expected_input_type
        allowed_values = state.allowed_values or []
        
        # Normaliser l'input (trim)
        message_text = message_text.strip()
        
        if expected_type == ConversationState.InputType.MENU_CHOICE:
            # Doit être dans allowed_values
            if message_text not in [str(v) for v in allowed_values]:
                return False, f"Choix invalide. Valeurs acceptées: {', '.join(map(str, allowed_values))}"
            return True, None
        
        elif expected_type == ConversationState.InputType.CONFIRMATION:
            # O ou N uniquement
            normalized = message_text.upper()
            if normalized not in ["O", "N", "OUI", "NON", "Y", "YES", "NO"]:
                return False, "Réponse invalide. Répondez O (Oui) ou N (Non)"
            return True, None
        
        elif expected_type == ConversationState.InputType.PHONE_NUMBER:
            # Allow navigation/cancel command for phone entry prompts
            if message_text == "0":
                return True, None
            # Validation E.164
            if not self.E164_REGEX.match(message_text):
                return False, "Format invalide. Format attendu: +221771234567"
            return True, None
        
        elif expected_type == ConversationState.InputType.PERMIT_NUMBER:
            # Allow navigation/cancel command for permit entry prompts
            if message_text == "0":
                return True, None
            # Only some permit-entry flows support "3 = Retour"
            if message_text == "3" and getattr(state, "current_state", "") == "DRIVER_ENTER_PERMIT":
                return True, None
            # Validation permis (normalisation et format)
            is_valid, normalized, error = self.identity_service.normalize_and_validate_permit(message_text)
            if not is_valid:
                return False, error or "Format de permis invalide"
            return True, None
        
        elif expected_type == ConversationState.InputType.SCORE_RATING:
            # Score 1-5 uniquement
            try:
                score = int(message_text)
                if score < 1 or score > 5:
                    return False, "Score invalide. Valeur attendue: 1 à 5"
                return True, None
            except ValueError:
                return False, "Score invalide. Entrez un nombre entre 1 et 5"
        
        elif expected_type == ConversationState.InputType.SANITIZED_TEXT:
            # Texte libre avec limite de caractères
            # Allow navigation/back for free-text prompts
            if message_text == "0":
                return True, None
            if len(message_text) > 500:
                return False, "Texte trop long. Maximum 500 caractères."
            if len(message_text) < 1:
                return False, "Texte vide. Entrez votre commentaire."
            return True, None
        
        # Type inconnu - accepter par défaut
        logger.warning(f"Type d'input inconnu: {expected_type}")
        return True, None
    
    def _handle_invalid_input(
        self,
        user: User,
        state: ConversationState,
        error_message: str
    ) -> Tuple[str, MessagePayload]:
        """
        Gère une entrée invalide selon behavior_on_invalid_input
        """
        # Incrémenter le compteur
        state = self.conversation_service.increment_invalid_input(state)
        
        behavior = state.behavior_on_invalid_input
        invalid_count = state.invalid_input_count
        
        # Si 3 erreurs consécutives, retour au menu principal (nouveau menu trust)
        if invalid_count >= 3:
            new_state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="INITIAL",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_initial(user, new_state, "")
        
        # Selon le comportement
        if behavior == ConversationState.InvalidInputBehavior.RETRY_SAME_STATE:
            # Réafficher le même état avec message d'erreur
            # Store error message in temp_data for handler to display
            state.temp_data["_error_message"] = error_message
            state = self.conversation_service.update_state(state)
            # Call handler directly, bypassing validation
            handler = self._get_state_handler(state.current_state)
            if handler:
                next_state, message_payload = handler(user, state, "")
                # Add error message to payload if handler didn't include it
                if error_message and error_message not in (message_payload.body or ""):
                    message_payload.body = f"❌ {error_message}\n\n{message_payload.body or ''}"
                return next_state, message_payload
            # Fallback to the trust main menu if handler not found
            new_state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="INITIAL",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_initial(user, new_state, "")
        
        elif behavior == ConversationState.InvalidInputBehavior.RETURN_TO_PREVIOUS:
            # TODO: Implémenter retour à l'état précédent
            # Pour l'instant, retour au menu principal trust
            new_state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="INITIAL",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_initial(user, new_state, "")
        
        elif behavior == ConversationState.InvalidInputBehavior.RETURN_TO_MAIN_MENU:
            new_state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="INITIAL",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_initial(user, new_state, "")
        
        # Par défaut, retry
        return self._route_to_state_handler(user, state, "")
    
    # ==================== HANDLERS D'ÉTAT ====================
    
    def _handle_initial(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour l'état INITIAL (nouveau menu trust-focused, owner-only)"""
        if not message_text:
            return self._show_initial_welcome()

        if message_text == "1":
            new_state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="VERIFY_ENTER_PERMIT",
                expected_input_type=ConversationState.InputType.PERMIT_NUMBER,
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_verify_enter_permit(user, new_state, "")
        if message_text == "2":
            new_state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_CONFIRM_OWNER",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_report_confirm_owner(user, new_state, "")
        if message_text == "3":
            new_state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="INFO_SYSTEM",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_info_system(user, new_state, "")

        return self._show_initial_welcome()
    
    def _show_initial_welcome(self) -> Tuple[str, MessagePayload]:
        """Affiche le message de bienvenue (trust-focused)"""
        return "INITIAL", MessagePayload(
            title="Bienvenue.",
            body="Ce service est réservé aux propriétaires de véhicules Yango.\n\nIl permet de :",
            options=[
                {"number": "1", "text": "Vérifier la réputation d’un chauffeur"},
                {"number": "2", "text": "Signaler / évaluer un chauffeur"},
                {"number": "3", "text": "Comprendre le fonctionnement"},
            ]
        )
    
    def _transition_to_owner(
        self,
        user: User,
        state: ConversationState
    ) -> Tuple[str, MessagePayload]:
        """Transition vers owner - une seule transition atomique"""
        from ..models import OwnerProfile
        OwnerProfile.objects.get_or_create(user=user)
        user.role = User.Role.OWNER
        user.save()
        
        new_state = self._execute_transition(
            user, state, "OWNER_MAIN_MENU",
            ConversationState.InputType.MENU_CHOICE,
            allowed_values=["1", "2", "3", "4"]
        )
        
        return self._handle_owner_main_menu(user, new_state, "")
    
    def _transition_to_driver(
        self,
        user: User,
        state: ConversationState
    ) -> Tuple[str, MessagePayload]:
        """Transition vers driver - une seule transition atomique"""
        user.role = User.Role.DRIVER
        user.save()
        
        new_state = self._execute_transition(
            user, state, "DRIVER_MAIN_MENU",
            ConversationState.InputType.MENU_CHOICE,
            allowed_values=["1", "2", "3", "4"]
        )
        
        return self._handle_driver_main_menu(user, new_state, "")
    
    def _show_help(self, state: ConversationState) -> Tuple[str, MessagePayload]:
        """Affiche l'aide - pas de transition"""
        self.conversation_service.update_state(
            state,
            expected_input_type=ConversationState.InputType.MENU_CHOICE,
            allowed_values=["1"],
            reset_invalid_count=True
        )
        
        return "INITIAL", MessagePayload(
            title="AIDE",
            body="Pour les propriétaires :\n• Vérifiez un chauffeur avant de l'embaucher\n• Déclarez vos collaborations\n• Évaluez vos chauffeurs (après 7 jours minimum)\n\nPour les chauffeurs :\n• Créez votre profil professionnel\n• Verrouillez votre identité avec votre permis\n• Consultez votre réputation\n• Répondez aux évaluations\n\nSécurité :\n• Votre numéro de permis n'est jamais affiché\n• Seules les collaborations confirmées comptent\n• Les commentaires sont vérifiés avant publication",
            options=[{"number": "1", "text": "Retour"}]
        )

    # ==================== NOUVEAU FLOW TRUST (Yango Owners) ====================

    def _handle_verify_enter_permit(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """FLOW 1 - Step 1: Strong identification via permit."""
        if not message_text:
            return "VERIFY_ENTER_PERMIT", MessagePayload(
                body="Entrez le numéro de permis de conduire du chauffeur.\n\n0️⃣ Retour au menu",
                options=[{"number": "0", "text": "Retour au menu"}],
            )

        if message_text == "0":
            state2 = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="INITIAL",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_initial(user, state2, "")

        # Validate permit and compute summary
        ok, summary, error = self.trust_reputation_service.summarize(message_text)
        if not ok:
            return self._handle_invalid_input(user, state, error or "Format invalide.")

        # Persist what we need for next steps (details / report shortcut)
        state = self.conversation_service.set_temp_data(state, "permit_input", message_text)
        state = self.conversation_service.set_temp_data(state, "permit_masked", summary.masked_permit)

        new_state = self._execute_transition(
            user=user,
            current_state=state,
            next_state_name="VERIFY_RESULT",
            expected_input_type=ConversationState.InputType.MENU_CHOICE,
            allowed_values=["0", "1", "2", "3"],
            temp_data={
                "permit_input": message_text,
                "permit_masked": summary.masked_permit,
            },
        )
        return self._handle_verify_result(user, new_state, "")

    def _handle_verify_result(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """FLOW 1 - Step 2: Synthesized reputation result."""
        permit_input = self.conversation_service.get_temp_data(state, "permit_input")
        ok, summary, error = self.trust_reputation_service.summarize(permit_input or "")
        if not ok or not summary:
            # If temp data missing, restart verify flow
            back = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="VERIFY_ENTER_PERMIT",
                expected_input_type=ConversationState.InputType.PERMIT_NUMBER,
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_verify_enter_permit(user, back, "")

        if not message_text:
            body = (
                "Chauffeur identifié\n"
                f"Nom : {summary.driver_name}\n"
                f"Permis : **{summary.masked_permit}**\n\n"
                f"Statut actuel : {summary.status_emoji} {summary.status_label}\n"
                f"Basé sur {summary.distinct_owners} propriétaire(s) distinct(s)\n\n"
                "Motifs les plus signalés :\n"
                f"– {summary.top_reasons[0]}\n"
                f"– {summary.top_reasons[1]}\n\n"
                f"Dernier signalement : {summary.last_signal_relative}\n"
            )
            return "VERIFY_RESULT", MessagePayload(
                body=body,
                options=[
                    {"number": "0", "text": "Changer le permis"},
                    {"number": "1", "text": "Voir plus de détails"},
                    {"number": "2", "text": "Signaler ce chauffeur"},
                    {"number": "3", "text": "Revenir au menu"},
                ],
            )

        if message_text == "0":
            back = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="VERIFY_ENTER_PERMIT",
                expected_input_type=ConversationState.InputType.PERMIT_NUMBER,
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_verify_enter_permit(user, back, "")

        if message_text == "1":
            new_state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="VERIFY_DETAILS",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["0", "1", "2", "3"],
                temp_data={"permit_input": permit_input},
            )
            return self._handle_verify_details(user, new_state, "")
        if message_text == "2":
            # Jump into report flow with permit prefilled
            new_state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_CONFIRM_OWNER",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"],
                temp_data={"prefill_permit": permit_input},
            )
            return self._handle_report_confirm_owner(user, new_state, "")
        if message_text == "3":
            return self._handle_initial(user, self.conversation_service.get_or_create_state(
                user=user,
                initial_state="INITIAL",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            ), "")

        return self._handle_invalid_input(user, state, "Choix invalide")

    def _handle_verify_details(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """FLOW 1 - Step 3: Optional details (dissuasion-oriented)."""
        permit_input = self.conversation_service.get_temp_data(state, "permit_input")
        ok, summary, _ = self.trust_reputation_service.summarize(permit_input or "")
        if not ok or not summary:
            # If temp is inconsistent, go back to the trust main menu
            state2 = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="INITIAL",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_initial(user, state2, "")

        if not message_text:
            grave, serious, positive = summary.details
            body = (
                "Historique résumé :\n"
                f"🔴 Incidents graves : {grave}\n"
                f"🟠 Alertes sérieuses : {serious}\n"
                f"🟢 Collaborations satisfaisantes : {positive}\n\n"
                "Le statut est maintenu jusqu’à réévaluation."
            )
            return "VERIFY_DETAILS", MessagePayload(
                body=body,
                options=[
                    {"number": "0", "text": "Changer le permis"},
                    {"number": "1", "text": "Voir plus de détails"},
                    {"number": "2", "text": "Signaler ce chauffeur"},
                    {"number": "3", "text": "Revenir au menu"},
                ],
            )

        if message_text == "0":
            back = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="VERIFY_ENTER_PERMIT",
                expected_input_type=ConversationState.InputType.PERMIT_NUMBER,
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_verify_enter_permit(user, back, "")

        # Same options as result for consistency
        if message_text == "1":
            # Re-show (no deeper page yet)
            return self._handle_verify_details(user, state, "")
        if message_text == "2":
            new_state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_CONFIRM_OWNER",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"],
                temp_data={"prefill_permit": permit_input},
            )
            return self._handle_report_confirm_owner(user, new_state, "")
        if message_text == "3":
            return self._handle_initial(user, self.conversation_service.get_or_create_state(
                user=user,
                initial_state="INITIAL",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            ), "")
        return self._handle_invalid_input(user, state, "Choix invalide")

    def _handle_report_confirm_owner(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """FLOW 2 - Step 1: Owner confirmation."""
        if not message_text:
            return "REPORT_CONFIRM_OWNER", MessagePayload(
                body="Confirmez-vous être propriétaire d’un véhicule confié à ce chauffeur ?",
                options=[
                    {"number": "1", "text": "Oui"},
                    {"number": "2", "text": "Non"},
                ],
            )

        if message_text == "2":
            # Clean exit to main menu
            return self._handle_initial(user, self.conversation_service.get_or_create_state(
                user=user,
                initial_state="INITIAL",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            ), "")

        if message_text == "1":
            prefill = self.conversation_service.get_temp_data(state, "prefill_permit")
            new_state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_ENTER_PERMIT",
                expected_input_type=ConversationState.InputType.PERMIT_NUMBER,
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
                temp_data={"prefill_permit": prefill} if prefill else {},
            )
            # If we have a prefill, we can reuse it as the message_text by calling handler with it.
            return self._handle_report_enter_permit(user, new_state, prefill or "")

        return self._handle_invalid_input(user, state, "Choix invalide")

    def _handle_report_enter_permit(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """FLOW 2 - Step 2: Driver permit."""
        if not message_text:
            return "REPORT_ENTER_PERMIT", MessagePayload(
                body="Entrez le numéro de permis du chauffeur concerné.\n\n0️⃣ Retour",
                options=[{"number": "0", "text": "Retour"}],
            )

        if message_text == "0":
            back = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_CONFIRM_OWNER",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_report_confirm_owner(user, back, "")
        ok, summary, error = self.trust_reputation_service.summarize(message_text)
        if not ok:
            return self._handle_invalid_input(user, state, error or "Format invalide.")

        state = self.conversation_service.set_temp_data(state, "report_permit_input", message_text)
        state = self.conversation_service.set_temp_data(state, "report_permit_masked", summary.masked_permit)

        new_state = self._execute_transition(
            user=user,
            current_state=state,
            next_state_name="REPORT_ENTER_PHONE",
            expected_input_type=ConversationState.InputType.PHONE_NUMBER,
            behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            temp_data={
                "report_permit_input": message_text,
                "report_permit_masked": summary.masked_permit,
            },
        )
        return self._handle_report_enter_phone(user, new_state, "")

    def _handle_report_enter_phone(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """FLOW 2 - Step 3: Phone enrichment."""
        if not message_text:
            return "REPORT_ENTER_PHONE", MessagePayload(
                body="Entrez un numéro de téléphone utilisé par ce chauffeur (format +225…).\n\n0️⃣ Retour",
                options=[{"number": "0", "text": "Retour"}],
            )

        if message_text == "0":
            back = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_ENTER_PERMIT",
                expected_input_type=ConversationState.InputType.PERMIT_NUMBER,
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_report_enter_permit(user, back, "")
        # Validate phone (E.164)
        if not self.E164_REGEX.match((message_text or "").strip()):
            return self._handle_invalid_input(user, state, "Format invalide. Exemple : +22501020304")

        state = self.conversation_service.set_temp_data(state, "report_phone_e164", message_text.strip())
        new_state = self._execute_transition(
            user=user,
            current_state=state,
            next_state_name="REPORT_CONFIRM_IDENTITY",
            expected_input_type=ConversationState.InputType.MENU_CHOICE,
            allowed_values=["0", "1", "2"],
            behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            temp_data={
                "report_permit_input": self.conversation_service.get_temp_data(state, "report_permit_input"),
                "report_phone_e164": message_text.strip(),
            },
        )
        return self._handle_report_confirm_identity(user, new_state, "")

    def _handle_report_confirm_identity(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """FLOW 2 - Step 4: Identity confirmation."""
        permit_input = self.conversation_service.get_temp_data(state, "report_permit_input")
        phone = self.conversation_service.get_temp_data(state, "report_phone_e164")
        ok, summary, _ = self.trust_reputation_service.summarize(permit_input or "")
        if not ok or not summary:
            back = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_ENTER_PERMIT",
                expected_input_type=ConversationState.InputType.PERMIT_NUMBER,
            )
            return self._handle_report_enter_permit(user, back, "")

        if not message_text:
            body = (
                "Chauffeur identifié :\n"
                f"Nom : {summary.driver_name}\n"
                f"Permis : **{summary.masked_permit}**\n"
                f"Téléphone déclaré : {self.trust_reputation_service.mask_phone_e164(phone)}\n\n"
                "Confirmez-vous ?"
            )
            return "REPORT_CONFIRM_IDENTITY", MessagePayload(
                body=body,
                options=[
                    {"number": "0", "text": "Retour"},
                    {"number": "1", "text": "Oui"},
                    {"number": "2", "text": "Non"},
                ],
            )

        if message_text == "0":
            back = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_ENTER_PHONE",
                expected_input_type=ConversationState.InputType.PHONE_NUMBER,
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
                temp_data={
                    "report_permit_input": permit_input,
                },
            )
            return self._handle_report_enter_phone(user, back, "")

        if message_text == "2":
            # Restart flow safely (back to permit step)
            back = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_ENTER_PERMIT",
                expected_input_type=ConversationState.InputType.PERMIT_NUMBER,
            )
            return self._handle_report_enter_permit(user, back, "")

        if message_text == "1":
            new_state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_DURATION",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["0", "1", "2", "3"],
                temp_data={
                    "report_permit_input": permit_input,
                    "report_phone_e164": phone,
                },
            )
            return self._handle_report_duration(user, new_state, "")

        return self._handle_invalid_input(user, state, "Choix invalide")

    def _handle_report_duration(self, user: User, state: ConversationState, message_text: str) -> Tuple[str, MessagePayload]:
        """FLOW 2 - Step 5: Collaboration duration."""
        if not message_text:
            return "REPORT_DURATION", MessagePayload(
                body="Combien de temps ce chauffeur a-t-il travaillé avec vous ?",
                options=[
                    {"number": "0", "text": "Retour"},
                    {"number": "1", "text": "Moins d’1 mois"},
                    {"number": "2", "text": "1 à 3 mois"},
                    {"number": "3", "text": "Plus de 3 mois"},
                ],
            )
        if message_text == "0":
            back = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_CONFIRM_IDENTITY",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["0", "1", "2"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_report_confirm_identity(user, back, "")
        if message_text not in ["1", "2", "3"]:
            return self._handle_invalid_input(user, state, "Choix invalide")
        state = self.conversation_service.set_temp_data(state, "duration_choice", int(message_text))
        new_state = self._execute_transition(
            user=user,
            current_state=state,
            next_state_name="REPORT_MAIN_PROBLEM",
            expected_input_type=ConversationState.InputType.MENU_CHOICE,
            allowed_values=["0"] + [str(i) for i in range(1, 8)],
        )
        return self._handle_report_main_problem(user, new_state, "")

    def _handle_report_main_problem(self, user: User, state: ConversationState, message_text: str) -> Tuple[str, MessagePayload]:
        """FLOW 2 - Step 6: Main problem."""
        if not message_text:
            return "REPORT_MAIN_PROBLEM", MessagePayload(
                body="Quel problème principal avez-vous rencontré ?",
                options=[
                    {"number": "0", "text": "Retour"},
                    {"number": "1", "text": "Versements journaliers irréguliers"},
                    {"number": "2", "text": "Mensonges / manque de transparence"},
                    {"number": "3", "text": "Mauvaise gestion du véhicule"},
                    {"number": "4", "text": "Refus de consignes"},
                    {"number": "5", "text": "Problèmes avec la police"},
                    {"number": "6", "text": "Abandon / injoignable"},
                    {"number": "7", "text": "Autre"},
                ],
            )
        if message_text == "0":
            back = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_DURATION",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["0", "1", "2", "3"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_report_duration(user, back, "")
        if message_text not in [str(i) for i in range(1, 8)]:
            return self._handle_invalid_input(user, state, "Choix invalide")
        code = int(message_text)
        state = self.conversation_service.set_temp_data(state, "main_problem_code", code)
        if code == 7:
            new_state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_OTHER_TEXT",
                expected_input_type=ConversationState.InputType.SANITIZED_TEXT,
            )
            return self._handle_report_other_text(user, new_state, "")
        new_state = self._execute_transition(
            user=user,
            current_state=state,
            next_state_name="REPORT_SEVERITY",
            expected_input_type=ConversationState.InputType.MENU_CHOICE,
            allowed_values=["1", "2", "3"],
        )
        return self._handle_report_severity(user, new_state, "")

    def _handle_report_other_text(self, user: User, state: ConversationState, message_text: str) -> Tuple[str, MessagePayload]:
        """FLOW 2 - Step 6b: Other (one short sentence)."""
        if not message_text:
            return "REPORT_OTHER_TEXT", MessagePayload(
                body="Décrivez en une seule phrase (courte) :\n\n0️⃣ Retour",
                options=[{"number": "0", "text": "Retour"}],
            )
        if message_text == "0":
            back = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_MAIN_PROBLEM",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["0"] + [str(i) for i in range(1, 8)],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_report_main_problem(user, back, "")
        text = (message_text or "").strip()
        if len(text) > 140:
            return self._handle_invalid_input(user, state, "Trop long. Une phrase courte (max 140 caractères).")
        # rudimentary "one sentence" check: avoid newlines
        if "\n" in text:
            return self._handle_invalid_input(user, state, "Une seule phrase (sans retour à la ligne).")
        state = self.conversation_service.set_temp_data(state, "main_problem_other_text", text)
        new_state = self._execute_transition(
            user=user,
            current_state=state,
            next_state_name="REPORT_SEVERITY",
            expected_input_type=ConversationState.InputType.MENU_CHOICE,
            allowed_values=["0", "1", "2", "3"],
        )
        return self._handle_report_severity(user, new_state, "")

    def _handle_report_severity(self, user: User, state: ConversationState, message_text: str) -> Tuple[str, MessagePayload]:
        """FLOW 2 - Step 7: Severity."""
        if not message_text:
            return "REPORT_SEVERITY", MessagePayload(
                body="Ce problème était :",
                options=[
                    {"number": "0", "text": "Retour"},
                    {"number": "1", "text": "Mineur"},
                    {"number": "2", "text": "Sérieux"},
                    {"number": "3", "text": "Grave"},
                ],
            )
        if message_text == "0":
            back = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_MAIN_PROBLEM",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["0"] + [str(i) for i in range(1, 8)],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_report_main_problem(user, back, "")
        if message_text not in ["1", "2", "3"]:
            return self._handle_invalid_input(user, state, "Choix invalide")
        state = self.conversation_service.set_temp_data(state, "severity", int(message_text))
        new_state = self._execute_transition(
            user=user,
            current_state=state,
            next_state_name="REPORT_RECOMMEND",
            expected_input_type=ConversationState.InputType.MENU_CHOICE,
            allowed_values=["0", "1", "2"],
        )
        return self._handle_report_recommend(user, new_state, "")

    def _handle_report_recommend(self, user: User, state: ConversationState, message_text: str) -> Tuple[str, MessagePayload]:
        """FLOW 2 - Step 8: Final recommendation (high weight)."""
        if not message_text:
            return "REPORT_RECOMMEND", MessagePayload(
                body="Recommanderiez-vous ce chauffeur à un autre propriétaire ?\n\n⚠️ Un “Non” a un poids négatif important dans le calcul.",
                options=[
                    {"number": "0", "text": "Retour"},
                    {"number": "1", "text": "Oui"},
                    {"number": "2", "text": "Non"},
                ],
            )
        if message_text == "0":
            back = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_SEVERITY",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["0", "1", "2", "3"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_report_severity(user, back, "")
        if message_text not in ["1", "2"]:
            return self._handle_invalid_input(user, state, "Choix invalide")
        state = self.conversation_service.set_temp_data(state, "recommend", True if message_text == "1" else False)
        new_state = self._execute_transition(
            user=user,
            current_state=state,
            next_state_name="REPORT_CONFIRM_LEGAL",
            expected_input_type=ConversationState.InputType.MENU_CHOICE,
            allowed_values=["0", "1", "2"],
        )
        return self._handle_report_confirm_legal(user, new_state, "")

    def _handle_report_confirm_legal(self, user: User, state: ConversationState, message_text: str) -> Tuple[str, MessagePayload]:
        """FLOW 2 - Step 9/10: Legal confirmation + persist + closure."""
        if not message_text:
            return "REPORT_CONFIRM_LEGAL", MessagePayload(
                body="Confirmez-vous que ces informations sont exactes ?",
                options=[
                    {"number": "0", "text": "Retour"},
                    {"number": "1", "text": "Oui, je confirme"},
                    {"number": "2", "text": "Annuler"},
                ],
            )
        if message_text == "0":
            back = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_RECOMMEND",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["0", "1", "2"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_report_recommend(user, back, "")
        if message_text == "2":
            # clean exit
            return self._handle_initial(user, self.conversation_service.get_or_create_state(
                user=user,
                initial_state="INITIAL",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            ), "")
        if message_text != "1":
            return self._handle_invalid_input(user, state, "Choix invalide")

        # Persist the report as DriverSignal
        from ..models import DriverSignal
        permit_input = self.conversation_service.get_temp_data(state, "report_permit_input")
        ok, summary, error = self.trust_reputation_service.summarize(permit_input or "")
        if not ok or not summary:
            return self._handle_invalid_input(user, state, "Permis invalide.")

        # Try link to DriverProfile if exists
        driver_profile = self.identity_service.search_by_permit(permit_input or "")

        fp = self.trust_reputation_service.fingerprint_permit(
            self.identity_service.normalize_and_validate_permit(permit_input or "")[1]
        )

        main_problem_code = self.conversation_service.get_temp_data(state, "main_problem_code")
        other_text = self.conversation_service.get_temp_data(state, "main_problem_other_text")

        DriverSignal.objects.create(
            owner=user,
            permit_fingerprint=fp,
            driver_profile=driver_profile,
            reported_phone_e164=self.conversation_service.get_temp_data(state, "report_phone_e164"),
            duration_choice=int(self.conversation_service.get_temp_data(state, "duration_choice") or 1),
            main_problem_code=int(main_problem_code or 1),
            main_problem_other_text=other_text if int(main_problem_code or 1) == 7 else None,
            severity=int(self.conversation_service.get_temp_data(state, "severity") or 1),
            recommend=bool(self.conversation_service.get_temp_data(state, "recommend")),
            legal_confirmed=True,
        )

        # Clear state and return to main menu
        self.conversation_service.delete_state(state)
        state2 = self.conversation_service.get_or_create_state(
            user=user,
            initial_state="INITIAL",
            expected_input_type=ConversationState.InputType.MENU_CHOICE,
            allowed_values=["1", "2", "3"],
            behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
        )
        _, payload = self._handle_initial(user, state2, "")
        payload.body = (
            "Merci.\n"
            "Votre signalement a été enregistré.\n"
            "Il contribue à protéger les propriétaires et à responsabiliser les chauffeurs.\n\n"
            + (payload.body or "")
        ).strip()
        return "INITIAL", payload

    def _handle_info_system(self, user: User, state: ConversationState, message_text: str) -> Tuple[str, MessagePayload]:
        """FLOW 3 - Controlled transparency."""
        if not message_text:
            body = (
                "Fonctionnement du système :\n"
                "• Le permis de conduire est l’identifiant principal\n"
                "• Les numéros de téléphone peuvent changer\n"
                "• Seuls les propriétaires peuvent signaler\n"
                "• Les signalements graves pèsent plus que les positifs\n"
                "• Un chauffeur ne peut pas effacer son historique\n"
                "• Une réévaluation est possible après une période sans incident\n\n"
                "Statuts affichés :\n"
                "🟢 FIABLE — Aucun incident grave récent, collaborations stables\n"
                "🟠 À SURVEILLER — Alertes répétées ou comportement instable\n"
                "🔴 DÉCONSEILLÉ — Problèmes graves signalés par plusieurs propriétaires"
            )
            return "INFO_SYSTEM", MessagePayload(
                body=body,
                options=[
                    {"number": "0", "text": "Retour au menu"},
                    {"number": "1", "text": "Vérifier un chauffeur"},
                    {"number": "2", "text": "Signaler un chauffeur"},
                    {"number": "3", "text": "Quitter"},
                ],
            )
        if message_text == "0":
            state2 = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="INITIAL",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )
            return self._handle_initial(user, state2, "")
        if message_text == "1":
            new_state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="VERIFY_ENTER_PERMIT",
                expected_input_type=ConversationState.InputType.PERMIT_NUMBER,
            )
            return self._handle_verify_enter_permit(user, new_state, "")
        if message_text == "2":
            new_state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="REPORT_CONFIRM_OWNER",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"],
            )
            return self._handle_report_confirm_owner(user, new_state, "")
        if message_text == "3":
            self.conversation_service.delete_state(state)
            return "END", MessagePayload(body="Au revoir.")
        return self._handle_invalid_input(user, state, "Choix invalide")
    
    def _handle_owner_main_menu(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour le menu principal owner"""
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="OWNER_MAIN_MENU",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3", "4"]
            )
        
        if not message_text:
            # Afficher le menu
            payload = MessagePayload(
                title="MENU PRINCIPAL - PROPRIÉTAIRE",
                body="Que souhaitez-vous faire ?",
                options=[
                    {"number": "1", "text": "Vérifier un chauffeur"},
                    {"number": "2", "text": "Déclarer une collaboration"},
                    {"number": "3", "text": "Voir mes collaborations"},
                    {"number": "4", "text": "Quitter"}
                ]
            )
            return "OWNER_MAIN_MENU", payload
        
        # Traiter le choix
        if message_text == "1":
            return self._handle_owner_verify_driver(user, None, "")
        elif message_text == "2":
            return self._handle_owner_declare_collaboration(user, None, "")
        elif message_text == "3":
            return self._handle_owner_view_collaborations(user, None, "")
        elif message_text == "4":
            payload = MessagePayload(
                body="Au revoir ! À bientôt."
            )
            # Supprimer l'état
            if state:
                self.conversation_service.delete_state(state)
            return "END", payload
        
        return self._handle_invalid_input(user, state, "Choix invalide")

    def _owner_main_menu_notice(self, user: User, notice: str) -> Tuple[str, MessagePayload]:
        """Return owner main menu with a notice prepended (avoids 'dead-end' messages without navigation)."""
        state = self.conversation_service.get_or_create_state(
            user=user,
            initial_state="OWNER_MAIN_MENU",
            expected_input_type=ConversationState.InputType.MENU_CHOICE,
            allowed_values=["1", "2", "3", "4"],
        )
        _, payload = self._handle_owner_main_menu(user, state, "")
        payload.body = f"{notice}\n\n{payload.body or ''}".strip()
        return "OWNER_MAIN_MENU", payload
    
    def _handle_owner_verify_driver(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour vérification chauffeur"""
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="OWNER_VERIFY_DRIVER",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"]
            )
        
        if not message_text:
            payload = MessagePayload(
                title="VÉRIFICATION DE CHAUFFEUR",
                body="Comment souhaitez-vous rechercher ?",
                options=[
                    {"number": "1", "text": "Par numéro de téléphone"},
                    {"number": "2", "text": "Par numéro de permis"},
                    {"number": "3", "text": "Retour au menu"}
                ]
            )
            return "OWNER_VERIFY_DRIVER", payload
        
        if message_text == "1":
            return self._handle_owner_search_by_phone(user, None, "")
        elif message_text == "2":
            return self._handle_owner_search_by_permit(user, None, "")
        elif message_text == "3":
            return self._handle_owner_main_menu(user, None, "")
        
        return self._handle_invalid_input(user, state, "Choix invalide")
    
    def _handle_owner_search_by_phone(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour recherche par téléphone - Handler court"""
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="OWNER_SEARCH_BY_PHONE",
                expected_input_type=ConversationState.InputType.PHONE_NUMBER,
                allowed_values=None,
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE
            )
        
        if not message_text:
            return self._show_search_by_phone_prompt()
        
        if message_text == "0":
            return self._transition_back_to_verify_driver(user, state)
        
        return self._process_phone_search(user, state, message_text)
    
    def _show_search_by_phone_prompt(self) -> Tuple[str, MessagePayload]:
        """Affiche le prompt de recherche par téléphone"""
        return "OWNER_SEARCH_BY_PHONE", MessagePayload(
            title="RECHERCHE PAR TÉLÉPHONE",
            body="Entrez le numéro de téléphone du chauffeur.\n\nFormat : +221771234567\n(Code pays + numéro, sans espaces)\n\nOu tapez 0 pour annuler."
        )
    
    def _transition_back_to_verify_driver(
        self,
        user: User,
        state: ConversationState
    ) -> Tuple[str, MessagePayload]:
        """Transition retour vers verify driver - une seule transition"""
        new_state = self._execute_transition(
            user, state, "OWNER_VERIFY_DRIVER",
            ConversationState.InputType.MENU_CHOICE,
            allowed_values=["1", "2", "3"]
        )
        return self._handle_owner_verify_driver(user, new_state, "")
    
    def _process_phone_search(
        self,
        user: User,
        state: ConversationState,
        phone_number: str
    ) -> Tuple[str, MessagePayload]:
        """Traite la recherche par téléphone - logique extraite"""
        try:
            # Support both WhatsApp users (identifier is E.164) and Telegram users (identifier is tg:<id>)
            # by searching on contact_phone_number too.
            driver_user = User.objects.get(
                Q(phone_number=phone_number) | Q(contact_phone_number=phone_number),
                role=User.Role.DRIVER,
                is_active=True,
            )
            
            if hasattr(driver_user, 'driver_profile'):
                driver_profile = driver_user.driver_profile
                if driver_profile.identity_locked:
                    return self._handle_owner_view_profile_result(user, driver_profile)
                else:
                    return self._handle_owner_view_profile_not_locked(user, driver_profile)
            else:
                return self._show_profile_not_found_phone(user, state, phone_number)
                
        except User.DoesNotExist:
            return self._show_profile_not_found_phone(user, state, phone_number)
    
    def _show_profile_not_found_phone(
        self,
        user: User,
        state: ConversationState,
        searched_phone: str
    ) -> Tuple[str, MessagePayload]:
        """Affiche message profil non trouvé (recherche par téléphone) avec navigation"""
        new_state = self._execute_transition(
            user=user,
            current_state=state,
            next_state_name="OWNER_PHONE_NOT_FOUND",
            expected_input_type=ConversationState.InputType.MENU_CHOICE,
            allowed_values=["1", "2"],
            temp_data={"searched_phone": searched_phone},
        )
        return self._handle_owner_phone_not_found(user, new_state, "")

    def _handle_owner_phone_not_found(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler for phone search not found result"""
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="OWNER_PHONE_NOT_FOUND",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"],
            )

        if not message_text:
            searched_phone = self.conversation_service.get_temp_data(state, "searched_phone", "")
            body = "Aucun profil trouvé pour ce numéro. Le chauffeur peut créer un profil en s'inscrivant."
            if searched_phone:
                body = f"{body}\n\nNuméro: {searched_phone}"
            return "OWNER_PHONE_NOT_FOUND", MessagePayload(
                title="PROFIL NON TROUVÉ",
                body=body,
                options=[
                    {"number": "1", "text": "Nouvelle recherche"},
                    {"number": "2", "text": "Retour au menu"},
                ],
            )

        if message_text == "1":
            return self._handle_owner_search_by_phone(user, None, "")
        if message_text == "2":
            return self._handle_owner_main_menu(user, None, "")

        return self._handle_invalid_input(user, state, "Choix invalide")
    
    def _handle_owner_search_by_permit(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour recherche par permis"""
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="OWNER_SEARCH_BY_PERMIT",
                expected_input_type=ConversationState.InputType.PERMIT_NUMBER,
                allowed_values=None,
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE
            )
        
        if not message_text:
            payload = MessagePayload(
                title="RECHERCHE PAR PERMIS",
                body="Entrez le numéro de permis du chauffeur.\n\nFormat : 8 à 15 caractères alphanumériques\n\nOu tapez 0 pour annuler."
            )
            return "OWNER_SEARCH_BY_PERMIT", payload
        
        if message_text == "0":
            return self._handle_owner_verify_driver(user, None, "")
        
        # Rechercher par permis
        driver_profile = self.identity_service.search_by_permit(message_text)
        
        if driver_profile:
            if driver_profile.identity_locked:
                return self._handle_owner_view_profile_result(user, driver_profile)
            else:
                return self._handle_owner_view_profile_not_locked(user, driver_profile)
        else:
            # Profil non trouvé - sauvegarder permis et proposer invitation
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="OWNER_INVITE_DRIVER",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"]
            )
            # Normaliser et sauvegarder le permis
            is_valid, normalized, error = self.identity_service.normalize_and_validate_permit(message_text)
            if is_valid:
                state = self.conversation_service.set_temp_data(state, "permit_normalized", normalized)
                return self._handle_owner_invite_driver(user, state, "")
            else:
                return "OWNER_MAIN_MENU", MessagePayload(
                    body="Aucun profil trouvé pour ce permis. Le chauffeur peut créer un profil en s'inscrivant."
                )
    
    def _handle_owner_view_profile_not_locked(
        self,
        user: User,
        driver_profile: Optional[DriverProfile] = None,
        state: Optional[ConversationState] = None,
        message_text: str = ""
    ) -> Tuple[str, MessagePayload]:
        """Handler pour profil non verrouillé - Orchestrateur mince"""
        # Si driver_profile passé directement (depuis view_profile_result)
        if driver_profile:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="OWNER_VIEW_PROFILE_NOT_LOCKED",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"]
            )
            state = self.conversation_service.set_temp_data(state, "driver_id", driver_profile.user.id)
        
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="OWNER_VIEW_PROFILE_NOT_LOCKED",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"]
            )
        
        if not message_text:
            driver_id = self.conversation_service.get_temp_data(state, "driver_id")
            if driver_id:
                driver = User.objects.get(id=driver_id)
                driver_profile = driver.driver_profile
            else:
                return self._owner_main_menu_notice(user, "Profil introuvable.")
            
            display_name = driver_profile.display_name or driver_profile.user.phone_number
            
            return "OWNER_VIEW_PROFILE_NOT_LOCKED", MessagePayload(
                title="PROFIL NON VÉRIFIÉ",
                body=f"Nom : {display_name}\nIdentité : Non vérifiée\n\nLe chauffeur doit verrouiller son identité pour recevoir des évaluations.",
                options=[
                    {"number": "1", "text": "Contacter le chauffeur"},
                    {"number": "2", "text": "Retour"},
                    {"number": "3", "text": "Retour au menu"}
                ]
            )
        
        if message_text == "1":
            # Afficher numéro
            driver_id = self.conversation_service.get_temp_data(state, "driver_id")
            driver = User.objects.get(id=driver_id)
            return "OWNER_VIEW_PROFILE_NOT_LOCKED", MessagePayload(
                body=f"Numéro de téléphone : {driver.phone_number}"
            )
        elif message_text == "2":
            return self._handle_owner_verify_driver(user, None, "")
        elif message_text == "3":
            return self._handle_owner_main_menu(user, None, "")
        
        return self._handle_invalid_input(user, state, "Choix invalide")
    
    def _handle_owner_view_profile_result(
        self,
        user: User,
        driver_profile
    ) -> Tuple[str, MessagePayload]:
        """Handler pour affichage résultat profil - Orchestrateur mince"""
        # Récupérer la réputation (utilise le cache, ne recalcule pas)
        reputation = self.reputation_service.get_reputation(driver_profile, force_recompute=False)
        
        # Router vers le bon handler selon l'état
        if not driver_profile.identity_locked:
            return self._handle_owner_view_profile_not_locked(user, driver_profile)
        elif reputation['ratings_count'] == 0:
            return self._handle_owner_view_profile_new(user, driver_profile)
        else:
            return self._handle_owner_view_profile_with_history(user, driver_profile)
    
    def _handle_owner_invite_driver(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour inviter un driver - Orchestrateur mince"""
        # Récupérer le permis depuis temp_data (si recherche par permis)
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="OWNER_INVITE_DRIVER",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"]
            )
        
        permit_normalized = self.conversation_service.get_temp_data(state, "permit_normalized")
        
        if not message_text:
            if permit_normalized:
                masked = self.identity_service.get_display_permit_for_confirmation(permit_normalized)
                return "OWNER_INVITE_DRIVER", MessagePayload(
                    title="INVITER UN CHAUFFEUR",
                    body=f"Permis : {masked}\n\nLe chauffeur n'a pas de profil. Souhaitez-vous l'inviter à créer un profil ?",
                    options=[
                        {"number": "1", "text": "Oui, envoyer invitation"},
                        {"number": "2", "text": "Non, annuler"}
                    ]
                )
            else:
                return "OWNER_MAIN_MENU", MessagePayload(
                    body="Aucune information de permis disponible."
                )
        
        if message_text == "1":
            # Invitation envoyée (notification service sera implémenté plus tard)
            return "OWNER_MAIN_MENU", MessagePayload(
                body="Invitation envoyée. Le chauffeur recevra une notification pour créer son profil."
            )
        elif message_text == "2":
            return self._handle_owner_main_menu(user, None, "")
        
        return self._handle_invalid_input(user, state, "Choix invalide")
    
    def _handle_owner_declare_collaboration(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour déclarer collaboration - Orchestrateur mince"""
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="OWNER_DECLARE_COLLABORATION",
                expected_input_type=ConversationState.InputType.PHONE_NUMBER,
                allowed_values=None
            )
        
        # Vérifier si driver_id pré-rempli depuis profil
        driver_id = self.conversation_service.get_temp_data(state, "driver_id")
        
        if driver_id:
            # Driver pré-rempli - afficher confirmation
            if not message_text:
                driver = User.objects.get(id=driver_id)
                display_name = driver.driver_profile.display_name if hasattr(driver, 'driver_profile') else driver.phone_number
                return "OWNER_DECLARE_COLLABORATION", MessagePayload(
                    title="DÉCLARER UNE COLLABORATION",
                    body=f"Chauffeur : {display_name}\n\nConfirmer la déclaration ?",
                    options=[
                        {"number": "1", "text": "Oui, continuer"},
                        {"number": "2", "text": "Non, annuler"}
                    ]
                )
            
            if message_text == "1":
                # Confirmer - transition vers dates
                new_state = self._execute_transition(
                    user, state, "OWNER_DECLARE_COLLABORATION_DATES",
                    ConversationState.InputType.SANITIZED_TEXT,
                    temp_data={"driver_id": driver_id}
                )
                return "OWNER_DECLARE_COLLABORATION_DATES", MessagePayload(
                    title="DATES DE COLLABORATION",
                    body="Entrez les dates de collaboration.\n\nFormat : JJ/MM/AAAA - JJ/MM/AAAA\n(Exemple : 01/01/2024 - 07/01/2024)\n\nMinimum 7 jours requis."
                )
            elif message_text == "2":
                return self._handle_owner_main_menu(user, None, "")
            else:
                return self._handle_invalid_input(user, state, "Choix invalide")
        else:
            # Pas de driver pré-rempli - demander numéro
            if not message_text:
                return "OWNER_DECLARE_COLLABORATION", MessagePayload(
                    title="DÉCLARER UNE COLLABORATION",
                    body="Entrez le numéro de téléphone du chauffeur.\n\nFormat : +221771234567\n\nOu tapez 0 pour annuler."
                )
            
            if message_text == "0":
                return self._handle_owner_main_menu(user, None, "")
            
            # Rechercher le driver
            try:
                driver = User.objects.get(
                    Q(phone_number=message_text) | Q(contact_phone_number=message_text),
                    role=User.Role.DRIVER,
                    is_active=True,
                )
                # Sauvegarder dans temp_data
                state = self.conversation_service.set_temp_data(state, "driver_id", driver.id)
                # Transition vers saisie dates
                new_state = self._execute_transition(
                    user, state, "OWNER_DECLARE_COLLABORATION_DATES",
                    ConversationState.InputType.SANITIZED_TEXT,
                    temp_data={"driver_id": driver.id}
                )
                return "OWNER_DECLARE_COLLABORATION_DATES", MessagePayload(
                    title="DATES DE COLLABORATION",
                    body="Entrez les dates de collaboration.\n\nFormat : JJ/MM/AAAA - JJ/MM/AAAA\n(Exemple : 01/01/2024 - 07/01/2024)\n\nMinimum 7 jours requis."
                )
            except User.DoesNotExist:
                return "OWNER_DECLARE_COLLABORATION", MessagePayload(
                    body="Chauffeur non trouvé. Vérifiez le numéro ou invitez-le à créer un profil."
                )
    
    def _handle_owner_declare_collaboration_dates(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour dates collaboration - Orchestrateur mince"""
        from datetime import datetime
        
        if not message_text:
            return "OWNER_DECLARE_COLLABORATION_DATES", MessagePayload(
                title="DATES DE COLLABORATION",
                body="Entrez les dates de collaboration.\n\nFormat : JJ/MM/AAAA - JJ/MM/AAAA\n(Exemple : 01/01/2024 - 07/01/2024)\n\nMinimum 7 jours requis.",
                options=[
                    {"number": "3", "text": "Retour"},
                    {"number": "0", "text": "Quitter"}
                ]
            )

        # Navigation options
        if message_text == "0":
            return self._handle_owner_main_menu(user, None, "")
        if message_text == "3":
            # Go back to the previous step (driver confirmation / selection)
            driver_id = self.conversation_service.get_temp_data(state, "driver_id")
            if driver_id:
                back_state = self._execute_transition(
                    user=user,
                    current_state=state,
                    next_state_name="OWNER_DECLARE_COLLABORATION",
                    expected_input_type=ConversationState.InputType.MENU_CHOICE,
                    allowed_values=["1", "2"],
                    temp_data={"driver_id": driver_id},
                )
                return self._handle_owner_declare_collaboration(user, back_state, "")
            return self._handle_owner_main_menu(user, None, "")
        
        # Parser les dates
        try:
            parts = message_text.split(" - ")
            if len(parts) != 2:
                return self._handle_invalid_input(user, state, "Format invalide. Utilisez : JJ/MM/AAAA - JJ/MM/AAAA")
            
            start_date = datetime.strptime(parts[0].strip(), "%d/%m/%Y").date()
            end_date = datetime.strptime(parts[1].strip(), "%d/%m/%Y").date()
            
            # Récupérer le driver
            driver_id = self.conversation_service.get_temp_data(state, "driver_id")
            if not driver_id:
                return self._handle_owner_main_menu(user, None, "")
            
            driver = User.objects.get(id=driver_id)
            
            # Appeler CollaborationService (logique métier)
            success, collaboration, error = self.collaboration_service.declare_collaboration(
                owner=user,
                driver=driver,
                start_date=start_date,
                end_date=end_date
            )
            
            if success:
                return "OWNER_MAIN_MENU", MessagePayload(
                    body=f"Collaboration déclarée avec succès. Le chauffeur recevra une notification pour confirmer."
                )
            else:
                return "OWNER_DECLARE_COLLABORATION_DATES", MessagePayload(
                    body=f"Erreur : {error}"
                )
        except ValueError:
            return self._handle_invalid_input(user, state, "Format de date invalide. Utilisez JJ/MM/AAAA")
        except User.DoesNotExist:
            return self._owner_main_menu_notice(user, "Chauffeur introuvable.")
    
    def _handle_owner_rate_select_collaboration(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour sélection collaboration à noter - Orchestrateur mince"""
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="OWNER_RATE_SELECT_COLLABORATION",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=[]
            )
        
        # Récupérer les collaborations éligibles via CollaborationService
        eligible = self.collaboration_service.get_eligible_collaborations_for_rating(user)
        
        if not message_text:
            if not eligible:
                return "OWNER_MAIN_MENU", MessagePayload(
                    body="Aucune collaboration éligible pour évaluation."
                )
            
            # Construire le menu
            options = []
            for idx, collab in enumerate(eligible[:9], 1):  # Max 9 options
                driver_name = collab.driver.driver_profile.display_name if hasattr(collab.driver, 'driver_profile') else collab.driver.phone_number
                options.append({
                    "number": str(idx),
                    "text": f"{driver_name} ({collab.duration_days} jours)"
                })
            options.append({"number": "0", "text": "Retour"})
            
            # Sauvegarder les IDs dans temp_data
            collab_ids = [str(c.id) for c in eligible[:9]]
            state = self.conversation_service.set_temp_data(state, "collaboration_ids", collab_ids)
            state = self.conversation_service.update_state(
                state,
                allowed_values=[str(i) for i in range(1, len(eligible[:9]) + 1)] + ["0"]
            )
            
            return "OWNER_RATE_SELECT_COLLABORATION", MessagePayload(
                title="SÉLECTIONNER UNE COLLABORATION",
                body="Choisissez la collaboration à évaluer :",
                options=options
            )
        
        if message_text == "0":
            return self._handle_owner_main_menu(user, None, "")
        
        # Récupérer la collaboration sélectionnée
        collab_ids = self.conversation_service.get_temp_data(state, "collaboration_ids", [])
        try:
            idx = int(message_text) - 1
            if 0 <= idx < len(collab_ids):
                collab_id = int(collab_ids[idx])
                collaboration = self.collaboration_service.get_collaboration_by_id(collab_id)
                if collaboration:
                    # Sauvegarder et transition
                    state = self.conversation_service.set_temp_data(state, "selected_collaboration_id", collab_id)
                    new_state = self._execute_transition(
                        user, state, "OWNER_RATE_COLLECT_SCORES",
                        ConversationState.InputType.SCORE_RATING,
                        allowed_values=["1", "2", "3", "4", "5"],
                        temp_data={"selected_collaboration_id": collab_id, "scores": {}}
                    )
                    return "OWNER_RATE_COLLECT_SCORES", MessagePayload(
                        title="ÉVALUATION - PONCTUALITÉ",
                        body="Notez la ponctualité du chauffeur (1-5) :\n\n1 = Très mauvais\n5 = Excellent"
                    )
        except (ValueError, IndexError):
            return self._handle_invalid_input(user, state, "Choix invalide")
        
        return self._handle_invalid_input(user, state, "Choix invalide")
    
    def _handle_owner_rate_collect_scores(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour collecte scores - Orchestrateur mince"""
        scores = self.conversation_service.get_temp_data(state, "scores", {})
        score_order = ["punctuality", "vehicle_respect", "client_relation", "reliability"]
        score_labels = {
            "punctuality": "PONCTUALITÉ",
            "vehicle_respect": "RESPECT DU VÉHICULE",
            "client_relation": "RELATION CLIENT",
            "reliability": "FIABILITÉ GLOBALE"
        }
        
        if message_text:
            # Valider le score
            try:
                score = int(message_text)
                if not (1 <= score <= 5):
                    return self._handle_invalid_input(user, state, "Score invalide. Entrez un nombre entre 1 et 5")
                
                # Trouver le prochain score à collecter
                current_idx = len(scores)
                if current_idx < len(score_order):
                    current_key = score_order[current_idx]
                    scores[current_key] = score
                    state = self.conversation_service.set_temp_data(state, "scores", scores)
                    
                    # Passer au suivant
                    if current_idx + 1 < len(score_order):
                        next_key = score_order[current_idx + 1]
                        return "OWNER_RATE_COLLECT_SCORES", MessagePayload(
                            title=f"ÉVALUATION - {score_labels[next_key]}",
                            body=f"Notez {score_labels[next_key].lower()} (1-5) :\n\n1 = Très mauvais\n5 = Excellent"
                        )
                    else:
                        # Tous les scores collectés - demander commentaire
                        new_state = self._execute_transition(
                            user, state, "OWNER_RATE_COLLECT_COMMENT",
                            ConversationState.InputType.MENU_CHOICE,
                            allowed_values=["1", "2"]
                        )
                        return "OWNER_RATE_COLLECT_COMMENT", MessagePayload(
                            title="COMMENTAIRE",
                            body="Souhaitez-vous ajouter un commentaire ?",
                            options=[
                                {"number": "1", "text": "Oui, ajouter un commentaire"},
                                {"number": "2", "text": "Non, publier sans commentaire"}
                            ]
                        )
            except ValueError:
                return self._handle_invalid_input(user, state, "Score invalide. Entrez un nombre entre 1 et 5")
        
        # Premier appel - demander le premier score
        return "OWNER_RATE_COLLECT_SCORES", MessagePayload(
            title="ÉVALUATION - PONCTUALITÉ",
            body="Notez la ponctualité du chauffeur (1-5) :\n\n1 = Très mauvais\n5 = Excellent"
        )
    
    def _handle_owner_rate_collect_comment(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour collecte commentaire - Orchestrateur mince"""
        if not message_text:
            return "OWNER_RATE_COLLECT_COMMENT", MessagePayload(
                title="COMMENTAIRE",
                body="Souhaitez-vous ajouter un commentaire ?",
                options=[
                    {"number": "1", "text": "Oui, ajouter un commentaire"},
                    {"number": "2", "text": "Non, publier sans commentaire"}
                ]
            )
        
        if message_text == "1":
            # Transition vers saisie commentaire
            new_state = self._execute_transition(
                user, state, "OWNER_RATE_ENTER_COMMENT",
                ConversationState.InputType.SANITIZED_TEXT
            )
            return "OWNER_RATE_ENTER_COMMENT", MessagePayload(
                title="SAISIE COMMENTAIRE",
                body="Entrez votre commentaire (max 500 caractères) :"
            )
        elif message_text == "2":
            # Publier sans commentaire - créer le rating
            return self._publish_rating_without_comment(user, state)
        
        return self._handle_invalid_input(user, state, "Choix invalide")
    
    def _publish_rating_without_comment(
        self,
        user: User,
        state: ConversationState
    ) -> Tuple[str, MessagePayload]:
        """Publie un rating sans commentaire - Orchestrateur mince"""
        scores = self.conversation_service.get_temp_data(state, "scores", {})
        collab_id = self.conversation_service.get_temp_data(state, "selected_collaboration_id")
        
        if not collab_id or len(scores) != 4:
            return self._owner_main_menu_notice(user, "Données incomplètes.")
        
        collaboration = self.collaboration_service.get_collaboration_by_id(collab_id)
        if not collaboration:
            return self._owner_main_menu_notice(user, "Collaboration introuvable.")
        
        # Créer le rating via RatingService
        success, rating, error = self.rating_service.create_rating(
            owner=user,
            collaboration=collaboration,
            score_punctuality=scores["punctuality"],
            score_vehicle_respect=scores["vehicle_respect"],
            score_client_relation=scores["client_relation"],
            score_reliability=scores["reliability"],
            comment_raw=None
        )
        
        if success:
            # Publier sans commentaire
            publish_success, publish_error = self.rating_service.publish_without_comment(rating, user)
            if publish_success:
                return "OWNER_MAIN_MENU", MessagePayload(
                    body="Évaluation publiée avec succès (scores uniquement)."
                )
            else:
                return self._owner_main_menu_notice(user, f"Erreur publication : {publish_error}")
        else:
            return self._owner_main_menu_notice(user, f"Erreur : {error}")
    
    def _handle_owner_rate_enter_comment(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour saisie commentaire - Orchestrateur mince"""
        if not message_text:
            return "OWNER_RATE_ENTER_COMMENT", MessagePayload(
                title="SAISIE COMMENTAIRE",
                body="Entrez votre commentaire (max 500 caractères) :"
            )
        
        # Créer le rating avec commentaire brut
        scores = self.conversation_service.get_temp_data(state, "scores", {})
        collab_id = self.conversation_service.get_temp_data(state, "selected_collaboration_id")
        
        if not collab_id or len(scores) != 4:
            return self._owner_main_menu_notice(user, "Données incomplètes.")
        
        collaboration = self.collaboration_service.get_collaboration_by_id(collab_id)
        if not collaboration:
            return self._owner_main_menu_notice(user, "Collaboration introuvable.")
        
        # Créer le rating via RatingService
        success, rating, error = self.rating_service.create_rating(
            owner=user,
            collaboration=collaboration,
            score_punctuality=scores["punctuality"],
            score_vehicle_respect=scores["vehicle_respect"],
            score_client_relation=scores["client_relation"],
            score_reliability=scores["reliability"],
            comment_raw=message_text
        )
        
        if not success:
            return self._owner_main_menu_notice(user, f"Erreur : {error}")
        
        # Sanitiser via RatingService (qui appelle LLMService)
        sanitize_success, sanitized, sanitize_error = self.rating_service.sanitize_comment(
            rating, self.llm_service
        )
        
        if sanitize_success:
            # Transition vers révision
            new_state = self._execute_transition(
                user, state, "OWNER_RATE_REVIEW_SANITIZED",
                ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"],
                temp_data={"rating_id": rating.id}
            )
            return "OWNER_RATE_REVIEW_SANITIZED", MessagePayload(
                title="APERÇU DU COMMENTAIRE",
                body=f"Commentaire nettoyé :\n\n{sanitized}\n\nSouhaitez-vous publier ce commentaire ?",
                options=[
                    {"number": "1", "text": "Oui, publier"},
                    {"number": "2", "text": "Non, modifier"}
                ]
            )
        else:
            # LLM failure - publish without comment
            logger.warning(f"LLM sanitization failed for rating {rating.id}, publishing without comment")
            publish_success, publish_error = self.rating_service.publish_without_comment(rating, user)
            if publish_success:
                return "OWNER_MAIN_MENU", MessagePayload(
                    body="Évaluation publiée avec succès (scores uniquement, commentaire non disponible)."
                )
            else:
                return "OWNER_MAIN_MENU", MessagePayload(
                    body=f"Erreur lors de la publication : {publish_error}"
                )
    
    def _handle_owner_rate_review_sanitized(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour révision commentaire sanitisé - Orchestrateur mince"""
        rating_id = self.conversation_service.get_temp_data(state, "rating_id")
        if not rating_id:
            return "OWNER_MAIN_MENU", MessagePayload(body="Rating introuvable.")
        
        rating = self.rating_service.get_rating_by_id(rating_id)
        if not rating:
            return "OWNER_MAIN_MENU", MessagePayload(body="Rating introuvable.")
        
        if not message_text:
            return "OWNER_RATE_REVIEW_SANITIZED", MessagePayload(
                title="APERÇU DU COMMENTAIRE",
                body=f"Commentaire nettoyé :\n\n{rating.comment_sanitized}\n\nSouhaitez-vous publier ce commentaire ?",
                options=[
                    {"number": "1", "text": "Oui, publier"},
                    {"number": "2", "text": "Non, modifier"}
                ]
            )
        
        if message_text == "1":
            # Approuver via RatingService
            success, error = self.rating_service.approve_sanitized_comment(rating, user)
            if success:
                return "OWNER_MAIN_MENU", MessagePayload(
                    body="Évaluation publiée avec succès."
                )
            else:
                return "OWNER_RATE_REVIEW_SANITIZED", MessagePayload(
                    body=f"Erreur : {error}"
                )
        elif message_text == "2":
            # Rejeter via RatingService
            success, error, can_retry = self.rating_service.reject_sanitized_comment(rating, user)
            if success:
                if can_retry:
                    # Retour à la saisie
                    new_state = self._execute_transition(
                        user, state, "OWNER_RATE_ENTER_COMMENT",
                        ConversationState.InputType.SANITIZED_TEXT,
                        temp_data={"rating_id": rating_id}
                    )
                    return "OWNER_RATE_ENTER_COMMENT", MessagePayload(
                        title="MODIFIER LE COMMENTAIRE",
                        body=f"Vous avez rejeté le commentaire ({rating.sanitization_rejections_count}/3).\n\nEntrez votre nouveau commentaire :"
                    )
                else:
                    # Limite atteinte - publish without comment immediately
                    logger.info(f"Max rejections reached for rating {rating.id}, publishing without comment")
                    publish_success, publish_error = self.rating_service.publish_without_comment(rating, user)
                    if publish_success:
                        return "OWNER_MAIN_MENU", MessagePayload(
                            body="Évaluation publiée avec succès (scores uniquement, limite de modifications atteinte)."
                        )
                    else:
                        return "OWNER_MAIN_MENU", MessagePayload(
                            body=f"Erreur lors de la publication : {publish_error}"
                        )
            else:
                return "OWNER_RATE_REVIEW_SANITIZED", MessagePayload(body=f"Erreur : {error}")
        
        return self._handle_invalid_input(user, state, "Choix invalide")
    
    def _handle_owner_rate_limit_reached(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour limite atteinte - Orchestrateur mince"""
        rating_id = self.conversation_service.get_temp_data(state, "rating_id")
        if not rating_id:
            return "OWNER_MAIN_MENU", MessagePayload(body="Rating introuvable.")
        
        rating = self.rating_service.get_rating_by_id(rating_id)
        if not rating:
            return "OWNER_MAIN_MENU", MessagePayload(body="Rating introuvable.")
        
        if not message_text:
            return "OWNER_RATE_LIMIT_REACHED", MessagePayload(
                title="LIMITE ATTEINTE",
                body="Vous avez rejeté le commentaire 3 fois.\n\nQue souhaitez-vous faire ?",
                options=[
                    {"number": "1", "text": "Publier sans commentaire (scores uniquement)"},
                    {"number": "2", "text": "Annuler l'évaluation"}
                ]
            )
        
        if message_text == "1":
            # Publier sans commentaire via RatingService
            success, error = self.rating_service.publish_without_comment(rating, user)
            if success:
                return "OWNER_MAIN_MENU", MessagePayload(
                    body="Évaluation publiée avec succès (scores uniquement)."
                )
            else:
                return "OWNER_RATE_LIMIT_REACHED", MessagePayload(body=f"Erreur : {error}")
        elif message_text == "2":
            # Annuler - supprimer le rating
            rating.delete()
            return "OWNER_MAIN_MENU", MessagePayload(
                body="Évaluation annulée."
            )
        
        return self._handle_invalid_input(user, state, "Choix invalide")
    
    def _handle_owner_view_collaborations(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour voir collaborations (+ accès évaluation si éligible) - Orchestrateur mince"""
        # Récupérer toutes les collaborations de l'owner
        from ..models import Collaboration
        collaborations = Collaboration.objects.filter(
            owner=user
        ).select_related('driver', 'driver__driver_profile').order_by('-declared_at')[:10]

        # Récupérer les collaborations éligibles pour afficher l'action "Évaluer"
        eligible = self.collaboration_service.get_eligible_collaborations_for_rating(user)
        has_eligible = bool(eligible)

        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="OWNER_VIEW_COLLABORATIONS",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"] if has_eligible else ["1"],
            )

        if not message_text:
            if not collaborations:
                if has_eligible:
                    # Théoriquement impossible (éligible ⊆ collaborations), mais on garde une UX propre.
                    return "OWNER_VIEW_COLLABORATIONS", MessagePayload(
                        title="VOS COLLABORATIONS",
                        body="Aucune collaboration trouvée.",
                        options=[
                            {"number": "1", "text": "Évaluer une collaboration"},
                            {"number": "2", "text": "Retour au menu"},
                        ],
                    )
                return "OWNER_VIEW_COLLABORATIONS", MessagePayload(
                    title="VOS COLLABORATIONS",
                    body="Aucune collaboration déclarée.",
                    options=[{"number": "1", "text": "Retour au menu"}],
                )

            # Construire le résumé
            body_lines = ["VOS COLLABORATIONS", ""]

            state_labels = {
                Collaboration.State.DECLARED: "Déclarée",
                Collaboration.State.PENDING_CONFIRMATION: "En attente",
                Collaboration.State.CONFIRMED: "Confirmée",
                Collaboration.State.ELIGIBLE_FOR_RATING: "Éligible évaluation",
                Collaboration.State.RATED: "Évaluée",
            }

            for idx, collab in enumerate(collaborations, 1):
                driver_name = (
                    collab.driver.driver_profile.display_name
                    if hasattr(collab.driver, "driver_profile")
                    else collab.driver.phone_number
                )
                body_lines.append(f"{idx}. {driver_name}")
                body_lines.append(f"   État : {state_labels.get(collab.state, collab.state)}")
                if collab.start_date and collab.end_date:
                    body_lines.append(f"   Période : {collab.start_date} au {collab.end_date}")
                if collab.duration_days:
                    body_lines.append(f"   Durée : {collab.duration_days} jours")
                body_lines.append("")

            # Options
            if has_eligible:
                options = [
                    {"number": "1", "text": "Évaluer une collaboration"},
                    {"number": "2", "text": "Retour au menu"},
                ]
                # Keep allowed values in sync with current eligibility
                self.conversation_service.update_state(state, allowed_values=["1", "2"])
            else:
                options = [{"number": "1", "text": "Retour au menu"}]
                self.conversation_service.update_state(state, allowed_values=["1"])

            return "OWNER_VIEW_COLLABORATIONS", MessagePayload(
                title="VOS COLLABORATIONS",
                body="\n".join(body_lines).strip(),
                options=options,
            )

        # Handle choice
        if has_eligible and message_text == "1":
            return self._handle_owner_rate_select_collaboration(user, None, "")
        if has_eligible and message_text == "2":
            return self._handle_owner_main_menu(user, None, "")
        if (not has_eligible) and message_text == "1":
            return self._handle_owner_main_menu(user, None, "")

        return self._handle_invalid_input(user, state, "Choix invalide")
    
    def _handle_owner_view_profile_new(
        self,
        user: User,
        driver_profile: Optional[DriverProfile] = None,
        state: Optional[ConversationState] = None,
        message_text: str = ""
    ) -> Tuple[str, MessagePayload]:
        """Handler pour profil nouveau - Orchestrateur mince"""
        # Si driver_profile passé directement (depuis view_profile_result)
        if driver_profile:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="OWNER_VIEW_PROFILE_NEW",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"]
            )
            state = self.conversation_service.set_temp_data(state, "driver_id", driver_profile.user.id)
        
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="OWNER_VIEW_PROFILE_NEW",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"]
            )
        
        if not message_text:
            driver_id = self.conversation_service.get_temp_data(state, "driver_id")
            if driver_id:
                driver = User.objects.get(id=driver_id)
                driver_profile = driver.driver_profile
            else:
                return "OWNER_MAIN_MENU", MessagePayload(body="Profil introuvable.")
            
            display_name = driver_profile.display_name or driver_profile.user.phone_number
            reputation = self.reputation_service.get_reputation(driver_profile, force_recompute=False)
            
            return "OWNER_VIEW_PROFILE_NEW", MessagePayload(
                title="PROFIL VÉRIFIÉ",
                body=f"Nom : {display_name}\nIdentité : Vérifiée\nRéputation : Nouveau (0 collaboration confirmée)\n\nAucune collaboration confirmée pour le moment.",
                options=[
                    {"number": "1", "text": "Contacter le chauffeur"},
                    {"number": "2", "text": "Déclarer une collaboration"},
                    {"number": "3", "text": "Retour au menu"}
                ]
            )
        
        if message_text == "1":
            # Afficher numéro
            driver_id = self.conversation_service.get_temp_data(state, "driver_id")
            driver = User.objects.get(id=driver_id)
            return "OWNER_VIEW_PROFILE_NEW", MessagePayload(
                body=f"Numéro de téléphone : {driver.phone_number}"
            )
        elif message_text == "2":
            # Déclarer collaboration avec driver pré-rempli
            driver_id = self.conversation_service.get_temp_data(state, "driver_id")
            new_state = self._execute_transition(
                user, state, "OWNER_DECLARE_COLLABORATION",
                ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"],
                temp_data={"driver_id": driver_id}
            )
            driver = User.objects.get(id=driver_id)
            display_name = driver.driver_profile.display_name if hasattr(driver, 'driver_profile') else driver.phone_number
            return "OWNER_DECLARE_COLLABORATION", MessagePayload(
                title="DÉCLARER UNE COLLABORATION",
                body=f"Chauffeur : {display_name}\n\nConfirmer la déclaration ?",
                options=[
                    {"number": "1", "text": "Oui, continuer"},
                    {"number": "2", "text": "Non, annuler"}
                ]
            )
        elif message_text == "3":
            return self._handle_owner_main_menu(user, None, "")
        
        return self._handle_invalid_input(user, state, "Choix invalide")
    
    def _handle_owner_view_profile_with_history(
        self,
        user: User,
        driver_profile: Optional[DriverProfile] = None,
        state: Optional[ConversationState] = None,
        message_text: str = ""
    ) -> Tuple[str, MessagePayload]:
        """Handler pour profil avec historique - Orchestrateur mince"""
        # Si driver_profile passé directement (depuis view_profile_result)
        if driver_profile:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="OWNER_VIEW_PROFILE_WITH_HISTORY",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3", "4"]
            )
            state = self.conversation_service.set_temp_data(state, "driver_id", driver_profile.user.id)
        
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="OWNER_VIEW_PROFILE_WITH_HISTORY",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3", "4"]
            )
        
        if not message_text:
            driver_id = self.conversation_service.get_temp_data(state, "driver_id")
            if driver_id:
                driver = User.objects.get(id=driver_id)
                driver_profile = driver.driver_profile
            else:
                return "OWNER_MAIN_MENU", MessagePayload(body="Profil introuvable.")
            
            display_name = driver_profile.display_name or driver_profile.user.phone_number
            reputation = self.reputation_service.get_reputation(driver_profile, force_recompute=False)
            
            state_labels = {
                DriverProfile.ReputationState.NEW: "Nouveau",
                DriverProfile.ReputationState.ESTABLISHED: "Établi",
                DriverProfile.ReputationState.CONFIRMED: "Confirmé",
                DriverProfile.ReputationState.REBUILDING: "Reconstruction"
            }
            
            body_lines = [
                f"Nom : {display_name}",
                f"Identité : Vérifiée",
                f"Réputation : {state_labels.get(reputation['state'], reputation['state'])}",
                f"Score médian : {reputation['score_median']}/5",
                f"Collaborations : {reputation['collaborations_count']} confirmée(s)"
            ]
            
            return "OWNER_VIEW_PROFILE_WITH_HISTORY", MessagePayload(
                title="PROFIL VÉRIFIÉ",
                body="\n".join(body_lines),
                options=[
                    {"number": "1", "text": "Voir le profil complet"},
                    {"number": "2", "text": "Contacter le chauffeur"},
                    {"number": "3", "text": "Déclarer une collaboration"},
                    {"number": "4", "text": "Retour au menu"}
                ]
            )
        
        if message_text == "1":
            # Transition vers profil complet
            driver_id = self.conversation_service.get_temp_data(state, "driver_id")
            new_state = self._execute_transition(
                user, state, "OWNER_VIEW_FULL_PROFILE",
                ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"],
                temp_data={"driver_id": driver_id}
            )
            return self._handle_owner_view_full_profile(user, new_state, "")
        elif message_text == "2":
            # Afficher numéro
            driver_id = self.conversation_service.get_temp_data(state, "driver_id")
            driver = User.objects.get(id=driver_id)
            return "OWNER_VIEW_PROFILE_WITH_HISTORY", MessagePayload(
                body=f"Numéro de téléphone : {driver.phone_number}"
            )
        elif message_text == "3":
            # Déclarer collaboration avec driver pré-rempli
            driver_id = self.conversation_service.get_temp_data(state, "driver_id")
            new_state = self._execute_transition(
                user, state, "OWNER_DECLARE_COLLABORATION",
                ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"],
                temp_data={"driver_id": driver_id}
            )
            driver = User.objects.get(id=driver_id)
            display_name = driver.driver_profile.display_name if hasattr(driver, 'driver_profile') else driver.phone_number
            return "OWNER_DECLARE_COLLABORATION", MessagePayload(
                title="DÉCLARER UNE COLLABORATION",
                body=f"Chauffeur : {display_name}\n\nConfirmer la déclaration ?",
                options=[
                    {"number": "1", "text": "Oui, continuer"},
                    {"number": "2", "text": "Non, annuler"}
                ]
            )
        elif message_text == "4":
            return self._handle_owner_main_menu(user, None, "")
        
        return self._handle_invalid_input(user, state, "Choix invalide")
    
    def _handle_owner_view_full_profile(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour profil complet - Orchestrateur mince"""
        driver_id = self.conversation_service.get_temp_data(state, "driver_id")
        if not driver_id:
            return "OWNER_MAIN_MENU", MessagePayload(body="Profil introuvable.")
        
        driver = User.objects.get(id=driver_id)
        driver_profile = driver.driver_profile
        display_name = driver_profile.display_name or driver.phone_number
        reputation = self.reputation_service.get_reputation(driver_profile, force_recompute=False)
        
        state_labels = {
            DriverProfile.ReputationState.NEW: "Nouveau",
            DriverProfile.ReputationState.ESTABLISHED: "Établi",
            DriverProfile.ReputationState.CONFIRMED: "Confirmé",
            DriverProfile.ReputationState.REBUILDING: "Reconstruction"
        }
        
        if not message_text:
            # Construire le message avec les évaluations
            body_lines = [
                f"Nom : {display_name}",
                f"Identité : Vérifiée",
                f"Réputation : {state_labels.get(reputation['state'], reputation['state'])}",
                f"Score médian : {reputation['score_median']}/5",
                f"Collaborations : {reputation['collaborations_count']} confirmée(s)",
                "",
                "─── ÉVALUATIONS ───",
                ""
            ]
            
            # Récupérer les ratings publiés
            from ..models import Rating
            ratings = Rating.objects.filter(
                collaboration__driver=driver,
                is_published=True
            ).select_related('collaboration').order_by('-published_at')[:5]
            
            if ratings:
                for rating in ratings:
                    median = rating.get_median_score()
                    body_lines.append(f"⭐ {median:.1f}/5")
                    body_lines.append(
                        f"Ponctualité: {rating.score_punctuality}/5 | "
                        f"Véhicule: {rating.score_vehicle_respect}/5"
                    )
                    body_lines.append(
                        f"Client: {rating.score_client_relation}/5 | "
                        f"Fiabilité: {rating.score_reliability}/5"
                    )
                    if rating.comment_published:
                        body_lines.append(f'"{rating.comment_published}"')
                    if rating.driver_response:
                        body_lines.append(f'💬 Réponse : "{rating.driver_response}"')
                    body_lines.append("")
            else:
                body_lines.append("Aucune évaluation pour le moment.")
                body_lines.append("")
            
            body_lines.append("───")
            
            return "OWNER_VIEW_FULL_PROFILE", MessagePayload(
                title="PROFIL COMPLET",
                body="\n".join(body_lines),
                options=[
                    {"number": "1", "text": "Contacter le chauffeur"},
                    {"number": "2", "text": "Déclarer une collaboration"},
                    {"number": "3", "text": "Retour au menu"}
                ]
            )
        
        if message_text == "1":
            return "OWNER_VIEW_FULL_PROFILE", MessagePayload(
                body=f"Numéro de téléphone : {driver.phone_number}"
            )
        elif message_text == "2":
            # Déclarer collaboration avec driver pré-rempli
            new_state = self._execute_transition(
                user, state, "OWNER_DECLARE_COLLABORATION",
                ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"],
                temp_data={"driver_id": driver_id}
            )
            return "OWNER_DECLARE_COLLABORATION", MessagePayload(
                title="DÉCLARER UNE COLLABORATION",
                body=f"Chauffeur : {display_name}\n\nConfirmer la déclaration ?",
                options=[
                    {"number": "1", "text": "Oui, continuer"},
                    {"number": "2", "text": "Non, annuler"}
                ]
            )
        elif message_text == "3":
            return self._handle_owner_main_menu(user, None, "")
        
        return self._handle_invalid_input(user, state, "Choix invalide")
    
    # ==================== HANDLERS DRIVER ====================
    
    def _handle_driver_main_menu(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour menu principal driver"""
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="DRIVER_MAIN_MENU",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3", "4"]
            )
        
        has_profile = hasattr(user, "driver_profile")
        if not message_text:
            # If profile already exists, make option 1 "View my profile" instead of "Create my profile"
            option_1_label = "Voir mon profil" if has_profile else "Créer mon profil"
            payload = MessagePayload(
                title="MENU PRINCIPAL - CHAUFFEUR",
                body="Que souhaitez-vous faire ?",
                options=[
                    {"number": "1", "text": option_1_label},
                    {"number": "2", "text": "Voir ma réputation"},
                    {"number": "3", "text": "Voir mes collaborations"},
                    {"number": "4", "text": "Quitter"}
                ]
            )
            return "DRIVER_MAIN_MENU", payload
        
        if message_text == "1":
            if has_profile:
                return self._handle_driver_view_profile(user, None, "")
            return self._handle_driver_create_profile(user, None, "")
        elif message_text == "2":
            return self._handle_driver_view_reputation(user, None, "")
        elif message_text == "3":
            return self._handle_driver_view_collaborations(user, None, "")
        elif message_text == "4":
            if state:
                self.conversation_service.delete_state(state)
            return "END", MessagePayload(body="Au revoir ! À bientôt.")
        
        return self._handle_invalid_input(user, state, "Choix invalide")

    def _handle_driver_view_profile(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str,
    ) -> Tuple[str, MessagePayload]:
        """Show driver's profile summary (no dead-end)."""
        if not hasattr(user, "driver_profile"):
            return self._driver_main_menu_notice(user, "Vous n'avez pas encore de profil. Choisissez 'Créer mon profil'.")

        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="DRIVER_VIEW_PROFILE",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )

        if not message_text:
            dp = user.driver_profile
            phone = user.contact_phone_number or "(non renseigné)"
            locked = "✅ Verrouillée" if dp.identity_locked else "⚠️ Non verrouillée"
            name = dp.display_name or "(non renseigné)"

            return "DRIVER_VIEW_PROFILE", MessagePayload(
                title="MON PROFIL",
                body=f"Nom : {name}\nTéléphone : {phone}\nIdentité : {locked}",
                options=[
                    {"number": "1", "text": "Modifier mon nom"},
                    {"number": "2", "text": "Supprimer mon profil"},
                    {"number": "3", "text": "Retour au menu"},
                ],
            )

        if message_text == "1":
            # Go to update name flow
            new_state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="DRIVER_UPDATE_DISPLAY_NAME",
                expected_input_type=ConversationState.InputType.SANITIZED_TEXT,
                allowed_values=None,
                temp_data=state.temp_data,
            )
            return self._handle_driver_update_display_name(user, new_state, "")
        if message_text == "2":
            # Delete/deactivate confirmation
            new_state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="DRIVER_DELETE_PROFILE_CONFIRM",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"],
                temp_data=state.temp_data,
            )
            return self._handle_driver_delete_profile_confirm(user, new_state, "")
        if message_text == "3":
            return self._handle_driver_main_menu(user, None, "")

        return self._handle_invalid_input(user, state, "Choix invalide")

    def _handle_driver_delete_profile_confirm(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str,
    ) -> Tuple[str, MessagePayload]:
        """
        "Delete profile" implemented as deactivation:
        - Keeps collaborations/ratings history intact
        - Removes driver from searches and menus until reactivated
        """
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="DRIVER_DELETE_PROFILE_CONFIRM",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )

        if not message_text:
            return "DRIVER_DELETE_PROFILE_CONFIRM", MessagePayload(
                title="SUPPRIMER MON PROFIL",
                body="Souhaitez-vous désactiver votre profil ?\n\n- Vous ne serez plus trouvable par numéro\n- Vos collaborations/évaluations restent enregistrées\n\n1️⃣ Oui, désactiver\n2️⃣ Non, annuler",
                options=[
                    {"number": "1", "text": "Oui, désactiver"},
                    {"number": "2", "text": "Non, annuler"},
                ],
            )

        if message_text == "2":
            return self._handle_driver_view_profile(user, None, "")

        if message_text == "1":
            # Deactivate
            user.is_active = False
            user.save(update_fields=["is_active"])
            # Clear conversation states for cleanliness
            from ..models import ConversationState as CS
            CS.objects.filter(user=user).delete()
            return "END", MessagePayload(body="Votre profil a été désactivé. Tapez /start pour le réactiver.")

        return self._handle_invalid_input(user, state, "Choix invalide")

    def _handle_driver_update_display_name(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str,
    ) -> Tuple[str, MessagePayload]:
        """Update driver's display name."""
        if not hasattr(user, "driver_profile"):
            return self._driver_main_menu_notice(user, "Vous n'avez pas encore de profil.")

        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="DRIVER_UPDATE_DISPLAY_NAME",
                expected_input_type=ConversationState.InputType.SANITIZED_TEXT,
                allowed_values=None,
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE,
            )

        if not message_text:
            return "DRIVER_UPDATE_DISPLAY_NAME", MessagePayload(
                title="MODIFIER MON NOM",
                body="Entrez votre nouveau nom d'affichage (max 100 caractères).\n\nOu tapez 0 pour annuler.",
            )

        if message_text == "0":
            return self._handle_driver_view_profile(user, None, "")

        new_name = message_text.strip()[:100]
        user.driver_profile.display_name = new_name
        user.driver_profile.save(update_fields=["display_name"])

        # Show updated profile
        return self._handle_driver_view_profile(user, None, "")

    def _driver_main_menu_notice(self, user: User, notice: str) -> Tuple[str, MessagePayload]:
        """Return driver main menu with a notice prepended (avoids 'dead-end' messages without navigation)."""
        state = self.conversation_service.get_or_create_state(
            user=user,
            initial_state="DRIVER_MAIN_MENU",
            expected_input_type=ConversationState.InputType.MENU_CHOICE,
            allowed_values=["1", "2", "3", "4"],
        )
        _, payload = self._handle_driver_main_menu(user, state, "")
        payload.body = f"{notice}\n\n{payload.body or ''}".strip()
        return "DRIVER_MAIN_MENU", payload
    
    def _handle_driver_create_profile(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour création profil driver"""
        # Vérifier si profil existe déjà
        if hasattr(user, 'driver_profile'):
            return self._driver_main_menu_notice(user, "Vous avez déjà un profil. Votre identité est verrouillée.")

        # If user is coming from Telegram, we must collect a contact phone number first
        # (WhatsApp provides it implicitly, Telegram does not).
        if user.phone_number.startswith("tg:") and not user.contact_phone_number:
            return self._handle_driver_enter_phone(user, None, "")
        
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="DRIVER_CREATE_PROFILE",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"]
            )
        
        if not message_text:
            payload = MessagePayload(
                title="CRÉATION DE PROFIL",
                body="Pour créer votre profil, vous devez :\n1. Entrer un nom d'affichage (optionnel)\n2. Entrer votre numéro de permis\n3. Verrouiller votre identité\n\nSouhaitez-vous continuer ?",
                options=[
                    {"number": "1", "text": "Oui, continuer"},
                    {"number": "2", "text": "Non, annuler"}
                ]
            )
            return "DRIVER_CREATE_PROFILE", payload
        
        if message_text == "1":
            return self._handle_driver_enter_display_name(user, None, "")
        elif message_text == "2":
            return self._handle_driver_main_menu(user, None, "")
        
        return self._handle_invalid_input(user, state, "Choix invalide")

    def _handle_driver_enter_phone(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Collect driver's phone number (E.164) for Telegram users."""
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="DRIVER_ENTER_PHONE",
                expected_input_type=ConversationState.InputType.PHONE_NUMBER,
                allowed_values=None,
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE
            )

        if not message_text:
            return "DRIVER_ENTER_PHONE", MessagePayload(
                title="NUMÉRO DE TÉLÉPHONE",
                body="Entrez votre numéro de téléphone (format international).\n\nFormat : +221771234567\n\nOu tapez 0 pour annuler.",
            )

        if message_text == "0":
            return self._handle_driver_main_menu(user, None, "")

        # Persist it on the user (used by owners to verify/search drivers)
        user.contact_phone_number = message_text.strip()
        user.save(update_fields=["contact_phone_number"])

        # Now proceed with profile creation
        return self._handle_driver_create_profile(user, None, "")
    
    def _handle_driver_enter_display_name(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour saisie nom d'affichage"""
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="DRIVER_ENTER_DISPLAY_NAME",
                expected_input_type=ConversationState.InputType.SANITIZED_TEXT,
                allowed_values=None
            )
        
        if not message_text:
            payload = MessagePayload(
                title="NOM D'AFFICHAGE",
                body="Entrez votre nom d'affichage (optionnel, max 100 caractères).\n\nOu tapez 0 pour passer cette étape."
            )
            return "DRIVER_ENTER_DISPLAY_NAME", payload
        
        if message_text == "0":
            display_name = None
        else:
            display_name = message_text[:100]  # Limiter à 100 caractères
        
        # Sauvegarder dans temp_data
        state = self.conversation_service.set_temp_data(state, "display_name", display_name)

        # IMPORTANT: Transition the persisted state to DRIVER_ENTER_PERMIT.
        # Otherwise the next message is still validated/handled as DRIVER_ENTER_DISPLAY_NAME,
        # which causes the "permit input" loop you're seeing on Telegram.
        new_state = self._execute_transition(
            user=user,
            current_state=state,
            next_state_name="DRIVER_ENTER_PERMIT",
            expected_input_type=ConversationState.InputType.PERMIT_NUMBER,
            allowed_values=None,
            temp_data=state.temp_data,
        )

        # Passer à l'étape suivante
        return self._handle_driver_enter_permit(user, new_state, "")
    
    def _handle_driver_enter_permit(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour saisie permis - Handler court"""
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="DRIVER_ENTER_PERMIT",
                expected_input_type=ConversationState.InputType.PERMIT_NUMBER,
                allowed_values=None
            )
        elif state.current_state != "DRIVER_ENTER_PERMIT" or state.expected_input_type != ConversationState.InputType.PERMIT_NUMBER:
            # Ensure persisted state matches the permit step (prevents looping)
            state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="DRIVER_ENTER_PERMIT",
                expected_input_type=ConversationState.InputType.PERMIT_NUMBER,
                allowed_values=None,
                temp_data=state.temp_data,
            )
        
        if not message_text:
            return self._show_permit_input_prompt()
        
        return self._process_permit_input(user, state, message_text)
    
    def _show_permit_input_prompt(self) -> Tuple[str, MessagePayload]:
        """Affiche le prompt de saisie permis"""
        return "DRIVER_ENTER_PERMIT", MessagePayload(
            title="NUMÉRO DE PERMIS",
            body="Entrez votre numéro de permis.\n\nFormat : 8 à 15 caractères alphanumériques\n\nImportant : Une fois verrouillé, votre identité ne pourra plus être modifiée.",
            options=[
                {"number": "3", "text": "Retour"},
                {"number": "0", "text": "Annuler"}
            ]
        )
    
    def _process_permit_input(
        self,
        user: User,
        state: ConversationState,
        permit_text: str
    ) -> Tuple[str, MessagePayload]:
        """Traite la saisie permis - logique extraite"""
        # Navigation
        if permit_text == "0":
            return self._handle_driver_main_menu(user, None, "")
        if permit_text == "3":
            # Back to display name step
            back_state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="DRIVER_ENTER_DISPLAY_NAME",
                expected_input_type=ConversationState.InputType.SANITIZED_TEXT,
                allowed_values=None,
                temp_data=state.temp_data,
            )
            return self._handle_driver_enter_display_name(user, back_state, "")

        # Normaliser et valider
        is_valid, normalized, error = self.identity_service.normalize_and_validate_permit(permit_text)
        
        if not is_valid:
            return self._handle_invalid_input(user, state, error)
        
        # Vérifier si le permis existe déjà
        if self.identity_service.check_permit_exists(normalized):
            return "DRIVER_ENTER_PERMIT", MessagePayload(
                body="Ce numéro de permis est déjà associé à un profil."
            )
        
        # Sauvegarder dans temp_data
        state = self.conversation_service.set_temp_data(state, "permit_normalized", normalized)
        
        # Transition vers confirmation - une seule transition atomique
        masked = self.identity_service.get_display_permit_for_confirmation(normalized)
        new_state = self._execute_transition(
            user, state, "DRIVER_CONFIRM_PERMIT",
            ConversationState.InputType.MENU_CHOICE,
            allowed_values=["1", "2"],
            temp_data={"permit_normalized": normalized}
        )
        
        return "DRIVER_CONFIRM_PERMIT", MessagePayload(
            title="CONFIRMATION DU PERMIS",
            body=f"Permis masqué : {masked}\n\nConfirmez-vous ce numéro de permis ?",
            options=[
                {"number": "1", "text": "Oui, confirmer"},
                {"number": "2", "text": "Non, modifier"}
            ]
        )
    
    def _handle_driver_confirm_permit(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour confirmation permis"""
        if message_text == "1":
            # Ensure user is marked as DRIVER when completing registration
            if user.role != User.Role.DRIVER:
                user.role = User.Role.DRIVER
                user.save(update_fields=["role"])
            # Confirmer - créer le profil
            permit_normalized = self.conversation_service.get_temp_data(state, "permit_normalized")
            display_name = self.conversation_service.get_temp_data(state, "display_name")
            
            if not permit_normalized:
                return self._handle_invalid_input(user, state, "Permis manquant")
            
            # Créer le profil via IdentityService
            success, driver_profile, error = self.identity_service.create_driver_profile(
                user, permit_normalized, display_name
            )
            
            if not success:
                payload = MessagePayload(
                    body=f"Erreur lors de la création du profil : {error}"
                )
                return "DRIVER_MAIN_MENU", payload
            
            # Passer à la confirmation de verrouillage (persist the state transition!)
            lock_state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="DRIVER_LOCK_IDENTITY",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"],
                temp_data={"permit_normalized": permit_normalized, "display_name": display_name},
            )
            return self._handle_driver_lock_identity(user, lock_state, "")
        
        elif message_text == "2":
            # Modifier - retour à la saisie
            return self._handle_driver_enter_permit(user, state, "")
        
        return self._handle_invalid_input(user, state, "Choix invalide")
    
    def _handle_driver_lock_identity(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour verrouillage identité - Handler court"""
        if not hasattr(user, 'driver_profile'):
            return self._handle_driver_main_menu(user, None, "")
        
        driver_profile = user.driver_profile
        
        if driver_profile.identity_locked:
            return self._driver_main_menu_notice(user, "Votre identité est déjà verrouillée.")
        
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="DRIVER_LOCK_IDENTITY",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"],
                behavior_on_invalid_input=ConversationState.InvalidInputBehavior.RETRY_SAME_STATE
            )
        elif state.current_state != "DRIVER_LOCK_IDENTITY" or state.expected_input_type != ConversationState.InputType.MENU_CHOICE:
            # Ensure we don't keep routing inputs to DRIVER_CONFIRM_PERMIT (causes duplicate profile creation)
            state = self._execute_transition(
                user=user,
                current_state=state,
                next_state_name="DRIVER_LOCK_IDENTITY",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2"],
                temp_data=state.temp_data,
            )
        
        if not message_text:
            return self._show_lock_identity_prompt(state)
        
        return self._process_lock_identity_confirmation(user, state, driver_profile, message_text)
    
    def _show_lock_identity_prompt(self, state: ConversationState) -> Tuple[str, MessagePayload]:
        """Affiche le prompt de verrouillage"""
        permit_normalized = self.conversation_service.get_temp_data(state, "permit_normalized")
        masked = self.identity_service.get_display_permit_for_confirmation(permit_normalized) if permit_normalized else "****"
        
        return "DRIVER_LOCK_IDENTITY", MessagePayload(
            title="VERROUILLAGE D'IDENTITÉ",
            body=f"Permis : {masked}\n\nAttention : Le verrouillage est irréversible. Une fois verrouillé, vous pourrez recevoir des évaluations.\n\nSouhaitez-vous verrouiller votre identité maintenant ?",
            options=[
                {"number": "1", "text": "Oui, verrouiller"},
                {"number": "2", "text": "Non, plus tard"}
            ]
        )
    
    def _process_lock_identity_confirmation(
        self,
        user: User,
        state: ConversationState,
        driver_profile,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Traite la confirmation de verrouillage - logique extraite"""
        if message_text.upper() in ["O", "OUI", "Y", "YES", "1"]:
            success, error = self.identity_service.lock_identity(driver_profile)
            
            if success:
                self.conversation_service.clear_temp_data(state)
                new_state = self._execute_transition(
                    user, state, "DRIVER_MAIN_MENU",
                    ConversationState.InputType.MENU_CHOICE,
                    allowed_values=["1", "2", "3", "4"]
                )
                return "DRIVER_MAIN_MENU", MessagePayload(
                    body="Votre identité a été verrouillée avec succès. Vous pouvez maintenant recevoir des évaluations."
                )
            else:
                return "DRIVER_LOCK_IDENTITY", MessagePayload(
                    body=f"Erreur lors du verrouillage : {error}"
                )
        
        elif message_text.upper() in ["N", "NON", "NO", "2"]:
            new_state = self._execute_transition(
                user, state, "DRIVER_MAIN_MENU",
                ConversationState.InputType.MENU_CHOICE,
                allowed_values=["1", "2", "3", "4"]
            )
            return "DRIVER_MAIN_MENU", MessagePayload(
                body="Vous pourrez verrouiller votre identité plus tard depuis le menu principal."
            )
        
        return self._handle_invalid_input(user, state, "Réponse invalide")
    
    def _handle_driver_view_collaborations(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour voir collaborations driver - Orchestrateur mince"""
        # Récupérer toutes les collaborations du driver
        from ..models import Collaboration
        collaborations = Collaboration.objects.filter(
            driver=user
        ).select_related('owner').order_by('-declared_at')[:10]
        
        if not collaborations:
            return "DRIVER_MAIN_MENU", MessagePayload(
                body="Aucune collaboration déclarée."
            )
        
        # Construire le résumé
        body_lines = ["MES COLLABORATIONS", ""]
        
        for idx, collab in enumerate(collaborations, 1):
            state_labels = {
                Collaboration.State.DECLARED: "Déclarée",
                Collaboration.State.PENDING_CONFIRMATION: "En attente confirmation",
                Collaboration.State.CONFIRMED: "Confirmée",
                Collaboration.State.ELIGIBLE_FOR_RATING: "Éligible évaluation",
                Collaboration.State.RATED: "Évaluée"
            }
            
            body_lines.append(f"{idx}. Propriétaire : {collab.owner.phone_number}")
            body_lines.append(f"   État : {state_labels.get(collab.state, collab.state)}")
            if collab.start_date and collab.end_date:
                body_lines.append(f"   Période : {collab.start_date} au {collab.end_date}")
            if collab.duration_days:
                body_lines.append(f"   Durée : {collab.duration_days} jours")
            if hasattr(collab, 'rating') and collab.rating.is_published:
                median = collab.rating.get_median_score()
                body_lines.append(f"   Évaluation : {median:.1f}/5")
            body_lines.append("")
        
        return "DRIVER_MAIN_MENU", MessagePayload(
            body="\n".join(body_lines)
        )
    
    def _handle_driver_view_reputation(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour voir réputation - Orchestrateur mince"""
        if not hasattr(user, 'driver_profile'):
            return "DRIVER_MAIN_MENU", MessagePayload(
                body="Vous devez créer un profil pour voir votre réputation."
            )
        
        driver_profile = user.driver_profile
        
        # Récupérer la réputation (utilise le cache, ne recalcule pas)
        reputation = self.reputation_service.get_reputation(driver_profile, force_recompute=False)
        
        # Construire le résumé
        state_labels = {
            DriverProfile.ReputationState.NEW: "Nouveau",
            DriverProfile.ReputationState.ESTABLISHED: "Établi",
            DriverProfile.ReputationState.CONFIRMED: "Confirmé",
            DriverProfile.ReputationState.REBUILDING: "Reconstruction"
        }
        
        body_lines = [
            f"Score médian : {reputation['score_median']}/5",
            f"État : {state_labels.get(reputation['state'], reputation['state'])}",
            f"Collaborations : {reputation['collaborations_count']}",
            f"Évaluations : {reputation['ratings_count']}"
        ]
        
        return "DRIVER_MAIN_MENU", MessagePayload(
            title="MA RÉPUTATION",
            body="\n".join(body_lines)
        )
    
    def _handle_driver_confirm_collaboration(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour confirmation collaboration - Orchestrateur mince"""
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="DRIVER_CONFIRM_COLLABORATION",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=[]
            )
        
        # Récupérer les collaborations en attente via CollaborationService
        pending = self.collaboration_service.get_pending_confirmations_for_driver(user)
        
        if not message_text:
            if not pending:
                return "DRIVER_MAIN_MENU", MessagePayload(
                    body="Aucune collaboration en attente de confirmation."
                )
            
            # Construire le menu
            options = []
            for idx, collab in enumerate(pending[:9], 1):
                owner_phone = collab.owner.phone_number
                options.append({
                    "number": str(idx),
                    "text": f"{owner_phone} ({collab.duration_days} jours)"
                })
            options.append({"number": "0", "text": "Retour"})
            
            # Sauvegarder les IDs
            collab_ids = [str(c.id) for c in pending[:9]]
            state = self.conversation_service.set_temp_data(state, "collaboration_ids", collab_ids)
            state = self.conversation_service.update_state(
                state,
                allowed_values=[str(i) for i in range(1, len(pending[:9]) + 1)] + ["0"]
            )
            
            return "DRIVER_CONFIRM_COLLABORATION", MessagePayload(
                title="CONFIRMER UNE COLLABORATION",
                body="Choisissez la collaboration à confirmer :",
                options=options
            )
        
        if message_text == "0":
            return self._handle_driver_main_menu(user, None, "")
        
        # Récupérer la collaboration sélectionnée
        collab_ids = self.conversation_service.get_temp_data(state, "collaboration_ids", [])
        try:
            idx = int(message_text) - 1
            if 0 <= idx < len(collab_ids):
                collab_id = int(collab_ids[idx])
                collaboration = self.collaboration_service.get_collaboration_by_id(collab_id)
                if collaboration:
                    # Sauvegarder et transition vers détail
                    state = self.conversation_service.set_temp_data(state, "selected_collaboration_id", collab_id)
                    new_state = self._execute_transition(
                        user, state, "DRIVER_CONFIRM_COLLABORATION_DETAIL",
                        ConversationState.InputType.CONFIRMATION,
                        temp_data={"selected_collaboration_id": collab_id}
                    )
                    return self._show_collaboration_detail(collaboration)
        except (ValueError, IndexError):
            return self._handle_invalid_input(user, state, "Choix invalide")
        
        return self._handle_invalid_input(user, state, "Choix invalide")
    
    def _show_collaboration_detail(self, collaboration) -> Tuple[str, MessagePayload]:
        """Affiche le détail d'une collaboration"""
        summary = self.collaboration_service.get_collaboration_summary(collaboration)
        body_lines = [
            f"Propriétaire : {summary['owner_phone']}",
            f"Période : {summary['start_date']} au {summary['end_date']}",
            f"Durée : {summary['duration_days']} jours"
        ]
        return "DRIVER_CONFIRM_COLLABORATION_DETAIL", MessagePayload(
            title="DÉTAIL DE LA COLLABORATION",
            body="\n".join(body_lines) + "\n\nConfirmez-vous cette collaboration ?",
            options=[
                {"number": "1", "text": "Oui, confirmer"},
                {"number": "2", "text": "Non, refuser"}
            ]
        )
    
    def _handle_driver_confirm_collaboration_detail(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour détail confirmation collaboration - Orchestrateur mince"""
        collab_id = self.conversation_service.get_temp_data(state, "selected_collaboration_id")
        if not collab_id:
            return self._driver_main_menu_notice(user, "Collaboration introuvable.")
        
        collaboration = self.collaboration_service.get_collaboration_by_id(collab_id)
        if not collaboration:
            return self._driver_main_menu_notice(user, "Collaboration introuvable.")
        
        if not message_text:
            return self._show_collaboration_detail(collaboration)
        
        if message_text.upper() in ["O", "OUI", "Y", "YES", "1"]:
            # Confirmer via CollaborationService
            success, error = self.collaboration_service.confirm_collaboration(user, collaboration)
            if success:
                return "DRIVER_MAIN_MENU", MessagePayload(
                    body="Collaboration confirmée avec succès."
                )
            else:
                return "DRIVER_CONFIRM_COLLABORATION_DETAIL", MessagePayload(
                    body=f"Erreur : {error}"
                )
        elif message_text.upper() in ["N", "NON", "NO", "2"]:
            return "DRIVER_MAIN_MENU", MessagePayload(
                body="Collaboration non confirmée."
            )
        
        return self._handle_invalid_input(user, state, "Réponse invalide")
    
    def _handle_driver_respond_to_review(
        self,
        user: User,
        state: Optional[ConversationState],
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour répondre à une review - Orchestrateur mince"""
        # Récupérer les ratings avec réponse possible
        from ..models import Rating
        ratings = Rating.objects.filter(
            collaboration__driver=user,
            is_published=True,
            driver_response__isnull=True
        ).select_related('collaboration').order_by('-published_at')[:9]
        
        if not state:
            state = self.conversation_service.get_or_create_state(
                user=user,
                initial_state="DRIVER_RESPOND_TO_REVIEW",
                expected_input_type=ConversationState.InputType.MENU_CHOICE,
                allowed_values=[]
            )
        
        if not message_text:
            if not ratings:
                return "DRIVER_MAIN_MENU", MessagePayload(
                    body="Aucune évaluation en attente de réponse."
                )
            
            options = []
            for idx, rating in enumerate(ratings, 1):
                owner_phone = rating.collaboration.owner.phone_number
                median = rating.get_median_score()
                options.append({
                    "number": str(idx),
                    "text": f"{owner_phone} ({median:.1f}/5)"
                })
            options.append({"number": "0", "text": "Retour"})
            
            rating_ids = [str(r.id) for r in ratings]
            state = self.conversation_service.set_temp_data(state, "rating_ids", rating_ids)
            state = self.conversation_service.update_state(
                state,
                allowed_values=[str(i) for i in range(1, len(ratings) + 1)] + ["0"]
            )
            
            return "DRIVER_RESPOND_TO_REVIEW", MessagePayload(
                title="RÉPONDRE À UNE ÉVALUATION",
                body="Choisissez l'évaluation à laquelle répondre :",
                options=options
            )
        
        if message_text == "0":
            return self._handle_driver_main_menu(user, None, "")
        
        rating_ids = self.conversation_service.get_temp_data(state, "rating_ids", [])
        try:
            idx = int(message_text) - 1
            if 0 <= idx < len(rating_ids):
                rating_id = int(rating_ids[idx])
                rating = self.rating_service.get_rating_by_id(rating_id)
                if rating:
                    state = self.conversation_service.set_temp_data(state, "selected_rating_id", rating_id)
                    new_state = self._execute_transition(
                        user, state, "DRIVER_ENTER_RESPONSE",
                        ConversationState.InputType.SANITIZED_TEXT,
                        temp_data={"selected_rating_id": rating_id}
                    )
                    return "DRIVER_ENTER_RESPONSE", MessagePayload(
                        title="VOTRE RÉPONSE",
                        body="Entrez votre réponse à cette évaluation (max 500 caractères) :"
                    )
        except (ValueError, IndexError):
            return self._handle_invalid_input(user, state, "Choix invalide")
        
        return self._handle_invalid_input(user, state, "Choix invalide")
    
    def _handle_driver_enter_response(
        self,
        user: User,
        state: ConversationState,
        message_text: str
    ) -> Tuple[str, MessagePayload]:
        """Handler pour saisie réponse - Orchestrateur mince"""
        if not message_text:
            return "DRIVER_ENTER_RESPONSE", MessagePayload(
                title="VOTRE RÉPONSE",
                body="Entrez votre réponse à cette évaluation (max 500 caractères) :"
            )
        
        rating_id = self.conversation_service.get_temp_data(state, "selected_rating_id")
        if not rating_id:
            return self._driver_main_menu_notice(user, "Évaluation introuvable.")
        
        rating = self.rating_service.get_rating_by_id(rating_id)
        if not rating:
            return self._driver_main_menu_notice(user, "Évaluation introuvable.")
        
        # Vérifier que c'est bien le driver de cette collaboration
        if rating.collaboration.driver != user:
            return self._driver_main_menu_notice(user, "Vous n'êtes pas autorisé à répondre à cette évaluation.")
        
        # Enregistrer la réponse
        from django.utils import timezone
        rating.driver_response = message_text
        rating.driver_response_at = timezone.now()
        rating.save(update_fields=['driver_response', 'driver_response_at'])
        
        # Traiter la réponse (recalcul réputation)
        from ..services.event_handler import EventHandlerService
        EventHandlerService.handle_driver_response(rating)
        
        return "DRIVER_MAIN_MENU", MessagePayload(
            body="Votre réponse a été enregistrée avec succès."
        )

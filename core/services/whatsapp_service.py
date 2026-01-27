"""
Service pour la gestion des messages WhatsApp via l'API Meta
"""
import json
import logging
from typing import Dict, Optional, Tuple, List
import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class WhatsAppService:
    """Service pour envoyer et recevoir des messages WhatsApp"""
    
    # Base URL de l'API WhatsApp Cloud API
    API_BASE_URL = "https://graph.facebook.com/v18.0"
    
    @staticmethod
    def get_api_url(endpoint: str = "") -> str:
        """Construit l'URL complète de l'API"""
        phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID
        if not phone_number_id:
            raise ValueError("WHATSAPP_PHONE_NUMBER_ID non configuré")
        
        if endpoint:
            return f"{WhatsAppService.API_BASE_URL}/{phone_number_id}/{endpoint}"
        return f"{WhatsAppService.API_BASE_URL}/{phone_number_id}"
    
    @staticmethod
    def get_headers() -> Dict[str, str]:
        """Retourne les headers pour les requêtes API"""
        token = settings.WHATSAPP_API_TOKEN
        if not token:
            raise ValueError("WHATSAPP_API_TOKEN non configuré")
        
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    
    @staticmethod
    def send_text_message(
        phone_number: str,
        message_text: str
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """
        Envoie un message texte via WhatsApp
        
        Args:
            phone_number: Numéro au format E.164 (ex: +221771234567)
            message_text: Texte du message à envoyer
        
        Returns:
            Tuple (success, error_message, response_data)
        """
        try:
            url = WhatsAppService.get_api_url("messages")
            headers = WhatsAppService.get_headers()
            
            # Ensure phone number is in E.164 format (starts with +)
            if not phone_number.startswith("+"):
                phone_number = "+" + phone_number
                logger.warning(f"Phone number missing + prefix, corrected to: {phone_number}")
            
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": phone_number,
                "type": "text",
                "text": {
                    "preview_url": False,  # Désactiver preview pour éviter problèmes
                    "body": message_text
                }
            }
            
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            
            if response.status_code == 200:
                response_data = response.json()
                # Log the full response to debug delivery issues
                logger.info(
                    f"Message envoyé avec succès à {phone_number} | "
                    f"message_id={response_data.get('messages', [{}])[0].get('id', 'unknown')}"
                )
                return True, None, response_data
            else:
                error_data = response.json() if response.content else {}
                error_info = error_data.get("error", {})
                error_msg = error_info.get("message", "Erreur inconnue")
                error_code = error_info.get("code", "unknown")
                error_type = error_info.get("type", "unknown")
                logger.error(
                    f"Erreur envoi message à {phone_number} | "
                    f"code={error_code} | type={error_type} | message={error_msg} | "
                    f"full_response={error_data}"
                )
                return False, error_msg, error_data
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Exception lors de l'envoi du message: {str(e)}")
            return False, f"Erreur réseau: {str(e)}", None
        except Exception as e:
            logger.error(f"Erreur inattendue lors de l'envoi: {str(e)}")
            return False, f"Erreur inattendue: {str(e)}", None
    
    @staticmethod
    def format_menu_message(
        title: str,
        options: List[Tuple[str, str]],
        footer: Optional[str] = None
    ) -> str:
        """
        Formate un message avec menu numéroté
        
        Args:
            title: Titre du message
            options: Liste de tuples (numéro, texte) pour les options
            footer: Texte optionnel en bas du message
        
        Returns:
            Message formaté avec emojis numériques
        """
        lines = [title, ""]  # Titre + ligne vide
        
        # Ajouter les options avec emojis numériques
        emoji_map = {
            "1": "1️⃣", "2": "2️⃣", "3": "3️⃣", "4": "4️⃣", "5": "5️⃣",
            "6": "6️⃣", "7": "7️⃣", "8": "8️⃣", "9": "9️⃣"
        }
        
        for num, text in options:
            emoji = emoji_map.get(num, f"{num}.")
            lines.append(f"{emoji} {text}")
        
        if footer:
            lines.append("")
            lines.append(footer)
        
        return "\n".join(lines)
    
    @staticmethod
    def format_simple_message(
        title: Optional[str],
        content: str,
        footer: Optional[str] = None
    ) -> str:
        """
        Formate un message simple sans menu
        
        Args:
            title: Titre optionnel
            content: Contenu principal
            footer: Texte optionnel en bas
        
        Returns:
            Message formaté
        """
        lines = []
        
        if title:
            lines.append(title)
            lines.append("")
        
        lines.append(content)
        
        if footer:
            lines.append("")
            lines.append(footer)
        
        return "\n".join(lines)
    
    @staticmethod
    def format_error_message(
        error_text: str,
        retry_instruction: Optional[str] = None
    ) -> str:
        """
        Formate un message d'erreur
        
        Args:
            error_text: Texte de l'erreur
            retry_instruction: Instruction pour réessayer
        
        Returns:
            Message d'erreur formaté
        """
        lines = ["❌ " + error_text]
        
        if retry_instruction:
            lines.append("")
            lines.append(retry_instruction)
        
        return "\n".join(lines)
    
    @staticmethod
    def format_success_message(
        success_text: str,
        next_action: Optional[str] = None
    ) -> str:
        """
        Formate un message de succès
        
        Args:
            success_text: Texte de succès
            next_action: Action suivante optionnelle
        
        Returns:
            Message de succès formaté
        """
        lines = ["✅ " + success_text]
        
        if next_action:
            lines.append("")
            lines.append(next_action)
        
        return "\n".join(lines)
    
    @staticmethod
    def parse_webhook_payload(payload: Dict) -> Dict:
        """
        Parse le payload du webhook WhatsApp de manière défensive
        
        Gère tous les types de webhooks:
        - Messages texte
        - Messages non-texte (images, audio, stickers, etc.)
        - Statuts de message (delivered, read, sent)
        - Erreurs de parsing
        
        Returns:
            Dict avec structure standardisée:
            {
                "type": "text_message" | "non_text_message" | "status_update" | "error" | "unknown",
                "phone_number": str | None,
                "message_id": str | None,
                "message_text": str | None,
                "timestamp": str | None,
                "message_type": str | None,
                "error_message": str | None  # Si type == "error" ou "non_text_message"
            }
        """
        result = {
            "type": "unknown",
            "phone_number": None,
            "message_id": None,
            "message_text": None,
            "timestamp": None,
            "message_type": None,
            "error_message": None
        }
        
        try:
            # Try to find messages in the payload - be flexible with structure
            # First try standard webhook format
            entries = payload.get("entry", [])
            if not entries:
                # Try direct message format (for testing)
                messages = payload.get("messages", [])
                if messages:
                    # Direct message format - process it
                    message = messages[0]
                    phone_number = message.get("from")
                    message_id = message.get("id")
                    timestamp = message.get("timestamp")
                    message_type = message.get("type")
                    
                    if phone_number:
                        result["phone_number"] = phone_number
                        result["message_id"] = message_id
                        result["timestamp"] = timestamp
                        result["message_type"] = message_type
                        
                        if message_type == "text":
                            text_obj = message.get("text", {})
                            message_text = text_obj.get("body", "")
                            result["type"] = "text_message"
                            result["message_text"] = message_text.strip()
                        else:
                            result["type"] = "non_text_message"
                            result["error_message"] = f"Non-text message type '{message_type}' not supported. Please send a text message."
                        return result
                
                result["type"] = "unknown"
                result["error_message"] = "No entries or messages in webhook"
                logger.warning("Aucune entrée dans le webhook")
                return result
            
            # Prendre la première entrée
            entry = entries[0]
            changes = entry.get("changes", [])
            if not changes:
                result["type"] = "unknown"
                result["error_message"] = "No changes in entry"
                logger.warning("Aucun changement dans l'entrée")
                return result
            
            # Prendre le premier changement
            change = changes[0]
            value = change.get("value", {})
            
            # Check for messages - if we have messages, it's a user message (even if structure incomplete)
            messages = value.get("messages", [])
            if messages:
                # We have messages - this is a user message, process it
                message = messages[0]
                phone_number = message.get("from")
                message_id = message.get("id")
                timestamp = message.get("timestamp")
                message_type = message.get("type")
                
                if phone_number:
                    result["phone_number"] = phone_number
                    result["message_id"] = message_id
                    result["timestamp"] = timestamp
                    result["message_type"] = message_type
                    
                    if message_type == "text":
                        text_obj = message.get("text", {})
                        message_text = text_obj.get("body", "")
                        result["type"] = "text_message"
                        result["message_text"] = message_text.strip()
                    else:
                        result["type"] = "non_text_message"
                        result["error_message"] = f"Non-text message type '{message_type}' not supported. Please send a text message."
                    return result
            
            # Vérifier les statuts (delivered, read, sent, failed)
            statuses = value.get("statuses", [])
            if statuses:
                result["type"] = "status_update"
                status = statuses[0]
                result["phone_number"] = status.get("recipient_id")
                result["message_id"] = status.get("id")
                result["timestamp"] = status.get("timestamp")
                status_value = status.get("status")
                
                # Log detailed status information, especially for failures
                if status_value == "failed":
                    error_info = status.get("errors", [])
                    error_details = error_info[0] if error_info else {}
                    error_code = error_details.get("code", "unknown")
                    error_title = error_details.get("title", "unknown")
                    error_message = error_details.get("message", "unknown")
                    logger.error(
                        f"Message delivery FAILED | "
                        f"message_id={status.get('id')} | "
                        f"recipient={status.get('recipient_id')} | "
                        f"error_code={error_code} | "
                        f"error_title={error_title} | "
                        f"error_message={error_message} | "
                        f"full_status={status}"
                    )
                else:
                    logger.info(f"Reçu un statut de message: {status_value} | message_id={status.get('id')}")
                return result
            
            # If we get here, we already processed messages above, so this shouldn't happen
            # But keep for safety
            messages = value.get("messages", [])
            if not messages:
                result["type"] = "unknown"
                result["error_message"] = "No messages or statuses in webhook"
                return result
            
            # Prendre le premier message
            message = messages[0]
            
            # Extraire les informations communes
            phone_number = message.get("from")
            message_id = message.get("id")
            timestamp = message.get("timestamp")
            message_type = message.get("type")
            
            if not phone_number:
                phone_number = "unknown"
                logger.warning("Numéro de téléphone manquant dans le message")
            
            result["phone_number"] = phone_number
            result["message_id"] = message_id
            result["timestamp"] = timestamp
            result["message_type"] = message_type
            
            # Extraire le texte selon le type
            if message_type == "text":
                text_obj = message.get("text", {})
                message_text = text_obj.get("body", "")
                result["type"] = "text_message"
                result["message_text"] = message_text.strip()
            elif message_type == "interactive":
                # Boutons interactifs
                interactive = message.get("interactive", {})
                button_reply = interactive.get("button_reply")
                list_reply = interactive.get("list_reply")
                
                if button_reply:
                    result["type"] = "text_message"
                    result["message_text"] = button_reply.get("id", "").strip()
                elif list_reply:
                    result["type"] = "text_message"
                    result["message_text"] = list_reply.get("id", "").strip()
                else:
                    # Interactive message without reply - treat as empty text
                    result["type"] = "text_message"
                    result["message_text"] = ""
            else:
                # Message non-texte (image, audio, video, document, sticker, location, etc.)
                # These are controlled inputs, not errors
                result["type"] = "non_text_message"
                result["error_message"] = f"Non-text message type '{message_type}' not supported. Please send a text message."
                logger.info(f"Type de message non-texte reçu: {message_type}")
            
            return result
            
        except Exception as e:
            logger.error(f"Erreur lors du parsing du webhook: {str(e)}")
            result["type"] = "error"
            result["error_message"] = f"Webhook parsing error: {str(e)}"
            return result
    
    @staticmethod
    def validate_phone_number(phone_number: str) -> bool:
        """
        Valide le format du numéro de téléphone selon la norme E.164 uniquement
        
        E.164 spécification:
        - Commence par +
        - Suivi de 1 à 15 chiffres
        - Pas de validation spécifique par pays
        
        Args:
            phone_number: Numéro à valider
        
        Returns:
            True si conforme E.164, False sinon
        """
        if not phone_number:
            return False
        
        # Format E.164: +[1-15 chiffres]
        if not phone_number.startswith("+"):
            return False
        
        # Vérifier que le reste est numérique uniquement
        rest = phone_number[1:]
        if not rest.isdigit():
            return False
        
        # E.164: longueur totale 1-15 chiffres après le +
        if len(rest) < 1 or len(rest) > 15:
            return False
        
        return True
    
    @staticmethod
    def send_menu_message(
        phone_number: str,
        title: str,
        options: List[Tuple[str, str]],
        footer: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Envoie un message avec menu numéroté
        
        Args:
            phone_number: Numéro au format E.164
            title: Titre du message
            options: Liste de tuples (numéro, texte)
            footer: Texte optionnel en bas
        
        Returns:
            Tuple (success, error_message)
        """
        formatted_message = WhatsAppService.format_menu_message(
            title, options, footer
        )
        success, error_msg, _ = WhatsAppService.send_text_message(
            phone_number, formatted_message
        )
        return success, error_msg
    
    @staticmethod
    def send_simple_message(
        phone_number: str,
        title: Optional[str],
        content: str,
        footer: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Envoie un message simple sans menu
        
        Args:
            phone_number: Numéro au format E.164
            title: Titre optionnel
            content: Contenu principal
            footer: Texte optionnel en bas
        
        Returns:
            Tuple (success, error_message)
        """
        formatted_message = WhatsAppService.format_simple_message(
            title, content, footer
        )
        success, error_msg, _ = WhatsAppService.send_text_message(
            phone_number, formatted_message
        )
        return success, error_msg
    
    @staticmethod
    def send_error_message(
        phone_number: str,
        error_text: str,
        retry_instruction: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Envoie un message d'erreur
        
        Args:
            phone_number: Numéro au format E.164
            error_text: Texte de l'erreur
            retry_instruction: Instruction pour réessayer
        
        Returns:
            Tuple (success, error_message)
        """
        formatted_message = WhatsAppService.format_error_message(
            error_text, retry_instruction
        )
        success, error_msg, _ = WhatsAppService.send_text_message(
            phone_number, formatted_message
        )
        return success, error_msg
    
    @staticmethod
    def send_success_message(
        phone_number: str,
        success_text: str,
        next_action: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Envoie un message de succès
        
        Args:
            phone_number: Numéro au format E.164
            success_text: Texte de succès
            next_action: Action suivante optionnelle
        
        Returns:
            Tuple (success, error_message)
        """
        formatted_message = WhatsAppService.format_success_message(
            success_text, next_action
        )
        success, error_msg, _ = WhatsAppService.send_text_message(
            phone_number, formatted_message
        )
        return success, error_msg

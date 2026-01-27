"""
Views for WhatsApp bot
"""
import json
import logging
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.utils import timezone
from core.services.whatsapp_service import WhatsAppService
from core.services.telegram_service import TelegramService
from core.handlers.conversation_handler import ConversationHandler

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["GET", "POST", "HEAD"])
def whatsapp_verify(request):
    """
    Webhook verification endpoint for WhatsApp
    """
    # Meta sends HEAD before GET - both must return 200
    if request.method == 'HEAD':
        return HttpResponse(status=200)
    
    if request.method == 'GET':
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')
        
        # Byte-perfect verification - DO NOT modify challenge or token
        if mode == 'subscribe' and token == settings.WHATSAPP_VERIFY_TOKEN:
            # Return challenge EXACTLY as received - no string conversion, no strip, nothing
            return HttpResponse(challenge, status=200, content_type='text/plain')
        
        return HttpResponse(status=403)
    
    return HttpResponse(status=405)


@csrf_exempt
@require_http_methods(["GET", "POST", "HEAD"])
def whatsapp_webhook(request):
    """
    Webhook endpoint for WhatsApp
    - GET/HEAD: Handles webhook verification from Meta
    - POST: Handles incoming WhatsApp messages
    
    Gestion défensive des erreurs:
    - Try/except au niveau webhook uniquement
    - Pas de logique métier dans la gestion d'erreurs
    - Pas de mutation d'état sur erreur
    - Messages d'erreur clairs pour l'utilisateur
    - Logging structuré pour debugging
    """
    # Handle GET/HEAD request for webhook verification (Meta sends this to verify the webhook)
    # Meta sends HEAD before GET - both must return 200
    if request.method == 'HEAD':
        return HttpResponse(status=200)
    
    if request.method == 'GET':
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')
        
        # Byte-perfect verification - DO NOT modify challenge or token
        # Meta does exact string comparison
        if mode == 'subscribe' and token == settings.WHATSAPP_VERIFY_TOKEN:
            # Return challenge EXACTLY as received - no string conversion, no strip, nothing
            # Meta requires byte-perfect match
            return HttpResponse(challenge, status=200, content_type='text/plain')
        
        return HttpResponse(status=403)
    
    # Handle POST request for incoming messages
    try:
        # Log incoming POST request
        logger.info("WEBHOOK_POST | received POST request | body_size=%d", len(request.body))
        
        # Parser le payload JSON
        try:
            payload = json.loads(request.body)
            logger.debug("WEBHOOK_POST | payload_keys=%s", list(payload.keys()) if isinstance(payload, dict) else "not_dict")
        except json.JSONDecodeError as e:
            logger.error(
                "WEBHOOK_ERROR | type=json_decode | error=%s | body_preview=%s",
                str(e),
                request.body[:200] if request.body else "empty"
            )
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON payload'}, status=400)
        
        # Parser le message avec le service WhatsApp (retourne toujours un dict)
        try:
            message_data = WhatsAppService.parse_webhook_payload(payload)
        except Exception as e:
            logger.error(
                "WEBHOOK_ERROR | type=parse_payload | error=%s | payload_keys=%s",
                str(e),
                list(payload.keys()) if isinstance(payload, dict) else "not_dict",
                exc_info=True
            )
            return JsonResponse({'status': 'error', 'message': 'Failed to parse webhook payload'}, status=400)
        
        webhook_type = message_data.get("type")
        phone_number = message_data.get("phone_number")
        
        logger.info(
            "WEBHOOK_PARSED | type=%s | phone_number=%s | message_id=%s",
            webhook_type,
            phone_number or "unknown",
            message_data.get('message_id', 'unknown')
        )
        
        if webhook_type == "text_message":
            return _handle_text_message(phone_number, message_data)
        
        elif webhook_type == "non_text_message":
            return _handle_non_text_message(phone_number, message_data)
        
        elif webhook_type == "status_update":
            # Statut de message (delivered, read, etc.) - on ignore
            logger.debug(
                "WEBHOOK_STATUS | message_id=%s | status=%s",
                message_data.get('message_id', 'unknown'),
                message_data.get('status', 'unknown')
            )
            return JsonResponse({'status': 'ok', 'message_received': False, 'type': 'status'})
        
        elif webhook_type == "error":
            # Erreur de parsing
            error_msg = message_data.get("error_message", "Erreur inconnue")
            logger.error(
                "WEBHOOK_ERROR | type=webhook_error | error=%s | phone_number=%s",
                error_msg,
                phone_number or "unknown"
            )
            return JsonResponse({
                'status': 'error',
                'message': error_msg
            }, status=400)
        
        else:
            # Type inconnu
            logger.warning(
                "WEBHOOK_WARNING | type=unknown | webhook_type=%s | phone_number=%s",
                webhook_type,
                phone_number or "unknown"
            )
            return JsonResponse({'status': 'ok', 'message_received': False, 'type': 'unknown'})
            
    except Exception as e:
        # Erreur inattendue au niveau webhook
        logger.error(
            "WEBHOOK_ERROR | type=unexpected | error=%s | error_type=%s",
            str(e),
            type(e).__name__,
            exc_info=True
        )
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)


def _handle_text_message(phone_number: str, message_data: dict) -> JsonResponse:
    """
    Traite un message texte de manière défensive
    
    Args:
        phone_number: Numéro de téléphone
        message_data: Données du message parsé
    
    Returns:
        JsonResponse
    """
    message_text = message_data.get('message_text', '')
    
    logger.info(
        "MESSAGE_RECEIVED | phone_number=%s | message_length=%d | message_preview=%s",
        phone_number,
        len(message_text),
        message_text[:50]
    )
    
    # Traiter le message via le conversation handler
    try:
        handler = ConversationHandler()
        next_state, message_payload = handler.process_message(
            phone_number, message_text
        )
    except Exception as e:
        # Erreur dans le conversation handler
        logger.error(
            "HANDLER_ERROR | phone_number=%s | error=%s | error_type=%s",
            phone_number,
            str(e),
            type(e).__name__,
            exc_info=True
        )
        
        # Envoyer un message d'erreur générique à l'utilisateur
        _send_error_message_safe(
            phone_number,
            "Une erreur est survenue lors du traitement de votre message. Veuillez réessayer."
        )
        
        return JsonResponse({
            'status': 'error',
            'message_received': True,
            'error': 'Handler processing failed'
        }, status=500)
    
    # Envoyer la réponse de manière défensive
    try:
        if message_payload:
            _send_message_payload_safe(phone_number, message_payload)
    except Exception as e:
        # Erreur lors de l'envoi - déjà loggé dans _send_message_payload_safe
        logger.error(
            "MESSAGE_SEND_ERROR | phone_number=%s | next_state=%s | error=%s",
            phone_number,
            next_state,
            str(e)
        )
    
    return JsonResponse({
        'status': 'ok',
        'message_received': True,
        'next_state': next_state
    })


def _handle_non_text_message(phone_number: str, message_data: dict) -> JsonResponse:
    """
    Traite un message non-texte de manière défensive
    
    Args:
        phone_number: Numéro de téléphone
        message_data: Données du message parsé
    
    Returns:
        JsonResponse
    """
    error_msg = message_data.get("error_message", "Type de message non supporté")
    message_type = message_data.get('message_type', 'unknown')
    
    logger.info(
        "NON_TEXT_MESSAGE | phone_number=%s | message_type=%s",
        phone_number,
        message_type
    )
    
    # Envoyer le message d'erreur contrôlé
    _send_error_message_safe(phone_number, error_msg)
    
    return JsonResponse({
        'status': 'ok',
        'message_received': False,
        'error_type': 'non_text_message'
    })


def _send_message_payload_safe(phone_number: str, message_payload) -> None:
    """
    Envoie un message payload de manière défensive
    
    Args:
        phone_number: Numéro de téléphone
        message_payload: Payload abstrait du message
    
    Ne lève pas d'exception - logue les erreurs uniquement
    """
    try:
        # Telegram routing (tg:<chat_id>)
        if phone_number and str(phone_number).startswith("tg:"):
            if message_payload.options:
                options = [(opt["number"], opt["text"]) for opt in message_payload.options]
                text = TelegramService.format_menu_message(
                    message_payload.title or "",
                    message_payload.body or "",
                    options=options,
                    footer=message_payload.footer,
                )
            else:
                text = TelegramService.format_menu_message(
                    message_payload.title or "",
                    message_payload.body or "",
                    options=None,
                    footer=message_payload.footer,
                )
            success, error, _ = TelegramService.send_message(str(phone_number), text)
            if not success:
                logger.error(
                    "TELEGRAM_SEND_ERROR | phone_number=%s | error=%s",
                    phone_number,
                    error,
                )
            return

        if message_payload.options:
            # Message avec menu
            options = [(opt["number"], opt["text"]) for opt in message_payload.options]
            success, error = WhatsAppService.send_menu_message(
                phone_number,
                message_payload.title or "",
                options,
                message_payload.footer
            )
        else:
            # Message simple
            success, error = WhatsAppService.send_simple_message(
                phone_number,
                message_payload.title,
                message_payload.body,
                message_payload.footer
            )
        
        if not success:
            logger.error(
                "WHATSAPP_SEND_ERROR | phone_number=%s | error=%s | has_options=%s",
                phone_number,
                error,
                bool(message_payload.options)
            )
    except Exception as e:
        logger.error(
            "WHATSAPP_SEND_EXCEPTION | phone_number=%s | error=%s | error_type=%s",
            phone_number,
            str(e),
            type(e).__name__,
            exc_info=True
        )


def _send_error_message_safe(phone_number: str, error_message: str) -> None:
    """
    Envoie un message d'erreur de manière défensive
    
    Args:
        phone_number: Numéro de téléphone
        error_message: Message d'erreur à envoyer
    
    Ne lève pas d'exception - logue les erreurs uniquement
    """
    try:
        # Telegram routing (tg:<chat_id>)
        if phone_number and str(phone_number).startswith("tg:"):
            success, error, _ = TelegramService.send_message(str(phone_number), error_message)
            if not success:
                logger.error(
                    "TELEGRAM_ERROR_SEND_FAILED | phone_number=%s | telegram_error=%s | user_message=%s",
                    phone_number,
                    error,
                    error_message[:100]
                )
            return

        success, error = WhatsAppService.send_error_message(phone_number, error_message)
        
        if not success:
            logger.error(
                "ERROR_MESSAGE_SEND_FAILED | phone_number=%s | whatsapp_error=%s | user_message=%s",
                phone_number,
                error,
                error_message[:100]
            )
    except Exception as e:
        logger.error(
            "ERROR_MESSAGE_SEND_EXCEPTION | phone_number=%s | error=%s | error_type=%s",
            phone_number,
            str(e),
            type(e).__name__,
            exc_info=True
        )


@require_http_methods(["GET", "HEAD"])
def root(request):
    """
    Root endpoint that provides API information and available endpoints
    
    Returns:
        JsonResponse with API status and endpoint list
    """
    return JsonResponse({
        'status': 'ok',
        'message': 'WhatsApp Bot API is running',
        'endpoints': {
            'health': '/api/health',
            'webhook': '/api/whatsapp/webhook',
            'verify': '/api/whatsapp/verify',
            'admin': '/admin/'
        }
    })


@require_http_methods(["GET", "HEAD"])
def health_check(request):
    """
    Health check endpoint for monitoring and load balancers
    
    Returns:
        JsonResponse with system status
    """
    from django.db import connection
    from django.conf import settings
    
    health_status = {
        'status': 'ok',
        'timestamp': timezone.now().isoformat(),
        'checks': {}
    }
    
    # Check database connection
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            health_status['checks']['database'] = 'connected'
    except Exception as e:
        health_status['status'] = 'degraded'
        health_status['checks']['database'] = f'error: {str(e)}'
    
    # Check critical settings
    health_status['checks']['settings'] = {
        'debug': settings.DEBUG,
        'has_whatsapp_token': bool(settings.WHATSAPP_API_TOKEN),
        'has_openai_key': bool(settings.OPENAI_API_KEY),
    }
    
    status_code = 200 if health_status['status'] == 'ok' else 503
    return JsonResponse(health_status, status=status_code)


@csrf_exempt
@require_http_methods(["POST"])
def telegram_webhook(request):
    """
    Telegram Bot API webhook endpoint.

    Configure Telegram webhook to point to:
      https://<your-domain>/api/telegram/webhook
    """
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    tg_identifier, message_text = TelegramService.parse_update(payload)
    if not tg_identifier or message_text is None:
        # Acknowledge non-message updates
        return JsonResponse({"ok": True, "ignored": True})

    logger.info("TELEGRAM_WEBHOOK | from=%s | text_preview=%s", tg_identifier, message_text[:50])

    try:
        handler = ConversationHandler()
        next_state, message_payload = handler.process_message(tg_identifier, message_text)
    except Exception as e:
        logger.error("TELEGRAM_HANDLER_ERROR | from=%s | error=%s", tg_identifier, str(e), exc_info=True)
        TelegramService.send_message(tg_identifier, "Une erreur est survenue. Veuillez réessayer.")
        return JsonResponse({"ok": True})

    # Send reply via Telegram
    try:
        if message_payload:
            _send_message_payload_safe(tg_identifier, message_payload)
    except Exception as e:
        logger.error("TELEGRAM_SEND_EXCEPTION | from=%s | error=%s", tg_identifier, str(e), exc_info=True)

    return JsonResponse({"ok": True, "next_state": next_state})

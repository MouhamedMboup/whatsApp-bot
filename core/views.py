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
from core.handlers.conversation_handler import ConversationHandler

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def whatsapp_verify(request):
    """
    Webhook verification endpoint for WhatsApp
    """
    if request.method == 'GET':
        # Verification request from Meta
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')
        
        if mode == 'subscribe' and token == settings.WHATSAPP_VERIFY_TOKEN:
            return HttpResponse(challenge, status=200)
        else:
            return HttpResponse('Forbidden', status=403)
    
    return HttpResponse('Method not allowed', status=405)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def whatsapp_webhook(request):
    """
    Webhook endpoint for WhatsApp
    - GET: Handles webhook verification from Meta
    - POST: Handles incoming WhatsApp messages
    
    Gestion défensive des erreurs:
    - Try/except au niveau webhook uniquement
    - Pas de logique métier dans la gestion d'erreurs
    - Pas de mutation d'état sur erreur
    - Messages d'erreur clairs pour l'utilisateur
    - Logging structuré pour debugging
    """
    # Handle GET request for webhook verification (Meta sends this to verify the webhook)
    if request.method == 'GET':
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')
        
        logger.info(
            "WEBHOOK_VERIFY | mode=%s | token_match=%s",
            mode,
            token == settings.WHATSAPP_VERIFY_TOKEN
        )
        
        if mode == 'subscribe' and token == settings.WHATSAPP_VERIFY_TOKEN:
            return HttpResponse(challenge, status=200)
        else:
            logger.warning(
                "WEBHOOK_VERIFY_FAILED | mode=%s | token_provided=%s",
                mode,
                'yes' if token else 'no'
            )
            return HttpResponse('Forbidden', status=403)
    
    # Handle POST request for incoming messages
    try:
        # Parser le payload JSON
        try:
            payload = json.loads(request.body)
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


@require_http_methods(["GET"])
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


@require_http_methods(["GET"])
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

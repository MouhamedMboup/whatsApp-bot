"""
Views for WhatsApp bot
"""
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
import json


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
@require_http_methods(["POST"])
def whatsapp_webhook(request):
    """
    Webhook endpoint for receiving WhatsApp messages
    """
    # TODO: Implement message processing
    return JsonResponse({'status': 'ok'})

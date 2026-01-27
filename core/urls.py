"""
URLs for core app
"""
from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    # WhatsApp webhook endpoints
    path('whatsapp/webhook', views.whatsapp_webhook, name='whatsapp_webhook'),
    path('whatsapp/verify', views.whatsapp_verify, name='whatsapp_verify'),
    # Telegram webhook endpoint
    path('telegram/webhook', views.telegram_webhook, name='telegram_webhook'),
    # Health check endpoint
    path('health', views.health_check, name='health_check'),
]

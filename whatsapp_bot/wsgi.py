"""
WSGI config for whatsapp_bot project.
"""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'whatsapp_bot.settings')

application = get_wsgi_application()

"""
Custom middleware for development convenience
"""
import re
import logging
from django.core.exceptions import DisallowedHost
from django.conf import settings

logger = logging.getLogger(__name__)


class NgrokHostMiddleware:
    """
    Middleware to allow ngrok domains in development mode only.
    
    This middleware dynamically allows ngrok subdomains when DEBUG=True,
    making it easier to test with ngrok without manually updating ALLOWED_HOSTS
    every time the ngrok URL changes.
    
    Security:
    - Only active when DEBUG=True
    - Only allows known ngrok domain patterns
    - Never active in production
    
    How it works:
    - Intercepts requests before CommonMiddleware validates ALLOWED_HOSTS
    - Temporarily adds ngrok domains to ALLOWED_HOSTS for validation
    - Only processes in DEBUG mode
    """
    
    # ngrok domain patterns (free tier)
    NGROK_PATTERNS = [
        r'\.ngrok-free\.app$',
        r'\.ngrok-free\.dev$',
        r'\.ngrok\.io$',
        r'\.ngrok\.app$',
    ]
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Only process in DEBUG mode
        # This middleware runs BEFORE CommonMiddleware (position 2 in MIDDLEWARE list)
        # So we can modify ALLOWED_HOSTS before host validation happens
        if settings.DEBUG:
            # Extract host from request META (before get_host() validation)
            host = request.META.get('HTTP_HOST', '').split(':')[0]
            
            # Check if it's an ngrok domain and not already in ALLOWED_HOSTS
            if host and any(re.search(pattern, host) for pattern in self.NGROK_PATTERNS):
                if host not in settings.ALLOWED_HOSTS:
                    # Add to ALLOWED_HOSTS before CommonMiddleware validates
                    # This is safe because:
                    # 1. Only in DEBUG mode
                    # 2. Only for ngrok domains
                    # 3. Only for this request (though it persists in settings)
                    settings.ALLOWED_HOSTS.append(host)
                    logger.debug(f"Temporarily allowed ngrok domain: {host}")
        
        response = self.get_response(request)
        return response


class WebhookVerificationMiddleware:
    """
    Middleware to remove security headers for Meta webhook verification requests.
    
    This runs at the END of the middleware chain to ensure all security headers
    added by Django are removed before Meta receives the response.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        response = self.get_response(request)
        
        # Remove security headers for Meta webhook verification requests
        # Meta requires absolutely minimal response headers
        if '/api/whatsapp/webhook' in request.path and request.method in ['GET', 'HEAD']:
            # Check if this is a verification request (has hub.mode parameter)
            if request.GET.get('hub.mode') == 'subscribe':
                # Remove all security headers that Django middleware adds
                headers_to_remove = [
                    'X-Frame-Options',
                    'X-Content-Type-Options',
                    'Cross-Origin-Opener-Policy',
                    'Referrer-Policy',
                    'Server',  # Remove server identification
                ]
                for header in headers_to_remove:
                    if header in response:
                        del response[header]
                logger.debug("Removed security headers for Meta webhook verification")
        
        return response

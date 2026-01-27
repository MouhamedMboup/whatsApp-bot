"""
Django settings for whatsapp_bot project.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-change-me-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG', 'True') == 'True'

# Get ALLOWED_HOSTS from environment
ALLOWED_HOSTS = [host.strip() for host in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if host.strip()]

# In development (DEBUG=True), automatically allow ngrok domains
# This is safe because:
# 1. Only enabled when DEBUG=True (never in production)
# 2. ngrok domains are temporary and change frequently
# 3. Production should never have DEBUG=True
if DEBUG:
    import re
    
    def is_ngrok_domain(host):
        """Check if host is an ngrok domain"""
        ngrok_patterns = [
            r'\.ngrok-free\.app$',
            r'\.ngrok-free\.dev$',
            r'\.ngrok\.io$',
            r'\.ngrok\.app$',
        ]
        return any(re.search(pattern, host) for pattern in ngrok_patterns)
    
    # Note: ngrok domains are handled by NgrokHostMiddleware
    # See core/middleware.py for implementation
    pass
else:
    # Production: Only allow explicitly listed hosts
    # Never allow wildcards or ngrok domains in production
    pass

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.WebhookVerificationMiddleware',  # Must be LAST to remove headers after all other middleware
]

# In DEBUG mode, add custom middleware to handle ngrok domains
if DEBUG:
    # Insert ngrok host middleware before CommonMiddleware
    # This allows ngrok domains without modifying ALLOWED_HOSTS
    MIDDLEWARE.insert(2, 'core.middleware.NgrokHostMiddleware')

ROOT_URLCONF = 'whatsapp_bot.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'whatsapp_bot.wsgi.application'

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'whatsapp_bot'),
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD', 'postgres'),
        'HOST': os.getenv('DB_HOST', 'localhost'),  # Empty string = use Unix socket
        'PORT': os.getenv('DB_PORT', '5432'),
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'Africa/Abidjan'  # Côte d'Ivoire timezone
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Disable APPEND_SLASH for webhook verification - Meta requires exact URL match
APPEND_SLASH = False

# WhatsApp API Configuration
WHATSAPP_API_TOKEN = os.getenv('WHATSAPP_API_TOKEN', '')
WHATSAPP_PHONE_NUMBER_ID = os.getenv('WHATSAPP_PHONE_NUMBER_ID', '')
WHATSAPP_VERIFY_TOKEN = os.getenv('WHATSAPP_VERIFY_TOKEN', '')

# Telegram Bot API Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')

# OpenAI Configuration (for comment sanitization)
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')

# Security Settings
PERMIT_SALT_LENGTH = int(os.getenv('PERMIT_SALT_LENGTH', '32'))

# Session timeout (30 minutes)
SESSION_COOKIE_AGE = 30 * 60

# Logging configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
            'level': 'INFO',
        },
    },
    'loggers': {
        'core.views': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'core.services': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
}

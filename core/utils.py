"""
Utility functions for the WhatsApp bot
"""
import secrets
from django.conf import settings
from .models import DriverProfile


def generate_salt(length=None):
    """Génère un salt unique pour le hachage du permis"""
    if length is None:
        length = settings.PERMIT_SALT_LENGTH
    return secrets.token_hex(length)


def normalize_permit(permit_number: str) -> str:
    """
    Normalise le numéro de permis ivoirien
    - Convertit en majuscules
    - Supprime espaces et tirets
    """
    return DriverProfile.normalize_permit(permit_number)


def validate_permit_format(normalized_permit: str) -> bool:
    """
    Valide le format du permis normalisé
    - Longueur : 8-15 caractères
    - Format : alphanumérique uniquement (A-Z, 0-9)
    """
    return DriverProfile.validate_permit_format(normalized_permit)


def hash_permit(permit_normalized: str, salt: str) -> str:
    """
    Hash le permis normalisé avec le salt
    SHA-256(permit_normalized + salt)
    """
    return DriverProfile.hash_permit(permit_normalized, salt)


def mask_permit(permit_normalized: str) -> str:
    """
    Masque le permis pour affichage
    Affiche 2 premiers caractères + 2-3 derniers
    """
    return DriverProfile.mask_permit(permit_normalized)

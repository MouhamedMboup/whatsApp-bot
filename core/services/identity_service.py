"""
Service pour la gestion d'identité des drivers
"""
from django.db import transaction
from django.utils import timezone
from ..models import User, DriverProfile, ConsentLog
from ..utils import (
    normalize_permit,
    validate_permit_format,
    hash_permit,
    generate_salt,
    mask_permit
)


class IdentityService:
    """Service pour gérer l'identité des drivers"""
    
    @staticmethod
    def normalize_and_validate_permit(permit_number: str) -> tuple[bool, str, str]:
        """
        Normalise et valide un numéro de permis
        Retourne: (is_valid, normalized_permit, error_message)
        """
        try:
            normalized = normalize_permit(permit_number)
            is_valid = validate_permit_format(normalized)
            
            if not is_valid:
                return False, normalized, "Le permis doit contenir 8 à 15 caractères, lettres et chiffres uniquement."
            
            return True, normalized, ""
        except Exception as e:
            return False, "", f"Erreur de traitement: {str(e)}"
    
    @staticmethod
    def check_permit_exists(permit_normalized: str) -> bool:
        """
        Vérifie si un permis existe déjà (par hash)
        Note: Cette méthode itère sur tous les profils pour comparer les hashs
        En production, on pourrait optimiser avec un index ou une table de recherche
        """
        # Pour MVP: itérer sur tous les profils et comparer les hashs
        # Chaque profil a un salt unique, donc on doit tester avec chaque salt
        for profile in DriverProfile.objects.all():
            test_hash = hash_permit(permit_normalized, profile.permit_salt)
            if test_hash == profile.permit_hash:
                return True
        return False
    
    @staticmethod
    @transaction.atomic
    def create_driver_profile(
        user: User,
        permit_normalized: str,
        display_name: str = None
    ) -> tuple[bool, DriverProfile, str]:
        """
        Crée un profil driver avec permis hashé
        Retourne: (success, driver_profile, error_message)
        """
        try:
            # Vérifier que l'utilisateur est un driver
            if user.role != User.Role.DRIVER:
                return False, None, "L'utilisateur n'est pas un driver"
            
            # Vérifier qu'un profil n'existe pas déjà
            if hasattr(user, 'driver_profile'):
                return False, None, "Un profil existe déjà pour cet utilisateur"
            
            # Vérifier que le permis n'existe pas déjà
            if IdentityService.check_permit_exists(permit_normalized):
                return False, None, "Ce numéro de permis est déjà associé à un profil"
            
            # Générer salt unique
            salt = generate_salt()
            
            # Hasher le permis
            permit_hash = hash_permit(permit_normalized, salt)
            
            # Créer le profil
            driver_profile = DriverProfile.objects.create(
                user=user,
                display_name=display_name,
                permit_hash=permit_hash,
                permit_salt=salt,
                identity_locked=False
            )
            
            # Enregistrer le consentement
            ConsentLog.objects.create(
                user=user,
                action=ConsentLog.Action.PROFILE_CREATION,
                context={'driver_profile_id': driver_profile.id}
            )
            
            return True, driver_profile, ""
            
        except Exception as e:
            return False, None, f"Erreur lors de la création du profil: {str(e)}"
    
    @staticmethod
    @transaction.atomic
    def lock_identity(driver_profile: DriverProfile) -> tuple[bool, str]:
        """
        Verrouille l'identité d'un driver (irréversible)
        Retourne: (success, error_message)
        """
        try:
            if driver_profile.identity_locked:
                return False, "L'identité est déjà verrouillée"
            
            driver_profile.identity_locked = True
            driver_profile.identity_locked_at = timezone.now()
            driver_profile.save(update_fields=['identity_locked', 'identity_locked_at'])
            
            return True, ""
            
        except Exception as e:
            return False, f"Erreur lors du verrouillage: {str(e)}"
    
    @staticmethod
    def search_by_permit(permit_number: str) -> DriverProfile | None:
        """
        Recherche un driver par numéro de permis
        Retourne le DriverProfile si trouvé, None sinon
        """
        # Normaliser le permis
        is_valid, normalized, error = IdentityService.normalize_and_validate_permit(permit_number)
        if not is_valid:
            return None
        
        # Chercher dans tous les profils (comparaison hash)
        # TODO: Optimiser cette recherche
        for profile in DriverProfile.objects.all():
            test_hash = hash_permit(normalized, profile.permit_salt)
            if test_hash == profile.permit_hash:
                return profile
        
        return None
    
    @staticmethod
    def get_masked_permit(driver_profile: DriverProfile) -> str:
        """
        Retourne le permis masqué pour affichage
        Note: On ne peut pas démasquer car on n'a pas le permis original
        On retourne un format générique basé sur le hash
        """
        # Comme on n'a pas le permis original, on ne peut pas le masquer correctement
        # On retourne un format générique
        # En production, on pourrait stocker une version partielle pour l'affichage
        # Pour MVP, on retourne un format générique
        return "CI*******"
    
    @staticmethod
    def get_display_permit_for_confirmation(permit_normalized: str) -> str:
        """
        Retourne le permis masqué pour confirmation avant verrouillage
        Utilisé quand on a encore le permis normalisé en mémoire
        """
        return mask_permit(permit_normalized)

"""
Models for WhatsApp Driver Verification Bot
"""
import hashlib
import secrets
import json
from django.db import models
from django.core.validators import RegexValidator
from django.utils import timezone


class User(models.Model):
    """Utilisateur central avec rôle (Owner ou Driver)"""
    
    class Role(models.TextChoices):
        OWNER = 'OWNER', 'Propriétaire'
        DRIVER = 'DRIVER', 'Chauffeur'
    
    phone_number = models.CharField(
        max_length=20,
        unique=True,
        validators=[
            RegexValidator(
                regex=r'^\+[1-9]\d{1,14}$',
                message='Format E.164 requis (ex: +221771234567)'
            )
        ],
        help_text='Numéro WhatsApp au format E.164'
    )
    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        null=False,
        blank=False
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        db_table = 'users'
        indexes = [
            models.Index(fields=['phone_number']),
        ]
    
    def __str__(self):
        return f"{self.phone_number} ({self.role})"


class OwnerProfile(models.Model):
    """Profil spécifique aux propriétaires de véhicules"""
    
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='owner_profile',
        unique=True
    )
    collaborations_declared_count = models.IntegerField(default=0)
    last_declaration_at = models.DateTimeField(null=True, blank=True)
    abuse_flags = models.JSONField(
        default=dict,
        help_text='Flags d\'abus détectés (silencieux, non-bloquant)'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'owner_profiles'
    
    def __str__(self):
        return f"OwnerProfile: {self.user.phone_number}"


class DriverProfile(models.Model):
    """Profil spécifique aux chauffeurs avec identité vérifiée"""
    
    class ReputationState(models.TextChoices):
        NEW = 'NEW', 'Nouveau'
        ESTABLISHED = 'ESTABLISHED', 'Établi'
        CONFIRMED = 'CONFIRMED', 'Confirmé'
        REBUILDING = 'REBUILDING', 'Reconstruction'
    
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='driver_profile',
        unique=True
    )
    display_name = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text='Nom d\'affichage optionnel'
    )
    permit_hash = models.CharField(
        max_length=64,
        unique=True,
        null=False,
        blank=False,
        help_text='SHA-256 hash du permis normalisé + salt'
    )
    permit_salt = models.CharField(
        max_length=64,
        null=False,
        blank=False,
        help_text='Salt unique pour le hachage (jamais exposé)'
    )
    identity_locked = models.BooleanField(
        default=False,
        help_text='Une fois True, ne peut jamais revenir à False'
    )
    identity_locked_at = models.DateTimeField(null=True, blank=True)
    reputation_cached = models.JSONField(
        null=True,
        blank=True,
        help_text='Cache de réputation calculée'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'driver_profiles'
        indexes = [
            models.Index(fields=['permit_hash']),
            models.Index(fields=['identity_locked']),
            models.Index(fields=['permit_hash', 'permit_salt']),
        ]
    
    def __str__(self):
        return f"DriverProfile: {self.user.phone_number}"
    
    @staticmethod
    def normalize_permit(permit_number: str) -> str:
        """
        Normalise le numéro de permis ivoirien
        - Convertit en majuscules
        - Supprime espaces et tirets
        - Retourne la version normalisée
        """
        normalized = permit_number.upper().replace(' ', '').replace('-', '')
        return normalized
    
    @staticmethod
    def validate_permit_format(normalized_permit: str) -> bool:
        """
        Valide le format du permis normalisé
        - Longueur : 8-15 caractères
        - Format : alphanumérique uniquement (A-Z, 0-9)
        """
        if not (8 <= len(normalized_permit) <= 15):
            return False
        if not normalized_permit.isalnum():
            return False
        return True
    
    @staticmethod
    def hash_permit(permit_normalized: str, salt: str) -> str:
        """
        Hash le permis normalisé avec le salt
        SHA-256(permit_normalized + salt)
        """
        combined = permit_normalized + salt
        return hashlib.sha256(combined.encode()).hexdigest()
    
    @staticmethod
    def mask_permit(permit_normalized: str) -> str:
        """
        Masque le permis pour affichage
        Affiche 2 premiers caractères + 2-3 derniers
        Ex: CI1234567 -> CI****567
        """
        if len(permit_normalized) <= 4:
            return '****'
        first_two = permit_normalized[:2]
        last_chars = permit_normalized[-3:] if len(permit_normalized) >= 5 else permit_normalized[-2:]
        masked = '*' * (len(permit_normalized) - len(first_two) - len(last_chars))
        return f"{first_two}{masked}{last_chars}"


class Collaboration(models.Model):
    """Relation de travail entre Owner et Driver"""
    
    class State(models.TextChoices):
        DECLARED = 'DECLARED', 'Déclarée'
        PENDING_CONFIRMATION = 'PENDING_CONFIRMATION', 'En attente de confirmation'
        CONFIRMED = 'CONFIRMED', 'Confirmée'
        ELIGIBLE_FOR_RATING = 'ELIGIBLE_FOR_RATING', 'Éligible pour évaluation'
        RATED = 'RATED', 'Évaluée'
    
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='collaborations_as_owner'
    )
    driver = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='collaborations_as_driver'
    )
    state = models.CharField(
        max_length=30,
        choices=State.choices,
        default=State.DECLARED
    )
    declared_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    duration_days = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'collaborations'
        indexes = [
            models.Index(fields=['owner', 'driver']),
            models.Index(fields=['state']),
            models.Index(fields=['driver', 'state']),
        ]
        constraints = [
            models.CheckConstraint(
                check=~models.Q(owner=models.F('driver')),
                name='owner_cannot_be_driver'
            ),
        ]
    
    def __str__(self):
        return f"Collaboration: {self.owner.phone_number} -> {self.driver.phone_number} ({self.state})"
    
    def calculate_duration(self):
        """Calcule la durée en jours (inclusif)"""
        if self.start_date and self.end_date:
            delta = self.end_date - self.start_date
            self.duration_days = delta.days + 1
            self.save(update_fields=['duration_days'])
        return self.duration_days


class Rating(models.Model):
    """Évaluation d'une collaboration par l'owner"""
    
    collaboration = models.OneToOneField(
        Collaboration,
        on_delete=models.CASCADE,
        related_name='rating',
        unique=True
    )
    score_punctuality = models.IntegerField(
        choices=[(i, i) for i in range(1, 6)],
        help_text='Score ponctualité (1-5)'
    )
    score_vehicle_respect = models.IntegerField(
        choices=[(i, i) for i in range(1, 6)],
        help_text='Score respect véhicule (1-5)'
    )
    score_client_relation = models.IntegerField(
        choices=[(i, i) for i in range(1, 6)],
        help_text='Score relation client (1-5)'
    )
    score_reliability = models.IntegerField(
        choices=[(i, i) for i in range(1, 6)],
        help_text='Score fiabilité globale (1-5)'
    )
    comment_raw = models.TextField(
        null=True,
        blank=True,
        help_text='Commentaire brut original (jamais exposé publiquement)'
    )
    comment_sanitized = models.TextField(
        null=True,
        blank=True,
        help_text='Commentaire nettoyé par LLM'
    )
    comment_published = models.TextField(
        null=True,
        blank=True,
        help_text='Commentaire publié (peut être NULL si rejeté 3 fois)'
    )
    sanitization_rejections_count = models.IntegerField(
        default=0,
        help_text='Nombre de rejets de sanitisation (max 3)'
    )
    is_published = models.BooleanField(default=False)
    driver_response = models.TextField(null=True, blank=True)
    driver_response_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'ratings'
        indexes = [
            models.Index(fields=['collaboration']),
            models.Index(fields=['is_published']),
        ]
    
    def __str__(self):
        return f"Rating: {self.collaboration} - {self.get_median_score()}/5"
    
    def get_median_score(self):
        """Calcule le score médian des 4 critères"""
        scores = [
            self.score_punctuality,
            self.score_vehicle_respect,
            self.score_client_relation,
            self.score_reliability
        ]
        scores.sort()
        mid = len(scores) // 2
        if len(scores) % 2 == 0:
            return (scores[mid - 1] + scores[mid]) / 2.0
        return float(scores[mid])


class ConversationState(models.Model):
    """État de conversation WhatsApp pour chaque utilisateur"""
    
    class InputType(models.TextChoices):
        MENU_CHOICE = 'MENU_CHOICE', 'Choix menu'
        CONFIRMATION = 'CONFIRMATION', 'Confirmation Oui/Non'
        PHONE_NUMBER = 'PHONE_NUMBER', 'Numéro téléphone'
        PERMIT_NUMBER = 'PERMIT_NUMBER', 'Numéro permis'
        SANITIZED_TEXT = 'SANITIZED_TEXT', 'Texte à sanitiser'
        SCORE_RATING = 'SCORE_RATING', 'Score évaluation'
    
    class InvalidInputBehavior(models.TextChoices):
        RETRY_SAME_STATE = 'RETRY_SAME_STATE', 'Réessayer même état'
        RETURN_TO_PREVIOUS = 'RETURN_TO_PREVIOUS', 'Retour état précédent'
        RETURN_TO_MAIN_MENU = 'RETURN_TO_MAIN_MENU', 'Retour menu principal'
    
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='conversation_states'
    )
    current_state = models.CharField(max_length=100)
    temp_data = models.JSONField(default=dict)
    expected_input_type = models.CharField(
        max_length=20,
        choices=InputType.choices
    )
    allowed_values = models.JSONField(null=True, blank=True)
    invalid_input_count = models.IntegerField(default=0)
    behavior_on_invalid_input = models.CharField(
        max_length=30,
        choices=InvalidInputBehavior.choices
    )
    last_updated = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()
    
    class Meta:
        db_table = 'conversation_states'
        indexes = [
            models.Index(fields=['user']),
            models.Index(fields=['expires_at']),
        ]
    
    def __str__(self):
        return f"ConversationState: {self.user.phone_number} - {self.current_state}"
    
    def is_expired(self):
        """Vérifie si la session a expiré"""
        return timezone.now() > self.expires_at


class ConsentLog(models.Model):
    """Journal de tous les consentements explicites pour conformité légale"""
    
    class Action(models.TextChoices):
        PROFILE_CREATION = 'PROFILE_CREATION', 'Création profil'
        COLLABORATION_CONFIRMATION = 'COLLABORATION_CONFIRMATION', 'Confirmation collaboration'
        RATING_PUBLICATION = 'RATING_PUBLICATION', 'Publication évaluation'
    
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='consent_logs'
    )
    action = models.CharField(
        max_length=30,
        choices=Action.choices
    )
    context = models.JSONField(
        null=True,
        blank=True,
        help_text='Contexte de l\'action (collaboration_id, rating_id, etc.)'
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'consent_logs'
        indexes = [
            models.Index(fields=['user', 'action', 'timestamp']),
        ]
    
    def __str__(self):
        return f"ConsentLog: {self.user.phone_number} - {self.action} - {self.timestamp}"

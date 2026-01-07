"""
Admin configuration for core models
"""
from django.contrib import admin
from .models import (
    User, OwnerProfile, DriverProfile, Collaboration,
    Rating, ConversationState, ConsentLog
)


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('phone_number', 'role', 'created_at', 'last_activity', 'is_active')
    list_filter = ('role', 'is_active', 'created_at')
    search_fields = ('phone_number',)
    readonly_fields = ('created_at', 'last_activity')


@admin.register(OwnerProfile)
class OwnerProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'collaborations_declared_count', 'last_declaration_at', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('user__phone_number',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(DriverProfile)
class DriverProfileAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'display_name', 'identity_locked', 'identity_locked_at', 'created_at'
    )
    list_filter = ('identity_locked', 'created_at')
    search_fields = ('user__phone_number', 'display_name')
    readonly_fields = (
        'permit_hash', 'permit_salt', 'identity_locked_at',
        'created_at', 'updated_at'
    )
    
    def has_change_permission(self, request, obj=None):
        """Empêcher la modification du permis après verrouillage"""
        if obj and obj.identity_locked:
            # Ne pas permettre la modification des champs critiques
            return False
        return super().has_change_permission(request, obj)


@admin.register(Collaboration)
class CollaborationAdmin(admin.ModelAdmin):
    list_display = (
        'owner', 'driver', 'state', 'start_date', 'end_date',
        'duration_days', 'declared_at'
    )
    list_filter = ('state', 'declared_at', 'confirmed_at')
    search_fields = ('owner__phone_number', 'driver__phone_number')
    readonly_fields = ('declared_at', 'confirmed_at', 'created_at', 'updated_at')


@admin.register(Rating)
class RatingAdmin(admin.ModelAdmin):
    list_display = (
        'collaboration', 'score_punctuality', 'score_vehicle_respect',
        'score_client_relation', 'score_reliability', 'is_published', 'created_at'
    )
    list_filter = ('is_published', 'created_at')
    search_fields = ('collaboration__owner__phone_number', 'collaboration__driver__phone_number')
    readonly_fields = (
        'comment_raw', 'sanitization_rejections_count',
        'created_at', 'updated_at', 'published_at'
    )


@admin.register(ConversationState)
class ConversationStateAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'current_state', 'expected_input_type',
        'invalid_input_count', 'expires_at', 'last_updated'
    )
    list_filter = ('current_state', 'expected_input_type', 'expires_at')
    search_fields = ('user__phone_number',)
    readonly_fields = ('last_updated',)


@admin.register(ConsentLog)
class ConsentLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'action', 'timestamp', 'context')
    list_filter = ('action', 'timestamp')
    search_fields = ('user__phone_number',)
    readonly_fields = ('timestamp',)

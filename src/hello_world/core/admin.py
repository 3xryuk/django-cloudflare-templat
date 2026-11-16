from django.contrib import admin

from .models import DeploymentNote


@admin.register(DeploymentNote)
class DeploymentNoteAdmin(admin.ModelAdmin):
    """Admin configuration for DeploymentNote."""

    list_display = ("title", "environment", "is_active", "created_at")
    list_filter = ("environment", "is_active")
    search_fields = ("title",)

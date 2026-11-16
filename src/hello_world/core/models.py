from django.db import models


class DeploymentNote(models.Model):
    """Sample model compatible with Cloudflare D1 (SQLite) and local SQLite."""

    class Environment(models.TextChoices):
        LOCAL = "local", "Local Development"
        WORKERS = "workers", "Cloudflare Workers"

    title = models.CharField(max_length=255, db_index=True)
    environment = models.CharField(
        max_length=20,
        choices=Environment.choices,
        default=Environment.WORKERS,
        db_index=True,
    )
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["environment", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.environment})"

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class AIUsage(models.Model):
    """Single Anthropic API call record. Used for usage tracking and
    billing aggregation per team and per user."""

    class Endpoint(models.TextChoices):
        PING = "ping", _("Ping")
        PLAN = "plan", _("Generate plan")
        TRAINING = "training", _("Generate training")
        TRAINING_FREEFORM = "training_freeform", _("Generate free-text training")
        REVIEW = "review", _("Review training block")
        EXPLAIN = "explain", _("Explain session for athletes")

    team = models.ForeignKey(
        "team.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_usage",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_usage",
    )
    endpoint = models.CharField(max_length=20, choices=Endpoint.choices)
    model_used = models.CharField(
        max_length=100,
        help_text="Anthropic model identifier (e.g. claude-haiku-4-5)",
    )
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    cache_creation_tokens = models.IntegerField(default=0)
    cache_read_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["team", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["-created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        team_str = self.team.name if self.team else "(no team)"
        return (
            f"{self.endpoint} | {team_str} | {self.total_tokens} tokens | "
            f"{self.created_at:%Y-%m-%d %H:%M}"
        )

    def save(self, *args, **kwargs):
        self.total_tokens = (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
        )
        super().save(*args, **kwargs)

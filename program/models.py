from django.db import models
from django.utils.translation import gettext as _


class Program(models.Model):
    name = models.CharField(max_length=100, verbose_name=_("name"))
    date_start = models.DateField(
        blank=True,
        null=True,
    )
    date_end = models.DateField(
        blank=True,
        null=True,
    )
    team = models.ForeignKey(
        "team.Team",
        on_delete=models.PROTECT,
        related_name="programs",
    )
    frequency_per_week = models.PositiveIntegerField(null=True, blank=True)
    description = models.TextField(blank=True, default="")
    generated_by_ai = models.BooleanField(default=False)
    ai_prompt = models.TextField(blank=True, default="")
    ai_response = models.TextField(blank=True, default="")
    ai_generated_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Program")
        ordering = ["-updated_at", "-id"]
        indexes = [
            # The list endpoint filters team__in + is_active (then orders by
            # name over the small per-team result). Matches that predicate —
            # NOT (team, -updated_at), which the list does not order by.
            models.Index(fields=["team", "is_active"]),
        ]

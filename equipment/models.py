from django.db import models
from django.db.models import UniqueConstraint
from django.utils.translation import gettext_lazy as _


class Equipment(models.Model):
    """A managed, team-scoped piece of training equipment ("Matériel").

    Mirrors the managed ``place.Place`` model: a reusable, named item picked
    from a team-managed catalog. Sessions reference the items they use through
    the ``Event.equipment_items`` M2M; the free-text ``Event.equipment`` string
    is SYNCED to the joined item names so iCal, the public-share view and the AI
    generators — which read that string — keep working unchanged.
    """

    team = models.ForeignKey(
        "team.Team",
        on_delete=models.CASCADE,
        related_name="equipment",
    )
    name = models.CharField(
        max_length=255,
        verbose_name=_("name"),
        help_text=_("Display name of the equipment (e.g. 'Pull-buoy')."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("Equipment")
        verbose_name_plural = _("Equipment")
        constraints = [
            UniqueConstraint(fields=["team", "name"], name="uniq_equipment_team_name"),
        ]

    def __str__(self):
        return self.name

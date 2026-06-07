from django.db import models
from django.db.models import UniqueConstraint
from django.utils.translation import gettext_lazy as _


class Place(models.Model):
    """A managed, team-scoped venue ("Lieu") a team's sessions take place at.

    Additive layer over the free-text ``Event.location`` string: a Place is a
    reusable, named venue picked from a managed list. Selecting a Place on an
    Event (or as a Team default) SYNCS the canonical free-text fields
    (``Event.location`` / ``Team.default_pool``) to ``place.name`` so iCal, the
    AI plan generator and the public-share view — which all read those strings —
    keep working unchanged.
    """

    team = models.ForeignKey(
        "team.Team",
        on_delete=models.CASCADE,
        related_name="places",
    )
    name = models.CharField(
        max_length=255,
        verbose_name=_("name"),
        help_text=_("Display name of the venue (e.g. 'Piscine olympique')."),
    )
    address = models.CharField(
        max_length=500,
        blank=True,
        default="",
        verbose_name=_("address"),
        help_text=_("Optional postal address / directions for the venue."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            UniqueConstraint(fields=["team", "name"], name="uniq_place_team_name"),
        ]

    def __str__(self):
        return self.name

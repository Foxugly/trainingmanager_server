from django.db import models
from django.utils.translation import gettext_lazy as _


class Place(models.Model):
    """A venue ("Lieu") sessions take place at — a global, sport-scoped pool.

    A Place belongs to a sport and is shared: it can be linked to several teams
    in parallel through ``Team.places`` (M2M); a team marks one as its
    ``default_place``. Selecting a Place on an Event (or as a team default) SYNCS
    the canonical free-text fields (``Event.location`` / ``Team.default_pool``)
    to ``place.name`` so iCal, the AI plan generator and the public-share view —
    which read those strings — keep working unchanged.
    """

    sport = models.ForeignKey(
        "sport.Sport",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="places",
        help_text=_("Sport this venue belongs to (e.g. Natation)."),
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

    def __str__(self):
        return self.name

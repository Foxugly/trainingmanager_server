from django.db import models
from django.utils.translation import gettext_lazy as _


class Equipment(models.Model):
    """A global, multilingual piece of training equipment ("Matériel").

    Reference data (like sport modalities): curated centrally (admin + seed
    migration) and translated via django-modeltranslation, so ``name`` resolves
    to the active language. A team owner enables the subset its group can use
    through ``Team.equipment`` (M2M); a session records what it uses through
    ``Event.equipment_items`` (M2M), restricted to the team's enabled set.
    """

    sport = models.ForeignKey(
        "sport.Sport",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="equipment",
        help_text=_("Sport this equipment belongs to (e.g. Natation)."),
    )
    name = models.CharField(
        max_length=255,
        verbose_name=_("name"),
        help_text=_("Display name of the equipment (e.g. 'Pull-buoy')."),
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("Equipment")
        verbose_name_plural = _("Equipment")

    def __str__(self):
        return self.name

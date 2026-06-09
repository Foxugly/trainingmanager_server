from django.db import models
from django.utils.translation import gettext_lazy as _

from tools.choices import TrainingType


class Sport(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    default_training_type = models.CharField(
        max_length=20,
        choices=TrainingType.choices,
        default=TrainingType.STRUCTURED,
        help_text=_(
            "Default training-content type for events of this sport "
            "(overridable per team and per event)."
        ),
    )
    energy_systems = models.ManyToManyField(
        "exercise.EnergySystem",
        related_name="sports",
        blank=True,
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

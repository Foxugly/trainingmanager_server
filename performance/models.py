from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Unit(models.TextChoices):
    SECONDS = "s", _("Seconds")
    METERS = "m", _("Meters")
    REPS = "reps", _("Repetitions")
    KILOGRAMS = "kg", _("Kilograms")
    POINTS = "pts", _("Points")


class Performance(models.Model):
    """A single logged athlete performance (time / distance / reps / ...).

    Scoped to a (team, member) pair: an athlete logs personal results over
    time, coaches can log/view for any athlete in their team. The frontend
    draws progress charts and highlights personal bests, so each row carries
    a `unit` and an `is_lower_better` hint for the PB direction.
    """

    team = models.ForeignKey(
        "team.Team",
        on_delete=models.CASCADE,
        related_name="performances",
    )
    member = models.ForeignKey(
        "member.Member",
        on_delete=models.CASCADE,
        related_name="performances",
    )
    label = models.CharField(
        max_length=120,
        verbose_name=_("label"),
        help_text=_("Discipline / test name, e.g. '100m freestyle' or 'Cooper test'."),
    )
    value = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        help_text=_(
            "Numeric result, interpreted by unit (seconds for time, meters "
            "for distance, etc.)."
        ),
    )
    unit = models.CharField(
        max_length=10,
        choices=Unit.choices,
        default=Unit.SECONDS,
    )
    recorded_on = models.DateField(
        help_text=_("Date the performance was achieved."),
    )
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="logged_performances",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-recorded_on", "-id"]
        indexes = [
            models.Index(fields=["member", "label"]),
            models.Index(fields=["team", "recorded_on"]),
        ]

    def __str__(self):
        return f"{self.member} — {self.label}: {self.value} {self.unit}"

    @property
    def is_lower_better(self):
        """PB direction hint: True when a lower value is better.

        Only time (seconds) is lower-is-better; distance / reps / kg / points
        are all higher-is-better.
        """
        return self.unit == Unit.SECONDS

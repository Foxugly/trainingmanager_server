from django.conf import settings as django_settings
from django.db import models
from django.utils.translation import gettext as _

from tools.validators import MMSS_VALIDATOR


class Modality(models.Model):
    name = models.CharField(max_length=20, verbose_name=_("name"))
    sport = models.ForeignKey(
        "sport.Sport",
        on_delete=models.PROTECT,
        related_name="modalities",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("name", "sport")
        ordering = ["sport", "name"]

    def __str__(self):
        return self.name


class EnergySystem(models.Model):
    name = models.CharField(max_length=20, verbose_name=_("name"))
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class EnergySegment(models.Model):
    abv = models.CharField(max_length=10, verbose_name=_("abv"))
    description = models.CharField(
        max_length=200, null=True, blank=True, verbose_name=_("description")
    )
    energysystem = models.ForeignKey(EnergySystem, null=True, blank=True, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return "%s (%s)" % (self.abv, self.energysystem)


class Exercise(models.Model):
    order = models.IntegerField(verbose_name=_("order"), default=1)
    t_start = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        validators=[MMSS_VALIDATOR],
        verbose_name=_("start"),
    )
    t_break = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        validators=[MMSS_VALIDATOR],
        verbose_name=_("break"),
    )
    repetition = models.PositiveIntegerField(verbose_name=_("repetition"), default=1)
    distance = models.PositiveIntegerField(verbose_name=_("distance"), default=100)
    modality = models.ForeignKey(
        Modality, verbose_name=_("modality"), null=True, blank=True, on_delete=models.CASCADE
    )
    energysegment = models.ForeignKey(
        EnergySegment,
        null=True,
        blank=True,
        verbose_name=_("Energy Segment"),
        on_delete=models.CASCADE,
    )
    notes = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("notes"),
    )
    language = models.CharField(
        max_length=2,
        choices=django_settings.LANGUAGES,
    )
    # Who created the exercise (set on POST / standalone clone). Lets a *library*
    # exercise (one attached to no round, hence to no team) be edited/deleted only
    # by its author or staff — otherwise any same-(sport, language) trainer could
    # mutate it. Mirrors Round.author.
    author = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def usage_count(self):
        return self.round_set.count()

    def __str__(self):
        return "%s %d" % (_("Exercise"), self.id)

    class Meta:
        verbose_name = _("Exercise")
        indexes = [
            models.Index(fields=["language", "order"]),
        ]

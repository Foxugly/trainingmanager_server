from django.conf import settings as django_settings
from django.db import models
from django.utils.translation import gettext as _

from exercise.models import Exercise
from tools.validators import MMSS_VALIDATOR


class Round(models.Model):
    order = models.PositiveIntegerField(default=1)
    count = models.PositiveIntegerField(default=1)
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
    exercises = models.ManyToManyField(
        Exercise,
        blank=True,
    )
    sport = models.ForeignKey(
        "sport.Sport",
        on_delete=models.PROTECT,
        related_name="rounds",
    )
    language = models.CharField(
        max_length=2,
        choices=django_settings.LANGUAGES,
    )
    # Who created the round (set on POST). Lets a *library* round (one tied to no
    # event, hence to no team) be edited/deleted only by its author or staff —
    # otherwise any same-(sport, language) trainer could mutate it.
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
        return self.event_set.count()

    def __str__(self):
        return "%s %d" % (_("Round"), self.id)

    class Meta:
        verbose_name = _("Round")

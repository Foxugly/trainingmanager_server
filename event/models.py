from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.db import models
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext as _

from member.models import Member
from round.models import Round


class VisibilityMode(models.TextChoices):
    """How a per-aspect piece of a training session is shown to athletes.

    Shared by the team-level defaults (Team.vis_*) and the per-event
    overrides (Event.vis_*).
    """

    ALWAYS = "always", _("Always")
    AFTER = "after", _("After the session")
    NEVER = "never", _("Never")


# The three independently-gated aspects of a session.
VISIBILITY_ASPECTS = ("distance", "goal", "rounds")


class Event(models.Model):
    name = models.CharField(max_length=100, verbose_name=_("name"))
    goal = models.CharField(max_length=100, blank=True, null=True, verbose_name=_("goal"))
    color = models.CharField(max_length=10, blank=True, verbose_name=_("color"))
    date = models.DateField(
        blank=True,
        null=True,
    )
    hour_start = models.TimeField(
        blank=True,
        null=True,
    )
    hour_end = models.TimeField(
        blank=True,
        null=True,
    )
    total = models.PositiveIntegerField(default=0)
    rounds = models.ManyToManyField(
        Round,
        blank=True,
    )
    members = models.ManyToManyField(
        Member,
        blank=True,
    )
    refer_program = models.ForeignKey(
        "program.Program",
        verbose_name=_("refer_program"),
        related_name="events",
        null=True,
        on_delete=models.PROTECT,
    )
    vis_distance = models.CharField(
        max_length=10,
        choices=VisibilityMode.choices,
        default=VisibilityMode.ALWAYS,
        help_text=_("Visibility of this session's total distance to athletes."),
    )
    vis_goal = models.CharField(
        max_length=10,
        choices=VisibilityMode.choices,
        default=VisibilityMode.ALWAYS,
        help_text=_("Visibility of this session's goal to athletes."),
    )
    vis_rounds = models.CharField(
        max_length=10,
        choices=VisibilityMode.choices,
        default=VisibilityMode.ALWAYS,
        help_text=_("Visibility of this session's rounds (and exercises) to athletes."),
    )
    generated_by_ai = models.BooleanField(default=False)
    ai_prompt = models.TextField(blank=True, default="")
    ai_response = models.TextField(blank=True, default="")
    ai_generated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "%s %d" % (_("Event"), self.pk)

    @property
    def team(self):
        """The Team this event belongs to (via refer_program), or None."""
        program = self.refer_program
        return program.team if program is not None else None

    def _team_tzinfo(self):
        """ZoneInfo for the event's team timezone, falling back to UTC.

        Guards a missing team, a missing/blank timezone, and an invalid
        IANA name (which should not happen given serializer validation, but
        we must never crash visibility resolution on bad data).
        """
        team = self.team
        tz_name = getattr(team, "timezone", None)
        if not tz_name:
            return ZoneInfo("UTC")
        try:
            return ZoneInfo(tz_name)
        except Exception:
            return ZoneInfo("UTC")

    def is_over(self, now=None):
        """Return True if this session has ended, judged in the team's timezone.

        The session end is built from ``date`` + ``hour_end`` (or end-of-day
        23:59 when no ``hour_end`` is set), interpreted as a wall-clock time
        in the team's IANA timezone, then compared against ``now``
        (``django.utils.timezone.now()`` if not supplied).

        An event with no ``date`` is never considered over (it is unscheduled).
        A missing team/timezone falls back to UTC.
        """
        if self.date is None:
            return False
        if now is None:
            now = dj_timezone.now()
        tzinfo = self._team_tzinfo()
        end_time = self.hour_end if self.hour_end is not None else time(23, 59)
        end_dt = datetime.combine(self.date, end_time, tzinfo=tzinfo)
        return now >= end_dt

    def aspect_visible_to_athlete(self, aspect, now=None):
        """Resolve whether an athlete may see ``aspect`` of this session.

        ``aspect`` is one of ``"distance"``, ``"goal"``, ``"rounds"``.

        Returns True iff the event's mode for that aspect is ALWAYS, or it is
        AFTER and the session ``is_over(now)``. NEVER always returns False.
        Managers/owners bypass this entirely (handled at the API layer).
        """
        mode = getattr(self, f"vis_{aspect}")
        if mode == VisibilityMode.ALWAYS:
            return True
        if mode == VisibilityMode.AFTER:
            return self.is_over(now=now)
        return False

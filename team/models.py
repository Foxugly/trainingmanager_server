import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from event.models import VisibilityMode


def generate_invitation_token():
    return secrets.token_urlsafe(32)


def default_invitation_expiration():
    return timezone.now() + timedelta(days=7)


class Team(models.Model):
    class JoinRequestPolicy(models.TextChoices):
        MANUAL = "manual", _("Manual — managers accept/reject each request")
        AUTO = "auto", _("Auto-accept — every join request is accepted immediately")

    class TopicCreationPolicy(models.TextChoices):
        OWNER = "owner", _("Owner only")
        COACHES = "coaches", _("Owner and coaches")
        MEMBERS = "members", _("Everyone (incl. athletes)")

    name = models.CharField(max_length=200, unique=True)
    sport = models.ForeignKey(
        "sport.Sport",
        on_delete=models.PROTECT,
        related_name="teams",
        null=True,
        blank=True,
    )
    level = models.ForeignKey(
        "level.Level",
        on_delete=models.PROTECT,
        related_name="teams",
        null=True,
        blank=True,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_teams",
    )
    managers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="managed_teams",
        blank=True,
    )
    language = models.CharField(
        max_length=2,
        choices=settings.LANGUAGES,
        default="fr",
    )
    is_active = models.BooleanField(default=True)
    is_public = models.BooleanField(default=False)
    logo = models.TextField(
        blank=True,
        default="",
        help_text=_(
            "Small base64 data-URL for the team logo "
            "(e.g. 'data:image/png;base64,...'). Stored inline in the DB; "
            "no file storage."
        ),
    )
    roti_enabled = models.BooleanField(
        default=False,
        help_text=_(
            "If True, athletes can submit a per-session difficulty rating "
            "(ROTI, 1..5) for the team's events."
        ),
    )
    rsvp_enabled = models.BooleanField(
        default=False,
        help_text=_(
            "If True, athletes can declare their availability (RSVP: going / "
            "maybe / not going) for the team's events."
        ),
    )
    weekly_recap_enabled = models.BooleanField(
        default=False,
        help_text=_(
            "If True, this team is included in the weekly recap email sent to "
            "its owner and managers. Default False (opt-in per team)."
        ),
    )
    attendance_statuses = models.ManyToManyField(
        "attendance.AttendanceStatus",
        related_name="teams",
        blank=True,
        help_text=_(
            "Statuses available for marking attendance in this team's events. "
            "Default: present, absent, excused."
        ),
    )
    equipment = models.ManyToManyField(
        "equipment.Equipment",
        related_name="teams",
        blank=True,
        help_text=_(
            "Equipment from the global catalog this team is allowed to use. "
            "Sessions can only reference equipment in this enabled set."
        ),
    )
    join_request_policy = models.CharField(
        max_length=10,
        choices=JoinRequestPolicy.choices,
        default=JoinRequestPolicy.MANUAL,
        help_text=_(
            "Manual = managers accept/reject each TeamJoinRequest. "
            "Auto = every join request is accepted immediately on submission."
        ),
    )
    topic_creation = models.CharField(
        max_length=10,
        choices=TopicCreationPolicy.choices,
        default=TopicCreationPolicy.COACHES,
        help_text=_(
            "Who may create messaging topics in this team. "
            "owner = team owner only; coaches = owner and managers; "
            "members = owner, managers, and active athlete members."
        ),
    )
    timezone = models.CharField(
        max_length=64,
        default="Europe/Brussels",
        help_text=_(
            "IANA timezone name (e.g. 'Europe/Brussels'). Used to decide, in "
            "the team's local time, whether a session is over for per-aspect "
            "athlete visibility (vis_distance/vis_goal/vis_rounds)."
        ),
    )
    vis_distance = models.CharField(
        max_length=10,
        choices=VisibilityMode.choices,
        default=VisibilityMode.ALWAYS,
        help_text=_(
            "Default visibility of a session's total distance to athletes. "
            "Inherited by new events; overridable per event."
        ),
    )
    vis_goal = models.CharField(
        max_length=10,
        choices=VisibilityMode.choices,
        default=VisibilityMode.ALWAYS,
        help_text=_(
            "Default visibility of a session's goal to athletes. "
            "Inherited by new events; overridable per event."
        ),
    )
    vis_rounds = models.CharField(
        max_length=10,
        choices=VisibilityMode.choices,
        default=VisibilityMode.ALWAYS,
        help_text=_(
            "Default visibility of a session's rounds (and their exercises) "
            "to athletes. Inherited by new events; overridable per event."
        ),
    )
    public_sharing_enabled = models.BooleanField(
        default=False,
        help_text=_("Allow individual sessions of this team to be shared via a public read-only link."),
    )
    public_show_distance = models.BooleanField(
        default=True,
        help_text=_("Show a shared session's total distance on its public link."),
    )
    public_show_goal = models.BooleanField(
        default=False,
        help_text=_("Show a shared session's goal on its public link."),
    )
    public_show_rounds = models.BooleanField(
        default=True,
        help_text=_("Show a shared session's rounds & exercises on its public link."),
    )
    notify_managers_on_join_request = models.BooleanField(
        default=True,
        help_text=_(
            "When join_request_policy=manual, send the owner and managers an email "
            "with accept/reject magic links on each new request. Ignored when "
            "policy=auto (no manual decision is needed)."
        ),
    )
    notify_coaches_on_note = models.BooleanField(
        default=True,
        help_text=_(
            "When True, the team owner and managers (except the author) are "
            "notified each time a note is added on one of the team's athletes."
        ),
    )
    notify_athlete_on_visible_note = models.BooleanField(
        default=True,
        help_text=_(
            "When True, an athlete with a linked user account is notified when a "
            "note shared with them (visible_to_athlete=True) is added."
        ),
    )
    default_pool = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_("Default venue/pool for this team's sessions (used by the season-plan generator)."),
    )
    places = models.ManyToManyField(
        "place.Place",
        related_name="teams",
        blank=True,
        help_text=_(
            "Venues (Lieux) from the global sport-scoped pool this team uses. A "
            "place can belong to several teams in parallel."
        ),
    )
    default_place = models.ForeignKey(
        "place.Place",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text=_(
            "The team's default venue (Lieu), one of its linked places. When "
            "set, the canonical free-text 'default_pool' is synced to the "
            "place's name so the AI plan generator keeps reading it unchanged."
        ),
    )
    season_start = models.DateField(
        null=True,
        blank=True,
        help_text=_("Default season start date, pre-fills the plan generator."),
    )
    season_end = models.DateField(
        null=True,
        blank=True,
        help_text=_("Default season end date, pre-fills the plan generator."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def is_managed_by(self, user):
        if not user.is_authenticated:
            return False
        return user == self.owner or self.managers.filter(pk=user.pk).exists()

    @property
    def active_members(self):
        """Member queryset of currently active memberships (left_at IS NULL)."""
        from member.models import Member

        member_ids = self.memberships.filter(left_at__isnull=True).values_list(
            "member_id", flat=True
        )
        return Member.objects.filter(pk__in=member_ids)


class TeamMembership(models.Model):
    """Tracks athlete membership in a team over time.

    Multiple rows can exist for the same (team, member) pair: each row
    represents a distinct membership period. Re-joining creates a new row
    rather than reopening the previous one — this preserves history.
    """

    team = models.ForeignKey(
        "team.Team",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    member = models.ForeignKey(
        "member.Member",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    joined_at = models.DateTimeField(
        default=timezone.now,
        help_text=_("When this athlete joined the team."),
    )
    left_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When this athlete left the team. NULL = currently active."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-joined_at"]
        indexes = [
            models.Index(fields=["team", "left_at"], name="membership_team_left_idx"),
            models.Index(fields=["member", "left_at"], name="membership_member_left_idx"),
        ]

    def __str__(self):
        status = "active" if self.left_at is None else f"left {self.left_at:%Y-%m-%d}"
        return f"{self.member} in {self.team} ({status})"

    @property
    def is_active(self):
        return self.left_at is None


class TeamJoinRequest(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("accepted", "Accepted"),
        ("rejected", "Rejected"),
        ("cancelled", "Cancelled"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="join_requests",
    )
    team = models.ForeignKey(
        "team.Team",
        on_delete=models.CASCADE,
        related_name="join_requests",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    message = models.TextField(blank=True)
    response_message = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="handled_join_requests",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"{self.user} -> {self.team} ({self.status})"


class TeamInvitation(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("completed", "Completed"),
        ("expired", "Expired"),
        ("cancelled", "Cancelled"),
    ]

    team = models.ForeignKey(
        "team.Team",
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sent_invitations",
    )
    member = models.OneToOneField(
        "member.Member",
        on_delete=models.CASCADE,
        related_name="invitation",
    )
    email = models.EmailField()
    token = models.CharField(
        max_length=64,
        unique=True,
        default=generate_invitation_token,
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_invitation_expiration)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invitation to {self.team.name} for {self.email} ({self.status})"

    def is_valid(self):
        if self.status != "pending":
            return False
        if timezone.now() > self.expires_at:
            return False
        return True


class Weekday(models.IntegerChoices):
    """Weekday numbering follows Python's ``date.weekday()`` convention:
    Monday is 0 and Sunday is 6 (NOT isoweekday / cron numbering)."""

    MONDAY = 0, _("Monday")
    TUESDAY = 1, _("Tuesday")
    WEDNESDAY = 2, _("Wednesday")
    THURSDAY = 3, _("Thursday")
    FRIDAY = 4, _("Friday")
    SATURDAY = 5, _("Saturday")
    SUNDAY = 6, _("Sunday")


class TrainingSlot(models.Model):
    """One recurring weekly training slot of a team's reusable template.

    The template is the set of a team's TrainingSlots. Each slot is a
    (weekday, hour_start, hour_end) tuple; the season-plan generator places
    one session per slot per week and derives the weekly frequency from the
    number of slots.

    ``weekday`` follows Python's ``date.weekday()`` convention: Monday=0 …
    Sunday=6 (see the Weekday choices).
    """

    team = models.ForeignKey(
        "team.Team",
        on_delete=models.CASCADE,
        related_name="training_slots",
    )
    weekday = models.PositiveSmallIntegerField(
        choices=Weekday.choices,
        help_text=_("Day of the week (Python convention: Monday=0 … Sunday=6)."),
    )
    hour_start = models.TimeField(help_text=_("Slot start time (local team time)."))
    hour_end = models.TimeField(help_text=_("Slot end time; must be after hour_start."))
    place = models.ForeignKey(
        "place.Place",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text=_(
            "Optional venue for this weekly slot. Defaults (in the UI) to the "
            "team's default_place; feeds the season-plan generator per day."
        ),
    )

    class Meta:
        ordering = ["weekday", "hour_start"]

    def __str__(self):
        return f"{self.team} — {self.get_weekday_display()} {self.hour_start}-{self.hour_end}"

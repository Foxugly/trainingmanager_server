import re
from zoneinfo import ZoneInfo, available_timezones

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from customuser.serializers import CustomUserPublicSerializer
from level.models import Level
from level.serializers import LevelSerializer
from sport.models import Sport
from sport.serializers import SportSerializer

from .models import Team, TeamInvitation, TeamJoinRequest, TeamMembership

# Accepted data-URL prefixes for the team logo and the max total length.
LOGO_DATA_URL_RE = re.compile(r"^data:image/(png|jpeg|jpg|webp|svg\+xml);base64,")
LOGO_MAX_LENGTH = 500000  # ~375 KB once base64 is decoded


class TeamMinimalSerializer(serializers.ModelSerializer):
    """Compact team payload for nested read contexts."""

    class Meta:
        model = Team
        fields = ["id", "name", "language"]
        read_only_fields = fields


class TeamSerializer(serializers.ModelSerializer):
    sport = SportSerializer(read_only=True)
    sport_id = serializers.PrimaryKeyRelatedField(
        source="sport",
        queryset=Sport.objects.all(),
        write_only=True,
    )
    level = LevelSerializer(read_only=True)
    level_id = serializers.PrimaryKeyRelatedField(
        source="level",
        queryset=Level.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )
    owner = CustomUserPublicSerializer(read_only=True)
    managers = CustomUserPublicSerializer(many=True, read_only=True)
    managers_ids = serializers.PrimaryKeyRelatedField(
        source="managers",
        queryset=get_user_model().objects.all(),
        many=True,
        write_only=True,
        required=False,
    )

    class Meta:
        model = Team
        fields = [
            "id",
            "name",
            "sport",
            "sport_id",
            "level",
            "level_id",
            "owner",
            "managers",
            "managers_ids",
            "language",
            "is_active",
            "is_public",
            "logo",
            "roti_enabled",
            "chat_mode",
            "weekly_recap_enabled",
            "attendance_statuses",
            "join_request_policy",
            "notify_managers_on_join_request",
            "timezone",
            "vis_distance",
            "vis_goal",
            "vis_rounds",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "owner", "managers", "created_at", "updated_at"]

    def validate_timezone(self, value):
        # Reject anything that is not a valid IANA zone name. We check
        # against the canonical set first (fast, explicit) and confirm it
        # constructs — guards against platform tzdata gaps.
        if value not in available_timezones():
            raise serializers.ValidationError(
                _("'%(tz)s' is not a valid IANA timezone name.") % {"tz": value},
                code="invalid_timezone",
            )
        try:
            ZoneInfo(value)
        except Exception as exc:  # pragma: no cover - defensive
            raise serializers.ValidationError(
                _("'%(tz)s' is not a valid IANA timezone name.") % {"tz": value},
                code="invalid_timezone",
            ) from exc
        return value

    def validate_logo(self, value):
        if not value:
            return value
        if len(value) > LOGO_MAX_LENGTH:
            raise serializers.ValidationError(
                _("Logo is too large (max %(max)d characters).") % {"max": LOGO_MAX_LENGTH},
                code="logo_too_large",
            )
        if not LOGO_DATA_URL_RE.match(value):
            raise serializers.ValidationError(
                _("Logo must be a base64 data-URL of an image (png, jpeg, jpg, webp or svg+xml)."),
                code="logo_invalid_format",
            )
        return value


class TeamJoinRequestSerializer(serializers.ModelSerializer):
    """Standard serializer for /join-requests/. Mirrors TeamMembershipSerializer's
    pattern of exposing username/fullname denorms so the manager dashboard can
    render the requester without a separate /users/{id}/ fetch."""

    user_username = serializers.CharField(source="user.username", read_only=True)
    user_fullname = serializers.SerializerMethodField()
    responded_by_username = serializers.CharField(
        source="responded_by.username",
        read_only=True,
        allow_null=True,
        default=None,
    )

    class Meta:
        model = TeamJoinRequest
        fields = [
            "id",
            "user",
            "user_username",
            "user_fullname",
            "team",
            "status",
            "message",
            "response_message",
            "requested_at",
            "responded_at",
            "responded_by",
            "responded_by_username",
        ]
        read_only_fields = [
            "id",
            "user",
            "user_username",
            "user_fullname",
            "requested_at",
            "responded_at",
            "responded_by",
            "responded_by_username",
        ]

    @extend_schema_field(serializers.CharField())
    def get_user_fullname(self, obj) -> str:
        user = obj.user
        parts = [p for p in [user.first_name, user.last_name] if p]
        return " ".join(parts) if parts else user.username


# ---------------------------------------------------------------------
# Magic-action endpoints (/api/v1/join-magic/) — input + response shapes
# ---------------------------------------------------------------------


class TeamJoinRequestMagicActionPostSerializer(serializers.Serializer):
    """Body of POST /api/v1/join-magic/. Token is the HMAC-signed link
    received in the manager email."""

    token = serializers.CharField()


class _MagicActionJoinRequestSerializer(serializers.Serializer):
    """Inline shape of the `join_request` field returned by the magic-action
    endpoints. Mirrors the dict assembled in
    TeamJoinRequestMagicActionView._serialize — keep in sync."""

    id = serializers.IntegerField()
    team_id = serializers.IntegerField()
    team_name = serializers.CharField()
    requester_username = serializers.CharField()
    requester_email = serializers.EmailField()
    message = serializers.CharField(allow_blank=True)
    requested_at = serializers.DateTimeField()
    status = serializers.ChoiceField(choices=["pending", "accepted", "rejected", "cancelled"])
    responded_at = serializers.DateTimeField(allow_null=True)
    responded_by = serializers.CharField(allow_null=True, help_text="username")


class TeamJoinRequestMagicActionResponseSerializer(serializers.Serializer):
    """200 response for both GET (preview) and POST (execute) on
    /api/v1/join-magic/. Identical shape — POST just reflects the
    post-action state in `join_request.status`."""

    join_request = _MagicActionJoinRequestSerializer()
    action_proposed = serializers.ChoiceField(choices=["accept", "reject"])
    would_change_decision = serializers.BooleanField(
        help_text=(
            "True iff the action — if executed — would reverse a previous "
            "manager decision (accepted->rejected or rejected->accepted)."
        )
    )
    can_act = serializers.BooleanField(
        help_text="False when the request was cancelled by the requester."
    )


class JoinMagicErrorSerializer(serializers.Serializer):
    """Common 4xx body for the magic-action endpoints. Shape produced by
    tools.exceptions.custom_exception_handler."""

    code = serializers.ChoiceField(
        choices=[
            "invalid_or_expired_token",
            "token_required",
            "request_cancelled",
        ]
    )
    detail = serializers.CharField()


class JoinMagicCancelledResponseSerializer(TeamJoinRequestMagicActionResponseSerializer):
    """409 body when the requester cancelled the request. Combines the error
    envelope (code + detail) with the regular response payload so the
    frontend can show "the user cancelled" + the snapshot of what was
    being attempted."""

    code = serializers.ChoiceField(choices=["request_cancelled"])
    detail = serializers.CharField()


class CreateJoinRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = TeamJoinRequest
        fields = ["id", "team", "message"]
        read_only_fields = ["id"]

    def validate(self, data):
        user = self.context["request"].user
        team = data["team"]
        member_profile = getattr(user, "member_profile", None)
        if (
            member_profile is not None
            and member_profile.memberships.filter(team=team, left_at__isnull=True).exists()
        ):
            raise serializers.ValidationError(
                {"team": _("You are already a member of this team.")},
                code="already_member",
            )
        if TeamJoinRequest.objects.filter(user=user, team=team, status="pending").exists():
            raise serializers.ValidationError(
                {"team": _("You already have a pending request for this team.")},
                code="pending_request_exists",
            )
        if not team.is_active:
            raise serializers.ValidationError(
                {"team": _("This team is inactive.")},
                code="team_not_active",
            )
        if not team.is_public:
            raise serializers.ValidationError(
                {"team": _("This team is not public.")},
                code="team_not_public",
            )
        return data


class TeamInvitationSerializer(serializers.ModelSerializer):
    """List/detail view for managers. Token is intentionally excluded."""

    class Meta:
        model = TeamInvitation
        fields = [
            "id",
            "team",
            "invited_by",
            "member",
            "email",
            "status",
            "created_at",
            "expires_at",
            "completed_at",
        ]
        read_only_fields = [
            "id",
            "invited_by",
            "member",
            "status",
            "created_at",
            "expires_at",
            "completed_at",
        ]


class CreateInvitationSerializer(serializers.Serializer):
    team = serializers.PrimaryKeyRelatedField(queryset=Team.objects.all())
    email = serializers.EmailField()
    firstname = serializers.CharField(max_length=100)
    lastname = serializers.CharField(max_length=100)
    phonenumber = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")

    def validate_team(self, team):
        user = self.context["request"].user
        if not team.is_managed_by(user):
            raise serializers.ValidationError(
                _("You do not manage this team."),
                code="not_a_manager",
            )
        if not team.is_active:
            raise serializers.ValidationError(
                _("This team is inactive."),
                code="team_not_active",
            )
        return team

    def validate(self, data):
        if TeamInvitation.objects.filter(
            email=data["email"],
            team=data["team"],
            status="pending",
        ).exists():
            raise serializers.ValidationError(
                {"email": _("An invitation is already pending for this email on this team.")},
                code="email_already_invited",
            )
        return data


class ValidateInvitationSerializer(serializers.ModelSerializer):
    team_name = serializers.CharField(source="team.name", read_only=True)

    class Meta:
        model = TeamInvitation
        fields = ["email", "team_name", "status", "expires_at"]
        read_only_fields = fields


class TeamMembershipSerializer(serializers.ModelSerializer):
    """Read/write serializer for TeamMembership.

    `team` is set by the view from URL kwargs; only `member` is accepted on POST.
    """

    member_username = serializers.CharField(
        source="member.user.username",
        read_only=True,
        default=None,
    )
    member_fullname = serializers.SerializerMethodField()

    class Meta:
        model = TeamMembership
        fields = [
            "id",
            "team",
            "member",
            "member_username",
            "member_fullname",
            "joined_at",
            "left_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "team",
            "joined_at",
            "left_at",
            "created_at",
            "updated_at",
            "member_username",
            "member_fullname",
        ]

    @extend_schema_field(serializers.CharField())
    def get_member_fullname(self, obj) -> str:
        m = obj.member
        parts = [p for p in [m.firstname, m.lastname] if p]
        return " ".join(parts) if parts else ""


# ---------------------------------------------------------------------
# Team statistics (read-only aggregation) — GET /api/v1/teams/{id}/stats/
# These serializers exist purely to give drf-spectacular a clean,
# explicit schema for the aggregated payload assembled in
# TeamViewSet.stats(); they are never used to deserialize input.
# ---------------------------------------------------------------------


class StatsPeriodSerializer(serializers.Serializer):
    # `from` is a Python keyword, so the field is declared as `from_` and
    # sourced from the dict key "from"; the schema/output key is remapped to
    # "from" in get_fields().
    from_ = serializers.DateField(source="from", help_text="Window start (inclusive).")
    to = serializers.DateField(help_text="Window end (inclusive).")

    def get_fields(self):
        fields = super().get_fields()
        field = fields.pop("from_")
        # Clear the now-redundant source assertion: after rekeying to "from",
        # source == field_name, which DRF forbids declaring explicitly.
        field.source = None
        fields["from"] = field
        return fields


class StatsAttendanceBySessionSerializer(serializers.Serializer):
    event_id = serializers.IntegerField()
    name = serializers.CharField()
    date = serializers.DateField(allow_null=True)
    present = serializers.IntegerField()
    total = serializers.IntegerField()
    rate = serializers.FloatField(allow_null=True)


class StatsAttendanceByMemberSerializer(serializers.Serializer):
    member_id = serializers.IntegerField()
    name = serializers.CharField()
    present = serializers.IntegerField()
    total = serializers.IntegerField()
    rate = serializers.FloatField(allow_null=True)
    last_present_date = serializers.DateField(allow_null=True)


class StatsAttendanceSerializer(serializers.Serializer):
    team_rate = serializers.FloatField(
        allow_null=True,
        help_text="Overall present / expected across the period (null if no expected slots).",
    )
    by_session = StatsAttendanceBySessionSerializer(many=True)
    by_member = StatsAttendanceByMemberSerializer(many=True)


class StatsVolumeByWeekSerializer(serializers.Serializer):
    week_start = serializers.DateField(help_text="Monday (ISO) of the bucketed week.")
    distance = serializers.IntegerField()


class StatsVolumeByMemberSerializer(serializers.Serializer):
    member_id = serializers.IntegerField()
    name = serializers.CharField()
    distance = serializers.IntegerField()


class StatsVolumeSerializer(serializers.Serializer):
    total_distance = serializers.IntegerField()
    by_week = StatsVolumeByWeekSerializer(many=True)
    by_member = StatsVolumeByMemberSerializer(many=True)


class StatsIntensityBySegmentSerializer(serializers.Serializer):
    abv = serializers.CharField()
    label = serializers.CharField(allow_null=True, help_text="Localized segment description.")
    distance = serializers.IntegerField()


class StatsIntensitySerializer(serializers.Serializer):
    by_segment = StatsIntensityBySegmentSerializer(many=True)


class StatsMemberSerializer(serializers.Serializer):
    """The athlete a per-member scoped payload is restricted to. Null on the
    team-aggregate response (no ?member= query param)."""

    id = serializers.IntegerField()
    name = serializers.CharField()


class TeamStatsSerializer(serializers.Serializer):
    """Top-level read-only team statistics payload.

    Aggregates attendance, training volume and intensity for the team's
    events whose date falls within the requested window. Output-only:
    the view assembles a plain dict and passes it through this serializer
    for a clean, typed OpenAPI schema.

    `member` is null for the team aggregate, or `{id, name}` when the
    payload is scoped to a single athlete (via ?member=<id>).
    """

    period = StatsPeriodSerializer()
    member = StatsMemberSerializer(
        allow_null=True,
        help_text="The athlete the payload is scoped to, or null for the team aggregate.",
    )
    attendance = StatsAttendanceSerializer()
    volume = StatsVolumeSerializer()
    intensity = StatsIntensitySerializer()


class CompleteInvitationSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_username(self, username):
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            raise serializers.ValidationError(
                _("This username is already taken."),
                code="username_taken",
            )
        return username

    def validate_password(self, password):
        from django.contrib.auth.password_validation import validate_password

        validate_password(password)
        return password

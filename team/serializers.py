import re
from zoneinfo import ZoneInfo, available_timezones

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from customuser.serializers import CustomUserPublicSerializer
from equipment.models import Equipment
from equipment.serializers import EquipmentMinimalSerializer
from level.models import Level
from level.serializers import LevelSerializer
from place.models import Place
from place.serializers import PlaceMinimalSerializer
from sport.models import Sport
from sport.serializers import SportSerializer

from .models import (
    Team,
    TeamInvitation,
    TeamJoinRequest,
    TeamMembership,
    TeamSport,
    TrainingSlot,
)

# Accepted data-URL prefixes for the team logo and the max total length.
LOGO_DATA_URL_RE = re.compile(r"^data:image/(png|jpeg|jpg|webp|svg\+xml);base64,")
LOGO_MAX_LENGTH = 500000  # ~375 KB once base64 is decoded


class TeamMinimalSerializer(serializers.ModelSerializer):
    """Compact team payload for nested read contexts."""

    class Meta:
        model = Team
        fields = ["id", "name", "language"]
        read_only_fields = fields


class TeamSportReadSerializer(serializers.ModelSerializer):
    """A sport practised by a team, flattened with its default flag and order.

    The id/name/slug are the SPORT's (not the through row's), so the frontend
    can treat the list as sports while still knowing which one is the default.
    """

    id = serializers.IntegerField(source="sport.id", read_only=True)
    name = serializers.CharField(source="sport.name", read_only=True)
    slug = serializers.SlugField(source="sport.slug", read_only=True)

    class Meta:
        model = TeamSport
        fields = ["id", "name", "slug", "is_default", "order"]
        read_only_fields = fields


class TeamSerializer(serializers.ModelSerializer):
    # Multi-sport. `sports` (read) is the full set the team practises, each
    # flattened with its `is_default` flag/order. `sport` (read) stays the
    # single default sport for callers that only want one (e.g. the event form).
    #
    # Writes: `sport_ids` replaces the whole set; `default_sport_id` picks which
    # one is the default (must be in the set). `sport_id` is the legacy
    # single-sport shim (sets/replaces the default) kept for back-compat — all
    # three are handled in create()/update(), not bound to model fields.
    sports = TeamSportReadSerializer(source="team_sports", many=True, read_only=True)
    sport = SportSerializer(read_only=True)
    sport_id = serializers.PrimaryKeyRelatedField(
        queryset=Sport.objects.all(),
        write_only=True,
        required=False,
    )
    sport_ids = serializers.PrimaryKeyRelatedField(
        queryset=Sport.objects.all(),
        many=True,
        write_only=True,
        required=False,
    )
    default_sport_id = serializers.PrimaryKeyRelatedField(
        queryset=Sport.objects.all(),
        write_only=True,
        required=False,
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
    places = PlaceMinimalSerializer(many=True, read_only=True)
    place_ids = serializers.PrimaryKeyRelatedField(
        source="places",
        queryset=Place.objects.all(),
        many=True,
        write_only=True,
        required=False,
    )
    default_place = PlaceMinimalSerializer(read_only=True)
    default_place_id = serializers.PrimaryKeyRelatedField(
        source="default_place",
        queryset=Place.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )
    equipment = EquipmentMinimalSerializer(many=True, read_only=True)
    equipment_ids = serializers.PrimaryKeyRelatedField(
        source="equipment",
        queryset=Equipment.objects.filter(is_active=True),
        many=True,
        write_only=True,
        required=False,
    )
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = Team
        fields = [
            "id",
            "name",
            "sports",
            "sport",
            "sport_id",
            "sport_ids",
            "default_sport_id",
            "level",
            "level_id",
            "owner",
            "managers",
            "managers_ids",
            "default_pool",
            "places",
            "place_ids",
            "default_place",
            "default_place_id",
            "equipment",
            "equipment_ids",
            "logo_url",
            "language",
            "is_active",
            "is_public",
            "logo",
            "roti_enabled",
            "rsvp_enabled",
            "weekly_recap_enabled",
            "attendance_statuses",
            "join_request_policy",
            "topic_creation",
            "notify_managers_on_join_request",
            "notify_coaches_on_note",
            "notify_athlete_on_visible_note",
            "public_sharing_enabled",
            "public_show_distance",
            "public_show_goal",
            "public_show_rounds",
            "timezone",
            "vis_distance",
            "vis_goal",
            "vis_rounds",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "owner",
            "managers",
            "logo_url",
            # default_pool stays the canonical free-text venue but is written
            # via the training-template endpoint or synced from default_place;
            # it is read-only on this serializer to avoid two conflicting write
            # paths.
            "default_pool",
            "created_at",
            "updated_at",
        ]

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_logo_url(self, obj):
        """Absolute URL of the public logo endpoint, or null when there is no
        logo. Lets list/detail consumers render the logo via <img src> instead
        of shipping the base64 data-URL inline (the list drops it — see
        to_representation)."""
        if not obj.logo:
            return None
        path = reverse("team-logo", kwargs={"pk": obj.pk})
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request is not None else path

    def to_representation(self, instance):
        data = super().to_representation(instance)
        view = self.context.get("view")
        if getattr(view, "action", None) == "list":
            # Keep the list payload light: the base64 logo (served via logo_url)
            # and the heavy nested M2M are not used by any list consumer.
            data["logo"] = None
            for field in ("places", "equipment", "attendance_statuses", "default_place"):
                data.pop(field, None)
        return data

    def _apply_default_sport(self, team, sport):
        """Make `sport` the team's default sport (single-sport transition shim):
        flip any other default off, then ensure a single is_default TeamSport."""
        team.team_sports.filter(is_default=True).exclude(sport=sport).update(is_default=False)
        ts, created = TeamSport.objects.get_or_create(
            team=team, sport=sport, defaults={"is_default": True}
        )
        if not created and not ts.is_default:
            ts.is_default = True
            ts.save(update_fields=["is_default"])

    def _sync_sports(self, team, sports, default):
        """Replace the team's sports set with `sports` (Sport instances, in the
        given order), flagging `default` (or the first) as the single default.

        Sports no longer selected are detached. Existing rows are kept (so their
        created_at survives) with refreshed order/default. The default flag is
        cleared first across the board to avoid clashing with the partial-unique
        ``uniq_default_sport_per_team`` constraint mid-update.
        """
        keep_ids = list(dict.fromkeys(s.id for s in sports))  # dedupe, keep order
        default_id = default.id if default is not None else None
        if default_id not in keep_ids:
            default_id = keep_ids[0] if keep_ids else None
        team.team_sports.exclude(sport_id__in=keep_ids).delete()
        team.team_sports.update(is_default=False)
        existing = {ts.sport_id: ts for ts in team.team_sports.all()}
        for order, sid in enumerate(keep_ids):
            is_def = sid == default_id
            ts = existing.get(sid)
            if ts is None:
                TeamSport.objects.create(
                    team=team, sport_id=sid, is_default=is_def, order=order
                )
            else:
                ts.is_default = is_def
                ts.order = order
                ts.save(update_fields=["is_default", "order"])

    def validate(self, data):
        """Cross-field rules for the team's sports.

        A new team needs at least one sport (via `sport_ids` or the legacy
        `sport_id`). When `sport_ids` is given it must be non-empty and any
        `default_sport_id` must be one of them; when only `default_sport_id` is
        given (update), it must already be one of the team's sports.
        """
        sport_ids = data.get("sport_ids")
        default = data.get("default_sport_id")
        if self.instance is None and sport_ids is None and data.get("sport_id") is None:
            raise serializers.ValidationError(
                {"sport_id": _("A team must have at least one sport.")},
                code="sport_required",
            )
        if sport_ids is not None:
            if len(sport_ids) == 0:
                raise serializers.ValidationError(
                    {"sport_ids": _("A team must have at least one sport.")},
                    code="no_sports",
                )
            if default is not None and default not in sport_ids:
                raise serializers.ValidationError(
                    {"default_sport_id": _("The default sport must be one of the team's sports.")},
                    code="default_not_in_sports",
                )
        elif default is not None:
            if self.instance is None or not self.instance.team_sports.filter(
                sport=default
            ).exists():
                raise serializers.ValidationError(
                    {"default_sport_id": _("The default sport must be one of the team's sports.")},
                    code="default_not_in_sports",
                )
        self._validate_managers_owner_only(data)
        self._validate_places_in_sport(data)
        return data

    def _validate_managers_owner_only(self, data):
        """The management roster is the owner's prerogative: a manager who is not
        the owner must not be able to add/remove co-managers (self-escalation).
        `managers_ids` is sourced to `managers`."""
        if self.instance is None or "managers" not in data:
            return
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or self.instance.owner_id != getattr(user, "pk", None):
            raise serializers.ValidationError(
                {"managers_ids": _("Only the team owner can change the team's managers.")},
                code="managers_owner_only",
            )

    def _validate_places_in_sport(self, data):
        """Linked venues must serve at least one of the team's sports — a manager
        cannot attach a venue from an unrelated sport pool. `place_ids` is sourced
        to `places`, `default_place_id` to `default_place`."""
        places = list(data.get("places") or [])
        if "default_place" in data and data["default_place"] is not None:
            places.append(data["default_place"])
        if not places:
            return
        team_sport_ids = self._team_sport_ids(data)
        if not team_sport_ids:
            return
        for place in places:
            place_sport_ids = set(place.sports.values_list("id", flat=True))
            # A venue with no declared sports is unscoped (shared) — allow it.
            if place_sport_ids and place_sport_ids.isdisjoint(team_sport_ids):
                raise serializers.ValidationError(
                    {"place_ids": _("A venue must serve one of the team's sports.")},
                    code="place_not_in_sport",
                )

    def _team_sport_ids(self, data):
        """The set of sport ids in play for this write (new selection or, failing
        that, the team's current sports)."""
        if data.get("sport_ids"):
            return {s.id for s in data["sport_ids"]}
        if data.get("sport_id"):
            return {data["sport_id"].id}
        if self.instance is not None:
            return set(self.instance.sports.values_list("id", flat=True))
        return set()

    def _persist_sports(self, team, sports, default, legacy):
        """Apply whichever sport write path was supplied (set/default/legacy).

        `sport_id`/`sport_ids`/`default_sport_id` are declared fields not bound
        to a model attribute, so the caller MUST pop them out of validated_data
        before super().create()/update() (Team.sport_id is a read-only property)
        and pass them here.
        """
        if sports is not None:
            self._sync_sports(team, sports, default)
        elif default is not None:
            self._apply_default_sport(team, default)
        elif legacy is not None:
            self._apply_default_sport(team, legacy)

    def create(self, validated_data):
        sports = validated_data.pop("sport_ids", None)
        default = validated_data.pop("default_sport_id", None)
        legacy = validated_data.pop("sport_id", None)
        team = super().create(validated_data)
        self._persist_sports(team, sports, default, legacy)
        return team

    def update(self, instance, validated_data):
        """Persist places/default_place + the team's sports, and sync default_pool.

        - sport_ids -> replace the whole sports set (default_sport_id picks the
          default); default_sport_id alone -> just flip the default; sport_id ->
          legacy single-sport default shim.
        - default_place non-null -> default_pool = place.name (keeps the AI plan
          path, which reads default_pool, working unchanged) and the place is
          ensured to be one of the team's linked places.
        - default_place null      -> clear default_place; default_pool left as-is.
        """
        sports = validated_data.pop("sport_ids", None)
        default = validated_data.pop("default_sport_id", None)
        legacy = validated_data.pop("sport_id", None)
        if "default_place" in validated_data and validated_data["default_place"] is not None:
            validated_data["default_pool"] = validated_data["default_place"].name
        team = super().update(instance, validated_data)
        # A team's default must be one of its venues — auto-link it.
        if team.default_place_id and not team.places.filter(pk=team.default_place_id).exists():
            team.places.add(team.default_place_id)
        self._persist_sports(team, sports, default, legacy)
        return team

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


# ---------------------------------------------------------------------
# Weekly training template — GET/PUT /api/v1/teams/{id}/training-template/
# ---------------------------------------------------------------------


class TrainingSlotSerializer(serializers.ModelSerializer):
    """One weekly training slot (weekday + start/end time).

    weekday uses Python's date.weekday() convention: Monday=0 … Sunday=6.
    """

    place = PlaceMinimalSerializer(read_only=True)
    place_id = serializers.PrimaryKeyRelatedField(
        source="place",
        queryset=Place.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )
    # Multi-sport: the slot's sport (one of the team's sports); the plan
    # generator stamps each generated session's sport from it. Optional on
    # write — the view defaults it to the team's default sport on create.
    sport = SportSerializer(read_only=True)
    sport_id = serializers.PrimaryKeyRelatedField(
        source="sport",
        queryset=Sport.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = TrainingSlot
        fields = ["id", "weekday", "hour_start", "hour_end", "place", "place_id", "sport", "sport_id"]
        read_only_fields = ["id"]

    def validate_weekday(self, value):
        if value < 0 or value > 6:
            raise serializers.ValidationError(
                _("weekday must be between 0 (Monday) and 6 (Sunday)."),
                code="weekday_out_of_range",
            )
        return value

    def validate(self, data):
        # Partial-safe: on PATCH only some fields are present, so fall back to
        # the existing instance values when comparing the time range.
        start = data.get("hour_start", getattr(self.instance, "hour_start", None))
        end = data.get("hour_end", getattr(self.instance, "hour_end", None))
        if start is not None and end is not None and end <= start:
            raise serializers.ValidationError(
                {"hour_end": _("hour_end must be after hour_start.")},
                code="invalid_slot_range",
            )
        return data


class TrainingTemplateSerializer(serializers.Serializer):
    """The team's reusable weekly training template.

    Body of PUT and shape of GET on /teams/{id}/training-template/. PUT
    atomically REPLACES the template: existing slots are deleted and
    recreated from `slots`, and default_pool/season_start/season_end are
    set on the team.
    """

    slots = TrainingSlotSerializer(many=True)
    default_pool = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )
    season_start = serializers.DateField(required=False, allow_null=True, default=None)
    season_end = serializers.DateField(required=False, allow_null=True, default=None)


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

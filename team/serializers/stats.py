from rest_framework import serializers

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
    streak = serializers.IntegerField(
        allow_null=True,
        required=False,
        help_text="Current consecutive-present streak (per-athlete scope only; null on the team aggregate).",
    )


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


class StatsRotiPointSerializer(serializers.Serializer):
    event_id = serializers.IntegerField()
    name = serializers.CharField()
    date = serializers.DateField(allow_null=True)
    score = serializers.IntegerField(help_text="ROTI score 1-5 for that session.")


class StatsRotiSerializer(serializers.Serializer):
    """Per-athlete ROTI (return-on-training-investment) trend over the window.
    Empty series on the team aggregate (ROTI is per athlete)."""

    series = StatsRotiPointSerializer(many=True)
    average = serializers.FloatField(allow_null=True)
    count = serializers.IntegerField()


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
    roti = StatsRotiSerializer()


# ---------------------------------------------------------------------
# Roster history — GET /api/v1/teams/{id}/roster-history/
# ---------------------------------------------------------------------


class RosterHistoryEntrySerializer(serializers.Serializer):
    """One membership period (active or past) for the roster-history timeline."""

    member_id = serializers.IntegerField()
    name = serializers.CharField()
    joined_at = serializers.DateTimeField()
    left_at = serializers.DateTimeField(allow_null=True)
    active = serializers.BooleanField()


class RosterHistoryResponseSerializer(serializers.Serializer):
    """Object wrapper for the roster-history list.

    A plain ``many=True`` response on this (paginated) ViewSet would make
    drf-spectacular emit a paginated envelope the view does not actually return;
    wrapping in ``{entries: [...]}`` keeps the schema honest (single object)."""

    entries = RosterHistoryEntrySerializer(many=True)


# ---------------------------------------------------------------------
# RSVP reliability — GET /api/v1/teams/{id}/rsvp-reliability/
# ---------------------------------------------------------------------


class RsvpReliabilityEntrySerializer(serializers.Serializer):
    """One athlete's RSVP reliability over the window."""

    member_id = serializers.IntegerField()
    name = serializers.CharField()
    going = serializers.IntegerField(help_text="Events the athlete RSVP'd GOING to.")
    shows = serializers.IntegerField(help_text="Of those, where they were present.")
    no_shows = serializers.IntegerField(help_text="GOING but not present (absent / no record).")
    reliability = serializers.FloatField(allow_null=True, help_text="shows / going (0..1).")


class RsvpReliabilityResponseSerializer(serializers.Serializer):
    """Wrapper object (keeps the schema non-paginated; see RosterHistory)."""

    period = StatsPeriodSerializer()
    entries = RsvpReliabilityEntrySerializer(many=True)


# ---------------------------------------------------------------------
# ROTI drift — GET /api/v1/teams/{id}/roti-drift/
# ---------------------------------------------------------------------


class RotiDriftEntrySerializer(serializers.Serializer):
    """One athlete's mean ROTI vs the squad over the window."""

    member_id = serializers.IntegerField()
    name = serializers.CharField()
    average = serializers.FloatField(help_text="Athlete's mean ROTI (1..5).")
    count = serializers.IntegerField(help_text="Number of ROTI scores.")
    delta = serializers.FloatField(help_text="average - squad_average.")
    flag = serializers.CharField(help_text="One of: high, low, normal.")


class RotiDriftResponseSerializer(serializers.Serializer):
    """Squad mean + per-athlete drift (non-paginated wrapper)."""

    period = StatsPeriodSerializer()
    squad_average = serializers.FloatField(allow_null=True)
    count = serializers.IntegerField()
    entries = RotiDriftEntrySerializer(many=True)

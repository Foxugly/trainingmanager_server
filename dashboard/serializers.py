"""Read-only output serializers for the dashboard summary endpoint.

These exist purely to give drf-spectacular a clean, typed schema for the
aggregate payload assembled in DashboardSummaryView. They never deserialize
input — the view builds plain dicts and passes them through for serialization.
"""

from rest_framework import serializers

from attendance.serializers import AttendanceStatusSerializer
from place.serializers import PlaceMinimalSerializer


class DashboardEventSerializer(serializers.Serializer):
    """The minimal Event shape the dashboard lists render."""

    id = serializers.IntegerField()
    name = serializers.CharField()
    date = serializers.DateField(allow_null=True)
    hour_start = serializers.TimeField(allow_null=True)
    hour_end = serializers.TimeField(allow_null=True)
    location = serializers.CharField(allow_blank=True)
    place = PlaceMinimalSerializer(allow_null=True)


class DashboardManagedTeamSerializer(serializers.Serializer):
    """Per-team counts for a team the caller manages. Keyed by team_id so the
    frontend joins it to the full Team object it already holds."""

    team_id = serializers.IntegerField()
    programs_active = serializers.IntegerField()
    events_next_7d = serializers.IntegerField()
    members_count = serializers.IntegerField()


class DashboardMemberTeamSerializer(serializers.Serializer):
    """Per-team counts for a team the caller is an athlete-member of."""

    team_id = serializers.IntegerField()
    members_count = serializers.IntegerField()
    my_member_id = serializers.IntegerField(
        allow_null=True,
        help_text="The caller's athlete Member id in this team (null if unresolved).",
    )


class DashboardEventItemSerializer(serializers.Serializer):
    """An event row with its denormalized team/program names (no client join)."""

    event = DashboardEventSerializer()
    team_id = serializers.IntegerField()
    team_name = serializers.CharField()
    program_id = serializers.IntegerField(allow_null=True)
    program_name = serializers.CharField(allow_blank=True)


class DashboardHistoryItemSerializer(serializers.Serializer):
    """One past event the caller has an attendance row for."""

    event = DashboardEventSerializer()
    team_id = serializers.IntegerField()
    team_name = serializers.CharField()
    program_id = serializers.IntegerField(allow_null=True)
    program_name = serializers.CharField(allow_blank=True)
    attendance_id = serializers.IntegerField()
    status_code = serializers.CharField()
    status = AttendanceStatusSerializer(allow_null=True)


class DashboardSummarySerializer(serializers.Serializer):
    """Top-level aggregate payload for GET /dashboard/summary/.

    Replaces the previous per-team/per-program/per-event/per-attendance HTTP
    fan-out (50-100+ requests) with a single call. The frontend still fetches
    the full Team list once for names/sport and the stats/perf panels; this
    payload provides every computed count and list the dashboard renders.
    """

    coach_teams = DashboardManagedTeamSerializer(many=True)
    member_teams = DashboardMemberTeamSerializer(many=True)
    coach_upcoming = DashboardEventItemSerializer(many=True)
    coach_upcoming_total = serializers.IntegerField()
    coach_attendance_pending = DashboardEventItemSerializer(many=True)
    coach_pending_truncated = serializers.BooleanField()
    member_upcoming = DashboardEventItemSerializer(many=True)
    member_upcoming_total = serializers.IntegerField()
    member_attendance_history = DashboardHistoryItemSerializer(many=True)
    member_history_truncated = serializers.BooleanField()

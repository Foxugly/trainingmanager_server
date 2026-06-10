"""Single aggregate endpoint backing the SPA dashboard.

The dashboard used to fan out over HTTP: teams -> per-team programs ->
per-program events -> per-event attendance + per-team memberships (50-100+
requests per load). This view computes every count and list the dashboard
renders in one request, with a bounded number of DB queries (annotations,
not per-row loops).

Date windows mirror the old client logic. "today" is taken from the
?today=YYYY-MM-DD query param (the browser's local date, preserving the
previous per-user behaviour) and falls back to the server's local date.
"""

from datetime import date as date_cls
from datetime import timedelta

from django.db.models import Count
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from attendance.models import Attendance
from event.models import Event
from program.models import Program
from team.models import TeamMembership
from team.queries import managed_teams, user_member_teams

from .serializers import DashboardSummarySerializer

# Mirror the caps the frontend used so behaviour is unchanged.
NEXT_7D = 7
UPCOMING_DAYS = 14
PENDING_AUDIT_CAP = 30
HISTORY_DISPLAY_LIMIT = 20


def _parse_today(request):
    raw = request.query_params.get("today")
    if raw:
        try:
            y, m, d = (int(p) for p in raw.split("-"))
            return date_cls(y, m, d)
        except (ValueError, TypeError):
            pass
    from django.utils import timezone

    return timezone.localdate()


def _event_dict(event):
    place = event.place
    return {
        "id": event.id,
        "name": event.name,
        "date": event.date,
        "hour_start": event.hour_start,
        "hour_end": event.hour_end,
        "location": event.location or "",
        "place": {"id": place.id, "name": place.name} if place is not None else None,
    }


def _event_item(event):
    program = event.refer_program
    team = program.team if program is not None else None
    return {
        "event": _event_dict(event),
        "team_id": team.id if team is not None else None,
        "team_name": team.name if team is not None else "",
        "program_id": program.id if program is not None else None,
        "program_name": program.name if program is not None else "",
    }


class DashboardSummaryView(APIView):
    """GET /api/v1/dashboard/summary/ — one-shot aggregate for the dashboard."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Aggregate dashboard payload (replaces the per-resource fan-out)",
        parameters=[
            OpenApiParameter(
                name="today",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Caller's local date (YYYY-MM-DD) for the date windows; "
                "defaults to the server's local date.",
            )
        ],
        responses=DashboardSummarySerializer,
    )
    def get(self, request):
        user = request.user
        today = _parse_today(request)
        next7 = today + timedelta(days=NEXT_7D)
        next14 = today + timedelta(days=UPCOMING_DAYS)

        managed = list(managed_teams(user))
        managed_ids = {t.id for t in managed}
        member = [t for t in user_member_teams(user) if t.id not in managed_ids]
        member_ids = {t.id for t in member}

        payload = {
            "coach_teams": self._coach_teams(managed_ids, today, next7),
            "member_teams": self._member_teams(user, member_ids),
            "coach_upcoming": [],
            "coach_upcoming_total": 0,
            "coach_attendance_pending": [],
            "coach_pending_truncated": False,
            "member_upcoming": [],
            "member_upcoming_total": 0,
            "member_attendance_history": [],
            "member_history_truncated": False,
        }

        if managed_ids:
            upcoming = self._upcoming(managed_ids, today, next14)
            payload["coach_upcoming"] = upcoming
            payload["coach_upcoming_total"] = len(upcoming)
            pending, truncated = self._coach_pending(managed_ids, today)
            payload["coach_attendance_pending"] = pending
            payload["coach_pending_truncated"] = truncated

        if member_ids:
            upcoming = self._upcoming(member_ids, today, next14)
            payload["member_upcoming"] = upcoming
            payload["member_upcoming_total"] = len(upcoming)
            history, truncated = self._member_history(user, member_ids, today)
            payload["member_attendance_history"] = history
            payload["member_history_truncated"] = truncated

        return Response(DashboardSummarySerializer(payload).data)

    # -- per-team counts ----------------------------------------------------

    @staticmethod
    def _coach_teams(team_ids, today, next7):
        if not team_ids:
            return []
        programs = dict(
            Program.objects.filter(team_id__in=team_ids, is_active=True)
            .values_list("team_id")
            .annotate(c=Count("id"))
        )
        events7 = dict(
            Event.objects.filter(
                refer_program__team_id__in=team_ids,
                refer_program__is_active=True,
                date__gte=today,
                date__lt=next7,
            )
            .values_list("refer_program__team_id")
            .annotate(c=Count("id"))
        )
        members = dict(
            TeamMembership.objects.filter(team_id__in=team_ids, left_at__isnull=True)
            .values_list("team_id")
            .annotate(c=Count("id"))
        )
        return [
            {
                "team_id": tid,
                "programs_active": programs.get(tid, 0),
                "events_next_7d": events7.get(tid, 0),
                "members_count": members.get(tid, 0),
            }
            for tid in team_ids
        ]

    @staticmethod
    def _member_teams(user, team_ids):
        if not team_ids:
            return []
        members = dict(
            TeamMembership.objects.filter(team_id__in=team_ids, left_at__isnull=True)
            .values_list("team_id")
            .annotate(c=Count("id"))
        )
        # The caller's own active member id per team.
        my_ids = dict(
            TeamMembership.objects.filter(
                team_id__in=team_ids, member__user=user, left_at__isnull=True
            ).values_list("team_id", "member_id")
        )
        return [
            {
                "team_id": tid,
                "members_count": members.get(tid, 0),
                "my_member_id": my_ids.get(tid),
            }
            for tid in team_ids
        ]

    # -- event lists --------------------------------------------------------

    @staticmethod
    def _upcoming(team_ids, today, next14):
        events = (
            Event.objects.filter(
                refer_program__team_id__in=team_ids,
                refer_program__is_active=True,
                date__gte=today,
                date__lt=next14,
            )
            .select_related("refer_program__team", "place")
            .order_by("date", "hour_start")
        )
        return [_event_item(e) for e in events]

    @staticmethod
    def _coach_pending(team_ids, today):
        events = (
            Event.objects.filter(
                refer_program__team_id__in=team_ids,
                refer_program__is_active=True,
                date__lt=today,
            )
            .annotate(
                n_members=Count("members", distinct=True),
                n_att=Count("attendances", distinct=True),
            )
            .filter(n_members__gt=0, n_att=0)
            .select_related("refer_program__team", "place")
            .order_by("-date")
        )
        # +1 to detect truncation past the cap, like the old client audit.
        rows = list(events[: PENDING_AUDIT_CAP + 1])
        truncated = len(rows) > PENDING_AUDIT_CAP
        return [_event_item(e) for e in rows[:PENDING_AUDIT_CAP]], truncated

    @staticmethod
    def _member_history(user, team_ids, today):
        rows = (
            Attendance.objects.filter(
                member__user=user,
                event__date__lt=today,
                event__refer_program__team_id__in=team_ids,
            )
            .select_related(
                "status", "event", "event__refer_program__team", "event__place"
            )
            .order_by("-event__date", "-created_at")
        )
        sliced = list(rows[: HISTORY_DISPLAY_LIMIT + 1])
        truncated = len(sliced) > HISTORY_DISPLAY_LIMIT
        items = []
        for att in sliced[:HISTORY_DISPLAY_LIMIT]:
            item = _event_item(att.event)
            item.update(
                {
                    "attendance_id": att.id,
                    "status_code": att.status.code,
                    "status": att.status,
                }
            )
            items.append(item)
        return items, truncated

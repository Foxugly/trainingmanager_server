import logging

from django.db.models import Avg, Count
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from event.models import Event
from tools.exceptions import NotAnAthleteMember, RotiDisabled

from .models import Roti
from .serializers import RotiSummarySerializer, RotiUpsertSerializer

logger = logging.getLogger(__name__)


def _team_for_event(event):
    """Resolve the Team owning an event (via its program), or None."""
    program = getattr(event, "refer_program", None)
    return program.team if program else None


class IsTeamMemberOrCoachRoti(BasePermission):
    """Permissions for the nested ROTI endpoint under an Event.

    - team owner / managers: always allowed (read aggregate).
    - athlete (member.user == request.user, active membership): allowed
      (read own my_score / upsert own score).
    - non-team users: 403.

    The enabled-toggle (team.roti_enabled) is enforced in the view for
    writes, not here, so a clean 403/400 with a specific code is returned.
    """

    message = _("You don't have permission to access this event's ROTI.")

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        event = view.get_event()
        team = _team_for_event(event)
        if team is None:
            return False

        if team.is_managed_by(request.user):
            return True

        return team.memberships.filter(
            member__user_id=request.user.pk, left_at__isnull=True
        ).exists()


class RotiViewSet(viewsets.ViewSet):
    """Per-athlete session difficulty rating (ROTI), nested under an event.

    URL: /api/v1/events/{event_pk}/roti/

    - GET  -> summary object for the caller (incl. my_score).
    - PUT  -> upsert the caller's own score (1..5).
    - GET .../summary/ -> manager-facing aggregate (same shape as GET).
    """

    permission_classes = [IsAuthenticated, IsTeamMemberOrCoachRoti]

    def get_event(self):
        # Memoized per request: permission check + get_queryset + perform_*
        # all resolve the same URL pk. ``None`` is a valid result (no
        # event_pk), so use hasattr as the "resolved" sentinel.
        if not hasattr(self, "_event"):
            event_pk = self.kwargs.get("event_pk")
            self._event = get_object_or_404(Event, pk=event_pk) if event_pk else None
        return self._event

    def _resolve_member(self, team, user):
        """The caller's active Member within the event's team, or None.

        Mirrors how AttendanceViewSet scopes athlete access:
        team.memberships filtered on member__user_id + left_at IS NULL.
        """
        membership = (
            team.memberships.filter(member__user_id=user.pk, left_at__isnull=True)
            .select_related("member")
            .first()
        )
        return membership.member if membership else None

    def _summary(self, event, team, user):
        qs = Roti.objects.filter(event=event)
        agg = qs.aggregate(average=Avg("score"), count=Count("id"))
        distribution = {str(i): 0 for i in range(1, 6)}
        for row in qs.values("score").annotate(n=Count("id")):
            distribution[str(row["score"])] = row["n"]

        my_score = None
        member = self._resolve_member(team, user)
        if member is not None:
            own = qs.filter(member=member).values_list("score", flat=True).first()
            my_score = own

        return {
            "average": agg["average"],
            "count": agg["count"] or 0,
            "distribution": distribution,
            "my_score": my_score,
        }

    @extend_schema(
        operation_id="events_roti_retrieve",
        summary="Get ROTI summary for an event (incl. my_score)",
        description=(
            "Returns the aggregate ROTI for the event "
            "(average, count, distribution) plus the caller's own score "
            "(my_score). Coaches and athlete-members of the team may read."
        ),
        responses={200: RotiSummarySerializer},
    )
    def list(self, request, event_pk=None):
        event = self.get_event()
        team = _team_for_event(event)
        if team is None:
            raise ValidationError(
                detail={"event": _("Event has no team (orphan event).")},
                code="event_orphan",
            )
        data = self._summary(event, team, request.user)
        return Response(RotiSummarySerializer(data).data)

    @extend_schema(
        operation_id="events_roti_update",
        summary="Upsert the caller's own ROTI score (1..5)",
        description=(
            "Athlete-only: resolves the caller's Member within the event's "
            "team and update_or_creates their Roti(event, member) with the "
            "given score. Returns 403 if the team has roti_enabled=False or "
            "the caller is not an athlete-member; 400 if the score is out of "
            "range."
        ),
        request=RotiUpsertSerializer,
        responses={
            200: RotiSummarySerializer,
            400: OpenApiResponse(description="Score out of range (1..5)"),
            403: OpenApiResponse(description="ROTI disabled or caller not an athlete-member"),
        },
    )
    def update(self, request, event_pk=None):
        event = self.get_event()
        team = _team_for_event(event)
        if team is None:
            raise ValidationError(
                detail={"event": _("Event has no team (orphan event).")},
                code="event_orphan",
            )

        if not team.roti_enabled:
            raise RotiDisabled()

        member = self._resolve_member(team, request.user)
        if member is None:
            raise NotAnAthleteMember()

        serializer = RotiUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        score = serializer.validated_data["score"]

        Roti.objects.update_or_create(
            event=event,
            member=member,
            defaults={"score": score},
        )

        data = self._summary(event, team, request.user)
        return Response(RotiSummarySerializer(data).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="events_roti_summary",
        summary="ROTI aggregate summary for an event (managers)",
        description=(
            "Same shape as GET .../roti/. Provided as an explicit endpoint "
            "for the manager dashboard."
        ),
        responses={200: RotiSummarySerializer},
    )
    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request, event_pk=None):
        event = self.get_event()
        team = _team_for_event(event)
        if team is None:
            raise ValidationError(
                detail={"event": _("Event has no team (orphan event).")},
                code="event_orphan",
            )
        data = self._summary(event, team, request.user)
        return Response(RotiSummarySerializer(data).data)

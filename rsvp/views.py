import logging

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from attendance.models import Attendance, AttendanceStatus
from event.models import Event
from tools.exceptions import NotAnAthleteMember, RsvpDisabled

from .models import Rsvp, RsvpStatus
from .serializers import (
    RsvpApplyToAttendanceResultSerializer,
    RsvpSummarySerializer,
    RsvpUpsertSerializer,
)

logger = logging.getLogger(__name__)


def _team_for_event(event):
    """Resolve the Team owning an event (via its program), or None."""
    program = getattr(event, "refer_program", None)
    return program.team if program else None


class IsTeamMemberOrCoachRsvp(BasePermission):
    """Permissions for the nested RSVP endpoint under an Event.

    - team owner / managers: always allowed (read aggregate, apply).
    - athlete (member.user == request.user, active membership): allowed
      (read own my_status / upsert own status).
    - non-team users: 403.

    The enabled-toggle (team.rsvp_enabled) is enforced in the view for
    writes, not here, so a clean 403 with a specific code is returned.
    """

    message = _("You don't have permission to access this event's RSVP.")

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


class RsvpViewSet(viewsets.ViewSet):
    """Per-athlete availability (RSVP), nested under an event.

    URL: /api/v1/events/{event_pk}/rsvp/

    - GET  -> summary object for the caller (incl. my_status).
    - PUT  -> upsert the caller's own status (going/maybe/not_going).
    - POST .../apply_to_attendance/ -> managers pre-fill attendance from RSVPs.
    """

    permission_classes = [IsAuthenticated, IsTeamMemberOrCoachRsvp]

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

        Mirrors RotiViewSet / AttendanceViewSet: team.memberships filtered
        on member__user_id + left_at IS NULL.
        """
        membership = (
            team.memberships.filter(member__user_id=user.pk, left_at__isnull=True)
            .select_related("member")
            .first()
        )
        return membership.member if membership else None

    def _summary(self, event, team, user):
        rsvps = list(
            Rsvp.objects.filter(event=event).select_related("member")
        )
        status_by_member = {r.member_id: r.status for r in rsvps}

        counts = {
            "going": 0,
            "maybe": 0,
            "not_going": 0,
            "no_response": 0,
        }
        for r in rsvps:
            if r.status in counts:
                counts[r.status] += 1

        active_members = list(team.active_members)
        total_members = len(active_members)
        responded_member_ids = set(status_by_member.keys())
        no_response = sum(
            1 for m in active_members if m.pk not in responded_member_ids
        )
        counts["no_response"] = no_response

        member = self._resolve_member(team, user)
        my_status = status_by_member.get(member.pk) if member is not None else None

        by_member = []
        if team.is_managed_by(user):
            for m in active_members:
                by_member.append(
                    {
                        "member_id": m.pk,
                        "name": m.get_fullname(),
                        "status": status_by_member.get(m.pk),
                    }
                )

        return {
            "counts": counts,
            "total_members": total_members,
            "my_status": my_status,
            "by_member": by_member,
        }

    @extend_schema(
        operation_id="events_rsvp_retrieve",
        summary="Get RSVP summary for an event (incl. my_status)",
        description=(
            "Returns the aggregate availability for the event (counts, "
            "total_members) plus the caller's own status (my_status). "
            "Managers additionally get the per-member breakdown (by_member); "
            "athletes get an empty by_member. Coaches and athlete-members of "
            "the team may read."
        ),
        responses={200: RsvpSummarySerializer},
    )
    def summary(self, request, event_pk=None):
        event = self.get_event()
        team = _team_for_event(event)
        if team is None:
            raise ValidationError(
                detail={"event": _("Event has no team (orphan event).")},
                code="event_orphan",
            )
        data = self._summary(event, team, request.user)
        return Response(RsvpSummarySerializer(data).data)

    @extend_schema(
        operation_id="events_rsvp_update",
        summary="Upsert the caller's own RSVP (going/maybe/not_going)",
        description=(
            "Athlete-only: resolves the caller's Member within the event's "
            "team and update_or_creates their Rsvp(event, member) with the "
            "given status. Returns 403 if the team has rsvp_enabled=False or "
            "the caller is not an athlete-member; 400 if the status is invalid."
        ),
        request=RsvpUpsertSerializer,
        responses={
            200: RsvpSummarySerializer,
            400: OpenApiResponse(description="Invalid status"),
            403: OpenApiResponse(description="RSVP disabled or caller not an athlete-member"),
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

        if not team.rsvp_enabled:
            raise RsvpDisabled()

        member = self._resolve_member(team, request.user)
        if member is None:
            raise NotAnAthleteMember()

        serializer = RsvpUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rsvp_status = serializer.validated_data["status"]

        Rsvp.objects.update_or_create(
            event=event,
            member=member,
            defaults={"status": rsvp_status},
        )

        data = self._summary(event, team, request.user)
        return Response(RsvpSummarySerializer(data).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="events_rsvp_apply_to_attendance",
        summary="Pre-fill attendance from RSVPs (managers only)",
        description=(
            "Managers/owner only. For each member with an RSVP on the event, "
            "upserts their Attendance: GOING -> the 'present' AttendanceStatus, "
            "NOT_GOING -> 'absent'. MAYBE is skipped (attendance left "
            "untouched). RSVPs whose target status code is not seeded are also "
            "skipped. Returns how many rows were applied and skipped."
        ),
        request=None,
        responses={
            200: RsvpApplyToAttendanceResultSerializer,
            403: OpenApiResponse(description="Not a team manager"),
        },
    )
    @action(detail=False, methods=["post"], url_path="apply_to_attendance")
    def apply_to_attendance(self, request, event_pk=None):
        event = self.get_event()
        team = _team_for_event(event)
        if team is None:
            raise ValidationError(
                detail={"event": _("Event has no team (orphan event).")},
                code="event_orphan",
            )
        if not team.is_managed_by(request.user):
            raise PermissionDenied(
                _("Only team owner and managers can apply RSVPs to attendance.")
            )

        # Map RSVP status -> AttendanceStatus code. MAYBE is intentionally
        # absent (skipped, never overwrites an existing attendance).
        status_code_for_rsvp = {
            RsvpStatus.GOING: "present",
            RsvpStatus.NOT_GOING: "absent",
        }
        status_objs = {
            s.code: s
            for s in AttendanceStatus.objects.filter(
                code__in=set(status_code_for_rsvp.values()), is_active=True
            )
        }

        rsvps = Rsvp.objects.filter(event=event)
        applied = 0
        skipped = 0
        with transaction.atomic():
            for rsvp in rsvps:
                code = status_code_for_rsvp.get(rsvp.status)
                attendance_status = status_objs.get(code) if code else None
                if attendance_status is None:
                    # MAYBE, or the target status code isn't seeded -> skip.
                    skipped += 1
                    continue
                Attendance.objects.update_or_create(
                    event=event,
                    member_id=rsvp.member_id,
                    defaults={"status": attendance_status},
                )
                applied += 1

        return Response(
            RsvpApplyToAttendanceResultSerializer(
                {"applied": applied, "skipped": skipped}
            ).data
        )

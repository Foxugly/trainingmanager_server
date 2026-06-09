import logging

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.response import Response

from event.models import Event
from tools.mixins import SoftDeleteIncludeInactiveModelViewSet
from tools.openapi import INCLUDE_INACTIVE_PARAM
from tools.permissions import AdminWriteAuthRead

from .models import Attendance, AttendanceStatus
from .serializers import (
    AttendanceBulkSerializer,
    AttendanceSerializer,
    AttendanceStatusAdminSerializer,
    AttendanceStatusSerializer,
)

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(
        summary="List attendance statuses (public flavor)",
        description=(
            "Returns the public AttendanceStatus serializer with localized "
            "'label'. Available to all authenticated users. Pass "
            "?include_inactive=true (staff only) to include soft-deleted rows."
        ),
        responses=AttendanceStatusSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    retrieve=extend_schema(
        summary="Retrieve attendance status (admin flavor for staff)",
        responses=AttendanceStatusAdminSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    create=extend_schema(
        summary="Create attendance status (staff only)",
        request=AttendanceStatusAdminSerializer,
        responses=AttendanceStatusAdminSerializer,
    ),
    update=extend_schema(
        request=AttendanceStatusAdminSerializer,
        responses=AttendanceStatusAdminSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    partial_update=extend_schema(
        request=AttendanceStatusAdminSerializer,
        responses=AttendanceStatusAdminSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    destroy=extend_schema(
        summary="Soft delete attendance status (staff only)",
        description="Sets is_active=False; does not hard delete.",
    ),
)
class AttendanceStatusViewSet(SoftDeleteIncludeInactiveModelViewSet):
    """CRUD on the AttendanceStatus referential.

    Read: any authenticated user (default queryset filters is_active=True).
    Write: staff only. Soft delete bumps updated_at.
    """

    permission_classes = [AdminWriteAuthRead]
    filterset_fields = ["is_active", "is_default"]
    search_fields = ["code"]
    ordering_fields = ["order", "code"]
    ordering = ["order", "code"]
    soft_delete_fields = ("is_active", "updated_at")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return AttendanceStatus.objects.none()
        return self._apply_include_inactive_filter(AttendanceStatus.objects.all())

    def get_serializer_class(self):
        user = self.request.user
        if (
            user.is_authenticated
            and user.is_staff
            and self.action in ("create", "update", "partial_update", "retrieve")
        ):
            return AttendanceStatusAdminSerializer
        return AttendanceStatusSerializer


class IsTeamCoachOrReadOwnAttendance(BasePermission):
    """Permissions for nested attendance under an Event.

    Read (SAFE_METHODS):
      - team owner / managers: full access to all rows of the event
      - athlete (member.user == request.user) of an active membership:
        read access to own attendance only (filtered in get_queryset)
      - non-team users: 403

    Write (POST/PUT/PATCH/DELETE):
      - team owner / managers only

    The view must implement get_event() to expose the URL context.
    """

    message = _("You don't have permission to access this event's attendance.")

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        event = view.get_event()
        program = getattr(event, "refer_program", None)
        team = program.team if program else None
        if team is None:
            return False

        if team.is_managed_by(request.user):
            return True

        if request.method in SAFE_METHODS:
            return team.memberships.filter(
                member__user_id=request.user.pk, left_at__isnull=True
            ).exists()
        return False

    def has_object_permission(self, request, view, obj):
        event = view.get_event()
        program = getattr(event, "refer_program", None)
        team = program.team if program else None
        if team is None:
            return False

        if team.is_managed_by(request.user):
            return True

        if request.method in SAFE_METHODS:
            return obj.member.user_id == request.user.pk
        return False


@extend_schema_view(
    list=extend_schema(
        summary="List attendance for an event",
        description=(
            "Returns all attendance rows. Coaches see everyone; athletes see only their own row."
        ),
    ),
    retrieve=extend_schema(summary="Retrieve a single attendance row"),
    create=extend_schema(
        summary="Create an attendance row (coach only)",
        description=(
            "Member must be in the team via an active TeamMembership. "
            "Returns 400 if a row already exists for (event, member) — use PATCH."
        ),
    ),
    update=extend_schema(
        summary="Replace attendance row (coach only)",
        description=(
            "Replace an attendance row. `member` is read-only after create "
            "and is silently ignored on update — to reassign attendance, "
            "DELETE the row and create a new one."
        ),
    ),
    partial_update=extend_schema(
        summary="Update attendance status (coach only)",
        description=(
            "Update one or more fields of an attendance row. `member` is "
            "read-only after create and is silently ignored — to reassign "
            "attendance, DELETE the row and create a new one."
        ),
    ),
    destroy=extend_schema(summary="Delete an attendance row (coach only)"),
)
class AttendanceViewSet(viewsets.ModelViewSet):
    """CRUD on event attendance.

    URL: /api/v1/events/{event_pk}/attendance/
    """

    serializer_class = AttendanceSerializer
    permission_classes = [IsAuthenticated, IsTeamCoachOrReadOwnAttendance]

    def get_event(self):
        # Memoized per request: permission check + get_queryset + perform_*
        # all call this for the same URL pk. ``None`` is a valid result (no
        # event_pk), so use a sentinel to distinguish "not yet resolved".
        if not hasattr(self, "_event"):
            event_pk = self.kwargs.get("event_pk")
            self._event = get_object_or_404(Event, pk=event_pk) if event_pk else None
        return self._event

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Attendance.objects.none()

        event = self.get_event()
        if event is None:
            return Attendance.objects.none()

        qs = Attendance.objects.filter(event=event).select_related(
            "member", "member__user", "status"
        )

        program = getattr(event, "refer_program", None)
        team = program.team if program else None
        user = self.request.user
        if team is not None and not team.is_managed_by(user):
            qs = qs.filter(member__user_id=user.pk)
        return qs

    def perform_create(self, serializer):
        event = self.get_event()
        program = getattr(event, "refer_program", None)
        team = program.team if program else None
        if team is None:
            raise ValidationError(
                detail={"event": _("Event has no team (orphan event).")},
                code="event_orphan",
            )

        member = serializer.validated_data["member"]
        if not team.memberships.filter(member=member, left_at__isnull=True).exists():
            raise ValidationError(
                detail={"member": _("This member is not in the team.")},
                code="member_not_in_team",
            )

        if Attendance.objects.filter(event=event, member=member).exists():
            raise ValidationError(
                detail={
                    "member": _("Attendance already exists for this member. Use PATCH to update.")
                },
                code="attendance_exists",
            )

        self._require_team_status(team, serializer.validated_data.get("status"))
        serializer.save(event=event)

    def perform_update(self, serializer):
        event = self.get_event()
        program = getattr(event, "refer_program", None)
        team = program.team if program else None
        self._require_team_status(team, serializer.validated_data.get("status"))
        serializer.save()

    @staticmethod
    def _require_team_status(team, status):
        """A chosen status must be one the team has ENABLED and active — not any
        global status code."""
        if status is None or team is None:
            return
        if not team.attendance_statuses.filter(pk=status.pk, is_active=True).exists():
            raise ValidationError(
                detail={"status": _("This status is not enabled for this team.")},
                code="status_not_enabled",
            )

    @extend_schema(
        summary="Bulk set attendances for an event",
        description=(
            "Creates or updates multiple attendance rows in a single atomic call. "
            "For each item: if (event, member) exists, update status; otherwise, "
            "create new. Members not in the payload are NOT touched. "
            "All-or-nothing: any validation error rolls back the entire batch."
        ),
        request=AttendanceBulkSerializer,
        responses={
            200: OpenApiResponse(
                response=AttendanceSerializer(many=True),
                description="Returns counts and full attendance list of the event.",
            ),
            400: OpenApiResponse(description="Validation error (member or status)"),
            403: OpenApiResponse(description="Not a team manager"),
        },
    )
    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk(self, request, event_pk=None):
        event = self.get_event()
        program = getattr(event, "refer_program", None)
        team = program.team if program else None
        if team is None or not team.is_managed_by(request.user):
            raise PermissionDenied(_("Only team owner and managers can bulk-set attendances."))

        serializer = AttendanceBulkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        items = serializer.validated_data["attendances"]

        member_ids = [item["member_id"] for item in items]
        status_codes = list({item["status_code"] for item in items})

        team_member_ids = set(
            team.memberships.filter(left_at__isnull=True, member_id__in=member_ids).values_list(
                "member_id", flat=True
            )
        )
        missing_members = [mid for mid in member_ids if mid not in team_member_ids]
        if missing_members:
            raise ValidationError(
                detail={"attendances": _("Members not in team: {ids}").format(ids=missing_members)},
                code="members_not_in_team",
            )

        # Only statuses the TEAM has enabled (and active) — not any global code.
        status_map = {
            s.code: s
            for s in team.attendance_statuses.filter(code__in=status_codes, is_active=True)
        }
        missing_statuses = [code for code in status_codes if code not in status_map]
        if missing_statuses:
            raise ValidationError(
                detail={
                    "attendances": _("Status codes not enabled for this team: {codes}").format(
                        codes=missing_statuses
                    )
                },
                code="invalid_status_codes",
            )

        created_count = 0
        updated_count = 0
        with transaction.atomic():
            for item in items:
                _att, was_created = Attendance.objects.update_or_create(
                    event=event,
                    member_id=item["member_id"],
                    defaults={"status": status_map[item["status_code"]]},
                )
                if was_created:
                    created_count += 1
                else:
                    updated_count += 1

        attendances = Attendance.objects.filter(event=event).select_related(
            "member", "member__user", "status"
        )
        return Response(
            {
                "created_count": created_count,
                "updated_count": updated_count,
                "attendances": AttendanceSerializer(attendances, many=True).data,
            }
        )

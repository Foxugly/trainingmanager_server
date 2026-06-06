from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError

from team.queries import managed_teams, user_member_teams

from .models import Performance
from .serializers import PerformanceSerializer


def _is_athlete_for(member, user):
    """True if the user is the member's own linked user."""
    return member.user_id is not None and member.user_id == user.id


def _is_coach_of(team, user):
    """True if the user owns or manages the team."""
    return managed_teams(user).filter(pk=team.pk).exists()


def _member_in_team(team, member):
    """True if a TeamMembership (active or past) exists for (team, member)."""
    return team.memberships.filter(member=member).exists()


@extend_schema_view(
    list=extend_schema(
        summary="List athlete performances (scoped + filtered)",
        description=(
            "Returns the performances the caller may see: their own (as the "
            "member's linked user) plus those of any team they coach. Optional "
            "filters: team, member, label (icontains). Order by recorded_on."
        ),
        parameters=[
            OpenApiParameter("team", OpenApiTypes.INT, description="Filter by team id."),
            OpenApiParameter("member", OpenApiTypes.INT, description="Filter by member id."),
            OpenApiParameter("label", OpenApiTypes.STR, description="Filter by label (icontains)."),
        ],
    ),
    create=extend_schema(summary="Log a performance"),
    retrieve=extend_schema(summary="Retrieve a performance"),
    update=extend_schema(summary="Replace a performance"),
    partial_update=extend_schema(summary="Edit a performance"),
    destroy=extend_schema(summary="Delete a performance"),
)
class PerformanceViewSet(viewsets.ModelViewSet):
    """CRUD on athlete performance records, scoped to a team + member.

    URL: /api/v1/performances/

    READ scope: a user sees a Performance if they are the member's linked
    user OR a coach (owner/manager) of the performance's team.
    WRITE: the member's own user OR a coach of the team. Athletes may only
    create for their own member, in a team they belong to.
    """

    serializer_class = PerformanceSerializer
    ordering_fields = ["recorded_on", "id", "created_at"]
    ordering = ["-recorded_on", "-id"]

    def _base_queryset(self):
        """Everything the caller may see: own member's rows + coached teams'."""
        user = self.request.user
        return (
            Performance.objects.filter(
                Q(member__user_id=user.id) | Q(team__in=managed_teams(user))
            )
            .select_related("team", "member", "created_by")
            .distinct()
        )

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Performance.objects.none()

        qs = self._base_queryset()

        params = self.request.query_params
        team_id = params.get("team")
        member_id = params.get("member")
        label = params.get("label")
        if team_id:
            qs = qs.filter(team_id=team_id)
        if member_id:
            qs = qs.filter(member_id=member_id)
        if label:
            qs = qs.filter(label__icontains=label)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        team = serializer.validated_data["team"]
        member = serializer.validated_data["member"]

        is_coach = _is_coach_of(team, user)
        is_athlete = _is_athlete_for(member, user)

        if not (is_coach or is_athlete):
            # Neither a coach of the team nor the member's own user.
            raise PermissionDenied(
                _("You cannot log a performance for this member.")
            )

        # An athlete (non-coach) may only log for their own member, and only
        # in a team they are themselves a member of.
        if not is_coach:
            if not user_member_teams(user).filter(pk=team.pk).exists():
                raise PermissionDenied(
                    _("You can only log performances in a team you belong to.")
                )

        # The member must actually belong to the team (active or past).
        if not _member_in_team(team, member):
            raise ValidationError(
                {
                    "detail": _("This member does not belong to this team."),
                    "code": "member_not_in_team",
                }
            )

        serializer.save(created_by=user)

    def _check_object_write(self, obj):
        user = self.request.user
        if _is_athlete_for(obj.member, user) or _is_coach_of(obj.team, user):
            return
        raise PermissionDenied(_("You cannot modify this performance."))

    def perform_update(self, serializer):
        self._check_object_write(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._check_object_write(instance)
        instance.delete()

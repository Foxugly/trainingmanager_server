from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError

from team.queries import managed_teams, user_member_teams

from .models import Equipment
from .serializers import EquipmentSerializer


@extend_schema_view(
    list=extend_schema(
        summary="List managed equipment (Matériel) of a team",
        description=(
            "Returns the team's managed equipment, ordered by name. Scoped to "
            "the teams the requester is a strict member of (owner/manager/active "
            "athlete). Pass ?team=<id> to filter to a single team; without it, "
            "equipment across all the requester's member teams is returned."
        ),
        parameters=[
            OpenApiParameter(
                name="team",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Restrict to one team the requester is a member of.",
            ),
        ],
    ),
    retrieve=extend_schema(summary="Retrieve an equipment item"),
    create=extend_schema(
        summary="Create an equipment item (manager only)",
        description=(
            "Create a managed equipment item for a team. The requester must own "
            "or manage the team. A duplicate (team, name) returns 400 with code "
            "`equipment_already_exists`."
        ),
    ),
    update=extend_schema(summary="Update an equipment item (manager only)"),
    partial_update=extend_schema(summary="Patch an equipment item (manager only)"),
    destroy=extend_schema(
        summary="Delete an equipment item (manager only)",
        description=(
            "Hard-deletes the item and removes it from any session that "
            "referenced it (the M2M link is dropped)."
        ),
    ),
)
class EquipmentViewSet(viewsets.ModelViewSet):
    """CRUD on managed team equipment (Matériel).

    Read: any strict member of the equipment's team. Write
    (create/update/delete): owner or manager of the team only.
    """

    serializer_class = EquipmentSerializer
    ordering = ["name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Equipment.objects.none()
        qs = Equipment.objects.filter(
            team__in=user_member_teams(self.request.user)
        ).select_related("team")
        team_id = self.request.query_params.get("team")
        if team_id:
            qs = qs.filter(team_id=team_id)
        return qs

    def _check_manager(self, team):
        if team is None or not managed_teams(self.request.user).filter(pk=team.pk).exists():
            raise PermissionDenied(
                _("You must be owner or manager of this team to manage its equipment."),
                code="not_a_manager",
            )

    def perform_create(self, serializer):
        team = serializer.validated_data.get("team")
        self._check_manager(team)
        serializer.save()

    def perform_update(self, serializer):
        new_team = serializer.validated_data.get("team")
        if new_team is not None and new_team.pk != serializer.instance.team_id:
            raise ValidationError(
                {"team": _("Equipment cannot be moved to another team.")},
                code="equipment_team_immutable",
            )
        self._check_manager(serializer.instance.team)
        serializer.save()

    def perform_destroy(self, instance):
        self._check_manager(instance.team)
        instance.delete()

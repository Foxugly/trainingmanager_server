from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import viewsets

from team.queries import user_member_teams

from .models import Equipment
from .serializers import EquipmentSerializer


@extend_schema_view(
    list=extend_schema(
        summary="List the global equipment (Matériel) catalog",
        description=(
            "Returns the active, multilingual equipment catalog (names localized "
            "to the active language). Pass ?team=<id> to restrict to the equipment "
            "a team has enabled (the requester must be a member of that team)."
        ),
        parameters=[
            OpenApiParameter(
                name="team",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Restrict to a team's enabled equipment.",
            ),
            OpenApiParameter(
                name="sport",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Restrict to one sport's catalog.",
            ),
        ],
    ),
    retrieve=extend_schema(summary="Retrieve an equipment item"),
)
class EquipmentViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only access to the global equipment catalog.

    The catalog is curated centrally (Django admin + seed migration). Teams
    enable a subset via ``Team.equipment``; ``?team=<id>`` returns that subset.
    """

    serializer_class = EquipmentSerializer
    ordering = ["name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Equipment.objects.none()
        qs = Equipment.objects.filter(is_active=True)
        sport_id = self.request.query_params.get("sport")
        if sport_id:
            qs = qs.filter(sport_id=sport_id)
        team_id = self.request.query_params.get("team")
        if team_id:
            # Only members of the team may see its enabled subset; non-members
            # get an empty list rather than the full catalog.
            member_teams = user_member_teams(self.request.user).filter(pk=team_id)
            if not member_teams.exists():
                return Equipment.objects.none()
            qs = qs.filter(teams__id=team_id)
        return qs

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError

from team.queries import managed_teams, user_member_teams

from .models import Place
from .serializers import PlaceSerializer


@extend_schema_view(
    list=extend_schema(
        summary="List managed places (Lieux) of a team",
        description=(
            "Returns the team's managed venues, ordered by name. Scoped to the "
            "teams the requester is a strict member of (owner/manager/active "
            "athlete). Pass ?team=<id> to filter to a single team; without it, "
            "places across all the requester's member teams are returned."
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
    retrieve=extend_schema(summary="Retrieve a place"),
    create=extend_schema(
        summary="Create a place (manager only)",
        description=(
            "Create a managed venue for a team. The requester must own or "
            "manage the team. A duplicate (team, name) returns 400 with code "
            "`place_already_exists`."
        ),
    ),
    update=extend_schema(summary="Update a place (manager only)"),
    partial_update=extend_schema(summary="Patch a place (manager only)"),
    destroy=extend_schema(
        summary="Delete a place (manager only)",
        description=(
            "Hard-deletes the venue. Events referencing it keep their free-text "
            "`location` (their `place` FK is SET_NULL)."
        ),
    ),
)
class PlaceViewSet(viewsets.ModelViewSet):
    """CRUD on the managed team venue (Lieu).

    Read: any strict member of the place's team. Write (create/update/delete):
    owner or manager of the team only.
    """

    serializer_class = PlaceSerializer
    ordering = ["name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Place.objects.none()
        qs = Place.objects.filter(
            team__in=user_member_teams(self.request.user)
        ).select_related("team")
        team_id = self.request.query_params.get("team")
        if team_id:
            qs = qs.filter(team_id=team_id)
        return qs

    def _check_manager(self, team):
        if team is None or not managed_teams(self.request.user).filter(pk=team.pk).exists():
            raise PermissionDenied(
                _("You must be owner or manager of this team to manage its places."),
                code="not_a_manager",
            )

    def perform_create(self, serializer):
        team = serializer.validated_data.get("team")
        self._check_manager(team)
        serializer.save()

    def perform_update(self, serializer):
        # The team of an existing place is fixed; block reparenting and use the
        # instance's team for the manager check.
        new_team = serializer.validated_data.get("team")
        if new_team is not None and new_team.pk != serializer.instance.team_id:
            raise ValidationError(
                {"team": _("A place cannot be moved to another team.")},
                code="place_team_immutable",
            )
        self._check_manager(serializer.instance.team)
        serializer.save()

    def perform_destroy(self, instance):
        self._check_manager(instance.team)
        instance.delete()

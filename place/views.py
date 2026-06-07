from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied

from team.queries import managed_teams, user_member_teams

from .models import Place
from .serializers import PlaceSerializer


@extend_schema_view(
    list=extend_schema(
        summary="List venues (Lieux) from the sport pool",
        description=(
            "Returns venues from the global, sport-scoped pool. ?team=<id> "
            "restricts to a team's linked places (members only); ?sport=<id> "
            "returns the whole pool for a sport (to attach/share). Without "
            "filters, returns places linked to the requester's member teams."
        ),
        parameters=[
            OpenApiParameter(
                name="team",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Restrict to a team's linked places.",
            ),
            OpenApiParameter(
                name="sport",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Return the whole pool for a sport.",
            ),
        ],
    ),
    retrieve=extend_schema(summary="Retrieve a place"),
    create=extend_schema(
        summary="Create a place and link it to a team (manager only)",
        description=(
            "Creates a venue in the pool and links it to the given team (the "
            "requester must manage that team); the place's sport is the team's "
            "sport."
        ),
    ),
    update=extend_schema(summary="Update a place (manager of a linked team)"),
    partial_update=extend_schema(summary="Patch a place (manager of a linked team)"),
    destroy=extend_schema(
        summary="Delete a place (manager of a linked team)",
        description=(
            "Hard-deletes the venue across all teams that linked it. Events "
            "referencing it keep their free-text location (place FK is SET_NULL)."
        ),
    ),
)
class PlaceViewSet(viewsets.ModelViewSet):
    """CRUD on the shared, sport-scoped venue pool (Lieu).

    Read: members see places linked to their teams, or a whole sport's pool via
    ?sport. Write: create links a new place to a team the requester manages;
    update/delete require managing a team the place is linked to.
    """

    serializer_class = PlaceSerializer
    ordering = ["name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Place.objects.none()
        sport_id = self.request.query_params.get("sport")
        team_id = self.request.query_params.get("team")
        if sport_id:
            # Whole sport pool (to attach/share an existing venue).
            return Place.objects.filter(sport_id=sport_id).distinct()
        qs = Place.objects.filter(teams__in=user_member_teams(self.request.user))
        if team_id:
            qs = qs.filter(teams__id=team_id)
        return qs.distinct()

    def _require_manages(self, team):
        if team is None or not managed_teams(self.request.user).filter(pk=team.pk).exists():
            raise PermissionDenied(
                _("You must be owner or manager of this team to manage its places."),
                code="not_a_manager",
            )

    def _require_manages_a_linked_team(self, place):
        if not managed_teams(self.request.user).filter(places=place).exists():
            raise PermissionDenied(
                _("You must manage a team this place belongs to."),
                code="not_a_manager",
            )

    def perform_create(self, serializer):
        team = serializer.validated_data.pop("team", None)
        self._require_manages(team)
        place = serializer.save(sport=team.sport)
        team.places.add(place)

    def perform_update(self, serializer):
        serializer.validated_data.pop("team", None)
        self._require_manages_a_linked_team(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._require_manages_a_linked_team(instance)
        instance.delete()

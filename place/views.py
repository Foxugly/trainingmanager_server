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
            # Whole sport pool (to attach/share an existing venue). Multi-sport:
            # a venue is in a sport's pool if that sport is among its `sports`.
            return Place.objects.filter(sports__id=sport_id).distinct()
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

    def _require_owns_all_linked_teams(self, place):
        """Editing/deleting a SHARED venue would affect teams the caller doesn't
        manage. So mutation/deletion is allowed only when the caller manages
        EVERY team the place is linked to. To stop using a shared venue, a team
        simply removes it from its own ``places`` (Team.place_ids) instead."""
        linked_team_ids = set(place.teams.values_list("id", flat=True))
        managed_ids = set(managed_teams(self.request.user).values_list("id", flat=True))
        if not linked_team_ids or not linked_team_ids.issubset(managed_ids):
            raise PermissionDenied(
                _(
                    "This venue is shared with teams you don't manage. Remove it "
                    "from your team's venues instead of editing or deleting it."
                ),
                code="place_shared",
            )

    def perform_create(self, serializer):
        team = serializer.validated_data.pop("team", None)
        self._require_manages(team)
        place = serializer.save()
        # The venue serves the creating team's sports (multi-sport); a
        # single-sport team yields one sport, preserving the old behaviour.
        place.sports.set(team.sports.all())
        team.places.add(place)

    def perform_update(self, serializer):
        serializer.validated_data.pop("team", None)
        self._require_owns_all_linked_teams(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._require_owns_all_linked_teams(instance)
        instance.delete()

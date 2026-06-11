from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from team.queries import managed_teams

from .models import AuditLogEntry
from .serializers import AuditLogEntrySerializer


@extend_schema(
    summary="List audit log entries for teams you manage",
    parameters=[
        OpenApiParameter(
            "team",
            OpenApiTypes.INT,
            OpenApiParameter.QUERY,
            required=False,
            description=(
                "Restrict to a single team you manage. A team you do not manage "
                "yields 403."
            ),
        ),
    ],
)
class AuditLogListView(generics.ListAPIView):
    """GET /api/v1/audit-log/ — review security-sensitive actions.

    Team owners and managers see audit entries scoped to teams they manage
    (``team.queries.managed_teams``). Superusers see all entries. The optional
    ``?team=<id>`` filter narrows to a single managed team (403 otherwise).
    Ordered newest-first and paginated like other list endpoints.
    """

    serializer_class = AuditLogEntrySerializer
    permission_classes = [IsAuthenticated]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return AuditLogEntry.objects.none()

        user = self.request.user
        qs = AuditLogEntry.objects.select_related("actor", "team")

        if not user.is_superuser:
            managed_ids = set(managed_teams(user).values_list("pk", flat=True))
            qs = qs.filter(team_id__in=managed_ids)

        team_id = self.request.query_params.get("team")
        if team_id:
            try:
                team_pk = int(team_id)
            except (TypeError, ValueError):
                raise PermissionDenied("You do not manage this team.")
            # Reuse the managed_ids set already resolved above instead of a 2nd
            # managed_teams() DISTINCT query. (Short-circuits for superusers,
            # for whom managed_ids is never computed.)
            if not user.is_superuser and team_pk not in managed_ids:
                raise PermissionDenied("You do not manage this team.")
            qs = qs.filter(team_id=team_pk)

        return qs.order_by("-created_at")

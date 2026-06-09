"""Unauthenticated public read-only view of a shared training session.

GET /api/v1/public/events/<token>/ resolves a session purely by its opaque
``public_token`` — the token IS the authentication, exactly like the per-user
iCal feed. The endpoint is locked to AllowAny with authentication disabled, and
returns a generic 404 for any token that does not map to a currently-shared
session whose team still allows public sharing (defense in depth: disabling
sharing at the team level kills every existing link).
"""

from django.http import Http404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Event
from .public_serializers import EventPublicSerializer


class PublicEventView(APIView):
    """GET /api/v1/public/events/<token>/ — anonymous read-only session view.

    Resolves the Event by (public_token, is_public=True) and requires the
    owning team to still have public_sharing_enabled=True. Any miss returns a
    generic 404 so the endpoint never leaks whether a token exists.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response=EventPublicSerializer,
                description="Read-only public session payload.",
            ),
            404: OpenApiResponse(
                description="No shared session for this token (generic; never leaks existence)."
            ),
        },
        description=(
            "Public read-only view of a session shared via its unguessable "
            "token. Sensitive aspects (distance / goal / rounds) are revealed "
            "only when the team's public_show_* config allows; name, date, "
            "time, location, equipment, program and team name are always shown."
        ),
    )
    def get(self, request, token):
        event = (
            Event.objects.filter(public_token=token, is_public=True)
            .select_related("refer_program", "refer_program__team")
            .prefetch_related(
                "rounds",
                "rounds__exercises__modality",
                "rounds__exercises__energysegment",
            )
            .first()
        )
        if event is None:
            raise Http404("No shared session for this token.")

        team = event.team
        # Defense in depth: a team that later disables sharing kills all links.
        if team is None or not team.public_sharing_enabled:
            raise Http404("No shared session for this token.")

        serializer = EventPublicSerializer(event, context={"request": request})
        return Response(serializer.data)

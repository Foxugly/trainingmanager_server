"""GET /api/v1/health/ — readiness/liveness probe for load balancers.

Deliberately lightweight: a single SELECT 1 against the default database.
Public (no auth) so the probe doesn't depend on a JWT being valid; the
endpoint reveals nothing beyond up/degraded status. Returns 200 with
{status, database} on success, 503 with the same shape on DB failure —
the latter is the signal a Kubernetes readinessProbe / GCP/AWS LB needs
to take the pod out of rotation.
"""

import logging

from django.db import DatabaseError, connection
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework import status as http_status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


# Single response shape across both branches. Status/database are kept
# as plain strings (with documented valid values) instead of ChoiceFields:
# two ChoiceFields with overlapping field names would generate two
# StatusEnum / DatabaseEnum components in OpenAPI and trip drf-spectacular
# enum-collision warnings — not worth the schema noise for a probe.
_HealthCheckPayload = inline_serializer(
    name="HealthCheck",
    fields={
        "status": drf_serializers.CharField(help_text="'ok' on 200, 'degraded' on 503."),
        "database": drf_serializers.CharField(help_text="'ok' on 200, 'error' on 503."),
    },
)


class HealthCheckView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        summary="Service healthcheck",
        description=(
            "Readiness/liveness probe. Returns 200 when the default database "
            "answers a trivial SELECT, 503 otherwise. Public — no auth required."
        ),
        responses={
            200: OpenApiResponse(response=_HealthCheckPayload),
            503: OpenApiResponse(response=_HealthCheckPayload),
        },
    )
    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except DatabaseError:
            logger.exception("healthcheck: database probe failed")
            return Response(
                {"status": "degraded", "database": "error"},
                status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"status": "ok", "database": "ok"})


@extend_schema_view(get=extend_schema(exclude=True))
class HealthCheckAliasView(HealthCheckView):
    """Alias racine `/health/`, convention de flotte (les autres sites exposent
    leur sonde a cette adresse). Meme comportement que `/api/v1/health/`, qui
    reste l'adresse documentee et celle des sondes existantes.

    Exclu du schema OpenAPI a dessein : documenter deux fois le meme endpoint
    produirait une collision d'operationId, et le repo tient un zero warning
    spectacular en permanence.
    """

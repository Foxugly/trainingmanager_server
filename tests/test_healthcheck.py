"""GET /api/v1/health/ — public readiness/liveness probe.

Used by the load balancer / k8s readinessProbe; must answer without auth
and must downgrade to 503 when the database is unreachable so the pod
gets pulled from rotation instead of taking traffic blindly.
"""

from unittest.mock import patch

import pytest
from django.db import DatabaseError

pytestmark = pytest.mark.django_db

HEALTH_URL = "/api/v1/health/"
HEALTH_ALIAS_URL = "/health/"


def test_healthcheck_returns_200_when_db_ok(api_client):
    response = api_client.get(HEALTH_URL)
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "database": "ok"}


def test_healthcheck_does_not_require_auth(api_client):
    """Probes run anonymously — confirms the endpoint isn't behind
    DEFAULT_PERMISSION_CLASSES=IsAuthenticated."""
    # api_client has no credentials() set
    response = api_client.get(HEALTH_URL)
    assert response.status_code == 200


def test_healthcheck_returns_503_when_db_fails(api_client):
    """Simulates a DB outage by patching connection.cursor to raise. The
    endpoint must answer 503 with the degraded payload — the load
    balancer reads this to take the instance out of rotation."""
    with patch("tools.health.connection.cursor", side_effect=DatabaseError("simulated")):
        response = api_client.get(HEALTH_URL)
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "error"}


# ==========================================================================
# Alias racine /health/ — convention de flotte (les autres sites l'exposent la)
# ==========================================================================


def test_health_alias_returns_the_same_payload(api_client):
    """L'alias racine doit repondre exactement comme /api/v1/health/, sans auth."""
    canonical = api_client.get(HEALTH_URL)
    alias = api_client.get(HEALTH_ALIAS_URL)
    assert alias.status_code == 200
    assert alias.json() == canonical.json() == {"status": "ok", "database": "ok"}


def test_health_alias_returns_503_when_db_fails(api_client):
    with patch("tools.health.connection.cursor", side_effect=DatabaseError("simulated")):
        response = api_client.get(HEALTH_ALIAS_URL)
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "error"}


def test_health_alias_is_absent_from_the_openapi_schema():
    """Documenter deux fois le meme endpoint creerait une collision
    d'operationId ; l'alias est donc volontairement hors schema."""
    from drf_spectacular.generators import SchemaGenerator

    paths = SchemaGenerator().get_schema(request=None, public=True)["paths"]
    assert "/api/v1/health/" in paths
    assert "/health/" not in paths

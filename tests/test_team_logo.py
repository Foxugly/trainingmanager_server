"""Coverage for the public team-logo endpoint and the list payload lightening
(#13): the base64 logo is served via /teams/{id}/logo/ and dropped from the
list, which exposes `logo_url` instead.
"""

import base64

import pytest

from tests.factories import TeamFactory

pytestmark = pytest.mark.django_db

PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(b"hello-logo").decode()


def test_logo_endpoint_is_public_and_returns_image(api_client):
    team = TeamFactory(logo=PNG_DATA_URL)
    res = api_client.get(f"/api/v1/teams/{team.pk}/logo/")  # no auth on purpose
    assert res.status_code == 200
    assert res["Content-Type"] == "image/png"
    assert res.content == b"hello-logo"
    assert "max-age" in res["Cache-Control"]


def test_logo_endpoint_404_without_logo(api_client):
    team = TeamFactory(logo="")
    assert api_client.get(f"/api/v1/teams/{team.pk}/logo/").status_code == 404


def test_logo_endpoint_rejects_non_raster_mime_at_read(api_client):
    """Regression: the public logo endpoint must re-validate the STORED MIME
    against the raster allow-list (png/jpeg/jpg/webp) and 404 anything else
    (e.g. image/svg+xml) — never serve an active/attacker-chosen content-type,
    even if a non-conforming value somehow reached the DB (bypassing
    LOGO_DATA_URL_RE on write)."""
    svg = "data:image/svg+xml;base64," + base64.b64encode(
        b"<svg onload='x'/>"
    ).decode()
    team = TeamFactory()
    # Bypass serializer validation: write straight to the model column.
    team.logo = svg
    team.save(update_fields=["logo"])
    assert api_client.get(f"/api/v1/teams/{team.pk}/logo/").status_code == 404


def test_logo_endpoint_serves_webp(api_client):
    """Non-regression: a valid raster MIME on the allow-list is still served."""
    webp = "data:image/webp;base64," + base64.b64encode(b"webp-bytes").decode()
    team = TeamFactory(logo=webp)
    res = api_client.get(f"/api/v1/teams/{team.pk}/logo/")
    assert res.status_code == 200
    assert res["Content-Type"] == "image/webp"


def test_list_drops_base64_logo_and_exposes_logo_url(auth_client, authenticated_user):
    team = TeamFactory(owner=authenticated_user, logo=PNG_DATA_URL)
    res = auth_client.get("/api/v1/teams/")
    assert res.status_code == 200
    row = next(t for t in res.json()["results"] if t["id"] == team.pk)
    # The heavy base64 is not shipped in the list…
    assert row["logo"] is None
    # …but the consumer can still render it via the URL.
    assert row["logo_url"].endswith(f"/teams/{team.pk}/logo/")


def test_detail_keeps_base64_logo(auth_client, authenticated_user):
    team = TeamFactory(owner=authenticated_user, logo=PNG_DATA_URL)
    res = auth_client.get(f"/api/v1/teams/{team.pk}/")
    assert res.status_code == 200
    assert res.json()["logo"] == PNG_DATA_URL
    assert res.json()["logo_url"].endswith(f"/teams/{team.pk}/logo/")

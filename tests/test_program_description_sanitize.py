"""Program.description is sanitized on write (audit 2026-10-18 S-M5)."""

import pytest
from django.contrib.auth import get_user_model

from tests.factories import ProgramFactory, TeamFactory

pytestmark = pytest.mark.django_db


def test_patch_program_description_is_sanitized(api_client):
    owner = get_user_model().objects.create_user(
        email="prog_san@local.test", password="Str0ngP@ssTest!"
    )
    program = ProgramFactory(team=TeamFactory(owner=owner, is_active=True))
    api_client.force_authenticate(user=owner)

    resp = api_client.patch(
        f"/api/v1/programs/{program.pk}/",
        {"description": '<p onclick="x()">Plan<script>alert(1)</script></p>'},
        format="json",
    )

    assert resp.status_code == 200, resp.content
    program.refresh_from_db()
    assert "<script" not in program.description
    assert "onclick" not in program.description
    assert "Plan" in program.description

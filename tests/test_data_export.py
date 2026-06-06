"""Coverage of GET /api/v1/me/export/ — RGPD data portability.

The endpoint returns a single downloadable JSON document of all personal
data we hold about the caller. Two invariants are load-bearing: the
password hash and the live calendar_token secret must NEVER appear, and an
anonymous request must be rejected.
"""

import datetime as dt
import json

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from member.models import Member
from performance.models import Performance
from team.models import TeamMembership
from tests.factories import TeamFactory

pytestmark = pytest.mark.django_db


User = get_user_model()

URL = "/api/v1/me/export/"


@pytest.fixture
def export_user(db):
    return User.objects.create_user(
        username="export_me",
        email="export_me@local.test",
        password="Str0ngP@ssExport!",
        first_name="Ex",
        last_name="Porter",
    )


@pytest.fixture
def export_client(api_client, export_user):
    api_client.force_authenticate(user=export_user)
    return api_client


def test_export_anonymous_is_rejected(api_client):
    response = api_client.get(URL)
    assert response.status_code in (401, 403)


def test_export_returns_profile_and_disposition_header(export_client, export_user):
    response = export_client.get(URL)
    assert response.status_code == 200, getattr(response, "data", None)

    disposition = response["Content-Disposition"]
    assert disposition == (
        'attachment; filename="trainingmanager-export-export_me.json"'
    )

    data = response.json()
    profile = data["profile"]
    assert profile["id"] == export_user.id
    assert profile["username"] == "export_me"
    assert profile["email"] == "export_me@local.test"
    assert profile["first_name"] == "Ex"
    assert profile["last_name"] == "Porter"
    assert "weekly_recap_opt_in" in profile

    # Every documented section is present (empty lists when no data).
    for key in [
        "member_profile",
        "team_memberships",
        "owned_teams",
        "managed_teams",
        "performances",
        "rsvps",
        "roti_scores",
        "notes_about_me",
        "messages_authored",
        "uploaded_attachments",
    ]:
        assert key in data


def test_export_never_leaks_password_or_calendar_token(export_client, export_user):
    response = export_client.get(URL)
    assert response.status_code == 200

    # Serialize the whole payload and assert the secrets are absent both as
    # keys and as values anywhere in the document.
    raw = json.dumps(response.json())
    assert "password" not in raw
    assert "calendar_token" not in raw
    assert export_user.calendar_token not in raw
    assert export_user.password not in raw


def test_export_includes_member_profile_membership_and_performance(
    export_client, export_user
):
    team = TeamFactory(owner=export_user, is_active=True)
    member = Member.objects.create(
        firstname="Ex",
        lastname="Porter",
        email="member_email@local.test",
        phonenumber="+32499999999",
        user=export_user,
    )
    TeamMembership.objects.create(team=team, member=member, joined_at=timezone.now())
    Performance.objects.create(
        team=team,
        member=member,
        label="100m",
        value="12.500",
        unit="s",
        recorded_on=dt.date(2026, 1, 1),
        notes="PB",
    )

    response = export_client.get(URL)
    assert response.status_code == 200
    data = response.json()

    assert data["member_profile"]["firstname"] == "Ex"
    assert data["member_profile"]["phonenumber"] == "+32499999999"

    assert len(data["team_memberships"]) == 1
    assert data["team_memberships"][0]["team"] == team.name

    assert len(data["performances"]) == 1
    perf = data["performances"][0]
    assert perf["label"] == "100m"
    assert perf["unit"] == "s"
    assert perf["notes"] == "PB"

    # owned_teams carries names only (no nested secrets).
    assert {"id": team.id, "name": team.name} in data["owned_teams"]


def test_export_without_member_returns_empty_related_lists(export_client):
    response = export_client.get(URL)
    assert response.status_code == 200
    data = response.json()
    assert data["member_profile"] is None
    assert data["performances"] == []
    assert data["team_memberships"] == []
    assert data["rsvps"] == []
    assert data["roti_scores"] == []
    assert data["notes_about_me"] == []

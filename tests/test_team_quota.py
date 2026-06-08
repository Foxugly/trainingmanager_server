"""Coverage of CustomUser.team_quota — paid feature: creating a team
requires an unused slot.

- Default new user: quota=0, can_create=False.
- POST /teams/ as a 0-quota user: 403 team_quota_exceeded with body
  enriched with {used, max, can_create}.
- Bumped quota: user can create up to N teams; further attempts blocked.
- Soft-delete (is_active=False) frees up a slot.
- /me/ exposes team_quota: {used, max, can_create}.
- Staff is NOT exempt — same quota gate via API. Bypass is via /admin/.
"""

import pytest
from django.contrib.auth import get_user_model

from sport.models import Sport
from team.models import Team

pytestmark = pytest.mark.django_db


User = get_user_model()


@pytest.fixture
def sport(db):
    return Sport.objects.create(name="Test Sport", slug="test-sport-quota", is_active=True)


def _create_team_payload(sport):
    return {"name": f"Team {sport.pk}-{Team.objects.count() + 1}", "sport_id": sport.pk}


def _user(username, **kwargs):
    return User.objects.create_user(
        username=username, email=f"{username}@local.test", password="Sup3rS@fePass!", **kwargs
    )


# =====================================================================
# /me/ exposes team_quota
# =====================================================================


def test_GET_me_exposes_team_quota_block(api_client):
    user = _user("quota_default")
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/me/")
    assert response.status_code == 200
    quota = response.json()["team_quota"]
    assert quota == {"used": 0, "max": 0, "can_create": False}


def test_GET_me_team_quota_reflects_bumped_max_and_used(api_client, sport):
    user = _user("quota_bumped", team_quota=2)
    Team.objects.create(name="Already-owned", owner=user)
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/me/")
    quota = response.json()["team_quota"]
    assert quota == {"used": 1, "max": 2, "can_create": True}


# =====================================================================
# POST /teams/ — quota gate
# =====================================================================


def test_POST_team_default_user_returns_403_team_quota_exceeded(api_client, sport):
    user = _user("quota_blocked")
    api_client.force_authenticate(user=user)
    response = api_client.post("/api/v1/teams/", _create_team_payload(sport), format="json")
    assert response.status_code == 403, response.json()
    body = response.json()
    assert body["code"] == "team_quota_exceeded"
    assert body["used"] == 0
    assert body["max"] == 0
    assert body["can_create"] is False
    assert not Team.objects.filter(owner=user).exists()


def test_POST_team_with_remaining_slot_returns_201(api_client, sport):
    user = _user("quota_one_slot", team_quota=1)
    api_client.force_authenticate(user=user)
    response = api_client.post("/api/v1/teams/", _create_team_payload(sport), format="json")
    assert response.status_code == 201, response.json()
    assert Team.objects.filter(owner=user, is_active=True).count() == 1


def test_POST_team_at_quota_max_returns_403(api_client, sport):
    user = _user("quota_at_max", team_quota=1)
    Team.objects.create(name="First and last", owner=user)
    api_client.force_authenticate(user=user)
    response = api_client.post("/api/v1/teams/", _create_team_payload(sport), format="json")
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "team_quota_exceeded"
    assert body["used"] == 1
    assert body["max"] == 1


def test_POST_team_after_soft_delete_frees_slot(api_client, sport):
    """B-(a)/H semantic: an is_active=False team no longer counts toward
    the quota, so the user can create a fresh one."""
    user = _user("quota_softdel", team_quota=1)
    archived = Team.objects.create(name="Archived", owner=user, is_active=False)
    api_client.force_authenticate(user=user)
    response = api_client.post("/api/v1/teams/", _create_team_payload(sport), format="json")
    assert response.status_code == 201
    # New active team coexists with the archived one
    assert Team.objects.filter(owner=user, is_active=True).count() == 1
    assert Team.objects.filter(pk=archived.pk).exists()


def test_POST_team_staff_is_not_exempt_via_api(api_client, sport):
    """Staff has no implicit bypass on the API — they must bump their own
    quota or use /admin/. Decision I (b)."""
    staff = _user("staff_no_bypass", is_staff=True)
    api_client.force_authenticate(user=staff)
    response = api_client.post("/api/v1/teams/", _create_team_payload(sport), format="json")
    assert response.status_code == 403
    assert response.json()["code"] == "team_quota_exceeded"


# =====================================================================
# Migration data — `renaud` got bumped to 10
# =====================================================================


def test_migration_bumped_renaud_to_team_quota_10(db):
    """The 0004_add_team_quota migration runs RunPython that sets
    User(username=renaud).team_quota=10. New deployments without a
    `renaud` user are no-ops (the test would skip)."""
    try:
        renaud = User.objects.get(username="renaud")
    except User.DoesNotExist:
        pytest.skip("No `renaud` user in this test DB")
    assert renaud.team_quota == 10

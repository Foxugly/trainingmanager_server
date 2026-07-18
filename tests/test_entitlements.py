"""Coverage of subscription_bypass — accès offert accordé sans souscription.

- Le champ existe, défaut False, avec note d'audit et date d'octroi.
- Il est DISTINCT de is_staff, qui n'accorde aucun droit métier
  (cf. tests/test_team_quota.py, Decision I (b) — doit rester vert).
"""

import pytest
from django.contrib.auth import get_user_model

from customuser.entitlements import UNLIMITED, can_create_team, user_quota
from sport.models import Sport
from team.models import Team

pytestmark = pytest.mark.django_db

User = get_user_model()


def _user(name, **kwargs):
    return User.objects.create_user(email=f"{name}@local.test", password="Sup3rS@fePass!", **kwargs)


@pytest.fixture
def sport(db):
    return Sport.objects.create(name="Sport Bypass", slug="sport-bypass", is_active=True)


def test_subscription_bypass_defaults_to_false():
    user = _user("bypass_default")
    assert user.subscription_bypass is False
    assert user.bypass_note == ""
    assert user.bypass_granted_at is None


def test_user_quota_returns_team_quota_without_bypass():
    user = _user("quota_plain", team_quota=2)
    assert user_quota(user) == 2
    assert can_create_team(user) is True


def test_user_quota_is_unlimited_with_bypass():
    user = _user("quota_bypass", team_quota=0, subscription_bypass=True)
    assert user_quota(user) == UNLIMITED
    assert can_create_team(user) is True


def test_GET_me_reports_unlimited_quota_with_bypass(api_client):
    user = _user("me_bypass", team_quota=0, subscription_bypass=True)
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/me/")
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["subscription_bypass"] is True
    assert body["team_quota"] == {"used": 0, "max": UNLIMITED, "can_create": True}


def test_POST_team_with_bypass_and_zero_quota_returns_201(api_client, sport):
    user = _user("create_bypass", team_quota=0, subscription_bypass=True)
    api_client.force_authenticate(user=user)
    response = api_client.post(
        "/api/v1/teams/", {"name": "Team Bypass", "sport_id": sport.pk}, format="json"
    )
    assert response.status_code == 201, response.json()
    assert Team.objects.filter(owner=user).exists()


def test_PATCH_me_cannot_self_grant_bypass(api_client):
    """Auto-élévation : le champ est read-only sur /me/."""
    user = _user("me_escalate")
    api_client.force_authenticate(user=user)
    response = api_client.patch("/api/v1/me/", {"subscription_bypass": True}, format="json")
    assert response.status_code == 200, response.json()
    user.refresh_from_db()
    assert user.subscription_bypass is False

"""Coverage of POST /api/v1/auth/account/delete/ (authenticated).

Endpoint:
- POST /api/v1/auth/account/delete/ — authenticated self-service account
  deletion. Body: {current_password}.

Permission: IsAuthenticated. On success returns 204 (no body) and the user
row is gone. A user who still owns one or more active teams is refused with
409 (code=owns_teams) and nothing is deleted. Errors follow the repo's
{code, detail} / {fields} convention.
"""

import pytest
from django.contrib.auth import get_user_model

from tests.factories import TeamFactory

pytestmark = pytest.mark.django_db


User = get_user_model()


DELETE_URL = "/api/v1/auth/account/delete/"
CURRENT_PASSWORD = "OldP@ssw0rd!"


def _user(username="delete_user"):
    return User.objects.create_user(
        username=username,
        email=f"{username}@local.test",
        password=CURRENT_PASSWORD,
        first_name="Delete",
        last_name="User",
    )


def test_delete_with_correct_password_and_no_teams_returns_204_and_removes_user(api_client):
    user = _user()
    user_id = user.pk
    api_client.force_authenticate(user=user)
    response = api_client.post(
        DELETE_URL,
        {"current_password": CURRENT_PASSWORD},
        format="json",
    )
    assert response.status_code == 204, getattr(response, "data", None)
    # 204 carries no body.
    assert not response.content
    assert not User.objects.filter(pk=user_id).exists()


def test_delete_with_wrong_password_returns_400(api_client):
    user = _user()
    user_id = user.pk
    api_client.force_authenticate(user=user)
    response = api_client.post(
        DELETE_URL,
        {"current_password": "WrongP@ssw0rd!"},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "current_password_invalid"
    # User must still exist.
    assert User.objects.filter(pk=user_id).exists()


def test_delete_blocked_when_user_owns_active_team_returns_409(api_client):
    user = _user()
    user_id = user.pk
    team = TeamFactory(owner=user, is_active=True)
    api_client.force_authenticate(user=user)
    response = api_client.post(
        DELETE_URL,
        {"current_password": CURRENT_PASSWORD},
        format="json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "owns_teams"
    # Neither the team nor the user is deleted.
    assert User.objects.filter(pk=user_id).exists()
    from team.models import Team

    assert Team.objects.filter(pk=team.pk).exists()


def test_delete_blocked_even_when_owned_team_is_soft_deleted(api_client):
    """A soft-deleted (is_active=False) team still blocks deletion.

    Team.owner is on_delete=PROTECT, so even an inactive owned team would
    raise ProtectedError on user.delete(); the guard must refuse it cleanly
    with 409 rather than 500.
    """
    user = _user()
    user_id = user.pk
    team = TeamFactory(owner=user, is_active=False)
    api_client.force_authenticate(user=user)
    response = api_client.post(
        DELETE_URL,
        {"current_password": CURRENT_PASSWORD},
        format="json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "owns_teams"
    assert User.objects.filter(pk=user_id).exists()
    from team.models import Team

    assert Team.objects.filter(pk=team.pk).exists()


def test_delete_unauthenticated_is_rejected(api_client):
    response = api_client.post(
        DELETE_URL,
        {"current_password": CURRENT_PASSWORD},
        format="json",
    )
    assert response.status_code in (401, 403)

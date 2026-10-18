"""Coverage of POST /api/v1/auth/password/change/ (authenticated).

Endpoint:
- POST /api/v1/auth/password/change/ — authenticated self-service password
  change. Body: {current_password, new_password}.

Permission: IsAuthenticated. On success returns 200 + {detail, access, refresh}:
every other outstanding refresh token is revoked and the caller gets a fresh pair.
Errors follow the repo's {code, detail} / {fields} convention.
"""

import pytest
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db


User = get_user_model()


CHANGE_URL = "/api/v1/auth/password/change/"
CURRENT_PASSWORD = "OldP@ssw0rd!"


def _user(username="change_user"):
    return User.objects.create_user(
        email=f"{username}@local.test",
        password=CURRENT_PASSWORD,
        first_name="Change",
        last_name="User",
    )


def test_change_with_correct_current_password_returns_200_and_changes_password(api_client):
    user = _user()
    api_client.force_authenticate(user=user)
    response = api_client.post(
        CHANGE_URL,
        {"current_password": CURRENT_PASSWORD, "new_password": "BrandN3wP@ss!"},
        format="json",
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert "detail" in body
    # A fresh pair replaces the revoked sessions.
    assert body["access"] and body["refresh"]

    user.refresh_from_db()
    assert user.check_password("BrandN3wP@ss!")
    assert not user.check_password(CURRENT_PASSWORD)


def test_change_with_wrong_current_password_returns_400(api_client):
    user = _user()
    api_client.force_authenticate(user=user)
    response = api_client.post(
        CHANGE_URL,
        {"current_password": "WrongP@ssw0rd!", "new_password": "BrandN3wP@ss!"},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "current_password_invalid"

    user.refresh_from_db()
    assert user.check_password(CURRENT_PASSWORD)


def test_change_with_weak_new_password_returns_400_field_error(api_client):
    user = _user()
    api_client.force_authenticate(user=user)
    response = api_client.post(
        CHANGE_URL,
        {"current_password": CURRENT_PASSWORD, "new_password": "12345678"},
        format="json",
    )
    assert response.status_code == 400
    body = response.json()
    # Field-level error: new_password rejected by Django validators.
    assert "new_password" in body.get("fields", {})

    user.refresh_from_db()
    assert user.check_password(CURRENT_PASSWORD)


def test_change_rejects_new_equal_to_current(api_client):
    user = _user()
    api_client.force_authenticate(user=user)
    response = api_client.post(
        CHANGE_URL,
        {"current_password": CURRENT_PASSWORD, "new_password": CURRENT_PASSWORD},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "password_unchanged"

    user.refresh_from_db()
    assert user.check_password(CURRENT_PASSWORD)


def test_change_unauthenticated_is_rejected(api_client):
    response = api_client.post(
        CHANGE_URL,
        {"current_password": CURRENT_PASSWORD, "new_password": "BrandN3wP@ss!"},
        format="json",
    )
    assert response.status_code in (401, 403)


REFRESH_URL = "/api/v1/auth/token/refresh/"


def test_change_revokes_previously_issued_refresh_tokens(api_client):
    from rest_framework_simplejwt.tokens import RefreshToken

    user = _user()
    stolen = str(RefreshToken.for_user(user))
    api_client.force_authenticate(user=user)
    response = api_client.post(
        CHANGE_URL,
        {"current_password": CURRENT_PASSWORD, "new_password": "BrandN3wP@ss!"},
        format="json",
    )
    assert response.status_code == 200
    fresh = response.json()["refresh"]

    api_client.force_authenticate(user=None)
    assert api_client.post(REFRESH_URL, {"refresh": stolen}, format="json").status_code == 401
    assert api_client.post(REFRESH_URL, {"refresh": fresh}, format="json").status_code == 200


def test_change_is_throttled(api_client, set_throttle_rate):
    set_throttle_rate("auth_password_change", "2/hour")
    user = _user()
    api_client.force_authenticate(user=user)
    payload = {"current_password": "WrongP@ssw0rd!", "new_password": "BrandN3wP@ss!"}
    codes = [api_client.post(CHANGE_URL, payload, format="json").status_code for _ in range(3)]
    assert codes == [400, 400, 429]

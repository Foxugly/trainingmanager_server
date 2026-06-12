"""Coverage of POST /api/v1/auth/logout/ — JWT refresh-token blacklist.

The endpoint authenticates with the access token (Bearer) and revokes the
refresh token passed in the body. Once blacklisted, a refresh used at
/auth/token/refresh/ must be rejected (401) — that's the security
guarantee the token_blacklist app enables.
"""

import pytest
from rest_framework_simplejwt.tokens import RefreshToken

from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

LOGOUT_URL = "/api/v1/auth/logout/"
REFRESH_URL = "/api/v1/auth/token/refresh/"


def _bearer(api_client, user):
    """Authenticate api_client with a fresh JWT pair issued for `user`.
    Returns (access, refresh) so the test can drive both endpoints."""
    refresh = RefreshToken.for_user(user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return str(refresh.access_token), str(refresh)


def test_logout_valid_returns_204_and_blacklists_refresh(api_client):
    user = UserFactory()
    _, refresh = _bearer(api_client, user)

    response = api_client.post(LOGOUT_URL, {"refresh": refresh}, format="json")
    assert response.status_code == 204, response.content

    # Reuse of the now-blacklisted refresh must be rejected at /token/refresh/.
    refresh_resp = api_client.post(REFRESH_URL, {"refresh": refresh}, format="json")
    assert refresh_resp.status_code == 401, refresh_resp.json()


def test_refresh_after_logout_returns_401(api_client):
    """Direct check separate from the happy-path: a refresh used immediately
    after logout must not produce a new access token. Guards against future
    regressions where the blacklist app is silently disabled."""
    user = UserFactory()
    _, refresh = _bearer(api_client, user)
    api_client.post(LOGOUT_URL, {"refresh": refresh}, format="json")

    response = api_client.post(REFRESH_URL, {"refresh": refresh}, format="json")
    assert response.status_code == 401, response.json()


def test_logout_without_auth_header_returns_401(api_client):
    user = UserFactory()
    refresh = str(RefreshToken.for_user(user))
    # Note: api_client has no credentials() set → no Bearer header.
    response = api_client.post(LOGOUT_URL, {"refresh": refresh}, format="json")
    assert response.status_code == 401, response.json()


def test_logout_with_other_users_refresh_returns_400(api_client):
    """A holds an access token; B's refresh string is leaked. A tries to
    blacklist B's refresh via /auth/logout/ — must fail. Without this
    check, the endpoint would let any access-holder DOS another user's
    sessions."""
    alice = UserFactory(email="alice_logout@local.test")
    bob = UserFactory(email="bob_logout@local.test")
    bob_refresh = str(RefreshToken.for_user(bob))

    # Authenticate as alice
    _bearer(api_client, alice)

    response = api_client.post(LOGOUT_URL, {"refresh": bob_refresh}, format="json")
    assert response.status_code == 400, response.json()
    assert response.json()["code"] == "invalid_token"

    # Bob's refresh must still be usable.
    api_client.credentials()  # drop auth so the refresh call is anonymous
    refresh_resp = api_client.post(REFRESH_URL, {"refresh": bob_refresh}, format="json")
    assert refresh_resp.status_code == 200


def test_logout_with_malformed_refresh_returns_400(api_client):
    user = UserFactory()
    _bearer(api_client, user)

    response = api_client.post(LOGOUT_URL, {"refresh": "not.a.jwt"}, format="json")
    assert response.status_code == 400, response.json()
    assert response.json()["code"] == "invalid_token"


def test_logout_throttle_returns_429_after_limit(api_client, set_throttle_rate):
    """LogoutThrottle bounds abuse from a holder of an access token who
    might try to blacklist many candidate refresh strings in quick
    succession. Default 30/min in prod; lowered here for the test."""
    set_throttle_rate("auth_logout", "2/min")
    user = UserFactory()
    _bearer(api_client, user)

    # Burn the budget with 2 malformed-refresh calls.
    for _ in range(2):
        api_client.post(LOGOUT_URL, {"refresh": "not.a.jwt"}, format="json")

    response = api_client.post(LOGOUT_URL, {"refresh": "not.a.jwt"}, format="json")
    assert response.status_code == 429, response.json()

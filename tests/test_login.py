"""Coverage of POST /api/v1/auth/token/ — JWT login with email-confirmed gate.

Email-only auth: simplejwt authenticates on ``email`` (the USERNAME_FIELD).
VerifiedTokenObtainPairSerializer refuses login for users whose
``email_confirmed`` flag is False (the boolean that replaced allauth's
EmailAddress.verified gate).
"""

import pytest
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db


User = get_user_model()
TOKEN_URL = "/api/v1/auth/token/"


def _make_user(slug="login_user", password="Sup3rS@fePass!", confirmed=True):
    """Create a user. ``confirmed`` sets email_confirmed (the login gate)."""
    return User.objects.create_user(
        email=f"{slug}@local.test",
        password=password,
        email_confirmed=confirmed,
    )


def test_login_with_confirmed_user_returns_tokens(api_client):
    _make_user(slug="confirmed_login", confirmed=True)
    response = api_client.post(
        TOKEN_URL,
        {"email": "confirmed_login@local.test", "password": "Sup3rS@fePass!"},
        format="json",
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert "access" in body and "refresh" in body


def test_login_with_unconfirmed_user_returns_400_email_not_verified(api_client):
    _make_user(slug="unconfirmed_login", confirmed=False)
    response = api_client.post(
        TOKEN_URL,
        {"email": "unconfirmed_login@local.test", "password": "Sup3rS@fePass!"},
        format="json",
    )
    assert response.status_code == 400, response.json()
    assert response.json()["code"] == "email_not_verified"


# =====================================================================
# remember=true extends the refresh token TTL (access TTL untouched)
# =====================================================================


def _decode_token_exp(token_str):
    """Decode the JWT payload (without signature verification — we trust
    our own minted tokens) and return the `exp` epoch."""
    import base64
    import json

    payload_b64 = token_str.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)  # pad
    return json.loads(base64.urlsafe_b64decode(payload_b64))["exp"]


def test_login_without_remember_short_refresh_ttl(api_client):
    _make_user(slug="rem_short", confirmed=True)
    response = api_client.post(
        TOKEN_URL,
        {"email": "rem_short@local.test", "password": "Sup3rS@fePass!"},
        format="json",
    )
    assert response.status_code == 200
    body = response.json()
    refresh_exp = _decode_token_exp(body["refresh"])
    access_exp = _decode_token_exp(body["access"])
    # Default REFRESH_TOKEN_LIFETIME = 7 days; difference between exp and
    # now should be in [6.5d, 7.5d]. Loose bounds to dodge timing flakiness.
    import time

    delta_days = (refresh_exp - time.time()) / 86400
    assert 6.5 < delta_days < 7.5, f"refresh delta {delta_days} not ~7 days"
    # Access TTL is 60 minutes — make sure remember does NOT touch it.
    delta_access_min = (access_exp - time.time()) / 60
    assert 50 < delta_access_min < 70


def test_login_with_remember_extends_refresh_to_30_days(api_client):
    _make_user(slug="rem_long", confirmed=True)
    response = api_client.post(
        TOKEN_URL,
        {"email": "rem_long@local.test", "password": "Sup3rS@fePass!", "remember": True},
        format="json",
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    refresh_exp = _decode_token_exp(body["refresh"])
    access_exp = _decode_token_exp(body["access"])
    # REFRESH_TOKEN_LIFETIME_REMEMBER = 30 days; expect [29d, 31d].
    import time

    delta_days = (refresh_exp - time.time()) / 86400
    assert 29 < delta_days < 31, f"refresh delta {delta_days} not ~30 days"
    # Access still on standard 60 minutes
    delta_access_min = (access_exp - time.time()) / 60
    assert 50 < delta_access_min < 70

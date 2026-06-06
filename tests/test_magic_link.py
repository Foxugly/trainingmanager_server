"""Coverage of POST /api/v1/auth/magic-link/request/ + .../exchange/.

Endpoints:
- POST /api/v1/auth/magic-link/request/  — anti-leak request (constant 200)
- POST /api/v1/auth/magic-link/exchange/ — trade a signed token for a JWT pair

The signed token is minted by customuser.magic_link_token (a Django
TimestampSigner, 15-min TTL, payload = user id). Email send is captured
by the locmem backend (conftest forces it across the suite).

"Email confirmed" mirrors the login gate: a user with no allauth
EmailAddress row is legacy/confirmed; once an EmailAddress exists the
`verified` flag is authoritative.
"""

import re

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core import mail

from customuser.magic_link_token import (
    make_magic_link_token,
    parse_magic_link_token,
)

pytestmark = pytest.mark.django_db


User = get_user_model()

REQUEST_URL = "/api/v1/auth/magic-link/request/"
EXCHANGE_URL = "/api/v1/auth/magic-link/exchange/"


def _user(username="magic_user", verified=True, is_active=True):
    """Create a user. `verified` controls the allauth EmailAddress row:
    True = verified, False = unverified, None = no row at all (legacy)."""
    user = User.objects.create_user(
        username=username,
        email=f"{username}@local.test",
        password="S0meP@ssw0rd!",
        first_name="Magic",
        last_name="User",
        is_active=is_active,
    )
    if verified is True:
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    elif verified is False:
        EmailAddress.objects.create(user=user, email=user.email, verified=False, primary=True)
    return user


def _extract_token(body: str) -> str:
    """Pull the magic-link token out of the email body."""
    match = re.search(r"/auth/magic-link/([^\s/]+)", body)
    assert match, f"no magic-link token found in body:\n{body}"
    return match.group(1)


# =====================================================================
# /auth/magic-link/request/
# =====================================================================


def test_request_for_confirmed_user_sends_one_email_with_token(api_client):
    user = _user(verified=True)
    mail.outbox = []
    response = api_client.post(REQUEST_URL, {"email": user.email}, format="json")

    assert response.status_code == 200
    assert response.json() == {"detail": "ok"}
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]

    body = mail.outbox[0].body
    assert "/auth/magic-link/" in body
    # Token in the link parses back to this user's id.
    token = _extract_token(body)
    assert parse_magic_link_token(token) == user.id


def test_request_for_legacy_user_no_emailaddress_sends_email(api_client):
    """Legacy accounts (no allauth EmailAddress row) count as confirmed."""
    user = _user(username="legacy_magic", verified=None)
    mail.outbox = []
    response = api_client.post(REQUEST_URL, {"email": user.email}, format="json")

    assert response.status_code == 200
    assert response.json() == {"detail": "ok"}
    assert len(mail.outbox) == 1


def test_request_for_unknown_email_returns_200_no_mail(api_client):
    """Anti-leak: identical 200 + identical body, no email."""
    mail.outbox = []
    response = api_client.post(REQUEST_URL, {"email": "ghost@local.test"}, format="json")

    assert response.status_code == 200
    assert response.json() == {"detail": "ok"}
    assert mail.outbox == []


def test_request_for_unconfirmed_user_returns_200_no_mail(api_client):
    """An unverified (non-legacy) user is not eligible: same 200, no login email."""
    user = _user(username="unconfirmed_magic", verified=False)
    mail.outbox = []
    response = api_client.post(REQUEST_URL, {"email": user.email}, format="json")

    assert response.status_code == 200
    assert response.json() == {"detail": "ok"}
    assert mail.outbox == []


def test_request_for_inactive_user_returns_200_no_mail(api_client):
    user = _user(username="inactive_magic", verified=True, is_active=False)
    mail.outbox = []
    response = api_client.post(REQUEST_URL, {"email": user.email}, format="json")

    assert response.status_code == 200
    assert response.json() == {"detail": "ok"}
    assert mail.outbox == []


def test_request_throttled_after_limit(api_client, set_throttle_rate):
    """5/hour by default. Reduce to 2/min for the test budget."""
    set_throttle_rate("auth_magic_link_request", "2/min")
    payload = {"email": "spam@local.test"}
    statuses = [
        api_client.post(REQUEST_URL, payload, format="json").status_code for _ in range(3)
    ]
    assert statuses[:2] == [200, 200]
    assert statuses[2] == 429


# =====================================================================
# /auth/magic-link/exchange/
# =====================================================================


def test_exchange_valid_token_returns_jwt_pair(api_client):
    user = _user(username="exchange_ok", verified=True)
    token = make_magic_link_token(user.id)
    response = api_client.post(EXCHANGE_URL, {"token": token}, format="json")

    assert response.status_code == 200, response.json()
    body = response.json()
    assert "access" in body and "refresh" in body
    assert isinstance(body["access"], str) and body["access"]
    assert isinstance(body["refresh"], str) and body["refresh"]

    # The access token authenticates a subsequent request.
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access']}")
    me = api_client.get("/api/v1/me/")
    assert me.status_code == 200
    assert me.json()["username"] == user.username


def test_exchange_garbage_token_returns_400_token_invalid(api_client):
    response = api_client.post(
        EXCHANGE_URL, {"token": "totally.garbage.token"}, format="json"
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "token_invalid"}


def test_exchange_tampered_token_returns_400_token_invalid(api_client):
    user = _user(username="exchange_tamper", verified=True)
    token = make_magic_link_token(user.id)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    response = api_client.post(EXCHANGE_URL, {"token": tampered}, format="json")
    assert response.status_code == 400
    assert response.json() == {"detail": "token_invalid"}


def test_exchange_expired_token_returns_410_token_expired(api_client, monkeypatch):
    """Patch the TTL to 0 so a freshly-minted token reads as expired on
    exchange (the signer timestamp is older than max_age=0)."""
    import customuser.views as views_mod

    user = _user(username="exchange_expired", verified=True)
    token = make_magic_link_token(user.id)

    # The view imports TOKEN_MAX_AGE_SECONDS from magic_link_token at call
    # time; patch the source constant to a negative value to force expiry.
    monkeypatch.setattr(
        "customuser.magic_link_token.TOKEN_MAX_AGE_SECONDS", -1, raising=True
    )
    assert views_mod  # imported for clarity; view re-imports the constant

    response = api_client.post(EXCHANGE_URL, {"token": token}, format="json")
    assert response.status_code == 410
    assert response.json() == {"detail": "token_expired"}


def test_exchange_for_deactivated_user_returns_400_token_invalid(api_client):
    user = _user(username="exchange_deactivated", verified=True)
    token = make_magic_link_token(user.id)

    user.is_active = False
    user.save(update_fields=["is_active"])

    response = api_client.post(EXCHANGE_URL, {"token": token}, format="json")
    assert response.status_code == 400
    assert response.json() == {"detail": "token_invalid"}


def test_exchange_for_deleted_user_returns_400_token_invalid(api_client):
    user = _user(username="exchange_deleted", verified=True)
    token = make_magic_link_token(user.id)
    user.delete()

    response = api_client.post(EXCHANGE_URL, {"token": token}, format="json")
    assert response.status_code == 400
    assert response.json() == {"detail": "token_invalid"}

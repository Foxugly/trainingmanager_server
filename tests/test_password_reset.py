"""Coverage of POST /api/v1/auth/password/reset/ + .../confirm/.

Endpoints:
- POST /api/v1/auth/password/reset/         — anti-leak request
- POST /api/v1/auth/password/reset/confirm/ — finalize with key + new_password

Allauth's `default_token_generator` (EmailAwarePasswordResetTokenGenerator)
mints / validates the {uid}-{token} key. Email send is captured by the
locmem backend; Turnstile is monkeypatched to True unless a test
explicitly flips it.
"""

import pytest
from allauth.account.forms import default_token_generator
from allauth.account.utils import user_pk_to_url_str
from django.contrib.auth import get_user_model
from django.core import mail

pytestmark = pytest.mark.django_db


User = get_user_model()


REQUEST_URL = "/api/v1/auth/password/reset/"
CONFIRM_URL = "/api/v1/auth/password/reset/confirm/"


@pytest.fixture(autouse=True)
def turnstile_pass(monkeypatch):
    """Default: Turnstile succeeds. Tests that need failure flip this
    inline."""
    monkeypatch.setattr(
        "customuser.views.password_reset.verify_turnstile_token", lambda token, remote_ip=None: True
    )


def _user(username="reset_user"):
    return User.objects.create_user(
        username=username,
        email=f"{username}@local.test",
        password="OldP@ssw0rd!",
        first_name="Reset",
        last_name="User",
    )


def _make_key(user):
    """Mint a {uid}-{token} key the way the request endpoint does."""
    uid = user_pk_to_url_str(user)
    token = default_token_generator.make_token(user)
    return f"{uid}-{token}"


# =====================================================================
# /auth/password/reset/  (request)
# =====================================================================


def test_reset_request_with_valid_email_sends_mail(api_client):
    user = _user()
    mail.outbox = []
    response = api_client.post(
        REQUEST_URL,
        {"email": user.email, "turnstile_token": "ok"},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["code"] == "password_reset_processed"
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    # Frontend URL: no trailing slash, matches Angular routing convention.
    assert "/auth/reset-password/" in mail.outbox[0].body
    assert not mail.outbox[0].body.rstrip().endswith("/")


def test_reset_request_unknown_email_returns_200_no_mail(api_client):
    """Anti-leak: identical 200 + identical body for unknown emails."""
    mail.outbox = []
    response = api_client.post(
        REQUEST_URL,
        {"email": "ghost@local.test", "turnstile_token": "ok"},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["code"] == "password_reset_processed"
    assert mail.outbox == []


def test_reset_request_invalid_turnstile_returns_400(api_client, monkeypatch):
    monkeypatch.setattr(
        "customuser.views.password_reset.verify_turnstile_token", lambda token, remote_ip=None: False
    )
    user = _user()
    mail.outbox = []
    response = api_client.post(
        REQUEST_URL,
        {"email": user.email, "turnstile_token": "bad"},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "captcha_failed"
    assert mail.outbox == []


def test_reset_request_throttled_after_3_per_hour(api_client, set_throttle_rate):
    """3/hour by default. Reduce to 2/min for the test budget."""
    set_throttle_rate("auth_password_reset", "2/min")
    payload = {"email": "spam@local.test", "turnstile_token": "ok"}
    statuses = [api_client.post(REQUEST_URL, payload, format="json").status_code for _ in range(3)]
    assert statuses[:2] == [200, 200]
    assert statuses[2] == 429


# =====================================================================
# /auth/password/reset/confirm/
# =====================================================================


def test_reset_confirm_with_valid_key_and_password_returns_tokens(api_client):
    user = _user()
    key = _make_key(user)
    response = api_client.post(
        CONFIRM_URL,
        {"key": key, "new_password": "BrandN3wP@ss!"},
        format="json",
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert "access" in body and "refresh" in body
    assert body["user"]["username"] == user.username

    user.refresh_from_db()
    assert user.check_password("BrandN3wP@ss!")
    assert not user.check_password("OldP@ssw0rd!")


def test_reset_confirm_invalid_key_returns_400(api_client):
    response = api_client.post(
        CONFIRM_URL,
        {"key": "garbage-token", "new_password": "BrandN3wP@ss!"},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_or_expired_token"


def test_reset_confirm_weak_password_returns_400(api_client):
    user = _user()
    key = _make_key(user)
    response = api_client.post(
        CONFIRM_URL,
        {"key": key, "new_password": "12345678"},  # too common, too numeric
        format="json",
    )
    assert response.status_code == 400
    body = response.json()
    # Field-level error: new_password rejected by Django validators.
    assert "new_password" in body.get("fields", {}) or body.get("code") in (
        "weak_password",
        "validation_error",
    )

    # Password unchanged
    user.refresh_from_db()
    assert user.check_password("OldP@ssw0rd!")


def test_reset_confirm_token_consumed_once_login_with_new_password(api_client):
    """Sanity: after a successful reset, the user can /auth/token/ with
    the new password."""
    user = _user()
    key = _make_key(user)
    api_client.post(
        CONFIRM_URL,
        {"key": key, "new_password": "BrandN3wP@ss!"},
        format="json",
    )

    response = api_client.post(
        "/api/v1/auth/token/",
        {"username": user.username, "password": "BrandN3wP@ss!"},
        format="json",
    )
    assert response.status_code == 200
    assert "access" in response.json()

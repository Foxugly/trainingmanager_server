"""Coverage of the public registration + email-confirmation flow.

Endpoints:
- POST /api/v1/auth/register/      — public self-signup, sends confirmation mail
- POST /api/v1/auth/email/confirm/ — verifies the email + returns JWT pair
- POST /api/v1/auth/email/resend/  — re-sends the confirmation link (anti-leak)

Email-only: there is no username. Confirmation uses Django's
default_token_generator + a base64url uid (the "<uid>-<token>" key), and the
account becomes usable once ``email_confirmed`` flips True.

The autouse `use_locmem_email_backend` fixture (tests/conftest.py) routes
mail to django.core.mail.outbox so we never hit Graph in tests.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core import mail

from customuser.email_tokens import make_key

pytestmark = pytest.mark.django_db


User = get_user_model()


REGISTER_URL = "/api/v1/auth/register/"
CONFIRM_URL = "/api/v1/auth/email/confirm/"
RESEND_URL = "/api/v1/auth/email/resend/"


def _valid_payload(**overrides):
    base = {
        "email": "newcomer@local.test",
        "password": "Sup3rS@fePass!",
        "first_name": "New",
        "last_name": "Comer",
        "language": "en",
        "turnstile_token": "test-mock-token",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def turnstile_pass(monkeypatch):
    """Default: Turnstile verification succeeds. Override per-test by
    patching tools.turnstile.verify_turnstile_token directly."""
    monkeypatch.setattr(
        "customuser.views.registration.verify_turnstile_token", lambda token, remote_ip=None: True
    )


# =====================================================================
# /auth/register/
# =====================================================================


def test_register_valid_payload_creates_user_and_sends_mail(api_client):
    mail.outbox = []
    response = api_client.post(REGISTER_URL, _valid_payload(), format="json")
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["code"] == "registration_pending_verification"
    assert body["email"] == "newcomer@local.test"
    assert "access" not in body and "refresh" not in body  # no JWT pre-verification

    user = User.objects.get(email="newcomer@local.test")
    assert user.is_active is True  # is_active unrelated; the gate is email_confirmed
    assert user.email_confirmed is False
    assert len(mail.outbox) == 1
    assert "newcomer@local.test" in mail.outbox[0].to


def test_register_email_taken_returns_400(api_client):
    User.objects.create_user(email="dup@local.test", password="x")
    response = api_client.post(REGISTER_URL, _valid_payload(email="dup@local.test"), format="json")
    assert response.status_code == 400
    body = response.json()
    assert body["fields"]["email"][0]["code"] == "email_taken"


def test_register_password_too_short_returns_400(api_client):
    response = api_client.post(REGISTER_URL, _valid_payload(password="short"), format="json")
    assert response.status_code == 400
    assert "password" in response.json().get("fields", {})


# =====================================================================
# /auth/email/confirm/
# =====================================================================


def _register_and_get_key(api_client):
    """Helper: register a user and mint the confirmation key for them."""
    api_client.post(REGISTER_URL, _valid_payload(), format="json")
    user = User.objects.get(email="newcomer@local.test")
    return make_key(user), user


def test_email_confirm_valid_key_returns_tokens(api_client):
    mail.outbox = []
    key, user = _register_and_get_key(api_client)

    response = api_client.post(CONFIRM_URL, {"key": key}, format="json")
    assert response.status_code == 200, response.json()
    body = response.json()
    assert "access" in body and "refresh" in body
    assert body["user"]["email"] == "newcomer@local.test"

    user.refresh_from_db()
    assert user.email_confirmed is True


def test_email_confirm_invalid_key_returns_400(api_client):
    response = api_client.post(CONFIRM_URL, {"key": "totally-bogus-key"}, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_or_expired_token"


# =====================================================================
# /auth/email/resend/
# =====================================================================


def test_email_resend_existing_unverified_sends_new_mail(api_client):
    api_client.post(REGISTER_URL, _valid_payload(), format="json")
    mail.outbox = []  # discard signup mail

    response = api_client.post(RESEND_URL, {"email": "newcomer@local.test"}, format="json")
    assert response.status_code == 200
    assert response.json()["code"] == "resend_processed"
    assert len(mail.outbox) == 1
    assert "newcomer@local.test" in mail.outbox[0].to


def test_email_resend_unknown_email_returns_200_no_mail(api_client):
    """Anti-leak: identical 200 + identical body for unknown emails. No mail sent."""
    mail.outbox = []
    response = api_client.post(RESEND_URL, {"email": "ghost@local.test"}, format="json")
    assert response.status_code == 200
    assert response.json()["code"] == "resend_processed"
    assert mail.outbox == []


# =====================================================================
# Turnstile captcha (Batch 2)
# =====================================================================


def test_register_turnstile_invalid_returns_400_no_user_created(api_client, monkeypatch):
    """Fail-closed: a rejected Turnstile token blocks registration before
    any DB write."""
    monkeypatch.setattr(
        "customuser.views.registration.verify_turnstile_token", lambda token, remote_ip=None: False
    )
    mail.outbox = []
    response = api_client.post(REGISTER_URL, _valid_payload(), format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "captcha_failed"
    assert not User.objects.filter(email="newcomer@local.test").exists()
    assert mail.outbox == []


def test_register_missing_turnstile_token_returns_400(api_client):
    payload = _valid_payload()
    del payload["turnstile_token"]
    response = api_client.post(REGISTER_URL, payload, format="json")
    assert response.status_code == 400
    body = response.json()
    assert "turnstile_token" in body.get("fields", {})

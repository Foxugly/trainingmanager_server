"""Coverage of the verified change-email flow (E7)."""

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core import mail

from customuser.email_change import make_email_change_token

pytestmark = pytest.mark.django_db

User = get_user_model()

REQUEST_URL = "/api/v1/me/email/change/"
CONFIRM_URL = "/api/v1/auth/email/change/confirm/"


@pytest.fixture
def user(db):
    u = User.objects.create_user(
        username="ec_user", email="old@local.test", password="pass", language="en"
    )
    EmailAddress.objects.create(user=u, email="old@local.test", primary=True, verified=True)
    return u


def test_request_sends_link_then_confirm_swaps_email(api_client, user):
    mail.outbox = []
    api_client.force_authenticate(user=user)
    resp = api_client.post(REQUEST_URL, {"new_email": "new@local.test"}, format="json")
    assert resp.status_code == 200, resp.content
    assert resp.json()["code"] == "email_change_requested"
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["new@local.test"]
    assert "/auth/confirm-email-change/" in mail.outbox[0].body

    # Confirm is public; mint the token the way the request endpoint did.
    api_client.force_authenticate(user=None)
    token = make_email_change_token(user.pk, "new@local.test")
    resp2 = api_client.post(CONFIRM_URL, {"token": token}, format="json")
    assert resp2.status_code == 200, resp2.content
    assert resp2.json()["code"] == "email_changed"

    user.refresh_from_db()
    assert user.email == "new@local.test"
    assert EmailAddress.objects.filter(
        user=user, email="new@local.test", primary=True, verified=True
    ).exists()
    assert not EmailAddress.objects.filter(user=user, email="old@local.test", primary=True).exists()


def test_request_same_email_returns_400(api_client, user):
    api_client.force_authenticate(user=user)
    resp = api_client.post(REQUEST_URL, {"new_email": "old@local.test"}, format="json")
    assert resp.status_code == 400
    assert "new_email" in resp.json().get("fields", {})


def test_request_taken_email_returns_400(api_client, user):
    User.objects.create_user(username="other", email="taken@local.test", password="pass")
    api_client.force_authenticate(user=user)
    resp = api_client.post(REQUEST_URL, {"new_email": "taken@local.test"}, format="json")
    assert resp.status_code == 400


def test_request_unauthenticated_returns_401(api_client):
    resp = api_client.post(REQUEST_URL, {"new_email": "x@local.test"}, format="json")
    assert resp.status_code == 401


def test_confirm_tampered_token_returns_400(api_client, user):
    resp = api_client.post(CONFIRM_URL, {"token": "garbage"}, format="json")
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_or_expired_token"


def test_confirm_email_grabbed_meanwhile_returns_409(api_client, user):
    token = make_email_change_token(user.pk, "race@local.test")
    User.objects.create_user(username="racer", email="race@local.test", password="pass")
    resp = api_client.post(CONFIRM_URL, {"token": token}, format="json")
    assert resp.status_code == 409
    assert resp.json()["code"] == "email_taken"
    user.refresh_from_db()
    assert user.email == "old@local.test"


def test_confirm_expired_token_returns_410(api_client, user, monkeypatch):
    import customuser.email_change as ec

    token = make_email_change_token(user.pk, "later@local.test")
    monkeypatch.setattr(ec, "TOKEN_MAX_AGE_SECONDS", -1)
    resp = api_client.post(CONFIRM_URL, {"token": token}, format="json")
    assert resp.status_code == 410
    assert resp.json()["code"] == "token_expired"

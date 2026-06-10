"""Coverage of the one-click RSVP magic link (server-rendered).

  - /api/v1/rsvp-magic/<token>/  GET  -> confirmation page (NO mutation)
                                 POST -> idempotent upsert of the member's RSVP

The signed token (member-scoped, 72h TTL) is the whole credential — the
endpoint is unauthenticated and CSRF-exempt. Two-step by design so an email
link-prefetcher hitting the GET cannot silently set a status.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from event.models import Event
from member.models import Member
from rsvp import magic_rsvp
from rsvp.magic_rsvp import make_token, parse_token
from rsvp.models import Rsvp
from team.models import TeamMembership
from tests.factories import ProgramFactory, TeamFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def team(db):
    owner = User.objects.create_user(
        username="magic_coach", email="magic_coach@local.test", password="pass"
    )
    return TeamFactory(owner=owner, is_active=True, rsvp_enabled=True)


@pytest.fixture
def event(team):
    program = ProgramFactory(team=team)
    return Event.objects.create(
        refer_program=program,
        name="Magic RSVP event",
        date=timezone.localdate() + timedelta(days=1),
    )


@pytest.fixture
def member(team):
    user = User.objects.create_user(
        username="magic_athlete", email="magic_athlete@local.test", password="pass"
    )
    m = Member.objects.create(firstname="M", lastname="A", email=user.email, user=user)
    TeamMembership.objects.create(team=team, member=m)
    return m


@pytest.fixture
def client():
    return Client()


def _url(token):
    return f"/api/v1/rsvp-magic/{token}/"


# ---------------------------------------------------------------------------
# token module
# ---------------------------------------------------------------------------


def test_token_roundtrip():
    token = make_token(7, 42, "going")
    assert parse_token(token) == (7, 42, "going")


def test_parse_token_bad_signature_returns_none():
    assert parse_token("not-a-real-token") is None


def test_parse_token_tampered_status_returns_none():
    # A correctly-signed payload whose status is not a real RsvpStatus must be
    # rejected (make_token itself refuses to mint one, so sign it directly).
    forged = magic_rsvp._TOKEN._signer().sign("1:2:bogus")
    assert parse_token(forged) is None


def test_make_token_rejects_bad_status():
    with pytest.raises(ValueError):
        make_token(1, 2, "bogus")


def test_parse_token_expired_raises(monkeypatch):
    token = make_token(1, 2, "going")
    monkeypatch.setattr(magic_rsvp._TOKEN, "max_age", -1)
    with pytest.raises(magic_rsvp.SignatureExpired):
        parse_token(token)


# ---------------------------------------------------------------------------
# GET is non-mutating (link-prefetch safe)
# ---------------------------------------------------------------------------


def test_get_renders_confirmation_without_mutating(client, event, member):
    token = make_token(event.pk, member.pk, "going")
    response = client.get(_url(token))
    assert response.status_code == 200
    assert b"<form method=\"post\">" in response.content
    assert not Rsvp.objects.filter(event=event, member=member).exists()


def test_get_carries_strict_nonced_csp(client, event, member):
    """The page sets its own locked-down CSP (overriding the global policy):
    default-src 'none', and the inline <style> is admitted only via a nonce
    that matches the header."""
    import re

    token = make_token(event.pk, member.pk, "going")
    response = client.get(_url(token))
    csp = response.headers.get("Content-Security-Policy")
    assert csp is not None
    assert "default-src 'none'" in csp
    assert "form-action 'self'" in csp
    m = re.search(r"style-src 'nonce-([^']+)'", csp)
    assert m, csp
    assert f'<style nonce="{m.group(1)}">'.encode() in response.content


# ---------------------------------------------------------------------------
# POST performs the upsert
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["going", "maybe", "not_going"])
def test_post_creates_rsvp(client, event, member, status):
    token = make_token(event.pk, member.pk, status)
    response = client.post(_url(token))
    assert response.status_code == 200
    rsvp = Rsvp.objects.get(event=event, member=member)
    assert rsvp.status == status


def test_post_is_idempotent_and_overwrites(client, event, member):
    client.post(_url(make_token(event.pk, member.pk, "maybe")))
    client.post(_url(make_token(event.pk, member.pk, "going")))
    assert Rsvp.objects.filter(event=event, member=member).count() == 1
    assert Rsvp.objects.get(event=event, member=member).status == "going"


# ---------------------------------------------------------------------------
# error pages
# ---------------------------------------------------------------------------


def test_invalid_token_returns_400(client):
    response = client.get(_url("garbage"))
    assert response.status_code == 400


def test_expired_token_returns_410(client, event, member, monkeypatch):
    token = make_token(event.pk, member.pk, "going")
    monkeypatch.setattr(magic_rsvp._TOKEN, "max_age", -1)
    response = client.get(_url(token))
    assert response.status_code == 410
    assert not Rsvp.objects.filter(event=event, member=member).exists()


def test_rsvp_disabled_returns_409(client, event, member, team):
    team.rsvp_enabled = False
    team.save(update_fields=["rsvp_enabled"])
    token = make_token(event.pk, member.pk, "going")
    response = client.post(_url(token))
    assert response.status_code == 409
    assert not Rsvp.objects.filter(event=event, member=member).exists()


def test_left_member_returns_403(client, event, member, team):
    TeamMembership.objects.filter(team=team, member=member).update(
        left_at=timezone.now()
    )
    token = make_token(event.pk, member.pk, "going")
    response = client.post(_url(token))
    assert response.status_code == 403
    assert not Rsvp.objects.filter(event=event, member=member).exists()


def test_unknown_event_returns_404(client, member):
    token = make_token(999999, member.pk, "going")
    response = client.get(_url(token))
    assert response.status_code == 404

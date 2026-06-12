"""Coverage of GET /api/v1/teams/{id}/roti-drift/ (F1).

Per-athlete perceived-effort (ROTI) drift vs the squad (managers only)."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from event.models import Event
from member.models import Member
from roti.models import Roti
from team.models import TeamMembership
from tests.factories import ProgramFactory, TeamFactory

pytestmark = pytest.mark.django_db
User = get_user_model()


def _url(team_pk):
    return f"/api/v1/teams/{team_pk}/roti-drift/"


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="rd_o@x.test", password="p")


@pytest.fixture
def team(owner):
    return TeamFactory(owner=owner, is_active=True)


@pytest.fixture
def program(team):
    return ProgramFactory(team=team)


def _event(program, n):
    return Event.objects.create(
        refer_program=program, name=f"S{n}", date=timezone.localdate() - timedelta(days=n)
    )


def _member(team, first):
    m = Member.objects.create(firstname=first, lastname="X")
    TeamMembership.objects.create(team=team, member=m)
    return m


def test_unauthenticated_returns_401(api_client, team):
    assert api_client.get(_url(team.pk)).status_code == 401


def test_athlete_forbidden(api_client, team):
    athlete = User.objects.create_user(email="rd_a@x.test", password="p")
    m = Member.objects.create(firstname="A", lastname="T", user=athlete)
    TeamMembership.objects.create(team=team, member=m)
    api_client.force_authenticate(user=athlete)
    assert api_client.get(_url(team.pk)).status_code == 403


def test_flags_high_and_low_drift(api_client, owner, team, program):
    """One athlete rates hard (5s), one easy (1s) -> high / low flags around the
    squad mean."""
    hard = _member(team, "Hard")
    easy = _member(team, "Easy")
    for i in range(3):
        e = _event(program, i + 1)
        Roti.objects.create(event=e, member=hard, score=5)
        Roti.objects.create(event=e, member=easy, score=1)

    api_client.force_authenticate(user=owner)
    resp = api_client.get(_url(team.pk))
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["squad_average"] == pytest.approx(3.0)  # mean of 5s and 1s
    by_name = {e["name"]: e for e in body["entries"]}
    assert by_name["Hard X"]["flag"] == "high"
    assert by_name["Hard X"]["delta"] == pytest.approx(2.0)
    assert by_name["Easy X"]["flag"] == "low"


def test_empty_window_returns_nulls(api_client, owner, team):
    api_client.force_authenticate(user=owner)
    body = api_client.get(_url(team.pk)).json()
    assert body["squad_average"] is None
    assert body["count"] == 0
    assert body["entries"] == []


def test_aligned_athlete_is_normal(api_client, owner, team, program):
    a = _member(team, "Aa")
    b = _member(team, "Bb")
    for i in range(2):
        e = _event(program, i + 1)
        Roti.objects.create(event=e, member=a, score=3)
        Roti.objects.create(event=e, member=b, score=3)
    api_client.force_authenticate(user=owner)
    body = api_client.get(_url(team.pk)).json()
    assert all(e["flag"] == "normal" for e in body["entries"])

"""Coverage of GET /api/v1/teams/{id}/rsvp-reliability/ (F4).

Per-athlete going-but-absent reliability over the window (managers only)."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from attendance.models import Attendance, AttendanceStatus
from event.models import Event
from member.models import Member
from rsvp.models import Rsvp
from team.models import TeamMembership
from tests.factories import ProgramFactory, TeamFactory

pytestmark = pytest.mark.django_db
User = get_user_model()


def _url(team_pk):
    return f"/api/v1/teams/{team_pk}/rsvp-reliability/"


@pytest.fixture
def present_status(db):
    return AttendanceStatus.objects.get(code="present")


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="rel_o@x.test", password="p")


@pytest.fixture
def team(owner):
    return TeamFactory(owner=owner, is_active=True, rsvp_enabled=True)


@pytest.fixture
def program(team):
    return ProgramFactory(team=team)


def _event(program, days_ago):
    return Event.objects.create(
        refer_program=program,
        name=f"S{days_ago}",
        date=timezone.localdate() - timedelta(days=days_ago),
    )


def _member(team):
    m = Member.objects.create(firstname="Ath", lastname="Lete")
    TeamMembership.objects.create(team=team, member=m)
    return m


def test_unauthenticated_returns_401(api_client, team):
    assert api_client.get(_url(team.pk)).status_code == 401


def test_athlete_member_forbidden(api_client, team):
    athlete = User.objects.create_user(email="rel_a@x.test", password="p")
    m = Member.objects.create(firstname="A", lastname="T", user=athlete)
    TeamMembership.objects.create(team=team, member=m)
    api_client.force_authenticate(user=athlete)
    assert api_client.get(_url(team.pk)).status_code == 403


def test_reliability_counts_shows_and_no_shows(
    api_client, owner, team, program, present_status
):
    m = _member(team)
    e1, e2, e3 = _event(program, 3), _event(program, 5), _event(program, 7)
    # RSVP going to all three.
    for e in (e1, e2, e3):
        Rsvp.objects.create(event=e, member=m, status="going")
    # Present at e1 only -> 1 show, 2 no-shows.
    Attendance.objects.create(event=e1, member=m, status=present_status)

    api_client.force_authenticate(user=owner)
    resp = api_client.get(_url(team.pk))
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert "period" in body
    entry = body["entries"][0]
    assert entry["going"] == 3
    assert entry["shows"] == 1
    assert entry["no_shows"] == 2
    assert entry["reliability"] == pytest.approx(1 / 3)


def test_non_going_rsvp_excluded(api_client, owner, team, program, present_status):
    """Only GOING RSVPs count; a maybe/not_going athlete is omitted entirely."""
    m = _member(team)
    e = _event(program, 2)
    Rsvp.objects.create(event=e, member=m, status="maybe")

    api_client.force_authenticate(user=owner)
    body = api_client.get(_url(team.pk)).json()
    assert body["entries"] == []


def test_perfect_attendance_reliability_1(api_client, owner, team, program, present_status):
    m = _member(team)
    e = _event(program, 1)
    Rsvp.objects.create(event=e, member=m, status="going")
    Attendance.objects.create(event=e, member=m, status=present_status)

    api_client.force_authenticate(user=owner)
    entry = api_client.get(_url(team.pk)).json()["entries"][0]
    assert entry["reliability"] == 1.0
    assert entry["no_shows"] == 0

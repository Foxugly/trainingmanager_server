"""Coverage of GET /api/v1/events/{id}/debrief/ (F5) + the editable debrief field."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from attendance.models import Attendance, AttendanceStatus
from event.models import Event
from member.models import Member
from roti.models import Roti
from rsvp.models import Rsvp
from team.models import TeamMembership
from tests.factories import ProgramFactory, TeamFactory

pytestmark = pytest.mark.django_db
User = get_user_model()


def _url(event_pk):
    return f"/api/v1/events/{event_pk}/debrief/"


@pytest.fixture
def present_status(db):
    return AttendanceStatus.objects.get(code="present")


@pytest.fixture
def owner(db):
    return User.objects.create_user(username="db_owner", email="db_o@x.test", password="p")


@pytest.fixture
def team(owner):
    return TeamFactory(owner=owner, is_active=True, rsvp_enabled=True)


@pytest.fixture
def event(team):
    program = ProgramFactory(team=team)
    return Event.objects.create(
        refer_program=program, name="Sess", date=timezone.localdate() - timedelta(days=1)
    )


def _member(team):
    m = Member.objects.create(firstname="A", lastname="T")
    TeamMembership.objects.create(team=team, member=m)
    return m


def test_unauthenticated_returns_401(api_client, event):
    assert api_client.get(_url(event.pk)).status_code == 401


def test_athlete_forbidden(api_client, team, event):
    athlete = User.objects.create_user(username="db_ath", email="db_a@x.test", password="p")
    m = Member.objects.create(firstname="Ath", lastname="L", user=athlete)
    TeamMembership.objects.create(team=team, member=m)
    api_client.force_authenticate(user=athlete)
    assert api_client.get(_url(event.pk)).status_code == 403


def test_manager_gets_consolidated_debrief(api_client, owner, team, event, present_status):
    m1, m2 = _member(team), _member(team)
    Attendance.objects.create(event=event, member=m1, status=present_status)
    Roti.objects.create(event=event, member=m1, score=4)
    Roti.objects.create(event=event, member=m2, score=2)
    Rsvp.objects.create(event=event, member=m1, status="going")
    Rsvp.objects.create(event=event, member=m2, status="not_going")

    api_client.force_authenticate(user=owner)
    resp = api_client.get(_url(event.pk))
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["attendance"] == {"present": 1, "absent": 0, "total": 2}
    assert body["roti"]["average"] == pytest.approx(3.0)
    assert body["roti"]["count"] == 2
    assert body["rsvp"]["going"] == 1
    assert body["rsvp"]["not_going"] == 1
    assert body["rsvp"]["no_response"] == 0
    assert body["attachments_count"] == 0
    assert body["debrief"] == ""


def test_debrief_field_editable_via_patch(api_client, owner, team, event):
    api_client.force_authenticate(user=owner)
    resp = api_client.patch(
        f"/api/v1/events/{event.pk}/", {"debrief": "Great session, push next week."},
        format="json",
    )
    assert resp.status_code == 200, resp.json()
    event.refresh_from_db()
    assert event.debrief == "Great session, push next week."
    # And it surfaces in the debrief endpoint.
    assert api_client.get(_url(event.pk)).json()["debrief"] == "Great session, push next week."

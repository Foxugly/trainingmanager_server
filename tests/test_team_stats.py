"""Coverage of GET /api/v1/teams/{team_pk}/stats/ — read-only team statistics.

Permissions:
  - Owner / manager of the team: 200 with aggregated payload
  - Athlete / other authenticated user: 403
  - Unauthenticated: 401

Aggregation:
  - attendance (team_rate, by_session, by_member)
  - volume (total_distance, by_week, by_member)
  - intensity (by_segment)
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from attendance.models import Attendance, AttendanceStatus
from event.models import Event
from member.models import Member
from team.models import TeamMembership
from tests.factories import (
    EnergySegmentFactory,
    ExerciseFactory,
    ProgramFactory,
    RoundFactory,
    TeamFactory,
)

pytestmark = pytest.mark.django_db

User = get_user_model()


def _url(team_pk):
    return f"/api/v1/teams/{team_pk}/stats/"


@pytest.fixture
def present_status(db):
    return AttendanceStatus.objects.get(code="present")


@pytest.fixture
def absent_status(db):
    return AttendanceStatus.objects.get(code="absent")


@pytest.fixture
def owner_user(db):
    return User.objects.create_user(
        username="stats_owner", email="stats_owner@local.test", password="pass"
    )


@pytest.fixture
def team(owner_user):
    return TeamFactory(owner=owner_user, is_active=True)


@pytest.fixture
def program(team):
    return ProgramFactory(team=team)


@pytest.fixture
def owner_client(api_client, owner_user):
    api_client.force_authenticate(user=owner_user)
    return api_client


def _make_member(team, firstname, lastname, with_user=False):
    user = None
    if with_user:
        user = User.objects.create_user(
            username=f"{firstname}{lastname}".lower(),
            email=f"{firstname}.{lastname}@local.test".lower(),
            password="pass",
        )
    member = Member.objects.create(
        firstname=firstname, lastname=lastname, email=f"{firstname}@x.test", user=user
    )
    TeamMembership.objects.create(team=team, member=member)
    return member


# =====================================================================
# Permissions
# =====================================================================


def test_unauthenticated_returns_401(api_client, team):
    resp = api_client.get(_url(team.pk))
    assert resp.status_code == 401


def test_owner_gets_200(owner_client, team):
    resp = owner_client.get(_url(team.pk))
    assert resp.status_code == 200


def test_manager_gets_200(api_client, team):
    mgr = User.objects.create_user(
        username="stats_mgr", email="stats_mgr@local.test", password="pass"
    )
    team.managers.add(mgr)
    api_client.force_authenticate(user=mgr)
    resp = api_client.get(_url(team.pk))
    assert resp.status_code == 200


def test_athlete_member_gets_403(api_client, team):
    athlete = User.objects.create_user(
        username="stats_athlete", email="stats_athlete@local.test", password="pass"
    )
    member = Member.objects.create(
        firstname="Ath", lastname="Lete", email="a@x.test", user=athlete
    )
    TeamMembership.objects.create(team=team, member=member)
    api_client.force_authenticate(user=athlete)
    resp = api_client.get(_url(team.pk))
    assert resp.status_code == 403


def test_other_user_gets_403_or_404(api_client, team):
    other = User.objects.create_user(
        username="stats_other", email="stats_other@local.test", password="pass"
    )
    api_client.force_authenticate(user=other)
    resp = api_client.get(_url(team.pk))
    # Not visible in the user's queryset -> get_object 404, or 403 if visible.
    assert resp.status_code in (403, 404)


# =====================================================================
# Empty / guard cases
# =====================================================================


def test_empty_team_returns_zeros(owner_client, team):
    resp = owner_client.get(_url(team.pk))
    assert resp.status_code == 200
    body = resp.json()
    assert body["attendance"]["team_rate"] is None
    assert body["attendance"]["by_session"] == []
    assert body["attendance"]["by_member"] == []
    assert body["volume"]["total_distance"] == 0
    assert body["volume"]["by_week"] == []
    assert body["volume"]["by_member"] == []
    assert body["intensity"]["by_segment"] == []
    assert body["period"]["weeks"] == 12


def test_weeks_param_clamping_and_default(owner_client, team):
    assert owner_client.get(_url(team.pk)).json()["period"]["weeks"] == 12
    assert owner_client.get(_url(team.pk) + "?weeks=0").json()["period"]["weeks"] == 12
    assert owner_client.get(_url(team.pk) + "?weeks=4").json()["period"]["weeks"] == 4
    assert owner_client.get(_url(team.pk) + "?weeks=99999").json()["period"]["weeks"] == 520
    assert owner_client.get(_url(team.pk) + "?weeks=abc").json()["period"]["weeks"] == 12


# =====================================================================
# Aggregation
# =====================================================================


def test_full_aggregation(owner_client, team, program, present_status, absent_status):
    today = timezone.localdate()

    m_alice = _make_member(team, "Alice", "Aaa")
    m_bob = _make_member(team, "Bob", "Bbb")
    m_cara = _make_member(team, "Cara", "Ccc")  # never present

    # Two events in different ISO weeks.
    e1 = Event.objects.create(
        refer_program=program, name="E1", date=today - timedelta(days=10), total=1000
    )
    e2 = Event.objects.create(
        refer_program=program, name="E2", date=today - timedelta(days=3), total=2000
    )

    # E1: alice + bob present, cara absent.
    Attendance.objects.create(event=e1, member=m_alice, status=present_status)
    Attendance.objects.create(event=e1, member=m_bob, status=present_status)
    Attendance.objects.create(event=e1, member=m_cara, status=absent_status)
    # E2: only alice present.
    Attendance.objects.create(event=e2, member=m_alice, status=present_status)

    # Intensity: rounds/exercises across two zones on E1.
    seg_z2 = EnergySegmentFactory(abv="Z2", description="Endurance")
    seg_z4 = EnergySegmentFactory(abv="Z4", description="Threshold")
    r = RoundFactory(count=2, event=e1)
    ExerciseFactory(round=r, distance=100, repetition=3, energysegment=seg_z2)  # 100*3*2=600
    ExerciseFactory(round=r, distance=50, repetition=4, energysegment=seg_z4)  # 50*4*2=400

    body = owner_client.get(_url(team.pk)).json()

    # --- attendance ---
    att = body["attendance"]
    # 3 present slots filled / (3 active members * 2 sessions = 6 expected)
    assert att["team_rate"] == pytest.approx(3 / 6)

    by_session = {s["name"]: s for s in att["by_session"]}
    assert by_session["E1"]["present"] == 2
    assert by_session["E1"]["total"] == 3
    assert by_session["E1"]["rate"] == pytest.approx(2 / 3)
    assert by_session["E2"]["present"] == 1
    assert by_session["E2"]["total"] == 3
    # chronological order
    assert [s["name"] for s in att["by_session"]] == ["E1", "E2"]

    by_member = {m["name"]: m for m in att["by_member"]}
    assert by_member["Alice Aaa"]["present"] == 2
    assert by_member["Alice Aaa"]["total"] == 2
    assert by_member["Alice Aaa"]["rate"] == pytest.approx(1.0)
    assert by_member["Alice Aaa"]["last_present_date"] == str(e2.date)
    assert by_member["Bob Bbb"]["present"] == 1
    assert by_member["Bob Bbb"]["last_present_date"] == str(e1.date)
    assert by_member["Cara Ccc"]["present"] == 0
    assert by_member["Cara Ccc"]["rate"] == pytest.approx(0.0)
    assert by_member["Cara Ccc"]["last_present_date"] is None

    # --- volume ---
    vol = body["volume"]
    assert vol["total_distance"] == 3000
    assert len(vol["by_week"]) == 2
    assert sum(w["distance"] for w in vol["by_week"]) == 3000
    # weeks chronological
    weeks = [w["week_start"] for w in vol["by_week"]]
    assert weeks == sorted(weeks)

    vbm = {m["name"]: m["distance"] for m in vol["by_member"]}
    # alice present at both -> 1000 + 2000
    assert vbm["Alice Aaa"] == 3000
    # bob present at E1 only -> 1000
    assert vbm["Bob Bbb"] == 1000
    # cara never present -> not present in by_member
    assert "Cara Ccc" not in vbm

    # --- intensity ---
    seg = {s["abv"]: s for s in body["intensity"]["by_segment"]}
    assert seg["Z2"]["distance"] == 600
    assert seg["Z2"]["label"] == "Endurance"
    assert seg["Z4"]["distance"] == 400
    # ordered Z2 before Z4
    assert [s["abv"] for s in body["intensity"]["by_segment"]] == ["Z2", "Z4"]


def test_events_outside_window_excluded(owner_client, team, program, present_status):
    today = timezone.localdate()
    m = _make_member(team, "Win", "Dow")

    inside = Event.objects.create(
        refer_program=program, name="inside", date=today - timedelta(days=5), total=500
    )
    Event.objects.create(
        refer_program=program,
        name="old",
        date=today - timedelta(weeks=40),
        total=9999,
    )
    Attendance.objects.create(event=inside, member=m, status=present_status)

    body = owner_client.get(_url(team.pk) + "?weeks=4").json()
    assert body["volume"]["total_distance"] == 500
    assert [s["name"] for s in body["attendance"]["by_session"]] == ["inside"]


def test_other_team_events_excluded(owner_client, team, program, present_status):
    """Strict scoping: another team's events must not leak in."""
    today = timezone.localdate()
    other_program = ProgramFactory()  # different team
    Event.objects.create(
        refer_program=other_program,
        name="foreign",
        date=today - timedelta(days=2),
        total=12345,
    )
    body = owner_client.get(_url(team.pk)).json()
    assert body["volume"]["total_distance"] == 0
    assert body["attendance"]["by_session"] == []

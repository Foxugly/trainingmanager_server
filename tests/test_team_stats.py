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
    assert body["member"] is None
    # Default window: last 12 weeks (to=today, from=today-84d).
    today = timezone.localdate()
    assert body["period"]["to"] == str(today)
    assert body["period"]["from"] == str(today - timedelta(days=84))
    assert "weeks" not in body["period"]


# =====================================================================
# Date-range window (from / to)
# =====================================================================


def test_default_window_is_last_12_weeks(owner_client, team):
    today = timezone.localdate()
    body = owner_client.get(_url(team.pk)).json()
    assert body["period"]["to"] == str(today)
    assert body["period"]["from"] == str(today - timedelta(days=84))


def test_explicit_range_filters_events(owner_client, team, program, present_status):
    today = timezone.localdate()
    m = _make_member(team, "Win", "Dow")

    inside = Event.objects.create(
        refer_program=program, name="inside", date=today - timedelta(days=5), total=500
    )
    # Outside the explicit [today-7, today] window.
    Event.objects.create(
        refer_program=program, name="old", date=today - timedelta(days=30), total=9999
    )
    Attendance.objects.create(event=inside, member=m, status=present_status)

    date_from = (today - timedelta(days=7)).isoformat()
    date_to = today.isoformat()
    body = owner_client.get(
        _url(team.pk) + f"?from={date_from}&to={date_to}"
    ).json()
    assert body["period"]["from"] == date_from
    assert body["period"]["to"] == date_to
    assert body["volume"]["total_distance"] == 500
    assert [s["name"] for s in body["attendance"]["by_session"]] == ["inside"]


def test_only_from_provided_defaults_to_today(owner_client, team):
    today = timezone.localdate()
    date_from = (today - timedelta(days=20)).isoformat()
    body = owner_client.get(_url(team.pk) + f"?from={date_from}").json()
    assert body["period"]["from"] == date_from
    assert body["period"]["to"] == str(today)


def test_only_to_provided_defaults_from_84d_back(owner_client, team):
    date_to = (timezone.localdate() - timedelta(days=10)).isoformat()
    body = owner_client.get(_url(team.pk) + f"?to={date_to}").json()
    to_d = timezone.localdate() - timedelta(days=10)
    assert body["period"]["to"] == date_to
    assert body["period"]["from"] == str(to_d - timedelta(days=84))


def test_malformed_date_returns_400(owner_client, team):
    assert owner_client.get(_url(team.pk) + "?from=not-a-date").status_code == 400
    assert owner_client.get(_url(team.pk) + "?to=2024-13-99").status_code == 400


def test_from_after_to_returns_400(owner_client, team):
    today = timezone.localdate()
    date_from = today.isoformat()
    date_to = (today - timedelta(days=5)).isoformat()
    resp = owner_client.get(_url(team.pk) + f"?from={date_from}&to={date_to}")
    assert resp.status_code == 400


def test_span_clamped_to_two_years(owner_client, team):
    today = timezone.localdate()
    date_from = (today - timedelta(days=5000)).isoformat()
    body = owner_client.get(_url(team.pk) + f"?from={date_from}").json()
    # from is pulled forward to to-731d.
    assert body["period"]["from"] == str(today - timedelta(days=731))


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

    date_from = (today - timedelta(days=28)).isoformat()
    body = owner_client.get(_url(team.pk) + f"?from={date_from}").json()
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


# =====================================================================
# Per-athlete scoping (?member=<id>)
# =====================================================================


@pytest.fixture
def scoped_setup(team, program, present_status, absent_status):
    """A team with two athletes (one with a user account) and two events.

    Alice (has a user) is present at E1 only; Bob is present at both.
    Returns the relevant handles for the per-member tests.
    """
    today = timezone.localdate()

    # Alice has a linked user account so she can authenticate.
    alice_user = User.objects.create_user(
        username="alice_athlete", email="alice@local.test", password="pass"
    )
    m_alice = Member.objects.create(
        firstname="Alice", lastname="Aaa", email="alice@x.test", user=alice_user
    )
    TeamMembership.objects.create(team=team, member=m_alice)
    m_bob = _make_member(team, "Bob", "Bbb")

    e1 = Event.objects.create(
        refer_program=program, name="E1", date=today - timedelta(days=10), total=1000
    )
    e2 = Event.objects.create(
        refer_program=program, name="E2", date=today - timedelta(days=3), total=2000
    )

    # Alice present at E1 only; Bob present at both.
    Attendance.objects.create(event=e1, member=m_alice, status=present_status)
    Attendance.objects.create(event=e2, member=m_alice, status=absent_status)
    Attendance.objects.create(event=e1, member=m_bob, status=present_status)
    Attendance.objects.create(event=e2, member=m_bob, status=present_status)

    # Intensity on E1 (Alice present) + E2 (Alice absent).
    seg_z2 = EnergySegmentFactory(abv="Z2", description="Endurance")
    r1 = RoundFactory(count=2, event=e1)
    ExerciseFactory(round=r1, distance=100, repetition=3, energysegment=seg_z2)  # 600
    r2 = RoundFactory(count=1, event=e2)
    ExerciseFactory(round=r2, distance=200, repetition=2, energysegment=seg_z2)  # 400

    return {
        "alice_user": alice_user,
        "m_alice": m_alice,
        "m_bob": m_bob,
        "e1": e1,
        "e2": e2,
    }


def test_manager_can_query_any_member_scoped(owner_client, team, scoped_setup):
    m_alice = scoped_setup["m_alice"]
    body = owner_client.get(_url(team.pk) + f"?member={m_alice.pk}").json()

    # member object present + identifies the scope.
    assert body["member"] == {"id": m_alice.pk, "name": "Alice Aaa"}

    # attendance: Alice present at E1 only -> personal timeline (total 1/session).
    att = body["attendance"]
    by_session = {s["name"]: s for s in att["by_session"]}
    assert by_session["E1"]["present"] == 1
    assert by_session["E1"]["total"] == 1
    assert by_session["E2"]["present"] == 0
    assert by_session["E2"]["total"] == 1
    assert att["team_rate"] == pytest.approx(0.5)  # 1 of 2 sessions
    assert [m["member_id"] for m in att["by_member"]] == [m_alice.pk]

    # volume: only E1 (where Alice was present) -> 1000.
    vol = body["volume"]
    assert vol["total_distance"] == 1000
    assert sum(w["distance"] for w in vol["by_week"]) == 1000
    assert [m["member_id"] for m in vol["by_member"]] == [m_alice.pk]
    assert vol["by_member"][0]["distance"] == 1000

    # intensity: only E1's exercises (Alice present) -> 600, not E2's 400.
    seg = {s["abv"]: s for s in body["intensity"]["by_segment"]}
    assert seg["Z2"]["distance"] == 600


def test_athlete_can_query_own_member(api_client, team, scoped_setup):
    alice_user = scoped_setup["alice_user"]
    m_alice = scoped_setup["m_alice"]
    api_client.force_authenticate(user=alice_user)
    resp = api_client.get(_url(team.pk) + f"?member={m_alice.pk}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["member"]["id"] == m_alice.pk
    assert body["volume"]["total_distance"] == 1000


def test_athlete_cannot_query_other_member(api_client, team, scoped_setup):
    alice_user = scoped_setup["alice_user"]
    m_bob = scoped_setup["m_bob"]
    api_client.force_authenticate(user=alice_user)
    resp = api_client.get(_url(team.pk) + f"?member={m_bob.pk}")
    assert resp.status_code == 403


def test_athlete_cannot_query_team_aggregate(api_client, team, scoped_setup):
    alice_user = scoped_setup["alice_user"]
    api_client.force_authenticate(user=alice_user)
    resp = api_client.get(_url(team.pk))  # no member -> aggregate
    assert resp.status_code == 403


def test_member_not_in_team_returns_404(owner_client, team, scoped_setup):
    # A member that exists but is not a member of this team.
    foreign_member = Member.objects.create(
        firstname="No", lastname="Body", email="nobody@x.test"
    )
    resp = owner_client.get(_url(team.pk) + f"?member={foreign_member.pk}")
    assert resp.status_code == 404


def test_member_param_non_integer_returns_400(owner_client, team, scoped_setup):
    resp = owner_client.get(_url(team.pk) + "?member=abc")
    assert resp.status_code == 400


def test_member_scope_includes_roti_trend_and_streak(owner_client, team, program, present_status):
    """E6: per-athlete payload carries a date-ordered ROTI series + a current
    attendance streak; the team aggregate has an empty ROTI series."""
    from roti.models import Roti

    today = timezone.localdate()
    m = _make_member(team, "Ro", "Ti")
    e1 = Event.objects.create(refer_program=program, name="s1", date=today - timedelta(days=14), total=100)
    e2 = Event.objects.create(refer_program=program, name="s2", date=today - timedelta(days=7), total=100)
    e3 = Event.objects.create(refer_program=program, name="s3", date=today - timedelta(days=1), total=100)
    # Present only at the two most recent sessions -> streak of 2.
    Attendance.objects.create(event=e2, member=m, status=present_status)
    Attendance.objects.create(event=e3, member=m, status=present_status)
    Roti.objects.create(event=e1, member=m, score=3)
    Roti.objects.create(event=e3, member=m, score=5)

    body = owner_client.get(_url(team.pk) + f"?member={m.id}").json()
    roti = body["roti"]
    assert roti["count"] == 2
    assert [p["score"] for p in roti["series"]] == [3, 5]  # date-ordered
    assert roti["average"] == 4.0
    assert body["attendance"]["by_member"][0]["streak"] == 2

    # Team aggregate: ROTI is per-athlete, so the series is empty.
    agg = owner_client.get(_url(team.pk)).json()
    assert agg["roti"] == {"series": [], "average": None, "count": 0}


# =====================================================================
# Historical roster: departed athletes (B-P1 family)
# =====================================================================


def test_departed_athlete_does_not_inflate_rate_above_100(
    owner_client, team, program, present_status
):
    """A member present at a session and who has since LEFT must still be
    counted as expected for that session — the present rate can never exceed
    100% (regression test for the current-roster denominator bug)."""
    today = timezone.localdate()
    stayer = _make_member(team, "Stay", "Er")
    leaver = _make_member(team, "Leave", "Er")

    e1 = Event.objects.create(
        refer_program=program, name="E1", date=today - timedelta(days=10), total=100
    )
    # Both present at E1.
    Attendance.objects.create(event=e1, member=stayer, status=present_status)
    Attendance.objects.create(event=e1, member=leaver, status=present_status)

    # The leaver leaves AFTER E1 (yesterday) — they were on the roster for E1.
    leaver_membership = TeamMembership.objects.get(team=team, member=leaver)
    leaver_membership.left_at = timezone.now() - timedelta(days=1)
    leaver_membership.save(update_fields=["left_at"])

    att = owner_client.get(_url(team.pk)).json()["attendance"]
    by_session = {s["name"]: s for s in att["by_session"]}
    # E1 expected = 2 (both rostered then), present = 2 -> exactly 100%, not >100.
    assert by_session["E1"]["total"] == 2
    assert by_session["E1"]["present"] == 2
    assert by_session["E1"]["rate"] == pytest.approx(1.0)
    assert att["team_rate"] <= 1.0

    # The departed athlete still appears in the per-member breakdown.
    names = {m["name"] for m in att["by_member"]}
    assert "Leave Er" in names
    leaver_row = next(m for m in att["by_member"] if m["name"] == "Leave Er")
    assert leaver_row["present"] == 1
    assert leaver_row["rate"] == pytest.approx(1.0)


def test_member_who_left_before_session_is_not_expected(
    owner_client, team, program, present_status
):
    """A member who left BEFORE a session must not be counted in that
    session's expected denominator."""
    today = timezone.localdate()
    stayer = _make_member(team, "Stay", "Er")
    early = _make_member(team, "Early", "Gone")

    e_recent = Event.objects.create(
        refer_program=program, name="recent", date=today - timedelta(days=2), total=100
    )
    Attendance.objects.create(event=e_recent, member=stayer, status=present_status)

    # `early` left 5 days ago — before the recent session.
    m = TeamMembership.objects.get(team=team, member=early)
    m.left_at = timezone.now() - timedelta(days=5)
    m.save(update_fields=["left_at"])

    att = owner_client.get(_url(team.pk)).json()["attendance"]
    by_session = {s["name"]: s for s in att["by_session"]}
    # Only `stayer` was rostered for the recent session.
    assert by_session["recent"]["total"] == 1


def test_manager_can_scope_departed_member(owner_client, team, program, present_status):
    """End-of-season review: a manager can request ?member=<id> for an athlete
    who has since left (no longer 404)."""
    today = timezone.localdate()
    gone = _make_member(team, "Gone", "Athlete")
    e1 = Event.objects.create(
        refer_program=program, name="E1", date=today - timedelta(days=10), total=100
    )
    Attendance.objects.create(event=e1, member=gone, status=present_status)

    m = TeamMembership.objects.get(team=team, member=gone)
    m.left_at = timezone.now() - timedelta(days=1)
    m.save(update_fields=["left_at"])

    resp = owner_client.get(_url(team.pk) + f"?member={gone.pk}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["member"] == {"id": gone.pk, "name": "Gone Athlete"}
    assert body["attendance"]["by_member"][0]["present"] == 1

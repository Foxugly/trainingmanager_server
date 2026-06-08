"""Coverage for the aggregate GET /api/v1/dashboard/summary/ endpoint.

It replaces the old per-team/per-program/per-event HTTP fan-out, so the tests
assert the computed counts and lists match what the dashboard used to derive
client-side. "today" is pinned via the ?today= query param for determinism.
"""

from datetime import date, timedelta

import pytest

from attendance.models import Attendance, AttendanceStatus
from event.models import Event
from member.models import Member
from program.models import Program
from team.models import TeamMembership
from tests.factories import TeamFactory

pytestmark = pytest.mark.django_db

TODAY = date(2026, 6, 8)
URL = f"/api/v1/dashboard/summary/?today={TODAY.isoformat()}"


def _event(program, on, **kwargs):
    return Event.objects.create(name=kwargs.pop("name", "Ev"), refer_program=program, date=on, **kwargs)


def test_requires_auth(api_client):
    assert api_client.get(URL).status_code in (401, 403)


def test_coach_counts_upcoming_and_pending(auth_client, authenticated_user):
    team = TeamFactory(owner=authenticated_user)
    active = Program.objects.create(name="Active", team=team, is_active=True)
    inactive = Program.objects.create(name="Inactive", team=team, is_active=False)

    # Two active members -> members_count == 2.
    for i in range(2):
        m = Member.objects.create(firstname=f"A{i}", lastname="X")
        TeamMembership.objects.create(team=team, member=m)

    ev_soon = _event(active, TODAY + timedelta(days=1))  # in 7d + 14d windows
    _event(active, TODAY + timedelta(days=10))  # 14d only
    _event(inactive, TODAY + timedelta(days=1))  # inactive program -> ignored

    # A past event with members but no attendance -> pending.
    past = _event(active, TODAY - timedelta(days=2))
    past.members.add(Member.objects.create(firstname="P", lastname="Q"))

    res = auth_client.get(URL)
    assert res.status_code == 200, res.json()
    data = res.json()

    coach = {c["team_id"]: c for c in data["coach_teams"]}[team.id]
    assert coach["programs_active"] == 1
    assert coach["events_next_7d"] == 1
    assert coach["members_count"] == 2

    assert data["coach_upcoming_total"] == 2
    upcoming_ids = [item["event"]["id"] for item in data["coach_upcoming"]]
    assert upcoming_ids[0] == ev_soon.id  # ordered by date asc

    pending_ids = [item["event"]["id"] for item in data["coach_attendance_pending"]]
    assert pending_ids == [past.id]


def test_pending_excludes_events_with_attendance(auth_client, authenticated_user):
    team = TeamFactory(owner=authenticated_user)
    program = Program.objects.create(name="P", team=team, is_active=True)
    status, _ = AttendanceStatus.objects.get_or_create(code="present", defaults={"label": "Present"})

    past = _event(program, TODAY - timedelta(days=1))
    member = Member.objects.create(firstname="M", lastname="N")
    past.members.add(member)
    Attendance.objects.create(event=past, member=member, status=status)

    data = auth_client.get(URL).json()
    assert data["coach_attendance_pending"] == []


def test_member_history_and_my_member_id(auth_client, authenticated_user):
    # The caller is an athlete-member of a team owned by someone else.
    team = TeamFactory()
    program = Program.objects.create(name="P", team=team, is_active=True)
    me = Member.objects.create(firstname="Me", lastname="Athlete", user=authenticated_user)
    TeamMembership.objects.create(team=team, member=me)
    status, _ = AttendanceStatus.objects.get_or_create(code="present", defaults={"label": "Present"})
    status.color = "#10b981"
    status.save()

    past = _event(program, TODAY - timedelta(days=3))
    Attendance.objects.create(event=past, member=me, status=status)
    _event(program, TODAY + timedelta(days=2))  # upcoming for the athlete

    data = auth_client.get(URL).json()

    member_card = {c["team_id"]: c for c in data["member_teams"]}[team.id]
    assert member_card["my_member_id"] == me.id
    assert member_card["members_count"] == 1

    assert data["member_upcoming_total"] == 1
    history = data["member_attendance_history"]
    assert len(history) == 1
    assert history[0]["event"]["id"] == past.id
    assert history[0]["status_code"] == "present"
    assert history[0]["status"]["color"] == "#10b981"

    # A pure athlete manages nothing.
    assert data["coach_teams"] == []


def test_manager_membership_does_not_double_count_as_member(auth_client, authenticated_user):
    """A team where the caller is owner AND has a membership row is a coach team,
    never a member team (mirrors the client's single-role-per-team split)."""
    team = TeamFactory(owner=authenticated_user)
    me = Member.objects.create(firstname="Owner", lastname="Also", user=authenticated_user)
    TeamMembership.objects.create(team=team, member=me)

    data = auth_client.get(URL).json()
    assert team.id in {c["team_id"] for c in data["coach_teams"]}
    assert team.id not in {c["team_id"] for c in data["member_teams"]}

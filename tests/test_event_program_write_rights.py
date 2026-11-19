"""Write rights on events and programs: source team AND destination team.

Covers:
  - Athlete-member cannot DELETE an event (403) and the event still exists.
  - Coach (owner) of the team can still DELETE and PATCH its event.
  - Athlete of team A who manages team B cannot move an event of A into B
    (refer_program_id), nor a program of A into B (team_id).
  - A coach managing both A and B can move an event / a program from A to B.
"""

import pytest

from event.models import Event
from member.models import Member
from program.models import Program
from team.models import TeamMembership
from tests.factories import (
    EventFactory,
    ProgramFactory,
    SportFactory,
    TeamFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def sport():
    return SportFactory()


@pytest.fixture
def coach_a():
    return UserFactory(email="rights_coach_a@local.test")


@pytest.fixture
def team_a(coach_a, sport):
    return TeamFactory(owner=coach_a, is_active=True, sport=sport)


@pytest.fixture
def program_a(team_a):
    return ProgramFactory(team=team_a)


@pytest.fixture
def event_a(program_a):
    return EventFactory(refer_program=program_a, name="Original")


@pytest.fixture
def athlete():
    """Athlete-member of team A who owns (manages) a team B of their own."""
    return UserFactory(email="rights_athlete@local.test")


@pytest.fixture
def athlete_membership(athlete, team_a):
    member = Member.objects.create(firstname="A", lastname="Thlete", email=athlete.email, user=athlete)
    TeamMembership.objects.create(team=team_a, member=member)
    return member


@pytest.fixture
def team_b(athlete, sport):
    return TeamFactory(owner=athlete, is_active=True, sport=sport)


@pytest.fixture
def program_b(team_b):
    return ProgramFactory(team=team_b)


def _client(api_client, user):
    api_client.force_authenticate(user=user)
    return api_client


# --- events: delete -------------------------------------------------------


def test_athlete_member_cannot_delete_event(api_client, athlete, athlete_membership, event_a):
    resp = _client(api_client, athlete).delete(f"/api/v1/events/{event_a.id}/")
    assert resp.status_code == 403
    assert Event.objects.filter(pk=event_a.pk).exists()


def test_coach_can_delete_event(api_client, coach_a, event_a):
    resp = _client(api_client, coach_a).delete(f"/api/v1/events/{event_a.id}/")
    assert resp.status_code == 204
    assert not Event.objects.filter(pk=event_a.pk).exists()


# --- events: update / move -----------------------------------------------


def test_coach_can_update_event(api_client, coach_a, event_a):
    resp = _client(api_client, coach_a).patch(
        f"/api/v1/events/{event_a.id}/", {"name": "Renamed"}, format="json"
    )
    assert resp.status_code == 200
    event_a.refresh_from_db()
    assert event_a.name == "Renamed"


def test_athlete_cannot_move_event_into_own_team(
    api_client, athlete, athlete_membership, event_a, program_a, program_b
):
    resp = _client(api_client, athlete).patch(
        f"/api/v1/events/{event_a.id}/",
        {"refer_program_id": program_b.id, "name": "Moved"},
        format="json",
    )
    assert resp.status_code == 403
    event_a.refresh_from_db()
    assert event_a.refer_program_id == program_a.id
    assert event_a.name == "Original"


def test_manager_of_both_teams_can_move_event(api_client, coach_a, team_b, event_a, program_b):
    team_b.managers.add(coach_a)
    resp = _client(api_client, coach_a).patch(
        f"/api/v1/events/{event_a.id}/", {"refer_program_id": program_b.id}, format="json"
    )
    assert resp.status_code == 200
    event_a.refresh_from_db()
    assert event_a.refer_program_id == program_b.id


# --- programs: update / move ---------------------------------------------


def test_athlete_cannot_move_program_into_own_team(
    api_client, athlete, athlete_membership, team_a, program_a, team_b
):
    resp = _client(api_client, athlete).patch(
        f"/api/v1/programs/{program_a.id}/", {"team_id": team_b.id}, format="json"
    )
    assert resp.status_code == 403
    program_a.refresh_from_db()
    assert program_a.team_id == team_a.id


def test_coach_can_update_program(api_client, coach_a, program_a):
    resp = _client(api_client, coach_a).patch(
        f"/api/v1/programs/{program_a.id}/", {"name": "Renamed"}, format="json"
    )
    assert resp.status_code == 200
    assert Program.objects.get(pk=program_a.pk).name == "Renamed"


def test_manager_of_both_teams_can_move_program(api_client, coach_a, team_b, program_a):
    team_b.managers.add(coach_a)
    resp = _client(api_client, coach_a).patch(
        f"/api/v1/programs/{program_a.id}/", {"team_id": team_b.id}, format="json"
    )
    assert resp.status_code == 200
    program_a.refresh_from_db()
    assert program_a.team_id == team_b.id

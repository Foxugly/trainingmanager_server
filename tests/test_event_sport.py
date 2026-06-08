"""Multi-sport phase 3: Event.sport.

A session carries its own sport (one of the team's sports), defaulting to the
team's default sport. Drives AI modality scoping; the picker filter is frontend.
"""

import pytest

from event.models import Event
from team.models import TeamSport
from tests.factories import ProgramFactory, SportFactory

pytestmark = pytest.mark.django_db


def _create_event(client, program, **extra):
    payload = {"name": "Séance", "refer_program_id": program.pk, **extra}
    return client.post("/api/v1/events/", payload, format="json")


def test_event_defaults_to_team_default_sport(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    program = ProgramFactory(team=team)
    resp = _create_event(auth_client_trainer, program)
    assert resp.status_code == 201, resp.json()
    event = Event.objects.get(pk=resp.json()["id"])
    assert event.sport == team.default_sport
    # The sport is serialized on read.
    assert resp.json()["sport"]["id"] == team.default_sport.id


def test_event_accepts_a_secondary_team_sport(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    running = SportFactory(slug="running-evt")
    TeamSport.objects.create(team=team, sport=running, is_default=False)
    program = ProgramFactory(team=team)
    resp = _create_event(auth_client_trainer, program, sport_id=running.pk)
    assert resp.status_code == 201, resp.json()
    assert Event.objects.get(pk=resp.json()["id"]).sport == running


def test_event_rejects_a_sport_not_in_the_team(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    foreign = SportFactory(slug="foreign-evt")  # not one of the team's sports
    program = ProgramFactory(team=team)
    resp = _create_event(auth_client_trainer, program, sport_id=foreign.pk)
    assert resp.status_code == 400
    assert resp.json()["fields"]["sport_id"][0]["code"] == "sport_not_in_team"

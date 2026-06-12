"""Coverage of the session-template feature (F6):
  - POST /events/{id}/save-as-template/
  - GET/DELETE /event-templates/  + POST /event-templates/{id}/instantiate/
"""


import pytest
from django.contrib.auth import get_user_model

from event.models import Event, EventTemplate
from tests.factories import (
    EnergySegmentFactory,
    EventFactory,
    ExerciseFactory,
    ModalityFactory,
    ProgramFactory,
    RoundFactory,
    TeamFactory,
)

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="tpl_o@x.test", password="p")


@pytest.fixture
def team(owner):
    return TeamFactory(owner=owner, is_active=True)


@pytest.fixture
def program(team):
    return ProgramFactory(team=team)


@pytest.fixture
def event_with_round(team, program):
    event = EventFactory(refer_program=program, name="Source", total=1500)
    sport = team.sport
    ex = ExerciseFactory(modality=ModalityFactory(sport=sport), energysegment=EnergySegmentFactory())
    r = RoundFactory(sport=sport, exercises=[ex])
    event.rounds.add(r)
    return event


def test_save_as_template_clones_rounds(api_client, owner, team, event_with_round):
    api_client.force_authenticate(user=owner)
    resp = api_client.post(
        f"/api/v1/events/{event_with_round.pk}/save-as-template/",
        {"name": "Threshold day"},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    assert resp.json()["name"] == "Threshold day"
    assert resp.json()["rounds_count"] == 1
    tpl = EventTemplate.objects.get(pk=resp.json()["id"])
    # The template's round is a NEW row (not the event's), independent.
    assert tpl.rounds.count() == 1
    assert tpl.rounds.first().pk != event_with_round.rounds.first().pk


def test_save_as_template_requires_manager(api_client, team, event_with_round):
    athlete = User.objects.create_user(email="tpl_a@x.test", password="p")
    api_client.force_authenticate(user=athlete)
    resp = api_client.post(
        f"/api/v1/events/{event_with_round.pk}/save-as-template/", {"name": "x"}, format="json"
    )
    assert resp.status_code in (403, 404)


def test_list_scoped_to_managed_teams(api_client, owner, team):
    EventTemplate.objects.create(team=team, name="Mine")
    other_team = TeamFactory()
    EventTemplate.objects.create(team=other_team, name="Foreign")
    api_client.force_authenticate(user=owner)
    resp = api_client.get("/api/v1/event-templates/")
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()["results"]]
    assert "Mine" in names
    assert "Foreign" not in names


def test_instantiate_creates_event_with_cloned_rounds(
    api_client, owner, team, program, event_with_round
):
    api_client.force_authenticate(user=owner)
    tpl_id = api_client.post(
        f"/api/v1/events/{event_with_round.pk}/save-as-template/", {"name": "T"}, format="json"
    ).json()["id"]
    tpl = EventTemplate.objects.get(pk=tpl_id)
    tpl_round_pk = tpl.rounds.first().pk

    resp = api_client.post(
        f"/api/v1/event-templates/{tpl_id}/instantiate/",
        {"refer_program": program.pk, "date": "2026-07-01"},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["name"] == "T"
    assert body["date"] == "2026-07-01"
    new_event = Event.objects.get(pk=body["id"])
    assert new_event.rounds.count() == 1
    # The instantiated round is a fresh row, not the template's.
    assert new_event.rounds.first().pk != tpl_round_pk
    # Templates capture rounds -> the instantiated session is structured.
    assert new_event.training_type == "structured"


def test_instantiate_rejects_foreign_program(api_client, owner, team):
    tpl = EventTemplate.objects.create(team=team, name="T")
    foreign_program = ProgramFactory(team=TeamFactory())
    api_client.force_authenticate(user=owner)
    resp = api_client.post(
        f"/api/v1/event-templates/{tpl.pk}/instantiate/",
        {"refer_program": foreign_program.pk, "date": "2026-07-01"},
        format="json",
    )
    assert resp.status_code == 403


def test_delete_template(api_client, owner, team):
    tpl = EventTemplate.objects.create(team=team, name="T")
    api_client.force_authenticate(user=owner)
    assert api_client.delete(f"/api/v1/event-templates/{tpl.pk}/").status_code == 204
    assert not EventTemplate.objects.filter(pk=tpl.pk).exists()


def test_list_unauthenticated_401(api_client):
    assert api_client.get("/api/v1/event-templates/").status_code == 401

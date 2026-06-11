"""Contract for the page-open cascade collapse (audit, backend lot):

- Event exposes ``team_id`` so the detail screen can fetch the full team
  directly instead of chaining event -> program -> team (one fewer round-trip).
- ``GET /events/?refer_program__in=`` and ``GET /programs/?team__in=`` let the
  calendar/event-form fetch many programs'/teams' rows in ONE request instead
  of one request per program/team (kills the client-side fan-out).
"""

import pytest

from tests.factories import ProgramFactory, TeamFactory

pytestmark = pytest.mark.django_db


def _create_event(client, program, **extra):
    payload = {"name": "Séance", "refer_program_id": program.pk, **extra}
    return client.post("/api/v1/events/", payload, format="json")


def test_event_exposes_team_id(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    program = ProgramFactory(team=team)
    created = _create_event(auth_client_trainer, program)
    assert created.status_code == 201, created.json()
    event_id = created.json()["id"]

    detail = auth_client_trainer.get(f"/api/v1/events/{event_id}/")
    assert detail.status_code == 200
    # team_id resolves via refer_program and matches the program's team — no
    # need to fetch the program just to learn it.
    assert detail.json()["team_id"] == team.id


def test_events_filter_by_refer_program_in(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    prog_a = ProgramFactory(team=team)
    prog_b = ProgramFactory(team=team)
    prog_c = ProgramFactory(team=team)
    for p in (prog_a, prog_b, prog_c):
        assert _create_event(auth_client_trainer, p).status_code == 201

    resp = auth_client_trainer.get(
        f"/api/v1/events/?refer_program__in={prog_a.pk},{prog_b.pk}"
    )
    assert resp.status_code == 200
    returned_program_ids = {row["refer_program"]["id"] for row in resp.json()["results"]}
    assert returned_program_ids == {prog_a.pk, prog_b.pk}  # prog_c excluded


def test_programs_filter_by_team_in(auth_client_trainer, trainer_user):
    team_a = trainer_user.owned_teams.first()
    team_b = TeamFactory(owner=trainer_user)
    team_c = TeamFactory(owner=trainer_user)
    prog_a = ProgramFactory(team=team_a)
    prog_b = ProgramFactory(team=team_b)
    ProgramFactory(team=team_c)  # must be excluded

    resp = auth_client_trainer.get(
        f"/api/v1/programs/?team__in={team_a.pk},{team_b.pk}"
    )
    assert resp.status_code == 200
    rows = resp.json()["results"]
    returned = {row["id"] for row in rows}
    assert prog_a.pk in returned
    assert prog_b.pk in returned

    # `team` may serialize as a nested object or a bare pk depending on the
    # serializer — normalise before checking the filter held.
    def _team_id(row):
        t = row["team"]
        return t["id"] if isinstance(t, dict) else t

    assert all(_team_id(row) in {team_a.pk, team_b.pk} for row in rows)

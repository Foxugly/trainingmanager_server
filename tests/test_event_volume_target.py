"""Volume split (issues #8, #9) and program-bounded generation window (#12).

- #8: the coach's target volume lives in its own `total_target` field, PATCHable
  and independent from the realized `total` (which the rounds sync owns).
- #9: a structured session cannot be generated without a target volume.
- #12: the generation window must stay within the program's own dates.
"""

import datetime

import pytest

from event.models import Event
from tests.factories import EventFactory, ProgramFactory

pytestmark = pytest.mark.django_db


# --- #8: total_target is an independent, persistent field --------------------


def test_patch_event_total_target_persists(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    program = ProgramFactory(team=team)
    event = EventFactory(refer_program=program, total=1000, total_target=1000)

    resp = auth_client_trainer.patch(
        f"/api/v1/events/{event.pk}/",
        {"total_target": 4200},
        format="json",
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["total_target"] == 4200
    event.refresh_from_db()
    assert event.total_target == 4200
    # The realized total is untouched by a target edit — no more clobbering.
    assert event.total == 1000


def test_total_and_target_are_independent(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    program = ProgramFactory(team=team)
    event = EventFactory(refer_program=program, total=0, total_target=0)

    # The rounds sync writes `total`; the coach's objective stays put.
    auth_client_trainer.patch(
        f"/api/v1/events/{event.pk}/", {"total_target": 5000}, format="json"
    )
    auth_client_trainer.patch(
        f"/api/v1/events/{event.pk}/", {"total": 3600}, format="json"
    )
    event.refresh_from_db()
    assert event.total_target == 5000
    assert event.total == 3600


# --- #9: no structured generation without a target volume --------------------


def test_generate_training_blocked_without_volume(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    program = ProgramFactory(team=team)
    # Structured (default), no rounds, no target volume, unscheduled (future-ok).
    event = EventFactory(refer_program=program, date=None, total_target=0)

    resp = auth_client_trainer.post(f"/api/v1/events/{event.pk}/generate-training/")
    assert resp.status_code == 409, resp.json()
    assert resp.json()["code"] == "event_without_volume"


# --- #12: generation window bounded by the program dates ---------------------


def _generate_events(client, program, date_start, date_end):
    return client.post(
        f"/api/v1/programs/{program.pk}/generate-events/",
        {
            "date_start": date_start.isoformat(),
            "date_end": date_end.isoformat(),
            "frequency_per_week": 3,
            "description": "x",
            "overlap_strategy": "add_only",
        },
        format="json",
    )


def test_generate_events_rejects_start_before_program(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    program = ProgramFactory(
        team=team,
        date_start=datetime.date(2026, 9, 1),
        date_end=datetime.date(2026, 12, 1),
    )
    resp = _generate_events(
        auth_client_trainer, program, datetime.date(2026, 8, 25), datetime.date(2026, 10, 1)
    )
    assert resp.status_code == 400, resp.json()
    assert resp.json()["fields"]["date_start"][0]["code"] == "date_start_before_program"


def test_generate_events_rejects_end_after_program(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    program = ProgramFactory(
        team=team,
        date_start=datetime.date(2026, 9, 1),
        date_end=datetime.date(2026, 12, 1),
    )
    resp = _generate_events(
        auth_client_trainer, program, datetime.date(2026, 9, 15), datetime.date(2026, 12, 15)
    )
    assert resp.status_code == 400, resp.json()
    assert resp.json()["fields"]["date_end"][0]["code"] == "date_end_after_program"

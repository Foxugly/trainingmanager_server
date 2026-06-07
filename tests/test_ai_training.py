from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from exercise.models import EnergySegment, EnergySystem, Exercise, Modality
from round.models import Round
from tests.factories import (
    EventFactory,
    ProgramFactory,
    TeamFactory,
)

pytestmark = pytest.mark.django_db


def _mock_training_response(rounds, rationale="Plan rationale", equipment_used=None):
    response = MagicMock()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "create_training_session"
    tool_input = {"rounds": rounds, "rationale": rationale}
    if equipment_used is not None:
        tool_input["equipment_used"] = equipment_used
    tool_block.input = tool_input
    response.content = [tool_block]
    response.model = "claude-haiku-4-5-20251001"
    response.usage.input_tokens = 500
    response.usage.output_tokens = 800
    response.stop_reason = "tool_use"
    return response


@pytest.fixture
def trainer_event(trainer_user):
    """Event linked to a Program in trainer_user's team, with sport + catalog."""
    team = trainer_user.owned_teams.first()
    sport = team.sport
    Modality.objects.create(name="Freestyle", sport=sport)
    Modality.objects.create(name="Backstroke", sport=sport)
    es_system = EnergySystem.objects.create(name="Aerobic")
    EnergySegment.objects.create(abv="A1", energysystem=es_system)
    EnergySegment.objects.create(abv="A2", energysystem=es_system)
    program = ProgramFactory(team=team, date_start=date(2026, 6, 1), date_end=date(2026, 6, 30))
    return EventFactory(refer_program=program, total=2000)


def _build_rounds_payload(event=None):
    # Scope modality ids to the event's sport so the AI mock returns ids the
    # view's catalog filter accepts. Seed migrations populate Natation
    # modalities globally; tests use a different sport.
    if event is not None:
        sport = event.refer_program.team.sport
        mod_ids = list(Modality.objects.filter(sport=sport).values_list("id", flat=True))
    else:
        mod_ids = list(Modality.objects.values_list("id", flat=True))
    seg_ids = list(EnergySegment.objects.values_list("id", flat=True))
    return [
        {
            "count": 1,
            "t_start": "00:00",
            "t_break": "01:00",
            "exercises": [
                {
                    "modality_id": mod_ids[0],
                    "energysegment_id": seg_ids[0],
                    "distance": 200,
                    "repetition": 4,
                    "t_start": "00:00",
                    "t_break": "00:30",
                    "notes": "warm-up",
                },
            ],
        },
        {
            "count": 2,
            "t_start": "00:00",
            "t_break": "01:30",
            "exercises": [
                {
                    "modality_id": mod_ids[1],
                    "energysegment_id": seg_ids[1],
                    "distance": 100,
                    "repetition": 6,
                    "t_start": "00:00",
                    "t_break": "00:20",
                    "notes": "main set",
                },
            ],
        },
    ]


# ----------------------------- Happy path ----------------------------


def test_POST_generate_training_as_team_manager_returns_200(
    auth_client_trainer, trainer_event, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    rounds_payload = _build_rounds_payload(trainer_event)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_training_response(rounds_payload)
        response = auth_client_trainer.post(
            f"/api/v1/events/{trainer_event.pk}/generate-training/",
            {},
            format="json",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["rounds_created"] == 2
    assert body["exercises_created"] == 2
    assert body["exercises_reused"] == 0
    assert body["model"] == "claude-haiku-4-5-20251001"
    assert body["tokens_used"] == {"input": 500, "output": 800}

    trainer_event.refresh_from_db()
    assert trainer_event.generated_by_ai is True
    assert trainer_event.ai_response == "Plan rationale"
    assert trainer_event.rounds.count() == 2
    assert Round.objects.filter(event=trainer_event).count() == 2


# ----------------------------- 409 conflict --------------------------


def test_POST_generate_training_with_existing_rounds_returns_409(
    auth_client_trainer, trainer_event, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    existing = Round.objects.create(sport=trainer_event.refer_program.team.sport, count=1, order=1)
    trainer_event.rounds.add(existing)

    response = auth_client_trainer.post(
        f"/api/v1/events/{trainer_event.pk}/generate-training/",
        {},
        format="json",
    )
    assert response.status_code == 409


# ----------------------------- Future-only guard ---------------------


def test_POST_generate_training_past_dated_event_returns_409_event_in_past(
    auth_client_trainer, trainer_event, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    trainer_event.date = timezone.now().date() - timedelta(days=1)
    trainer_event.save(update_fields=["date"])

    response = auth_client_trainer.post(
        f"/api/v1/events/{trainer_event.pk}/generate-training/",
        {},
        format="json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "event_in_past"


def test_POST_generate_training_future_dated_event_passes_guard(
    auth_client_trainer, trainer_event, settings
):
    """A future-dated session passes the date guard and reaches generation."""
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    trainer_event.date = timezone.now().date() + timedelta(days=7)
    trainer_event.save(update_fields=["date"])
    rounds_payload = _build_rounds_payload(trainer_event)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_training_response(rounds_payload)
        response = auth_client_trainer.post(
            f"/api/v1/events/{trainer_event.pk}/generate-training/",
            {},
            format="json",
        )

    # The guard let it through to the AI path (200 generated, not 409 in-past).
    assert response.status_code == 200
    assert response.json()["rounds_created"] == 2


def test_POST_generate_training_undated_event_passes_guard(
    auth_client_trainer, trainer_event, settings
):
    """An unscheduled session (date is None) is always allowed."""
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    assert trainer_event.date is None
    rounds_payload = _build_rounds_payload(trainer_event)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_training_response(rounds_payload)
        response = auth_client_trainer.post(
            f"/api/v1/events/{trainer_event.pk}/generate-training/",
            {},
            format="json",
        )

    assert response.status_code == 200


# ----------------------------- Permissions ---------------------------


def test_POST_generate_training_as_non_member_returns_404(auth_client_trainer):
    """Events of teams the user is NOT a member of are invisible (404),
    not just write-protected (403). Strict team-scope: discoverability
    via /teams/ does not grant content access."""
    other_team = TeamFactory(is_active=True, is_public=True)
    Modality.objects.create(name="X", sport=other_team.sport)
    other_program = ProgramFactory(team=other_team)
    other_event = EventFactory(refer_program=other_program)

    response = auth_client_trainer.post(
        f"/api/v1/events/{other_event.pk}/generate-training/",
        {},
        format="json",
    )
    assert response.status_code == 404


def test_POST_generate_training_unauthenticated_returns_401(api_client, trainer_event):
    response = api_client.post(
        f"/api/v1/events/{trainer_event.pk}/generate-training/",
        {},
        format="json",
    )
    assert response.status_code == 401


# ----------------------------- AI errors -----------------------------


def test_POST_generate_training_no_api_key_returns_500(
    auth_client_trainer, trainer_event, settings
):
    settings.ANTHROPIC_API_KEY = ""
    response = auth_client_trainer.post(
        f"/api/v1/events/{trainer_event.pk}/generate-training/",
        {},
        format="json",
    )
    assert response.status_code == 500


def test_POST_generate_training_invalid_modality_id_returns_502(
    auth_client_trainer, trainer_event, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    seg_id = EnergySegment.objects.values_list("id", flat=True).first()
    bad_rounds = [
        {
            "count": 1,
            "exercises": [
                {
                    "modality_id": 999999,
                    "energysegment_id": seg_id,
                    "distance": 100,
                    "repetition": 1,
                }
            ],
        }
    ]
    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_training_response(bad_rounds)
        response = auth_client_trainer.post(
            f"/api/v1/events/{trainer_event.pk}/generate-training/",
            {},
            format="json",
        )
    assert response.status_code == 502


def test_POST_generate_training_empty_rounds_returns_502(
    auth_client_trainer, trainer_event, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_training_response([])
        response = auth_client_trainer.post(
            f"/api/v1/events/{trainer_event.pk}/generate-training/",
            {},
            format="json",
        )
    assert response.status_code == 502


# ----------------------------- get_or_create reuse -------------------


def test_POST_generate_training_get_or_create_reuses_exercise(
    auth_client_trainer, trainer_event, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    rounds_payload = _build_rounds_payload(trainer_event)

    # Pre-create one of the exercises identically -> get_or_create should reuse it
    first_ex = rounds_payload[0]["exercises"][0]
    Exercise.objects.create(
        modality_id=first_ex["modality_id"],
        energysegment_id=first_ex["energysegment_id"],
        distance=first_ex["distance"],
        repetition=first_ex["repetition"],
        t_start=first_ex["t_start"],
        t_break=first_ex["t_break"],
        notes=first_ex["notes"],
        language=trainer_event.refer_program.team.language,
    )
    exercises_before = Exercise.objects.count()

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_training_response(rounds_payload)
        response = auth_client_trainer.post(
            f"/api/v1/events/{trainer_event.pk}/generate-training/",
            {},
            format="json",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["exercises_created"] == 1
    assert body["exercises_reused"] == 1
    # Net new exercise count = 1
    assert Exercise.objects.count() == exercises_before + 1


# ----------------------------- Atomicity -----------------------------


def test_POST_generate_training_atomicity_on_failure(auth_client_trainer, trainer_event, settings):
    """If a database error happens mid-persistence, nothing leaks."""
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    rounds_payload = _build_rounds_payload(trainer_event)

    rounds_before = Round.objects.count()
    exercises_before = Exercise.objects.count()

    # Make the second Round.create call blow up
    real_create = Round.objects.create
    call_count = {"n": 0}

    def flaky_create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("boom")
        return real_create(**kwargs)

    with (
        patch("tools.ai.Anthropic") as MockAnthropic,
        patch("round.models.Round.objects.create", side_effect=flaky_create),
    ):
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_training_response(rounds_payload)
        with pytest.raises(RuntimeError):
            auth_client_trainer.post(
                f"/api/v1/events/{trainer_event.pk}/generate-training/",
                {},
                format="json",
            )

    trainer_event.refresh_from_db()
    assert trainer_event.generated_by_ai is False
    assert trainer_event.rounds.count() == 0
    assert Round.objects.count() == rounds_before
    assert Exercise.objects.count() == exercises_before


# ----------------------------- additional_prompt ---------------------


def test_POST_generate_training_additional_prompt_empty_returns_200(
    auth_client_trainer, trainer_event, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    rounds_payload = _build_rounds_payload(trainer_event)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_training_response(rounds_payload)
        response = auth_client_trainer.post(
            f"/api/v1/events/{trainer_event.pk}/generate-training/",
            {"additional_prompt": ""},
            format="json",
        )

    assert response.status_code == 200
    sent_prompt = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "---" not in sent_prompt or "Additional instructions" not in sent_prompt


def test_POST_generate_training_additional_prompt_appended_to_llm_prompt(
    auth_client_trainer, trainer_event, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    rounds_payload = _build_rounds_payload(trainer_event)
    coach_text = "focus sur l'endurance, sans matériel, athlètes seniors"

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_training_response(rounds_payload)
        response = auth_client_trainer.post(
            f"/api/v1/events/{trainer_event.pk}/generate-training/",
            {"additional_prompt": coach_text},
            format="json",
        )

    assert response.status_code == 200
    sent_prompt = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert coach_text in sent_prompt
    assert "Additional instructions provided by the coach" in sent_prompt
    # Coach text appears AFTER the structured context, never before — guards
    # against prompt-injection that could override the system instructions.
    assert sent_prompt.index("Authorized modalities catalog") < sent_prompt.index(coach_text)


def test_POST_generate_training_additional_prompt_too_long_returns_400(
    auth_client_trainer, trainer_event, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"

    response = auth_client_trainer.post(
        f"/api/v1/events/{trainer_event.pk}/generate-training/",
        {"additional_prompt": "x" * 2001},
        format="json",
    )

    assert response.status_code == 400
    body = response.json()
    field_errors = body.get("fields", {}).get("additional_prompt", [])
    assert any(err.get("code") == "additional_prompt_too_long" for err in field_errors), body


# ----------------------------- Session context ------------------------


def test_generate_training_prompt_includes_equipment_duration_and_venue(
    auth_client_trainer, trainer_event, settings
):
    """Equipment (HTML-stripped), the duration derived from hour_start/hour_end,
    and the venue (managed Place name + address) are fed to the AI."""
    from datetime import time

    from place.models import Place

    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    place = Place.objects.create(
        team=trainer_event.refer_program.team,
        name="Piscine 50m",
        address="Av. des Sports 1",
    )
    trainer_event.equipment = "<p>Pull-buoy, plaquettes</p>"
    trainer_event.hour_start = time(18, 0)
    trainer_event.hour_end = time(19, 30)
    trainer_event.place = place
    trainer_event.location = place.name
    trainer_event.save(
        update_fields=["equipment", "hour_start", "hour_end", "place", "location"]
    )
    rounds_payload = _build_rounds_payload(trainer_event)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_training_response(rounds_payload)
        response = auth_client_trainer.post(
            f"/api/v1/events/{trainer_event.pk}/generate-training/",
            {},
            format="json",
        )

    assert response.status_code == 200
    sent_prompt = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Available equipment: Pull-buoy, plaquettes" in sent_prompt
    assert "<p>" not in sent_prompt  # HTML stripped
    assert "Session duration: about 90 minutes" in sent_prompt
    assert "Venue: Piscine 50m (Av. des Sports 1)" in sent_prompt


def test_generate_training_prompt_omits_missing_session_context(
    auth_client_trainer, trainer_event, settings
):
    """With no equipment, no times and no venue, those lines are absent (no
    empty 'duration'/'equipment'/'venue' placeholders leak into the prompt)."""
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    # trainer_event has no equipment, hour_start/end or place by default.
    rounds_payload = _build_rounds_payload(trainer_event)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_training_response(rounds_payload)
        response = auth_client_trainer.post(
            f"/api/v1/events/{trainer_event.pk}/generate-training/",
            {},
            format="json",
        )

    assert response.status_code == 200
    sent_prompt = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Session duration" not in sent_prompt
    assert "Available equipment" not in sent_prompt
    assert "Venue:" not in sent_prompt


# ----------------------------- Equipment catalog ----------------------


def test_generate_training_feeds_equipment_catalog_and_links_used(
    auth_client_trainer, trainer_event, settings
):
    """The team's enabled equipment is listed in the prompt; the AI-reported
    equipment_used (restricted to that set) is attached to the event and the
    free-text equipment string is synced to the joined names."""
    from equipment.models import Equipment

    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    team = trainer_event.refer_program.team
    pb = Equipment.objects.create(name="Pull-buoy")
    plaq = Equipment.objects.create(name="Plaquettes")
    team.equipment.add(pb, plaq)
    rounds_payload = _build_rounds_payload(trainer_event)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_training_response(
            rounds_payload, equipment_used=["Pull-buoy"]
        )
        response = auth_client_trainer.post(
            f"/api/v1/events/{trainer_event.pk}/generate-training/",
            {},
            format="json",
        )

    assert response.status_code == 200
    sent_prompt = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Team equipment catalog" in sent_prompt
    assert "Pull-buoy" in sent_prompt and "Plaquettes" in sent_prompt

    trainer_event.refresh_from_db()
    assert set(trainer_event.equipment_items.values_list("id", flat=True)) == {pb.pk}
    assert trainer_event.equipment == "Pull-buoy"


def test_generate_training_ignores_equipment_not_enabled(
    auth_client_trainer, trainer_event, settings
):
    """An equipment name the AI returns that is not in the team's enabled set is
    dropped (never attached)."""
    from equipment.models import Equipment

    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    team = trainer_event.refer_program.team
    pb = Equipment.objects.create(name="Pull-buoy")
    team.equipment.add(pb)
    rounds_payload = _build_rounds_payload(trainer_event)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_training_response(
            rounds_payload, equipment_used=["Plaquettes"]  # not enabled
        )
        response = auth_client_trainer.post(
            f"/api/v1/events/{trainer_event.pk}/generate-training/",
            {},
            format="json",
        )

    assert response.status_code == 200
    trainer_event.refresh_from_db()
    assert trainer_event.equipment_items.count() == 0


def test_generate_training_no_catalog_omits_equipment_block(
    auth_client_trainer, trainer_event, settings
):
    """With no enabled team equipment, the catalog block and equipment_used
    field are absent from the prompt/schema."""
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    rounds_payload = _build_rounds_payload(trainer_event)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_training_response(rounds_payload)
        response = auth_client_trainer.post(
            f"/api/v1/events/{trainer_event.pk}/generate-training/",
            {},
            format="json",
        )

    assert response.status_code == 200
    sent_prompt = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Team equipment catalog" not in sent_prompt
    tool_arg = mock_client.messages.create.call_args.kwargs["tools"][0]
    assert "equipment_used" not in tool_arg["input_schema"]["properties"]

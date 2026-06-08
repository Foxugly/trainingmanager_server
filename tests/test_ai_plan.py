from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from event.models import Event
from tests.factories import ProgramFactory, TeamFactory

pytestmark = pytest.mark.django_db


def _mock_tool_use_response(events, rationale="Test rationale"):
    response = MagicMock()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "create_training_plan"
    tool_block.input = {"events": events, "rationale": rationale}
    response.content = [tool_block]
    response.model = "claude-haiku-4-5-20251001"
    response.usage.input_tokens = 100
    response.usage.output_tokens = 200
    response.usage.cache_creation_input_tokens = 0
    response.usage.cache_read_input_tokens = 0
    response.stop_reason = "tool_use"
    return response


def _make_events_payload(start, count, color="#3498db", spacing_days=2):
    return [
        {
            "name": f"Seance {i + 1}",
            "goal": "Endurance",
            "date": (start + timedelta(days=i * spacing_days)).isoformat(),
            "total_distance": 3000,
            "color": color,
        }
        for i in range(count)
    ]


def _trainer_program(trainer_user):
    team = trainer_user.owned_teams.first()
    return ProgramFactory(team=team)


def _generate_payload(date_start, date_end, **overrides):
    base = {
        "date_start": date_start.isoformat(),
        "date_end": date_end.isoformat(),
        "frequency_per_week": 2,
        "description": "Endurance progressive",
        "overlap_strategy": "add_only",
    }
    base.update(overrides)
    return base


# ----------------------------- Happy path ----------------------------


def test_POST_generate_events_as_team_manager_returns_200(
    auth_client_trainer, trainer_user, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    start = date(2026, 5, 1)
    end = date(2026, 5, 14)
    events_payload = _make_events_payload(start, 4)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_tool_use_response(
            events=events_payload, rationale="Plan progressif"
        )
        response = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            _generate_payload(start, end),
            format="json",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 4
    assert body["deleted_count"] == 0
    assert body["rationale"] == "Plan progressif"
    assert body["model"] == "claude-haiku-4-5-20251001"
    assert body["tokens_used"] == {"input": 100, "output": 200}
    assert Event.objects.filter(refer_program=program).count() == 4


def test_POST_generate_events_updates_program_ai_fields(
    auth_client_trainer, trainer_user, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    start = date(2026, 6, 1)
    end = date(2026, 6, 14)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_tool_use_response(
            events=_make_events_payload(start, 2), rationale="Why this plan"
        )
        auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            _generate_payload(start, end, frequency_per_week=3, description="Compete"),
            format="json",
        )

    program.refresh_from_db()
    assert program.generated_by_ai is True
    assert program.frequency_per_week == 3
    assert program.description == "Compete"
    assert "Generate a training plan" in program.ai_prompt
    assert program.ai_response == "Why this plan"
    assert program.ai_generated_at is not None


def test_POST_generate_events_sets_time_and_location(
    auth_client_trainer, trainer_user, settings
):
    """The AI plan fills hour_start / hour_end / location on each session."""
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    start = date(2026, 5, 1)
    end = date(2026, 5, 14)
    events_payload = [
        {
            "name": "Seance 1",
            "goal": "Endurance",
            "date": start.isoformat(),
            "hour_start": "18:00",
            "hour_end": "19:30",
            "location": "Piscine communale",
            "total_distance": 3000,
            "color": "#3498db",
        }
    ]

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_tool_use_response(events=events_payload)
        resp = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            _generate_payload(start, end),
            format="json",
        )

    assert resp.status_code == 200
    ev = Event.objects.filter(refer_program=program, date=start).first()
    assert ev is not None
    assert ev.hour_start.strftime("%H:%M") == "18:00"
    assert ev.hour_end.strftime("%H:%M") == "19:30"
    assert ev.location == "Piscine communale"


# ----------------------------- Permissions ---------------------------


def test_POST_generate_events_as_non_member_returns_404(auth_client_trainer):
    """Programs of teams the user is NOT a member of are invisible (404),
    not just write-protected (403). Strict team-scope: discoverability
    via /teams/ does not grant content access."""
    other_team = TeamFactory(is_public=True, is_active=True)
    other_program = ProgramFactory(team=other_team)
    response = auth_client_trainer.post(
        f"/api/v1/programs/{other_program.pk}/generate-events/",
        _generate_payload(date(2026, 5, 1), date(2026, 5, 14)),
        format="json",
    )
    assert response.status_code == 404


def test_POST_generate_events_unauthenticated_returns_401(api_client, trainer_user):
    program = _trainer_program(trainer_user)
    response = api_client.post(
        f"/api/v1/programs/{program.pk}/generate-events/",
        _generate_payload(date(2026, 5, 1), date(2026, 5, 14)),
        format="json",
    )
    assert response.status_code == 401


# ----------------------------- Validation ----------------------------


def test_POST_generate_events_invalid_dates_returns_400(
    auth_client_trainer, trainer_user, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    response = auth_client_trainer.post(
        f"/api/v1/programs/{program.pk}/generate-events/",
        _generate_payload(date(2026, 5, 14), date(2026, 5, 1)),
        format="json",
    )
    assert response.status_code == 400


def test_POST_generate_events_range_too_long_returns_400(
    auth_client_trainer, trainer_user, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    response = auth_client_trainer.post(
        f"/api/v1/programs/{program.pk}/generate-events/",
        _generate_payload(date(2026, 1, 1), date(2027, 5, 1)),
        format="json",
    )
    assert response.status_code == 400


# ----------------------------- AI errors -----------------------------


def test_POST_generate_events_no_api_key_returns_500(auth_client_trainer, trainer_user, settings):
    settings.ANTHROPIC_API_KEY = ""
    program = _trainer_program(trainer_user)
    response = auth_client_trainer.post(
        f"/api/v1/programs/{program.pk}/generate-events/",
        _generate_payload(date(2026, 5, 1), date(2026, 5, 14)),
        format="json",
    )
    assert response.status_code == 500


def test_POST_generate_events_ai_returns_invalid_date_returns_502(
    auth_client_trainer, trainer_user, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    bad_event = {
        "name": "Out of range",
        "goal": "x",
        "date": "2030-01-01",
        "total_distance": 1000,
        "color": "#3498db",
    }
    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_tool_use_response(events=[bad_event])
        response = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            _generate_payload(date(2026, 5, 1), date(2026, 5, 14)),
            format="json",
        )
    assert response.status_code == 502


def test_POST_generate_events_ai_returns_empty_returns_502(
    auth_client_trainer, trainer_user, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_tool_use_response(events=[])
        response = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            _generate_payload(date(2026, 5, 1), date(2026, 5, 14)),
            format="json",
        )
    assert response.status_code == 502


def test_POST_generate_events_ai_does_not_call_tool_returns_502(
    auth_client_trainer, trainer_user, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    response_no_tool = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "I'm sorry, I can't help with that."
    response_no_tool.content = [text_block]
    response_no_tool.model = "claude-haiku-4-5-20251001"
    response_no_tool.usage.input_tokens = 50
    response_no_tool.usage.output_tokens = 20
    response_no_tool.usage.cache_creation_input_tokens = 0
    response_no_tool.usage.cache_read_input_tokens = 0
    response_no_tool.stop_reason = "end_turn"

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = response_no_tool
        response = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            _generate_payload(date(2026, 5, 1), date(2026, 5, 14)),
            format="json",
        )
    assert response.status_code == 502


# ----------------------------- Overlap strategies --------------------


def test_POST_generate_events_add_only_skips_duplicates(
    auth_client_trainer, trainer_user, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    start = date(2026, 7, 1)
    end = date(2026, 7, 14)
    Event.objects.create(
        refer_program=program,
        name="Existing",
        date=start,
        color="#000000",
    )
    events_payload = _make_events_payload(start, 3)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_tool_use_response(events=events_payload)
        response = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            _generate_payload(start, end, overlap_strategy="add_only"),
            format="json",
        )

    assert response.status_code == 200
    assert response.json()["created_count"] == 2
    assert response.json()["deleted_count"] == 0
    assert Event.objects.filter(refer_program=program, date=start, name="Existing").exists()


def test_POST_generate_events_replace_deletes_existing_in_range(
    auth_client_trainer, trainer_user, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    start = date(2026, 8, 1)
    end = date(2026, 8, 14)
    for d in [start, start + timedelta(days=3), start + timedelta(days=6)]:
        Event.objects.create(refer_program=program, name="Old", date=d, color="#000000")

    events_payload = _make_events_payload(start, 4)
    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_tool_use_response(events=events_payload)
        response = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            _generate_payload(start, end, overlap_strategy="replace"),
            format="json",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["deleted_count"] == 3
    assert body["created_count"] == 4
    assert not Event.objects.filter(refer_program=program, name="Old").exists()


def test_POST_generate_events_merge_adds_alongside_existing(
    auth_client_trainer, trainer_user, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    start = date(2026, 9, 1)
    end = date(2026, 9, 14)
    Event.objects.create(refer_program=program, name="Existing", date=start, color="#000000")

    events_payload = _make_events_payload(start, 3)
    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_tool_use_response(events=events_payload)
        response = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            _generate_payload(start, end, overlap_strategy="merge"),
            format="json",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 3
    assert body["deleted_count"] == 0
    # Two events on the start date now (existing + AI duplicate)
    assert Event.objects.filter(refer_program=program, date=start).count() == 2


# ----------------------------- additional_prompt ---------------------


def test_POST_generate_events_additional_prompt_appended_to_llm_prompt(
    auth_client_trainer, trainer_user, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    start = date(2026, 5, 1)
    end = date(2026, 5, 14)
    events_payload = _make_events_payload(start, 2)
    coach_text = "pic en mars, semaine de récup en novembre"

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_tool_use_response(events_payload)
        response = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            _generate_payload(start, end, additional_prompt=coach_text),
            format="json",
        )

    assert response.status_code == 200
    sent_prompt = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert coach_text in sent_prompt
    assert "Additional instructions provided by the coach" in sent_prompt
    # Coach text appears AFTER the structured context — guards against
    # prompt-injection that could override the system instructions.
    assert sent_prompt.index("IMPORTANT instructions") < sent_prompt.index(coach_text)


# ----------------------------- Training template ---------------------


def test_POST_generate_events_derives_frequency_from_template_slots(
    auth_client_trainer, trainer_user, settings
):
    """A team WITH 3 weekly slots -> frequency derived = 3 (no frequency
    needed in the request); the prompt lists the FIXED slots + default pool;
    the mocked AI events are created and program.frequency_per_week == 3."""
    from team.models import TrainingSlot

    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    team = program.team
    team.default_pool = "Piscine olympique"
    team.save(update_fields=["default_pool"])
    TrainingSlot.objects.create(team=team, weekday=0, hour_start="18:00", hour_end="19:30")
    TrainingSlot.objects.create(team=team, weekday=2, hour_start="18:00", hour_end="19:30")
    TrainingSlot.objects.create(team=team, weekday=4, hour_start="18:00", hour_end="19:30")

    start = date(2026, 5, 1)
    end = date(2026, 5, 14)
    events_payload = _make_events_payload(start, 6)

    # Request WITHOUT frequency_per_week — it must be derived from the slots.
    payload = {
        "date_start": start.isoformat(),
        "date_end": end.isoformat(),
        "description": "Endurance",
        "overlap_strategy": "add_only",
    }

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_tool_use_response(events_payload)
        response = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            payload,
            format="json",
        )

    assert response.status_code == 200, response.content
    assert response.json()["created_count"] == 6
    assert Event.objects.filter(refer_program=program).count() == 6

    program.refresh_from_db()
    assert program.frequency_per_week == 3  # derived from the 3 slots

    sent_prompt = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "FIXED weekly slots" in sent_prompt
    assert "Monday 18:00" in sent_prompt
    assert "Wednesday 18:00" in sent_prompt
    assert "Friday 18:00" in sent_prompt
    assert "Piscine olympique" in sent_prompt


def test_POST_generate_events_no_template_requires_frequency(
    auth_client_trainer, trainer_user, settings
):
    """A team with NO template -> frequency_per_week is required; omitting it
    returns 400 with code frequency_required."""
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    start = date(2026, 5, 1)
    end = date(2026, 5, 14)

    payload = {
        "date_start": start.isoformat(),
        "date_end": end.isoformat(),
        "description": "Endurance",
        "overlap_strategy": "add_only",
    }
    response = auth_client_trainer.post(
        f"/api/v1/programs/{program.pk}/generate-events/",
        payload,
        format="json",
    )
    assert response.status_code == 400
    body = response.json()
    # Code surfaces either at top-level or under fields, depending on handler.
    assert "frequency_required" in str(body), body


def test_POST_generate_events_additional_prompt_too_long_returns_400(
    auth_client_trainer, trainer_user, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    start = date(2026, 5, 1)
    end = date(2026, 5, 14)

    response = auth_client_trainer.post(
        f"/api/v1/programs/{program.pk}/generate-events/",
        _generate_payload(start, end, additional_prompt="x" * 2001),
        format="json",
    )

    assert response.status_code == 400
    body = response.json()
    field_errors = body.get("fields", {}).get("additional_prompt", [])
    assert any(err.get("code") == "additional_prompt_too_long" for err in field_errors), body


# ----------------------------- Places (Lieux) ------------------------


def _one_event_at(start, location):
    return [
        {
            "name": "Seance 1",
            "goal": "Endurance",
            "date": start.isoformat(),
            "hour_start": "18:00",
            "hour_end": "19:30",
            "location": location,
            "total_distance": 3000,
            "color": "#3498db",
        }
    ]


def test_generate_events_prompt_lists_team_places(
    auth_client_trainer, trainer_user, settings
):
    """The team's managed Places (name + address) are listed in the prompt as
    known venues the AI must map the coach's venue mentions to."""
    from place.models import Place

    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    p1 = Place.objects.create(
        sport=program.team.sport, name="Piscine olympique", address="12 rue des Bains"
    )
    p2 = Place.objects.create(sport=program.team.sport, name="Bassin nordique")
    program.team.places.add(p1, p2)
    start = date(2026, 5, 1)
    end = date(2026, 5, 14)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_tool_use_response(
            _make_events_payload(start, 2)
        )
        auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            _generate_payload(start, end),
            format="json",
        )

    sent_prompt = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Known venues for this team" in sent_prompt
    assert "Piscine olympique (12 rue des Bains)" in sent_prompt
    assert "Bassin nordique" in sent_prompt


def test_generate_events_links_existing_place(
    auth_client_trainer, trainer_user, settings
):
    """An AI location matching a managed Place links that Place (case-insensitive)
    and creates no duplicate."""
    from place.models import Place

    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    place = Place.objects.create(sport=program.team.sport, name="Piscine Nord")
    program.team.places.add(place)
    start = date(2026, 5, 1)
    end = date(2026, 5, 14)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_tool_use_response(
            _one_event_at(start, "piscine nord")  # different case on purpose
        )
        resp = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            _generate_payload(start, end),
            format="json",
        )

    assert resp.status_code == 200
    assert program.team.places.count() == 1  # no duplicate
    ev = Event.objects.get(refer_program=program, date=start)
    assert ev.place_id == place.id
    assert ev.location == "Piscine Nord"  # synced to the canonical Place name


def test_generate_events_creates_unknown_place(
    auth_client_trainer, trainer_user, settings
):
    """An AI location with no matching Place creates a new Place and links it."""
    from place.models import Place

    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    start = date(2026, 5, 1)
    end = date(2026, 5, 14)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_tool_use_response(
            _one_event_at(start, "Stade nautique")
        )
        resp = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            _generate_payload(start, end),
            format="json",
        )

    assert resp.status_code == 200
    created = program.team.places.get(name="Stade nautique")
    assert created.sport_id == program.team.sport_id
    ev = Event.objects.get(refer_program=program, date=start)
    assert ev.place_id == created.id
    assert ev.location == "Stade nautique"


def test_generate_events_reuses_created_place_across_sessions(
    auth_client_trainer, trainer_user, settings
):
    """A new venue named on several sessions of one plan is created only once."""
    from place.models import Place

    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    start = date(2026, 5, 1)
    end = date(2026, 5, 14)
    events_payload = [
        _one_event_at(start, "Lac municipal")[0],
        _one_event_at(start + timedelta(days=2), "Lac municipal")[0],
    ]

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_tool_use_response(events_payload)
        resp = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            _generate_payload(start, end),
            format="json",
        )

    assert resp.status_code == 200
    assert program.team.places.filter(name="Lac municipal").count() == 1
    place = program.team.places.get(name="Lac municipal")
    linked = Event.objects.filter(refer_program=program, place=place).count()
    assert linked == 2


def test_generate_events_empty_location_leaves_place_null(
    auth_client_trainer, trainer_user, settings
):
    """An empty AI location links no Place and creates none."""
    from place.models import Place

    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    program = _trainer_program(trainer_user)
    start = date(2026, 5, 1)
    end = date(2026, 5, 14)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_tool_use_response(
            _one_event_at(start, "")
        )
        resp = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            _generate_payload(start, end),
            format="json",
        )

    assert resp.status_code == 200
    assert program.team.places.count() == 0
    ev = Event.objects.get(refer_program=program, date=start)
    assert ev.place_id is None
    assert ev.location == ""

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

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from program.models import Program
from team.models import Team

pytestmark = pytest.mark.django_db


# ----------------------------- Defaults & DB --------------------------


def test_team_default_language_is_fr(authenticated_user):
    team = Team.objects.create(name="Test team default", owner=authenticated_user)
    assert team.language == "fr"


def test_team_can_change_language(authenticated_user):
    team = Team.objects.create(name="Mutable team", owner=authenticated_user)
    team.language = "en"
    team.save()
    team.refresh_from_db()
    assert team.language == "en"


def test_user_default_language_is_fr(authenticated_user):
    assert authenticated_user.language == "fr"


# ----------------------------- API surface ----------------------------


def test_PATCH_team_language_as_owner_returns_200(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    response = auth_client_trainer.patch(
        f"/api/v1/teams/{team.pk}/",
        {"language": "nl"},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["language"] == "nl"


def test_invalid_language_code_returns_400(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    response = auth_client_trainer.patch(
        f"/api/v1/teams/{team.pk}/",
        {"language": "xx"},
        format="json",
    )
    assert response.status_code == 400


def test_PATCH_me_changes_user_language(auth_client_trainer):
    response = auth_client_trainer.patch(
        "/api/v1/me/",
        {"language": "en"},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["language"] == "en"


# ----------------------------- Helper ---------------------------------


def test_resolve_language_label():
    from tools.i18n import resolve_language_label

    assert resolve_language_label("fr") == "Français"
    assert resolve_language_label("en") == "English"
    assert resolve_language_label("nl") == "Nederlands"
    assert resolve_language_label("it") == "Italiano"
    assert resolve_language_label("es") == "Español"
    assert resolve_language_label("xx") == "Français"  # fallback


# --------------------- Prompt language injection ----------------------


def _mock_plan_response():
    response = MagicMock()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "create_training_plan"
    tool_block.input = {
        "events": [
            {
                "name": "Test",
                "goal": "Test",
                "date": "2026-06-02",
                "total_distance": 1000,
                "color": "#3498db",
            }
        ],
        "rationale": "Test",
    }
    response.content = [tool_block]
    response.model = "claude-haiku-4-5-20251001"
    response.usage.input_tokens = 100
    response.usage.output_tokens = 50
    response.stop_reason = "tool_use"
    return response


def test_generate_plan_prompt_includes_team_language(auth_client_trainer, trainer_user, settings):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake"
    team = trainer_user.owned_teams.first()
    team.language = "nl"
    team.save()
    program = Program.objects.create(name="NL test plan", team=team)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = _mock_plan_response()
        response = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            {
                "date_start": date(2026, 6, 1).isoformat(),
                "date_end": date(2026, 6, 7).isoformat(),
                "frequency_per_week": 1,
                "description": "Test",
                "overlap_strategy": "add_only",
            },
            format="json",
        )
        assert response.status_code == 200
        call_args = MockAnthropic.return_value.messages.create.call_args
        user_message = call_args.kwargs["messages"][0]["content"]
        assert "Nederlands" in user_message
        assert "Français" not in user_message


def test_generate_plan_prompt_falls_back_to_french_if_unset(
    auth_client_trainer, trainer_user, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake"
    team = trainer_user.owned_teams.first()
    # Default already 'fr' from migration; ensure prompt mentions it.
    program = Program.objects.create(name="FR test plan", team=team)

    with patch("tools.ai.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = _mock_plan_response()
        response = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            {
                "date_start": date(2026, 6, 1).isoformat(),
                "date_end": date(2026, 6, 7).isoformat(),
                "frequency_per_week": 1,
                "description": "Test",
                "overlap_strategy": "add_only",
            },
            format="json",
        )
        assert response.status_code == 200
        call_args = MockAnthropic.return_value.messages.create.call_args
        user_message = call_args.kwargs["messages"][0]["content"]
        assert "Français" in user_message

"""Coverage of AIUsage tracking + GET /teams/{id}/ai-usage/ endpoints."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from aiusage.models import AIUsage
from tools.ai_tracking import track_ai_usage

pytestmark = pytest.mark.django_db


# =====================================================================
# Tracking helper unit tests
# =====================================================================


def _fake_response(input_tokens=10, output_tokens=20, cache_creation=0, cache_read=0):
    return SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
        )
    )


def test_track_ai_usage_creates_row(trainer_user):
    team = trainer_user.owned_teams.first()
    response = _fake_response(100, 250)
    row = track_ai_usage(
        response,
        team=team,
        user=trainer_user,
        endpoint="plan",
        model_used="claude-haiku-4-5",
    )
    assert row is not None
    assert row.team_id == team.pk
    assert row.user_id == trainer_user.pk
    assert row.endpoint == "plan"
    assert row.input_tokens == 100
    assert row.output_tokens == 250
    assert row.total_tokens == 350


def test_track_ai_usage_with_no_team_works(authenticated_user):
    response = _fake_response(5, 1)
    row = track_ai_usage(
        response, team=None, user=authenticated_user, endpoint="ping", model_used="claude-haiku-4-5"
    )
    assert row is not None
    assert row.team is None
    assert row.user_id == authenticated_user.pk


def test_track_ai_usage_with_no_user_works():
    response = _fake_response(2, 3)
    row = track_ai_usage(
        response, team=None, user=None, endpoint="ping", model_used="claude-haiku-4-5"
    )
    assert row is not None
    assert row.team is None
    assert row.user is None


def test_track_ai_usage_silent_on_error(caplog):
    """A malformed response must not raise — only log."""
    row = track_ai_usage(
        SimpleNamespace(usage=SimpleNamespace(input_tokens="not_an_int")),
        team=None,
        user=None,
        endpoint="ping",
        model_used="claude-haiku-4-5",
    )
    # Either tracker swallowed and returned None, or coerced to 0; both are
    # acceptable as long as no exception bubbles up.
    assert row is None or isinstance(row, AIUsage)


def test_total_tokens_auto_computed_at_save(trainer_user):
    row = AIUsage.objects.create(
        team=trainer_user.owned_teams.first(),
        user=trainer_user,
        endpoint="training",
        model_used="claude-haiku-4-5",
        input_tokens=100,
        output_tokens=200,
        cache_creation_tokens=50,
        cache_read_tokens=25,
    )
    assert row.total_tokens == 375


# =====================================================================
# Integration test : /ai/ping/ creates an AIUsage row
# =====================================================================


def _mock_ping_response():
    response = MagicMock()
    block = MagicMock()
    block.text = "OK"
    response.content = [block]
    response.model = "claude-haiku-4-5-20251001"
    response.usage.input_tokens = 5
    response.usage.output_tokens = 1
    response.usage.cache_creation_input_tokens = 0
    response.usage.cache_read_input_tokens = 0
    response.stop_reason = "end_turn"
    return response


def test_ai_ping_creates_aiusage_row_with_endpoint_ping(
    auth_client_trainer, trainer_user, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test"
    before = AIUsage.objects.count()
    with patch("tools.ai.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = _mock_ping_response()
        response = auth_client_trainer.post("/api/v1/ai/ping/", {"prompt": "hi"}, format="json")
    assert response.status_code == 200
    assert AIUsage.objects.count() == before + 1
    row = AIUsage.objects.latest("created_at")
    assert row.endpoint == "ping"
    assert row.team is None
    assert row.user_id == trainer_user.pk
    assert row.input_tokens == 5
    assert row.output_tokens == 1


# =====================================================================
# Aggregation endpoint
# =====================================================================


def _seed_ai_usage(team, user, *, count_plan=2, count_ping=1, days_ago=0):
    base = timezone.now() - timedelta(days=days_ago)
    for _ in range(count_plan):
        u = AIUsage.objects.create(
            team=team,
            user=user,
            endpoint="plan",
            model_used="claude-haiku-4-5",
            input_tokens=1000,
            output_tokens=500,
        )
        AIUsage.objects.filter(pk=u.pk).update(created_at=base)
    for _ in range(count_ping):
        u = AIUsage.objects.create(
            team=team,
            user=user,
            endpoint="ping",
            model_used="claude-haiku-4-5",
            input_tokens=10,
            output_tokens=5,
        )
        AIUsage.objects.filter(pk=u.pk).update(created_at=base)


def test_GET_team_ai_usage_as_owner_returns_200(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    _seed_ai_usage(team, trainer_user)
    response = auth_client_trainer.get(f"/api/v1/teams/{team.pk}/ai-usage/")
    assert response.status_code == 200
    body = response.json()
    assert body["team_id"] == team.pk
    assert body["period"] == "month"
    assert body["exclude_ping"] is True
    # plan calls only (ping excluded by default)
    total_calls = sum(row["total_calls"] for row in body["data"])
    assert total_calls == 2


def test_GET_team_ai_usage_as_manager_returns_200(api_client, trainer_user, authenticated_user):
    team = trainer_user.owned_teams.first()
    team.managers.add(authenticated_user)
    _seed_ai_usage(team, trainer_user)
    api_client.force_authenticate(user=authenticated_user)
    response = api_client.get(f"/api/v1/teams/{team.pk}/ai-usage/")
    assert response.status_code == 200


def test_GET_team_ai_usage_as_random_returns_403(api_client, trainer_user, non_trainer_user):
    team = trainer_user.owned_teams.first()
    api_client.force_authenticate(user=non_trainer_user)
    response = api_client.get(f"/api/v1/teams/{team.pk}/ai-usage/")
    assert response.status_code == 403


def test_GET_team_ai_usage_filter_by_period_day(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    _seed_ai_usage(team, trainer_user, count_plan=3, count_ping=0)
    response = auth_client_trainer.get(f"/api/v1/teams/{team.pk}/ai-usage/?period=day")
    assert response.status_code == 200
    body = response.json()
    assert body["period"] == "day"
    assert sum(row["total_calls"] for row in body["data"]) == 3


def test_GET_team_ai_usage_exclude_ping_default_true(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    _seed_ai_usage(team, trainer_user, count_plan=1, count_ping=4)
    # default → ping excluded
    response = auth_client_trainer.get(f"/api/v1/teams/{team.pk}/ai-usage/")
    assert response.status_code == 200
    body = response.json()
    assert sum(row["total_calls"] for row in body["data"]) == 1
    # exclude_ping=false → ping included
    response = auth_client_trainer.get(f"/api/v1/teams/{team.pk}/ai-usage/?exclude_ping=false")
    body = response.json()
    assert sum(row["total_calls"] for row in body["data"]) == 5


def test_GET_team_ai_usage_invalid_period_returns_400(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    response = auth_client_trainer.get(f"/api/v1/teams/{team.pk}/ai-usage/?period=hour")
    assert response.status_code == 400
    body = response.json()
    # Now goes through the standard DRF ChoiceField validation +
    # custom_exception_handler normalisation. The field-level code
    # remains identifiable via fields.period[0].code.
    assert body["code"] == "validation_error"
    assert body["fields"]["period"][0]["code"] == "invalid_choice"


def test_GET_team_ai_usage_invalid_start_date_returns_400(auth_client_trainer, trainer_user):
    """Replication of I5 fix on aiusage: malformed ?start used to bubble up
    as 500 from the ORM."""
    team = trainer_user.owned_teams.first()
    response = auth_client_trainer.get(f"/api/v1/teams/{team.pk}/ai-usage/?start=foo")
    assert response.status_code == 400


def test_GET_team_ai_usage_details_invalid_since_returns_400(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    response = auth_client_trainer.get(
        f"/api/v1/teams/{team.pk}/ai-usage/details/?since=not-a-date"
    )
    assert response.status_code == 400


# =====================================================================
# Details endpoint
# =====================================================================


def test_GET_team_ai_usage_details_returns_paginated_rows(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    _seed_ai_usage(team, trainer_user, count_plan=3, count_ping=2)
    response = auth_client_trainer.get(f"/api/v1/teams/{team.pk}/ai-usage/details/")
    assert response.status_code == 200
    body = response.json()
    assert "results" in body  # paginated
    assert body["count"] == 5  # plan + ping rows


def test_GET_team_ai_usage_details_includes_username(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    _seed_ai_usage(team, trainer_user, count_plan=1, count_ping=0)
    response = auth_client_trainer.get(f"/api/v1/teams/{team.pk}/ai-usage/details/")
    body = response.json()
    assert body["count"] == 1
    row = body["results"][0]
    # Email-only: the "username" denorm field is sourced off email now.
    assert row["username"] == trainer_user.email
    assert row["endpoint"] == "plan"
    assert row["total_tokens"] == 1500


def test_GET_team_ai_usage_details_filter_by_endpoint(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    _seed_ai_usage(team, trainer_user, count_plan=2, count_ping=3)
    response = auth_client_trainer.get(f"/api/v1/teams/{team.pk}/ai-usage/details/?endpoint=ping")
    body = response.json()
    assert body["count"] == 3
    for row in body["results"]:
        assert row["endpoint"] == "ping"

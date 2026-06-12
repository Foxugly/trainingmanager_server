"""Coverage of POST /api/v1/teams/{id}/review-block/ — AI training-block review.

Permissions:
  - Owner / manager: 200 with a structured critique
  - Athlete / other authenticated user: 403
  - Unauthenticated: 401

The Anthropic call is mocked (tools.ai.Anthropic) so no network is hit. The
view assembles the team-aggregate stats, runs the (forced-tool) review, records
an AIUsage row (endpoint=review), and returns the validated payload.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model

from aiusage.models import AIUsage
from member.models import Member
from team.models import TeamMembership
from tests.factories import ProgramFactory, TeamFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


def _url(team_pk):
    return f"/api/v1/teams/{team_pk}/review-block/"


def _mock_review_response(tool_input):
    response = MagicMock()
    block = MagicMock()
    block.type = "tool_use"
    block.name = "review_training_block"
    block.input = tool_input
    response.content = [block]
    response.model = "claude-haiku-4-5-20251001"
    response.usage.input_tokens = 300
    response.usage.output_tokens = 400
    response.usage.cache_creation_input_tokens = 0
    response.usage.cache_read_input_tokens = 0
    response.stop_reason = "tool_use"
    return response


_GOOD_TOOL_INPUT = {
    "summary": "Solid aerobic base; attendance dipping mid-block.",
    "load_assessment": "balanced",
    "findings": [
        {"area": "attendance", "severity": "warning", "observation": "Drop in week 3."},
        {"area": "intensity", "severity": "info", "observation": "Z1 heavy."},
    ],
    "adjustments": [
        {"recommendation": "Add a threshold set.", "rationale": "Broaden zones."},
    ],
    "confidence": "medium",
}


@pytest.fixture
def owner_user(db):
    return User.objects.create_user(
        email="rev_owner@local.test", password="pass"
    )


@pytest.fixture
def team(owner_user):
    return TeamFactory(owner=owner_user, is_active=True)


@pytest.fixture
def program(team):
    return ProgramFactory(team=team)


@pytest.fixture
def owner_client(api_client, owner_user):
    api_client.force_authenticate(user=owner_user)
    return api_client


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------


def test_unauthenticated_returns_401(api_client, team):
    assert api_client.post(_url(team.pk), {}, format="json").status_code == 401


def test_athlete_member_returns_403(api_client, team, settings):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    athlete = User.objects.create_user(
        email="rev_ath@local.test", password="pass"
    )
    member = Member.objects.create(
        firstname="Ath", lastname="Lete", email="a@x.test", user=athlete
    )
    TeamMembership.objects.create(team=team, member=member)
    api_client.force_authenticate(user=athlete)
    assert api_client.post(_url(team.pk), {}, format="json").status_code == 403


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


def test_owner_gets_structured_review(owner_client, team, program, settings):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    with patch("tools.ai.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = _mock_review_response(
            _GOOD_TOOL_INPUT
        )
        resp = owner_client.post(_url(team.pk), {}, format="json")

    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["summary"].startswith("Solid aerobic base")
    assert body["load_assessment"] == "balanced"
    assert body["confidence"] == "medium"
    assert len(body["findings"]) == 2
    assert body["findings"][0]["area"] == "attendance"
    assert len(body["adjustments"]) == 1
    assert body["tokens_used"] == {"input": 300, "output": 400}
    assert "period" in body


def test_review_records_ai_usage(owner_client, team, program, settings):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    with patch("tools.ai.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = _mock_review_response(
            _GOOD_TOOL_INPUT
        )
        owner_client.post(_url(team.pk), {}, format="json")

    usage = AIUsage.objects.filter(team=team, endpoint="review")
    assert usage.count() == 1
    assert usage.first().total_tokens == 700


def test_review_drops_invalid_enums(owner_client, team, program, settings):
    """A finding with an out-of-vocab area is dropped; a bad load_assessment
    falls back to 'uncertain' (we never trust the model blindly)."""
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    tool_input = {
        "summary": "x",
        "load_assessment": "explosive",  # not a valid enum -> uncertain
        "findings": [
            {"area": "attendance", "severity": "info", "observation": "ok"},
            {"area": "vibes", "severity": "info", "observation": "dropped"},
            {"area": "volume", "severity": "loud", "observation": "dropped-sev"},
        ],
        "adjustments": [
            {"recommendation": "keep going", "rationale": ""},
            {"recommendation": "  ", "rationale": "blank -> dropped"},
        ],
        "confidence": "wat",  # invalid -> low
    }
    with patch("tools.ai.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = _mock_review_response(
            tool_input
        )
        resp = owner_client.post(_url(team.pk), {}, format="json")

    body = resp.json()
    assert body["load_assessment"] == "uncertain"
    assert body["confidence"] == "low"
    assert len(body["findings"]) == 1
    assert body["findings"][0]["area"] == "attendance"
    assert len(body["adjustments"]) == 1


def test_review_not_configured_returns_500(owner_client, team, program, settings):
    settings.ANTHROPIC_API_KEY = ""
    resp = owner_client.post(_url(team.pk), {}, format="json")
    assert resp.status_code == 500
    assert resp.json()["code"] == "ai_configuration_error"

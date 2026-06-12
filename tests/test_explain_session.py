"""Coverage of POST /api/v1/events/{id}/explain/ (F7) — AI athlete brief."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from event.models import Event
from member.models import Member
from team.models import TeamMembership
from tests.factories import ProgramFactory, TeamFactory

pytestmark = pytest.mark.django_db
User = get_user_model()


def _url(event_pk):
    return f"/api/v1/events/{event_pk}/explain/"


def _mock_explain(brief="Today is an aerobic base session — hold your form."):
    response = MagicMock()
    block = MagicMock()
    block.type = "tool_use"
    block.name = "explain_session_for_athletes"
    block.input = {"athlete_brief": brief}
    response.content = [block]
    response.model = "claude-haiku-4-5-20251001"
    response.usage.input_tokens = 150
    response.usage.output_tokens = 120
    response.usage.cache_creation_input_tokens = 0
    response.usage.cache_read_input_tokens = 0
    response.stop_reason = "tool_use"
    return response


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="ex_o@x.test", password="p")


@pytest.fixture
def team(owner):
    return TeamFactory(owner=owner, is_active=True)


@pytest.fixture
def event(team):
    program = ProgramFactory(team=team)
    return Event.objects.create(
        refer_program=program, name="Sess", total=2000,
        date=timezone.localdate() + timedelta(days=1),
    )


def test_unauthenticated_returns_401(api_client, event):
    assert api_client.post(_url(event.pk), {}, format="json").status_code == 401


def test_athlete_forbidden(api_client, team, event, settings):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake"
    athlete = User.objects.create_user(email="ex_a@x.test", password="p")
    m = Member.objects.create(firstname="A", lastname="T", user=athlete)
    TeamMembership.objects.create(team=team, member=m)
    api_client.force_authenticate(user=athlete)
    assert api_client.post(_url(event.pk), {}, format="json").status_code == 403


def test_manager_generates_and_stores_brief(api_client, owner, event, settings):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake"
    api_client.force_authenticate(user=owner)
    with patch("tools.ai.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = _mock_explain()
        resp = api_client.post(_url(event.pk), {}, format="json")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["athlete_brief"].startswith("Today is an aerobic")
    event.refresh_from_db()
    assert event.ai_athlete_brief.startswith("Today is an aerobic")
    assert event.ai_brief_generated_at is not None


def test_not_configured_returns_500(api_client, owner, event, settings):
    settings.ANTHROPIC_API_KEY = ""
    api_client.force_authenticate(user=owner)
    resp = api_client.post(_url(event.pk), {}, format="json")
    assert resp.status_code == 500

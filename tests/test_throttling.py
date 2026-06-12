from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from exercise.models import EnergySegment, EnergySystem, Modality
from tests.factories import EventFactory, ProgramFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def set_throttle_rate(monkeypatch):
    """Override DRF Throttle.THROTTLE_RATES for the duration of a test.

    Direct monkey-patching is required because SimpleRateThrottle.THROTTLE_RATES
    is captured as a class attribute at import time; pytest-django's settings
    fixture only replaces the live settings dict and does not propagate.

    Both UserRateThrottle (AI endpoints, JWT-authenticated) and
    AnonRateThrottle (auth flow endpoints, anonymous) read from this dict,
    so we patch both class attributes."""
    from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

    new_rates = dict(UserRateThrottle.THROTTLE_RATES)

    def _set(scope, rate):
        new_rates[scope] = rate

    monkeypatch.setattr(UserRateThrottle, "THROTTLE_RATES", new_rates)
    monkeypatch.setattr(AnonRateThrottle, "THROTTLE_RATES", new_rates)
    return _set


def _mock_ping_response():
    response = MagicMock()
    block = MagicMock()
    block.text = "OK"
    response.content = [block]
    response.model = "claude-haiku-4-5-20251001"
    response.usage.input_tokens = 5
    response.usage.output_tokens = 1
    response.stop_reason = "end_turn"
    return response


def _mock_plan_response():
    response = MagicMock()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "create_training_plan"
    tool_block.input = {
        "events": [
            {
                "name": "X",
                "goal": "Y",
                "date": date(2026, 5, 1).isoformat(),
                "total_distance": 1000,
                "color": "#3498db",
            }
        ],
        "rationale": "Test",
    }
    response.content = [tool_block]
    response.model = "claude-haiku-4-5-20251001"
    response.usage.input_tokens = 100
    response.usage.output_tokens = 200
    response.stop_reason = "tool_use"
    return response


def _mock_training_response(rounds_payload):
    response = MagicMock()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "create_training_session"
    tool_block.input = {"rounds": rounds_payload, "rationale": "Test"}
    response.content = [tool_block]
    response.model = "claude-haiku-4-5-20251001"
    response.usage.input_tokens = 500
    response.usage.output_tokens = 800
    response.stop_reason = "tool_use"
    return response


# ----------------------------- /ai/ping/ -----------------------------


def test_ai_ping_throttle_after_limit_returns_429(auth_client_trainer, settings, set_throttle_rate):
    set_throttle_rate("ai_ping", "2/min")
    settings.ANTHROPIC_API_KEY = "sk-ant-fake"

    with patch("tools.ai.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = _mock_ping_response()
        r1 = auth_client_trainer.post("/api/v1/ai/ping/", {"prompt": "hi"}, format="json")
        r2 = auth_client_trainer.post("/api/v1/ai/ping/", {"prompt": "hi"}, format="json")
        r3 = auth_client_trainer.post("/api/v1/ai/ping/", {"prompt": "hi"}, format="json")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


def test_ai_ping_throttle_below_limit_passes(auth_client_trainer, settings, set_throttle_rate):
    set_throttle_rate("ai_ping", "5/min")
    settings.ANTHROPIC_API_KEY = "sk-ant-fake"

    with patch("tools.ai.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = _mock_ping_response()
        for _ in range(5):
            r = auth_client_trainer.post("/api/v1/ai/ping/", {"prompt": "hi"}, format="json")
            assert r.status_code == 200


# ----------------- /programs/{id}/generate-events/ -------------------


def test_generate_events_throttle_after_limit_returns_429(
    auth_client_trainer, trainer_user, settings, set_throttle_rate
):
    set_throttle_rate("ai_plan_generation", "2/min")
    settings.ANTHROPIC_API_KEY = "sk-ant-fake"
    program = ProgramFactory(team=trainer_user.owned_teams.first())

    payload = {
        "date_start": "2026-05-01",
        "date_end": "2026-05-07",
        "frequency_per_week": 1,
        "description": "x",
        "overlap_strategy": "merge",
    }

    with patch("tools.ai.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = _mock_plan_response()
        r1 = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/", payload, format="json"
        )
        r2 = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/", payload, format="json"
        )
        r3 = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/", payload, format="json"
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


# --------------- /events/{id}/generate-training/ --------------------


def _trainer_event(trainer_user):
    team = trainer_user.owned_teams.first()
    sport = team.sport
    Modality.objects.create(name="Free", sport=sport)
    es = EnergySystem.objects.create(name="Aero")
    EnergySegment.objects.create(abv="A1", energysystem=es)
    program = ProgramFactory(team=team)
    return EventFactory(refer_program=program, total=1000)


def test_generate_training_throttle_after_limit_returns_429(
    auth_client_trainer, trainer_user, settings, set_throttle_rate
):
    set_throttle_rate("ai_training_generation", "1/min")
    settings.ANTHROPIC_API_KEY = "sk-ant-fake"

    e1 = _trainer_event(trainer_user)
    team = trainer_user.owned_teams.first()
    program = ProgramFactory(team=team)
    e2 = EventFactory(refer_program=program, total=1000)

    mod = Modality.objects.filter(sport=team.sport).first()
    seg = EnergySegment.objects.first()
    rounds_payload = [
        {
            "count": 1,
            "exercises": [
                {
                    "modality_id": mod.id,
                    "energysegment_id": seg.id,
                    "distance": 100,
                    "repetition": 1,
                }
            ],
        }
    ]

    with patch("tools.ai.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = _mock_training_response(
            rounds_payload
        )
        r1 = auth_client_trainer.post(
            f"/api/v1/events/{e1.pk}/generate-training/", {}, format="json"
        )
        r2 = auth_client_trainer.post(
            f"/api/v1/events/{e2.pk}/generate-training/", {}, format="json"
        )

    assert r1.status_code == 200
    assert r2.status_code == 429


# -------------------- Independence between scopes --------------------


def test_throttle_scopes_are_independent(
    auth_client_trainer, trainer_user, settings, set_throttle_rate
):
    """Saturer ai_ping ne doit pas bloquer ai_plan_generation."""
    set_throttle_rate("ai_ping", "1/min")
    set_throttle_rate("ai_plan_generation", "5/min")
    settings.ANTHROPIC_API_KEY = "sk-ant-fake"

    program = ProgramFactory(team=trainer_user.owned_teams.first())
    payload = {
        "date_start": "2026-05-01",
        "date_end": "2026-05-07",
        "frequency_per_week": 1,
        "description": "x",
        "overlap_strategy": "merge",
    }

    with patch("tools.ai.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = _mock_ping_response()
        r_p1 = auth_client_trainer.post("/api/v1/ai/ping/", {"prompt": "hi"}, format="json")
        r_p2 = auth_client_trainer.post("/api/v1/ai/ping/", {"prompt": "hi"}, format="json")
        assert r_p1.status_code == 200
        assert r_p2.status_code == 429

        MockAnthropic.return_value.messages.create.return_value = _mock_plan_response()
        r_g = auth_client_trainer.post(
            f"/api/v1/programs/{program.pk}/generate-events/", payload, format="json"
        )
    assert r_g.status_code == 200


# =====================================================================
# Auth flow throttles (Batch 2): register / email/resend / token
# =====================================================================


def _register_payload(suffix):
    return {
        "email": f"thr_user_{suffix}@local.test",
        "password": "Sup3rS@fePass!",
        "first_name": "Thr",
        "last_name": "User",
        "language": "en",
        "turnstile_token": "mock-token",
    }


def test_register_throttle_returns_429_after_limit(api_client, monkeypatch, set_throttle_rate):
    """Anti-bot signup: 5 hits/h is the prod default; reduce here to 2 to
    fit the test budget."""
    set_throttle_rate("auth_register", "2/min")
    monkeypatch.setattr(
        "customuser.views.registration.verify_turnstile_token", lambda token, remote_ip=None: True
    )

    r1 = api_client.post("/api/v1/auth/register/", _register_payload("a"), format="json")
    r2 = api_client.post("/api/v1/auth/register/", _register_payload("b"), format="json")
    r3 = api_client.post("/api/v1/auth/register/", _register_payload("c"), format="json")
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r3.status_code == 429


def test_resend_email_throttle_returns_429_after_limit(api_client, set_throttle_rate):
    set_throttle_rate("auth_resend_email", "2/min")
    r1 = api_client.post("/api/v1/auth/email/resend/", {"email": "a@local.test"}, format="json")
    r2 = api_client.post("/api/v1/auth/email/resend/", {"email": "b@local.test"}, format="json")
    r3 = api_client.post("/api/v1/auth/email/resend/", {"email": "c@local.test"}, format="json")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


def test_login_throttle_returns_429_after_limit(api_client, set_throttle_rate):
    """Anti-bruteforce. Bad creds also count toward the throttle (DRF
    increments on the request, not the response)."""
    set_throttle_rate("auth_login", "3/min")
    payload = {"email": "ghost@local.test", "password": "wrong"}
    statuses = [
        api_client.post("/api/v1/auth/token/", payload, format="json").status_code for _ in range(4)
    ]
    # First 3 attempts get a normal 401 (creds rejected); the 4th hits 429.
    assert statuses[:3] == [401, 401, 401]
    assert statuses[3] == 429

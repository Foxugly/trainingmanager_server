from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db


def _mock_anthropic_response(text="Hello", input_tokens=10, output_tokens=2):
    response = MagicMock()
    block = MagicMock()
    block.text = text
    response.content = [block]
    response.model = "claude-haiku-4-5-20251001"
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    response.stop_reason = "end_turn"
    return response


def test_POST_ai_ping_as_trainer_returns_200(auth_client_trainer, settings):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_anthropic_response(
            text="Hello",
            input_tokens=12,
            output_tokens=1,
        )
        response = auth_client_trainer.post(
            "/api/v1/ai/ping/",
            {"prompt": "Say hello"},
            format="json",
        )

    assert response.status_code == 200
    data = response.json()
    assert data["text"] == "Hello"
    assert data["input_tokens"] == 12
    assert data["output_tokens"] == 1
    assert data["model"] == "claude-haiku-4-5-20251001"
    assert data["stop_reason"] == "end_turn"


def test_POST_ai_ping_default_prompt_used_when_missing(auth_client_trainer, settings):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_anthropic_response()
        response = auth_client_trainer.post("/api/v1/ai/ping/", {}, format="json")
        # The default prompt should be used.
        called_kwargs = mock_client.messages.create.call_args.kwargs
        assert called_kwargs["messages"][0]["content"] == "Say hello in one word."

    assert response.status_code == 200


def test_POST_ai_ping_as_non_trainer_returns_403(auth_client_non_trainer):
    response = auth_client_non_trainer.post("/api/v1/ai/ping/", {}, format="json")
    assert response.status_code == 403


def test_POST_ai_ping_unauthenticated_returns_401(api_client):
    response = api_client.post("/api/v1/ai/ping/", {}, format="json")
    assert response.status_code == 401


def test_POST_ai_ping_empty_prompt_returns_400(auth_client_trainer, settings):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    response = auth_client_trainer.post(
        "/api/v1/ai/ping/",
        {"prompt": "   "},
        format="json",
    )
    assert response.status_code == 400


def test_POST_ai_ping_too_long_prompt_returns_400(auth_client_trainer, settings):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    response = auth_client_trainer.post(
        "/api/v1/ai/ping/",
        {"prompt": "x" * 5001},
        format="json",
    )
    assert response.status_code == 400


def test_POST_ai_ping_no_api_key_returns_500(auth_client_trainer, settings):
    settings.ANTHROPIC_API_KEY = ""
    response = auth_client_trainer.post(
        "/api/v1/ai/ping/",
        {"prompt": "test"},
        format="json",
    )
    assert response.status_code == 500
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_POST_ai_ping_api_error_returns_502(auth_client_trainer, settings):
    from anthropic import APIError

    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.side_effect = APIError(
            "Service down",
            request=MagicMock(),
            body=None,
        )
        response = auth_client_trainer.post(
            "/api/v1/ai/ping/",
            {"prompt": "test"},
            format="json",
        )

    assert response.status_code == 502


def test_POST_ai_ping_authentication_error_returns_500(auth_client_trainer, settings):
    from anthropic import AuthenticationError

    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"

    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.side_effect = AuthenticationError(
            "Invalid API key",
            response=MagicMock(status_code=401),
            body=None,
        )
        response = auth_client_trainer.post(
            "/api/v1/ai/ping/",
            {"prompt": "test"},
            format="json",
        )

    assert response.status_code == 500

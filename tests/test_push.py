import pytest

from notifications import push
from notifications.push import InvalidPushTokenError, send_push_to_device


def test_send_is_mocked_when_unconfigured(settings):
    settings.FCM_SERVICE_ACCOUNT_PATH = ""
    assert send_push_to_device("t" * 40, "Hi", "Body") == "mock-message-id"


def test_invalid_token_maps_to_invalid_push_token_error(settings, monkeypatch):
    settings.FCM_SERVICE_ACCOUNT_PATH = "/tmp/fake-service-account.json"
    monkeypatch.setattr(push, "_ensure_fcm_initialized", lambda: None)
    from firebase_admin import messaging

    def _raise(_message):
        raise messaging.UnregisteredError("token gone")

    monkeypatch.setattr(messaging, "send", _raise)
    with pytest.raises(InvalidPushTokenError):
        send_push_to_device("t" * 40, "Hi", "Body")

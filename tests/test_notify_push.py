import pytest
from django.contrib.auth import get_user_model

from devices.models import Device, DevicePlatform, DeviceTokenStatus
from notifications import services
from notifications.models import NotificationPreference, NotificationType

TYPE = NotificationType.MESSAGE_NEW_REPLY.value


@pytest.fixture
def recipient_with_device(authenticated_user):
    Device.objects.create(
        user=authenticated_user,
        push_token="d" * 40,
        platform=DevicePlatform.ANDROID,
    )
    return authenticated_user


@pytest.mark.django_db
def test_notify_pushes_to_active_device(recipient_with_device, monkeypatch):
    calls = []
    monkeypatch.setattr(
        services,
        "send_push_to_device",
        lambda token, title, body, data=None, platform=None: calls.append(data) or "id",
    )
    services.notify(recipient_with_device, TYPE, "Title", "Body", url="/teams/3")
    assert len(calls) == 1
    assert calls[0]["type"] == TYPE
    assert calls[0]["url"] == "/teams/3"
    assert "notification_id" in calls[0]


@pytest.mark.django_db
def test_notify_skips_push_when_pref_off(recipient_with_device, monkeypatch):
    NotificationPreference.objects.create(
        user=recipient_with_device, type=TYPE, in_app=True, email=False, push=False
    )
    calls = []
    monkeypatch.setattr(
        services,
        "send_push_to_device",
        lambda *a, **k: calls.append(1) or "id",
    )
    services.notify(recipient_with_device, TYPE, "Title", "Body")
    assert calls == []


@pytest.mark.django_db
def test_notify_marks_device_invalid_on_bad_token(recipient_with_device, monkeypatch):
    def _raise(*a, **k):
        raise services.InvalidPushTokenError("gone")

    monkeypatch.setattr(services, "send_push_to_device", _raise)
    services.notify(recipient_with_device, TYPE, "Title", "Body")
    device = Device.objects.get(push_token="d" * 40)
    assert device.status == DeviceTokenStatus.INVALID


@pytest.mark.django_db
def test_notify_no_push_to_actor(recipient_with_device, monkeypatch):
    calls = []
    monkeypatch.setattr(
        services, "send_push_to_device", lambda *a, **k: calls.append(1) or "id"
    )
    # actor == recipient => nothing happens at all
    services.notify(
        recipient_with_device, TYPE, "Title", "Body", actor=recipient_with_device
    )
    assert calls == []


@pytest.mark.django_db
def test_notify_many_pushes_per_recipient_with_mixed_prefs(monkeypatch):
    User = get_user_model()
    user_a = User.objects.create_user(email="usera@local.test", password="Str0ngP@ss1!")
    user_b = User.objects.create_user(email="userb@local.test", password="Str0ngP@ss2!")
    Device.objects.create(user=user_a, push_token="a" * 40, platform=DevicePlatform.ANDROID)
    Device.objects.create(user=user_b, push_token="e" * 40, platform=DevicePlatform.ANDROID)
    NotificationPreference.objects.create(
        user=user_b, type=TYPE, in_app=True, email=False, push=False
    )
    calls = []
    monkeypatch.setattr(
        services,
        "send_push_to_device",
        lambda token, title, body, data=None, platform=None: calls.append(
            {"token": token, "data": data}
        )
        or "id",
    )
    services.notify_many([user_a, user_b], TYPE, "T", "B", url="/teams/9")
    assert len(calls) == 1
    assert "notification_id" in calls[0]["data"]
    assert calls[0]["data"]["type"] == TYPE
    assert calls[0]["data"]["url"] == "/teams/9"


@pytest.mark.django_db
def test_notify_many_excludes_actor(monkeypatch):
    User = get_user_model()
    user_a = User.objects.create_user(email="actora@local.test", password="Str0ngP@ss1!")
    user_b = User.objects.create_user(email="recipientb@local.test", password="Str0ngP@ss2!")
    Device.objects.create(user=user_a, push_token="a" * 40, platform=DevicePlatform.ANDROID)
    Device.objects.create(user=user_b, push_token="b" * 40, platform=DevicePlatform.ANDROID)
    calls = []
    monkeypatch.setattr(
        services,
        "send_push_to_device",
        lambda token, title, body, data=None, platform=None: calls.append(token) or "id",
    )
    services.notify_many([user_a, user_b], TYPE, "T", "B", actor=user_a)
    assert len(calls) == 1
    assert calls[0] == "b" * 40


@pytest.mark.django_db
def test_notify_marks_failure_count_on_provider_error(recipient_with_device, monkeypatch):
    def _raise(*a, **k):
        raise services.PushProviderError("boom")

    monkeypatch.setattr(services, "send_push_to_device", _raise)
    services.notify(recipient_with_device, TYPE, "Title", "Body")
    device = Device.objects.get(push_token="d" * 40)
    device.refresh_from_db()
    assert device.failure_count > 0
    assert device.status == DeviceTokenStatus.ACTIVE

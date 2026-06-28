import pytest

from devices.models import Device, DeviceTokenStatus

TOKEN = "f" * 40


@pytest.mark.django_db
def test_register_creates_device(auth_client, authenticated_user):
    resp = auth_client.post(
        "/api/v1/devices/register/",
        {"push_token": TOKEN, "platform": "android"},
        format="json",
    )
    assert resp.status_code == 201
    device = Device.objects.get(push_token=TOKEN)
    assert device.user == authenticated_user
    assert device.status == DeviceTokenStatus.ACTIVE


@pytest.mark.django_db
def test_register_is_idempotent_upsert(auth_client):
    auth_client.post(
        "/api/v1/devices/register/",
        {"push_token": TOKEN, "platform": "android"},
        format="json",
    )
    resp = auth_client.post(
        "/api/v1/devices/register/",
        {"push_token": TOKEN, "platform": "ios", "device_name": "iPhone"},
        format="json",
    )
    assert resp.status_code == 200
    assert Device.objects.filter(push_token=TOKEN).count() == 1
    device = Device.objects.get(push_token=TOKEN)
    assert device.platform == "ios"
    assert device.device_name == "iPhone"


@pytest.mark.django_db
def test_unregister_revokes(auth_client):
    auth_client.post(
        "/api/v1/devices/register/",
        {"push_token": TOKEN, "platform": "android"},
        format="json",
    )
    resp = auth_client.post(
        "/api/v1/devices/unregister/", {"push_token": TOKEN}, format="json"
    )
    assert resp.status_code == 204
    assert Device.objects.get(push_token=TOKEN).status == DeviceTokenStatus.REVOKED


@pytest.mark.django_db
def test_list_returns_only_active_for_caller(auth_client):
    from django.contrib.auth import get_user_model

    from devices.models import DevicePlatform

    resp = auth_client.post(
        "/api/v1/devices/register/",
        {"push_token": TOKEN, "platform": "android"},
        format="json",
    )
    caller_device_id = resp.json()["id"]

    other = get_user_model().objects.create_user(email="other@local.test", password="Str0ngP@ssOther!")
    Device.objects.create(user=other, push_token="o" * 40, platform=DevicePlatform.ANDROID)

    resp = auth_client.get("/api/v1/devices/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == caller_device_id


@pytest.mark.django_db
def test_register_requires_auth(api_client):
    resp = api_client.post(
        "/api/v1/devices/register/",
        {"push_token": TOKEN, "platform": "android"},
        format="json",
    )
    assert resp.status_code == 401


@pytest.mark.django_db
def test_unregister_requires_auth(api_client):
    resp = api_client.post(
        "/api/v1/devices/unregister/",
        {"push_token": TOKEN},
        format="json",
    )
    assert resp.status_code == 401


@pytest.mark.django_db
def test_list_requires_auth(api_client):
    resp = api_client.get("/api/v1/devices/")
    assert resp.status_code == 401

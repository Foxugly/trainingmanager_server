import pytest

from devices.models import Device, DevicePlatform, DeviceTokenStatus


@pytest.mark.django_db
def test_device_defaults_active(authenticated_user):
    device = Device.objects.create(
        user=authenticated_user,
        push_token="x" * 40,
        platform=DevicePlatform.ANDROID,
    )
    assert device.status == DeviceTokenStatus.ACTIVE
    assert device.failure_count == 0
    assert device.created_at is not None

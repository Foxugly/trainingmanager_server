"""The admin 2FA (admin_2fa/) must not change anything for API users, staff included.

Also pins the nginx side: the 2FA steps share the admin login rate-limit zone.
"""

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework.test import APIClient

PASSWORD = "Sup3rS@fePass!"
VHOST = Path(__file__).resolve().parent.parent / "deploy" / "nginx" / "tm-api.conf"


@pytest.mark.django_db
@pytest.mark.parametrize("enrolled", [False, True])
def test_staff_uses_the_api_with_a_password_only(enrolled):
    user = get_user_model().objects.create_user(
        email="ops@local.test", password=PASSWORD, email_confirmed=True, is_staff=True
    )
    if enrolled:
        TOTPDevice.objects.create(user=user, name="admin-2fa", confirmed=True)

    client = APIClient()
    login = client.post(
        "/api/v1/auth/token/", {"email": user.email, "password": PASSWORD}, format="json"
    )
    assert login.status_code == 200, login.json()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")
    me = client.get("/api/v1/me/")
    assert me.status_code == 200
    assert me.json()["email"] == "ops@local.test"
    assert me.json()["is_staff"] is True


@pytest.mark.django_db
def test_health_stays_public():
    assert APIClient().get("/health/").status_code == 200


def test_admin_2fa_steps_share_the_login_rate_limit():
    conf = VHOST.read_text(encoding="utf-8")
    block = conf.split("location ^~ /admin/2fa/ {", 1)[1].split("}", 1)[0]
    assert "limit_req zone=tm_admin_login" in block
    assert "proxy_pass http://127.0.0.1:8005;" in block

"""Endpoints staff d'administration du flag subscription_bypass (spec lot A §A.4).

- Réservés à is_staff : un utilisateur authentifié ordinaire reçoit 403.
- L'activation horodate bypass_granted_at ; la révocation le conserve.
"""

import pytest
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db

User = get_user_model()


def _user(name, **kwargs):
    return User.objects.create_user(email=f"{name}@local.test", password="Sup3rS@fePass!", **kwargs)


def test_GET_staff_users_as_admin_returns_200(admin_client):
    _user("searchable")
    response = admin_client.get("/api/v1/staff/users/?q=searchable")
    assert response.status_code == 200, response.json()
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["email"] == "searchable@local.test"
    assert results[0]["subscription_bypass"] is False


def test_GET_staff_users_as_plain_user_returns_403(auth_client):
    response = auth_client.get("/api/v1/staff/users/?q=x")
    assert response.status_code == 403, response.json()


def test_GET_staff_users_anonymous_returns_401(api_client):
    response = api_client.get("/api/v1/staff/users/?q=x")
    assert response.status_code == 401


def test_PATCH_staff_user_grants_bypass_and_stamps_date(admin_client):
    target = _user("grantee")
    response = admin_client.patch(
        f"/api/v1/staff/users/{target.pk}/",
        {"subscription_bypass": True, "bypass_note": "asso X"},
        format="json",
    )
    assert response.status_code == 200, response.json()
    target.refresh_from_db()
    assert target.subscription_bypass is True
    assert target.bypass_note == "asso X"
    assert target.bypass_granted_at is not None


def test_PATCH_staff_user_revoke_keeps_granted_at(admin_client):
    target = _user("revokee")
    admin_client.patch(
        f"/api/v1/staff/users/{target.pk}/", {"subscription_bypass": True}, format="json"
    )
    target.refresh_from_db()
    granted = target.bypass_granted_at
    assert granted is not None
    admin_client.patch(
        f"/api/v1/staff/users/{target.pk}/", {"subscription_bypass": False}, format="json"
    )
    target.refresh_from_db()
    assert target.subscription_bypass is False
    assert target.bypass_granted_at == granted


def test_PATCH_staff_user_as_plain_user_returns_403(auth_client):
    target = _user("protected")
    response = auth_client.patch(
        f"/api/v1/staff/users/{target.pk}/", {"subscription_bypass": True}, format="json"
    )
    assert response.status_code == 403, response.json()
    target.refresh_from_db()
    assert target.subscription_bypass is False

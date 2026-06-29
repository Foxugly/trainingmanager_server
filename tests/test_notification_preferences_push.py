import pytest

from notifications.models import NotificationPreference, NotificationType


@pytest.mark.django_db
def test_preferences_get_defaults_push_true(auth_client):
    resp = auth_client.get("/api/v1/notifications/preferences/")
    assert resp.status_code == 200
    assert all(row["push"] is True for row in resp.json())


@pytest.mark.django_db
def test_preferences_put_sets_push(auth_client, authenticated_user):
    payload = {
        "preferences": [
            {
                "type": NotificationType.MESSAGE_NEW_REPLY.value,
                "in_app": True,
                "email": False,
                "push": False,
            }
        ]
    }
    resp = auth_client.put(
        "/api/v1/notifications/preferences/", payload, format="json"
    )
    assert resp.status_code == 200
    pref = NotificationPreference.objects.get(
        user=authenticated_user, type=NotificationType.MESSAGE_NEW_REPLY.value
    )
    assert pref.push is False

"""Coverage of the notification system.

Three layers:
  - notify() service: channel resolution + preference defaults + actor skip.
  - note CREATE triggers: athlete + coach notifications honouring team toggles.
  - endpoints: list/unread_count/read/read_all + preferences GET/PUT.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core import mail

from member.models import Member
from notifications.models import (
    Notification,
    NotificationPreference,
    NotificationType,
)
from notifications.services import get_effective_preferences, notify
from team.models import TeamMembership
from tests.factories import TeamFactory, UserFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


# =====================================================================
# SERVICE: notify()
# =====================================================================


def test_notify_in_app_only_creates_row_no_email():
    recipient = UserFactory(email="r@local.test", language="en")
    NotificationPreference.objects.create(
        user=recipient, type=NotificationType.NOTE_FOR_COACH, in_app=True, email=False
    )
    notify(recipient, NotificationType.NOTE_FOR_COACH, title="Hi", body="b", url="/teams/1")
    assert Notification.objects.filter(recipient=recipient).count() == 1
    assert len(mail.outbox) == 0


def test_notify_email_only_sends_email_no_row():
    recipient = UserFactory(email="r2@local.test", language="en")
    NotificationPreference.objects.create(
        user=recipient, type=NotificationType.NOTE_FOR_COACH, in_app=False, email=True
    )
    notify(recipient, NotificationType.NOTE_FOR_COACH, title="Hi", body="b", url="/teams/1")
    assert Notification.objects.filter(recipient=recipient).count() == 0
    assert len(mail.outbox) == 1
    # The absolute deep link is included in the email body.
    assert "http://localhost:4200/teams/1" in mail.outbox[0].body
    assert mail.outbox[0].subject == "Hi"


def test_notify_both_channels():
    recipient = UserFactory(email="r3@local.test", language="en")
    NotificationPreference.objects.create(
        user=recipient, type=NotificationType.NOTE_FOR_COACH, in_app=True, email=True
    )
    notify(recipient, NotificationType.NOTE_FOR_COACH, title="Hi", body="b", url="/teams/1")
    assert Notification.objects.filter(recipient=recipient).count() == 1
    assert len(mail.outbox) == 1


def test_notify_neither_channel_does_nothing():
    recipient = UserFactory(email="r4@local.test", language="en")
    NotificationPreference.objects.create(
        user=recipient, type=NotificationType.NOTE_FOR_COACH, in_app=False, email=False
    )
    notify(recipient, NotificationType.NOTE_FOR_COACH, title="Hi", body="b", url="/teams/1")
    assert Notification.objects.filter(recipient=recipient).count() == 0
    assert len(mail.outbox) == 0


def test_notify_defaults_both_when_no_pref_row():
    recipient = UserFactory(email="r5@local.test", language="en")
    notify(recipient, NotificationType.NOTE_FOR_COACH, title="Hi", body="b", url="/teams/1")
    assert Notification.objects.filter(recipient=recipient).count() == 1
    assert len(mail.outbox) == 1


def test_notify_never_notifies_actor():
    actor = UserFactory(email="actor@local.test", language="en")
    notify(
        actor,
        NotificationType.NOTE_FOR_COACH,
        title="Hi",
        body="b",
        url="/teams/1",
        actor=actor,
    )
    assert Notification.objects.filter(recipient=actor).count() == 0
    assert len(mail.outbox) == 0


def test_notify_no_email_when_recipient_has_no_email():
    recipient = UserFactory(email="", language="en")
    notify(recipient, NotificationType.NOTE_FOR_COACH, title="Hi", body="b", url="/teams/1")
    # in-app row created (default), but no email since no address.
    assert Notification.objects.filter(recipient=recipient).count() == 1
    assert len(mail.outbox) == 0


def test_get_effective_preferences_covers_all_types_with_defaults():
    user = UserFactory()
    NotificationPreference.objects.create(
        user=user, type=NotificationType.NOTE_FOR_COACH, in_app=False, email=True
    )
    effective = get_effective_preferences(user)
    by_type = {e["type"]: e for e in effective}
    # Every NotificationType is present.
    assert set(by_type) == set(NotificationType.values)
    # Stored row reflected.
    assert by_type[NotificationType.NOTE_FOR_COACH]["in_app"] is False
    assert by_type[NotificationType.NOTE_FOR_COACH]["email"] is True
    # Defaulted type.
    assert by_type[NotificationType.NOTE_FOR_ATHLETE]["in_app"] is True
    assert by_type[NotificationType.NOTE_FOR_ATHLETE]["email"] is True
    # Localized label present.
    assert by_type[NotificationType.NOTE_FOR_ATHLETE]["label"]


# =====================================================================
# NOTE CREATE TRIGGERS
# =====================================================================


def _note_url(team_id, member_id):
    return f"/api/v1/teams/{team_id}/members/{member_id}/notes/"


@pytest.fixture
def coach():
    return UserFactory(email="coach@local.test", language="en")


@pytest.fixture
def athlete():
    return UserFactory(email="athlete@local.test", language="en")


@pytest.fixture
def team(coach):
    return TeamFactory(owner=coach, is_active=True)


@pytest.fixture
def member(athlete, team):
    m = Member.objects.create(firstname="Al", lastname="Ete", email="al@local.test", user=athlete)
    TeamMembership.objects.create(team=team, member=m)
    return m


def test_visible_note_notifies_athlete_when_toggle_on(api_client, coach, team, member, athlete):
    api_client.force_authenticate(user=coach)
    resp = api_client.post(
        _note_url(team.pk, member.pk),
        {"content": "<p>x</p>", "visible_to_athlete": True},
        format="json",
    )
    assert resp.status_code == 201
    assert Notification.objects.filter(
        recipient=athlete, type=NotificationType.NOTE_FOR_ATHLETE
    ).exists()


def test_visible_note_does_not_notify_athlete_when_toggle_off(
    api_client, coach, team, member, athlete
):
    team.notify_athlete_on_visible_note = False
    team.save(update_fields=["notify_athlete_on_visible_note"])
    api_client.force_authenticate(user=coach)
    resp = api_client.post(
        _note_url(team.pk, member.pk),
        {"content": "<p>x</p>", "visible_to_athlete": True},
        format="json",
    )
    assert resp.status_code == 201
    assert not Notification.objects.filter(
        recipient=athlete, type=NotificationType.NOTE_FOR_ATHLETE
    ).exists()


def test_non_visible_note_does_not_notify_athlete(api_client, coach, team, member, athlete):
    api_client.force_authenticate(user=coach)
    resp = api_client.post(
        _note_url(team.pk, member.pk),
        {"content": "<p>x</p>", "visible_to_athlete": False},
        format="json",
    )
    assert resp.status_code == 201
    assert not Notification.objects.filter(
        recipient=athlete, type=NotificationType.NOTE_FOR_ATHLETE
    ).exists()


def test_coaches_notified_except_author(api_client, coach, team, member):
    manager = UserFactory(email="mgr@local.test", language="en")
    team.managers.add(manager)
    # The author is `coach` (the owner): they must NOT be notified, the
    # manager must be.
    api_client.force_authenticate(user=coach)
    resp = api_client.post(
        _note_url(team.pk, member.pk),
        {"content": "<p>x</p>"},
        format="json",
    )
    assert resp.status_code == 201
    coach_notifs = Notification.objects.filter(type=NotificationType.NOTE_FOR_COACH)
    recipients = set(coach_notifs.values_list("recipient_id", flat=True))
    assert manager.pk in recipients
    assert coach.pk not in recipients


def test_coaches_not_notified_when_toggle_off(api_client, coach, team, member):
    manager = UserFactory(email="mgr2@local.test", language="en")
    team.managers.add(manager)
    team.notify_coaches_on_note = False
    team.save(update_fields=["notify_coaches_on_note"])
    api_client.force_authenticate(user=coach)
    resp = api_client.post(
        _note_url(team.pk, member.pk),
        {"content": "<p>x</p>"},
        format="json",
    )
    assert resp.status_code == 201
    assert not Notification.objects.filter(type=NotificationType.NOTE_FOR_COACH).exists()


# =====================================================================
# ENDPOINTS
# =====================================================================


def test_list_returns_only_own_notifications(api_client):
    me = UserFactory()
    other = UserFactory()
    Notification.objects.create(recipient=me, type=NotificationType.NOTE_FOR_COACH, title="mine")
    Notification.objects.create(
        recipient=other, type=NotificationType.NOTE_FOR_COACH, title="theirs"
    )
    api_client.force_authenticate(user=me)
    resp = api_client.get("/api/v1/notifications/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["results"][0]["title"] == "mine"


def test_list_filter_is_read_false(api_client):
    me = UserFactory()
    Notification.objects.create(
        recipient=me, type=NotificationType.NOTE_FOR_COACH, title="unread", is_read=False
    )
    Notification.objects.create(
        recipient=me, type=NotificationType.NOTE_FOR_COACH, title="read", is_read=True
    )
    api_client.force_authenticate(user=me)
    resp = api_client.get("/api/v1/notifications/?is_read=false")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    assert resp.json()["results"][0]["title"] == "unread"


def test_unread_count(api_client):
    me = UserFactory()
    Notification.objects.create(
        recipient=me, type=NotificationType.NOTE_FOR_COACH, title="a", is_read=False
    )
    Notification.objects.create(
        recipient=me, type=NotificationType.NOTE_FOR_COACH, title="b", is_read=False
    )
    Notification.objects.create(
        recipient=me, type=NotificationType.NOTE_FOR_COACH, title="c", is_read=True
    )
    api_client.force_authenticate(user=me)
    resp = api_client.get("/api/v1/notifications/unread_count/")
    assert resp.status_code == 200
    assert resp.json() == {"count": 2}


def test_read_marks_one_read(api_client):
    me = UserFactory()
    n = Notification.objects.create(
        recipient=me, type=NotificationType.NOTE_FOR_COACH, title="x", is_read=False
    )
    api_client.force_authenticate(user=me)
    resp = api_client.post(f"/api/v1/notifications/{n.pk}/read/")
    assert resp.status_code == 200
    assert resp.json()["is_read"] is True
    n.refresh_from_db()
    assert n.is_read is True


def test_read_cannot_mark_others_notification(api_client):
    me = UserFactory()
    other = UserFactory()
    n = Notification.objects.create(
        recipient=other, type=NotificationType.NOTE_FOR_COACH, title="x"
    )
    api_client.force_authenticate(user=me)
    resp = api_client.post(f"/api/v1/notifications/{n.pk}/read/")
    assert resp.status_code == 404
    n.refresh_from_db()
    assert n.is_read is False


def test_read_all(api_client):
    me = UserFactory()
    Notification.objects.create(
        recipient=me, type=NotificationType.NOTE_FOR_COACH, title="a", is_read=False
    )
    Notification.objects.create(
        recipient=me, type=NotificationType.NOTE_FOR_COACH, title="b", is_read=False
    )
    api_client.force_authenticate(user=me)
    resp = api_client.post("/api/v1/notifications/read_all/")
    assert resp.status_code == 200
    assert resp.json() == {"updated": 2}
    assert Notification.objects.filter(recipient=me, is_read=False).count() == 0


def test_preferences_get_returns_all_types_with_defaults(api_client):
    me = UserFactory()
    api_client.force_authenticate(user=me)
    resp = api_client.get("/api/v1/notifications/preferences/")
    assert resp.status_code == 200
    body = resp.json()
    assert {e["type"] for e in body} == set(NotificationType.values)
    for e in body:
        assert e["in_app"] is True
        assert e["email"] is True
        assert e["label"]


def test_preferences_put_upserts(api_client):
    me = UserFactory()
    api_client.force_authenticate(user=me)
    resp = api_client.put(
        "/api/v1/notifications/preferences/",
        {
            "preferences": [
                {"type": NotificationType.NOTE_FOR_COACH, "in_app": False, "email": True},
            ]
        },
        format="json",
    )
    assert resp.status_code == 200
    by_type = {e["type"]: e for e in resp.json()}
    assert by_type[NotificationType.NOTE_FOR_COACH]["in_app"] is False
    assert by_type[NotificationType.NOTE_FOR_COACH]["email"] is True
    # Untouched type keeps defaults.
    assert by_type[NotificationType.NOTE_FOR_ATHLETE]["in_app"] is True
    # Row persisted.
    pref = NotificationPreference.objects.get(user=me, type=NotificationType.NOTE_FOR_COACH)
    assert pref.in_app is False

    # A second PUT updates (upsert, not duplicate).
    resp2 = api_client.put(
        "/api/v1/notifications/preferences/",
        {
            "preferences": [
                {"type": NotificationType.NOTE_FOR_COACH, "in_app": True, "email": False},
            ]
        },
        format="json",
    )
    assert resp2.status_code == 200
    assert (
        NotificationPreference.objects.filter(user=me, type=NotificationType.NOTE_FOR_COACH).count()
        == 1
    )
    pref.refresh_from_db()
    assert pref.in_app is True
    assert pref.email is False


def test_preferences_put_rejects_invalid_type(api_client):
    me = UserFactory()
    api_client.force_authenticate(user=me)
    resp = api_client.put(
        "/api/v1/notifications/preferences/",
        {"preferences": [{"type": "not_a_real_type", "in_app": True, "email": True}]},
        format="json",
    )
    assert resp.status_code == 400


def test_endpoints_require_authentication(api_client):
    resp = api_client.get("/api/v1/notifications/")
    assert resp.status_code == 401

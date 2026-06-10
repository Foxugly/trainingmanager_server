"""Coverage of the per-team messaging feature.

Layers:
  - visibility scoping (athlete never sees coaches-only topics)
  - create permissions (coach only)
  - reply permissions (athlete iff team + allow_athlete_replies)
  - message ordering + topic.updated_at bump on new message
  - delete permissions (author / coach)
  - notification triggers (new topic, new message) + recipient prefs
"""

import pytest
from django.contrib.auth import get_user_model
from django.core import mail

from member.models import Member
from messaging.models import Message, Topic, TopicAudience
from notifications.models import Notification, NotificationPreference, NotificationType
from team.models import TeamMembership
from tests.factories import TeamFactory, UserFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def coach():
    return UserFactory(email="coach@local.test", language="en")


@pytest.fixture
def manager():
    return UserFactory(email="manager@local.test", language="en")


@pytest.fixture
def athlete():
    return UserFactory(email="athlete@local.test", language="en")


@pytest.fixture
def outsider():
    return UserFactory(email="outsider@local.test", language="en")


@pytest.fixture
def team(coach, manager):
    t = TeamFactory(owner=coach, is_active=True)
    t.managers.add(manager)
    return t


@pytest.fixture
def member(athlete, team):
    m = Member.objects.create(
        firstname="Al", lastname="Ete", email="al@local.test", user=athlete
    )
    TeamMembership.objects.create(team=team, member=m)
    return m


def _topics_url(team_id):
    return f"/api/v1/teams/{team_id}/topics/"


def _topic_url(team_id, topic_id):
    return f"/api/v1/teams/{team_id}/topics/{topic_id}/"


def _messages_url(team_id, topic_id):
    return f"/api/v1/teams/{team_id}/topics/{topic_id}/messages/"


def _message_url(team_id, topic_id, msg_id):
    return f"/api/v1/teams/{team_id}/topics/{topic_id}/messages/{msg_id}/"


def _make_topic(team, author, audience=TopicAudience.TEAM, allow_replies=True, title="T"):
    return Topic.objects.create(
        team=team,
        author=author,
        title=title,
        audience=audience,
        allow_athlete_replies=allow_replies,
    )


# ---------------------------------------------------------------------------
# Create permissions
# ---------------------------------------------------------------------------


def test_coach_creates_team_topic(api_client, coach, team, member):
    api_client.force_authenticate(user=coach)
    resp = api_client.post(
        _topics_url(team.pk),
        {"title": "Welcome", "audience": "team"},
        format="json",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Welcome"
    assert body["audience"] == "team"
    assert body["author"]["id"] == coach.pk
    assert body["message_count"] == 0


def test_topic_title_is_html_stripped(api_client, coach, team):
    """A title is plain text — any HTML payload is stripped on save (no
    stored XSS), consistent with how message bodies are sanitized."""
    api_client.force_authenticate(user=coach)
    resp = api_client.post(
        _topics_url(team.pk),
        {"title": "<script>alert(1)</script>Plan", "audience": "team"},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    title = resp.json()["title"]
    assert "<script>" not in title
    assert "Plan" in title


def test_coach_creates_coaches_topic(api_client, coach, team):
    api_client.force_authenticate(user=coach)
    resp = api_client.post(
        _topics_url(team.pk),
        {"title": "Staff only", "audience": "coaches"},
        format="json",
    )
    assert resp.status_code == 201
    assert resp.json()["audience"] == "coaches"


def test_athlete_cannot_create_topic_under_coaches_policy(api_client, athlete, team, member):
    # Default policy is "coaches": athletes cannot create.
    assert team.topic_creation == "coaches"
    api_client.force_authenticate(user=athlete)
    resp = api_client.post(
        _topics_url(team.pk),
        {"title": "Nope", "audience": "team"},
        format="json",
    )
    assert resp.status_code == 403
    assert not Topic.objects.filter(title="Nope").exists()


def test_athlete_can_create_team_topic_under_members_policy(api_client, athlete, team, member):
    team.topic_creation = "members"
    team.save(update_fields=["topic_creation"])
    api_client.force_authenticate(user=athlete)
    resp = api_client.post(
        _topics_url(team.pk),
        {"title": "Athlete topic", "audience": "team"},
        format="json",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["audience"] == "team"
    assert body["author"]["id"] == athlete.pk


def test_athlete_cannot_create_coaches_topic_under_members_policy(api_client, athlete, team, member):
    team.topic_creation = "members"
    team.save(update_fields=["topic_creation"])
    api_client.force_authenticate(user=athlete)
    resp = api_client.post(
        _topics_url(team.pk),
        {"title": "Staff wannabe", "audience": "coaches"},
        format="json",
    )
    assert resp.status_code == 403
    assert not Topic.objects.filter(title="Staff wannabe").exists()


def test_manager_cannot_create_topic_under_owner_policy(api_client, coach, manager, team):
    team.topic_creation = "owner"
    team.save(update_fields=["topic_creation"])
    api_client.force_authenticate(user=manager)
    resp = api_client.post(
        _topics_url(team.pk),
        {"title": "Manager topic", "audience": "team"},
        format="json",
    )
    assert resp.status_code == 403
    assert not Topic.objects.filter(title="Manager topic").exists()


def test_owner_can_create_topic_under_owner_policy(api_client, coach, team):
    team.topic_creation = "owner"
    team.save(update_fields=["topic_creation"])
    api_client.force_authenticate(user=coach)
    resp = api_client.post(
        _topics_url(team.pk),
        {"title": "Owner topic", "audience": "team"},
        format="json",
    )
    assert resp.status_code == 201


def test_non_member_cannot_create_topic_under_members_policy(api_client, outsider, team):
    team.topic_creation = "members"
    team.save(update_fields=["topic_creation"])
    api_client.force_authenticate(user=outsider)
    resp = api_client.post(
        _topics_url(team.pk),
        {"title": "Intruder", "audience": "team"},
        format="json",
    )
    assert resp.status_code == 403
    assert not Topic.objects.filter(title="Intruder").exists()


def test_outsider_cannot_list_topics(api_client, outsider, team):
    api_client.force_authenticate(user=outsider)
    resp = api_client.get(_topics_url(team.pk))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Visibility scoping
# ---------------------------------------------------------------------------


def test_athlete_sees_team_topic_not_coaches_topic(api_client, coach, team, athlete, member):
    team_topic = _make_topic(team, coach, TopicAudience.TEAM, title="Team")
    _make_topic(team, coach, TopicAudience.COACHES, title="Coaches")

    api_client.force_authenticate(user=athlete)
    resp = api_client.get(_topics_url(team.pk))
    assert resp.status_code == 200
    ids = {t["id"] for t in resp.json()["results"]}
    assert team_topic.pk in ids
    titles = {t["title"] for t in resp.json()["results"]}
    assert "Coaches" not in titles


def test_athlete_cannot_retrieve_coaches_topic(api_client, coach, team, athlete, member):
    coaches_topic = _make_topic(team, coach, TopicAudience.COACHES)
    api_client.force_authenticate(user=athlete)
    resp = api_client.get(_topic_url(team.pk, coaches_topic.pk))
    # filtered out of the queryset -> 404 (never leak existence)
    assert resp.status_code == 404


def test_coach_sees_all_topics(api_client, coach, team, member):
    _make_topic(team, coach, TopicAudience.TEAM, title="Team")
    _make_topic(team, coach, TopicAudience.COACHES, title="Coaches")
    api_client.force_authenticate(user=coach)
    resp = api_client.get(_topics_url(team.pk))
    assert resp.status_code == 200
    titles = {t["title"] for t in resp.json()["results"]}
    assert {"Team", "Coaches"} <= titles


def test_manager_sees_coaches_topic(api_client, coach, manager, team):
    coaches_topic = _make_topic(team, coach, TopicAudience.COACHES)
    api_client.force_authenticate(user=manager)
    resp = api_client.get(_topic_url(team.pk, coaches_topic.pk))
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Reply permissions
# ---------------------------------------------------------------------------


def test_athlete_can_reply_when_team_and_allowed(api_client, coach, team, athlete, member):
    topic = _make_topic(team, coach, TopicAudience.TEAM, allow_replies=True)
    api_client.force_authenticate(user=athlete)
    resp = api_client.post(
        _messages_url(team.pk, topic.pk),
        {"content": "<p>hi</p>"},
        format="json",
    )
    assert resp.status_code == 201
    assert resp.json()["author"]["id"] == athlete.pk


def test_athlete_cannot_reply_when_replies_disabled(api_client, coach, team, athlete, member):
    topic = _make_topic(team, coach, TopicAudience.TEAM, allow_replies=False)
    api_client.force_authenticate(user=athlete)
    resp = api_client.post(
        _messages_url(team.pk, topic.pk),
        {"content": "<p>hi</p>"},
        format="json",
    )
    assert resp.status_code == 403
    assert Message.objects.filter(topic=topic).count() == 0


def test_athlete_cannot_reply_to_coaches_topic(api_client, coach, team, athlete, member):
    # Coaches topic is invisible to the athlete -> cannot post (403).
    topic = _make_topic(team, coach, TopicAudience.COACHES)
    api_client.force_authenticate(user=athlete)
    resp = api_client.post(
        _messages_url(team.pk, topic.pk),
        {"content": "<p>hi</p>"},
        format="json",
    )
    assert resp.status_code in (403, 404)
    assert Message.objects.filter(topic=topic).count() == 0


def test_coach_can_always_reply_even_when_replies_disabled(api_client, coach, team):
    topic = _make_topic(team, coach, TopicAudience.TEAM, allow_replies=False)
    api_client.force_authenticate(user=coach)
    resp = api_client.post(
        _messages_url(team.pk, topic.pk),
        {"content": "<p>coach</p>"},
        format="json",
    )
    assert resp.status_code == 201


def test_coach_replies_to_coaches_topic(api_client, coach, manager, team):
    topic = _make_topic(team, coach, TopicAudience.COACHES)
    api_client.force_authenticate(user=manager)
    resp = api_client.post(
        _messages_url(team.pk, topic.pk),
        {"content": "<p>staff</p>"},
        format="json",
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Message listing / ordering / updated_at bump
# ---------------------------------------------------------------------------


def test_message_list_ordering_oldest_first(api_client, coach, team):
    topic = _make_topic(team, coach, TopicAudience.TEAM)
    m1 = Message.objects.create(topic=topic, author=coach, content="first")
    m2 = Message.objects.create(topic=topic, author=coach, content="second")
    api_client.force_authenticate(user=coach)
    resp = api_client.get(_messages_url(team.pk, topic.pk))
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()["results"]]
    assert ids == [m1.pk, m2.pk]


def test_new_message_bumps_topic_updated_at(api_client, coach, team):
    topic = _make_topic(team, coach, TopicAudience.TEAM)
    before = topic.updated_at
    api_client.force_authenticate(user=coach)
    resp = api_client.post(
        _messages_url(team.pk, topic.pk),
        {"content": "<p>bump</p>"},
        format="json",
    )
    assert resp.status_code == 201
    topic.refresh_from_db()
    assert topic.updated_at > before


def test_topic_message_count(api_client, coach, team):
    topic = _make_topic(team, coach, TopicAudience.TEAM)
    Message.objects.create(topic=topic, author=coach, content="a")
    Message.objects.create(topic=topic, author=coach, content="b")
    api_client.force_authenticate(user=coach)
    resp = api_client.get(_topic_url(team.pk, topic.pk))
    assert resp.status_code == 200
    assert resp.json()["message_count"] == 2


# ---------------------------------------------------------------------------
# Delete permissions
# ---------------------------------------------------------------------------


def test_author_can_delete_own_topic(api_client, coach, manager, team):
    topic = _make_topic(team, manager, TopicAudience.TEAM)
    api_client.force_authenticate(user=manager)
    resp = api_client.delete(_topic_url(team.pk, topic.pk))
    assert resp.status_code == 204
    assert not Topic.objects.filter(pk=topic.pk).exists()


def test_owner_can_delete_any_topic(api_client, coach, manager, team):
    topic = _make_topic(team, manager, TopicAudience.TEAM)
    api_client.force_authenticate(user=coach)  # owner, not author
    resp = api_client.delete(_topic_url(team.pk, topic.pk))
    assert resp.status_code == 204


def test_athlete_cannot_delete_topic(api_client, coach, team, athlete, member):
    topic = _make_topic(team, coach, TopicAudience.TEAM)
    api_client.force_authenticate(user=athlete)
    resp = api_client.delete(_topic_url(team.pk, topic.pk))
    assert resp.status_code == 403
    assert Topic.objects.filter(pk=topic.pk).exists()


def test_author_can_delete_own_message(api_client, coach, team, athlete, member):
    topic = _make_topic(team, coach, TopicAudience.TEAM, allow_replies=True)
    msg = Message.objects.create(topic=topic, author=athlete, content="mine")
    api_client.force_authenticate(user=athlete)
    resp = api_client.delete(_message_url(team.pk, topic.pk, msg.pk))
    assert resp.status_code == 204
    assert not Message.objects.filter(pk=msg.pk).exists()


def test_coach_can_delete_any_message(api_client, coach, team, athlete, member):
    topic = _make_topic(team, coach, TopicAudience.TEAM, allow_replies=True)
    msg = Message.objects.create(topic=topic, author=athlete, content="theirs")
    api_client.force_authenticate(user=coach)
    resp = api_client.delete(_message_url(team.pk, topic.pk, msg.pk))
    assert resp.status_code == 204


def test_athlete_cannot_delete_others_message(api_client, coach, team, athlete, member):
    topic = _make_topic(team, coach, TopicAudience.TEAM, allow_replies=True)
    msg = Message.objects.create(topic=topic, author=coach, content="coach")
    api_client.force_authenticate(user=athlete)
    resp = api_client.delete(_message_url(team.pk, topic.pk, msg.pk))
    assert resp.status_code == 403
    assert Message.objects.filter(pk=msg.pk).exists()


# ---------------------------------------------------------------------------
# Notification triggers
# ---------------------------------------------------------------------------


def test_new_team_topic_notifies_audience_except_author(
    api_client, coach, manager, team, athlete, member
):
    api_client.force_authenticate(user=coach)
    resp = api_client.post(
        _topics_url(team.pk),
        {"title": "Hello", "audience": "team"},
        format="json",
    )
    assert resp.status_code == 201
    notifs = Notification.objects.filter(type=NotificationType.MESSAGE_NEW_TOPIC)
    recipients = set(notifs.values_list("recipient_id", flat=True))
    # athlete + manager notified; author (coach) is not.
    assert athlete.pk in recipients
    assert manager.pk in recipients
    assert coach.pk not in recipients


def test_new_coaches_topic_notifies_only_coaches(
    api_client, coach, manager, team, athlete, member
):
    api_client.force_authenticate(user=coach)
    resp = api_client.post(
        _topics_url(team.pk),
        {"title": "Staff", "audience": "coaches"},
        format="json",
    )
    assert resp.status_code == 201
    notifs = Notification.objects.filter(type=NotificationType.MESSAGE_NEW_TOPIC)
    recipients = set(notifs.values_list("recipient_id", flat=True))
    assert manager.pk in recipients
    assert athlete.pk not in recipients  # athlete never sees coaches topics
    assert coach.pk not in recipients


def test_new_message_notifies_audience_except_sender(
    api_client, coach, manager, team, athlete, member
):
    topic = _make_topic(team, coach, TopicAudience.TEAM, allow_replies=True)
    api_client.force_authenticate(user=athlete)
    resp = api_client.post(
        _messages_url(team.pk, topic.pk),
        {"content": "<p>hi all</p>"},
        format="json",
    )
    assert resp.status_code == 201
    notifs = Notification.objects.filter(type=NotificationType.MESSAGE_NEW_REPLY)
    recipients = set(notifs.values_list("recipient_id", flat=True))
    # coach + manager notified; sender (athlete) is not.
    assert coach.pk in recipients
    assert manager.pk in recipients
    assert athlete.pk not in recipients


def test_message_notification_respects_in_app_pref(api_client, coach, team, athlete, member):
    # Manager disables in-app for MESSAGE_NEW_REPLY -> no in-app row for them.
    NotificationPreference.objects.create(
        user=coach,
        type=NotificationType.MESSAGE_NEW_REPLY,
        in_app=False,
        email=False,
    )
    topic = _make_topic(team, coach, TopicAudience.TEAM, allow_replies=True)
    api_client.force_authenticate(user=athlete)
    resp = api_client.post(
        _messages_url(team.pk, topic.pk),
        {"content": "<p>hi</p>"},
        format="json",
    )
    assert resp.status_code == 201
    assert not Notification.objects.filter(
        recipient=coach, type=NotificationType.MESSAGE_NEW_REPLY
    ).exists()


def test_new_topic_sends_email_to_audience(api_client, coach, team, athlete, member):
    mail.outbox.clear()
    api_client.force_authenticate(user=coach)
    resp = api_client.post(
        _topics_url(team.pk),
        {"title": "Mailed", "audience": "team"},
        format="json",
    )
    assert resp.status_code == 201
    # default prefs -> email sent; athlete has an email address.
    assert any(athlete.email in m.to for m in mail.outbox)
    assert any(f"/teams/{team.pk}" in m.body for m in mail.outbox)


# ---------------------------------------------------------------------------
# Content sanitization
# ---------------------------------------------------------------------------


def test_message_content_sanitized(api_client, coach, team):
    topic = _make_topic(team, coach, TopicAudience.TEAM)
    api_client.force_authenticate(user=coach)
    resp = api_client.post(
        _messages_url(team.pk, topic.pk),
        {"content": "<script>alert(1)</script><p>ok</p>"},
        format="json",
    )
    assert resp.status_code == 201
    assert "<script>" not in resp.json()["content"]
    assert "<p>ok</p>" in resp.json()["content"]


def test_empty_message_rejected(api_client, coach, team):
    topic = _make_topic(team, coach, TopicAudience.TEAM)
    api_client.force_authenticate(user=coach)
    resp = api_client.post(
        _messages_url(team.pk, topic.pk),
        {"content": "   "},
        format="json",
    )
    assert resp.status_code == 400


def test_endpoints_require_authentication(api_client, team):
    resp = api_client.get(_topics_url(team.pk))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Edit message (author only) + edited_at
# ---------------------------------------------------------------------------


def test_author_edits_own_message_sets_edited_at(api_client, coach, team):
    topic = _make_topic(team, coach)
    msg = Message.objects.create(topic=topic, author=coach, content="<p>old</p>")
    api_client.force_authenticate(user=coach)
    resp = api_client.patch(
        _message_url(team.pk, topic.pk, msg.pk),
        {"content": "<p>new</p>"},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert "new" in body["content"]
    assert body["edited_at"] is not None
    msg.refresh_from_db()
    assert msg.edited_at is not None


def test_non_author_cannot_edit_message(api_client, coach, manager, team):
    topic = _make_topic(team, coach)
    msg = Message.objects.create(topic=topic, author=coach, content="<p>old</p>")
    # manager is a coach but NOT the author -> edit forbidden (author-only).
    api_client.force_authenticate(user=manager)
    resp = api_client.patch(
        _message_url(team.pk, topic.pk, msg.pk),
        {"content": "<p>hijack</p>"},
        format="json",
    )
    assert resp.status_code == 403
    msg.refresh_from_db()
    assert "old" in msg.content


# ---------------------------------------------------------------------------
# Read-state: mark-read + unread summary
# ---------------------------------------------------------------------------


def _unread_url():
    return "/api/v1/discussions/unread/"


def test_unread_counts_then_clears_after_read(api_client, coach, athlete, team, member):
    from messaging.models import TopicRead

    topic = _make_topic(team, coach)
    # two messages by the coach -> unread for the athlete
    Message.objects.create(topic=topic, author=coach, content="<p>1</p>")
    Message.objects.create(topic=topic, author=coach, content="<p>2</p>")

    api_client.force_authenticate(user=athlete)
    body = api_client.get(_unread_url()).json()
    assert body["count"] == 2
    assert body["topics"][0]["topic_id"] == topic.pk
    assert body["topics"][0]["unread_count"] == 2

    # mark read -> unread clears
    resp = api_client.post(_topic_url(team.pk, topic.pk) + "read/")
    assert resp.status_code == 204
    assert TopicRead.objects.filter(user=athlete, topic=topic).exists()

    body = api_client.get(_unread_url()).json()
    assert body["count"] == 0
    assert body["topics"] == []


def test_unread_excludes_own_messages(api_client, coach, team):
    topic = _make_topic(team, coach)
    Message.objects.create(topic=topic, author=coach, content="<p>mine</p>")
    api_client.force_authenticate(user=coach)
    body = api_client.get(_unread_url()).json()
    assert body["count"] == 0


def test_unread_after_read_then_new_message(api_client, coach, athlete, team, member):
    topic = _make_topic(team, coach)
    Message.objects.create(topic=topic, author=coach, content="<p>1</p>")
    api_client.force_authenticate(user=athlete)
    api_client.post(_topic_url(team.pk, topic.pk) + "read/")
    assert api_client.get(_unread_url()).json()["count"] == 0
    # a new message after the read marker -> unread again
    Message.objects.create(topic=topic, author=coach, content="<p>2</p>")
    assert api_client.get(_unread_url()).json()["count"] == 1

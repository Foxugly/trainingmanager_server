"""Coverage of the S3 presigned file-attachment feature.

S3 is fully MOCKED — these tests never touch AWS. We patch the helpers in
``attachment.s3`` so presign returns a fake URL and HEAD reports a size; the
bucket is forced "configured" via @override_settings.

Covers presign/complete/download/list/delete on both Event and Message targets,
the permission rules (event-team manager write / team-member read; message
author write / topic reader read), the MIME + size guards, and the 503 path.
"""

from unittest import mock
from uuid import uuid4

import pytest
from django.contrib.contenttypes.models import ContentType

from attachment.models import Attachment
from member.models import Member
from messaging.models import Message, Topic, TopicAudience
from team.models import TeamMembership
from tests.factories import (
    EventFactory,
    ProgramFactory,
    TeamFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def configured_bucket(settings):
    """Force the feature 'configured' for the whole module (overridable per-test)."""
    settings.ATTACHMENTS_S3_BUCKET = "test-bucket"

PRESIGN_URL = "https://test-bucket.s3.amazonaws.com/signed-put"
GET_URL = "https://test-bucket.s3.amazonaws.com/signed-get"

LIST_URL = "/api/v1/attachments/"
PRESIGN_PATH = "/api/v1/attachments/presign/"


def _detail(pk, suffix=""):
    return f"/api/v1/attachments/{pk}/{suffix}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager_user():
    return UserFactory(email="mgr@local.test", language="en")


@pytest.fixture
def athlete_user():
    return UserFactory(email="ath@local.test", language="en")


@pytest.fixture
def outsider_user():
    return UserFactory(email="out@local.test", language="en")


@pytest.fixture
def team(manager_user):
    return TeamFactory(owner=manager_user, is_active=True)


@pytest.fixture
def athlete_member(team, athlete_user):
    m = Member.objects.create(
        firstname="Al", lastname="Ete", email="al@local.test", user=athlete_user
    )
    TeamMembership.objects.create(team=team, member=m)
    return m


@pytest.fixture
def event(team):
    program = ProgramFactory(team=team)
    return EventFactory(refer_program=program)


@pytest.fixture
def topic(team, manager_user):
    return Topic.objects.create(
        team=team, author=manager_user, title="T", audience=TopicAudience.TEAM,
        allow_athlete_replies=True,
    )


@pytest.fixture
def message(topic, manager_user):
    return Message.objects.create(topic=topic, author=manager_user, content="hi")


@pytest.fixture(autouse=True)
def mock_s3():
    """Patch S3 helpers so no AWS call is ever made."""
    with mock.patch("attachment.s3.presign_put", return_value=PRESIGN_URL) as put, mock.patch(
        "attachment.s3.presign_get", return_value=GET_URL
    ) as get, mock.patch(
        "attachment.s3.head_object", return_value={"ContentLength": 4321}
    ) as head, mock.patch(
        "attachment.s3.delete_object", return_value=None
    ) as delete:
        yield {"put": put, "get": get, "head": head, "delete": delete}


def _presign_body(target_type, target_id, **overrides):
    body = {
        "target_type": target_type,
        "target_id": target_id,
        "filename": "plan.pdf",
        "content_type": "application/pdf",
        "size": 1024,
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# presign — event target
# ---------------------------------------------------------------------------


def test_presign_event_as_manager(api_client, manager_user, event):
    api_client.force_authenticate(user=manager_user)
    resp = api_client.post(PRESIGN_PATH, _presign_body("event", event.pk), format="json")
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["upload_url"] == PRESIGN_URL
    assert body["headers"]["Content-Type"] == "application/pdf"
    att = Attachment.objects.get(pk=body["attachment_id"])
    assert att.status == "pending"
    assert att.object_id == event.pk
    assert att.uploaded_by_id == manager_user.pk


def test_presign_disallowed_mime(api_client, manager_user, event):
    api_client.force_authenticate(user=manager_user)
    resp = api_client.post(
        PRESIGN_PATH,
        _presign_body("event", event.pk, content_type="application/x-msdownload"),
        format="json",
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "mime_not_allowed"


def test_presign_too_large(api_client, manager_user, event, settings):
    settings.ATTACHMENTS_MAX_BYTES = 1000
    api_client.force_authenticate(user=manager_user)
    resp = api_client.post(
        PRESIGN_PATH, _presign_body("event", event.pk, size=5000), format="json"
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "file_too_large"


def test_presign_event_as_athlete_forbidden(api_client, athlete_user, event, athlete_member):
    api_client.force_authenticate(user=athlete_user)
    resp = api_client.post(PRESIGN_PATH, _presign_body("event", event.pk), format="json")
    assert resp.status_code == 403
    assert not Attachment.objects.exists()


def test_presign_unconfigured_returns_503(api_client, manager_user, event, settings):
    settings.ATTACHMENTS_S3_BUCKET = ""
    api_client.force_authenticate(user=manager_user)
    resp = api_client.post(PRESIGN_PATH, _presign_body("event", event.pk), format="json")
    assert resp.status_code == 503
    assert resp.json()["code"] == "attachments_unconfigured"


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------


def _make_attachment(event, user, status="pending"):
    return Attachment.objects.create(
        content_type=ContentType.objects.get(app_label="event", model="event"),
        object_id=event.pk,
        s3_key=f"attachments/event/{event.pk}/{uuid4().hex}/plan.pdf",
        filename="plan.pdf",
        content_type_mime="application/pdf",
        size_bytes=1024,
        uploaded_by=user,
        status=status,
    )


def test_complete_sets_ready_and_size(api_client, manager_user, event, mock_s3):
    att = _make_attachment(event, manager_user)
    api_client.force_authenticate(user=manager_user)
    resp = api_client.post(_detail(att.pk, "complete/"))
    assert resp.status_code == 200, resp.content
    att.refresh_from_db()
    assert att.status == "ready"
    assert att.size_bytes == 4321  # from mocked HEAD ContentLength
    mock_s3["head"].assert_called_once()


def test_complete_oversized_real_object_rejected(
    api_client, manager_user, event, mock_s3, settings
):
    """Size-cap bypass: the real S3 object is larger than the cap declared at
    presign. complete must reject, delete the object, and NOT flip to ready."""
    settings.ATTACHMENTS_MAX_BYTES = 1000
    mock_s3["head"].return_value = {"ContentLength": 5000}
    att = _make_attachment(event, manager_user)
    api_client.force_authenticate(user=manager_user)
    resp = api_client.post(_detail(att.pk, "complete/"))
    assert resp.status_code == 400, resp.content
    assert resp.json()["code"] == "file_too_large"
    att.refresh_from_db()
    assert att.status == "pending"  # not flipped to ready
    mock_s3["delete"].assert_called_once()  # best-effort object cleanup attempted


def test_complete_upload_not_found(api_client, manager_user, event, mock_s3):
    mock_s3["head"].return_value = None
    att = _make_attachment(event, manager_user)
    api_client.force_authenticate(user=manager_user)
    resp = api_client.post(_detail(att.pk, "complete/"))
    assert resp.status_code == 400
    assert resp.json()["code"] == "upload_not_found"


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


def test_download_as_team_member(api_client, athlete_user, event, athlete_member, manager_user):
    att = _make_attachment(event, manager_user, status="ready")
    api_client.force_authenticate(user=athlete_user)
    resp = api_client.get(_detail(att.pk, "download/"))
    assert resp.status_code == 200, resp.content
    assert resp.json()["url"] == GET_URL


def test_download_pending_conflicts(api_client, manager_user, event):
    att = _make_attachment(event, manager_user, status="pending")
    api_client.force_authenticate(user=manager_user)
    resp = api_client.get(_detail(att.pk, "download/"))
    assert resp.status_code == 409


def test_download_no_access_forbidden(api_client, outsider_user, event, manager_user):
    att = _make_attachment(event, manager_user, status="ready")
    api_client.force_authenticate(user=outsider_user)
    resp = api_client.get(_detail(att.pk, "download/"))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# list by target
# ---------------------------------------------------------------------------


def test_list_returns_only_ready(api_client, manager_user, event):
    _make_attachment(event, manager_user, status="ready")
    _make_attachment(event, manager_user, status="pending")
    api_client.force_authenticate(user=manager_user)
    resp = api_client.get(LIST_URL, {"target_type": "event", "target_id": event.pk})
    assert resp.status_code == 200, resp.content
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["status"] == "ready"
    assert results[0]["target_type"] == "event"
    assert results[0]["target_id"] == event.pk


def test_list_missing_params(api_client, manager_user, event):
    api_client.force_authenticate(user=manager_user)
    resp = api_client.get(LIST_URL, {"target_type": "event"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "missing_params"


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_as_uploader(api_client, manager_user, event, mock_s3):
    att = _make_attachment(event, manager_user, status="ready")
    api_client.force_authenticate(user=manager_user)
    resp = api_client.delete(_detail(att.pk))
    assert resp.status_code == 204
    mock_s3["delete"].assert_called_once()
    assert not Attachment.objects.filter(pk=att.pk).exists()


# ---------------------------------------------------------------------------
# message target
# ---------------------------------------------------------------------------


def _make_message_attachment(message, user, status="ready"):
    return Attachment.objects.create(
        content_type=ContentType.objects.get(app_label="messaging", model="message"),
        object_id=message.pk,
        s3_key=f"attachments/message/{message.pk}/{uuid4().hex}/plan.pdf",
        filename="plan.pdf",
        content_type_mime="application/pdf",
        size_bytes=1024,
        uploaded_by=user,
        status=status,
    )


def test_message_author_can_presign(api_client, manager_user, message):
    api_client.force_authenticate(user=manager_user)  # author of the message
    resp = api_client.post(PRESIGN_PATH, _presign_body("message", message.pk), format="json")
    assert resp.status_code == 201, resp.content
    att = Attachment.objects.get(pk=resp.json()["attachment_id"])
    assert att.object_id == message.pk


def test_topic_reader_can_download_message_attachment(
    api_client, athlete_user, message, athlete_member, manager_user
):
    # athlete is a team member -> can see the team-audience topic -> can read.
    att = _make_message_attachment(message, manager_user, status="ready")
    api_client.force_authenticate(user=athlete_user)
    resp = api_client.get(_detail(att.pk, "download/"))
    assert resp.status_code == 200, resp.content
    assert resp.json()["url"] == GET_URL


def test_outsider_cannot_presign_message(api_client, outsider_user, message):
    api_client.force_authenticate(user=outsider_user)
    resp = api_client.post(PRESIGN_PATH, _presign_body("message", message.pk), format="json")
    assert resp.status_code == 403


def test_outsider_cannot_download_message_attachment(
    api_client, outsider_user, message, manager_user
):
    att = _make_message_attachment(message, manager_user, status="ready")
    api_client.force_authenticate(user=outsider_user)
    resp = api_client.get(_detail(att.pk, "download/"))
    assert resp.status_code == 403


def test_endpoints_require_auth(api_client, event):
    resp = api_client.get(LIST_URL, {"target_type": "event", "target_id": event.pk})
    assert resp.status_code == 401

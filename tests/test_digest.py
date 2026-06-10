"""Coverage of the daily notification digest (F9):
  - digest_email users get NO immediate notification email (suppressed)
  - send_digests batches the day's notifications into one email
"""

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command

from notifications.models import Notification, NotificationType
from notifications.services import notify

pytestmark = pytest.mark.django_db
User = get_user_model()


def _user(digest, **kw):
    return User.objects.create_user(
        username=kw.get("username", "d_user"),
        email=kw.get("email", "d@local.test"),
        password="p",
        digest_email=digest,
    )


def test_digest_user_gets_no_immediate_email():
    user = _user(True)
    notify(user, NotificationType.PERFORMANCE_LOGGED, title="X", body="b", url="/x")
    # In-app row still created; immediate email suppressed.
    assert Notification.objects.filter(recipient=user).count() == 1
    assert mail.outbox == []


def test_non_digest_user_gets_immediate_email():
    user = _user(False, username="n_user", email="n@local.test")
    notify(user, NotificationType.PERFORMANCE_LOGGED, title="X", body="b", url="/x")
    assert len(mail.outbox) == 1


def test_send_digests_batches_and_advances_marker():
    user = _user(True)
    for i in range(3):
        notify(user, NotificationType.PERFORMANCE_LOGGED, title=f"N{i}", body="b", url=f"/e/{i}")
    assert mail.outbox == []  # suppressed

    call_command("send_digests", stdout=StringIO())
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert "N0" in body and "N2" in body
    user.refresh_from_db()
    assert user.last_digest_at is not None

    # A second run with nothing new sends nothing.
    mail.outbox.clear()
    call_command("send_digests", stdout=StringIO())
    assert mail.outbox == []


def test_send_digests_skips_non_digest_users():
    user = _user(False, username="n2", email="n2@local.test")
    Notification.objects.create(recipient=user, type=NotificationType.PERFORMANCE_LOGGED, title="X")
    call_command("send_digests", stdout=StringIO())
    assert mail.outbox == []


def test_dry_run_sends_nothing_and_keeps_marker():
    user = _user(True)
    notify(user, NotificationType.PERFORMANCE_LOGGED, title="X", url="/x")
    call_command("send_digests", "--dry-run", stdout=StringIO())
    assert mail.outbox == []
    user.refresh_from_db()
    assert user.last_digest_at is None

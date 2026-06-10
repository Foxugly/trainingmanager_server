"""Notification triggers added beyond the note flow.

Covers:
  - performance logged by a COACH notifies the athlete (but NOT on self-log),
  - season plan generation notifies the team's active athletes (once),
  - the daily send_session_reminders management command.
"""

import datetime
from datetime import date, timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from member.models import Member
from notifications.models import Notification, NotificationType
from team.models import TeamMembership
from tests.factories import EventFactory, ProgramFactory, TeamFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


# ---------------------------------------------------------------------------
# B.2 — performance logged
# ---------------------------------------------------------------------------

PERF_URL = "/api/v1/performances/"


@pytest.fixture
def coach_user():
    return User.objects.create_user(
        username="nt_coach", email="nt_coach@local.test", password="pass"
    )


@pytest.fixture
def coach_team(coach_user):
    return TeamFactory(owner=coach_user, is_active=True)


@pytest.fixture
def athlete_user():
    return User.objects.create_user(
        username="nt_athlete", email="nt_athlete@local.test", password="pass"
    )


@pytest.fixture
def athlete_member(athlete_user, coach_team):
    member = Member.objects.create(
        firstname="Ath", lastname="Lete", email=athlete_user.email, user=athlete_user
    )
    TeamMembership.objects.create(team=coach_team, member=member)
    return member


def _perf_payload(team, member, **overrides):
    data = {
        "team": team.pk,
        "member": member.pk,
        "label": "100m freestyle",
        "value": "62.500",
        "unit": "s",
        "recorded_on": "2026-01-15",
        "notes": "",
    }
    data.update(overrides)
    return data


def test_coach_logging_perf_notifies_athlete(
    api_client, coach_user, coach_team, athlete_member, athlete_user
):
    api_client.force_authenticate(user=coach_user)
    resp = api_client.post(PERF_URL, _perf_payload(coach_team, athlete_member), format="json")
    assert resp.status_code == 201, resp.json()
    notif = Notification.objects.filter(
        recipient=athlete_user, type=NotificationType.PERFORMANCE_LOGGED
    )
    assert notif.count() == 1
    # The body mentions the discipline label.
    assert "100m freestyle" in notif.first().body


def test_athlete_self_log_does_not_notify(
    api_client, coach_team, athlete_member, athlete_user
):
    api_client.force_authenticate(user=athlete_user)
    resp = api_client.post(PERF_URL, _perf_payload(coach_team, athlete_member), format="json")
    assert resp.status_code == 201, resp.json()
    assert not Notification.objects.filter(
        type=NotificationType.PERFORMANCE_LOGGED
    ).exists()


def test_coach_logging_for_member_without_user_does_not_crash(
    api_client, coach_user, coach_team
):
    """A member with no linked user simply gets no notification (no error)."""
    member = Member.objects.create(firstname="No", lastname="User")
    TeamMembership.objects.create(team=coach_team, member=member)
    api_client.force_authenticate(user=coach_user)
    resp = api_client.post(PERF_URL, _perf_payload(coach_team, member), format="json")
    assert resp.status_code == 201, resp.json()
    assert not Notification.objects.filter(
        type=NotificationType.PERFORMANCE_LOGGED
    ).exists()


# ---------------------------------------------------------------------------
# B.2b — personal-best beaten (F2)
# ---------------------------------------------------------------------------


def test_first_record_does_not_notify_pb(api_client, coach_user, coach_team, athlete_member):
    api_client.force_authenticate(user=coach_user)
    resp = api_client.post(
        PERF_URL, _perf_payload(coach_team, athlete_member, value="65.000"), format="json"
    )
    assert resp.status_code == 201, resp.json()
    assert not Notification.objects.filter(type=NotificationType.PB_BEATEN).exists()


def test_beating_prior_best_notifies_athlete_pb(
    api_client, coach_user, coach_team, athlete_member, athlete_user
):
    api_client.force_authenticate(user=coach_user)
    api_client.post(
        PERF_URL, _perf_payload(coach_team, athlete_member, value="65.000"), format="json"
    )
    # Seconds = lower is better: a faster time beats the prior best.
    api_client.post(
        PERF_URL, _perf_payload(coach_team, athlete_member, value="62.000"), format="json"
    )
    pb = Notification.objects.filter(recipient=athlete_user, type=NotificationType.PB_BEATEN)
    assert pb.count() == 1
    assert "100m freestyle" in pb.first().body


def test_slower_time_does_not_notify_pb(api_client, coach_user, coach_team, athlete_member):
    api_client.force_authenticate(user=coach_user)
    api_client.post(
        PERF_URL, _perf_payload(coach_team, athlete_member, value="60.000"), format="json"
    )
    api_client.post(
        PERF_URL, _perf_payload(coach_team, athlete_member, value="61.000"), format="json"
    )
    assert not Notification.objects.filter(type=NotificationType.PB_BEATEN).exists()


def test_higher_is_better_pb(api_client, coach_user, coach_team, athlete_member, athlete_user):
    api_client.force_authenticate(user=coach_user)
    base = dict(label="long jump", unit="m")
    api_client.post(
        PERF_URL, _perf_payload(coach_team, athlete_member, value="5.000", **base), format="json"
    )
    api_client.post(
        PERF_URL, _perf_payload(coach_team, athlete_member, value="5.500", **base), format="json"
    )
    assert Notification.objects.filter(
        recipient=athlete_user, type=NotificationType.PB_BEATEN
    ).count() == 1


def test_self_logged_pb_still_notifies(api_client, coach_team, athlete_member, athlete_user):
    """Unlike PERFORMANCE_LOGGED (skipped on self-log), a PB is a milestone the
    athlete is congratulated on even when they logged it themselves."""
    api_client.force_authenticate(user=athlete_user)
    api_client.post(
        PERF_URL, _perf_payload(coach_team, athlete_member, value="65.000"), format="json"
    )
    api_client.post(
        PERF_URL, _perf_payload(coach_team, athlete_member, value="61.000"), format="json"
    )
    assert Notification.objects.filter(
        recipient=athlete_user, type=NotificationType.PB_BEATEN
    ).count() == 1


# ---------------------------------------------------------------------------
# B.3 — plan generated
# ---------------------------------------------------------------------------


def _mock_tool_use_response(events, rationale="Test rationale"):
    response = MagicMock()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "create_training_plan"
    tool_block.input = {"events": events, "rationale": rationale}
    response.content = [tool_block]
    response.model = "claude-haiku-4-5-20251001"
    response.usage.input_tokens = 100
    response.usage.output_tokens = 200
    response.stop_reason = "tool_use"
    return response


def _events_payload(start, count):
    return [
        {
            "name": f"Seance {i + 1}",
            "goal": "Endurance",
            "date": (start + timedelta(days=i * 2)).isoformat(),
            "total_distance": 3000,
            "color": "#3498db",
        }
        for i in range(count)
    ]


def test_plan_generated_notifies_active_athletes(
    api_client, coach_user, coach_team, athlete_member, athlete_user, settings
):
    settings.ANTHROPIC_API_KEY = "sk-ant-fake-test-key"
    # A second active athlete with a linked user.
    other_user = User.objects.create_user(
        username="nt_other", email="nt_other@local.test", password="pass"
    )
    other_member = Member.objects.create(
        firstname="Oth", lastname="Er", user=other_user
    )
    TeamMembership.objects.create(team=coach_team, member=other_member)
    # An athlete with NO linked user — must be skipped.
    nouser = Member.objects.create(firstname="No", lastname="User")
    TeamMembership.objects.create(team=coach_team, member=nouser)
    # An athlete who LEFT — must be skipped.
    left_user = User.objects.create_user(username="nt_left", email="l@local.test", password="p")
    left_member = Member.objects.create(firstname="Le", lastname="Ft", user=left_user)
    TeamMembership.objects.create(
        team=coach_team, member=left_member, left_at=timezone.now()
    )

    program = ProgramFactory(team=coach_team)
    start = date(2026, 5, 1)
    end = date(2026, 5, 14)

    api_client.force_authenticate(user=coach_user)
    with patch("tools.ai.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_tool_use_response(
            events=_events_payload(start, 3)
        )
        resp = api_client.post(
            f"/api/v1/programs/{program.pk}/generate-events/",
            {
                "date_start": start.isoformat(),
                "date_end": end.isoformat(),
                "frequency_per_week": 2,
                "description": "Endurance",
                "overlap_strategy": "add_only",
            },
            format="json",
        )
    assert resp.status_code == 200, resp.json()

    notifs = Notification.objects.filter(type=NotificationType.PLAN_GENERATED)
    recipients = set(notifs.values_list("recipient_id", flat=True))
    # The two active linked athletes are notified; left/no-user are not; the
    # actor (coach) is never notified.
    assert recipients == {athlete_user.pk, other_user.pk}
    assert left_user.pk not in recipients
    assert coach_user.pk not in recipients
    # Body mentions the count and program name.
    assert "3" in notifs.first().body


# ---------------------------------------------------------------------------
# C — send_session_reminders command
# ---------------------------------------------------------------------------


def _team_with_athlete():
    coach = User.objects.create_user(username="sr_coach", email="src@local.test", password="p")
    team = TeamFactory(owner=coach, is_active=True)
    user = User.objects.create_user(username="sr_ath", email="sra@local.test", password="p")
    member = Member.objects.create(firstname="Se", lastname="Ssion", user=user)
    TeamMembership.objects.create(team=team, member=member)
    program = ProgramFactory(team=team)
    return team, user, member, program


def test_reminders_created_for_tomorrows_sessions():
    team, user, member, program = _team_with_athlete()
    tomorrow = timezone.localdate() + datetime.timedelta(days=1)
    EventFactory(refer_program=program, date=tomorrow, name="Morning swim")

    call_command("send_session_reminders", stdout=StringIO())

    notifs = Notification.objects.filter(
        recipient=user, type=NotificationType.SESSION_REMINDER
    )
    assert notifs.count() == 1
    assert "Morning swim" in notifs.first().body


def test_no_reminders_for_other_days():
    team, user, member, program = _team_with_athlete()
    today = timezone.localdate()
    EventFactory(refer_program=program, date=today, name="Today")
    EventFactory(
        refer_program=program,
        date=today + datetime.timedelta(days=3),
        name="Later",
    )

    call_command("send_session_reminders", stdout=StringIO())

    assert not Notification.objects.filter(
        type=NotificationType.SESSION_REMINDER
    ).exists()


def test_duplicate_active_membership_rejected():
    """Reminders can't be duplicated by redundant membership rows because the
    partial unique constraint forbids a second ACTIVE (team, member) row.
    (Previously the reminder command deduped this at runtime; it's now a DB
    invariant.)"""
    from django.db import IntegrityError, transaction

    team, user, member, program = _team_with_athlete()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TeamMembership.objects.create(team=team, member=member)


def test_reminder_skips_member_without_user():
    coach = User.objects.create_user(username="sr_c2", email="c2@local.test", password="p")
    team = TeamFactory(owner=coach, is_active=True)
    member = Member.objects.create(firstname="No", lastname="User")
    TeamMembership.objects.create(team=team, member=member)
    program = ProgramFactory(team=team)
    tomorrow = timezone.localdate() + datetime.timedelta(days=1)
    EventFactory(refer_program=program, date=tomorrow, name="X")

    call_command("send_session_reminders", stdout=StringIO())

    assert not Notification.objects.filter(
        type=NotificationType.SESSION_REMINDER
    ).exists()


def test_reminder_dry_run_creates_nothing():
    team, user, member, program = _team_with_athlete()
    tomorrow = timezone.localdate() + datetime.timedelta(days=1)
    EventFactory(refer_program=program, date=tomorrow, name="Dry")

    call_command("send_session_reminders", "--dry-run", stdout=StringIO())

    assert not Notification.objects.filter(
        type=NotificationType.SESSION_REMINDER
    ).exists()


def test_reminder_email_has_rsvp_links_when_enabled():
    from django.core import mail

    team, user, member, program = _team_with_athlete()
    team.rsvp_enabled = True
    team.save(update_fields=["rsvp_enabled"])
    tomorrow = timezone.localdate() + datetime.timedelta(days=1)
    event = EventFactory(refer_program=program, date=tomorrow, name="RSVP swim")

    call_command("send_session_reminders", stdout=StringIO())

    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert "/api/v1/rsvp-magic/" in body
    # One link per status (going / maybe / not_going).
    assert body.count("/api/v1/rsvp-magic/") == 3
    # The per-recipient link must NOT leak into the stored in-app row.
    notif = Notification.objects.get(
        recipient=user, type=NotificationType.SESSION_REMINDER
    )
    assert "rsvp-magic" not in notif.body
    # Each token is member-scoped to this athlete + event (re-signing yields a
    # different timestamp, so parse the emitted token rather than compare it).
    import re

    from rsvp.magic_rsvp import parse_token

    tokens = re.findall(r"/api/v1/rsvp-magic/([^/\s]+)/", body)
    parsed = {parse_token(t) for t in tokens}
    assert parsed == {
        (event.pk, member.pk, "going"),
        (event.pk, member.pk, "maybe"),
        (event.pk, member.pk, "not_going"),
    }


def test_reminder_email_no_rsvp_links_when_disabled():
    from django.core import mail

    team, user, member, program = _team_with_athlete()  # rsvp_enabled defaults False
    tomorrow = timezone.localdate() + datetime.timedelta(days=1)
    EventFactory(refer_program=program, date=tomorrow, name="No RSVP")

    call_command("send_session_reminders", stdout=StringIO())

    assert len(mail.outbox) == 1
    assert "rsvp-magic" not in mail.outbox[0].body

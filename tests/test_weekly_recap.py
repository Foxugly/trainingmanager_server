"""Coverage of the weekly recap email (Feature 2).

Covers:
  - the send_weekly_recaps management command: emails opted-in coaches of
    enabled teams, skips disabled teams, skips opted-out users, aggregates a
    multi-team coach into ONE email, --dry-run sends nothing;
  - the last_completed_week window helper;
  - the MeSerializer weekly_recap_opt_in round-trip (GET/PATCH /me/).

Email is exercised via the locmem backend (mail.outbox), installed by the
autouse conftest fixture.
"""

import datetime

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command

from attendance.models import Attendance, AttendanceStatus
from customuser.management.commands.send_weekly_recaps import last_completed_week
from event.models import Event
from member.models import Member
from team.models import TeamMembership
from tests.factories import ProgramFactory, TeamFactory, UserFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


# Deterministic window: a Monday..Sunday block firmly in the past.
WEEK_FROM = datetime.date(2024, 1, 1)  # a Monday
WEEK_TO = datetime.date(2024, 1, 7)  # the following Sunday


def _run(**kwargs):
    call_command(
        "send_weekly_recaps",
        f"--from={WEEK_FROM.isoformat()}",
        f"--to={WEEK_TO.isoformat()}",
        **kwargs,
    )


@pytest.fixture
def present_status(db):
    return AttendanceStatus.objects.get(code="present")


def _make_member(team, firstname="A", lastname="B"):
    member = Member.objects.create(
        firstname=firstname, lastname=lastname, email=f"{firstname}@x.test"
    )
    TeamMembership.objects.create(team=team, member=member)
    return member


# =====================================================================
# Window helper
# =====================================================================


def test_last_completed_week_is_previous_monday_to_sunday():
    # Wednesday 2024-01-10 -> last completed week is 2024-01-01..2024-01-07.
    monday, sunday = last_completed_week(datetime.date(2024, 1, 10))
    assert monday == datetime.date(2024, 1, 1)
    assert sunday == datetime.date(2024, 1, 7)


def test_last_completed_week_on_a_monday():
    # On Monday 2024-01-08, the last completed week is the one that just ended.
    monday, sunday = last_completed_week(datetime.date(2024, 1, 8))
    assert monday == datetime.date(2024, 1, 1)
    assert sunday == datetime.date(2024, 1, 7)


def test_week_offset_shifts_further_back():
    monday, sunday = last_completed_week(datetime.date(2024, 1, 10), offset=1)
    assert monday == datetime.date(2023, 12, 25)
    assert sunday == datetime.date(2023, 12, 31)


# =====================================================================
# Command — sending
# =====================================================================


def test_emails_opted_in_owner_of_enabled_team(present_status):
    owner = UserFactory(email="owner@local.test", weekly_recap_opt_in=True)
    team = TeamFactory(owner=owner, is_active=True, weekly_recap_enabled=True)
    program = ProgramFactory(team=team)
    member = _make_member(team)
    ev = Event.objects.create(
        refer_program=program, name="sess", date=WEEK_FROM + datetime.timedelta(days=1), total=300
    )
    Attendance.objects.create(event=ev, member=member, status=present_status)

    _run()

    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == ["owner@local.test"]
    assert team.name in msg.body
    assert "300" in msg.body  # total volume


def test_idempotent_no_double_send_on_same_week_rerun(present_status):
    """Re-running the command for the same week must not re-email a recipient
    already marked sent (WeeklyRecapLog)."""
    owner = UserFactory(email="owner@local.test", weekly_recap_opt_in=True)
    team = TeamFactory(owner=owner, is_active=True, weekly_recap_enabled=True)
    program = ProgramFactory(team=team)
    Event.objects.create(
        refer_program=program, name="sess", date=WEEK_FROM + datetime.timedelta(days=1), total=300
    )

    _run()
    assert len(mail.outbox) == 1

    _run()  # same window again
    assert len(mail.outbox) == 1  # still one — second run skipped


def test_dry_run_does_not_mark_sent(present_status):
    """--dry-run must not write the idempotency marker, so a real run after a
    dry-run still sends."""
    owner = UserFactory(email="owner@local.test", weekly_recap_opt_in=True)
    team = TeamFactory(owner=owner, is_active=True, weekly_recap_enabled=True)
    program = ProgramFactory(team=team)
    Event.objects.create(
        refer_program=program, name="sess", date=WEEK_FROM + datetime.timedelta(days=1), total=300
    )

    _run(**{"dry_run": True})
    assert mail.outbox == []
    _run()
    assert len(mail.outbox) == 1


def test_skips_disabled_team():
    owner = UserFactory(email="owner@local.test", weekly_recap_opt_in=True)
    TeamFactory(owner=owner, is_active=True, weekly_recap_enabled=False)
    _run()
    assert mail.outbox == []


def test_skips_opted_out_user():
    owner = UserFactory(email="owner@local.test", weekly_recap_opt_in=False)
    TeamFactory(owner=owner, is_active=True, weekly_recap_enabled=True)
    _run()
    assert mail.outbox == []


def test_skips_user_without_email():
    owner = UserFactory(email="", weekly_recap_opt_in=True)
    TeamFactory(owner=owner, is_active=True, weekly_recap_enabled=True)
    _run()
    assert mail.outbox == []


def test_manager_also_receives():
    owner = UserFactory(email="owner@local.test", weekly_recap_opt_in=True)
    mgr = UserFactory(email="mgr@local.test", weekly_recap_opt_in=True)
    team = TeamFactory(owner=owner, is_active=True, weekly_recap_enabled=True)
    team.managers.add(mgr)
    _run()
    recipients = sorted(addr for msg in mail.outbox for addr in msg.to)
    assert recipients == ["mgr@local.test", "owner@local.test"]


def test_aggregates_multi_team_coach_into_one_email():
    """A coach owning two enabled teams gets ONE email listing both."""
    coach = UserFactory(email="coach@local.test", weekly_recap_opt_in=True)
    team_a = TeamFactory(owner=coach, is_active=True, weekly_recap_enabled=True)
    team_b = TeamFactory(owner=coach, is_active=True, weekly_recap_enabled=True)
    _run()
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert team_a.name in body
    assert team_b.name in body


def test_owner_who_is_also_manager_emailed_once():
    """Owner listed as a manager too -> still a single email, both teams listed."""
    coach = UserFactory(email="coach@local.test", weekly_recap_opt_in=True)
    team = TeamFactory(owner=coach, is_active=True, weekly_recap_enabled=True)
    team.managers.add(coach)
    _run()
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["coach@local.test"]


def test_dry_run_sends_nothing():
    owner = UserFactory(email="owner@local.test", weekly_recap_opt_in=True)
    TeamFactory(owner=owner, is_active=True, weekly_recap_enabled=True)
    _run(dry_run=True)
    assert mail.outbox == []


def test_recipient_language_is_used(monkeypatch):
    """Each recipient's recap is rendered in their own language."""
    from django.utils import translation as dj_translation

    owner = UserFactory(email="fr@local.test", language="fr", weekly_recap_opt_in=True)
    TeamFactory(owner=owner, is_active=True, weekly_recap_enabled=True)

    calls = []
    real_override = dj_translation.override

    def _spy(lang, *a, **k):
        calls.append(lang)
        return real_override(lang, *a, **k)

    monkeypatch.setattr(
        "customuser.management.commands.send_weekly_recaps.translation.override",
        _spy,
    )
    _run()
    assert "fr" in calls


def test_no_crash_on_empty_data():
    """Enabled team with no events/members must not crash; attendance n/a."""
    owner = UserFactory(email="owner@local.test", weekly_recap_opt_in=True)
    TeamFactory(owner=owner, is_active=True, weekly_recap_enabled=True)
    _run()
    assert len(mail.outbox) == 1


# =====================================================================
# MeSerializer — weekly_recap_opt_in is user read+write
# =====================================================================


def test_me_returns_weekly_recap_opt_in(api_client):
    user = UserFactory(weekly_recap_opt_in=True)
    api_client.force_authenticate(user=user)
    resp = api_client.get("/api/v1/me/")
    assert resp.status_code == 200
    assert resp.json()["weekly_recap_opt_in"] is True


def test_me_patch_can_set_weekly_recap_opt_in(api_client):
    user = UserFactory(weekly_recap_opt_in=True)
    api_client.force_authenticate(user=user)
    resp = api_client.patch(
        "/api/v1/me/", {"weekly_recap_opt_in": False}, format="json"
    )
    assert resp.status_code == 200
    assert resp.json()["weekly_recap_opt_in"] is False
    user.refresh_from_db()
    assert user.weekly_recap_opt_in is False

"""Audit log of sensitive actions + coach-scoped read API.

Covers:
  - The record() service: writes an entry with the right fields + actor_label
    snapshot, and never raises on a bad arg (returns None, logs).
  - Wiring: member anonymize -> MEMBER_ANONYMIZED; session share/unshare ->
    SESSION_SHARED then SESSION_UNSHARED.
  - GET /api/v1/audit-log/ scoping: a manager sees their team's entries; a
    non-manager sees none; anonymous is rejected; ?team= for a team you don't
    manage is forbidden.
"""

import pytest
from django.utils import timezone

from audit.models import AuditAction, AuditLogEntry
from audit.services import record
from member.models import Member
from team.models import TeamMembership
from tests.factories import ProgramFactory, TeamFactory, UserFactory

pytestmark = pytest.mark.django_db


AUDIT_URL = "/api/v1/audit-log/"


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def manager():
    return UserFactory(username="audit_manager")


@pytest.fixture
def team(manager):
    return TeamFactory(owner=manager, is_active=True, public_sharing_enabled=True)


@pytest.fixture
def manager_client(api_client, manager):
    api_client.force_authenticate(user=manager)
    return api_client


@pytest.fixture
def outsider():
    return UserFactory(username="audit_outsider")


@pytest.fixture
def outsider_client(api_client, outsider):
    api_client.force_authenticate(user=outsider)
    return api_client


@pytest.fixture
def athlete_user():
    return UserFactory(username="audit_athlete")


@pytest.fixture
def member(team, athlete_user):
    m = Member.objects.create(
        firstname="Alice",
        lastname="Martin",
        email="alice.martin@local.test",
        phonenumber="+32411111111",
        user=athlete_user,
    )
    TeamMembership.objects.create(team=team, member=m, joined_at=timezone.now())
    return m


# --------------------------------------------------------------------------
# 1. service
# --------------------------------------------------------------------------


def test_record_creates_entry_with_snapshot(manager, team):
    entry = record(
        AuditAction.MEMBER_REMOVED,
        actor=manager,
        team=team,
        target_repr="Member #1 (Alice Martin)",
        metadata={"reason": "left"},
    )
    assert entry is not None
    assert entry.pk is not None
    assert entry.action == "member_removed"
    assert entry.actor_id == manager.pk
    assert entry.actor_label == "audit_manager"  # snapshot of username
    assert entry.team_id == team.pk
    assert entry.target_repr == "Member #1 (Alice Martin)"
    assert entry.metadata == {"reason": "left"}
    assert entry.created_at is not None


def test_record_with_no_actor_has_blank_label(team):
    entry = record(AuditAction.TEAM_CONFIG_UPDATED, actor=None, team=team)
    assert entry is not None
    assert entry.actor_id is None
    assert entry.actor_label == ""


def test_record_never_raises_on_bad_arg(team):
    # Non-JSON-serializable metadata fails at DB serialization time. record()
    # must swallow that and return None rather than propagate the error to the
    # caller (auditing must never break the audited action).
    #
    # record() wraps its write in an inner savepoint, so the failure must not
    # propagate AND must leave the caller's transaction usable.
    before = AuditLogEntry.objects.count()
    try:
        result = record(
            AuditAction.MEMBER_REMOVED,
            actor=None,
            team=team,
            metadata={"bad": object()},
        )
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"record() propagated an exception: {exc!r}")

    assert result is None
    # The surrounding transaction is still usable (no poisoning) and nothing
    # was committed.
    assert AuditLogEntry.objects.count() == before


# --------------------------------------------------------------------------
# 2. member anonymize wiring
# --------------------------------------------------------------------------


def test_anonymize_records_audit_entry(manager_client, manager, team, member):
    resp = manager_client.post(f"/api/v1/members/{member.id}/anonymize/", format="json")
    assert resp.status_code == 200, resp.json()

    entries = AuditLogEntry.objects.filter(action=AuditAction.MEMBER_ANONYMIZED)
    assert entries.count() == 1
    entry = entries.first()
    assert entry.team_id == team.pk
    assert entry.actor_id == manager.pk
    assert entry.target_repr == f"Member #{member.id}"


# --------------------------------------------------------------------------
# 3. session share toggle wiring
# --------------------------------------------------------------------------


def _make_event(team):
    from event.models import Event

    program = ProgramFactory(team=team)
    return Event.objects.create(name="Audit Sess", refer_program=program)


def test_session_share_toggle_records_both(manager_client, manager, team):
    event = _make_event(team)
    url = f"/api/v1/events/{event.id}/share/"

    on = manager_client.post(url, {"is_public": True}, format="json")
    assert on.status_code == 200, on.json()
    off = manager_client.post(url, {"is_public": False}, format="json")
    assert off.status_code == 200, off.json()

    actions = list(
        AuditLogEntry.objects.filter(team=team, target_repr=f"Event #{event.id}")
        .order_by("created_at")
        .values_list("action", flat=True)
    )
    assert actions == [AuditAction.SESSION_SHARED, AuditAction.SESSION_UNSHARED]


# --------------------------------------------------------------------------
# 4. read endpoint scoping
# --------------------------------------------------------------------------


def test_manager_sees_their_team_entries(manager_client, manager, team):
    record(AuditAction.MEMBER_REMOVED, actor=manager, team=team, target_repr="Member #9")
    resp = manager_client.get(AUDIT_URL)
    assert resp.status_code == 200
    data = resp.json()
    results = data["results"] if isinstance(data, dict) else data
    assert len(results) == 1
    row = results[0]
    assert row["action"] == "member_removed"
    assert row["action_display"]  # human label present
    assert row["team"] == team.pk
    assert row["actor"] == manager.pk
    assert row["actor_label"] == "audit_manager"


def test_outsider_sees_no_entries(outsider_client, manager, team):
    record(AuditAction.MEMBER_REMOVED, actor=manager, team=team, target_repr="Member #9")
    resp = outsider_client.get(AUDIT_URL)
    assert resp.status_code == 200
    data = resp.json()
    results = data["results"] if isinstance(data, dict) else data
    assert results == []


def test_anonymous_is_rejected(api_client, manager, team):
    record(AuditAction.MEMBER_REMOVED, actor=manager, team=team)
    resp = api_client.get(AUDIT_URL)
    assert resp.status_code in (401, 403)


def test_team_filter_for_unmanaged_team_forbidden(outsider_client, team):
    resp = outsider_client.get(AUDIT_URL, {"team": team.pk})
    assert resp.status_code == 403


def test_team_filter_for_managed_team_ok(manager_client, manager, team):
    other_team = TeamFactory(owner=manager, is_active=True)
    record(AuditAction.MEMBER_REMOVED, actor=manager, team=team, target_repr="A")
    record(AuditAction.MEMBER_REMOVED, actor=manager, team=other_team, target_repr="B")

    resp = manager_client.get(AUDIT_URL, {"team": team.pk})
    assert resp.status_code == 200
    data = resp.json()
    results = data["results"] if isinstance(data, dict) else data
    assert len(results) == 1
    assert results[0]["team"] == team.pk

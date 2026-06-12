"""Coverage of /api/v1/events/{event_pk}/attendance/.

Permissions:
  - Coach (owner / managers of the event's team): full CRUD
  - Athlete (member.user == request.user, active membership): read OWN only
  - Other: 403

Bulk:
  - POST /api/v1/events/{event_pk}/attendance/bulk/
  - update_or_create per item, all-or-nothing atomic
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from attendance.models import Attendance, AttendanceStatus
from event.models import Event
from member.models import Member
from team.models import TeamMembership
from tests.factories import ProgramFactory, TeamFactory

pytestmark = pytest.mark.django_db


User = get_user_model()


@pytest.fixture
def coach_user(db):
    return User.objects.create_user(
        email="att_coach@local.test", password="pass"
    )


@pytest.fixture
def coach_team(coach_user):
    return TeamFactory(owner=coach_user, is_active=True)


@pytest.fixture
def coach_program(coach_team):
    return ProgramFactory(team=coach_team)


@pytest.fixture
def future_event(coach_program):
    return Event.objects.create(
        refer_program=coach_program,
        name="Test event",
        date=timezone.localdate() + timedelta(days=3),
    )


@pytest.fixture
def athlete_user(db):
    return User.objects.create_user(
        email="att_athlete@local.test", password="pass"
    )


@pytest.fixture
def athlete_member(athlete_user, coach_team):
    member = Member.objects.create(
        firstname="A", lastname="Th", email=athlete_user.email, user=athlete_user
    )
    TeamMembership.objects.create(team=coach_team, member=member)
    return member


@pytest.fixture
def coach_client(api_client, coach_user):
    api_client.force_authenticate(user=coach_user)
    return api_client


@pytest.fixture
def athlete_client(api_client, athlete_user):
    api_client.force_authenticate(user=athlete_user)
    return api_client


@pytest.fixture
def random_client(api_client, db):
    user = User.objects.create_user(
        email="att_rando@local.test", password="pass"
    )
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def present_status(db):
    return AttendanceStatus.objects.get(code="present")


@pytest.fixture
def absent_status(db):
    return AttendanceStatus.objects.get(code="absent")


def _url(event_pk, att_id=None):
    base = f"/api/v1/events/{event_pk}/attendance/"
    return base if att_id is None else f"{base}{att_id}/"


def _bulk_url(event_pk):
    return f"/api/v1/events/{event_pk}/attendance/bulk/"


# =====================================================================
# READ permissions
# =====================================================================


def test_owner_sees_all_attendances_for_event(
    coach_client, future_event, athlete_member, present_status
):
    other = Member.objects.create(firstname="O", lastname="X", email="ox@local.test")
    TeamMembership.objects.create(team=future_event.refer_program.team, member=other)
    Attendance.objects.create(event=future_event, member=athlete_member, status=present_status)
    Attendance.objects.create(event=future_event, member=other, status=present_status)

    response = coach_client.get(_url(future_event.pk))
    assert response.status_code == 200
    body = response.json()["results"]
    assert len(body) == 2


def test_athlete_sees_only_own_attendance(
    athlete_client, future_event, athlete_member, present_status
):
    other = Member.objects.create(firstname="O", lastname="X", email="ox2@local.test")
    TeamMembership.objects.create(team=future_event.refer_program.team, member=other)
    Attendance.objects.create(event=future_event, member=athlete_member, status=present_status)
    Attendance.objects.create(event=future_event, member=other, status=present_status)

    response = athlete_client.get(_url(future_event.pk))
    assert response.status_code == 200
    body = response.json()["results"]
    assert len(body) == 1
    assert body[0]["member"] == athlete_member.pk


def test_random_user_returns_403(random_client, future_event):
    response = random_client.get(_url(future_event.pk))
    assert response.status_code == 403


# =====================================================================
# WRITE permissions
# =====================================================================


def test_owner_can_create_attendance(coach_client, future_event, athlete_member, present_status):
    response = coach_client.post(
        _url(future_event.pk),
        {"member": athlete_member.pk, "status": present_status.pk},
        format="json",
    )
    assert response.status_code == 201, response.json()
    assert Attendance.objects.filter(event=future_event, member=athlete_member).exists()


def test_athlete_cannot_create_attendance(
    athlete_client, future_event, athlete_member, present_status
):
    response = athlete_client.post(
        _url(future_event.pk),
        {"member": athlete_member.pk, "status": present_status.pk},
        format="json",
    )
    assert response.status_code == 403


def test_create_for_member_not_in_team_returns_400(coach_client, future_event, present_status):
    other = Member.objects.create(firstname="N", lastname="O", email="no@local.test")
    response = coach_client.post(
        _url(future_event.pk),
        {"member": other.pk, "status": present_status.pk},
        format="json",
    )
    assert response.status_code == 400


def test_create_duplicate_returns_400(coach_client, future_event, athlete_member, present_status):
    Attendance.objects.create(event=future_event, member=athlete_member, status=present_status)
    response = coach_client.post(
        _url(future_event.pk),
        {"member": athlete_member.pk, "status": present_status.pk},
        format="json",
    )
    assert response.status_code == 400


def test_owner_can_patch_status(
    coach_client, future_event, athlete_member, present_status, absent_status
):
    att = Attendance.objects.create(
        event=future_event, member=athlete_member, status=present_status
    )
    response = coach_client.patch(
        _url(future_event.pk, att.pk),
        {"status": absent_status.pk},
        format="json",
    )
    assert response.status_code == 200
    att.refresh_from_db()
    assert att.status_id == absent_status.pk


def test_athlete_cannot_patch_status(
    athlete_client, future_event, athlete_member, present_status, absent_status
):
    att = Attendance.objects.create(
        event=future_event, member=athlete_member, status=present_status
    )
    response = athlete_client.patch(
        _url(future_event.pk, att.pk),
        {"status": absent_status.pk},
        format="json",
    )
    assert response.status_code == 403
    att.refresh_from_db()
    assert att.status_id == present_status.pk


def test_owner_can_delete(coach_client, future_event, athlete_member, present_status):
    att = Attendance.objects.create(
        event=future_event, member=athlete_member, status=present_status
    )
    response = coach_client.delete(_url(future_event.pk, att.pk))
    assert response.status_code == 204
    assert not Attendance.objects.filter(pk=att.pk).exists()


# =====================================================================
# C3 — member is read-only after create (no cross-team repointing)
# =====================================================================


def test_PATCH_attendance_cannot_repoint_member_to_other_team(
    coach_client, future_event, athlete_member, present_status, absent_status
):
    """C3 fix: a coach must not be able to PATCH `member` to a Member of
    another team. The pre-fix viewset only validated team-membership in
    perform_create — a PATCH could swap `member` to any Member id without
    re-validation, breaking the team-scoped boundary."""
    att = Attendance.objects.create(
        event=future_event, member=athlete_member, status=present_status
    )

    # Member that belongs to a completely separate team.
    other_team = TeamFactory()
    foreign_member = Member.objects.create(
        firstname="For", lastname="Eign", email="foreign@local.test"
    )
    TeamMembership.objects.create(team=other_team, member=foreign_member)

    response = coach_client.patch(
        _url(future_event.pk, att.pk),
        {"member": foreign_member.pk, "status": absent_status.pk},
        format="json",
    )
    # Silent ignore on member (read-only post-create); status still applies.
    assert response.status_code == 200, response.json()
    att.refresh_from_db()
    assert att.member_id == athlete_member.pk
    assert att.status_id == absent_status.pk


def test_PATCH_attendance_cannot_repoint_member_even_within_same_team(
    coach_client, future_event, athlete_member, present_status
):
    """member is read-only post-create regardless of legitimacy. An
    Attendance row represents 'this specific person was at this event
    with this status' — not a reassignable slot. To reassign, delete and
    re-create."""
    att = Attendance.objects.create(
        event=future_event, member=athlete_member, status=present_status
    )

    # Another legit member of the SAME team.
    team_mate = Member.objects.create(firstname="T", lastname="M", email="tm@local.test")
    TeamMembership.objects.create(team=future_event.refer_program.team, member=team_mate)

    response = coach_client.patch(
        _url(future_event.pk, att.pk),
        {"member": team_mate.pk},
        format="json",
    )
    assert response.status_code == 200
    att.refresh_from_db()
    assert att.member_id == athlete_member.pk


def test_PATCH_attendance_status_only_still_works(
    coach_client, future_event, athlete_member, present_status, absent_status
):
    """Non-regression: PATCH with status only behaves as before (already
    covered by test_owner_can_patch_status above; this duplicates the
    intent in the C3 cluster for clarity)."""
    att = Attendance.objects.create(
        event=future_event, member=athlete_member, status=present_status
    )
    response = coach_client.patch(
        _url(future_event.pk, att.pk),
        {"status": absent_status.pk},
        format="json",
    )
    assert response.status_code == 200
    att.refresh_from_db()
    assert att.status_id == absent_status.pk
    assert att.member_id == athlete_member.pk


# =====================================================================
# BULK
# =====================================================================


def test_bulk_set_creates_new_rows(coach_client, future_event, athlete_member):
    other = Member.objects.create(firstname="B", lastname="Ulk", email="bulk@local.test")
    TeamMembership.objects.create(team=future_event.refer_program.team, member=other)

    response = coach_client.post(
        _bulk_url(future_event.pk),
        {
            "attendances": [
                {"member_id": athlete_member.pk, "status_code": "present"},
                {"member_id": other.pk, "status_code": "absent"},
            ]
        },
        format="json",
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["created_count"] == 2
    assert body["updated_count"] == 0
    assert len(body["attendances"]) == 2


def test_bulk_set_updates_existing_rows(coach_client, future_event, athlete_member, present_status):
    Attendance.objects.create(event=future_event, member=athlete_member, status=present_status)
    response = coach_client.post(
        _bulk_url(future_event.pk),
        {"attendances": [{"member_id": athlete_member.pk, "status_code": "absent"}]},
        format="json",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 0
    assert body["updated_count"] == 1
    att = Attendance.objects.get(event=future_event, member=athlete_member)
    assert att.status.code == "absent"


def test_bulk_validates_all_members_in_team(coach_client, future_event, athlete_member):
    outsider = Member.objects.create(firstname="Out", lastname="Side", email="out@local.test")
    response = coach_client.post(
        _bulk_url(future_event.pk),
        {
            "attendances": [
                {"member_id": athlete_member.pk, "status_code": "present"},
                {"member_id": outsider.pk, "status_code": "present"},
            ]
        },
        format="json",
    )
    assert response.status_code == 400
    # No partial create
    assert not Attendance.objects.filter(event=future_event).exists()


def test_bulk_validates_status_codes(coach_client, future_event, athlete_member):
    response = coach_client.post(
        _bulk_url(future_event.pk),
        {"attendances": [{"member_id": athlete_member.pk, "status_code": "ghost"}]},
        format="json",
    )
    assert response.status_code == 400


def test_bulk_rejects_oversized_payload(coach_client, future_event):
    """An over-cap payload is rejected before opening the write transaction."""
    from attendance.serializers import MAX_BULK_ATTENDANCES

    items = [
        {"member_id": i, "status_code": "present"}
        for i in range(1, MAX_BULK_ATTENDANCES + 2)
    ]
    response = coach_client.post(
        _bulk_url(future_event.pk), {"attendances": items}, format="json"
    )
    assert response.status_code == 400, response.status_code
    assert not Attendance.objects.filter(event=future_event).exists()
    assert not Attendance.objects.filter(event=future_event).exists()


def test_bulk_atomic_no_partial_creates_on_error(coach_client, future_event, athlete_member):
    """One invalid item rolls back the whole batch."""
    response = coach_client.post(
        _bulk_url(future_event.pk),
        {
            "attendances": [
                {"member_id": athlete_member.pk, "status_code": "present"},
                {"member_id": athlete_member.pk, "status_code": "present"},
            ]
        },
        format="json",
    )
    # Duplicate member_id rejected by serializer
    assert response.status_code == 400
    assert not Attendance.objects.filter(event=future_event).exists()


def test_bulk_as_athlete_returns_403(athlete_client, future_event, athlete_member):
    response = athlete_client.post(
        _bulk_url(future_event.pk),
        {"attendances": [{"member_id": athlete_member.pk, "status_code": "present"}]},
        format="json",
    )
    assert response.status_code == 403

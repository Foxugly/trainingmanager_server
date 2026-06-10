"""Coverage of /api/v1/events/{event_pk}/rsvp/.

RSVP = per-athlete availability (going/maybe/not_going), gated by the
team's rsvp_enabled toggle. Mirrors the roti test layout.

Surface:
  - GET  /api/v1/events/{event_pk}/rsvp/                       -> summary (incl. my_status)
  - PUT  /api/v1/events/{event_pk}/rsvp/                       -> upsert caller's own status
  - POST /api/v1/events/{event_pk}/rsvp/apply_to_attendance/  -> manager pre-fill attendance
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from attendance.models import Attendance, AttendanceStatus
from event.models import Event
from member.models import Member
from rsvp.models import Rsvp
from team.models import TeamMembership
from tests.factories import ProgramFactory, TeamFactory

pytestmark = pytest.mark.django_db


User = get_user_model()


@pytest.fixture
def coach_user(db):
    return User.objects.create_user(
        username="rsvp_coach", email="rsvp_coach@local.test", password="pass"
    )


@pytest.fixture
def coach_team(coach_user):
    return TeamFactory(owner=coach_user, is_active=True, rsvp_enabled=True)


@pytest.fixture
def coach_program(coach_team):
    return ProgramFactory(team=coach_team)


@pytest.fixture
def future_event(coach_program):
    return Event.objects.create(
        refer_program=coach_program,
        name="RSVP event",
        date=timezone.localdate() + timedelta(days=3),
    )


@pytest.fixture
def athlete_user(db):
    return User.objects.create_user(
        username="rsvp_athlete", email="rsvp_athlete@local.test", password="pass"
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
        username="rsvp_rando", email="rsvp_rando@local.test", password="pass"
    )
    api_client.force_authenticate(user=user)
    return api_client


def _url(event_pk):
    return f"/api/v1/events/{event_pk}/rsvp/"


def _apply_url(event_pk):
    return f"/api/v1/events/{event_pk}/rsvp/apply_to_attendance/"


# =====================================================================
# Athlete upserts own status
# =====================================================================


@pytest.mark.parametrize("value", ["going", "maybe", "not_going"])
def test_athlete_submits_status_persisted(athlete_client, future_event, athlete_member, value):
    response = athlete_client.put(_url(future_event.pk), {"status": value}, format="json")
    assert response.status_code == 200, response.json()
    rsvp = Rsvp.objects.get(event=future_event, member=athlete_member)
    assert rsvp.status == value
    assert response.json()["my_status"] == value


def test_athlete_resubmit_updates_no_duplicate(athlete_client, future_event, athlete_member):
    athlete_client.put(_url(future_event.pk), {"status": "maybe"}, format="json")
    response = athlete_client.put(_url(future_event.pk), {"status": "going"}, format="json")
    assert response.status_code == 200
    assert Rsvp.objects.filter(event=future_event, member=athlete_member).count() == 1
    rsvp = Rsvp.objects.get(event=future_event, member=athlete_member)
    assert rsvp.status == "going"


def test_athlete_invalid_status_returns_400(athlete_client, future_event, athlete_member):
    response = athlete_client.put(_url(future_event.pk), {"status": "definitely"}, format="json")
    assert response.status_code == 400
    assert not Rsvp.objects.filter(event=future_event, member=athlete_member).exists()


def test_submit_when_rsvp_disabled_returns_403(
    athlete_client, future_event, athlete_member, coach_team
):
    coach_team.rsvp_enabled = False
    coach_team.save(update_fields=["rsvp_enabled"])
    response = athlete_client.put(_url(future_event.pk), {"status": "going"}, format="json")
    assert response.status_code == 403
    assert response.json()["code"] == "rsvp_disabled"
    assert not Rsvp.objects.filter(event=future_event, member=athlete_member).exists()


def test_non_member_cannot_submit_returns_403(random_client, future_event):
    response = random_client.put(_url(future_event.pk), {"status": "going"}, format="json")
    assert response.status_code == 403


def test_unauthenticated_returns_401(api_client, future_event):
    response = api_client.put(_url(future_event.pk), {"status": "going"}, format="json")
    assert response.status_code == 401


def test_unauthenticated_get_returns_401(api_client, future_event):
    response = api_client.get(_url(future_event.pk))
    assert response.status_code == 401


# =====================================================================
# Summary / my_status reads
# =====================================================================


def test_athlete_reads_own_my_status(athlete_client, future_event, athlete_member):
    athlete_client.put(_url(future_event.pk), {"status": "going"}, format="json")
    response = athlete_client.get(_url(future_event.pk))
    assert response.status_code == 200
    assert response.json()["my_status"] == "going"
    # Athletes do not get the per-member breakdown.
    assert response.json()["by_member"] == []


def test_manager_reads_summary_counts(coach_client, future_event, coach_team, athlete_member):
    # athlete_member -> going. Add maybe, not_going, and a no-response member.
    Rsvp.objects.create(event=future_event, member=athlete_member, status="going")
    statuses = {"maybe": "maybe", "not_going": "not_going"}
    for label, st in statuses.items():
        m = Member.objects.create(
            firstname=f"M{label}", lastname="X", email=f"{label}{future_event.pk}@local.test"
        )
        TeamMembership.objects.create(team=coach_team, member=m)
        Rsvp.objects.create(event=future_event, member=m, status=st)
    # A member with no RSVP.
    silent = Member.objects.create(
        firstname="Silent", lastname="Y", email=f"silent{future_event.pk}@local.test"
    )
    TeamMembership.objects.create(team=coach_team, member=silent)

    response = coach_client.get(_url(future_event.pk))
    assert response.status_code == 200
    body = response.json()
    assert body["counts"] == {"going": 1, "maybe": 1, "not_going": 1, "no_response": 1}
    assert body["total_members"] == 4
    # Manager has no Member here -> my_status null.
    assert body["my_status"] is None
    # Manager gets per-member breakdown of all 4 active members.
    assert len(body["by_member"]) == 4
    by_id = {row["member_id"]: row["status"] for row in body["by_member"]}
    assert by_id[athlete_member.pk] == "going"
    assert by_id[silent.pk] is None


def test_summary_empty(coach_client, future_event, athlete_member):
    response = coach_client.get(_url(future_event.pk))
    assert response.status_code == 200
    body = response.json()
    assert body["counts"] == {"going": 0, "maybe": 0, "not_going": 0, "no_response": 1}
    assert body["total_members"] == 1
    assert body["my_status"] is None


# =====================================================================
# apply_to_attendance (managers only)
# =====================================================================


@pytest.fixture
def seeded_statuses(db):
    present, _ = AttendanceStatus.objects.get_or_create(
        code="present", defaults={"label": "Present"}
    )
    absent, _ = AttendanceStatus.objects.get_or_create(
        code="absent", defaults={"label": "Absent"}
    )
    return {"present": present, "absent": absent}


def _member(team, label, event_pk):
    m = Member.objects.create(
        firstname=label, lastname="Z", email=f"{label}{event_pk}@local.test"
    )
    TeamMembership.objects.create(team=team, member=m)
    return m


def test_apply_to_attendance_maps_statuses(
    coach_client, future_event, coach_team, seeded_statuses, athlete_member
):
    # The team must have the target statuses enabled (mirrors the attendance
    # write path); the seed fixture creates them globally, enable them here.
    coach_team.attendance_statuses.set(seeded_statuses.values())
    going = athlete_member
    not_going = _member(coach_team, "ng", future_event.pk)
    maybe = _member(coach_team, "mb", future_event.pk)
    Rsvp.objects.create(event=future_event, member=going, status="going")
    Rsvp.objects.create(event=future_event, member=not_going, status="not_going")
    Rsvp.objects.create(event=future_event, member=maybe, status="maybe")

    response = coach_client.post(_apply_url(future_event.pk))
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["applied"] == 2
    assert body["skipped"] == 1  # MAYBE skipped

    going_att = Attendance.objects.get(event=future_event, member=going)
    assert going_att.status.code == "present"
    ng_att = Attendance.objects.get(event=future_event, member=not_going)
    assert ng_att.status.code == "absent"
    # MAYBE -> no attendance row created/overwritten.
    assert not Attendance.objects.filter(event=future_event, member=maybe).exists()


def test_apply_to_attendance_skips_departed_members(
    coach_client, future_event, coach_team, seeded_statuses
):
    """B-P2: an RSVP left behind by a member who has since LEFT the team must
    NOT be materialized into attendance (integrity + RGPD)."""
    from django.utils import timezone

    from team.models import TeamMembership

    coach_team.attendance_statuses.set(seeded_statuses.values())
    gone = _member(coach_team, "gone", future_event.pk)
    Rsvp.objects.create(event=future_event, member=gone, status="going")
    TeamMembership.objects.filter(team=coach_team, member=gone, left_at__isnull=True).update(
        left_at=timezone.now()
    )

    response = coach_client.post(_apply_url(future_event.pk))
    assert response.status_code == 200, response.json()
    assert response.json()["applied"] == 0
    assert response.json()["skipped"] == 1
    assert not Attendance.objects.filter(event=future_event, member=gone).exists()


def test_apply_to_attendance_skips_when_status_code_missing(
    coach_client, future_event, coach_team, athlete_member
):
    # No present/absent AttendanceStatus seeded -> everything skipped.
    AttendanceStatus.objects.filter(code__in=["present", "absent"]).delete()
    Rsvp.objects.create(event=future_event, member=athlete_member, status="going")

    response = coach_client.post(_apply_url(future_event.pk))
    assert response.status_code == 200
    assert response.json()["applied"] == 0
    assert response.json()["skipped"] == 1
    assert not Attendance.objects.filter(event=future_event).exists()


def test_apply_to_attendance_skips_statuses_not_enabled_for_team(
    coach_client, future_event, coach_team, seeded_statuses, athlete_member
):
    """Regression: a status that exists globally but is NOT enabled on the team
    must not be materialized via RSVP-apply (team-status invariant)."""
    # Enable only 'present' on the team; 'absent' exists globally but is disabled.
    coach_team.attendance_statuses.set([seeded_statuses["present"]])
    going = athlete_member
    not_going = _member(coach_team, "ng2", future_event.pk)
    Rsvp.objects.create(event=future_event, member=going, status="going")
    Rsvp.objects.create(event=future_event, member=not_going, status="not_going")

    response = coach_client.post(_apply_url(future_event.pk))
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["applied"] == 1  # only GOING -> present
    assert body["skipped"] == 1  # NOT_GOING -> absent (disabled) skipped
    assert Attendance.objects.get(event=future_event, member=going).status.code == "present"
    assert not Attendance.objects.filter(event=future_event, member=not_going).exists()


def test_apply_to_attendance_athlete_forbidden(
    athlete_client, future_event, seeded_statuses, athlete_member
):
    Rsvp.objects.create(event=future_event, member=athlete_member, status="going")
    response = athlete_client.post(_apply_url(future_event.pk))
    assert response.status_code == 403
    assert not Attendance.objects.filter(event=future_event).exists()


def test_apply_to_attendance_unauthenticated_returns_401(api_client, future_event):
    response = api_client.post(_apply_url(future_event.pk))
    assert response.status_code == 401

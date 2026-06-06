"""POST /events/{id}/duplicate/ — duplicate a session + optional weekly recurrence.

Covers:
  - Manager duplicates to a new date -> 201, scalar fields copied, date set.
  - Copy gets its OWN Round rows (different PKs) but re-links the SAME shared
    Exercise rows; round/exercise sets match by PK.
  - Round isolation: deleting the copy's rounds does not touch the source's.
  - Members are NOT copied.
  - repeat_weekly=True -> N events on date, date+7, ... in chronological order.
  - repeat_weekly=False with a large occurrences -> forced to a single copy.
  - occurrences min/max validation (0 -> 400, 53 -> 400).
  - Athlete-member -> 403, nothing created.
  - Unrelated user -> 4xx (404 via queryset scope), nothing created.
"""

from datetime import date, time, timedelta

import pytest

from event.models import Event
from member.models import Member
from team.models import TeamMembership
from tests.factories import (
    EventFactory,
    ExerciseFactory,
    MemberFactory,
    ProgramFactory,
    RoundFactory,
    TeamFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# fixtures: a team with an owner (manager), an athlete-member, an outsider
# --------------------------------------------------------------------------


@pytest.fixture
def owner_user():
    return UserFactory(username="dup_owner")


@pytest.fixture
def team(owner_user):
    return TeamFactory(owner=owner_user, is_active=True)


@pytest.fixture
def owner_client(api_client, owner_user):
    api_client.force_authenticate(user=owner_user)
    return api_client


@pytest.fixture
def athlete_user():
    return UserFactory(username="dup_athlete")


@pytest.fixture
def athlete_member(athlete_user, team):
    member = Member.objects.create(
        firstname="A", lastname="Thlete", email=athlete_user.email, user=athlete_user
    )
    TeamMembership.objects.create(team=team, member=member)
    return member


@pytest.fixture
def athlete_client(api_client, athlete_user, athlete_member):
    api_client.force_authenticate(user=athlete_user)
    return api_client


@pytest.fixture
def outsider_client(api_client):
    user = UserFactory(username="dup_outsider")
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def source_event(team):
    """A fully-populated source session: scalar fields + 2 rounds sharing
    a small library of exercises."""
    program = ProgramFactory(team=team)
    event = EventFactory(
        name="Threshold set",
        refer_program=program,
        goal="Endurance",
        color="#abcdef",
        location="Piscine 50m",
        equipment="palmes, plaquettes",
        total=4200,
        hour_start=time(18, 0),
        hour_end=time(19, 30),
        vis_distance="never",
        vis_goal="after",
        vis_rounds="always",
        date=date(2026, 7, 1),
    )
    ex1 = ExerciseFactory(distance=200, repetition=4)
    ex2 = ExerciseFactory(distance=100, repetition=8)
    r1 = RoundFactory(sport=team.sport, language=team.language, order=1, count=1)
    r1.exercises.set([ex1, ex2])
    r2 = RoundFactory(sport=team.sport, language=team.language, order=2, count=3)
    r2.exercises.set([ex2])
    event.rounds.set([r1, r2])
    return event


def _url(event):
    return f"/api/v1/events/{event.pk}/duplicate/"


# ==========================================================================
# 1. Basic duplicate: scalar fields + date
# ==========================================================================


def test_duplicate_copies_scalar_fields_and_sets_date(owner_client, source_event):
    target = date(2026, 8, 15)
    resp = owner_client.post(_url(source_event), {"date": target.isoformat()}, format="json")
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert len(body["created"]) == 1
    new_id = body["created"][0]["id"]
    assert body["created"][0]["date"] == target.isoformat()

    copy = Event.objects.get(pk=new_id)
    assert copy.pk != source_event.pk
    assert copy.date == target
    assert copy.name == source_event.name
    assert copy.goal == source_event.goal
    assert copy.color == source_event.color
    assert copy.location == source_event.location
    assert copy.equipment == source_event.equipment
    assert copy.total == source_event.total
    assert copy.hour_start == source_event.hour_start
    assert copy.hour_end == source_event.hour_end
    assert copy.vis_distance == source_event.vis_distance
    assert copy.vis_goal == source_event.vis_goal
    assert copy.vis_rounds == source_event.vis_rounds
    assert copy.refer_program_id == source_event.refer_program_id


# ==========================================================================
# 2. Own Round rows, shared Exercise rows
# ==========================================================================


def test_duplicate_deep_copies_rounds_relinks_exercises(owner_client, source_event):
    target = date(2026, 8, 15)
    resp = owner_client.post(_url(source_event), {"date": target.isoformat()}, format="json")
    assert resp.status_code == 201, resp.json()
    copy = Event.objects.get(pk=resp.json()["created"][0]["id"])

    src_rounds = list(source_event.rounds.all().order_by("order"))
    copy_rounds = list(copy.rounds.all().order_by("order"))

    # Same number of rounds.
    assert len(copy_rounds) == len(src_rounds) == 2

    # Rounds are NEW rows (disjoint PKs) — deep-copied, not shared.
    src_round_pks = {r.pk for r in src_rounds}
    copy_round_pks = {r.pk for r in copy_rounds}
    assert src_round_pks.isdisjoint(copy_round_pks)

    # But exercises are RE-LINKED (same shared library PKs), per round.
    for src_r, copy_r in zip(src_rounds, copy_rounds):
        assert copy_r.order == src_r.order
        assert copy_r.count == src_r.count
        src_ex_pks = set(src_r.exercises.values_list("pk", flat=True))
        copy_ex_pks = set(copy_r.exercises.values_list("pk", flat=True))
        assert copy_ex_pks == src_ex_pks


# ==========================================================================
# 3. Round isolation
# ==========================================================================


def test_duplicate_round_edit_does_not_affect_source(owner_client, source_event):
    target = date(2026, 8, 15)
    resp = owner_client.post(_url(source_event), {"date": target.isoformat()}, format="json")
    copy = Event.objects.get(pk=resp.json()["created"][0]["id"])

    src_round_pks_before = set(source_event.rounds.values_list("pk", flat=True))
    assert len(src_round_pks_before) == 2

    # Delete the copy's rounds entirely.
    for r in copy.rounds.all():
        r.delete()
    copy.refresh_from_db()
    assert copy.rounds.count() == 0

    # Source is untouched.
    source_event.refresh_from_db()
    src_round_pks_after = set(source_event.rounds.values_list("pk", flat=True))
    assert src_round_pks_after == src_round_pks_before
    assert source_event.rounds.count() == 2


# ==========================================================================
# 4. Members are NOT copied
# ==========================================================================


def test_duplicate_does_not_copy_members(owner_client, source_event, team):
    """The duplicate's M2M is NOT cloned from the source. A new Event's
    members are populated solely by the event-create signal (active team
    members); a member that is on the SOURCE but is not an active team
    member must never appear on the copy."""
    # A member attached to the SOURCE event but with NO active team
    # membership — proves the copy does not inherit the source's M2M.
    foreign = MemberFactory()  # no teams= -> no TeamMembership
    source_event.members.add(foreign)
    assert foreign in source_event.members.all()

    target = date(2026, 8, 15)
    resp = owner_client.post(_url(source_event), {"date": target.isoformat()}, format="json")
    assert resp.status_code == 201, resp.json()
    copy = Event.objects.get(pk=resp.json()["created"][0]["id"])
    # The source's foreign member did NOT carry over.
    assert foreign not in copy.members.all()
    # The copy's members come only from the signal: the team's active members
    # (the source's own attendance list is irrelevant to the copy).
    active_member_ids = set(
        team.memberships.filter(left_at__isnull=True).values_list("member_id", flat=True)
    )
    assert set(copy.members.values_list("id", flat=True)) == active_member_ids


# ==========================================================================
# 5. repeat_weekly=True -> N events 7 days apart, chronological order
# ==========================================================================


def test_duplicate_weekly_creates_series(owner_client, source_event):
    start = date(2026, 8, 3)
    resp = owner_client.post(
        _url(source_event),
        {"date": start.isoformat(), "repeat_weekly": True, "occurrences": 4},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    created = resp.json()["created"]
    assert len(created) == 4

    expected_dates = [(start + timedelta(days=7 * k)).isoformat() for k in range(4)]
    assert [c["date"] for c in created] == expected_dates  # chronological

    # Four distinct events actually exist on those dates.
    ids = [c["id"] for c in created]
    assert len(set(ids)) == 4
    for c in created:
        ev = Event.objects.get(pk=c["id"])
        assert ev.date.isoformat() == c["date"]
        assert ev.rounds.count() == 2


# ==========================================================================
# 6. repeat_weekly=False forces occurrences to 1
# ==========================================================================


def test_duplicate_no_repeat_forces_single_copy(owner_client, source_event):
    before = Event.objects.count()
    start = date(2026, 8, 3)
    resp = owner_client.post(
        _url(source_event),
        {"date": start.isoformat(), "repeat_weekly": False, "occurrences": 5},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    assert len(resp.json()["created"]) == 1
    assert Event.objects.count() == before + 1


# ==========================================================================
# 7. occurrences bounds
# ==========================================================================


def test_duplicate_occurrences_too_high_returns_400(owner_client, source_event):
    before = Event.objects.count()
    resp = owner_client.post(
        _url(source_event),
        {"date": "2026-08-03", "repeat_weekly": True, "occurrences": 53},
        format="json",
    )
    assert resp.status_code == 400
    assert Event.objects.count() == before


def test_duplicate_occurrences_zero_returns_400(owner_client, source_event):
    before = Event.objects.count()
    resp = owner_client.post(
        _url(source_event),
        {"date": "2026-08-03", "repeat_weekly": True, "occurrences": 0},
        format="json",
    )
    assert resp.status_code == 400
    assert Event.objects.count() == before


# ==========================================================================
# 8. Athlete-member -> 403, nothing created
# ==========================================================================


def test_duplicate_as_athlete_member_returns_403(athlete_client, source_event):
    before = Event.objects.count()
    resp = athlete_client.post(
        _url(source_event), {"date": "2026-08-03"}, format="json"
    )
    assert resp.status_code == 403
    assert Event.objects.count() == before


# ==========================================================================
# 9. Unrelated user -> 4xx (404 via queryset scope), nothing created
# ==========================================================================


def test_duplicate_as_outsider_does_not_create(outsider_client, source_event):
    before = Event.objects.count()
    resp = outsider_client.post(
        _url(source_event), {"date": "2026-08-03"}, format="json"
    )
    assert 400 <= resp.status_code < 500
    assert resp.status_code == 404
    assert Event.objects.count() == before

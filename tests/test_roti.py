"""Coverage of /api/v1/events/{event_pk}/roti/.

ROTI = per-athlete session difficulty rating (1..5), gated by the team's
roti_enabled toggle. Mirrors the attendance test layout.

Surface:
  - GET  /api/v1/events/{event_pk}/roti/          -> summary (incl. my_score)
  - PUT  /api/v1/events/{event_pk}/roti/          -> upsert caller's own score
  - GET  /api/v1/events/{event_pk}/roti/summary/  -> manager aggregate
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from event.models import Event
from member.models import Member
from roti.models import Roti
from team.models import TeamMembership
from tests.factories import ProgramFactory, TeamFactory

pytestmark = pytest.mark.django_db


User = get_user_model()


@pytest.fixture
def coach_user(db):
    return User.objects.create_user(
        username="roti_coach", email="roti_coach@local.test", password="pass"
    )


@pytest.fixture
def coach_team(coach_user):
    return TeamFactory(owner=coach_user, is_active=True, roti_enabled=True)


@pytest.fixture
def coach_program(coach_team):
    return ProgramFactory(team=coach_team)


@pytest.fixture
def future_event(coach_program):
    return Event.objects.create(
        refer_program=coach_program,
        name="ROTI event",
        date=timezone.localdate() + timedelta(days=3),
    )


@pytest.fixture
def athlete_user(db):
    return User.objects.create_user(
        username="roti_athlete", email="roti_athlete@local.test", password="pass"
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
        username="roti_rando", email="roti_rando@local.test", password="pass"
    )
    api_client.force_authenticate(user=user)
    return api_client


def _url(event_pk):
    return f"/api/v1/events/{event_pk}/roti/"


def _summary_url(event_pk):
    return f"/api/v1/events/{event_pk}/roti/summary/"


# =====================================================================
# Athlete upserts own score
# =====================================================================


def test_athlete_submits_score_persisted(athlete_client, future_event, athlete_member):
    response = athlete_client.put(_url(future_event.pk), {"score": 4}, format="json")
    assert response.status_code == 200, response.json()
    roti = Roti.objects.get(event=future_event, member=athlete_member)
    assert roti.score == 4
    assert response.json()["my_score"] == 4


def test_athlete_resubmit_updates_no_duplicate(athlete_client, future_event, athlete_member):
    athlete_client.put(_url(future_event.pk), {"score": 2}, format="json")
    response = athlete_client.put(_url(future_event.pk), {"score": 5}, format="json")
    assert response.status_code == 200
    assert Roti.objects.filter(event=future_event, member=athlete_member).count() == 1
    roti = Roti.objects.get(event=future_event, member=athlete_member)
    assert roti.score == 5


@pytest.mark.parametrize("bad_score", [0, 6, -1, 99])
def test_athlete_score_out_of_range_returns_400(
    athlete_client, future_event, athlete_member, bad_score
):
    response = athlete_client.put(_url(future_event.pk), {"score": bad_score}, format="json")
    assert response.status_code == 400
    assert not Roti.objects.filter(event=future_event, member=athlete_member).exists()


def test_submit_when_roti_disabled_returns_403(
    athlete_client, future_event, athlete_member, coach_team
):
    coach_team.roti_enabled = False
    coach_team.save(update_fields=["roti_enabled"])
    response = athlete_client.put(_url(future_event.pk), {"score": 3}, format="json")
    assert response.status_code == 403
    assert response.json()["code"] == "roti_disabled"
    assert not Roti.objects.filter(event=future_event, member=athlete_member).exists()


def test_non_member_cannot_submit_returns_403(random_client, future_event):
    response = random_client.put(_url(future_event.pk), {"score": 3}, format="json")
    assert response.status_code == 403


def test_unauthenticated_returns_401(api_client, future_event):
    response = api_client.put(_url(future_event.pk), {"score": 3}, format="json")
    assert response.status_code == 401


def test_unauthenticated_get_returns_401(api_client, future_event):
    response = api_client.get(_url(future_event.pk))
    assert response.status_code == 401


# =====================================================================
# Summary / my_score reads
# =====================================================================


def test_manager_reads_summary(coach_client, future_event, coach_team, athlete_member):
    # Build several members with scores: 1, 3, 3, 5  -> avg 3.0, count 4
    Roti.objects.create(event=future_event, member=athlete_member, score=1)
    for score in (3, 3, 5):
        m = Member.objects.create(
            firstname=f"M{score}", lastname="X", email=f"m{score}{future_event.pk}@local.test"
        )
        TeamMembership.objects.create(team=coach_team, member=m)
        Roti.objects.create(event=future_event, member=m, score=score)

    response = coach_client.get(_summary_url(future_event.pk))
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 4
    assert body["average"] == pytest.approx(3.0)
    assert body["distribution"] == {"1": 1, "2": 0, "3": 2, "4": 0, "5": 1}


def test_manager_reads_summary_via_list_endpoint(
    coach_client, future_event, coach_team, athlete_member
):
    Roti.objects.create(event=future_event, member=athlete_member, score=2)
    response = coach_client.get(_url(future_event.pk))
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["average"] == pytest.approx(2.0)
    # Manager has no Member here -> my_score null
    assert body["my_score"] is None


def test_athlete_reads_own_my_score(athlete_client, future_event, athlete_member):
    athlete_client.put(_url(future_event.pk), {"score": 4}, format="json")
    response = athlete_client.get(_url(future_event.pk))
    assert response.status_code == 200
    assert response.json()["my_score"] == 4


def test_summary_empty_returns_null_average(coach_client, future_event):
    response = coach_client.get(_summary_url(future_event.pk))
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0
    assert body["average"] is None
    assert body["distribution"] == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
    assert body["my_score"] is None

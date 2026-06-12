"""Coverage of GET /api/v1/teams/{id}/roster-history/ (F3).

Manager-only timeline of every membership period (active + past)."""

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from member.models import Member
from team.models import TeamMembership
from tests.factories import TeamFactory

pytestmark = pytest.mark.django_db
User = get_user_model()


def _url(team_pk):
    return f"/api/v1/teams/{team_pk}/roster-history/"


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="rh_owner@x.test", password="p")


@pytest.fixture
def team(owner):
    return TeamFactory(owner=owner, is_active=True)


def _member(team, first, last, left=False):
    m = Member.objects.create(firstname=first, lastname=last, email=f"{first}@x.test")
    ms = TeamMembership.objects.create(team=team, member=m)
    if left:
        ms.left_at = timezone.now()
        ms.save(update_fields=["left_at"])
    return m


def test_unauthenticated_returns_401(api_client, team):
    assert api_client.get(_url(team.pk)).status_code == 401


def test_owner_gets_active_and_past_periods(api_client, owner, team):
    _member(team, "Anna", "Active")
    _member(team, "Past", "Gone", left=True)
    api_client.force_authenticate(user=owner)

    resp = api_client.get(_url(team.pk))
    assert resp.status_code == 200, resp.json()
    rows = resp.json()["entries"]
    assert len(rows) == 2
    by_name = {r["name"]: r for r in rows}
    assert by_name["Anna Active"]["active"] is True
    assert by_name["Anna Active"]["left_at"] is None
    assert by_name["Past Gone"]["active"] is False
    assert by_name["Past Gone"]["left_at"] is not None


def test_athlete_member_forbidden(api_client, team):
    athlete = User.objects.create_user(email="rh_ath@x.test", password="p")
    m = Member.objects.create(firstname="Ath", lastname="Lete", user=athlete)
    TeamMembership.objects.create(team=team, member=m)
    api_client.force_authenticate(user=athlete)
    assert api_client.get(_url(team.pk)).status_code == 403


def test_rejoin_shows_two_rows(api_client, owner, team):
    """A member who left and rejoined has two membership rows -> two periods."""
    m = Member.objects.create(firstname="Re", lastname="Join")
    old = TeamMembership.objects.create(team=team, member=m)
    old.left_at = timezone.now()
    old.save(update_fields=["left_at"])
    TeamMembership.objects.create(team=team, member=m)  # active again
    api_client.force_authenticate(user=owner)

    rows = api_client.get(_url(team.pk)).json()["entries"]
    join_rows = [r for r in rows if r["name"] == "Re Join"]
    assert len(join_rows) == 2
    assert {r["active"] for r in join_rows} == {True, False}

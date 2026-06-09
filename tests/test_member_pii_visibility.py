"""Coverage of teammate-PII protection on the Member endpoint.

Regression for the HIGH security bug where any athlete could GET
/api/v1/members/ (and /members/{id}/) and read every teammate's email +
phonenumber. The serializer now redacts email/phonenumber for requesters who
are not a manager (owner/manager) of one of the member's teams and are not the
member's own linked user; non-PII identity fields stay visible.
"""

import pytest
from django.contrib.auth import get_user_model

from member.models import Member
from team.models import TeamMembership
from tests.factories import TeamFactory

pytestmark = pytest.mark.django_db


User = get_user_model()


@pytest.fixture
def coach_user(db):
    return User.objects.create_user(
        username="pii_coach", email="pii_coach@local.test", password="pass"
    )


@pytest.fixture
def team(coach_user):
    return TeamFactory(owner=coach_user, is_active=True)


@pytest.fixture
def athlete_user(db):
    return User.objects.create_user(
        username="pii_athlete", email="pii_athlete@local.test", password="pass"
    )


@pytest.fixture
def athlete_member(team, athlete_user):
    """The requesting athlete's OWN linked member."""
    m = Member.objects.create(
        firstname="Al", lastname="Ete", email="al@local.test",
        phonenumber="+32111111111", user=athlete_user,
    )
    TeamMembership.objects.create(team=team, member=m)
    return m


@pytest.fixture
def teammate_member(team):
    """A different member on the same team (the PII target)."""
    m = Member.objects.create(
        firstname="Tea", lastname="Mmate", email="teammate@local.test",
        phonenumber="+32999999999",
    )
    TeamMembership.objects.create(team=team, member=m)
    return m


def _detail(pk):
    return f"/api/v1/members/{pk}/"


LIST_URL = "/api/v1/members/"


def test_athlete_cannot_see_teammate_pii(
    api_client, athlete_user, athlete_member, teammate_member
):
    api_client.force_authenticate(user=athlete_user)
    resp = api_client.get(_detail(teammate_member.pk))
    assert resp.status_code == 200, resp.content
    body = resp.json()
    # PII redacted...
    assert body["email"] is None
    assert body["phonenumber"] is None
    # ...but non-PII identity stays visible.
    assert body["id"] == teammate_member.pk
    assert body["firstname"] == "Tea"
    assert body["lastname"] == "Mmate"
    assert body["fullname"] == "Tea Mmate"


def test_manager_sees_full_teammate_pii(
    api_client, coach_user, teammate_member
):
    api_client.force_authenticate(user=coach_user)
    resp = api_client.get(_detail(teammate_member.pk))
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["email"] == "teammate@local.test"
    assert body["phonenumber"] == "+32999999999"


def test_user_sees_own_member_full_pii(
    api_client, athlete_user, athlete_member
):
    api_client.force_authenticate(user=athlete_user)
    resp = api_client.get(_detail(athlete_member.pk))
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["email"] == "al@local.test"
    assert body["phonenumber"] == "+32111111111"


def test_list_redacts_teammate_pii_for_athlete(
    api_client, athlete_user, athlete_member, teammate_member
):
    api_client.force_authenticate(user=athlete_user)
    resp = api_client.get(LIST_URL)
    assert resp.status_code == 200, resp.content
    rows = resp.json()["results"]
    by_id = {r["id"]: r for r in rows}
    # Own member -> full PII.
    assert by_id[athlete_member.pk]["email"] == "al@local.test"
    assert by_id[athlete_member.pk]["phonenumber"] == "+32111111111"
    # Teammate -> redacted.
    assert by_id[teammate_member.pk]["email"] is None
    assert by_id[teammate_member.pk]["phonenumber"] is None


def test_list_shows_full_pii_to_manager(
    api_client, coach_user, athlete_member, teammate_member
):
    api_client.force_authenticate(user=coach_user)
    resp = api_client.get(LIST_URL)
    assert resp.status_code == 200, resp.content
    rows = resp.json()["results"]
    by_id = {r["id"]: r for r in rows}
    assert by_id[teammate_member.pk]["email"] == "teammate@local.test"
    assert by_id[teammate_member.pk]["phonenumber"] == "+32999999999"

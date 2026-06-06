"""Coverage of POST /api/v1/members/{id}/anonymize/ — coach RGPD erasure.

A coach (owner/manager) of a team the member belongs to blanks the member's
PII, unlinks any user account (without deleting it), and deletes coach notes
about the member. Training history (memberships, performances) is preserved.
"""

import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from member.models import Member
from note.models import Note
from performance.models import Performance
from team.models import TeamMembership
from tests.factories import TeamFactory

pytestmark = pytest.mark.django_db


User = get_user_model()


def _url(member_id):
    return f"/api/v1/members/{member_id}/anonymize/"


@pytest.fixture
def coach_user(db):
    return User.objects.create_user(
        username="anon_coach", email="anon_coach@local.test", password="pass"
    )


@pytest.fixture
def coach_team(coach_user):
    return TeamFactory(owner=coach_user, is_active=True)


@pytest.fixture
def athlete_user(db):
    return User.objects.create_user(
        username="anon_athlete", email="anon_athlete@local.test", password="pass"
    )


@pytest.fixture
def member(coach_team, athlete_user):
    m = Member.objects.create(
        firstname="Jane",
        lastname="Doe",
        email="jane.doe@local.test",
        phonenumber="+32411111111",
        user=athlete_user,
    )
    TeamMembership.objects.create(team=coach_team, member=m, joined_at=timezone.now())
    return m


@pytest.fixture
def coach_client(api_client, coach_user):
    api_client.force_authenticate(user=coach_user)
    return api_client


def test_coach_anonymizes_member(coach_client, coach_team, member, athlete_user):
    # Seed PII-bearing notes + preserved training history.
    Note.objects.create(team=coach_team, member=member, content="secret note about Jane")
    Note.objects.create(
        team=coach_team, member=member, content="visible", visible_to_athlete=True
    )
    perf = Performance.objects.create(
        team=coach_team,
        member=member,
        label="100m",
        value="12.000",
        unit="s",
        recorded_on=dt.date(2026, 1, 1),
    )

    response = coach_client.post(_url(member.id), format="json")
    assert response.status_code == 200, response.json()

    member.refresh_from_db()
    assert member.firstname == "Athlète"
    assert member.lastname == f"anonymisé #{member.id}"
    assert member.email == ""
    assert member.phonenumber in ("", None)

    # User unlinked but NOT deleted.
    assert member.user is None
    assert User.objects.filter(pk=athlete_user.pk).exists()

    # Notes about the member are gone.
    assert Note.objects.filter(member=member).count() == 0

    # Training history preserved.
    assert Performance.objects.filter(pk=perf.pk).exists()
    assert TeamMembership.objects.filter(member=member).exists()

    body = response.json()
    assert body["firstname"] == "Athlète"
    assert body["user"] is None


def test_non_coach_athlete_cannot_anonymize(api_client, member, athlete_user):
    api_client.force_authenticate(user=athlete_user)
    response = api_client.post(_url(member.id), format="json")
    assert response.status_code in (403, 404)

    member.refresh_from_db()
    assert member.firstname == "Jane"


def test_unrelated_user_cannot_anonymize(api_client, member):
    other = User.objects.create_user(
        username="anon_other", email="anon_other@local.test", password="pass"
    )
    api_client.force_authenticate(user=other)
    response = api_client.post(_url(member.id), format="json")
    assert response.status_code in (403, 404)

    member.refresh_from_db()
    assert member.firstname == "Jane"


def test_anonymize_works_on_past_membership(coach_client, coach_team, athlete_user):
    """A member whose only membership in the managed team is PAST is still
    anonymizable (the default viewset queryset would 404 on it)."""
    m = Member.objects.create(firstname="Past", lastname="Gone", user=athlete_user)
    TeamMembership.objects.create(
        team=coach_team,
        member=m,
        joined_at=timezone.now(),
        left_at=timezone.now(),
    )
    response = coach_client.post(_url(m.id), format="json")
    assert response.status_code == 200, response.json()
    m.refresh_from_db()
    assert m.firstname == "Athlète"


def test_anonymize_is_idempotent_ish(coach_client, member):
    first = coach_client.post(_url(member.id), format="json")
    assert first.status_code == 200

    # Second call: member no longer has a linked user, but membership still
    # exists in the managed team -> must not crash.
    second = coach_client.post(_url(member.id), format="json")
    assert second.status_code == 200
    member.refresh_from_db()
    assert member.firstname == "Athlète"
    assert member.user is None

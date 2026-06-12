"""Coverage of the TeamMembership feature.

URL: /api/v1/teams/{team_pk}/memberships/

Permissions:
  - Read: any team member (owner / manager / athlete via Member.user)
  - Create: owner / managers only
  - Delete (soft, sets left_at): the member herself OR owner / managers
  - Owner cannot leave their own team
"""

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from member.models import Member
from team.models import TeamMembership
from tests.factories import TeamFactory

pytestmark = pytest.mark.django_db


User = get_user_model()


@pytest.fixture
def coach_user(db):
    return User.objects.create_user(
        email="ms_coach@local.test", password="pass"
    )


@pytest.fixture
def coach_team(coach_user):
    return TeamFactory(owner=coach_user, is_active=True)


@pytest.fixture
def athlete_user(db):
    return User.objects.create_user(
        email="ms_athlete@local.test", password="pass"
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
        email="ms_rando@local.test", password="pass"
    )
    api_client.force_authenticate(user=user)
    return api_client


def _url(team_pk, ms_id=None):
    base = f"/api/v1/teams/{team_pk}/memberships/"
    return base if ms_id is None else f"{base}{ms_id}/"


# =====================================================================
# CREATE
# =====================================================================


def test_owner_can_add_member_to_team(coach_client, coach_team):
    new_member = Member.objects.create(firstname="N", lastname="ew", email="n@local.test")
    response = coach_client.post(_url(coach_team.pk), {"member": new_member.pk}, format="json")
    assert response.status_code == 201, response.json()
    assert TeamMembership.objects.filter(
        team=coach_team, member=new_member, left_at__isnull=True
    ).exists()


def test_athlete_cannot_add_member(athlete_client, coach_team, athlete_member):
    new_member = Member.objects.create(firstname="X", lastname="Y", email="x@local.test")
    response = athlete_client.post(_url(coach_team.pk), {"member": new_member.pk}, format="json")
    assert response.status_code == 403


def test_random_user_cannot_add_member(random_client, coach_team):
    new_member = Member.objects.create(firstname="Z", lastname="W", email="z@local.test")
    response = random_client.post(_url(coach_team.pk), {"member": new_member.pk}, format="json")
    assert response.status_code == 403


def test_already_active_member_returns_400(coach_client, coach_team, athlete_member):
    """Idempotency: adding an already-active member fails with already_member."""
    response = coach_client.post(_url(coach_team.pk), {"member": athlete_member.pk}, format="json")
    assert response.status_code == 400
    assert "already" in str(response.json()).lower()


def test_manager_can_add_member_from_team_they_manage(coach_client, coach_user, coach_team):
    """A member active in another team the SAME coach manages can be attached."""
    other_team = TeamFactory(owner=coach_user, is_active=True)
    member = Member.objects.create(firstname="Own", lastname="Ed", email="owned@local.test")
    TeamMembership.objects.create(team=other_team, member=member)

    response = coach_client.post(_url(coach_team.pk), {"member": member.pk}, format="json")
    assert response.status_code == 201, response.json()
    assert TeamMembership.objects.filter(
        team=coach_team, member=member, left_at__isnull=True
    ).exists()


def test_manager_cannot_add_stranger_attached_member(coach_client, coach_team):
    """A member active in a team the caller does NOT manage -> 403 (PII grab)."""
    stranger_owner = User.objects.create_user(
        email="ms_stranger@local.test", password="pass"
    )
    stranger_team = TeamFactory(owner=stranger_owner, is_active=True)
    stranger_member = Member.objects.create(
        firstname="Str", lastname="Anger", email="stranger@local.test"
    )
    TeamMembership.objects.create(team=stranger_team, member=stranger_member)

    response = coach_client.post(
        _url(coach_team.pk), {"member": stranger_member.pk}, format="json"
    )
    assert response.status_code == 403
    assert response.json().get("code") == "not_authorized_member"
    assert not TeamMembership.objects.filter(team=coach_team, member=stranger_member).exists()


def test_manager_can_add_brand_new_unattached_member(coach_client, coach_team):
    """A member with no active membership anywhere (invitation/join case) -> ok."""
    new_member = Member.objects.create(firstname="Br", lastname="And", email="brand@local.test")
    response = coach_client.post(_url(coach_team.pk), {"member": new_member.pk}, format="json")
    assert response.status_code == 201, response.json()
    assert TeamMembership.objects.filter(
        team=coach_team, member=new_member, left_at__isnull=True
    ).exists()


# =====================================================================
# LIST
# =====================================================================


def test_owner_lists_active_memberships_by_default(coach_client, coach_team, athlete_member):
    # Add a second, then end one
    other = Member.objects.create(firstname="O", lastname="ther", email="o@local.test")
    ms_other = TeamMembership.objects.create(team=coach_team, member=other)
    ms_other.left_at = timezone.now()
    ms_other.save(update_fields=["left_at"])

    response = coach_client.get(_url(coach_team.pk))
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["member"] == athlete_member.pk


def test_owner_lists_full_history_with_include_inactive(coach_client, coach_team, athlete_member):
    other = Member.objects.create(firstname="O", lastname="ther", email="o2@local.test")
    ms_other = TeamMembership.objects.create(team=coach_team, member=other)
    ms_other.left_at = timezone.now()
    ms_other.save(update_fields=["left_at"])

    response = coach_client.get(_url(coach_team.pk) + "?include_inactive=true")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2


def test_athlete_can_read_memberships_of_own_team(athlete_client, coach_team, athlete_member):
    response = athlete_client.get(_url(coach_team.pk))
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_random_user_gets_empty_list(random_client, coach_team, athlete_member):
    response = random_client.get(_url(coach_team.pk))
    assert response.status_code == 200
    assert response.json() == []


# =====================================================================
# DELETE (leave)
# =====================================================================


def test_athlete_can_leave_team(athlete_client, coach_team, athlete_member):
    ms = TeamMembership.objects.get(team=coach_team, member=athlete_member, left_at__isnull=True)
    response = athlete_client.delete(_url(coach_team.pk, ms.pk))
    assert response.status_code == 204
    ms.refresh_from_db()
    assert ms.left_at is not None


def test_manager_can_remove_athlete(coach_client, coach_team, athlete_member):
    ms = TeamMembership.objects.get(team=coach_team, member=athlete_member, left_at__isnull=True)
    response = coach_client.delete(_url(coach_team.pk, ms.pk))
    assert response.status_code == 204
    ms.refresh_from_db()
    assert ms.left_at is not None


def test_owner_cannot_leave_own_team(coach_client, coach_team, coach_user):
    """Owner is enrolled as Member+Membership, then tries to self-leave."""
    coach_member = Member.objects.create(
        firstname="Co", lastname="Ach", email=coach_user.email, user=coach_user
    )
    ms = TeamMembership.objects.create(team=coach_team, member=coach_member)
    response = coach_client.delete(_url(coach_team.pk, ms.pk))
    assert response.status_code == 403
    ms.refresh_from_db()
    assert ms.left_at is None


def test_other_user_cannot_remove_someone_else(random_client, coach_team, athlete_member):
    ms = TeamMembership.objects.get(team=coach_team, member=athlete_member, left_at__isnull=True)
    response = random_client.delete(_url(coach_team.pk, ms.pk))
    assert response.status_code in (403, 404)
    ms.refresh_from_db()
    assert ms.left_at is None


# =====================================================================
# REJOIN
# =====================================================================


def test_rejoining_creates_new_row(coach_client, coach_team, athlete_member):
    """End the current membership, re-add via API: a NEW row is created."""
    old = TeamMembership.objects.get(team=coach_team, member=athlete_member, left_at__isnull=True)
    old.left_at = timezone.now()
    old.save(update_fields=["left_at"])

    response = coach_client.post(_url(coach_team.pk), {"member": athlete_member.pk}, format="json")
    assert response.status_code == 201
    assert TeamMembership.objects.filter(team=coach_team, member=athlete_member).count() == 2
    assert (
        TeamMembership.objects.filter(
            team=coach_team, member=athlete_member, left_at__isnull=True
        ).count()
        == 1
    )


# =====================================================================
# ACTIVE MEMBERS PROPERTY
# =====================================================================


def test_team_active_members_excludes_left(coach_team, athlete_member):
    other = Member.objects.create(firstname="O", lastname="L", email="ol@local.test")
    ms = TeamMembership.objects.create(team=coach_team, member=other)
    ms.left_at = timezone.now()
    ms.save(update_fields=["left_at"])

    active_ids = list(coach_team.active_members.values_list("pk", flat=True))
    assert athlete_member.pk in active_ids
    assert other.pk not in active_ids


# =====================================================================
# UPDATE (PATCH/PUT) is not allowed — membership has no editable fields.
# Guards against the IDOR where an athlete repoints `member` to another id.
# =====================================================================


def test_athlete_cannot_patch_membership(athlete_client, coach_team, athlete_member):
    ms = TeamMembership.objects.get(team=coach_team, member=athlete_member, left_at__isnull=True)
    other = Member.objects.create(firstname="V", lastname="ictim", email="victim@local.test")
    response = athlete_client.patch(
        _url(coach_team.pk, ms.pk), {"member": other.pk}, format="json"
    )
    assert response.status_code == 405
    ms.refresh_from_db()
    assert ms.member_id == athlete_member.pk


def test_athlete_cannot_put_membership(athlete_client, coach_team, athlete_member):
    ms = TeamMembership.objects.get(team=coach_team, member=athlete_member, left_at__isnull=True)
    other = Member.objects.create(firstname="V2", lastname="ictim", email="victim2@local.test")
    response = athlete_client.put(
        _url(coach_team.pk, ms.pk), {"member": other.pk}, format="json"
    )
    assert response.status_code == 405
    ms.refresh_from_db()
    assert ms.member_id == athlete_member.pk


def test_post_and_delete_still_work_after_update_block(coach_client, coach_team):
    """POST (add) and DELETE (leave) remain functional with PUT/PATCH blocked."""
    new_member = Member.objects.create(firstname="P", lastname="D", email="pd@local.test")
    create = coach_client.post(_url(coach_team.pk), {"member": new_member.pk}, format="json")
    assert create.status_code == 201, create.json()
    ms_id = create.json()["id"]

    delete = coach_client.delete(_url(coach_team.pk, ms_id))
    assert delete.status_code == 204

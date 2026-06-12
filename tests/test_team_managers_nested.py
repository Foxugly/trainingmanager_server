"""Coverage of TeamSerializer.managers nested read / managers_ids write.

Aligned with Team.owner shape (CustomUserPublicSerializer): the frontend
can render manager names without an extra fetch.
"""

import pytest
from django.contrib.auth import get_user_model

from tests.factories import TeamFactory

pytestmark = pytest.mark.django_db


User = get_user_model()


@pytest.fixture
def owner_user(db):
    return User.objects.create_user(
        email="tm_owner@local.test", password="pass"
    )


@pytest.fixture
def owner_client(api_client, owner_user):
    api_client.force_authenticate(user=owner_user)
    return api_client


@pytest.fixture
def manager_user(db):
    return User.objects.create_user(
        email="tm_manager@local.test",
        password="pass",
        first_name="Mana",
        last_name="Ger",
    )


@pytest.fixture
def team_with_manager(owner_user, manager_user):
    team = TeamFactory(owner=owner_user, is_active=True)
    team.managers.add(manager_user)
    return team


def test_GET_team_managers_returns_nested_user_public(owner_client, team_with_manager):
    response = owner_client.get(f"/api/v1/teams/{team_with_manager.pk}/")
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data["managers"], list)
    assert len(data["managers"]) == 1
    nested = data["managers"][0]
    assert "id" in nested
    assert "first_name" in nested
    assert "last_name" in nested
    # Email-only: there is no username field anymore. CustomUserPublic must NOT
    # leak the email either.
    assert "username" not in nested
    assert "email" not in nested


def test_PATCH_team_managers_via_managers_ids(owner_client, team_with_manager):
    new_mgr = User.objects.create_user(
        email="tm_new_mgr@local.test", password="pass"
    )
    response = owner_client.patch(
        f"/api/v1/teams/{team_with_manager.pk}/",
        {"managers_ids": [new_mgr.pk]},
        format="json",
    )
    assert response.status_code == 200, response.json()
    team_with_manager.refresh_from_db()
    assert new_mgr in team_with_manager.managers.all()


def test_PATCH_managers_ids_by_non_owner_manager_is_403(
    api_client, team_with_manager, manager_user
):
    """A manager who is not the owner must not change the management roster
    (self-escalation). managers_owner_only is raised."""
    api_client.force_authenticate(user=manager_user)
    intruder = User.objects.create_user(
        email="tm_intruder@local.test", password="pass"
    )
    response = api_client.patch(
        f"/api/v1/teams/{team_with_manager.pk}/",
        {"managers_ids": [manager_user.pk, intruder.pk]},
        format="json",
    )
    assert response.status_code == 400
    assert "managers_owner_only" in str(response.content)
    team_with_manager.refresh_from_db()
    assert intruder not in team_with_manager.managers.all()


def test_PATCH_other_fields_by_manager_still_allowed(api_client, team_with_manager, manager_user):
    """The roster lock must not block a manager from editing normal team fields."""
    api_client.force_authenticate(user=manager_user)
    response = api_client.patch(
        f"/api/v1/teams/{team_with_manager.pk}/",
        {"name": "Renamed by manager"},
        format="json",
    )
    assert response.status_code == 200, response.content
    team_with_manager.refresh_from_db()
    assert team_with_manager.name == "Renamed by manager"


def test_PATCH_with_old_managers_field_is_ignored(owner_client, team_with_manager):
    """`managers` is now read-only; PATCH on it should not mutate state."""
    new_mgr = User.objects.create_user(
        email="tm_snan@local.test", password="pass"
    )
    before = list(team_with_manager.managers.values_list("pk", flat=True))
    response = owner_client.patch(
        f"/api/v1/teams/{team_with_manager.pk}/",
        {"managers": [new_mgr.pk]},
        format="json",
    )
    # PATCH succeeds but managers field is read-only -> ignored
    assert response.status_code == 200
    team_with_manager.refresh_from_db()
    after = list(team_with_manager.managers.values_list("pk", flat=True))
    assert after == before

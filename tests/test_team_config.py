"""Coverage of Team config toggles (weekly_recap_enabled, roti_enabled, logo).

These fields are owner/manager-editable via PATCH /api/v1/teams/{id}/ and
surface on TeamSerializer (full); they are intentionally NOT on
TeamMinimalSerializer (kept lean for nested read contexts).
"""

import pytest

from tests.factories import TeamFactory

pytestmark = pytest.mark.django_db


# ----------------------------- defaults DB --------------------------


def test_team_default_weekly_recap_enabled_is_false():
    team = TeamFactory()
    assert team.weekly_recap_enabled is False


# ----------------------------- serializer exposure ------------------


def test_team_serializer_exposes_recap_flag(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    response = auth_client_trainer.get(f"/api/v1/teams/{team.pk}/")
    assert response.status_code == 200
    body = response.json()
    assert "weekly_recap_enabled" in body
    assert body["weekly_recap_enabled"] is False


# ----------------------------- write permissions --------------------


def test_team_manager_can_patch_weekly_recap_enabled(
    api_client, trainer_user, authenticated_user
):
    team = trainer_user.owned_teams.first()
    team.managers.add(authenticated_user)
    api_client.force_authenticate(user=authenticated_user)
    response = api_client.patch(
        f"/api/v1/teams/{team.pk}/",
        {"weekly_recap_enabled": True},
        format="json",
    )
    assert response.status_code == 200
    team.refresh_from_db()
    assert team.weekly_recap_enabled is True


def test_team_random_user_cannot_patch_config(api_client, trainer_user, non_trainer_user):
    """A random authenticated user (neither owner nor manager) is
    blocked: 403 if the team is public/visible, 404 if private."""
    team = trainer_user.owned_teams.first()
    team.is_public = True
    team.save(update_fields=["is_public"])
    api_client.force_authenticate(user=non_trainer_user)
    response = api_client.patch(
        f"/api/v1/teams/{team.pk}/",
        {"weekly_recap_enabled": True},
        format="json",
    )
    assert response.status_code == 403


# ----------------------------- DELETE owner-only --------------------


def test_team_owner_can_delete_team(auth_client_trainer, trainer_user):
    from team.models import Team

    team = trainer_user.owned_teams.first()
    response = auth_client_trainer.delete(f"/api/v1/teams/{team.pk}/")
    assert response.status_code == 204
    assert not Team.objects.filter(pk=team.pk).exists()


def test_team_manager_cannot_delete_team(api_client, trainer_user, authenticated_user):
    """A manager (non-owner) cannot DELETE the team."""
    from team.models import Team

    team = trainer_user.owned_teams.first()
    team.managers.add(authenticated_user)
    api_client.force_authenticate(user=authenticated_user)
    response = api_client.delete(f"/api/v1/teams/{team.pk}/")
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "owner_only_delete"
    # Team still exists
    assert Team.objects.filter(pk=team.pk).exists()


def test_team_manager_can_still_patch_team(api_client, trainer_user, authenticated_user):
    """Manager keeps PATCH access (only DELETE is owner-restricted)."""
    team = trainer_user.owned_teams.first()
    team.managers.add(authenticated_user)
    api_client.force_authenticate(user=authenticated_user)
    response = api_client.patch(
        f"/api/v1/teams/{team.pk}/",
        {"name": "Renamed by manager"},
        format="json",
    )
    assert response.status_code == 200
    team.refresh_from_db()
    assert team.name == "Renamed by manager"


# ----------------------------- logo (base64 data-URL) ---------------

_VALID_LOGO = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def test_team_default_logo_is_empty():
    team = TeamFactory()
    assert team.logo == ""


def test_team_serializer_exposes_logo(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    response = auth_client_trainer.get(f"/api/v1/teams/{team.pk}/")
    assert response.status_code == 200
    assert "logo" in response.json()


def test_team_owner_can_patch_valid_logo(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    response = auth_client_trainer.patch(
        f"/api/v1/teams/{team.pk}/",
        {"logo": _VALID_LOGO},
        format="json",
    )
    assert response.status_code == 200, response.json()
    team.refresh_from_db()
    assert team.logo == _VALID_LOGO


def test_team_patch_empty_logo_ok(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    team.logo = _VALID_LOGO
    team.save(update_fields=["logo"])
    response = auth_client_trainer.patch(
        f"/api/v1/teams/{team.pk}/",
        {"logo": ""},
        format="json",
    )
    assert response.status_code == 200
    team.refresh_from_db()
    assert team.logo == ""


def test_team_patch_invalid_logo_not_data_url_returns_400(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    response = auth_client_trainer.patch(
        f"/api/v1/teams/{team.pk}/",
        {"logo": "https://example.com/logo.png"},
        format="json",
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "validation_error"
    assert "logo" in body.get("fields", {})


def test_team_patch_oversized_logo_returns_400(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    oversized = "data:image/png;base64," + ("A" * 500001)
    response = auth_client_trainer.patch(
        f"/api/v1/teams/{team.pk}/",
        {"logo": oversized},
        format="json",
    )
    assert response.status_code == 400
    body = response.json()
    assert "logo" in body.get("fields", {})


# ----------------------------- roti_enabled toggle ------------------


def test_team_default_roti_enabled_is_false():
    team = TeamFactory()
    assert team.roti_enabled is False


def test_team_serializer_exposes_roti_enabled(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    response = auth_client_trainer.get(f"/api/v1/teams/{team.pk}/")
    assert response.status_code == 200
    body = response.json()
    assert "roti_enabled" in body
    assert body["roti_enabled"] is False


def test_team_owner_can_toggle_roti_enabled(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    response = auth_client_trainer.patch(
        f"/api/v1/teams/{team.pk}/",
        {"roti_enabled": True},
        format="json",
    )
    assert response.status_code == 200
    team.refresh_from_db()
    assert team.roti_enabled is True

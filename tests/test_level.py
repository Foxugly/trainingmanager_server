"""CRUD + permission tests for the Level taxonomy referential, plus
Team.level wiring and AI-prompt level injection.

Permission model mirrors the other catalog referentials
(Sport / AttendanceStatus):
  - Read  (SAFE_METHODS)  : any authenticated user.
  - Write (POST/PATCH/PUT/DELETE) : staff only.
  - DELETE soft-deletes (is_active = False).
  - Default queryset hides is_active=False.
  - ?include_inactive=true returns inactive items, only for staff.
  - Admin flavor (per-language name/description variants) for staff on
    create/update/partial_update/retrieve.
"""

import datetime

import pytest

from tests.factories import LevelFactory, ProgramFactory, TeamFactory

pytestmark = pytest.mark.django_db


# =====================================================================
# LEVEL — permissions
# =====================================================================


def test_GET_levels_as_authenticated_returns_200(auth_client):
    LevelFactory()
    response = auth_client.get("/api/v1/levels/")
    assert response.status_code == 200


def test_POST_level_as_non_staff_returns_403(auth_client):
    response = auth_client.post(
        "/api/v1/levels/",
        {"code": "lvl-nonstaff", "name_fr": "Débutant", "order": 1, "is_active": True},
        format="json",
    )
    assert response.status_code == 403


def test_POST_level_as_staff_returns_201(admin_client):
    response = admin_client.post(
        "/api/v1/levels/",
        {
            "code": "lvl-staff",
            "name_fr": "Découverte",
            "name_en": "Discovery",
            "description_fr": "Découvre l'activité",
            "order": 1,
            "is_active": True,
        },
        format="json",
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["code"] == "lvl-staff"
    assert body["name_fr"] == "Découverte"
    assert body["name_en"] == "Discovery"
    assert body["description_fr"] == "Découvre l'activité"


def test_PATCH_level_as_non_staff_returns_403(auth_client):
    level = LevelFactory(code="lvl-patch-nonstaff")
    response = auth_client.patch(
        f"/api/v1/levels/{level.pk}/",
        {"order": 9},
        format="json",
    )
    assert response.status_code == 403


def test_PATCH_level_as_staff_returns_200(admin_client):
    level = LevelFactory(code="lvl-patch-staff")
    response = admin_client.patch(
        f"/api/v1/levels/{level.pk}/",
        {"name_nl": "Ontdekking"},
        format="json",
    )
    assert response.status_code == 200
    level.refresh_from_db()
    assert level.name_nl == "Ontdekking"


# =====================================================================
# LEVEL — soft delete + include_inactive
# =====================================================================


def test_DELETE_level_as_staff_soft_deletes(admin_client):
    level = LevelFactory(code="lvl-del-staff", is_active=True)
    response = admin_client.delete(f"/api/v1/levels/{level.pk}/")
    assert response.status_code == 204
    level.refresh_from_db()
    assert level.is_active is False


def test_GET_levels_default_excludes_inactive(auth_client):
    LevelFactory(code="lvl-inactive", is_active=False)
    LevelFactory(code="lvl-active", is_active=True)
    response = auth_client.get("/api/v1/levels/")
    codes = {item["code"] for item in response.json()["results"]}
    assert "lvl-active" in codes
    assert "lvl-inactive" not in codes


def test_GET_levels_include_inactive_as_staff_returns_inactive(admin_client):
    LevelFactory(code="lvl-inactive-staff-view", is_active=False)
    response = admin_client.get("/api/v1/levels/?include_inactive=true")
    codes = {item["code"] for item in response.json()["results"]}
    assert "lvl-inactive-staff-view" in codes


def test_GET_levels_include_inactive_as_non_staff_ignores_param(auth_client):
    LevelFactory(code="lvl-inactive-nonstaff-view", is_active=False)
    response = auth_client.get("/api/v1/levels/?include_inactive=true")
    codes = {item["code"] for item in response.json()["results"]}
    assert "lvl-inactive-nonstaff-view" not in codes


# =====================================================================
# LEVEL — serializer flavors
# =====================================================================


def test_GET_level_as_staff_returns_admin_serializer_with_variants(admin_client):
    level = LevelFactory(code="lvl-flavor-admin")
    level.name_en = "Discovery"
    level.save()
    response = admin_client.get(f"/api/v1/levels/{level.pk}/")
    body = response.json()
    assert "name_fr" in body
    assert "description_en" in body
    assert body["name_en"] == "Discovery"


def test_GET_level_as_non_staff_returns_public_serializer_no_variants(auth_client):
    level = LevelFactory(code="lvl-flavor-public")
    response = auth_client.get(f"/api/v1/levels/{level.pk}/")
    body = response.json()
    assert "name" in body
    assert "description" in body
    assert "name_fr" not in body
    assert "description_en" not in body


# =====================================================================
# TEAM.level wiring
# =====================================================================


def test_PATCH_team_with_level_id_sets_level(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    level = LevelFactory(code="lvl-team-patch")
    response = auth_client_trainer.patch(
        f"/api/v1/teams/{team.pk}/",
        {"level_id": level.pk},
        format="json",
    )
    assert response.status_code == 200, response.json()
    team.refresh_from_db()
    assert team.level_id == level.pk


def test_PATCH_team_level_id_null_clears_level(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    level = LevelFactory(code="lvl-team-clear")
    team.level = level
    team.save()
    response = auth_client_trainer.patch(
        f"/api/v1/teams/{team.pk}/",
        {"level_id": None},
        format="json",
    )
    assert response.status_code == 200, response.json()
    team.refresh_from_db()
    assert team.level_id is None


def test_GET_team_returns_nested_level(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    level = LevelFactory(code="lvl-team-nested", name="Compétition")
    team.level = level
    team.save()
    response = auth_client_trainer.get(f"/api/v1/teams/{team.pk}/")
    assert response.status_code == 200
    body = response.json()
    assert body["level"] is not None
    assert body["level"]["code"] == "lvl-team-nested"
    assert body["level"]["name"] == "Compétition"


# =====================================================================
# AI prompt injection
# =====================================================================


def test_event_ai_prompt_includes_level(db):
    from event.ai import build_user_prompt
    from tests.factories import EventFactory

    level = LevelFactory(code="lvl-ai-event", name="Perfectionnement", description="Maîtrise")
    team = TeamFactory(level=level)
    program = ProgramFactory(team=team)
    event = EventFactory(refer_program=program, name="Séance", goal="Vitesse")
    prompt = build_user_prompt(
        event=event,
        modalities_catalog=[{"id": 1, "name": "Crawl"}],
        energysegments_catalog=[{"id": 1, "abv": "A1"}],
        team=team,
    )
    assert "Team skill level: Perfectionnement — Maîtrise" in prompt


def test_program_ai_prompt_includes_level(db):
    from program.ai import build_user_prompt

    level = LevelFactory(code="lvl-ai-plan", name="Compétition", description="Performance")
    team = TeamFactory(level=level)
    prompt = build_user_prompt(
        sport_name="Natation",
        language="fr",
        date_start=datetime.date(2026, 1, 1),
        date_end=datetime.date(2026, 1, 31),
        frequency_per_week=3,
        description="",
        team=team,
    )
    assert "Team skill level: Compétition — Performance" in prompt


def test_ai_prompt_omits_level_when_absent(db):
    from program.ai import build_user_prompt

    team = TeamFactory(level=None)
    prompt = build_user_prompt(
        sport_name="Natation",
        language="fr",
        date_start=datetime.date(2026, 1, 1),
        date_end=datetime.date(2026, 1, 31),
        frequency_per_week=3,
        description="",
        team=team,
    )
    assert "Team skill level" not in prompt

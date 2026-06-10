import pytest

from tests.factories import (
    EnergySegmentFactory,
    EnergySystemFactory,
    EventFactory,
    MemberFactory,
    ModalityFactory,
    ProgramFactory,
    SportFactory,
)

pytestmark = pytest.mark.django_db


# ------------------------------- /me/ --------------------------------


def test_GET_me_authenticated_returns_200(auth_client, authenticated_user):
    response = auth_client.get("/api/v1/me/")
    assert response.status_code == 200
    assert response.json()["username"] == authenticated_user.username


def test_PATCH_me_updates_first_name(auth_client):
    response = auth_client.patch("/api/v1/me/", {"first_name": "Updated"}, format="json")
    assert response.status_code == 200
    assert response.json()["first_name"] == "Updated"


def test_GET_me_unauthenticated_returns_401(api_client):
    response = api_client.get("/api/v1/me/")
    assert response.status_code == 401


def test_GET_me_member_id_is_null_when_no_member(auth_client):
    """member_id exposes the caller's linked Member id; null when unlinked."""
    response = auth_client.get("/api/v1/me/")
    assert response.status_code == 200
    body = response.json()
    assert "member_id" in body
    assert body["member_id"] is None


def test_GET_me_member_id_returns_linked_member(auth_client, authenticated_user):
    from member.models import Member

    member = Member.objects.create(
        firstname="Me", lastname="Mber", user=authenticated_user
    )
    response = auth_client.get("/api/v1/me/")
    assert response.status_code == 200
    assert response.json()["member_id"] == member.id


def test_GET_me_exposes_is_staff_readonly_but_not_is_superuser(auth_client):
    """is_staff is exposed READ-ONLY so the SPA can gate its admin back-office;
    is_superuser is never exposed. Server-side permissions still enforce every
    admin endpoint regardless of this flag."""
    response = auth_client.get("/api/v1/me/")
    assert response.status_code == 200
    body = response.json()
    assert "is_staff" in body
    assert body["is_staff"] is False
    assert "is_superuser" not in body


def test_PATCH_me_cannot_promote_to_staff(auth_client, authenticated_user):
    """A regular user must not be able to escalate via PATCH /me/."""
    assert authenticated_user.is_staff is False
    assert authenticated_user.is_superuser is False

    response = auth_client.patch(
        "/api/v1/me/",
        {"is_staff": True, "is_superuser": True, "first_name": "Hacker"},
        format="json",
    )
    assert response.status_code == 200
    body = response.json()
    # is_staff is exposed but READ-ONLY: the attempted promotion is silently
    # ignored and the response still reports the original (non-staff) value.
    assert body["is_staff"] is False
    # is_superuser is never serialized.
    assert "is_superuser" not in body
    # first_name update should still go through (proves PATCH wasn't blocked
    # entirely; only the privileged fields were silently ignored).
    assert body["first_name"] == "Hacker"

    authenticated_user.refresh_from_db()
    assert authenticated_user.is_staff is False
    assert authenticated_user.is_superuser is False


def test_PATCH_me_cannot_change_email(auth_client, authenticated_user):
    """C2 fix: email is read-only on /me/. Silently ignored on PATCH.

    Allowing direct email mutation without verification was a takeover
    vector — combined with C1 (existing-user invitations), an attacker
    could swap their email to receive invitations destined for someone
    else. Until a verified change-email flow lands (v2, deferred),
    email changes go through admin only.
    """
    original_email = authenticated_user.email
    response = auth_client.patch(
        "/api/v1/me/",
        {"email": "attacker@evil.test", "first_name": "StillUpdated"},
        format="json",
    )
    assert response.status_code == 200
    body = response.json()
    # email in response reflects the unchanged stored value
    assert body["email"] == original_email
    # other writable fields are still updatable in the same request
    assert body["first_name"] == "StillUpdated"

    authenticated_user.refresh_from_db()
    assert authenticated_user.email == original_email


def test_PUT_me_returns_405(auth_client):
    """C2 fix: PUT is disabled on /me/. PATCH only.

    PUT with a partial body would have reset unspecified writable fields
    (first_name, last_name, language) to their model defaults — a footgun
    for any client that thinks PUT is "the same as PATCH but full body"."""
    response = auth_client.put(
        "/api/v1/me/",
        {"first_name": "ShouldNotApply"},
        format="json",
    )
    assert response.status_code == 405


# ------------------------------ /teams/ ------------------------------


def test_GET_teams_authenticated_returns_200(auth_client):
    response = auth_client.get("/api/v1/teams/")
    assert response.status_code == 200


def test_POST_teams_creates_with_caller_as_owner(auth_client, authenticated_user):
    # CustomUser.team_quota defaults to 0 — bump for this smoke test
    # (quota is validated in tests/test_team_quota.py; here we just want
    # to assert that a successful create assigns owner=request.user).
    authenticated_user.team_quota = 1
    authenticated_user.save(update_fields=["team_quota"])
    sport = SportFactory()
    response = auth_client.post(
        "/api/v1/teams/",
        {
            "name": "New Team Smoke",
            "sport_id": sport.pk,
            "is_active": True,
            "is_public": False,
            "managers": [],
        },
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["owner"]["id"] == authenticated_user.pk


def test_POST_teams_without_sport_returns_400(auth_client):
    response = auth_client.post(
        "/api/v1/teams/",
        {"name": "No sport", "is_active": True, "is_public": False, "managers": []},
        format="json",
    )
    assert response.status_code == 400


def test_GET_team_owner_is_nested_in_response(auth_client, user_team, authenticated_user):
    response = auth_client.get(f"/api/v1/teams/{user_team.pk}/")
    assert response.status_code == 200
    body = response.json()
    assert body["owner"]["id"] == authenticated_user.pk
    assert body["owner"]["username"] == authenticated_user.username
    assert isinstance(body["sport"], dict)
    assert body["sport"]["id"] == user_team.sport.pk


def test_GET_team_detail_returns_200(auth_client, user_team):
    response = auth_client.get(f"/api/v1/teams/{user_team.pk}/")
    assert response.status_code == 200
    assert response.json()["id"] == user_team.pk


# ----------------------------- /programs/ -----------------------------


def test_GET_programs_returns_200(auth_client):
    response = auth_client.get("/api/v1/programs/")
    assert response.status_code == 200


def test_POST_programs_with_owned_team_returns_201(auth_client, user_team):
    response = auth_client.post(
        "/api/v1/programs/",
        {"name": "Smoke Program", "team_id": user_team.pk},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["team"]["id"] == user_team.pk


def test_GET_program_detail_returns_200(auth_client, user_team):
    program = ProgramFactory(team=user_team)
    response = auth_client.get(f"/api/v1/programs/{program.pk}/")
    assert response.status_code == 200


def test_DELETE_program_returns_204(auth_client, user_team):
    program = ProgramFactory(team=user_team)
    response = auth_client.delete(f"/api/v1/programs/{program.pk}/")
    assert response.status_code == 204


# ------------------------------ /events/ -----------------------------


def test_GET_events_returns_200(auth_client):
    response = auth_client.get("/api/v1/events/")
    assert response.status_code == 200


def test_POST_events_with_valid_program_returns_201(auth_client, user_team):
    program = ProgramFactory(team=user_team)
    response = auth_client.post(
        "/api/v1/events/",
        {"name": "Smoke Event", "refer_program_id": program.pk},
        format="json",
    )
    assert response.status_code == 201


# ------------------------------ /rounds/ -----------------------------


def test_GET_rounds_returns_200(auth_client):
    response = auth_client.get("/api/v1/rounds/")
    assert response.status_code == 200


def test_POST_rounds_returns_201(auth_client_trainer):
    sport = SportFactory()
    response = auth_client_trainer.post(
        "/api/v1/rounds/",
        {"order": 1, "count": 1, "sport_id": sport.pk, "language": "fr"},
        format="json",
    )
    assert response.status_code == 201


# --------------------------- /exercises/ -----------------------------


def test_GET_exercises_returns_200(auth_client):
    response = auth_client.get("/api/v1/exercises/")
    assert response.status_code == 200


def test_POST_exercises_returns_201(auth_client_trainer):
    response = auth_client_trainer.post(
        "/api/v1/exercises/",
        {"order": 1, "repetition": 1, "distance": 100, "language": "fr"},
        format="json",
    )
    assert response.status_code == 201


# --------- /sports/, nested modalities/, energy-systems/, energy-segments/ (RO) ---------


def test_GET_sports_returns_200(auth_client):
    SportFactory()
    response = auth_client.get("/api/v1/sports/")
    assert response.status_code == 200


def test_GET_nested_modalities_returns_200(auth_client):
    sport = SportFactory()
    ModalityFactory(sport=sport)
    response = auth_client.get(f"/api/v1/sports/{sport.pk}/modalities/")
    assert response.status_code == 200


def test_POST_nested_modalities_as_non_staff_returns_403(auth_client):
    sport = SportFactory()
    response = auth_client.post(
        f"/api/v1/sports/{sport.pk}/modalities/", {"name": "Foo"}, format="json"
    )
    assert response.status_code == 403


def test_GET_energy_systems_returns_200(auth_client):
    EnergySystemFactory()
    response = auth_client.get("/api/v1/energy-systems/")
    assert response.status_code == 200


def test_GET_energy_segments_returns_200(auth_client):
    EnergySegmentFactory()
    response = auth_client.get("/api/v1/energy-segments/")
    assert response.status_code == 200


# ----------------------------- /members/ -----------------------------


def test_GET_members_returns_200(auth_client):
    response = auth_client.get("/api/v1/members/")
    assert response.status_code == 200


def test_POST_members_with_owned_team_returns_201(auth_client, user_team):
    """user_team makes auth_user a manager => Member create allowed.

    Membership attachment is now done via /teams/{team_pk}/memberships/.
    """
    response = auth_client.post(
        "/api/v1/members/",
        {
            "firstname": "Smoke",
            "lastname": "Tester",
            "email": "smoke.tester@local.test",
        },
        format="json",
    )
    assert response.status_code == 201


# ----------------------- schema and docs ----------------------------
# The schema + Swagger UI are DEBUG-only (registered in urls.py under
# `if settings.DEBUG`). pytest-django runs with DEBUG=False (prod-like), so the
# routes are absent here — exactly the prod behavior we want: the full API
# surface is NOT publicly enumerable. The typed frontend client is generated
# from the committed openapi-schema.yaml via the `spectacular` management
# command, not this endpoint.


def test_GET_schema_not_exposed_in_prod(api_client):
    assert api_client.get("/api/v1/schema/").status_code == 404


def test_GET_docs_not_exposed_in_prod(api_client):
    assert api_client.get("/api/v1/docs/").status_code == 404


# ------------------ filtering / search / ordering -------------------


def test_GET_programs_with_ordering_returns_200(auth_client, user_team):
    ProgramFactory.create_batch(3, team=user_team)
    response = auth_client.get("/api/v1/programs/?ordering=name")
    assert response.status_code == 200


def test_GET_members_with_search_returns_200(auth_client, user_team):
    MemberFactory(firstname="Searchable", teams=[user_team])
    response = auth_client.get("/api/v1/members/?search=Searchable")
    assert response.status_code == 200


def test_GET_events_filtered_by_refer_program_returns_200(auth_client, user_team):
    program = ProgramFactory(team=user_team)
    EventFactory(refer_program=program)
    response = auth_client.get(f"/api/v1/events/?refer_program={program.pk}")
    assert response.status_code == 200

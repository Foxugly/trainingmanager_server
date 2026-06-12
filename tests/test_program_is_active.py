"""Soft-delete and is_active tests for Program (team-scoped variant).

Convention transposed from the staff-managed referentials (Sport, Modality,
EnergySystem, EnergySegment) but adapted to Program's team-scoped permission
model:

  - Read   : any user who can see the team (user_visible_teams)
  - Write  : only managers of the team (_check_team_write)
  - DELETE : soft delete (is_active=False), manager-of-team only
  - Default queryset hides is_active=False
  - ?include_inactive=true returns inactive programs to:
      - staff (across all visible teams)
      - managers (only for the teams they manage)
    and is silently ignored otherwise.
"""

import pytest
from django.contrib.auth import get_user_model

from member.models import Member
from team.models import TeamMembership
from tests.factories import ProgramFactory, TeamFactory

pytestmark = pytest.mark.django_db


User = get_user_model()


# ---------- Helpers -------------------------------------------------------


def _make_user(username):
    return User.objects.create_user(
        email=f"{username}@local.test",
        password="Str0ngP@ssTest!",
    )


def _attach_athlete(user, team):
    member = Member.objects.create(
        firstname="Ath",
        lastname=user.email,
        email=user.email,
        user=user,
    )
    TeamMembership.objects.create(team=team, member=member)
    return member


# =====================================================================
# Default queryset hides inactive programs
# =====================================================================


def test_GET_programs_default_excludes_inactive(api_client):
    owner = _make_user("owner_excl")
    api_client.force_authenticate(user=owner)
    team = TeamFactory(owner=owner)
    ProgramFactory(team=team, name="active-default", is_active=True)
    ProgramFactory(team=team, name="inactive-default", is_active=False)

    response = api_client.get("/api/v1/programs/")
    assert response.status_code == 200
    names = {p["name"] for p in response.json()["results"]}
    assert "active-default" in names
    assert "inactive-default" not in names


# =====================================================================
# include_inactive=true — manager scope
# =====================================================================


def test_manager_sees_archived_programs_of_their_team_with_include_inactive(api_client):
    manager = _make_user("mgr_own")
    api_client.force_authenticate(user=manager)
    team = TeamFactory(owner=manager)
    ProgramFactory(team=team, name="own-active", is_active=True)
    ProgramFactory(team=team, name="own-archived", is_active=False)

    response = api_client.get("/api/v1/programs/?include_inactive=true")
    assert response.status_code == 200
    names = {p["name"] for p in response.json()["results"]}
    assert names == {"own-active", "own-archived"}


def test_manager_does_not_see_archived_of_teams_they_do_not_manage(api_client):
    """Manager of team_a uses ?include_inactive=true. Programs are scoped
    via user_member_teams (strict, no public-team cascade), so team_b
    programs (whether active or archived) are invisible — the manager
    only sees programs of teams they are an actual member of."""
    manager_a = _make_user("mgr_a")
    api_client.force_authenticate(user=manager_a)
    team_a = TeamFactory(owner=manager_a)
    other_owner = _make_user("other_owner")
    team_b = TeamFactory(owner=other_owner, is_public=True, is_active=True)

    ProgramFactory(team=team_a, name="a-archived", is_active=False)
    ProgramFactory(team=team_b, name="b-active", is_active=True)
    ProgramFactory(team=team_b, name="b-archived", is_active=False)

    response = api_client.get("/api/v1/programs/?include_inactive=true")
    assert response.status_code == 200
    names = {p["name"] for p in response.json()["results"]}
    assert names == {"a-archived"}


# =====================================================================
# include_inactive=true — athlete is silently ignored
# =====================================================================


def test_athlete_does_not_see_archived_even_with_include_inactive(api_client):
    owner = _make_user("owner_for_athlete")
    team = TeamFactory(owner=owner)
    athlete = _make_user("athlete_user")
    _attach_athlete(athlete, team)
    ProgramFactory(team=team, name="visible-active", is_active=True)
    ProgramFactory(team=team, name="hidden-archived", is_active=False)

    api_client.force_authenticate(user=athlete)
    response = api_client.get("/api/v1/programs/?include_inactive=true")
    assert response.status_code == 200
    names = {p["name"] for p in response.json()["results"]}
    assert names == {"visible-active"}


# =====================================================================
# include_inactive=true — staff sees inactives across all visible teams
# =====================================================================


def test_staff_sees_archived_programs_of_their_visible_teams(api_client):
    staff = User.objects.create_user(
        email="staff_seer@local.test",
        password="Str0ngP@ssStaff!",
        is_staff=True,
    )
    api_client.force_authenticate(user=staff)
    team = TeamFactory(owner=staff)
    ProgramFactory(team=team, name="staff-archived", is_active=False)

    response = api_client.get("/api/v1/programs/?include_inactive=true")
    assert response.status_code == 200
    names = {p["name"] for p in response.json()["results"]}
    assert "staff-archived" in names


# =====================================================================
# Soft delete via PATCH and via DELETE
# =====================================================================


def test_PATCH_program_is_active_false_by_manager_works(api_client):
    manager = _make_user("mgr_patch")
    api_client.force_authenticate(user=manager)
    team = TeamFactory(owner=manager)
    program = ProgramFactory(team=team, is_active=True)

    response = api_client.patch(
        f"/api/v1/programs/{program.pk}/",
        {"is_active": False},
        format="json",
    )
    assert response.status_code == 200
    program.refresh_from_db()
    assert program.is_active is False


def test_DELETE_program_by_manager_soft_deletes(api_client):
    from program.models import Program

    manager = _make_user("mgr_del")
    api_client.force_authenticate(user=manager)
    team = TeamFactory(owner=manager)
    program = ProgramFactory(team=team, is_active=True)

    response = api_client.delete(f"/api/v1/programs/{program.pk}/")
    assert response.status_code == 204
    program.refresh_from_db()
    assert program.is_active is False
    assert Program.objects.filter(pk=program.pk).exists()


def test_DELETE_program_by_athlete_returns_403(api_client):
    """Athlete is a member of the team (so passes get_queryset visibility),
    but is not a manager, so perform_destroy must reject with 403."""
    owner = _make_user("owner_for_athlete_delete")
    team = TeamFactory(owner=owner)
    athlete = _make_user("athlete_delete")
    _attach_athlete(athlete, team)
    program = ProgramFactory(team=team, is_active=True)

    api_client.force_authenticate(user=athlete)
    response = api_client.delete(f"/api/v1/programs/{program.pk}/")
    assert response.status_code == 403
    program.refresh_from_db()
    assert program.is_active is True


# =====================================================================
# Program.events is now read-only (FK reverse manager — Program.events M2M
# was dropped in migration 0008). Cross-team attachment must use the
# canonical write path: setting Event.refer_program. The legacy C4 fix
# (scoped writable queryset) is no longer needed.
# =====================================================================


def test_PATCH_program_events_field_is_read_only(api_client):
    """`events` cannot be written via PATCH /programs/{id}/. DRF silently
    ignores writes to read-only fields; cross-team contamination is
    structurally impossible because the field has no write path."""
    from event.models import Event

    manager_a = _make_user("c4_mgr_a")
    team_a = TeamFactory(owner=manager_a)
    program_a = ProgramFactory(team=team_a, is_active=True)

    other_owner = _make_user("c4_other")
    team_b = TeamFactory(owner=other_owner)
    program_b = ProgramFactory(team=team_b, is_active=True)
    foreign_event = Event.objects.create(refer_program=program_b, name="foreign")

    api_client.force_authenticate(user=manager_a)
    response = api_client.patch(
        f"/api/v1/programs/{program_a.pk}/",
        {"events": [foreign_event.pk]},
        format="json",
    )
    # Read-only field => 200 with the payload silently ignored.
    assert response.status_code == 200
    program_a.refresh_from_db()
    assert not program_a.events.filter(pk=foreign_event.pk).exists()


def test_program_events_reflects_FK_reverse(api_client):
    """`program.events` (FK reverse manager) returns every Event whose
    refer_program points at it."""
    from event.models import Event

    manager = _make_user("c4_legit")
    team = TeamFactory(owner=manager)
    program = ProgramFactory(team=team, is_active=True)
    own_event = Event.objects.create(refer_program=program, name="own")

    api_client.force_authenticate(user=manager)
    response = api_client.get(f"/api/v1/programs/{program.pk}/")
    assert response.status_code == 200
    assert own_event.pk in response.json()["events"]

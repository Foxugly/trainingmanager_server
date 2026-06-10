from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from member.models import Member
from team.models import TeamInvitation, TeamMembership
from tests.factories import TeamFactory, UserFactory

pytestmark = pytest.mark.django_db


# ----------------------------- POST /invitations/ ----------------------------


def test_POST_create_invitation_as_trainer_returns_201(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    mail.outbox = []
    response = auth_client_trainer.post(
        "/api/v1/invitations/",
        {
            "team": team.pk,
            "email": "newathlete@local.test",
            "firstname": "New",
            "lastname": "Athlete",
        },
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "newathlete@local.test"
    assert "token" not in body
    assert TeamInvitation.objects.filter(
        email="newathlete@local.test", team=team, status="pending"
    ).exists()
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["newathlete@local.test"]
    assert "/invitation/" in mail.outbox[0].body


def test_POST_create_invitation_as_non_trainer_returns_403(auth_client_non_trainer):
    team = TeamFactory()
    response = auth_client_non_trainer.post(
        "/api/v1/invitations/",
        {
            "team": team.pk,
            "email": "someone@local.test",
            "firstname": "A",
            "lastname": "B",
        },
        format="json",
    )
    assert response.status_code == 403


def test_POST_create_invitation_existing_user_returns_400_no_db_changes(
    auth_client_trainer, trainer_user
):
    """C1 fix: refuse direct invitation when the email matches an existing user.

    Auto-creating Member + TeamMembership for an existing user without their
    consent was a hijack vector — any trainer could enrol any registered user
    in their team simply by knowing the email. We now require the trainer to
    use an alternate flow (e.g. ask the user to file a TeamJoinRequest)."""
    UserFactory(email="existing@local.test")
    team = trainer_user.owned_teams.first()
    mail.outbox = []
    response = auth_client_trainer.post(
        "/api/v1/invitations/",
        {
            "team": team.pk,
            "email": "existing@local.test",
            "firstname": "Exi",
            "lastname": "Sting",
        },
        format="json",
    )
    assert response.status_code == 400
    body = response.json()
    assert body.get("code") == "user_already_registered", body
    # No DB side effects on this branch: no Member, no TeamMembership, no Invitation.
    assert not Member.objects.filter(email="existing@local.test").exists()
    assert not TeamMembership.objects.filter(member__email="existing@local.test").exists()
    assert not TeamInvitation.objects.filter(email="existing@local.test").exists()
    # No email sent either (neither the signup link nor the legacy "you were
    # added" notification, which has been removed).
    assert mail.outbox == []


def test_POST_create_invitation_new_email_still_creates_invitation(
    auth_client_trainer, trainer_user
):
    """C1 fix non-regression: the new-email branch keeps working unchanged."""
    team = trainer_user.owned_teams.first()
    mail.outbox = []
    response = auth_client_trainer.post(
        "/api/v1/invitations/",
        {
            "team": team.pk,
            "email": "fresh@local.test",
            "firstname": "Fresh",
            "lastname": "User",
        },
        format="json",
    )
    assert response.status_code == 201
    assert TeamInvitation.objects.filter(
        email="fresh@local.test", team=team, status="pending"
    ).exists()
    # Member created (without user link, since no User exists for this email yet).
    member = Member.objects.get(email="fresh@local.test")
    assert member.user is None
    assert len(mail.outbox) == 1
    assert "/invitation/" in mail.outbox[0].body


def test_POST_create_invitation_duplicate_pending_returns_400(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    payload = {
        "team": team.pk,
        "email": "dup@local.test",
        "firstname": "D",
        "lastname": "P",
    }
    auth_client_trainer.post("/api/v1/invitations/", payload, format="json")
    response = auth_client_trainer.post("/api/v1/invitations/", payload, format="json")
    assert response.status_code == 400


def test_POST_create_invitation_unmanaged_team_returns_400(auth_client_trainer):
    other_team = TeamFactory()
    response = auth_client_trainer.post(
        "/api/v1/invitations/",
        {
            "team": other_team.pk,
            "email": "x@local.test",
            "firstname": "X",
            "lastname": "Y",
        },
        format="json",
    )
    assert response.status_code == 400


# ----------------------------- GET /invitations/ -----------------------------


def test_invitation_token_not_exposed_in_list(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    member = Member.objects.create(firstname="F", lastname="L", email="f@local.test")
    TeamMembership.objects.create(team=team, member=member)
    TeamInvitation.objects.create(team=team, member=member, email="f@local.test")
    response = auth_client_trainer.get("/api/v1/invitations/")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 1
    for r in body["results"]:
        assert "token" not in r


# ----------------------- GET /invitations/lookup/<token>/ --------------------


def test_GET_invitation_lookup_valid_token_returns_200(api_client, trainer_user):
    team = trainer_user.owned_teams.first()
    member = Member.objects.create(firstname="Lu", lastname="Up", email="lu@local.test")
    TeamMembership.objects.create(team=team, member=member)
    inv = TeamInvitation.objects.create(team=team, member=member, email="lu@local.test")
    response = api_client.get(f"/api/v1/invitations/lookup/{inv.token}/")
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "lu@local.test"
    assert body["team_name"] == team.name
    assert body["status"] == "pending"


def test_GET_invitation_lookup_invalid_token_returns_404(api_client):
    response = api_client.get("/api/v1/invitations/lookup/nope-no-such-token/")
    assert response.status_code == 404


def test_GET_invitation_lookup_expired_returns_410(api_client, trainer_user):
    team = trainer_user.owned_teams.first()
    member = Member.objects.create(firstname="Ex", lastname="Pired", email="ex@local.test")
    TeamMembership.objects.create(team=team, member=member)
    inv = TeamInvitation.objects.create(
        team=team,
        member=member,
        email="ex@local.test",
        expires_at=timezone.now() - timedelta(days=1),
    )
    response = api_client.get(f"/api/v1/invitations/lookup/{inv.token}/")
    assert response.status_code == 410
    inv.refresh_from_db()
    assert inv.status == "expired"


# --------------------- POST /invitations/lookup/<token>/ ---------------------


def test_POST_complete_invitation_creates_user_and_jwt(api_client, trainer_user):
    team = trainer_user.owned_teams.first()
    member = Member.objects.create(firstname="Co", lastname="Mplete", email="co@local.test")
    TeamMembership.objects.create(team=team, member=member)
    inv = TeamInvitation.objects.create(team=team, member=member, email="co@local.test")

    response = api_client.post(
        f"/api/v1/invitations/lookup/{inv.token}/",
        {"username": "newathlete", "password": "StrongPass!2026"},
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "newathlete"
    assert "access" in body
    assert "refresh" in body

    inv.refresh_from_db()
    assert inv.status == "completed"
    assert inv.completed_at is not None

    member.refresh_from_db()
    assert member.user is not None
    assert member.user.username == "newathlete"

    from allauth.account.models import EmailAddress

    ea = EmailAddress.objects.get(user=member.user, email="co@local.test")
    assert ea.verified is True
    assert ea.primary is True


def test_POST_complete_invitation_adds_member_to_team_roster(api_client, trainer_user):
    """B-P1: finalising an invitation must put the athlete on the roster
    (TeamMembership) AND attach them to the team's future events — the whole
    point of the invitation. The invitation flow creates a Member with NO
    membership; completion must create it."""
    from datetime import timedelta

    from django.utils import timezone

    from event.models import Event
    from program.models import Program

    team = trainer_user.owned_teams.first()
    member = Member.objects.create(firstname="Ros", lastname="Ter", email="ros@local.test")
    inv = TeamInvitation.objects.create(team=team, member=member, email="ros@local.test")
    assert not TeamMembership.objects.filter(team=team, member=member).exists()

    program = Program.objects.create(name="P", team=team)
    future = Event.objects.create(
        refer_program=program, name="F", date=timezone.localdate() + timedelta(days=3)
    )

    resp = api_client.post(
        f"/api/v1/invitations/lookup/{inv.token}/",
        {"username": "rosterath", "password": "StrongPass!2026"},
        format="json",
    )
    assert resp.status_code == 201, resp.json()

    assert (
        TeamMembership.objects.filter(team=team, member=member, left_at__isnull=True).count() == 1
    )
    assert future.members.filter(pk=member.pk).exists()


def test_POST_complete_invitation_invalid_username_returns_400(api_client, trainer_user):
    UserFactory(username="taken")
    team = trainer_user.owned_teams.first()
    member = Member.objects.create(firstname="F", lastname="L", email="dup@local.test")
    TeamMembership.objects.create(team=team, member=member)
    inv = TeamInvitation.objects.create(team=team, member=member, email="dup@local.test")

    response = api_client.post(
        f"/api/v1/invitations/lookup/{inv.token}/",
        {"username": "taken", "password": "StrongPass!2026"},
        format="json",
    )
    assert response.status_code == 400


def test_POST_complete_invitation_weak_password_returns_400(api_client, trainer_user):
    team = trainer_user.owned_teams.first()
    member = Member.objects.create(firstname="F", lastname="L", email="weak@local.test")
    TeamMembership.objects.create(team=team, member=member)
    inv = TeamInvitation.objects.create(team=team, member=member, email="weak@local.test")

    response = api_client.post(
        f"/api/v1/invitations/lookup/{inv.token}/",
        {"username": "someone", "password": "short"},
        format="json",
    )
    assert response.status_code == 400


def test_POST_complete_invitation_username_taken_case_insensitive_returns_400(
    api_client, trainer_user
):
    """Regression: username uniqueness must be case-insensitive (consistent with
    RegisterSerializer). A bare filter(username=...) would let "Taken" through
    even though "taken" exists, risking a near-duplicate / a 500 on a DB-level
    case-insensitive unique constraint."""
    UserFactory(username="taken")
    team = trainer_user.owned_teams.first()
    member = Member.objects.create(firstname="F", lastname="L", email="ci@local.test")
    TeamMembership.objects.create(team=team, member=member)
    inv = TeamInvitation.objects.create(team=team, member=member, email="ci@local.test")

    response = api_client.post(
        f"/api/v1/invitations/lookup/{inv.token}/",
        {"username": "Taken", "password": "StrongPass!2026"},
        format="json",
    )
    assert response.status_code == 400
    assert response.json().get("username", [{}])
    # The would-be user was never created.
    from django.contrib.auth import get_user_model

    assert get_user_model().objects.filter(username__iexact="taken").count() == 1


def test_POST_complete_invitation_email_already_taken_returns_409(api_client, trainer_user):
    """Regression: if the invitation email got claimed (User/EmailAddress)
    between sending and finalisation, completing must return a clean 409
    (code=email_taken) instead of a 500 from create_user's IntegrityError."""
    team = trainer_user.owned_teams.first()
    member = Member.objects.create(firstname="F", lastname="L", email="claimed@local.test")
    TeamMembership.objects.create(team=team, member=member)
    inv = TeamInvitation.objects.create(team=team, member=member, email="claimed@local.test")
    # Someone registers with the invitation email after the invite was sent
    # (case-insensitive match).
    UserFactory(email="Claimed@local.test", username="already")

    response = api_client.post(
        f"/api/v1/invitations/lookup/{inv.token}/",
        {"username": "freshname", "password": "StrongPass!2026"},
        format="json",
    )
    assert response.status_code == 409, response.json()
    assert response.json().get("code") == "email_taken"
    # The invitation is untouched and no second user/member link was created.
    inv.refresh_from_db()
    assert inv.status == "pending"
    member.refresh_from_db()
    assert member.user is None


def test_POST_complete_invitation_already_used_returns_400(api_client, trainer_user):
    team = trainer_user.owned_teams.first()
    member = Member.objects.create(firstname="F", lastname="L", email="used@local.test")
    TeamMembership.objects.create(team=team, member=member)
    inv = TeamInvitation.objects.create(
        team=team,
        member=member,
        email="used@local.test",
        status="completed",
    )

    response = api_client.post(
        f"/api/v1/invitations/lookup/{inv.token}/",
        {"username": "someone2", "password": "StrongPass!2026"},
        format="json",
    )
    assert response.status_code == 400


# ---------------------- DELETE /invitations/{id}/ ----------------------------


def test_DELETE_invitation_cancels_it(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    member = Member.objects.create(firstname="D", lastname="El", email="del@local.test")
    TeamMembership.objects.create(team=team, member=member)
    inv = TeamInvitation.objects.create(team=team, member=member, email="del@local.test")

    response = auth_client_trainer.delete(f"/api/v1/invitations/{inv.pk}/")
    # ModelViewSet's destroy returns 204
    assert response.status_code == 204
    inv.refresh_from_db()
    assert inv.status == "cancelled"

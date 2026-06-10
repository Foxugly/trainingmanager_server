import pytest
from django.core import mail

from member.models import Member
from team.models import TeamJoinRequest, TeamMembership
from tests.factories import TeamFactory, UserFactory

pytestmark = pytest.mark.django_db


# ----------------------------- POST ----------------------------------


def test_POST_join_request_returns_201(auth_client, authenticated_user):
    team = TeamFactory(is_active=True, is_public=True)
    response = auth_client.post(
        "/api/v1/join-requests/",
        {"team": team.pk, "message": "I want to join"},
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["team"] == team.pk
    assert TeamJoinRequest.objects.filter(
        user=authenticated_user, team=team, status="pending"
    ).exists()


def test_POST_join_request_sends_one_email_per_manager(auth_client):
    """One email per recipient (so each is rendered in the recipient's
    own language). Owner deduped if also in managers."""
    owner = UserFactory(email="owner@local.test")
    mgr = UserFactory(email="manager@local.test")
    team = TeamFactory(owner=owner, is_active=True, is_public=True)
    team.managers.add(mgr)

    mail.outbox = []
    response = auth_client.post(
        "/api/v1/join-requests/",
        {"team": team.pk, "message": "hi"},
        format="json",
    )
    assert response.status_code == 201
    assert len(mail.outbox) == 2
    addresses = {tuple(m.to) for m in mail.outbox}
    assert addresses == {("owner@local.test",), ("manager@local.test",)}


def test_POST_join_request_already_member_returns_400(auth_client, authenticated_user):
    team = TeamFactory(is_active=True, is_public=True)
    member = Member.objects.create(
        firstname="Test",
        lastname="User",
        email="already@local.test",
        user=authenticated_user,
    )
    TeamMembership.objects.create(team=team, member=member)
    response = auth_client.post(
        "/api/v1/join-requests/",
        {"team": team.pk},
        format="json",
    )
    assert response.status_code == 400


def test_POST_join_request_duplicate_pending_returns_400(auth_client, authenticated_user):
    team = TeamFactory(is_active=True, is_public=True)
    TeamJoinRequest.objects.create(user=authenticated_user, team=team, status="pending")
    response = auth_client.post(
        "/api/v1/join-requests/",
        {"team": team.pk},
        format="json",
    )
    assert response.status_code == 400


def test_POST_join_request_private_team_returns_400(auth_client):
    team = TeamFactory(is_active=True, is_public=False)
    response = auth_client.post(
        "/api/v1/join-requests/",
        {"team": team.pk},
        format="json",
    )
    assert response.status_code == 400


def test_POST_join_request_inactive_team_returns_400(auth_client):
    team = TeamFactory(is_active=False, is_public=True)
    response = auth_client.post(
        "/api/v1/join-requests/",
        {"team": team.pk},
        format="json",
    )
    assert response.status_code == 400


# ---------------------------- PATCH ----------------------------------


def test_PATCH_accept_join_request_creates_member(auth_client_trainer, trainer_user):
    requester = UserFactory(first_name="Alice", last_name="Wonder")
    team = trainer_user.owned_teams.first()
    jr = TeamJoinRequest.objects.create(user=requester, team=team, status="pending")

    response = auth_client_trainer.patch(
        f"/api/v1/join-requests/{jr.pk}/",
        {"status": "accepted", "response_message": "Welcome"},
        format="json",
    )
    assert response.status_code == 200
    jr.refresh_from_db()
    assert jr.status == "accepted"
    assert jr.responded_by_id == trainer_user.pk
    assert jr.responded_at is not None
    assert Member.objects.filter(
        user=requester,
        memberships__team=team,
        memberships__left_at__isnull=True,
    ).exists()


def test_PATCH_accept_existing_member_just_adds_team(auth_client_trainer, trainer_user):
    requester = UserFactory()
    other_team = TeamFactory()
    member = Member.objects.create(
        firstname="Bob",
        lastname="Existing",
        email=requester.email,
        user=requester,
    )
    TeamMembership.objects.create(team=other_team, member=member)

    team = trainer_user.owned_teams.first()
    jr = TeamJoinRequest.objects.create(user=requester, team=team, status="pending")
    response = auth_client_trainer.patch(
        f"/api/v1/join-requests/{jr.pk}/",
        {"status": "accepted"},
        format="json",
    )
    assert response.status_code == 200
    active_team_ids = set(
        member.memberships.filter(left_at__isnull=True).values_list("team_id", flat=True)
    )
    assert team.pk in active_team_ids
    assert other_team.pk in active_team_ids
    assert Member.objects.filter(user=requester).count() == 1


def test_PATCH_reject_join_request(auth_client_trainer, trainer_user):
    requester = UserFactory()
    team = trainer_user.owned_teams.first()
    jr = TeamJoinRequest.objects.create(user=requester, team=team, status="pending")

    response = auth_client_trainer.patch(
        f"/api/v1/join-requests/{jr.pk}/",
        {"status": "rejected", "response_message": "Sorry"},
        format="json",
    )
    assert response.status_code == 200
    jr.refresh_from_db()
    assert jr.status == "rejected"
    assert not Member.objects.filter(user=requester).exists()


def test_PATCH_cancel_own_request(auth_client, authenticated_user):
    team = TeamFactory(is_active=True, is_public=True)
    jr = TeamJoinRequest.objects.create(user=authenticated_user, team=team, status="pending")

    response = auth_client.patch(
        f"/api/v1/join-requests/{jr.pk}/",
        {"status": "cancelled"},
        format="json",
    )
    assert response.status_code == 200
    jr.refresh_from_db()
    assert jr.status == "cancelled"
    assert jr.responded_at is not None


def test_PATCH_already_handled_returns_400(auth_client_trainer, trainer_user):
    requester = UserFactory()
    team = trainer_user.owned_teams.first()
    jr = TeamJoinRequest.objects.create(user=requester, team=team, status="accepted")

    response = auth_client_trainer.patch(
        f"/api/v1/join-requests/{jr.pk}/",
        {"status": "rejected"},
        format="json",
    )
    assert response.status_code == 400


def test_magic_reject_after_accept_detaches_member_from_future_events(api_client, trainer_user):
    """B-P1: flipping accepted->rejected via the magic link must DETACH the
    athlete from the team's future events. _revoke_membership now saves each row
    (so the post_save signal fires); a bulk .update() bypassed it and left the
    athlete attached."""
    from datetime import timedelta

    from django.utils import timezone

    from event.models import Event
    from member.models import Member
    from program.models import Program
    from team.magic_action import make_token

    team = trainer_user.owned_teams.first()
    requester = UserFactory()
    jr = TeamJoinRequest.objects.create(user=requester, team=team, status="pending")
    program = Program.objects.create(name="P", team=team)
    future = Event.objects.create(
        refer_program=program, name="F", date=timezone.localdate() + timedelta(days=3)
    )

    api_client.force_authenticate(user=trainer_user)
    r1 = api_client.post("/api/v1/join-magic/", {"token": make_token(jr.id, "accept")}, format="json")
    assert r1.status_code == 200, r1.json()
    member = Member.objects.get(user=requester)
    assert TeamMembership.objects.filter(team=team, member=member, left_at__isnull=True).exists()
    assert future.members.filter(pk=member.pk).exists()

    r2 = api_client.post("/api/v1/join-magic/", {"token": make_token(jr.id, "reject")}, format="json")
    assert r2.status_code == 200, r2.json()
    assert not TeamMembership.objects.filter(team=team, member=member, left_at__isnull=True).exists()
    assert not future.members.filter(pk=member.pk).exists()


def test_PATCH_other_user_cant_modify(api_client, authenticated_user):
    requester = UserFactory()
    team = TeamFactory(is_active=True, is_public=True)
    jr = TeamJoinRequest.objects.create(user=requester, team=team, status="pending")

    api_client.force_authenticate(user=authenticated_user)
    response = api_client.patch(
        f"/api/v1/join-requests/{jr.pk}/",
        {"status": "cancelled"},
        format="json",
    )
    assert response.status_code in (403, 404)


def test_PATCH_non_manager_cannot_accept(auth_client, authenticated_user):
    team = TeamFactory(is_active=True, is_public=True)
    jr = TeamJoinRequest.objects.create(user=authenticated_user, team=team, status="pending")

    response = auth_client.patch(
        f"/api/v1/join-requests/{jr.pk}/",
        {"status": "accepted"},
        format="json",
    )
    assert response.status_code == 400
    jr.refresh_from_db()
    assert jr.status == "pending"


# ----------------------------- GET -----------------------------------


def test_GET_join_requests_user_sees_own_only(auth_client, authenticated_user):
    team_a = TeamFactory(is_active=True, is_public=True)
    team_b = TeamFactory(is_active=True, is_public=True)
    other = UserFactory()
    TeamJoinRequest.objects.create(user=authenticated_user, team=team_a)
    TeamJoinRequest.objects.create(user=other, team=team_b)

    response = auth_client.get("/api/v1/join-requests/")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["results"][0]["user"] == authenticated_user.pk


def test_GET_join_requests_manager_sees_team_requests(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    other = UserFactory()
    TeamJoinRequest.objects.create(user=other, team=team)
    other_team = TeamFactory()
    TeamJoinRequest.objects.create(user=UserFactory(), team=other_team)

    response = auth_client_trainer.get("/api/v1/join-requests/")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["results"][0]["team"] == team.pk

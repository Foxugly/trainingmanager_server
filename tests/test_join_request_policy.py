"""Coverage of Team.join_request_policy + notify + magic-action links.

- Auto-accept policy: POST /join-requests/ short-circuits to accepted.
- Manual + notify=True: email sent with two magic links (accept/reject).
- Manual + notify=False: no email.
- Magic-action GET: previews; no state change.
- Magic-action POST: executes, with reversal support (E-b semantic):
    pending -> accept/reject (creates / no membership)
    accepted -> reject       (revokes membership)
    rejected -> accept       (recreates membership)
    cancelled -> any         (409 conflict, irreversible)
- Token: HMAC-signed via team.magic_action; bogus / unsigned tokens 400.
- Permissions: GET/POST require IsAuthenticated + manager-of-team.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core import mail

from member.models import Member
from team.magic_action import make_token
from team.models import Team, TeamJoinRequest, TeamMembership
from tests.factories import TeamFactory, UserFactory

pytestmark = pytest.mark.django_db


User = get_user_model()


# =====================================================================
# Defaults
# =====================================================================


def test_team_factory_creates_team_with_manual_policy_and_notify_true():
    """Model default: manual policy + notifications opt-in (= True)."""
    team = TeamFactory()
    assert team.join_request_policy == Team.JoinRequestPolicy.MANUAL
    assert team.notify_managers_on_join_request is True


# =====================================================================
# perform_create branches
# =====================================================================


def test_POST_join_request_with_auto_policy_is_accepted_immediately(
    auth_client, authenticated_user
):
    team = TeamFactory(
        is_active=True,
        is_public=True,
        join_request_policy=Team.JoinRequestPolicy.AUTO,
    )
    mail.outbox = []
    response = auth_client.post("/api/v1/join-requests/", {"team": team.pk}, format="json")
    assert response.status_code == 201
    jr = TeamJoinRequest.objects.get(user=authenticated_user, team=team)
    assert jr.status == "accepted"
    assert jr.responded_at is not None
    assert jr.responded_by is None  # accepted by policy, not by a person
    assert TeamMembership.objects.filter(
        team=team, member__user=authenticated_user, left_at__isnull=True
    ).exists()
    assert mail.outbox == []  # auto-accept never emails managers


def test_POST_join_request_auto_policy_rolls_back_status_if_acceptance_fails(
    auth_client, authenticated_user
):
    """Regression: in the auto-accept branch the status flip + _handle_acceptance
    must commit together. If acceptance fails, the 'accepted' status must roll
    back too — never persist status=accepted with no membership."""
    from unittest import mock

    team = TeamFactory(
        is_active=True,
        is_public=True,
        join_request_policy=Team.JoinRequestPolicy.AUTO,
    )
    with mock.patch(
        "team.views.join_requests.TeamJoinRequestViewSet._handle_acceptance",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(RuntimeError):
            auth_client.post("/api/v1/join-requests/", {"team": team.pk}, format="json")

    jr = TeamJoinRequest.objects.filter(user=authenticated_user, team=team).first()
    # The status flip was rolled back with the failed acceptance: the row is
    # either absent or still 'pending' — never a dangling 'accepted'.
    if jr is not None:
        assert jr.status == "pending"
    assert not TeamMembership.objects.filter(
        team=team, member__user=authenticated_user
    ).exists()


def test_POST_join_request_with_notify_disabled_sends_no_email(auth_client):
    owner = UserFactory(email="owner_silent@local.test")
    team = TeamFactory(
        owner=owner,
        is_active=True,
        is_public=True,
        notify_managers_on_join_request=False,
    )
    mail.outbox = []
    response = auth_client.post("/api/v1/join-requests/", {"team": team.pk}, format="json")
    assert response.status_code == 201
    assert mail.outbox == []
    jr = TeamJoinRequest.objects.get(team=team)
    assert jr.status == "pending"  # waits for manual decision in dashboard


def test_POST_join_request_email_contains_two_magic_links(auth_client):
    owner = UserFactory(email="owner_links@local.test")
    team = TeamFactory(
        owner=owner,
        is_active=True,
        is_public=True,
        notify_managers_on_join_request=True,
    )
    mail.outbox = []
    response = auth_client.post("/api/v1/join-requests/", {"team": team.pk}, format="json")
    assert response.status_code == 201
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert "/team-join-requests/magic-action/" in body
    # Two distinct tokens (one for accept, one for reject)
    occurrences = body.count("/team-join-requests/magic-action/")
    assert occurrences == 2


# =====================================================================
# Magic-action GET (preview)
# =====================================================================


def _setup_pending_request():
    """Create owner, team (manual+notify), requester, and a pending JR."""
    owner = UserFactory(email="owner_magic@local.test")
    team = TeamFactory(owner=owner, is_active=True, is_public=True)
    requester = UserFactory(email="requester_magic@local.test")
    jr = TeamJoinRequest.objects.create(team=team, user=requester, message="please")
    return owner, team, requester, jr


def test_magic_action_GET_pending_returns_preview_no_state_change(api_client):
    owner, team, requester, jr = _setup_pending_request()
    api_client.force_authenticate(user=owner)
    token = make_token(jr.id, "accept")

    response = api_client.get(f"/api/v1/join-magic/{token}/")
    assert response.status_code == 200
    body = response.json()
    assert body["action_proposed"] == "accept"
    assert body["join_request"]["status"] == "pending"
    assert body["would_change_decision"] is False
    assert body["can_act"] is True

    jr.refresh_from_db()
    assert jr.status == "pending"  # GET is idempotent / no side effect


def test_magic_action_GET_unauthenticated_returns_401(api_client):
    _owner, _team, _requester, jr = _setup_pending_request()
    token = make_token(jr.id, "accept")
    response = api_client.get(f"/api/v1/join-magic/{token}/")
    assert response.status_code == 401


def test_magic_action_GET_non_manager_returns_403(api_client):
    _owner, _team, _requester, jr = _setup_pending_request()
    rando = UserFactory(email="rando_magic@local.test")
    api_client.force_authenticate(user=rando)
    token = make_token(jr.id, "accept")
    response = api_client.get(f"/api/v1/join-magic/{token}/")
    assert response.status_code == 403


def test_magic_action_GET_invalid_token_returns_400(api_client):
    owner = UserFactory(email="owner_bogus@local.test")
    api_client.force_authenticate(user=owner)
    response = api_client.get("/api/v1/join-magic/totally-bogus/")
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_or_expired_token"


# =====================================================================
# Magic-action POST (execute)
# =====================================================================


def _post_action(api_client, token):
    return api_client.post("/api/v1/join-magic/", {"token": token}, format="json")


def test_magic_action_POST_accept_pending_creates_membership(api_client):
    owner, team, requester, jr = _setup_pending_request()
    api_client.force_authenticate(user=owner)
    token = make_token(jr.id, "accept")

    response = _post_action(api_client, token)
    assert response.status_code == 200
    body = response.json()
    assert body["join_request"]["status"] == "accepted"

    jr.refresh_from_db()
    assert jr.status == "accepted"
    assert jr.responded_by_id == owner.id
    assert TeamMembership.objects.filter(
        team=team, member__user=requester, left_at__isnull=True
    ).exists()


def test_magic_action_POST_reject_pending_no_membership(api_client):
    owner, team, requester, jr = _setup_pending_request()
    api_client.force_authenticate(user=owner)
    token = make_token(jr.id, "reject")

    response = _post_action(api_client, token)
    assert response.status_code == 200
    jr.refresh_from_db()
    assert jr.status == "rejected"
    assert jr.responded_by_id == owner.id
    assert not TeamMembership.objects.filter(team=team, member__user=requester).exists()


def test_magic_action_POST_change_decision_accept_to_reject_revokes_membership(api_client):
    owner, team, requester, jr = _setup_pending_request()
    api_client.force_authenticate(user=owner)

    # 1st coach clicks accept
    _post_action(api_client, make_token(jr.id, "accept"))
    membership = TeamMembership.objects.get(team=team, member__user=requester, left_at__isnull=True)

    # 2nd coach (or same one second-thought) clicks reject
    response = _post_action(api_client, make_token(jr.id, "reject"))
    assert response.status_code == 200
    body = response.json()
    assert body["join_request"]["status"] == "rejected"
    assert body["would_change_decision"] is True

    membership.refresh_from_db()
    assert membership.left_at is not None
    assert not TeamMembership.objects.filter(
        team=team, member__user=requester, left_at__isnull=True
    ).exists()


def test_magic_action_POST_change_decision_reject_to_accept_creates_membership(api_client):
    owner, team, requester, jr = _setup_pending_request()
    api_client.force_authenticate(user=owner)

    _post_action(api_client, make_token(jr.id, "reject"))
    assert not TeamMembership.objects.filter(team=team, member__user=requester).exists()

    response = _post_action(api_client, make_token(jr.id, "accept"))
    assert response.status_code == 200
    jr.refresh_from_db()
    assert jr.status == "accepted"
    assert TeamMembership.objects.filter(
        team=team, member__user=requester, left_at__isnull=True
    ).exists()


def test_magic_action_POST_idempotent_when_already_in_target_status(api_client):
    owner, team, requester, jr = _setup_pending_request()
    api_client.force_authenticate(user=owner)

    _post_action(api_client, make_token(jr.id, "accept"))
    initial_membership_count = TeamMembership.objects.filter(team=team).count()

    # Same coach clicks accept again — no-op, no double membership.
    response = _post_action(api_client, make_token(jr.id, "accept"))
    assert response.status_code == 200
    assert TeamMembership.objects.filter(team=team).count() == initial_membership_count


def test_magic_action_POST_cancelled_request_returns_409(api_client):
    owner, team, requester, jr = _setup_pending_request()
    jr.status = "cancelled"
    jr.save(update_fields=["status"])
    api_client.force_authenticate(user=owner)

    response = _post_action(api_client, make_token(jr.id, "accept"))
    assert response.status_code == 409
    assert response.json()["code"] == "request_cancelled"
    assert not TeamMembership.objects.filter(team=team, member__user=requester).exists()


def test_magic_action_POST_invalid_token_returns_400(api_client):
    owner = UserFactory(email="owner_bad@local.test")
    api_client.force_authenticate(user=owner)
    response = _post_action(api_client, "obviously-fake-token")
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_or_expired_token"


def test_magic_action_POST_missing_token_returns_400(api_client):
    owner = UserFactory(email="owner_notoken@local.test")
    api_client.force_authenticate(user=owner)
    response = api_client.post("/api/v1/join-magic/", {}, format="json")
    assert response.status_code == 400


def test_magic_action_POST_non_manager_returns_403(api_client):
    _owner, _team, _requester, jr = _setup_pending_request()
    rando = UserFactory(email="rando_post@local.test")
    api_client.force_authenticate(user=rando)
    response = _post_action(api_client, make_token(jr.id, "accept"))
    assert response.status_code == 403


# =====================================================================
# Pre-existing requester (Member already exists for the user)
# =====================================================================


def test_magic_action_POST_accept_uses_existing_member_profile(api_client):
    owner, team, requester, jr = _setup_pending_request()
    # Requester already has a Member profile (came in via another team earlier)
    Member.objects.create(
        firstname="Pre", lastname="Existing", email=requester.email, user=requester
    )
    api_client.force_authenticate(user=owner)

    _post_action(api_client, make_token(jr.id, "accept"))
    # No second Member created — the existing one is reused.
    assert Member.objects.filter(user=requester).count() == 1

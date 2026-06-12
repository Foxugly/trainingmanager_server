"""Coverage of the Note feature.

URL pattern: /api/v1/teams/{team_id}/members/{member_id}/notes/

Permissions:
  - Coach (team owner / managers): full CRUD; sees every note of the team
  - Athlete (member.user == request.user): reads their own member's notes,
    PER-NOTE, only those with visible_to_athlete=True AND is_active=True
  - Soft-deleted notes never visible to athletes
"""

import pytest
from django.contrib.auth import get_user_model

from member.models import Member
from note.models import Note
from team.models import TeamMembership
from tests.factories import TeamFactory

pytestmark = pytest.mark.django_db


User = get_user_model()


@pytest.fixture
def coach_user(db):
    return User.objects.create_user(
        email="coach1@local.test", password="passcoach"
    )


@pytest.fixture
def coach_team(coach_user):
    return TeamFactory(owner=coach_user, is_active=True)


@pytest.fixture
def athlete_user(db):
    return User.objects.create_user(
        email="athlete1@local.test", password="passathlete"
    )


@pytest.fixture
def member_in_team(athlete_user, coach_team):
    member = Member.objects.create(
        firstname="Alex",
        lastname="Athlete",
        email="alex@local.test",
        user=athlete_user,
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
def random_user(db):
    return User.objects.create_user(
        email="rando@local.test", password="passrando"
    )


@pytest.fixture
def random_client(api_client, random_user):
    api_client.force_authenticate(user=random_user)
    return api_client


def _url(team_id, member_id, note_id=None):
    base = f"/api/v1/teams/{team_id}/members/{member_id}/notes/"
    return base if note_id is None else f"{base}{note_id}/"


# =====================================================================
# MODEL
# =====================================================================


def test_create_note_basic(coach_team, member_in_team, coach_user):
    note = Note.objects.create(
        team=coach_team,
        member=member_in_team,
        author=coach_user,
        content="<p>Test</p>",
    )
    assert note.is_active is True
    assert note.team_id == coach_team.pk
    assert note.member_id == member_in_team.pk
    assert note.author_id == coach_user.pk


def test_str_representation(coach_team, member_in_team, coach_user):
    note = Note.objects.create(
        team=coach_team, member=member_in_team, author=coach_user, content="x"
    )
    s = str(note)
    assert "coach1" in s
    assert "Alex" in s or "Athlete" in s


def test_str_handles_deleted_author(coach_team, member_in_team):
    note = Note.objects.create(team=coach_team, member=member_in_team, author=None, content="x")
    assert "(deleted)" in str(note)


# =====================================================================
# CRUD COACH
# =====================================================================


def test_owner_can_create_note(coach_client, coach_team, member_in_team):
    response = coach_client.post(
        _url(coach_team.pk, member_in_team.pk),
        {"content": "<p>Bonne séance aujourd'hui</p>"},
        format="json",
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["content"] == "<p>Bonne séance aujourd'hui</p>"
    assert body["author_username"] == "coach1@local.test"


def test_manager_can_create_note(api_client, coach_team, member_in_team, athlete_user):
    """Add athlete_user as a manager and verify they can create notes."""
    coach_team.managers.add(athlete_user)
    api_client.force_authenticate(user=athlete_user)
    response = api_client.post(
        _url(coach_team.pk, member_in_team.pk),
        {"content": "<p>Manager note</p>"},
        format="json",
    )
    assert response.status_code == 201


def test_random_user_cannot_create_note(random_client, coach_team, member_in_team):
    response = random_client.post(
        _url(coach_team.pk, member_in_team.pk),
        {"content": "<p>x</p>"},
        format="json",
    )
    assert response.status_code == 403


def test_owner_can_list_notes(coach_client, coach_team, member_in_team, coach_user):
    Note.objects.create(team=coach_team, member=member_in_team, author=coach_user, content="A")
    Note.objects.create(team=coach_team, member=member_in_team, author=coach_user, content="B")
    response = coach_client.get(_url(coach_team.pk, member_in_team.pk))
    assert response.status_code == 200
    assert response.json()["count"] == 2


def test_owner_can_patch_note(coach_client, coach_team, member_in_team, coach_user):
    note = Note.objects.create(
        team=coach_team, member=member_in_team, author=coach_user, content="<p>old</p>"
    )
    response = coach_client.patch(
        _url(coach_team.pk, member_in_team.pk, note.pk),
        {"content": "<p>new</p>"},
        format="json",
    )
    assert response.status_code == 200
    note.refresh_from_db()
    assert note.content == "<p>new</p>"


def test_owner_can_soft_delete_note(coach_client, coach_team, member_in_team, coach_user):
    note = Note.objects.create(
        team=coach_team, member=member_in_team, author=coach_user, content="x"
    )
    response = coach_client.delete(_url(coach_team.pk, member_in_team.pk, note.pk))
    assert response.status_code == 204
    note.refresh_from_db()
    assert note.is_active is False


def test_soft_deleted_notes_excluded_by_default(
    coach_client, coach_team, member_in_team, coach_user
):
    Note.objects.create(team=coach_team, member=member_in_team, author=coach_user, content="active")
    Note.objects.create(
        team=coach_team, member=member_in_team, author=coach_user, content="dead", is_active=False
    )
    response = coach_client.get(_url(coach_team.pk, member_in_team.pk))
    assert response.json()["count"] == 1


def test_owner_can_see_inactive_with_include_inactive(
    coach_client, coach_team, member_in_team, coach_user
):
    Note.objects.create(team=coach_team, member=member_in_team, author=coach_user, content="active")
    Note.objects.create(
        team=coach_team, member=member_in_team, author=coach_user, content="dead", is_active=False
    )
    response = coach_client.get(_url(coach_team.pk, member_in_team.pk) + "?include_inactive=true")
    assert response.json()["count"] == 2


# =====================================================================
# ATHLETE
# =====================================================================


def test_athlete_sees_only_visible_active_notes(
    athlete_client, coach_team, member_in_team, coach_user
):
    """Per-note visibility: athlete sees the visible+active note, not the
    invisible one nor the soft-deleted one."""
    Note.objects.create(
        team=coach_team,
        member=member_in_team,
        author=coach_user,
        content="shared",
        visible_to_athlete=True,
    )
    Note.objects.create(
        team=coach_team,
        member=member_in_team,
        author=coach_user,
        content="private",
        visible_to_athlete=False,
    )
    Note.objects.create(
        team=coach_team,
        member=member_in_team,
        author=coach_user,
        content="visible-but-archived",
        visible_to_athlete=True,
        is_active=False,
    )
    response = athlete_client.get(_url(coach_team.pk, member_in_team.pk))
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["results"][0]["content"] == "shared"


def test_athlete_gets_empty_list_when_no_visible_notes(
    athlete_client, coach_team, member_in_team, coach_user
):
    """An invisible note alone -> athlete gets 200 with an empty list (no 403:
    they may access their own member's notes, just see nothing shared)."""
    Note.objects.create(
        team=coach_team,
        member=member_in_team,
        author=coach_user,
        content="private",
        visible_to_athlete=False,
    )
    response = athlete_client.get(_url(coach_team.pk, member_in_team.pk))
    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_athlete_can_retrieve_a_visible_note_but_not_an_invisible_one(
    athlete_client, coach_team, member_in_team, coach_user
):
    visible = Note.objects.create(
        team=coach_team,
        member=member_in_team,
        author=coach_user,
        content="shared",
        visible_to_athlete=True,
    )
    hidden = Note.objects.create(
        team=coach_team,
        member=member_in_team,
        author=coach_user,
        content="private",
        visible_to_athlete=False,
    )
    ok = athlete_client.get(_url(coach_team.pk, member_in_team.pk, visible.pk))
    assert ok.status_code == 200
    nope = athlete_client.get(_url(coach_team.pk, member_in_team.pk, hidden.pk))
    assert nope.status_code == 404


def test_coach_sees_both_visible_and_invisible(
    coach_client, coach_team, member_in_team, coach_user
):
    Note.objects.create(
        team=coach_team, member=member_in_team, author=coach_user,
        content="shared", visible_to_athlete=True,
    )
    Note.objects.create(
        team=coach_team, member=member_in_team, author=coach_user,
        content="private", visible_to_athlete=False,
    )
    response = coach_client.get(_url(coach_team.pk, member_in_team.pk))
    assert response.status_code == 200
    assert response.json()["count"] == 2


def test_create_defaults_visible_to_athlete_false(
    coach_client, coach_team, member_in_team
):
    response = coach_client.post(
        _url(coach_team.pk, member_in_team.pk),
        {"content": "<p>x</p>"},
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["visible_to_athlete"] is False
    note = Note.objects.get(pk=body["id"])
    assert note.visible_to_athlete is False


def test_coach_can_toggle_visibility_and_athlete_sees_it(
    api_client, coach_team, member_in_team, coach_user, athlete_user
):
    note = Note.objects.create(
        team=coach_team, member=member_in_team, author=coach_user,
        content="x", visible_to_athlete=False,
    )
    # Hidden -> athlete sees nothing.
    api_client.force_authenticate(user=athlete_user)
    assert api_client.get(_url(coach_team.pk, member_in_team.pk)).json()["count"] == 0
    # Coach toggles it visible.
    api_client.force_authenticate(user=coach_user)
    patch = api_client.patch(
        _url(coach_team.pk, member_in_team.pk, note.pk),
        {"visible_to_athlete": True},
        format="json",
    )
    assert patch.status_code == 200
    note.refresh_from_db()
    assert note.visible_to_athlete is True
    # Now the athlete sees it.
    api_client.force_authenticate(user=athlete_user)
    assert api_client.get(_url(coach_team.pk, member_in_team.pk)).json()["count"] == 1


def test_athlete_cannot_read_other_member_notes(
    athlete_client, coach_team, member_in_team, coach_user
):
    """Another Member, same team, with a visible note -> still 403 for our
    athlete (not their own member record)."""
    other = Member.objects.create(firstname="Other", lastname="Person", email="other@local.test")
    TeamMembership.objects.create(team=coach_team, member=other)
    Note.objects.create(
        team=coach_team, member=other, author=coach_user,
        content="secret", visible_to_athlete=True,
    )
    response = athlete_client.get(_url(coach_team.pk, other.pk))
    assert response.status_code == 403


def test_athlete_cannot_see_soft_deleted_even_with_include_inactive(
    athlete_client, coach_team, member_in_team, coach_user
):
    Note.objects.create(
        team=coach_team, member=member_in_team, author=coach_user,
        content="alive", visible_to_athlete=True,
    )
    Note.objects.create(
        team=coach_team,
        member=member_in_team,
        author=coach_user,
        content="dead",
        visible_to_athlete=True,
        is_active=False,
    )
    response = athlete_client.get(_url(coach_team.pk, member_in_team.pk) + "?include_inactive=true")
    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_athlete_cannot_create_note(athlete_client, coach_team, member_in_team):
    response = athlete_client.post(
        _url(coach_team.pk, member_in_team.pk),
        {"content": "<p>I'm just an athlete</p>"},
        format="json",
    )
    assert response.status_code == 403


# =====================================================================
# VALIDATION
# =====================================================================


def test_create_note_for_member_not_in_team_returns_403(coach_client, coach_team, athlete_user):
    """Member exists but not attached to coach_team."""
    other = Member.objects.create(firstname="Out", lastname="Sider", email="out@local.test")
    response = coach_client.post(
        _url(coach_team.pk, other.pk),
        {"content": "<p>x</p>"},
        format="json",
    )
    assert response.status_code == 403


def test_content_is_sanitized_on_create(coach_client, coach_team, member_in_team):
    payload = '<p>Hello</p><script>alert("xss")</script>'
    response = coach_client.post(
        _url(coach_team.pk, member_in_team.pk),
        {"content": payload},
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert "<script>" not in body["content"]
    assert "<p>Hello</p>" in body["content"]


def test_content_is_sanitized_on_patch(coach_client, coach_team, member_in_team, coach_user):
    note = Note.objects.create(
        team=coach_team, member=member_in_team, author=coach_user, content="<p>ok</p>"
    )
    response = coach_client.patch(
        _url(coach_team.pk, member_in_team.pk, note.pk),
        {"content": '<p>ok</p><iframe src="evil"></iframe>'},
        format="json",
    )
    assert response.status_code == 200
    note.refresh_from_db()
    assert "<iframe" not in note.content


# =====================================================================
# AUTHOR TRACKING
# =====================================================================


def test_create_note_sets_author_to_current_user(
    coach_client, coach_team, member_in_team, coach_user
):
    response = coach_client.post(
        _url(coach_team.pk, member_in_team.pk),
        {"content": "<p>x</p>"},
        format="json",
    )
    assert response.status_code == 201
    note = Note.objects.get(pk=response.json()["id"])
    assert note.author_id == coach_user.pk


def test_author_set_null_if_user_deleted(coach_team, member_in_team):
    """Use a second coach (manager, not the team owner) as author so
    we can delete them without tripping the Team.owner PROTECT FK."""
    second_coach = User.objects.create_user(
        email="coach2_an@local.test",
        password="passcoach",
    )
    coach_team.managers.add(second_coach)
    note = Note.objects.create(
        team=coach_team, member=member_in_team, author=second_coach, content="x"
    )
    second_coach.delete()
    note.refresh_from_db()
    assert note.author is None

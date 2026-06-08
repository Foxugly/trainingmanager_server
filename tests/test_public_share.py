"""Public read-only session share link + team config gating.

Covers:
  - Manager POST /events/{id}/share/ enabling/disabling, team-config gate (409),
    token reuse on re-enable, and non-manager 403.
  - Anonymous GET /api/v1/public/events/<token>/ : always-public fields, the
    per-aspect redaction (distance/goal/rounds) driven by team.public_show_*,
    and the 404 cases (bogus token / not shared / team sharing disabled).
  - The authenticated EventSerializer exposes is_public + public_token
    read-only and a normal PATCH cannot change them.
"""

from datetime import date, time, timedelta

import pytest

from member.models import Member
from team.models import TeamMembership
from tests.factories import (
    EventFactory,
    ExerciseFactory,
    ModalityFactory,
    ProgramFactory,
    RoundFactory,
    TeamFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def owner_user():
    return UserFactory(username="share_owner")


@pytest.fixture
def team(owner_user):
    return TeamFactory(owner=owner_user, is_active=True)


@pytest.fixture
def owner_client(api_client, owner_user):
    api_client.force_authenticate(user=owner_user)
    return api_client


@pytest.fixture
def athlete_user():
    return UserFactory(username="share_athlete")


@pytest.fixture
def athlete_member(athlete_user, team):
    member = Member.objects.create(
        firstname="A", lastname="Thlete", email=athlete_user.email, user=athlete_user
    )
    TeamMembership.objects.create(team=team, member=member)
    return member


@pytest.fixture
def athlete_client(api_client, athlete_user, athlete_member):
    api_client.force_authenticate(user=athlete_user)
    return api_client


@pytest.fixture
def anon_client(api_client):
    return api_client


def _make_event(team, **kwargs):
    program = ProgramFactory(team=team, name="Prog Public")
    defaults = dict(
        name="Public Sess",
        refer_program=program,
        total=4200,
        goal="Endurance base",
        location="Pool 50m",
        equipment="fins, paddles",
        date=date.today() + timedelta(days=2),
        hour_start=time(18, 0),
        hour_end=time(19, 30),
    )
    defaults.update(kwargs)
    return EventFactory(**defaults)


# ==========================================================================
# Manager share toggle
# ==========================================================================


def test_manager_share_enable_when_team_allows(owner_client, team):
    team.public_sharing_enabled = True
    team.save()
    event = _make_event(team)

    resp = owner_client.post(
        f"/api/v1/events/{event.pk}/share/", {"is_public": True}, format="json"
    )
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["is_public"] is True
    assert body["public_token"]
    assert body["public_url_path"] == f"/s/e/{body['public_token']}"

    event.refresh_from_db()
    assert event.is_public is True
    assert event.public_token == body["public_token"]


def test_manager_share_enable_when_team_disallows_returns_409(owner_client, team):
    team.public_sharing_enabled = False
    team.save()
    event = _make_event(team)

    resp = owner_client.post(
        f"/api/v1/events/{event.pk}/share/", {"is_public": True}, format="json"
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "public_sharing_disabled"

    event.refresh_from_db()
    assert event.is_public is False
    assert event.public_token is None


def test_disabling_keeps_token_and_reenable_reuses_it(owner_client, team):
    team.public_sharing_enabled = True
    team.save()
    event = _make_event(team)

    enable = owner_client.post(
        f"/api/v1/events/{event.pk}/share/", {"is_public": True}, format="json"
    )
    token = enable.json()["public_token"]
    assert token

    disable = owner_client.post(
        f"/api/v1/events/{event.pk}/share/", {"is_public": False}, format="json"
    )
    assert disable.status_code == 200
    assert disable.json()["is_public"] is False
    # public_url_path is null while not shared, but the token is preserved.
    assert disable.json()["public_url_path"] is None
    event.refresh_from_db()
    assert event.is_public is False
    assert event.public_token == token

    reenable = owner_client.post(
        f"/api/v1/events/{event.pk}/share/", {"is_public": True}, format="json"
    )
    assert reenable.status_code == 200
    assert reenable.json()["public_token"] == token


def test_athlete_cannot_share(athlete_client, team):
    team.public_sharing_enabled = True
    team.save()
    event = _make_event(team)

    resp = athlete_client.post(
        f"/api/v1/events/{event.pk}/share/", {"is_public": True}, format="json"
    )
    assert resp.status_code == 403
    event.refresh_from_db()
    assert event.is_public is False


# ==========================================================================
# Public anonymous view — happy path + always-public fields
# ==========================================================================


def _share(event, team):
    team.public_sharing_enabled = True
    team.save()
    event.ensure_public_token()
    event.is_public = True
    event.save(update_fields=["is_public"])
    return event.public_token


def test_anonymous_can_view_shared_event_always_fields(anon_client, team):
    event = _make_event(team)
    token = _share(event, team)

    resp = anon_client.get(f"/api/v1/public/events/{token}/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["name"] == "Public Sess"
    assert body["date"] == event.date.isoformat()
    assert body["location"] == "Pool 50m"
    assert body["equipment"] == "fins, paddles"
    assert body["program_name"] == "Prog Public"
    assert body["team_name"] == team.name


# ==========================================================================
# Public view redaction driven by team.public_show_*
# ==========================================================================


def test_public_redaction_distance(anon_client, team):
    event = _make_event(team)
    token = _share(event, team)

    team.public_show_distance = False
    team.save()
    assert anon_client.get(f"/api/v1/public/events/{token}/").json()["total"] is None

    team.public_show_distance = True
    team.save()
    assert anon_client.get(f"/api/v1/public/events/{token}/").json()["total"] == 4200


def test_public_redaction_goal(anon_client, team):
    event = _make_event(team)
    token = _share(event, team)

    team.public_show_goal = False
    team.save()
    assert anon_client.get(f"/api/v1/public/events/{token}/").json()["goal"] is None

    team.public_show_goal = True
    team.save()
    assert anon_client.get(f"/api/v1/public/events/{token}/").json()["goal"] == "Endurance base"


def test_public_redaction_rounds(anon_client, team):
    event = _make_event(team)
    modality = ModalityFactory(name="Crawl", sport=team.sport)
    ex = ExerciseFactory(modality=modality, distance=200, language=team.language)
    r = RoundFactory(sport=team.sport, language=team.language, exercises=[ex])
    event.rounds.add(r)
    token = _share(event, team)

    team.public_show_rounds = False
    team.save()
    assert anon_client.get(f"/api/v1/public/events/{token}/").json()["rounds"] == []

    team.public_show_rounds = True
    team.save()
    rounds = anon_client.get(f"/api/v1/public/events/{token}/").json()["rounds"]
    assert len(rounds) == 1
    assert len(rounds[0]["exercises"]) == 1
    assert rounds[0]["exercises"][0]["distance"] == 200


# ==========================================================================
# Public view 404 cases (no existence leak)
# ==========================================================================


def test_public_bogus_token_404(anon_client, team):
    resp = anon_client.get("/api/v1/public/events/this-token-does-not-exist/")
    assert resp.status_code == 404


def test_public_token_but_not_public_404(anon_client, team):
    team.public_sharing_enabled = True
    team.save()
    event = _make_event(team)
    event.ensure_public_token()
    event.is_public = False  # token exists, but session is not shared
    event.save(update_fields=["is_public"])

    resp = anon_client.get(f"/api/v1/public/events/{event.public_token}/")
    assert resp.status_code == 404


def test_public_team_disables_sharing_kills_link_404(anon_client, team):
    event = _make_event(team)
    token = _share(event, team)
    # link works while sharing is enabled
    assert anon_client.get(f"/api/v1/public/events/{token}/").status_code == 200

    team.public_sharing_enabled = False
    team.save()
    assert anon_client.get(f"/api/v1/public/events/{token}/").status_code == 404


# ==========================================================================
# Authenticated EventSerializer: is_public/public_token read-only
# ==========================================================================


def test_event_serializer_exposes_is_public_and_token_readonly(owner_client, team):
    team.public_sharing_enabled = True
    team.save()
    event = _make_event(team)
    _share(event, team)

    resp = owner_client.get(f"/api/v1/events/{event.pk}/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_public"] is True
    assert body["public_token"] == event.public_token


def test_patch_cannot_change_is_public_or_token(owner_client, team):
    event = _make_event(team)
    assert event.is_public is False
    assert event.public_token is None

    resp = owner_client.patch(
        f"/api/v1/events/{event.pk}/",
        {"is_public": True, "public_token": "forged-token", "name": "Renamed"},
        format="json",
    )
    assert resp.status_code == 200, resp.json()
    event.refresh_from_db()
    # the writable field changed ...
    assert event.name == "Renamed"
    # ... but the share fields were ignored
    assert event.is_public is False
    assert event.public_token is None

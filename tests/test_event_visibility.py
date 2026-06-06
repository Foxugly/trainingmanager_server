"""Per-aspect athlete training visibility + team timezone.

Covers:
  - Team.timezone validation (valid / invalid IANA), manager PATCH.
  - Team.vis_* defaults (ALWAYS) + manager PATCH.
  - Event create inheritance of team vis_* defaults + explicit override.
  - Event.is_over / Event.aspect_visible_to_athlete (always/never/after,
    including a timezone-boundary case).
  - API enforcement: athlete-members never receive hidden total/goal/rounds;
    managers always see everything.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from event.models import Event, VisibilityMode
from member.models import Member
from program.models import Program
from team.models import TeamMembership
from tests.factories import (
    EventFactory,
    ProgramFactory,
    RoundFactory,
    TeamFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# fixtures: a team with an owner (manager), and an athlete-member
# --------------------------------------------------------------------------


@pytest.fixture
def owner_user():
    return UserFactory(username="vis_owner")


@pytest.fixture
def team(owner_user):
    return TeamFactory(owner=owner_user, is_active=True, timezone="Europe/Brussels")


@pytest.fixture
def athlete_user():
    return UserFactory(username="vis_athlete2")


@pytest.fixture
def athlete_member(athlete_user, team):
    member = Member.objects.create(
        firstname="A", lastname="Thlete", email=athlete_user.email, user=athlete_user
    )
    TeamMembership.objects.create(team=team, member=member)
    return member


@pytest.fixture
def owner_client(api_client, owner_user):
    api_client.force_authenticate(user=owner_user)
    return api_client


@pytest.fixture
def athlete_client(api_client, athlete_user, athlete_member):
    api_client.force_authenticate(user=athlete_user)
    return api_client


# ==========================================================================
# Team timezone
# ==========================================================================


def test_team_default_timezone_is_brussels():
    t = TeamFactory()
    assert t.timezone == "Europe/Brussels"


def test_team_serializer_exposes_timezone_and_vis(owner_client, team):
    resp = owner_client.get(f"/api/v1/teams/{team.pk}/")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("timezone", "vis_distance", "vis_goal", "vis_rounds"):
        assert key in body


def test_team_manager_can_patch_valid_timezone(owner_client, team):
    resp = owner_client.patch(
        f"/api/v1/teams/{team.pk}/", {"timezone": "America/New_York"}, format="json"
    )
    assert resp.status_code == 200, resp.json()
    team.refresh_from_db()
    assert team.timezone == "America/New_York"


def test_team_invalid_timezone_returns_400(owner_client, team):
    resp = owner_client.patch(
        f"/api/v1/teams/{team.pk}/", {"timezone": "Mars/Phobos"}, format="json"
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "validation_error"
    assert "timezone" in body.get("fields", {})


# ==========================================================================
# Team vis_* defaults + write
# ==========================================================================


def test_team_vis_defaults_are_always():
    t = TeamFactory()
    assert t.vis_distance == VisibilityMode.ALWAYS
    assert t.vis_goal == VisibilityMode.ALWAYS
    assert t.vis_rounds == VisibilityMode.ALWAYS


def test_team_manager_can_patch_vis_fields(owner_client, team):
    resp = owner_client.patch(
        f"/api/v1/teams/{team.pk}/",
        {"vis_distance": "never", "vis_goal": "after", "vis_rounds": "always"},
        format="json",
    )
    assert resp.status_code == 200, resp.json()
    team.refresh_from_db()
    assert team.vis_distance == "never"
    assert team.vis_goal == "after"
    assert team.vis_rounds == "always"


def test_team_invalid_vis_value_returns_400(owner_client, team):
    resp = owner_client.patch(
        f"/api/v1/teams/{team.pk}/", {"vis_distance": "sometimes"}, format="json"
    )
    assert resp.status_code == 400


# ==========================================================================
# Event create inheritance + override
# ==========================================================================


def test_event_create_inherits_team_vis_defaults(owner_client, team):
    team.vis_distance = "never"
    team.vis_goal = "after"
    team.vis_rounds = "never"
    team.save()
    program = ProgramFactory(team=team)

    resp = owner_client.post(
        "/api/v1/events/",
        {"name": "Inherited", "refer_program_id": program.pk},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    event = Event.objects.get(pk=resp.json()["id"])
    assert event.vis_distance == "never"
    assert event.vis_goal == "after"
    assert event.vis_rounds == "never"


def test_event_create_explicit_override_honored(owner_client, team):
    team.vis_distance = "never"
    team.save()
    program = ProgramFactory(team=team)

    resp = owner_client.post(
        "/api/v1/events/",
        {"name": "Override", "refer_program_id": program.pk, "vis_distance": "always"},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    event = Event.objects.get(pk=resp.json()["id"])
    # explicit always wins over team's never
    assert event.vis_distance == "always"


def test_event_manager_can_patch_vis(owner_client, team):
    program = ProgramFactory(team=team)
    event = EventFactory(refer_program=program)
    resp = owner_client.patch(
        f"/api/v1/events/{event.pk}/", {"vis_goal": "never"}, format="json"
    )
    assert resp.status_code == 200, resp.json()
    event.refresh_from_db()
    assert event.vis_goal == "never"


# ==========================================================================
# Model helpers: is_over / aspect_visible_to_athlete
# ==========================================================================


def test_is_over_past_and_future():
    past = EventFactory(date=date.today() - timedelta(days=2), hour_end=time(10, 0))
    future = EventFactory(date=date.today() + timedelta(days=2), hour_end=time(10, 0))
    assert past.is_over() is True
    assert future.is_over() is False


def test_is_over_no_date_is_never_over():
    e = EventFactory(date=None)
    assert e.is_over() is False


def test_is_over_uses_team_timezone_boundary():
    """An event ending 23:00 Brussels time: at 22:30 UTC the same calendar
    day it is NOT yet over (Brussels is UTC+1/+2). With a UTC-naive compare
    it would wrongly look over."""
    team = TeamFactory(timezone="Europe/Brussels")
    program = ProgramFactory(team=team)
    d = date(2026, 1, 15)  # winter -> Brussels = UTC+1
    event = EventFactory(refer_program=program, date=d, hour_end=time(23, 0))
    # 22:30 UTC == 23:30 Brussels -> over
    now_after = datetime(2026, 1, 15, 22, 30, tzinfo=ZoneInfo("UTC"))
    assert event.is_over(now=now_after) is True
    # 21:30 UTC == 22:30 Brussels -> NOT over (a naive UTC compare to 23:00
    # would incorrectly say not-over too, so push to a discriminating point)
    now_before = datetime(2026, 1, 15, 21, 30, tzinfo=ZoneInfo("UTC"))
    assert event.is_over(now=now_before) is False
    # discriminating point: 22:15 UTC == 23:15 Brussels -> over, but a buggy
    # UTC-naive impl comparing to 23:00 UTC would say NOT over.
    now_boundary = datetime(2026, 1, 15, 22, 15, tzinfo=ZoneInfo("UTC"))
    assert event.is_over(now=now_boundary) is True


def test_aspect_visible_always():
    e = EventFactory(date=date.today() + timedelta(days=1), vis_distance="always")
    assert e.aspect_visible_to_athlete("distance") is True


def test_aspect_visible_never():
    e = EventFactory(date=date.today() - timedelta(days=1), vis_distance="never")
    assert e.aspect_visible_to_athlete("distance") is False


def test_aspect_visible_after_before_and_after_session():
    future = EventFactory(
        date=date.today() + timedelta(days=2), hour_end=time(10, 0), vis_goal="after"
    )
    past = EventFactory(
        date=date.today() - timedelta(days=2), hour_end=time(10, 0), vis_goal="after"
    )
    assert future.aspect_visible_to_athlete("goal") is False
    assert past.aspect_visible_to_athlete("goal") is True


# ==========================================================================
# API enforcement (the security-relevant part)
# ==========================================================================


def _make_event(team, **vis):
    program = ProgramFactory(team=team)
    defaults = dict(
        name="Sess",
        refer_program=program,
        total=5000,
        goal="Endurance",
        date=date.today() + timedelta(days=3),
        hour_end=time(10, 0),
    )
    defaults.update(vis)
    return EventFactory(**defaults)


def test_athlete_distance_never_is_nulled(athlete_client, team):
    event = _make_event(team, vis_distance="never")
    resp = athlete_client.get(f"/api/v1/events/{event.pk}/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] is None
    # vis mode itself is readable
    assert body["vis_distance"] == "never"


def test_athlete_goal_after_future_is_nulled(athlete_client, team):
    event = _make_event(team, vis_goal="after")  # future event
    resp = athlete_client.get(f"/api/v1/events/{event.pk}/")
    assert resp.status_code == 200
    assert resp.json()["goal"] is None


def test_athlete_goal_after_past_is_visible(athlete_client, team):
    program = ProgramFactory(team=team)
    event = EventFactory(
        name="Past",
        refer_program=program,
        goal="Speed",
        total=3000,
        date=date.today() - timedelta(days=2),
        hour_end=time(10, 0),
        vis_goal="after",
    )
    resp = athlete_client.get(f"/api/v1/events/{event.pk}/")
    assert resp.status_code == 200
    assert resp.json()["goal"] == "Speed"


def test_athlete_always_sees_everything(athlete_client, team):
    event = _make_event(team, vis_distance="always", vis_goal="always")
    resp = athlete_client.get(f"/api/v1/events/{event.pk}/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5000
    assert body["goal"] == "Endurance"


def test_manager_sees_everything_despite_never(owner_client, team):
    event = _make_event(team, vis_distance="never", vis_goal="never", vis_rounds="never")
    resp = owner_client.get(f"/api/v1/events/{event.pk}/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5000
    assert body["goal"] == "Endurance"


def test_athlete_rounds_never_emptied_in_event_detail(athlete_client, team):
    event = _make_event(team, vis_rounds="never")
    r = RoundFactory(sport=team.sport, language=team.language)
    event.rounds.add(r)
    resp = athlete_client.get(f"/api/v1/events/{event.pk}/")
    assert resp.status_code == 200
    assert resp.json()["rounds"] == []


def test_athlete_rounds_never_hidden_in_rounds_list(athlete_client, team):
    event = _make_event(team, vis_rounds="never")
    r = RoundFactory(sport=team.sport, language=team.language)
    event.rounds.add(r)
    resp = athlete_client.get("/api/v1/rounds/")
    assert resp.status_code == 200
    items = resp.json()
    items = items.get("results", items) if isinstance(items, dict) else items
    ids = [row["id"] for row in items]
    assert r.id not in ids


def test_athlete_rounds_always_visible_in_rounds_list(athlete_client, team):
    event = _make_event(team, vis_rounds="always")
    r = RoundFactory(sport=team.sport, language=team.language)
    event.rounds.add(r)
    resp = athlete_client.get("/api/v1/rounds/")
    assert resp.status_code == 200
    items = resp.json()
    items = items.get("results", items) if isinstance(items, dict) else items
    ids = [row["id"] for row in items]
    assert r.id in ids


def test_manager_rounds_visible_despite_never(owner_client, team):
    event = _make_event(team, vis_rounds="never")
    r = RoundFactory(sport=team.sport, language=team.language)
    event.rounds.add(r)
    resp = owner_client.get("/api/v1/rounds/")
    assert resp.status_code == 200
    items = resp.json()
    items = items.get("results", items) if isinstance(items, dict) else items
    ids = [row["id"] for row in items]
    assert r.id in ids
    # and in the event detail too
    ev = owner_client.get(f"/api/v1/events/{event.pk}/").json()
    assert r.id in ev["rounds"]

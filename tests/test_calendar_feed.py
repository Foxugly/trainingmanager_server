"""Tests for the per-user iCal (.ics) subscription feed + token rotation.

Coverage:
  - valid token -> 200 text/calendar
  - unknown token -> 404
  - includes the user's team events (athlete + coach) within the window
  - excludes out-of-window events and other teams' events
  - all-day VEVENT (no hours) vs timed VEVENT (DTSTART with a time)
  - goal hidden for an athlete when vis_goal is NEVER, shown for a manager
  - rotate endpoint changes the token (old -> 404, new works)
  - rotate requires authentication
"""

from datetime import date, time, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from icalendar import Calendar

from event.models import Event, VisibilityMode
from member.models import Member
from program.models import Program
from team.models import TeamMembership
from tests.factories import TeamFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def coach():
    return User.objects.create_user(
        email="cal_coach@local.test", password="pass"
    )


@pytest.fixture
def athlete():
    return User.objects.create_user(
        email="cal_athlete@local.test", password="pass"
    )


@pytest.fixture
def team(coach):
    return TeamFactory(
        owner=coach, is_active=True, is_public=False, timezone="Europe/Brussels"
    )


@pytest.fixture
def athlete_membership(athlete, team):
    member = Member.objects.create(
        firstname="A", lastname="Thlete", email=athlete.email, user=athlete
    )
    TeamMembership.objects.create(team=team, member=member)
    return member


@pytest.fixture
def program(team):
    return Program.objects.create(name="Base Program", team=team, is_active=True)


def _make_event(program, *, name, day, hour_start=None, hour_end=None,
                goal="", vis_goal=VisibilityMode.ALWAYS):
    return Event.objects.create(
        name=name,
        refer_program=program,
        date=day,
        hour_start=hour_start,
        hour_end=hour_end,
        goal=goal,
        vis_goal=vis_goal,
    )


def _feed_url(token):
    return reverse("calendar_feed", kwargs={"token": token})


def _parse(content):
    return Calendar.from_ical(content)


def _uids(cal):
    return {str(c.get("uid")) for c in cal.walk("VEVENT")}


# --------------------------------------------------------------------------
# Feed basics
# --------------------------------------------------------------------------
def test_feed_returns_calendar_for_valid_token(api_client, coach, program):
    today = timezone.now().date()
    _make_event(program, name="Session 1", day=today + timedelta(days=3))

    resp = api_client.get(_feed_url(coach.calendar_token))

    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/calendar; charset=utf-8"
    assert "trainingmanager.ics" in resp["Content-Disposition"]
    cal = _parse(resp.content)
    assert str(cal.get("x-wr-calname")) == "TrainingManager"
    assert len(cal.walk("VEVENT")) == 1


def test_feed_unknown_token_404(api_client):
    resp = api_client.get(_feed_url("does-not-exist-token"))
    assert resp.status_code == 404


def test_feed_includes_athlete_team_events(api_client, athlete, athlete_membership, program):
    today = timezone.now().date()
    ev = _make_event(program, name="Athlete Session", day=today + timedelta(days=2))

    resp = api_client.get(_feed_url(athlete.calendar_token))

    assert resp.status_code == 200
    cal = _parse(resp.content)
    assert f"event-{ev.id}@tm.foxugly.com" in _uids(cal)


def test_feed_includes_coach_team_events(api_client, coach, program):
    today = timezone.now().date()
    ev = _make_event(program, name="Coach Session", day=today + timedelta(days=2))

    resp = api_client.get(_feed_url(coach.calendar_token))

    cal = _parse(resp.content)
    assert f"event-{ev.id}@tm.foxugly.com" in _uids(cal)


def test_feed_excludes_out_of_window_and_other_teams(api_client, coach, program):
    today = timezone.now().date()
    in_window = _make_event(program, name="In window", day=today + timedelta(days=5))
    too_far = _make_event(program, name="Too far", day=today + timedelta(days=365))
    too_old = _make_event(program, name="Too old", day=today - timedelta(days=120))

    # An event in a completely unrelated team the coach is not part of.
    other_team = TeamFactory(is_active=True, timezone="Europe/Brussels")
    other_program = Program.objects.create(name="Other", team=other_team)
    other_event = _make_event(other_program, name="Other team", day=today + timedelta(days=5))

    resp = api_client.get(_feed_url(coach.calendar_token))

    uids = _uids(_parse(resp.content))
    assert f"event-{in_window.id}@tm.foxugly.com" in uids
    assert f"event-{too_far.id}@tm.foxugly.com" not in uids
    assert f"event-{too_old.id}@tm.foxugly.com" not in uids
    assert f"event-{other_event.id}@tm.foxugly.com" not in uids


# --------------------------------------------------------------------------
# All-day vs timed
# --------------------------------------------------------------------------
def test_timed_event_has_datetime_dtstart(api_client, coach, program):
    today = timezone.now().date()
    ev = _make_event(
        program, name="Timed", day=today + timedelta(days=1),
        hour_start=time(18, 0), hour_end=time(19, 30),
    )

    resp = api_client.get(_feed_url(coach.calendar_token))
    cal = _parse(resp.content)
    vevent = next(c for c in cal.walk("VEVENT")
                  if str(c.get("uid")) == f"event-{ev.id}@tm.foxugly.com")

    dtstart = vevent.get("dtstart").dt
    # A timed event yields a datetime (has hour/minute), not a bare date.
    assert hasattr(dtstart, "hour")
    assert dtstart.hour == 18 and dtstart.minute == 0
    assert vevent.get("dtend").dt.hour == 19


def test_all_day_event_has_date_dtstart(api_client, coach, program):
    today = timezone.now().date()
    day = today + timedelta(days=1)
    ev = _make_event(program, name="All day", day=day)  # no hours

    resp = api_client.get(_feed_url(coach.calendar_token))
    cal = _parse(resp.content)
    vevent = next(c for c in cal.walk("VEVENT")
                  if str(c.get("uid")) == f"event-{ev.id}@tm.foxugly.com")

    dtstart = vevent.get("dtstart").dt
    # All-day event: DTSTART is a plain date (no hour attribute).
    assert isinstance(dtstart, date) and not hasattr(dtstart, "hour")
    assert dtstart == day
    assert vevent.get("dtend").dt == day + timedelta(days=1)


# --------------------------------------------------------------------------
# Goal visibility
# --------------------------------------------------------------------------
def test_goal_hidden_from_athlete_when_vis_never(api_client, athlete, athlete_membership, program):
    today = timezone.now().date()
    ev = _make_event(
        program, name="Secret goal", day=today + timedelta(days=2),
        goal="Beat the PB", vis_goal=VisibilityMode.NEVER,
    )

    resp = api_client.get(_feed_url(athlete.calendar_token))
    cal = _parse(resp.content)
    vevent = next(c for c in cal.walk("VEVENT")
                  if str(c.get("uid")) == f"event-{ev.id}@tm.foxugly.com")
    desc = str(vevent.get("description") or "")
    assert "Beat the PB" not in desc


def test_goal_visible_to_manager_even_when_vis_never(api_client, coach, program):
    today = timezone.now().date()
    ev = _make_event(
        program, name="Manager goal", day=today + timedelta(days=2),
        goal="Beat the PB", vis_goal=VisibilityMode.NEVER,
    )

    resp = api_client.get(_feed_url(coach.calendar_token))
    cal = _parse(resp.content)
    vevent = next(c for c in cal.walk("VEVENT")
                  if str(c.get("uid")) == f"event-{ev.id}@tm.foxugly.com")
    desc = str(vevent.get("description") or "")
    assert "Beat the PB" in desc


def test_goal_visible_to_athlete_when_vis_always(api_client, athlete, athlete_membership, program):
    today = timezone.now().date()
    ev = _make_event(
        program, name="Open goal", day=today + timedelta(days=2),
        goal="Easy run", vis_goal=VisibilityMode.ALWAYS,
    )

    resp = api_client.get(_feed_url(athlete.calendar_token))
    cal = _parse(resp.content)
    vevent = next(c for c in cal.walk("VEVENT")
                  if str(c.get("uid")) == f"event-{ev.id}@tm.foxugly.com")
    assert "Easy run" in str(vevent.get("description") or "")


def test_goal_html_is_stripped_in_description(api_client, coach, program):
    """Goal is now rich-text HTML; the iCal DESCRIPTION must be plain text
    (tags stripped) — calendar clients render plain text only."""
    today = timezone.now().date()
    ev = _make_event(
        program, name="HTML goal", day=today + timedelta(days=2),
        goal="<p><b>Beat</b> the &amp; PB</p>", vis_goal=VisibilityMode.ALWAYS,
    )

    resp = api_client.get(_feed_url(coach.calendar_token))
    cal = _parse(resp.content)
    vevent = next(c for c in cal.walk("VEVENT")
                  if str(c.get("uid")) == f"event-{ev.id}@tm.foxugly.com")
    desc = str(vevent.get("description") or "")
    assert "Beat the & PB" in desc
    assert "<b>" not in desc
    assert "<p>" not in desc


# --------------------------------------------------------------------------
# LOCATION (always visible, no gating)
# --------------------------------------------------------------------------
def test_location_in_vevent_when_set(api_client, athlete, athlete_membership, program):
    today = timezone.now().date()
    ev = _make_event(program, name="At the pool", day=today + timedelta(days=2))
    ev.location = "Piscine communale, bassin 50m"
    ev.save(update_fields=["location"])

    resp = api_client.get(_feed_url(athlete.calendar_token))
    cal = _parse(resp.content)
    vevent = next(c for c in cal.walk("VEVENT")
                  if str(c.get("uid")) == f"event-{ev.id}@tm.foxugly.com")
    assert str(vevent.get("location")) == "Piscine communale, bassin 50m"


def test_no_location_property_when_blank(api_client, coach, program):
    today = timezone.now().date()
    ev = _make_event(program, name="No venue", day=today + timedelta(days=2))

    resp = api_client.get(_feed_url(coach.calendar_token))
    cal = _parse(resp.content)
    vevent = next(c for c in cal.walk("VEVENT")
                  if str(c.get("uid")) == f"event-{ev.id}@tm.foxugly.com")
    assert vevent.get("location") is None


# --------------------------------------------------------------------------
# Token exposure on /me/ and rotation
# --------------------------------------------------------------------------
def test_me_exposes_calendar_token(auth_client, authenticated_user):
    resp = auth_client.get(reverse("me"))
    assert resp.status_code == 200
    assert resp.data["calendar_token"] == authenticated_user.calendar_token


def test_rotate_changes_token_old_404_new_works(api_client, coach, program):
    today = timezone.now().date()
    _make_event(program, name="S", day=today + timedelta(days=2))
    old_token = coach.calendar_token

    # Old token works first.
    assert api_client.get(_feed_url(old_token)).status_code == 200

    api_client.force_authenticate(user=coach)
    resp = api_client.post(reverse("me_calendar_token_rotate"))
    assert resp.status_code == 200
    new_token = resp.data["calendar_token"]
    assert new_token != old_token

    api_client.force_authenticate(user=None)
    # Old URL is now dead, new URL works.
    assert api_client.get(_feed_url(old_token)).status_code == 404
    assert api_client.get(_feed_url(new_token)).status_code == 200

    coach.refresh_from_db()
    assert coach.calendar_token == new_token


def test_rotate_requires_authentication(api_client):
    resp = api_client.post(reverse("me_calendar_token_rotate"))
    assert resp.status_code in (401, 403)

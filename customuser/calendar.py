"""Per-user iCal (.ics) subscription feed.

Builds a VCALENDAR of the training sessions (events) belonging to every
team a user is involved with — as owner/manager or as an active athlete
member — so they can subscribe in any RFC 5545 calendar client
(Google/Apple/Outlook/Thunderbird/Proton/Nextcloud, ...).

The feed is authenticated solely by the opaque ``calendar_token`` in the
URL, so it must never leak anything an athlete is not allowed to see: the
session goal is only included when the requesting user is a coach of the
event's team, or when ``aspect_visible_to_athlete('goal')`` is True.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone as dj_timezone
from icalendar import Calendar, Event as ICalEvent

# Sliding window around "now" (in days) for which events are published to
# the feed. Calendar clients poll periodically; a bounded window keeps the
# payload small while still showing recent history and the near future.
CALENDAR_WINDOW_PAST_DAYS = 30
CALENDAR_WINDOW_FUTURE_DAYS = 180

# Stable UID domain for VEVENTs (RFC 5545 wants a globally-unique UID; the
# event PK + this domain is stable across regenerations so clients update
# in place instead of duplicating).
UID_DOMAIN = "tm.foxugly.com"

CALENDAR_NAME = "TrainingManager"


def _user_team_ids(user):
    """IDs of every team the user is involved with.

    Coach side: teams they own or manage.
    Athlete side: teams where their linked Member has an ACTIVE membership
    (TeamMembership.left_at IS NULL).
    """
    from team.models import Team, TeamMembership

    team_ids = set(
        Team.objects.filter(Q(owner=user) | Q(managers=user))
        .values_list("id", flat=True)
        .distinct()
    )

    member = getattr(user, "member_profile", None)
    if member is not None:
        athlete_team_ids = TeamMembership.objects.filter(
            member=member, left_at__isnull=True
        ).values_list("team_id", flat=True)
        team_ids.update(athlete_team_ids)

    return team_ids


def _is_coach(user, team):
    """True if the user is the owner or a manager of the team."""
    if team is None:
        return False
    return team.owner_id == user.id or team.managers.filter(pk=user.pk).exists()


def _events_for_user(user, now=None):
    """Scheduled events within the window across all of the user's teams."""
    from event.models import Event

    if now is None:
        now = dj_timezone.now()
    today = now.date()
    start = today - timedelta(days=CALENDAR_WINDOW_PAST_DAYS)
    end = today + timedelta(days=CALENDAR_WINDOW_FUTURE_DAYS)

    team_ids = _user_team_ids(user)
    if not team_ids:
        return Event.objects.none()

    return (
        Event.objects.filter(
            refer_program__team_id__in=team_ids,
            date__isnull=False,
            date__gte=start,
            date__lte=end,
        )
        .select_related("refer_program", "refer_program__team")
        .order_by("date", "hour_start")
    )


def _goal_visible(user, event):
    """Whether this user may see the event's goal.

    Coaches (owner/manager of the event's team) always see it; athletes
    only when the per-aspect visibility resolves to True for them.
    """
    if not event.goal:
        return False
    if _is_coach(user, event.team):
        return True
    return event.aspect_visible_to_athlete("goal")


def _add_vevent(cal, user, event, dtstamp):
    """Append one VEVENT for ``event`` to ``cal``."""
    team = event.team
    tz_name = getattr(team, "timezone", None) or "UTC"
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = ZoneInfo("UTC")

    vevent = ICalEvent()
    vevent.add("uid", f"event-{event.id}@{UID_DOMAIN}")
    vevent.add("dtstamp", dtstamp)
    vevent.add("sequence", 0)

    # DTSTART/DTEND: timed when hour_start is present, otherwise an
    # all-day event (DATE value type per RFC 5545).
    if event.hour_start is not None:
        start_dt = datetime.combine(event.date, event.hour_start, tzinfo=tzinfo)
        if event.hour_end is not None:
            end_dt = datetime.combine(event.date, event.hour_end, tzinfo=tzinfo)
            # Guard a same/earlier end (bad data): fall back to +1h.
            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(hours=1)
        else:
            end_dt = start_dt + timedelta(hours=1)
        vevent.add("dtstart", start_dt)
        vevent.add("dtend", end_dt)
    else:
        # All-day: DTSTART is a DATE, DTEND is the (exclusive) next day.
        vevent.add("dtstart", event.date)
        vevent.add("dtend", event.date + timedelta(days=1))

    # SUMMARY: "[Team] name" when we have a team name.
    team_name = team.name if team is not None else None
    summary = f"[{team_name}] {event.name}" if team_name else event.name
    vevent.add("summary", summary)

    # DESCRIPTION: program name + goal (only when visible to this user).
    description_parts = []
    program = event.refer_program
    if program is not None and program.name:
        description_parts.append(program.name)
    if _goal_visible(user, event):
        description_parts.append(f"Goal: {event.goal}")
    if description_parts:
        vevent.add("description", "\n".join(description_parts))

    # LOCATION: always visible to athletes (no visibility gating), so emit
    # it verbatim whenever the event has one.
    if event.location:
        vevent.add("location", event.location)

    cal.add_component(vevent)


def build_calendar_for_user(user, now=None):
    """Return the .ics bytes of ``user``'s personal training calendar."""
    cal = Calendar()
    cal.add("prodid", "-//Foxugly//TrainingManager//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", CALENDAR_NAME)

    dtstamp = now or dj_timezone.now()
    for event in _events_for_user(user, now=now):
        _add_vevent(cal, user, event, dtstamp)

    # icalendar emits RFC 5545-correct CRLF line endings + folding.
    return cal.to_ical()


__all__ = [
    "build_calendar_for_user",
    "CALENDAR_WINDOW_PAST_DAYS",
    "CALENDAR_WINDOW_FUTURE_DAYS",
    "CALENDAR_NAME",
]

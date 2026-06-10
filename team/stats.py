"""Pure aggregation functions behind the team statistics endpoints.

Extracted from ``TeamViewSet`` (they were ``@staticmethod``/``@classmethod``
namespaced on the viewset) so they can be unit-tested directly and so the
viewset stays a thin HTTP layer. ``assemble_stats`` is the entry point used by
both ``teams/{id}/stats/`` and the AI ``review-block`` endpoint; permission /
scope resolution stays on the viewset (``_resolve_member_scope``).

Each function takes plain data (event dicts, id lists, name maps) and returns
plain dicts the serializers render — no request/permission coupling.
"""

import datetime

from django.db import models
from django.db.models import Count, F, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers as drf_serializers

# Default / clamp bounds for the stats date-range window.
STATS_DEFAULT_DAYS = 84  # 12 weeks; used when from/to are absent.
STATS_MAX_SPAN_DAYS = 731  # ~2 years; the window is clamped to this span.


def parse_window(request):
    """Parse the [from, to] date window from query params.

    Defaults: both absent -> to=today, from=today-84d. A single bound
    fills the other (to defaults today; from defaults to-84d). Malformed
    dates -> 400. from > to -> 400. The span is clamped to
    STATS_MAX_SPAN_DAYS by pulling `from` forward.
    """
    today = timezone.localdate()

    def _parse(value, field):
        if value is None or value == "":
            return None
        try:
            return datetime.date.fromisoformat(value)
        except (TypeError, ValueError):
            raise drf_serializers.ValidationError(
                {field: _("Invalid date. Use ISO format YYYY-MM-DD.")},
                code="invalid_date",
            )

    raw_from = request.query_params.get("from")
    raw_to = request.query_params.get("to")

    date_from = _parse(raw_from, "from")
    date_to = _parse(raw_to, "to")

    if date_to is None:
        date_to = today
    if date_from is None:
        date_from = date_to - datetime.timedelta(days=STATS_DEFAULT_DAYS)

    if date_from > date_to:
        raise drf_serializers.ValidationError(
            {"from": _("`from` must be on or before `to`.")},
            code="invalid_range",
        )

    # Clamp the span to a sane maximum by pulling `from` forward.
    if (date_to - date_from).days > STATS_MAX_SPAN_DAYS:
        date_from = date_to - datetime.timedelta(days=STATS_MAX_SPAN_DAYS)

    return date_from, date_to


def assemble_stats(team, date_from, date_to, scope_member=None):
    """Build the team-stats payload dict for [date_from, date_to].

    ``scope_member`` is ``None`` (team aggregate) or a ``{"id", "name"}``
    dict (per-athlete scope). Shared by the ``stats`` endpoint and the
    AI ``review_block`` endpoint (which always passes ``None``).
    """
    from event.models import Event

    # All of this team's events in the window. Filtering on the related
    # program.team keeps the scope strict.
    events = list(
        Event.objects.filter(
            refer_program__team=team,
            date__isnull=False,
            date__gte=date_from,
            date__lte=date_to,
        )
        .order_by("date", "id")
        .values("id", "name", "date", "total")
    )
    event_ids = [e["id"] for e in events]

    # Expected head-count for attendance "total": current active athlete
    # members of the team. Documented simplification: the same expected
    # roster applies to every session in the window (we do not reconstruct
    # the historical roster per event date).
    active_members = list(
        team.memberships.filter(left_at__isnull=True)
        .select_related("member")
        .values(
            "member_id",
            "member__firstname",
            "member__lastname",
        )
    )
    member_names = {
        m["member_id"]: (
            f"{m['member__firstname']} {m['member__lastname']}".strip()
        )
        for m in active_members
    }
    expected_per_session = len(active_members)

    member_id = scope_member["id"] if scope_member else None

    return {
        "period": {"from": date_from, "to": date_to},
        "member": scope_member,
        "attendance": attendance_stats(
            event_ids, events, member_names, expected_per_session, member_id
        ),
        "volume": volume_stats(event_ids, events, member_names, member_id),
        "intensity": intensity_stats(event_ids, member_id),
        "roti": roti_stats(event_ids, events, member_id),
    }


def attendance_stats(event_ids, events, member_names, expected_per_session, member_id=None):
    """Attendance present-counts grouped per event and per member.

    "present" = Attendance rows whose status.code == 'present'.

    Team aggregate (member_id is None): "total" per session = number of
    currently-active athlete members; by_member spans all members.

    Per-athlete scope (member_id set): by_session is that member's
    personal timeline (present 0/1, total 1 per session), team_rate is
    their overall present rate, by_member is the single scoped member.
    """
    from attendance.models import Attendance

    if not event_ids:
        return {"team_rate": None, "by_session": [], "by_member": []}

    if member_id is not None:
        return attendance_stats_member(event_ids, events, member_names, member_id)

    # present count per event
    per_event = dict(
        Attendance.objects.filter(event_id__in=event_ids, status__code="present")
        .values_list("event_id")
        .annotate(n=Count("id"))
        .values_list("event_id", "n")
    )

    by_session = []
    total_present = 0
    for e in events:
        present = per_event.get(e["id"], 0)
        total_present += present
        total = expected_per_session
        rate = (present / total) if total else None
        by_session.append(
            {
                "event_id": e["id"],
                "name": e["name"],
                "date": e["date"],
                "present": present,
                "total": total,
                "rate": rate,
            }
        )

    total_expected = expected_per_session * len(events)
    team_rate = (total_present / total_expected) if total_expected else None

    # per-member present count + last present date across the window
    present_rows = (
        Attendance.objects.filter(event_id__in=event_ids, status__code="present")
        .values("member_id")
        .annotate(
            present=Count("id"),
            last_present_date=models.Max("event__date"),
        )
    )
    present_by_member = {r["member_id"]: r for r in present_rows}

    session_count = len(events)
    by_member = []
    for member_id_, name in member_names.items():
        row = present_by_member.get(member_id_)
        present = row["present"] if row else 0
        last_present = row["last_present_date"] if row else None
        rate = (present / session_count) if session_count else None
        by_member.append(
            {
                "member_id": member_id_,
                "name": name,
                "present": present,
                "total": session_count,
                "rate": rate,
                "last_present_date": last_present,
                # Streak is a per-athlete metric; not computed on the aggregate.
                "streak": None,
            }
        )
    by_member.sort(key=lambda m: m["name"].lower())

    return {
        "team_rate": team_rate,
        "by_session": by_session,
        "by_member": by_member,
    }


def attendance_stats_member(event_ids, events, member_names, member_id):
    """Per-athlete attendance: personal timeline + overall rate."""
    from attendance.models import Attendance

    present_event_ids = set(
        Attendance.objects.filter(
            event_id__in=event_ids,
            status__code="present",
            member_id=member_id,
        ).values_list("event_id", flat=True)
    )

    by_session = []
    total_present = 0
    last_present_date = None
    for e in events:
        present = 1 if e["id"] in present_event_ids else 0
        total_present += present
        if present:
            if last_present_date is None or e["date"] > last_present_date:
                last_present_date = e["date"]
        by_session.append(
            {
                "event_id": e["id"],
                "name": e["name"],
                "date": e["date"],
                "present": present,
                "total": 1,
                "rate": float(present),
            }
        )

    session_count = len(events)
    team_rate = (total_present / session_count) if session_count else None

    # Current streak: consecutive present sessions counting back from the
    # most recent (chronologically), stopping at the first absence.
    streak = 0
    for s in sorted(by_session, key=lambda x: (x["date"] is None, x["date"]), reverse=True):
        if s["present"]:
            streak += 1
        else:
            break

    name = member_names.get(member_id) or f"member #{member_id}"
    by_member = [
        {
            "member_id": member_id,
            "name": name,
            "present": total_present,
            "total": session_count,
            "rate": team_rate,
            "last_present_date": last_present_date,
            "streak": streak,
        }
    ]

    return {
        "team_rate": team_rate,
        "by_session": by_session,
        "by_member": by_member,
    }


def roti_stats(event_ids, events, member_id):
    """Per-athlete ROTI (return-on-training-investment, 1-5) trend over the
    window: a date-ordered score series + average. ROTI is per athlete, so
    the team aggregate (member_id is None) returns an empty series."""
    if member_id is None or not event_ids:
        return {"series": [], "average": None, "count": 0}
    from roti.models import Roti

    score_by_event = dict(
        Roti.objects.filter(event_id__in=event_ids, member_id=member_id).values_list(
            "event_id", "score"
        )
    )
    series = []
    total = 0
    for e in events:
        score = score_by_event.get(e["id"])
        if score is None:
            continue
        series.append(
            {"event_id": e["id"], "name": e["name"], "date": e["date"], "score": score}
        )
        total += score
    series.sort(key=lambda x: (x["date"] is None, x["date"]))
    count = len(series)
    average = (total / count) if count else None
    return {"series": series, "average": average, "count": count}


def volume_stats(event_ids, events, member_names, member_id=None):
    """Training volume = sum of Event.total over the window.

    Team aggregate (member_id is None): total_distance counts each
    session once; by_week buckets by ISO Monday; by_member attributes a
    session's full distance to every member present at it.

    Per-athlete scope (member_id set): total_distance / by_week only
    count sessions where THAT member was present (their personal volume);
    by_member is the single scoped member.
    """
    from attendance.models import Attendance

    if not event_ids:
        return {"total_distance": 0, "by_week": [], "by_member": []}

    if member_id is not None:
        present_event_ids = set(
            Attendance.objects.filter(
                event_id__in=event_ids,
                status__code="present",
                member_id=member_id,
            ).values_list("event_id", flat=True)
        )
        scoped_events = [e for e in events if e["id"] in present_event_ids]
        total_distance = sum(e["total"] for e in scoped_events)
        by_week = bucket_by_week(scoped_events)
        name = member_names.get(member_id) or f"member #{member_id}"
        by_member = (
            [{"member_id": member_id, "name": name, "distance": total_distance}]
            if total_distance
            else [{"member_id": member_id, "name": name, "distance": 0}]
        )
        return {
            "total_distance": total_distance,
            "by_week": by_week,
            "by_member": by_member,
        }

    total_distance = sum(e["total"] for e in events)
    by_week = bucket_by_week(events)

    # by_member — attribute each present session's distance to the member.
    event_total = {e["id"]: e["total"] for e in events}
    present_links = Attendance.objects.filter(
        event_id__in=event_ids, status__code="present"
    ).values_list("member_id", "event_id")

    member_distance: dict[int, int] = {}
    for m_id, event_id in present_links:
        member_distance[m_id] = member_distance.get(m_id, 0) + event_total.get(event_id, 0)

    by_member = [
        {
            "member_id": m_id,
            "name": member_names.get(m_id) or f"member #{m_id}",
            "distance": dist,
        }
        for m_id, dist in member_distance.items()
    ]
    by_member.sort(key=lambda m: (-m["distance"], m["name"].lower()))

    return {
        "total_distance": total_distance,
        "by_week": by_week,
        "by_member": by_member,
    }


def bucket_by_week(events):
    """Bucket events' distance by the ISO Monday of their date (Python,
    DB-agnostic, small N of sessions)."""
    week_buckets: dict[datetime.date, int] = {}
    for e in events:
        d = e["date"]
        monday = d - datetime.timedelta(days=d.weekday())
        week_buckets[monday] = week_buckets.get(monday, 0) + e["total"]
    return [
        {"week_start": wk, "distance": dist}
        for wk, dist in sorted(week_buckets.items())
    ]


def intensity_stats(event_ids, member_id=None):
    """Distance per energy zone across the window's exercises.

    zone distance = sum(exercise.distance * exercise.repetition *
    round.count) grouped by exercise.energysegment.abv. The localized
    segment description (modeltranslation, active request language) is
    returned as `label`. Ordered by abv (Z0..Z7).

    Per-athlete scope (member_id set): restricts to the sessions THAT
    member was present at (attributing session exercises to the member).
    """
    from exercise.models import Exercise

    if not event_ids:
        return {"by_segment": []}

    if member_id is not None:
        from attendance.models import Attendance

        event_ids = list(
            Attendance.objects.filter(
                event_id__in=event_ids,
                status__code="present",
                member_id=member_id,
            ).values_list("event_id", flat=True)
        )
        if not event_ids:
            return {"by_segment": []}

    # Exercises reachable from the window's events:
    #   Event -(M2M)- Round -(M2M)- Exercise
    # Multiply the exercise's own reps*distance by the parent round's
    # count. The same exercise can appear under multiple rounds/events;
    # each occurrence contributes (this is intentional — total training
    # load over the period).
    rows = (
        Exercise.objects.filter(
            round__event__id__in=event_ids,
            energysegment__isnull=False,
        )
        .annotate(
            seg_abv=F("energysegment__abv"),
            contrib=F("distance") * F("repetition") * F("round__count"),
        )
        .values("seg_abv", "energysegment_id")
        .annotate(distance=Sum("contrib"))
    )

    # Resolve localized labels once per segment id.
    from exercise.models import EnergySegment

    seg_ids = {r["energysegment_id"] for r in rows}
    labels = {s.id: s.description for s in EnergySegment.objects.filter(id__in=seg_ids)}

    by_segment = [
        {
            "abv": r["seg_abv"],
            "label": labels.get(r["energysegment_id"]),
            "distance": r["distance"] or 0,
        }
        for r in rows
    ]
    by_segment.sort(key=lambda s: s["abv"])
    return {"by_segment": by_segment}


def rsvp_reliability(team, date_from, date_to):
    """Per-athlete RSVP reliability over [date_from, date_to].

    For each member who RSVP'd GOING to events in the window, how often did they
    actually show up? ``shows`` = GOING + a 'present' attendance for the same
    event; ``no_shows`` = GOING but not present (absent or no record);
    ``reliability`` = shows / going. Sorted worst-reliability first (most
    no-shows surface to the coach), then by name. Members with no GOING RSVP in
    the window are omitted. Returns a list of dicts.
    """
    from attendance.models import Attendance
    from event.models import Event
    from rsvp.models import Rsvp, RsvpStatus

    event_ids = list(
        Event.objects.filter(
            refer_program__team=team,
            date__isnull=False,
            date__gte=date_from,
            date__lte=date_to,
        ).values_list("id", flat=True)
    )
    if not event_ids:
        return []

    going_rows = Rsvp.objects.filter(
        event_id__in=event_ids, status=RsvpStatus.GOING
    ).values_list("member_id", "event_id", "member__firstname", "member__lastname")

    present = set(
        Attendance.objects.filter(
            event_id__in=event_ids, status__code="present"
        ).values_list("member_id", "event_id")
    )

    per_member: dict[int, dict] = {}
    for member_id, event_id, firstname, lastname in going_rows:
        entry = per_member.setdefault(
            member_id,
            {
                "member_id": member_id,
                "name": f"{firstname} {lastname}".strip() or f"member #{member_id}",
                "going": 0,
                "shows": 0,
            },
        )
        entry["going"] += 1
        if (member_id, event_id) in present:
            entry["shows"] += 1

    entries = []
    for entry in per_member.values():
        going = entry["going"]
        shows = entry["shows"]
        entry["no_shows"] = going - shows
        entry["reliability"] = (shows / going) if going else None
        entries.append(entry)

    # Worst reliability first (the coach's actionable list), then name.
    entries.sort(key=lambda e: (e["reliability"] if e["reliability"] is not None else 1.0, e["name"].lower()))
    return entries


# Drift threshold: how far an athlete's mean ROTI must sit from the squad mean
# (on a 1..5 scale) to be flagged as rating the sessions notably harder/easier.
ROTI_DRIFT_THRESHOLD = 0.75


def roti_drift(team, date_from, date_to):
    """Per-athlete perceived-effort (ROTI) drift vs the squad over the window.

    Compares each athlete's mean ROTI to the squad mean. An athlete rating
    sessions consistently HARDER than peers (delta >= +threshold) may be
    overreaching; consistently EASIER (delta <= -threshold) may be
    underchallenged. Returns ``{squad_average, count, entries}`` where each
    entry is ``{member_id, name, average, count, delta, flag}`` (flag ∈
    high/low/normal), sorted by |delta| desc (most divergent first).
    """
    from event.models import Event
    from roti.models import Roti

    event_ids = list(
        Event.objects.filter(
            refer_program__team=team,
            date__isnull=False,
            date__gte=date_from,
            date__lte=date_to,
        ).values_list("id", flat=True)
    )
    if not event_ids:
        return {"squad_average": None, "count": 0, "entries": []}

    rows = Roti.objects.filter(event_id__in=event_ids).values_list(
        "member_id", "score", "member__firstname", "member__lastname"
    )

    per_member: dict[int, dict] = {}
    total = 0
    n = 0
    for member_id, score, firstname, lastname in rows:
        total += score
        n += 1
        entry = per_member.setdefault(
            member_id,
            {
                "member_id": member_id,
                "name": f"{firstname} {lastname}".strip() or f"member #{member_id}",
                "_sum": 0,
                "count": 0,
            },
        )
        entry["_sum"] += score
        entry["count"] += 1

    if n == 0:
        return {"squad_average": None, "count": 0, "entries": []}

    squad_average = total / n

    entries = []
    for entry in per_member.values():
        avg = entry["_sum"] / entry["count"]
        delta = avg - squad_average
        if delta >= ROTI_DRIFT_THRESHOLD:
            flag = "high"
        elif delta <= -ROTI_DRIFT_THRESHOLD:
            flag = "low"
        else:
            flag = "normal"
        entries.append(
            {
                "member_id": entry["member_id"],
                "name": entry["name"],
                "average": round(avg, 2),
                "count": entry["count"],
                "delta": round(delta, 2),
                "flag": flag,
            }
        )

    entries.sort(key=lambda e: (-abs(e["delta"]), e["name"].lower()))
    return {"squad_average": round(squad_average, 2), "count": n, "entries": entries}

"""AI-driven training plan generation for Program."""

import logging
from datetime import date as _date

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from tools.ai import AIServiceError, call_claude_with_tool, truncate_for_log
from tools.i18n import resolve_language_label

logger = logging.getLogger(__name__)


ALLOWED_COLORS = [
    "#3498db",  # blue
    "#2ecc71",  # green
    "#e74c3c",  # red
    "#f39c12",  # orange
    "#9b59b6",  # purple
    "#1abc9c",  # turquoise
]


PLAN_TOOL_SCHEMA = {
    "name": "create_training_plan",
    "description": (
        "Generate a training plan as a list of high-level Events (one per "
        "planned session). Each Event has a short name, a goal, a date, a "
        "start and end time, a location (venue/pool), an approximate total "
        "distance, and a color from the allowed palette."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "maxLength": 100,
                            "description": "Short, descriptive session name",
                        },
                        "goal": {
                            "type": "string",
                            "maxLength": 100,
                            "description": "Main goal of the session",
                        },
                        "date": {
                            "type": "string",
                            "format": "date",
                            "description": "Date in YYYY-MM-DD format",
                        },
                        "hour_start": {
                            "type": "string",
                            "pattern": "^[0-9]{2}:[0-9]{2}$",
                            "description": "Start time, 24h HH:MM (e.g. 18:00)",
                        },
                        "hour_end": {
                            "type": "string",
                            "pattern": "^[0-9]{2}:[0-9]{2}$",
                            "description": "End time, 24h HH:MM, after hour_start (e.g. 19:30)",
                        },
                        "location": {
                            "type": "string",
                            "maxLength": 255,
                            "description": (
                                "Venue / pool where the session takes place. "
                                "When the coach's instructions name a venue for "
                                "this session, set this to the EXACT name of the "
                                "matching known venue listed in the prompt. If "
                                "the coach names a venue that is not in that list, "
                                "use that new name verbatim. Otherwise reuse the "
                                "default venue; leave empty only if none is known."
                            ),
                        },
                        "total_distance": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Approximate total distance (meters)",
                        },
                        "color": {
                            "type": "string",
                            "enum": ALLOWED_COLORS,
                            "description": "Color from the allowed palette",
                        },
                    },
                    "required": [
                        "name",
                        "goal",
                        "date",
                        "hour_start",
                        "hour_end",
                        "total_distance",
                        "color",
                    ],
                },
            },
            "rationale": {
                "type": "string",
                "description": "Overall plan explanation (3-5 sentences)",
            },
        },
        "required": ["events", "rationale"],
    },
}


def build_system_prompt(sport_name):
    return (
        f"You are an expert coach in {sport_name} training planning. "
        f"You generate structured and progressive training plans for athletes. "
        f"You adapt frequency, intensity, and variety based on the goals. "
        f"You MUST always respond using the 'create_training_plan' tool. "
        f"Never write free-form text."
    )


# Weekday int -> English name (Python date.weekday() convention: Monday=0).
# The prompt is written in English; the AI is still asked to output session
# names/goals/rationale in the team language.
_WEEKDAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def _format_place(place):
    """Render one managed venue as 'Name (address)' (address optional)."""
    name = (place.get("name") or "").strip()
    address = (place.get("address") or "").strip()
    return f"{name} ({address})" if address else name


def _format_slot(slot):
    """Render one template slot as 'Monday 18:00–19:30' for the prompt."""

    def _hhmm(value):
        # value may be a datetime.time or an 'HH:MM[:SS]' string.
        if hasattr(value, "strftime"):
            return value.strftime("%H:%M")
        return str(value)[:5]

    name = _WEEKDAY_NAMES[slot["weekday"]] if 0 <= slot["weekday"] <= 6 else "?"
    return f"{name} {_hhmm(slot['hour_start'])}–{_hhmm(slot['hour_end'])}"


def build_user_prompt(
    *,
    sport_name,
    language,
    date_start,
    date_end,
    frequency_per_week,
    description,
    team=None,
    pools=None,
    places=None,
    additional_prompt="",
    slots=None,
    default_pool="",
):
    duration_days = (date_end - date_start).days + 1
    weeks = max(duration_days // 7, 1)
    expected_events = weeks * frequency_per_week
    language_label = resolve_language_label(language)

    # Localized name/description: modeltranslation returns the active-language
    # value (the prompt resolves the team language).
    level_line = ""
    if team and team.level:
        level_line = f"- Team skill level: {team.level.name} — {team.level.description}\n"

    has_template = bool(slots)
    location_hint = (default_pool or "").strip()

    # Managed team venues (Lieux) take priority as the "known venues" list the
    # AI must map the coach's venue mentions to; fall back to the distinct
    # locations already used on the team's events. Listing them (with address)
    # lets the AI pick the right venue per day and reuse the exact name so the
    # response parser can link each session to its Place.
    if places:
        venue_lines = [v for v in (_format_place(p) for p in places) if v]
    elif pools:
        venue_lines = list(pools)
    else:
        venue_lines = []
    venues_block = ""
    if venue_lines:
        venues_block = (
            "- Known venues for this team — when the coach indicates a venue for "
            "a session, set its 'location' to the EXACT venue name below (you may "
            "introduce a new venue name if the coach names one not listed):\n"
            + "".join(f"    - {v}\n" for v in venue_lines)
        )

    if has_template:
        slots_text = ", ".join(_format_slot(s) for s in slots)
        pool_text = location_hint if location_hint else "(no fixed venue)"
        constraints = (
            f"Generate a training plan with these constraints:\n"
            f"- Sport: {sport_name}\n"
            f"{level_line}"
            f"- Period: from {date_start.isoformat()} to {date_end.isoformat()} "
            f"({duration_days} days, ~{weeks} weeks)\n"
            f"- The team trains on these FIXED weekly slots: {slots_text} "
            f"at {pool_text}.\n"
            f"{venues_block}"
            f"- Description and constraints provided by the coach: "
            f"{description or '(none)'}\n"
        )
        default_loc_clause = (
            f"location={pool_text}" if location_hint else "location left empty"
        )
        location_instruction = (
            f"- For each week in the period, create exactly one session per "
            f"slot, ON that weekday and at that hour_start/hour_end. Use "
            f"{default_loc_clause} by default, UNLESS the coach's instructions "
            f"assign a specific venue to a given day/slot — then set 'location' "
            f"to that venue's exact name (a known venue above, or a new name the "
            f"coach provides).\n"
        )
        progression_instruction = (
            "- Keep a coherent intensity progression across the sessions "
            "(endurance, technique, intensity, recovery).\n"
        )
    else:
        constraints = (
            f"Generate a training plan with these constraints:\n"
            f"- Sport: {sport_name}\n"
            f"{level_line}"
            f"- Period: from {date_start.isoformat()} to {date_end.isoformat()} "
            f"({duration_days} days, ~{weeks} weeks)\n"
            f"- Frequency: {frequency_per_week} sessions per week "
            f"(~{expected_events} sessions total)\n"
            f"{venues_block}"
            f"- Description and constraints provided by the coach: "
            f"{description or '(none)'}\n"
        )
        location_instruction = (
            f"- Generate approximately {expected_events} sessions distributed "
            f"intelligently across the period. Each session must have a date "
            f"within the requested range.\n"
            f"- Set a realistic start time (hour_start) and end time (hour_end, "
            f"after the start) for every session — keep them consistent (typical "
            f"training slots) unless the coach's description implies otherwise.\n"
            f"- Set a 'location' for every session: when the coach names a venue "
            f"for a given day use that venue's exact name (a known venue above, or "
            f"a new name the coach provides); otherwise reuse one known venue "
            f"consistently. Leave location empty only if no venue is known.\n"
        )
        progression_instruction = (
            "- Vary effort types (endurance, technique, intensity, recovery) "
            "following a coherent progression.\n"
        )

    base = (
        f"{constraints}"
        f"\n"
        f"IMPORTANT instructions:\n"
        f"- The 'Description and constraints' above is provided by the coach "
        f"in {language_label}. It may contain critical information about the "
        f"athletes' age category, current performance level, training goals, "
        f"available equipment, or other constraints. Take ALL of this "
        f"information into account when designing the plan.\n"
        f"{location_instruction}"
        f"{progression_instruction}"
        f"- Respond ENTIRELY in {language_label}: all event names, goals, "
        f"and the rationale must be in {language_label}.\n"
        f"- Use the 'create_training_plan' tool only.\n"
    )

    extra = (additional_prompt or "").strip()
    if extra:
        # Appended AFTER the structured context as a marked block to deter
        # prompt-injection: the system rules above stay binding even if the
        # coach's text says "ignore previous instructions".
        override_note = (
            "These take precedence over the defaults above. The fixed weekly "
            "slots are the default schedule, but if these instructions CLEARLY "
            "ask for a different schedule (different days, times, frequency or "
            "venue), follow the coach's instructions instead. Stay consistent "
            "with the team sport, language, and the requested period.\n"
            if has_template
            else (
                "These take precedence over generic defaults but must remain "
                "consistent with the team sport, language, and the requested "
                "period.\n"
            )
        )
        base += (
            "\n---\n"
            "Additional instructions provided by the coach:\n"
            f"{override_note}"
            f"{extra}\n"
            "---\n"
        )

    return base


def _parse_date_strict(s):
    try:
        return _date.fromisoformat(s)
    except (TypeError, ValueError):
        logger.warning("AI returned invalid date value: %r", s)
        raise AIServiceError(_("AI returned an invalid date format."))


def generate_plan(
    *,
    program,
    date_start,
    date_end,
    frequency_per_week,
    description,
    user=None,
    additional_prompt="",
    slots=None,
    default_pool="",
):
    sport_name = program.team.sport.name if program.team.sport else "the practiced sport"
    language = program.team.language

    duration_days = (date_end - date_start).days + 1
    weeks = max(duration_days // 7, 1)
    expected_events = weeks * frequency_per_week
    logger.info(
        "generate_plan inputs: program=%s team=%s sport=%r language=%r "
        "date_start=%s date_end=%s duration_days=%s weeks=%s "
        "frequency_per_week=%s expected_events=%s description_len=%s description=%r "
        "additional_prompt_len=%s",
        program.pk,
        program.team_id,
        sport_name,
        language,
        date_start,
        date_end,
        duration_days,
        weeks,
        frequency_per_week,
        expected_events,
        len(description or ""),
        description,
        len(additional_prompt or ""),
    )

    # When a default_pool is set on the team it supersedes the pools derived
    # from existing events. Otherwise fall back to the distinct, non-empty
    # locations already used across this team's events so the AI reuses a real
    # venue for each generated session.
    location_hint = (default_pool or "").strip()

    # The team's managed venues (Lieux) are the canonical "known venues" list
    # given to the AI so it can map the coach's per-day venue mentions to a real
    # Place. Fall back to the distinct free-text locations already used on this
    # team's events only when no Place is configured yet.
    from place.models import Place

    places = list(
        Place.objects.filter(team_id=program.team_id)
        .order_by("name")
        .values("name", "address")
    )
    if places:
        pools = []
    else:
        from event.models import Event

        pools = list(
            Event.objects.filter(refer_program__team_id=program.team_id)
            .exclude(location="")
            .values_list("location", flat=True)
            .distinct()
            .order_by("location")
        )

    system = build_system_prompt(sport_name)
    user_prompt = build_user_prompt(
        sport_name=sport_name,
        language=language,
        date_start=date_start,
        date_end=date_end,
        frequency_per_week=frequency_per_week,
        description=description,
        team=program.team,
        pools=pools,
        places=places,
        additional_prompt=additional_prompt,
        slots=slots,
        default_pool=location_hint,
    )
    logger.info(
        "generate_plan request: tool=%r system=%s user_prompt=%s",
        PLAN_TOOL_SCHEMA["name"],
        truncate_for_log(system),
        truncate_for_log(user_prompt),
    )

    result = call_claude_with_tool(
        prompt=user_prompt,
        system=system,
        tool=PLAN_TOOL_SCHEMA,
        max_tokens=settings.ANTHROPIC_MAX_TOKENS_PLAN,
        track_kwargs={
            "team": program.team,
            "user": user,
            "endpoint": "plan",
        },
    )
    logger.info(
        "generate_plan response: program=%s model=%s tokens(in/out)=%s/%s "
        "stop_reason=%s tool_input_keys=%s",
        program.pk,
        result["model"],
        result["input_tokens"],
        result["output_tokens"],
        result.get("stop_reason"),
        (
            sorted(result["tool_input"].keys())
            if isinstance(result.get("tool_input"), dict)
            else "n/a"
        ),
    )

    tool_input = result["tool_input"]
    events = tool_input.get("events", [])
    rationale = tool_input.get("rationale", "")

    if not isinstance(events, list) or not events:
        logger.warning(
            "AI returned empty/invalid events for program=%s. type=%s len=%s rationale=%r",
            program.pk,
            type(events).__name__,
            len(events) if isinstance(events, list) else "n/a",
            (rationale or "")[:200],
        )
        raise AIServiceError(_("AI returned an empty or invalid event list."))

    for ev in events:
        ev_date = _parse_date_strict(ev.get("date"))
        if ev_date < date_start or ev_date > date_end:
            logger.warning(
                "AI generated out-of-range date for program=%s: %s not in [%s, %s] (event=%r)",
                program.pk,
                ev_date,
                date_start,
                date_end,
                ev,
            )
            raise AIServiceError(_("AI generated an event with an out-of-range date."))

    return {
        "events": events,
        "rationale": rationale,
        "prompt_sent": user_prompt,
        "model": result["model"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
    }

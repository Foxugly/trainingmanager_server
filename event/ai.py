"""AI-driven training session generation for an Event."""

import logging

from django.utils.translation import gettext_lazy as _

from tools.ai import AIServiceError, call_claude_with_tool, truncate_for_log
from tools.ai_prompt import append_coach_instructions
from tools.ai_prompt import build_system_prompt as _build_system_prompt
from tools.html_sanitizer import strip_html
from tools.i18n import resolve_language_label

logger = logging.getLogger(__name__)


def build_training_tool_schema(*, modality_ids, energysegment_ids, equipment_names=None):
    """Tool schema with the catalog ids fixed via enum to prevent hallucination.

    When ``equipment_names`` is provided (the team's managed equipment catalog),
    an ``equipment_used`` array is added whose values are constrained by enum to
    that catalog — the AI may only report equipment the team actually owns.
    """
    schema = {
        "name": "create_training_session",
        "description": (
            "Generate the detail of a training session: a list of rounds, "
            "each containing exercises drawn from the provided catalog."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rounds": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "count": {
                                "type": "integer",
                                "minimum": 1,
                                "description": "How many times the round is repeated as a whole",
                            },
                            "t_start": {
                                "type": "string",
                                "pattern": "^[0-9]{2}:[0-9]{2}$",
                                "description": "MM:SS, '00:00' if not applicable",
                            },
                            "t_break": {
                                "type": "string",
                                "pattern": "^[0-9]{2}:[0-9]{2}$",
                                "description": "Pause after the round (MM:SS)",
                            },
                            "exercises": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "modality_id": {
                                            "type": "integer",
                                            "enum": modality_ids,
                                        },
                                        "energysegment_id": {
                                            "type": "integer",
                                            "enum": energysegment_ids,
                                        },
                                        "distance": {
                                            "type": "integer",
                                            "minimum": 0,
                                        },
                                        "repetition": {
                                            "type": "integer",
                                            "minimum": 1,
                                        },
                                        "t_start": {
                                            "type": "string",
                                            "pattern": "^[0-9]{2}:[0-9]{2}$",
                                        },
                                        "t_break": {
                                            "type": "string",
                                            "pattern": "^[0-9]{2}:[0-9]{2}$",
                                        },
                                        "notes": {
                                            "type": "string",
                                            "maxLength": 200,
                                        },
                                    },
                                    "required": [
                                        "modality_id",
                                        "energysegment_id",
                                        "distance",
                                        "repetition",
                                    ],
                                },
                            },
                        },
                        "required": ["count", "exercises"],
                    },
                },
                "rationale": {
                    "type": "string",
                    "description": "Overall explanation (3-5 sentences)",
                },
            },
            "required": ["rounds", "rationale"],
        },
    }
    if equipment_names:
        schema["input_schema"]["properties"]["equipment_used"] = {
            "type": "array",
            "items": {"type": "string", "enum": list(equipment_names)},
            "description": (
                "The team's equipment this session requires. Use ONLY items from "
                "the provided catalog; omit or leave empty if none is needed."
            ),
        }
    return schema


def build_system_prompt(sport_name):
    return _build_system_prompt(
        sport_name,
        body=(
            "You generate detailed and progressive training sessions using "
            "ONLY the modalities and energysegments from the provided catalog."
        ),
        tool_name="create_training_session",
    )


def _duration_minutes(start, end):
    """Whole minutes between two ``datetime.time`` values, or None.

    Returns None when either bound is missing or the range is non-positive
    (e.g. equal times or an overnight slot we don't try to interpret).
    """
    if start is None or end is None:
        return None
    delta = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    return delta if delta > 0 else None


def _venue_text(event):
    """Human venue string: managed Place (name + address) wins over free text."""
    if event.place_id:
        name = event.place.name
        address = (event.place.address or "").strip()
        return f"{name} ({address})" if address else name
    return (event.location or "").strip()


def build_user_prompt(
    *,
    event,
    modalities_catalog,
    energysegments_catalog,
    team=None,
    equipment_names=None,
    additional_prompt="",
):
    """Return ``(cached_prefix, variable_prompt)``.

    ``cached_prefix`` is the stable, reusable context (catalogs + generic rules +
    team skill level + language) — identical across every session of the same
    team, so it caches and is reused on repeat generations. ``variable_prompt``
    holds this session's specifics (name, goal, date, venue, target…) plus the
    coach's free-text instructions, and changes every call.
    """
    language = (
        event.refer_program.team.language
        if event.refer_program and event.refer_program.team
        else "fr"
    )
    language_label = resolve_language_label(language)

    cat_modalities = "\n".join(f"  {m['id']}: {m['name']}" for m in modalities_catalog)

    def _segment_line(s):
        desc = (s.get("description") or "").strip()
        return f"  {s['id']}: {s['abv']} — {desc}" if desc else f"  {s['id']}: {s['abv']}"

    cat_segments = "\n".join(_segment_line(s) for s in energysegments_catalog)

    # Localized name/description: modeltranslation returns the active-language
    # value (the prompt resolves the team language above).
    level_line = ""
    if team and team.level:
        level_line = f"- Team skill level: {team.level.name} — {team.level.description}\n"

    program_line = ""
    if event.refer_program and (event.refer_program.description or "").strip():
        program_line = f"- Program objective: {event.refer_program.description}\n"

    duration = _duration_minutes(event.hour_start, event.hour_end)
    duration_line = (
        f"- Session duration: about {duration} minutes\n" if duration else ""
    )

    equipment_text = strip_html(event.equipment or "").strip()
    equipment_line = (
        f"- Available equipment: {equipment_text}\n" if equipment_text else ""
    )

    venue = _venue_text(event)
    venue_line = f"- Venue: {venue}\n" if venue else ""

    equipment_catalog = list(equipment_names or [])
    equipment_catalog_block = ""
    if equipment_catalog:
        equipment_catalog_block = (
            "Team equipment catalog (use ONLY these in 'equipment_used'):\n"
            + "".join(f"  - {name}\n" for name in equipment_catalog)
            + "\n"
        )

    # --- Cacheable prefix: stable per (sport, team, language) ----------------
    cached_prefix = (
        "You generate ONE training session via the 'create_training_session' "
        "tool, using ONLY the catalogs and rules below (stable references).\n\n"
        f"Authorized modalities catalog (id: name):\n{cat_modalities}\n\n"
        f"Authorized energysegments catalog (id: abv — description):\n{cat_segments}\n\n"
        f"{equipment_catalog_block}"
        f"{level_line}"
        "General instructions:\n"
        "- The 'Session name' and 'Goal' below are provided by the coach in "
        f"{language_label}. They may contain indications about intensity, target "
        "athlete population, or equipment to use. Take this into account when "
        "designing the session.\n"
        "- Build a structured session with:\n"
        "  * a warm-up round\n"
        "  * one or more main rounds\n"
        "  * a cool-down round\n"
        "- Use ONLY the ids provided in the catalogs above.\n"
        f"- Respond ENTIRELY in {language_label}: all notes and the rationale "
        f"must be in {language_label}.\n"
        "- Use the 'create_training_session' tool only.\n"
        + (
            "- In 'equipment_used', list the team equipment this session requires, "
            "using ONLY items from the catalog above (exact names). Omit it if no "
            "equipment is needed.\n"
            if equipment_catalog
            else ""
        )
    )

    # --- Variable part: this session's specifics + coach instructions --------
    base = (
        f"Generate the detail of a training session with these constraints:\n"
        f"- Session name: {event.name}\n"
        f"- Goal: {strip_html(event.goal) or '(not specified)'}\n"
        f"{program_line}"
        f"- Planned date: {event.date.isoformat() if event.date else '(not specified)'}\n"
        f"{duration_line}"
        f"{venue_line}"
        f"{equipment_line}"
        f"- Target total distance: {event.total or 0} meters\n\n"
        f"Rules for THIS session:\n"
        f"- The sum of (exercise.distance * exercise.repetition * round.count) "
        f"across the whole session must approach {event.total or 0} meters.\n"
        + (
            "- Size the whole session (volume and rest) to realistically fit the "
            "stated duration.\n"
            if duration
            else ""
        )
        + (
            "- Use the available equipment listed above where it fits the goal; "
            "do not require equipment that is not listed.\n"
            if equipment_text
            else ""
        )
        + (
            "- The venue may indicate the pool/track length (e.g. 25 m or 50 m); "
            "keep exercise distances coherent with it (use multiples of the "
            "length).\n"
            if venue
            else ""
        )
    )

    variable_prompt = append_coach_instructions(
        base,
        additional_prompt,
        note=(
            "These take precedence over generic defaults but must remain "
            "consistent with the team sport, language, and existing event "
            "metadata."
        ),
    )
    return cached_prefix, variable_prompt


def build_freeform_tool_schema():
    return {
        "name": "create_freeform_training",
        "description": (
            "Generate a free-text training session as a short HTML document "
            "(headings, paragraphs, lists). No structured rounds."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "html": {
                    "type": "string",
                    "description": (
                        "The session as sanitizable HTML (use only <p>, <ul>/<ol>/<li>, "
                        "<strong>, <em>, <h2>/<h3>, <br>). Max ~4000 chars."
                    ),
                    "maxLength": 8000,
                },
                "rationale": {"type": "string", "description": "1-3 sentence explanation."},
            },
            "required": ["html", "rationale"],
        },
    }


def build_freeform_system_prompt(sport_name):
    """Minimal system prompt for freeform generation.

    Unlike the structured prompt, it makes no mention of the modalities /
    energysegments catalogs or the structured ``create_training_session`` tool —
    it just asks for a concise free-text session as sanitizable HTML and routes
    the answer through the ``create_freeform_training`` tool.
    """
    return (
        f"You are a coach assistant. Write a concise free-text training session "
        f"for {sport_name} as sanitizable HTML (use only <p>, <ul>/<ol>/<li>, "
        f"<strong>, <em>, <h2>/<h3>, <br>). Return it by calling the "
        f"'create_freeform_training' tool."
    )


def generate_freeform_training(*, event, user=None, additional_prompt=""):
    """Generate free-text (HTML) training content via Claude, in the team's
    language. Returns {html, rationale, prompt_sent, model, input_tokens, output_tokens}."""
    team = event.refer_program.team if event.refer_program else None
    sport = event.sport or (team.default_sport if team else None)
    sport_name = sport.name if sport else "the practiced sport"
    language = team.language if team is not None else "fr"

    tool = build_freeform_tool_schema()
    system = build_freeform_system_prompt(sport_name)
    lang_label = resolve_language_label(language)
    user_prompt = (
        f"Write a {sport_name} training session for the event '{event.name}'"
        f"{(' on ' + str(event.date)) if event.date else ''}. "
        f"Write ALL prose in {lang_label}. "
        f"Return it via the create_freeform_training tool as concise HTML. "
        f"{('Extra guidance: ' + additional_prompt) if additional_prompt else ''}"
    )

    result = call_claude_with_tool(
        prompt=user_prompt,
        system=system,
        cached_prefix="",
        tool=tool,
        track_kwargs={"team": team, "user": user, "endpoint": "training_freeform"},
    )
    tool_input = result["tool_input"]
    html = tool_input.get("html", "")
    if not html:
        raise AIServiceError(_("AI returned empty free-text content."))
    return {
        "html": html,
        "rationale": tool_input.get("rationale", ""),
        "prompt_sent": user_prompt,
        "model": result["model"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
    }


def generate_training(*, event, user=None, additional_prompt=""):
    from exercise.models import EnergySegment, Modality

    # The session's own sport drives modality scoping; fall back to the team's
    # default sport (multi-sport: an event may be for any of the team's sports).
    team = event.refer_program.team if event.refer_program else None
    sport = event.sport or (team.default_sport if team else None)
    sport_name = sport.name if sport else "the practiced sport"

    modalities_qs = Modality.objects.filter(sport=sport) if sport else Modality.objects.all()
    modalities = list(modalities_qs.values("id", "name"))
    # Iterate the queryset (not .values) so modeltranslation resolves the
    # description in the active language; the view sets the team's language
    # before calling generate_training.
    energysegments = [
        {"id": seg.id, "abv": seg.abv, "description": seg.description or ""}
        for seg in EnergySegment.objects.all()
    ]

    if not modalities:
        raise AIServiceError(_("No modalities defined for this sport. Cannot generate."))
    if not energysegments:
        raise AIServiceError(_("No energy segments defined. Cannot generate."))

    modality_ids = [m["id"] for m in modalities]
    energysegment_ids = [s["id"] for s in energysegments]

    program = event.refer_program
    team = program.team if program and program.team else None

    # The team's enabled equipment constrains what the AI may report in
    # 'equipment_used' (enum) — it can only pick from the team's catalog subset;
    # nothing is auto-created. Names are resolved via the .name accessor so they
    # are localized to the active (request) language, matching the response.
    equipment_names = (
        [e.name for e in team.equipment.filter(is_active=True).order_by("name")]
        if team is not None
        else []
    )
    logger.info(
        "generate_training inputs: event=%s program=%s team=%s sport=%r "
        "modalities=%s energysegments=%s event_total=%s event_date=%s "
        "additional_prompt_len=%s",
        event.pk,
        program.pk if program else None,
        team.pk if team else None,
        sport_name,
        modality_ids,
        energysegment_ids,
        event.total,
        event.date,
        len(additional_prompt or ""),
    )

    tool = build_training_tool_schema(
        modality_ids=modality_ids,
        energysegment_ids=energysegment_ids,
        equipment_names=equipment_names,
    )
    system = build_system_prompt(sport_name)
    cached_prefix, user_prompt = build_user_prompt(
        event=event,
        modalities_catalog=modalities,
        energysegments_catalog=energysegments,
        team=team,
        equipment_names=equipment_names,
        additional_prompt=additional_prompt,
    )
    logger.info(
        "generate_training request: tool=%r system=%s cached_prefix=%s user_prompt=%s",
        tool["name"],
        truncate_for_log(system),
        truncate_for_log(cached_prefix),
        truncate_for_log(user_prompt),
    )

    result = call_claude_with_tool(
        prompt=user_prompt,
        system=system,
        cached_prefix=cached_prefix,
        tool=tool,
        track_kwargs={
            "team": team,
            "user": user,
            "endpoint": "training",
        },
    )
    logger.info(
        "generate_training response: event=%s model=%s tokens(in/out)=%s/%s "
        "stop_reason=%s tool_input_keys=%s",
        event.pk,
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
    rounds_data = tool_input.get("rounds", [])
    rationale = tool_input.get("rationale", "")

    if not isinstance(rounds_data, list) or not rounds_data:
        logger.warning(
            "AI returned empty/invalid rounds for event=%s. type=%s len=%s rationale=%r",
            event.pk,
            type(rounds_data).__name__,
            len(rounds_data) if isinstance(rounds_data, list) else "n/a",
            (rationale or "")[:200],
        )
        raise AIServiceError(_("AI returned empty or invalid rounds."))

    valid_modality_ids = set(modality_ids)
    valid_segment_ids = set(energysegment_ids)
    for r in rounds_data:
        for ex in r.get("exercises", []):
            if ex.get("modality_id") not in valid_modality_ids:
                logger.warning(
                    "AI used invalid modality_id for event=%s: %r not in %s (exercise=%r)",
                    event.pk,
                    ex.get("modality_id"),
                    sorted(valid_modality_ids),
                    ex,
                )
                raise AIServiceError(_("AI used an invalid modality id."))
            if ex.get("energysegment_id") not in valid_segment_ids:
                logger.warning(
                    "AI used invalid energysegment_id for event=%s: %r not in %s (exercise=%r)",
                    event.pk,
                    ex.get("energysegment_id"),
                    sorted(valid_segment_ids),
                    ex,
                )
                raise AIServiceError(_("AI used an invalid energysegment id."))

    # Keep only equipment that is actually in the team catalog (the enum already
    # constrains the model, but we never trust the response blindly).
    valid_equipment = set(equipment_names)
    equipment_used = [
        name
        for name in (tool_input.get("equipment_used") or [])
        if name in valid_equipment
    ]

    return {
        "rounds": rounds_data,
        "rationale": rationale,
        "equipment_used": equipment_used,
        # Persist the full prompt actually sent (stable prefix + variable part)
        # for the audit trail on Event.ai_prompt.
        "prompt_sent": f"{cached_prefix}\n{user_prompt}",
        "model": result["model"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
    }

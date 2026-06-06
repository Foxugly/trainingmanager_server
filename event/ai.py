"""AI-driven training session generation for an Event."""

import logging

from django.utils.translation import gettext_lazy as _

from tools.ai import AIServiceError, call_claude_with_tool, truncate_for_log
from tools.html_sanitizer import strip_html
from tools.i18n import resolve_language_label

logger = logging.getLogger(__name__)


def build_training_tool_schema(*, modality_ids, energysegment_ids):
    """Tool schema with the catalog ids fixed via enum to prevent hallucination."""
    return {
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


def build_system_prompt(sport_name):
    return (
        f"You are an expert coach in {sport_name} training. "
        f"You generate detailed and progressive training sessions using "
        f"ONLY the modalities and energysegments from the provided catalog. "
        f"You MUST always respond using the 'create_training_session' tool. "
        f"Never write free-form text."
    )


def build_user_prompt(
    *, event, modalities_catalog, energysegments_catalog, team=None, additional_prompt=""
):
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

    base = (
        f"Generate the detail of a training session with these constraints:\n"
        f"- Session name: {event.name}\n"
        f"- Goal: {strip_html(event.goal) or '(not specified)'}\n"
        f"{level_line}"
        f"{program_line}"
        f"- Planned date: {event.date.isoformat() if event.date else '(not specified)'}\n"
        f"- Target total distance: {event.total or 0} meters\n\n"
        f"Authorized modalities catalog (id: name):\n{cat_modalities}\n\n"
        f"Authorized energysegments catalog (id: abv — description):\n{cat_segments}\n\n"
        f"IMPORTANT instructions:\n"
        f"- The 'Session name' and 'Goal' above are provided by the coach in "
        f"{language_label}. They may contain indications about intensity, "
        f"target athlete population, or equipment to use. Take this into "
        f"account when designing the session.\n"
        f"- Build a structured session with:\n"
        f"  * a warm-up round\n"
        f"  * one or more main rounds\n"
        f"  * a cool-down round\n"
        f"- The sum of (exercise.distance * exercise.repetition * round.count) "
        f"across the whole session must approach {event.total or 0} meters.\n"
        f"- Use ONLY the ids provided in the catalogs.\n"
        f"- Respond ENTIRELY in {language_label}: all notes and the rationale "
        f"must be in {language_label}.\n"
        f"- Use the 'create_training_session' tool only.\n"
    )

    extra = (additional_prompt or "").strip()
    if extra:
        # Appended AFTER the structured context so the system/base
        # instructions are not overridable via prompt-injection by the coach;
        # the marker line makes the boundary explicit for the model.
        base += (
            "\n---\n"
            "Additional instructions provided by the coach (these take "
            "precedence over generic defaults but must remain consistent "
            "with the team sport, language, and existing event metadata):\n"
            f"{extra}\n"
            "---\n"
        )

    return base


def generate_training(*, event, user=None, additional_prompt=""):
    from exercise.models import EnergySegment, Modality

    sport = (
        event.refer_program.team.sport if event.refer_program and event.refer_program.team else None
    )
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
    )
    system = build_system_prompt(sport_name)
    user_prompt = build_user_prompt(
        event=event,
        modalities_catalog=modalities,
        energysegments_catalog=energysegments,
        team=team,
        additional_prompt=additional_prompt,
    )
    logger.info(
        "generate_training request: tool=%r system=%s user_prompt=%s",
        tool["name"],
        truncate_for_log(system),
        truncate_for_log(user_prompt),
    )

    result = call_claude_with_tool(
        prompt=user_prompt,
        system=system,
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

    return {
        "rounds": rounds_data,
        "rationale": rationale,
        "prompt_sent": user_prompt,
        "model": result["model"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
    }

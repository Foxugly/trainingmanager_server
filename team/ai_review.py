"""AI review / critique of a team's training block over a period.

Unlike the *generators* (``event/ai.py``, ``program/ai.py``) which produce a
session/plan, this is an *analyst*: it reads the team's already-recorded
training data over a window (attendance, volume per week, intensity per energy
zone) and returns a structured critique — an overall load assessment, concrete
findings, and recommended adjustments.

The numeric data is assembled by the caller (the same aggregation the
``teams/{id}/stats/`` endpoint uses) and passed in as ``stats``; this module
only turns it into a prompt and forces the ``review_training_block`` tool so the
answer is always structured JSON.
"""

import logging

from tools.ai import call_claude_with_tool
from tools.ai_prompt import build_system_prompt as _build_system_prompt
from tools.i18n import resolve_language_label

logger = logging.getLogger(__name__)

REVIEW_AREAS = [
    "volume",
    "intensity",
    "attendance",
    "recovery",
    "consistency",
    "progression",
]
LOAD_ASSESSMENTS = ["too_light", "balanced", "too_heavy", "uncertain"]
SEVERITIES = ["info", "warning", "critical"]
CONFIDENCES = ["low", "medium", "high"]


def build_review_tool_schema():
    return {
        "name": "review_training_block",
        "description": (
            "Return a structured critique of a team's training block over the "
            "given period, based ONLY on the supplied data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Overall assessment in 2-4 sentences.",
                    "maxLength": 800,
                },
                "load_assessment": {
                    "type": "string",
                    "enum": LOAD_ASSESSMENTS,
                    "description": "Overall training load over the period.",
                },
                "findings": {
                    "type": "array",
                    "description": "Specific observations grounded in the data.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "area": {"type": "string", "enum": REVIEW_AREAS},
                            "severity": {"type": "string", "enum": SEVERITIES},
                            "observation": {"type": "string", "maxLength": 400},
                        },
                        "required": ["area", "severity", "observation"],
                    },
                },
                "adjustments": {
                    "type": "array",
                    "description": "Actionable recommendations for the next block.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "recommendation": {"type": "string", "maxLength": 300},
                            "rationale": {"type": "string", "maxLength": 300},
                        },
                        "required": ["recommendation"],
                    },
                },
                "confidence": {
                    "type": "string",
                    "enum": CONFIDENCES,
                    "description": "Confidence given the amount of data available.",
                },
            },
            "required": [
                "summary",
                "load_assessment",
                "findings",
                "adjustments",
                "confidence",
            ],
        },
    }


def _format_data_block(stats):
    """Compact, human-readable rendering of the aggregated stats for the prompt."""
    period = stats.get("period", {})
    attendance = stats.get("attendance", {})
    volume = stats.get("volume", {})
    intensity = stats.get("intensity", {})

    by_session = attendance.get("by_session") or []
    team_rate = attendance.get("team_rate")
    rate_pct = f"{team_rate * 100:.0f}%" if team_rate is not None else "n/a"

    lines = [
        f"Period: {period.get('from')} → {period.get('to')}",
        f"Sessions in period: {len(by_session)}",
        f"Overall attendance rate: {rate_pct}",
        f"Total training volume: {volume.get('total_distance', 0)} meters",
    ]

    by_week = volume.get("by_week") or []
    if by_week:
        lines.append("Volume per week (meters):")
        lines += [f"  {w['week_start']}: {w['distance']}" for w in by_week]

    by_segment = intensity.get("by_segment") or []
    if by_segment:
        lines.append("Distance per energy zone (meters):")
        for seg in by_segment:
            label = seg.get("label") or ""
            suffix = f" — {label}" if label else ""
            lines.append(f"  {seg['abv']}{suffix}: {seg['distance']}")

    if by_session:
        lines.append("Per-session attendance (date: present/expected):")
        for s in by_session:
            present = s.get("present", 0)
            total = s.get("total", 0)
            lines.append(f"  {s.get('date')} {s.get('name')}: {present}/{total}")

    return "\n".join(lines)


def review_training_block(*, team, date_from, date_to, stats, user=None):
    """Ask Claude to critique the team's training block.

    ``stats`` is the team-aggregate payload (attendance / volume / intensity).
    Returns ``{summary, load_assessment, findings, adjustments, confidence,
    model, input_tokens, output_tokens}``.
    """
    sport = team.default_sport
    sport_name = sport.name if sport else "the practiced sport"
    language_label = resolve_language_label(team.language or "fr")

    tool = build_review_tool_schema()
    system = _build_system_prompt(
        sport_name,
        body=(
            "You analyse a team's recent training data and return a concise, "
            "actionable critique. Ground every finding in the supplied numbers; "
            "do not invent data. If the data is sparse, say so and lower your "
            "confidence."
        ),
        tool_name="review_training_block",
    )

    data_block = _format_data_block(stats)
    user_prompt = (
        f"Review the training block for team '{team.name}'.\n\n"
        f"Respond ENTIRELY in {language_label}.\n\n"
        f"Training data for the period:\n{data_block}\n\n"
        "Assess the overall load, surface the most important findings (volume, "
        "intensity balance across energy zones, attendance, recovery, "
        "consistency, progression), and propose concrete adjustments for the "
        "next block. Call the 'review_training_block' tool only."
    )

    result = call_claude_with_tool(
        prompt=user_prompt,
        system=system,
        cached_prefix="",
        tool=tool,
        track_kwargs={"team": team, "user": user, "endpoint": "review"},
    )

    tool_input = result["tool_input"]

    # Never trust the response blindly: drop findings/adjustments whose enums
    # fall outside the allowed sets (the tool enum already constrains them).
    findings = [
        {
            "area": f.get("area"),
            "severity": f.get("severity"),
            "observation": (f.get("observation") or "").strip(),
        }
        for f in (tool_input.get("findings") or [])
        if f.get("area") in REVIEW_AREAS and f.get("severity") in SEVERITIES
    ]
    adjustments = [
        {
            "recommendation": (a.get("recommendation") or "").strip(),
            "rationale": (a.get("rationale") or "").strip(),
        }
        for a in (tool_input.get("adjustments") or [])
        if (a.get("recommendation") or "").strip()
    ]
    load_assessment = tool_input.get("load_assessment")
    if load_assessment not in LOAD_ASSESSMENTS:
        load_assessment = "uncertain"
    confidence = tool_input.get("confidence")
    if confidence not in CONFIDENCES:
        confidence = "low"

    return {
        "summary": (tool_input.get("summary") or "").strip(),
        "load_assessment": load_assessment,
        "findings": findings,
        "adjustments": adjustments,
        "confidence": confidence,
        "model": result["model"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
    }

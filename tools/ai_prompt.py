"""Shared prompt-building helpers for the AI generators.

Both the season-plan generator (``program/ai.py``) and the single-session
generator (``event/ai.py``) build a forced-tool prompt with the same shape: an
"expert coach" system prompt and the coach's free-text instructions appended as
a delimited, injection-hardened block after the structured context. These
helpers keep the two from drifting on that boilerplate.
"""


def build_system_prompt(sport_name, *, body, tool_name):
    """Standard 'expert coach' system prompt forcing a single tool.

    ``body`` is the task-specific sentence(s); ``tool_name`` is the tool the
    model MUST call.
    """
    return (
        f"You are an expert coach in {sport_name}. "
        f"{body} "
        f"You MUST always respond using the '{tool_name}' tool. "
        f"Never write free-form text."
    )


def append_coach_instructions(base, additional_prompt, *, note=""):
    """Append the coach's free-text instructions as a delimited block AFTER the
    structured context.

    Placing it last is deliberate prompt-injection hardening: the binding rules
    in ``base`` stay in force even if the coach text says "ignore the above".
    ``note`` is an optional precedence clause inserted before the coach text.
    Returns ``base`` unchanged when there is no instruction text.
    """
    extra = (additional_prompt or "").strip()
    if not extra:
        return base
    note_block = f"{note.strip()}\n" if note and note.strip() else ""
    return (
        f"{base}"
        "\n---\n"
        "Additional instructions provided by the coach:\n"
        f"{note_block}"
        f"{extra}\n"
        "---\n"
    )

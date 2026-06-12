"""GDPR data-portability export assembly for the authenticated user.

Extracted from ``DataExportView`` (it was ~11 ``_export_*`` builder methods +
``_build_export`` on the view) so the view stays a thin HTTP layer and the
collectors are unit-testable directly.

The export is assembled defensively: each related area is wrapped so a missing
app/relation or a user without a linked Member yields an empty list rather than
a 500. Imports are local-by-relation (no extra coupling). The password hash and
the live ``calendar_token`` secret are NEVER included.
"""

import logging

logger = logging.getLogger(__name__)


def _iso(value):
    return value.isoformat() if value is not None else None


def build_export(user):
    """Return the full personal-data export dict for ``user``."""
    member = getattr(user, "member_profile", None)

    export = {
        "profile": {
            "id": user.id,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "language": user.language,
            "date_joined": _iso(user.date_joined),
            "last_login": _iso(user.last_login),
            "weekly_recap_opt_in": user.weekly_recap_opt_in,
        },
        "member_profile": None,
        "team_memberships": [],
        "owned_teams": [],
        "managed_teams": [],
        "performances": [],
        "rsvps": [],
        "roti_scores": [],
        "notes_about_me": [],
        "messages_authored": [],
        "uploaded_attachments": [],
    }

    if member is not None:
        export["member_profile"] = {
            "firstname": member.firstname,
            "lastname": member.lastname,
            "email": member.email,
            "phonenumber": member.phonenumber,
        }

    export["team_memberships"] = _team_memberships(member)
    export["owned_teams"] = _owned_teams(user)
    export["managed_teams"] = _managed_teams(user)
    export["performances"] = _performances(member)
    export["rsvps"] = _rsvps(member)
    export["roti_scores"] = _roti_scores(member)
    export["notes_about_me"] = _notes_about_me(member)
    export["messages_authored"] = _messages_authored(user)
    export["uploaded_attachments"] = _uploaded_attachments(user)

    return export


def _team_memberships(member):
    if member is None:
        return []
    try:
        return [
            {
                "team": m.team.name,
                "joined_at": _iso(m.joined_at),
                "left_at": _iso(m.left_at),
            }
            for m in member.memberships.select_related("team").all()
        ]
    except Exception:  # pragma: no cover - defensive guard
        logger.exception("export: team_memberships failed")
        return []


def _owned_teams(user):
    try:
        return [{"id": t.id, "name": t.name} for t in user.owned_teams.all()]
    except Exception:  # pragma: no cover - defensive guard
        logger.exception("export: owned_teams failed")
        return []


def _managed_teams(user):
    try:
        return [{"id": t.id, "name": t.name} for t in user.managed_teams.all()]
    except Exception:  # pragma: no cover - defensive guard
        logger.exception("export: managed_teams failed")
        return []


def _performances(member):
    if member is None:
        return []
    try:
        return [
            {
                "label": p.label,
                "value": str(p.value),
                "unit": p.unit,
                "recorded_on": _iso(p.recorded_on),
                "notes": p.notes,
            }
            for p in member.performances.all()
        ]
    except Exception:  # pragma: no cover - defensive guard
        logger.exception("export: performances failed")
        return []


def _rsvps(member):
    if member is None:
        return []
    try:
        return [
            {
                "event_id": r.event_id,
                "event_name": r.event.name,
                "status": r.status,
            }
            for r in member.rsvps.select_related("event").all()
        ]
    except Exception:  # pragma: no cover - defensive guard
        logger.exception("export: rsvps failed")
        return []


def _roti_scores(member):
    if member is None:
        return []
    try:
        return [{"event_id": r.event_id, "score": r.score} for r in member.rotis.all()]
    except Exception:  # pragma: no cover - defensive guard
        logger.exception("export: roti_scores failed")
        return []


def _notes_about_me(member):
    if member is None:
        return []
    try:
        # Mirror the note app's athlete visibility rule: an athlete may
        # only read a note that is shared with them AND still active.
        return [
            {
                "team": n.team.name,
                "content": n.content,
                "created_at": _iso(n.created_at),
            }
            for n in member.notes.select_related("team").filter(
                visible_to_athlete=True, is_active=True
            )
        ]
    except Exception:  # pragma: no cover - defensive guard
        logger.exception("export: notes_about_me failed")
        return []


def _messages_authored(user):
    try:
        return [
            {
                "topic_title": m.topic.title,
                "content": m.content,
                "created_at": _iso(m.created_at),
            }
            for m in user.topic_messages_authored.select_related("topic").all()
        ]
    except Exception:  # pragma: no cover - defensive guard
        logger.exception("export: messages_authored failed")
        return []


def _uploaded_attachments(user):
    try:
        return [
            {
                "filename": a.filename,
                "content_type_mime": a.content_type_mime,
                "size_bytes": a.size_bytes,
                "created_at": _iso(a.created_at),
            }
            for a in user.uploaded_attachments.all()
        ]
    except Exception:  # pragma: no cover - defensive guard
        logger.exception("export: uploaded_attachments failed")
        return []

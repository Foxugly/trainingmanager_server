"""Audit logging service.

The single entry point is ``record(...)``. It is deliberately cheap and
synchronous, and it NEVER raises out to the caller: auditing must never break
the action being audited. Any failure is swallowed and logged as a warning.

Callers pass the already-known ``actor`` / ``team`` / ``target_repr`` — this
service does not re-resolve permissions or look anything up beyond reading the
actor's username for the snapshot label.
"""

import logging

logger = logging.getLogger("audit")


def record(action, *, actor=None, team=None, target_repr="", metadata=None, request=None):
    """Create and return an AuditLogEntry, or None on any failure.

    Args:
        action: an ``AuditAction`` value (or its string code).
        actor: the acting user, or None for system/anonymous.
        team: the Team the action is scoped to, or None.
        target_repr: short human-readable description of the target.
        metadata: optional dict of small structured extra context.
        request: optional DRF/Django request (reserved for future enrichment).

    Never raises: on error it logs a warning and returns None.
    """
    try:
        from django.db import transaction

        from .models import AuditLogEntry

        actor_label = ""
        if actor is not None:
            # Email-only: the human-readable actor snapshot is the email (the
            # former username column is gone).
            actor_label = (getattr(actor, "email", "") or "")[:150]

        # Wrap the write in its own savepoint so a failed INSERT rolls back
        # cleanly and never poisons the caller's surrounding transaction (the
        # audited action's own transaction must not break because auditing did).
        with transaction.atomic():
            return AuditLogEntry.objects.create(
                actor=actor if (actor is not None and getattr(actor, "pk", None)) else None,
                actor_label=actor_label,
                action=str(action),
                team=team,
                target_repr=target_repr or "",
                metadata=metadata or {},
            )
    except Exception:  # noqa: BLE001 - auditing must never break the audited action
        logger.warning("audit.record failed (action=%r)", action, exc_info=True)
        return None


def audit_event(request, action, **kwargs):
    """View-side convenience over :func:`record`: pulls ``actor`` from
    ``request.user`` and forwards the request, so callers only pass the
    action + ``team`` / ``target_repr`` / ``metadata``. Same never-raises
    contract as ``record``."""
    return record(action, actor=getattr(request, "user", None), request=request, **kwargs)

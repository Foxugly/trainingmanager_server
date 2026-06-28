"""Notification service layer.

Single entry point ``notify()`` used by feature code (note triggers today,
messaging tomorrow) to fan a single event out to the recipient's enabled
channels (in-app row + localized email), honouring per-type preferences.

Design notes:
- Preferences default to in_app=True / email=True when no
  ``NotificationPreference`` row exists for (user, type). No row is ever
  required.
- The actor (the user who triggered the event) is never notified of their
  own action.
- Email failures are logged and swallowed: a flaky mail send must never
  break the request that triggered the notification.
"""

import logging

from django.conf import settings
from django.core.mail import get_connection, send_mail
from django.utils import translation

from .models import Notification, NotificationPreference, NotificationType

logger = logging.getLogger(__name__)

# Channel defaults applied when no preference row exists for (user, type).
DEFAULT_IN_APP = True
DEFAULT_EMAIL = True
DEFAULT_PUSH = True


def get_effective_preferences(user):
    """Return the user's effective channel prefs for every NotificationType.

    A list of dicts ``{type, label, in_app, email}`` covering ALL members of
    ``NotificationType`` (defaulting to in_app/email = True where the user has
    no stored row). The order matches ``NotificationType.choices``.
    """
    stored = {p.type: p for p in NotificationPreference.objects.filter(user=user)}
    effective = []
    for value, label in NotificationType.choices:
        pref = stored.get(value)
        effective.append(
            {
                "type": value,
                "label": str(label),
                "in_app": pref.in_app if pref is not None else DEFAULT_IN_APP,
                "email": pref.email if pref is not None else DEFAULT_EMAIL,
                "push": pref.push if pref is not None else DEFAULT_PUSH,
            }
        )
    return effective


def _resolve_channels(recipient, type):
    """Resolve (in_app, email) booleans for one recipient + type, applying
    defaults when no preference row exists."""
    pref = NotificationPreference.objects.filter(user=recipient, type=type).first()
    if pref is None:
        return DEFAULT_IN_APP, DEFAULT_EMAIL
    return pref.in_app, pref.email


def notify(recipient, type, title, body="", url="", *, actor=None, email_extra=""):
    """Deliver a notification to ``recipient`` over their enabled channels.

    Args:
        recipient: the target user (AUTH_USER_MODEL instance).
        type: a ``NotificationType`` value.
        title: short subject line (also the email subject). May be a lazy
            translation string; it is coerced to str inside the recipient's
            language override.
        body: longer text body (optional). May be lazy.
        url: frontend deep-link PATH (e.g. ``/teams/3``). The email appends
            the absolute link ``FRONTEND_BASE_URL + url``.
        actor: the user who triggered the event, if any. The recipient is
            NEVER notified of their own action (skip when recipient == actor).
        email_extra: extra text appended to the EMAIL body only (after the deep
            link); never stored on the in-app row. Used for per-recipient
            content like one-click RSVP links.

    Returns the created ``Notification`` instance when an in-app row was
    created, else ``None``.
    """
    # Never notify the actor about their own action.
    if actor is not None and recipient is not None and recipient.pk == actor.pk:
        return None
    if recipient is None:
        return None

    in_app, email = _resolve_channels(recipient, type)

    created = None
    if in_app:
        # Resolve the (possibly lazy) strings now, in the request language —
        # the stored row is read back later in the recipient's UI which sets
        # its own language; storing a concrete string keeps it stable.
        with translation.override(getattr(recipient, "language", None) or "en"):
            created = Notification.objects.create(
                recipient=recipient,
                type=type,
                title=str(title),
                body=str(body),
                url=url,
            )

    # digest_email users get no immediate email — the daily send_digests cron
    # batches the day's notifications instead (the in-app row above still fires).
    if (
        email
        and getattr(recipient, "email", None)
        and not getattr(recipient, "digest_email", False)
    ):
        _send_email(recipient, title, body, url, email_extra=email_extra)

    return created


def notify_many(recipients, type, title, body="", url="", *, actor=None, email_extra_for=None):
    """Bulk variant of :func:`notify` for a set/iterable of recipients.

    Behaviour-identical to calling ``notify()`` once per recipient, but it
    resolves every recipient's channel preference with a SINGLE
    ``NotificationPreference`` query (instead of one per recipient) and creates
    the in-app rows with ``bulk_create``. Emails are still sent one-by-one
    (each in the recipient's language), with the same swallow-on-error guard.

    The actor is never notified of their own action; ``None`` recipients are
    skipped. Returns the list of created ``Notification`` instances (in-app
    rows only), matching the subset of recipients whose in-app channel is on.

    ``email_extra_for``: optional ``callable(recipient) -> str``. When given,
    the returned text is appended to that recipient's EMAIL body only (never to
    the in-app row). Used for per-recipient content like one-click RSVP links.
    """
    # De-dupe while preserving order; drop None and the actor.
    actor_pk = getattr(actor, "pk", None)
    seen = set()
    targets = []
    for recipient in recipients:
        if recipient is None:
            continue
        pk = recipient.pk
        if pk in seen:
            continue
        if actor_pk is not None and pk == actor_pk:
            continue
        seen.add(pk)
        targets.append(recipient)

    if not targets:
        return []

    # One query for the whole batch: {user_id: (in_app, email)}. Absent rows
    # fall back to the defaults, exactly like _resolve_channels.
    prefs = {
        p.user_id: (p.in_app, p.email)
        for p in NotificationPreference.objects.filter(
            user__in=targets, type=type
        )
    }

    to_create = []
    in_app_recipients = []
    email_recipients = []
    for recipient in targets:
        in_app, email = prefs.get(recipient.pk, (DEFAULT_IN_APP, DEFAULT_EMAIL))
        if in_app:
            # Resolve the (possibly lazy) strings in the recipient's language,
            # mirroring notify(): the stored row keeps a concrete string.
            with translation.override(getattr(recipient, "language", None) or "en"):
                to_create.append(
                    Notification(
                        recipient=recipient,
                        type=type,
                        title=str(title),
                        body=str(body),
                        url=url,
                    )
                )
            in_app_recipients.append(recipient)
        if (
            email
            and getattr(recipient, "email", None)
            and not getattr(recipient, "digest_email", False)
        ):
            email_recipients.append(recipient)

    created = []
    if to_create:
        created = Notification.objects.bulk_create(to_create)

    if email_recipients:
        # Reuse ONE SMTP connection for the whole batch instead of opening a
        # fresh TCP/TLS handshake per recipient (send_mail's default). On a large
        # team this turns N sequential connections into one. Best-effort: a
        # connection failure is logged, not raised.
        connection = None
        try:
            connection = get_connection(fail_silently=False)
            connection.open()
        except Exception:
            logger.exception("Failed to open the notification email connection")
            connection = None
        try:
            for recipient in email_recipients:
                extra = email_extra_for(recipient) if email_extra_for is not None else ""
                _send_email(recipient, title, body, url, email_extra=extra, connection=connection)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    logger.exception("Failed to close the notification email connection")

    return created


def _send_email(recipient, title, body, url, email_extra="", connection=None):
    """Send the localized notification email. Logs and swallows any error so
    a mail failure never breaks the triggering request. ``connection`` lets a
    batch caller (notify_many) reuse a single SMTP connection across recipients;
    when None, send_mail opens its own."""
    lang = getattr(recipient, "language", None) or "en"
    deep_link = ""
    if url:
        base = settings.FRONTEND_BASE_URL.rstrip("/")
        deep_link = f"{base}{url if url.startswith('/') else '/' + url}"

    try:
        with translation.override(lang):
            subject = str(title)
            parts = []
            if body:
                parts.append(str(body))
            if deep_link:
                parts.append(deep_link)
            if email_extra:
                parts.append(str(email_extra))
            message = "\n\n".join(parts) if parts else subject
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient.email],
                fail_silently=False,
                connection=connection,
            )
    except Exception:
        logger.exception("Failed to send notification email to %s", recipient.email)

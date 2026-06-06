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
from django.core.mail import send_mail
from django.utils import translation

from .models import Notification, NotificationPreference, NotificationType

logger = logging.getLogger(__name__)

# Channel defaults applied when no preference row exists for (user, type).
DEFAULT_IN_APP = True
DEFAULT_EMAIL = True


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


def notify(recipient, type, title, body="", url="", *, actor=None):
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

    if email and getattr(recipient, "email", None):
        _send_email(recipient, title, body, url)

    return created


def _send_email(recipient, title, body, url):
    """Send the localized notification email. Logs and swallows any error so
    a mail failure never breaks the triggering request."""
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
            message = "\n\n".join(parts) if parts else subject
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient.email],
                fail_silently=False,
            )
    except Exception:
        logger.exception("Failed to send notification email to %s", recipient.email)

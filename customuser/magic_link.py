"""Passwordless magic-link login: eligibility, mailer, and exchange.

Mirrors the QuizOnline design (request -> email a 15-min signed token;
exchange -> trade a valid token for a JWT pair) with TrainingManager's
conventions: the ``team/magic_action.py`` TimestampSigner family for the
token (see :mod:`customuser.magic_link_token`), the Microsoft-Graph mail
API (``tools/email.py``) wired through Django's ``send_mail``, and
``settings.FRONTEND_URL`` for the link.

"Email confirmed" is determined exactly like login
(:class:`customuser.serializers.VerifiedTokenObtainPairSerializer`): a
user is eligible if they have NO allauth ``EmailAddress`` rows (legacy,
pre-allauth accounts) OR at least one ``verified`` row. Once any
EmailAddress exists, the ``verified`` flag is authoritative.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.utils import translation
from django.utils.translation import gettext as _

from .magic_link_token import make_magic_link_token

logger = logging.getLogger(__name__)

User = get_user_model()


def _is_email_confirmed(user) -> bool:
    """Mirror the login gate: eligible iff the user has no EmailAddress
    rows (legacy) or at least one verified one."""
    from allauth.account.models import EmailAddress

    addresses = EmailAddress.objects.filter(user=user)
    if not addresses.exists():
        return True  # legacy account, pre-allauth — treated as confirmed
    return addresses.filter(verified=True).exists()


def _magic_link_url(token: str) -> str:
    """Absolute frontend URL the user receives in the email.

    No trailing slash — matches the SPA's strict-routing convention
    (cf. password-reset / email-confirm URLs in
    :class:`customuser.adapter.FrontendAccountAdapter`). The frontend
    route POSTs the token to /auth/magic-link/exchange/."""
    base = settings.FRONTEND_URL.rstrip("/")
    return f"{base}/auth/magic-link/{token}"


def send_magic_link_email(user) -> None:
    """Mail the user a magic-link to sign in (valid for 15 minutes).

    Localized to the user's language (gettext), plain-text body, sent
    through the same ``send_mail`` + DEFAULT_FROM_EMAIL path as the
    password-reset email. Best-effort: a mail failure is logged, never
    raised (so the request endpoint stays constant-time / non-leaky)."""
    if not getattr(user, "email", ""):
        return
    token = make_magic_link_token(user.id)
    link = _magic_link_url(token)
    with translation.override(user.language or "en"):
        subject = f"[TrainingManager] {_('Your sign-in link')}"
        body = (
            f"{_('Hello')} {user.first_name or user.username},\n\n"
            f"{_('Click the link below to sign in without a password:')}\n"
            f"{link}\n\n"
            f"{_('This link is valid for 15 minutes.')}\n"
            f"{_('If you did not request this, you can safely ignore this email.')}\n"
        )
        try:
            send_mail(
                subject=str(subject),
                message=str(body),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )
        except Exception:
            logger.exception("Failed to send magic-link email to %s", user.email)


def request_magic_link(email: str) -> None:
    """If an ACTIVE, email-confirmed user matches ``email``, mail them a
    magic link. Otherwise silently return.

    NEVER reveals whether the account exists (no enumeration): the caller
    (view) always returns the same 200 regardless. We don't ``.get()``
    (would raise DoesNotExist) — we ``.filter().first()`` and no-op on a
    miss / on an ineligible user.
    """
    user = User.objects.filter(email__iexact=(email or "").strip()).first()
    if user is None:
        return
    if not user.is_active or not _is_email_confirmed(user):
        return
    send_magic_link_email(user)


def exchange_magic_link(user_id: int):
    """Resolve the user id baked in a magic-link token and return the user
    iff it would normally be allowed to log in (still active +
    email-confirmed). Returns ``None`` for any refusal so the view can map
    a single response shape and never leak the cause.
    """
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return None
    if not user.is_active or not _is_email_confirmed(user):
        return None
    return user

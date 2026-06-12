"""Frontend SPA URL builders for the auth emails.

Replaces the former allauth ``FrontendAccountAdapter`` (removed with allauth).
These are plain helpers — no Django/allauth adapter machinery — that build the
absolute SPA links the confirmation / reset / email-change / magic-link emails
carry. No trailing slash, matching the SPA's strict-routing convention.
"""

from django.conf import settings


def _base() -> str:
    return settings.FRONTEND_URL.rstrip("/")


def get_email_confirmation_url(key: str) -> str:
    """Registration / resend confirmation link. ``key`` is ``"<uid>-<token>"``
    (Django's default_token_generator, mirroring QuizOnline)."""
    return f"{_base()}/auth/confirm-email/{key}"


def get_password_reset_url(key: str) -> str:
    """Password-reset link. ``key`` is ``"<uid>-<token>"``."""
    return f"{_base()}/auth/reset-password/{key}"


def get_email_change_url(token: str) -> str:
    """Verified change-email confirmation link (sent to the NEW address)."""
    return f"{_base()}/auth/confirm-email-change/{token}"


def get_magic_link_url(token: str) -> str:
    """Passwordless sign-in link."""
    return f"{_base()}/auth/magic-link/{token}"

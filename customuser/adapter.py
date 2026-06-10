"""Custom allauth adapter that points the email confirmation URL at the
frontend route.

We disabled allauth.headless (its API surface was dormant, replaced by
our own /auth/register/ + /auth/email/confirm/ endpoints). Without
HEADLESS_FRONTEND_URLS, allauth would default to its own template view
for `account_confirm_email` — but we don't have templates. Override
the URL builder so confirmation mails carry the frontend route the
SPA already handles.
"""

from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings


class FrontendAccountAdapter(DefaultAccountAdapter):
    def get_email_confirmation_url(self, request, emailconfirmation):
        base = settings.FRONTEND_URL.rstrip("/")
        return f"{base}/auth/confirm-email/{emailconfirmation.key}"

    @staticmethod
    def get_password_reset_url(key: str) -> str:
        """Build the absolute frontend URL the user receives in the
        password-reset email. No trailing slash — matches the SPA's
        strict-routing convention (cf. magic-link 277b331)."""
        base = settings.FRONTEND_URL.rstrip("/")
        return f"{base}/auth/reset-password/{key}"

    @staticmethod
    def get_email_change_url(token: str) -> str:
        """Absolute frontend URL for the verified change-email confirmation link
        (sent to the NEW address). No trailing slash (strict-routing SPA)."""
        base = settings.FRONTEND_URL.rstrip("/")
        return f"{base}/auth/confirm-email-change/{token}"

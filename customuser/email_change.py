"""Signed token + email for the verified change-email flow.

Same plumbing family as ``customuser/magic_link_token.py``: a Django
``TimestampSigner`` (salt ``auth.email-change``, 24h TTL) over
``"<user_pk>:<new_email>"``. The signature is the entire credential — there is
no DB row. The new email's uniqueness is re-checked at confirm time (another
account may have claimed it in the interval).
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import translation
from django.utils.translation import gettext as _

from tools.signed_token import SignedToken, nonempty_str

logger = logging.getLogger(__name__)

SIGNER_SALT = "auth.email-change"
TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24  # 24 hours

# "<user_pk>:<new_email>". raise_expired so the confirm view can show a distinct
# "link expired" (410) page vs an invalid (400) one.
_TOKEN = SignedToken(
    salt=SIGNER_SALT,
    max_age=TOKEN_MAX_AGE_SECONDS,
    converters=[int, nonempty_str],
    raise_expired=True,
)


def make_email_change_token(user_id: int, new_email: str) -> str:
    """HMAC-signed token encoding ``user_id`` + the requested ``new_email``."""
    return _TOKEN.sign(int(user_id), new_email)


def parse_email_change_token(token: str) -> tuple[int, str] | None:
    """Return ``(user_id, new_email)`` for a still-valid token, or ``None`` on a
    bad signature / malformed payload. Propagates :class:`SignatureExpired` so
    the caller can map a 410 (expired) distinctly from a 400 (invalid)."""
    return _TOKEN.parse(token)


def send_email_change_email(user, new_email: str) -> None:
    """Email the confirmation link to the NEW address, localized to the user's
    language. Never raises — a delivery failure is logged, not surfaced."""
    from customuser.frontend_urls import get_email_change_url

    token = make_email_change_token(user.pk, new_email)
    url = get_email_change_url(token)
    with translation.override(user.language or "en"):
        subject = f"[TrainingManager] {_('Confirm your new email address')}"
        body = (
            f"{_('Hello')} {user.first_name or user.email},\n\n"
            f"{_('You requested to change your account email to this address.')}\n\n"
            f"{_('To confirm the change, click the link below:')}\n"
            f"{url}\n\n"
            f"{_('If you did not request this, you can safely ignore this email.')}\n"
        )
        try:
            send_mail(
                subject=str(subject),
                message=str(body),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[new_email],
                fail_silently=False,
            )
        except Exception:  # noqa: BLE001 - delivery failure must not 500 the request
            logger.exception("Failed to send email-change confirmation to %s", new_email)

"""Signed one-click RSVP links for the session-reminder email.

A ``TimestampSigner`` over ``"event_id:member_id:status"`` (salt
``rsvp.one_click``, 72h TTL). The signature is the entire credential — no DB
row. Two-step by design: the email link is a GET that renders a confirmation
page, and only the page's POST button mutates, so an email link-prefetcher
cannot silently set a status.
"""

from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

from rsvp.models import RsvpStatus

SIGNER_SALT = "rsvp.one_click"
TOKEN_MAX_AGE_SECONDS = 72 * 3600  # 3 days — tolerate a late click on a "tomorrow" reminder
ALLOWED_STATUSES = set(RsvpStatus.values)


def _signer() -> TimestampSigner:
    return TimestampSigner(salt=SIGNER_SALT)


def make_token(event_id: int, member_id: int, status: str) -> str:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid RSVP status {status!r}")
    return _signer().sign(f"{int(event_id)}:{int(member_id)}:{status}")


def parse_token(token: str) -> tuple[int, int, str] | None:
    """``(event_id, member_id, status)`` for a valid token, or ``None`` on a bad
    signature / malformed payload. Propagates :class:`SignatureExpired` so the
    view can distinguish an expired link."""
    try:
        raw = _signer().unsign(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except SignatureExpired:
        raise
    except BadSignature:
        return None
    try:
        ev, mem, status = raw.split(":", 2)
    except ValueError:
        return None
    if status not in ALLOWED_STATUSES:
        return None
    try:
        return int(ev), int(mem), status
    except (ValueError, TypeError):
        return None


def _backend_base_url() -> str:
    """Absolute origin of the API itself (the magic endpoint is server-rendered,
    not an SPA route). Derived from the canonical ALLOWED_HOST."""
    host = (settings.ALLOWED_HOSTS or ["localhost"])[0]
    scheme = "http" if host in ("localhost", "127.0.0.1", "testserver") else "https"
    return f"{scheme}://{host}"


def rsvp_action_url(event_id: int, member_id: int, status: str) -> str:
    return f"{_backend_base_url()}/api/v1/rsvp-magic/{make_token(event_id, member_id, status)}/"

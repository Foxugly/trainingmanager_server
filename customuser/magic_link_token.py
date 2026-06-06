"""Signed, short-lived tokens for passwordless email login.

Same plumbing as ``team/magic_action.py`` (a Django
``TimestampSigner``), but salted to a distinct ``auth.magic_link``
namespace and with a much tighter TTL (15 minutes) because the token
grants direct authentication on click.

Payload: just the user primary key. The user's identity is
authoritative on the server side; we never trust client-side hints.
There is no DB row — the signature is the entire credential. A token is
replayable until it expires; the actual sign-in is harmless to repeat
(it only mints a fresh JWT pair for an already-eligible user).
"""

from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

SIGNER_SALT = "auth.magic_link"
TOKEN_MAX_AGE_SECONDS = 15 * 60  # 15 minutes


def _signer() -> TimestampSigner:
    return TimestampSigner(salt=SIGNER_SALT)


def make_magic_link_token(user_id: int) -> str:
    """Return an HMAC-signed token encoding ``user_id``.

    Tokens expire ``TOKEN_MAX_AGE_SECONDS`` after issue."""
    return _signer().sign(str(int(user_id)))


def parse_magic_link_token(token: str) -> int | None:
    """Reverse of :func:`make_magic_link_token`.

    Returns the integer ``user_id`` baked into a still-valid token, or
    ``None`` on any failure (bad signature, expired, malformed payload).

    Note: callers that need to distinguish *expired* from *invalid* (to
    map a 410 vs 400) should catch :class:`SignatureExpired` themselves —
    see :class:`customuser.views.MagicLinkExchangeView`.
    """
    try:
        raw = _signer().unsign(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except (SignatureExpired, BadSignature):
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None

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

from tools.signed_token import SignedToken

SIGNER_SALT = "auth.magic_link"
TOKEN_MAX_AGE_SECONDS = 15 * 60  # 15 minutes

# Payload: just the user pk. Expired is treated like invalid (-> None); the
# view re-derives the 410-vs-400 distinction on its own.
_TOKEN = SignedToken(salt=SIGNER_SALT, max_age=TOKEN_MAX_AGE_SECONDS, converters=[int])


def make_magic_link_token(user_id: int) -> str:
    """Return an HMAC-signed token encoding ``user_id``.

    Tokens expire ``TOKEN_MAX_AGE_SECONDS`` after issue."""
    return _TOKEN.sign(int(user_id))


def parse_magic_link_token(token: str) -> int | None:
    """Reverse of :func:`make_magic_link_token`.

    Returns the integer ``user_id`` baked into a still-valid token, or ``None``
    on any failure (bad signature, expired, malformed payload).
    """
    return _TOKEN.parse(token)

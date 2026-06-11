"""Signed, short-lived tokens for passwordless email login.

Same plumbing as ``team/magic_action.py`` (a Django
``TimestampSigner``), but salted to a distinct ``auth.magic_link``
namespace and with a much tighter TTL (15 minutes) because the token
grants direct authentication on click.

Payload: the user primary key AND a single-use ``nonce`` stored on the
user (``CustomUser.magic_link_nonce``). The user's identity is
authoritative on the server side; we never trust client-side hints. The
signature is the credential (the only DB state is the nonce field):
expiry bounds the window, and the nonce — cleared on exchange — makes the
link usable at most once, and a new request invalidates the previous one.
"""

from tools.signed_token import SignedToken, nonempty_str

SIGNER_SALT = "auth.magic_link"
TOKEN_MAX_AGE_SECONDS = 15 * 60  # 15 minutes

# Payload: (user pk, nonce). Expired is treated like invalid (-> None); the
# view re-derives the 410-vs-400 distinction on its own.
_TOKEN = SignedToken(
    salt=SIGNER_SALT, max_age=TOKEN_MAX_AGE_SECONDS, converters=[int, nonempty_str]
)


def make_magic_link_token(user_id: int, nonce: str) -> str:
    """Return an HMAC-signed token encoding ``user_id`` + the single-use
    ``nonce``. Tokens expire ``TOKEN_MAX_AGE_SECONDS`` after issue."""
    return _TOKEN.sign(int(user_id), nonce)


def parse_magic_link_token(token: str):
    """Reverse of :func:`make_magic_link_token`.

    Returns ``(user_id, nonce)`` for a still-valid token, or ``None`` on any
    failure (bad signature, expired, malformed payload).
    """
    return _TOKEN.parse(token)

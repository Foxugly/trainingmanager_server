"""HMAC-signed magic links for one-click accept/reject of TeamJoinRequest.

Format: TimestampSigner.sign(f"{join_request_id}:{action}") with a 48-hour
max-age. No DB row — the signature is the entire credential. A token
is replayable until it expires; the actual transition is idempotent
(see views.TeamJoinRequestMagicActionView).

Two-step interaction so Outlook safe-links / Gmail link previewers
can't accidentally trigger the action:
  - GET ?token=...  -> view returns the proposed action + current state
  - POST {token, confirm: true} -> view executes the action
"""

from django.conf import settings

from tools.signed_token import SignedToken, one_of

ALLOWED_ACTIONS = ("accept", "reject")
TOKEN_MAX_AGE_SECONDS = 48 * 3600  # 48 hours
SIGNER_SALT = "team.join_request.magic_action"

# "<join_request_id>:<action>". Expired is treated like invalid (-> None): a
# manager who clicks an old link just gets a generic "invalid or expired" result.
_TOKEN = SignedToken(
    salt=SIGNER_SALT,
    max_age=TOKEN_MAX_AGE_SECONDS,
    converters=[int, one_of(ALLOWED_ACTIONS)],
)


def make_token(join_request_id: int, action: str) -> str:
    """Return an HMAC-signed token encoding (join_request_id, action).

    `action` must be in ALLOWED_ACTIONS. Tokens expire 48 hours after issue."""
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"action must be one of {ALLOWED_ACTIONS}, got {action!r}")
    return _TOKEN.sign(int(join_request_id), action)


def parse_token(token: str) -> tuple[int, str] | None:
    """Reverse of make_token. Returns (join_request_id, action) or None on
    any failure (bad signature, expired, malformed payload)."""
    return _TOKEN.parse(token)


def magic_link(join_request_id: int, action: str) -> str:
    """Build the absolute URL the manager will receive in the email.

    No trailing slash: matches the Angular frontend's strict-routing
    convention (the frontend route is /team-join-requests/magic-action/:token).
    The Django REST endpoint /api/v1/join-magic/<token>/ keeps its trailing
    slash — this URL is the one shipped to the browser, not the API."""
    base = settings.FRONTEND_URL.rstrip("/")
    token = make_token(join_request_id, action)
    return f"{base}/team-join-requests/magic-action/{token}"

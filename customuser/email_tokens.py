"""uid + token plumbing for email-confirmation and password-reset links.

Replaces allauth's token machinery with Django's stock
``default_token_generator`` (django.contrib.auth.tokens) + base64url-encoded
user pk, mirroring the QuizOnline reference implementation.

The link key carried in the email is ``"<uid>-<token>"`` where ``uid`` is
``urlsafe_base64_encode(force_bytes(user.pk))``. The token itself can contain
dashes, so callers must split on the FIRST dash only.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

User = get_user_model()


def make_key(user) -> str:
    """Build the ``"<uid>-<token>"`` key for an email link."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f"{uid}-{token}"


def resolve_user_from_uid(uid: str):
    """Decode a base64url uid back to a user, or return ``None``."""
    try:
        uid_int = force_str(urlsafe_base64_decode(uid))
        return User.objects.get(pk=uid_int)
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        return None


def parse_key(key: str):
    """Split a ``"<uid>-<token>"`` key and resolve the (user, token).

    Returns ``(user, token)`` with ``user`` possibly ``None`` if the uid does
    not resolve. Returns ``None`` when the key has no ``"-"`` separator.
    """
    if "-" not in key:
        return None
    uid, token = key.split("-", 1)
    return resolve_user_from_uid(uid), token


def token_is_valid(user, token: str) -> bool:
    return bool(user) and default_token_generator.check_token(user, token)

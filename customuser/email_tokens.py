"""uid + token plumbing for email-confirmation and password-reset links.

Replaces allauth's token machinery with Django's stock
``default_token_generator`` (django.contrib.auth.tokens) + base64url-encoded
user pk, mirroring the QuizOnline reference implementation.

The link key carried in the email is ``"<uid>-<token>"`` where ``uid`` is
``urlsafe_base64_encode(force_bytes(user.pk))``. The token itself can contain
dashes, so callers must split on the FIRST dash only.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator, default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

User = get_user_model()


class EmailConfirmationTokenGenerator(PasswordResetTokenGenerator):
    """Token generator dedicated to email-confirmation links.

    A distinct ``key_salt`` keeps confirmation and password-reset tokens
    non-interchangeable, and hashing ``email_confirmed`` makes a confirmation
    link single-use: it stops validating as soon as the account is confirmed.
    """

    key_salt = "customuser.email_tokens.EmailConfirmationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{super()._make_hash_value(user, timestamp)}{user.email_confirmed}{user.email}"


email_confirmation_token_generator = EmailConfirmationTokenGenerator()


def make_key(user, generator=default_token_generator) -> str:
    """Build the ``"<uid>-<token>"`` key for an email link.

    Defaults to the password-reset generator; confirmation links go through
    :func:`make_confirmation_key`.
    """
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = generator.make_token(user)
    return f"{uid}-{token}"


def make_confirmation_key(user) -> str:
    """Build a single-use email-confirmation key (never valid for a reset)."""
    return make_key(user, generator=email_confirmation_token_generator)


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


def token_is_valid(user, token: str, generator=default_token_generator) -> bool:
    return bool(user) and generator.check_token(user, token)


def confirmation_token_is_valid(user, token: str) -> bool:
    return token_is_valid(user, token, generator=email_confirmation_token_generator)

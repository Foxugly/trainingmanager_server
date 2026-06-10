"""Shared plumbing for the app's signed, DB-less tokens.

Several features mint a short-lived credential that *is* its own signature (no
DB row): passwordless magic-login, the team join-request magic actions, the
verified email-change flow, and one-click RSVP. They all wrap a Django
``TimestampSigner`` over a ``sep``-joined payload of a few fields, with a salt
and a TTL. This module factors that out so each feature is just a salt + a TTL +
the per-field converters, instead of copy-pasted ``sign``/``unsign`` boilerplate.

Two deliberately different expiry behaviours are supported via ``raise_expired``:
flows that show a distinct "link expired" page (email-change, RSVP) set it True
so :class:`~django.core.signing.SignatureExpired` propagates; flows that treat
expired and invalid the same (magic-login, join actions) leave it False and get
``None``.
"""

from collections.abc import Callable, Sequence

from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

__all__ = ["SignedToken", "nonempty_str", "one_of"]


def nonempty_str(value: str) -> str:
    """Converter: accept a non-empty string, else reject (raise ValueError)."""
    if not value:
        raise ValueError("empty value")
    return value


def one_of(allowed) -> Callable[[str], str]:
    """Converter factory: accept a value only if it is in ``allowed``."""
    allowed = set(allowed)

    def convert(value: str) -> str:
        if value not in allowed:
            raise ValueError(f"value {value!r} not allowed")
        return value

    return convert


class SignedToken:
    """A ``TimestampSigner`` over a ``sep``-joined payload of N typed fields.

    ``converters`` is one callable per field, applied to the raw string segment
    on parse (``int``, :func:`nonempty_str`, :func:`one_of`, …); a converter that
    raises rejects the token. ``sign(*values)`` stringifies and joins; the value
    count must match ``converters``.
    """

    def __init__(
        self,
        *,
        salt: str,
        max_age: int,
        converters: Sequence[Callable[[str], object]],
        sep: str = ":",
        raise_expired: bool = False,
    ):
        self.salt = salt
        self.max_age = max_age
        self.converters = list(converters)
        self.sep = sep
        self.raise_expired = raise_expired

    def _signer(self) -> TimestampSigner:
        return TimestampSigner(salt=self.salt)

    def sign(self, *values) -> str:
        if len(values) != len(self.converters):
            raise ValueError(
                f"expected {len(self.converters)} value(s), got {len(values)}"
            )
        return self._signer().sign(self.sep.join(str(v) for v in values))

    def parse(self, token: str):
        """Return the typed field(s) of a valid token, or ``None`` on a bad
        signature / malformed payload. A single-field token returns the scalar;
        a multi-field token returns a tuple. Propagates
        :class:`SignatureExpired` when ``raise_expired`` is set."""
        try:
            raw = self._signer().unsign(token, max_age=self.max_age)
        except SignatureExpired:
            if self.raise_expired:
                raise
            return None
        except BadSignature:
            return None
        parts = raw.split(self.sep, len(self.converters) - 1)
        if len(parts) != len(self.converters):
            return None
        try:
            values = tuple(conv(p) for conv, p in zip(self.converters, parts))
        except (ValueError, TypeError, KeyError):
            return None
        return values[0] if len(values) == 1 else values

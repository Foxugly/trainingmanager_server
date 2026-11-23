"""Repo-local Sentry scrubbing (sentry-sdk 3.x).

sentry-sdk 3.0 removed ``EventScrubber`` (and its ``DEFAULT_DENYLIST``), which
2.x ran on every event. This module re-applies the same denylist, plus the
2.x PII keys, everywhere the old scrubber looked (request headers, cookies,
data and env, ``extra``, ``user``, breadcrumb data, stack-frame variables,
spans), and also to ``contexts`` (e.g. response headers, trace data),
``tags``, ``transaction``, ``culprit``, log messages, exception and thread
values and types, and span names and attributes for ``before_send_span``.

It only ever removes data: a key is sensitive when it is one of the 2.x
denylist keys, or when it *contains* one of a few fragments (``password``,
``token``, ``secret``…), which is stricter than 2.x. In strings it masks
``key=value`` pairs, ``Bearer`` credentials, URL userinfo (``user:pass@``;
an authority with a ``:`` that is not ``host:port`` is masked whole), query
values whose name is sensitive or whose value looks opaque (16+ URL-safe
characters), URL fragments, the secret segment of this repo's token-bearing
routes (``SECRET_PATH_PREFIXES``, taken from its ``urls.py``) and any long
opaque path segment (letters + digits, 24+ characters). Containers deeper
than the depth cap, bytes and unknown objects are scrubbed or replaced
whole: nothing is skipped because of its type (fail closed).

Self-contained on purpose: no SDK internals, so a future SDK release cannot
break it at import time. Nothing here may raise into the SDK — in 3.0 an
exception in a callback drops the event (or, for a span, restores the
unscrubbed original) — so both entry points fall back to stripping.
"""

from __future__ import annotations

import datetime
import re
from typing import Any
from urllib.parse import unquote_plus

FILTERED = "[Filtered]"

#: Routes whose next path segment is a secret (magic links, invitations,
#: public feeds…): keep in sync with ``urls.py``. Matched case-insensitively
#: on ``/<prefix>/<secret>``; the secret becomes ``[Filtered]``.
SECRET_PATH_PREFIXES: tuple[str, ...] = (
    "public/events",
    "rsvp-magic",
    "invitations/lookup",
    "join-magic",
    "calendar",
)

#: sentry-sdk 2.x ``DEFAULT_DENYLIST`` + ``DEFAULT_PII_DENYLIST``, normalised
#: (lower case, ``-`` -> ``_``) and matched on the whole key.
_EXACT_KEYS = frozenset(
    k.lower().replace("-", "_")
    for k in (
        "password", "passwd", "secret", "api_key", "apikey", "auth",
        "credentials", "mysql_pwd", "privatekey", "private_key", "token",
        "session", "csrftoken", "sessionid", "x_csrftoken", "set_cookie",
        "cookie", "authorization", "proxy-authorization", "x_api_key",
        "aiohttp_session", "connect.sid", "csrf_token", "csrf", "_csrf",
        "_csrf_token", "PHPSESSID", "_session", "symfony", "user_session",
        "_xsrf", "XSRF-TOKEN",
        # PII (send_default_pii=False)
        "x_forwarded_for", "x_real_ip", "ip_address", "remote_addr",
    )
)  # fmt: skip

#: A key containing any of these (after normalisation) is sensitive too.
_KEY_FRAGMENTS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "authorization", "cookie", "session", "csrf", "xsrf", "private_key",
    "privatekey", "credential", "jwt", "bearer", "totp", "otp_",
)  # fmt: skip

_SECRET_WORDS = (
    r"password|passwd|pwd|secret|token|api[_-]?key|apikey|authorization|"
    r"session(?:id)?|csrf\w*|xsrf\w*|jwt|signature|code|otp"
)
#: ``name=value`` (query strings, form bodies, log lines) with a sensitive name.
_PAIR_RE = re.compile(r"(?i)(\b[\w.\-\[\]]*(?:" + _SECRET_WORDS + r")[\w.\-\[\]]*=)[^&\s#;,'\"]+")
#: ``Bearer <credential>`` / ``Basic <credential>``.
_SCHEME_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
#: Absolute URLs (any scheme).
_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s'\"<>]+")
#: Relative URLs / paths standing on their own (``GET /x?y#z``, ``'/x'``).
_PATHLIKE_RE = re.compile(r"(?<![\w/.:\]-])/[^\s'\"<>]*")
#: ``host``, ``host:port``, ``[ipv6]``, ``[ipv6]:port``.
_HOSTPORT_RE = re.compile(r"(?:\[[0-9A-Fa-f:.]*\]|[^:@\[\]]*)(?::\d{0,5})?")
#: A query value that looks like a token (no digit required).
_OPAQUE_VALUE_RE = re.compile(r"[A-Za-z0-9_\-.~+/=%:]{16,}")
#: A query name (or a value-less chunk) that looks like a token: same, with
#: at least one letter and one digit, so long plain-word names survive.
_OPAQUE_NAME_RE = re.compile(r"(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9_\-.~+/=%:]{16,}")
#: A bare parameter / header name (not a sentence, not a ``k=v`` pair).
_NAME_RE = re.compile(r"[\w.\-]{1,64}")
_SECRET_WORD_RE = re.compile(r"(?i)" + _SECRET_WORDS)
_SEGMENT = r"[^/\s?#'\"<>]+"
#: ``/<prefix>/<secret>`` for this repo's token-bearing routes.
_SECRET_PATH_RE = (
    re.compile(
        r"(?i)(/(?:"
        + "|".join(re.escape(p.strip("/")) for p in SECRET_PATH_PREFIXES)
        + r")/)"
        + _SEGMENT
    )
    if SECRET_PATH_PREFIXES
    else None
)
#: A long opaque path segment (signed token, key, hash): 24+ URL-safe
#: characters with at least one letter and one digit.
_OPAQUE_SEGMENT_RE = re.compile(
    r"(?<=/)(?=[^/\s?#'\"<>]*\d)(?=[^/\s?#'\"<>]*[A-Za-z])"
    r"[A-Za-z0-9_\-.~=%:]{24,}(?=[/?#\s'\"<>]|$)"
)

_MAX_DEPTH = 12

#: Values that cannot carry a secret (breadcrumb timestamps are datetimes).
_SAFE_SCALARS = (bool, int, float, datetime.date, datetime.time, datetime.timedelta)

#: Event sections stripped when scrubbing itself fails (never leak on error).
_RISKY_SECTIONS = ("request", "extra", "user", "breadcrumbs", "contexts", "spans")


def is_sensitive_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    norm = key.lower().replace("-", "_")
    if norm in _EXACT_KEYS or norm.rsplit(".", 1)[-1] in _EXACT_KEYS:
        return True
    return any(fragment in norm for fragment in _KEY_FRAGMENTS)


def _secret_name(name: str) -> bool:
    return is_sensitive_key(name) or bool(_SECRET_WORD_RE.search(name))


def scrub_query(query: str) -> str:
    """Scrub a query string chunk by chunk. Names and values are checked both
    raw and percent-decoded (``unquote_plus``), so encoding cannot hide a
    secret: a sensitive or opaque name, or a value-less opaque chunk, becomes
    ``[Filtered]``; a value that is opaque, or that contains anything
    ``scrub_text`` would mask once decoded, becomes ``name=[Filtered]``."""
    parts = []
    for part in query.split("&"):
        raw_name, sep, raw_value = part.partition("=")
        name, value = unquote_plus(raw_name), unquote_plus(raw_value)
        if raw_name and (_OPAQUE_NAME_RE.fullmatch(raw_name) or _OPAQUE_NAME_RE.fullmatch(name)):
            part = FILTERED
        elif raw_name and (_secret_name(raw_name) or _secret_name(name)):
            part = raw_name + "=" + FILTERED if sep else FILTERED
        elif (
            sep
            and raw_value
            and (
                _OPAQUE_VALUE_RE.fullmatch(raw_value)
                or _OPAQUE_VALUE_RE.fullmatch(value)
                or scrub_text(value) != value
            )
        ):
            part = raw_name + "=" + FILTERED
        parts.append(part)
    return "&".join(parts)


def _scrub_url(url: str, absolute: bool) -> str:
    prefix, tail = "", url
    if absolute:
        scheme, _, rest = url.partition("://")
        end = min((i for i in (rest.find(c) for c in "/?#") if i >= 0), default=len(rest))
        authority, tail = rest[:end], rest[end:]
        # Userinfo: anything before an "@" in the authority goes.
        authority = authority.rpartition("@")[2]
        if not _HOSTPORT_RE.fullmatch(authority):
            authority = FILTERED
        prefix = scheme + "://" + authority
    tail, hash_sep, fragment = tail.partition("#")
    path, query_sep, query = tail.partition("?")
    out = prefix + path
    if query_sep:
        out += "?" + scrub_query(query)
    if hash_sep:
        out += "#" + (FILTERED if fragment else "")
    return out


def scrub_text(text: str) -> str:
    """Mask URL userinfo, sensitive or opaque query values, URL fragments,
    sensitive ``name=value`` pairs, ``Bearer`` credentials, secret route
    segments and long opaque path segments."""
    text = _URL_RE.sub(lambda m: _scrub_url(m.group(0), absolute=True), text)
    text = _PATHLIKE_RE.sub(lambda m: _scrub_url(m.group(0), absolute=False), text)
    text = _PAIR_RE.sub(lambda m: m.group(1) + FILTERED, text)
    text = _SCHEME_RE.sub(lambda m: m.group(1) + " " + FILTERED, text)
    if _SECRET_PATH_RE is not None:
        text = _SECRET_PATH_RE.sub(lambda m: m.group(1) + FILTERED, text)
    return _OPAQUE_SEGMENT_RE.sub(FILTERED, text)


def _mask_pair(items: list[Any]) -> None:
    """``(name, value)`` pairs (e.g. a parsed query string or header list):
    mask the value when the name is sensitive."""
    name = items[0] if len(items) == 2 else None
    if isinstance(name, str) and _NAME_RE.fullmatch(name) and _secret_name(name):
        items[1] = FILTERED


def scrub_data(value: Any, depth: int = 0) -> Any:
    """Scrub anything and return the scrubbed value: dicts and lists in
    place, tuples and sets by copy, strings and bytes through ``scrub_text``.
    Nothing is skipped because of its type (fail closed): a container nested
    deeper than the cap, or an object of an unknown type, becomes
    ``[Filtered]``."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, (bytes, bytearray)):
        return scrub_text(bytes(value).decode("utf-8", "replace"))
    if value is None or isinstance(value, _SAFE_SCALARS):
        return value
    if not isinstance(value, (dict, list, tuple, set, frozenset)) or depth >= _MAX_DEPTH:
        return FILTERED
    if isinstance(value, dict):
        for key, item in value.items():
            value[key] = FILTERED if is_sensitive_key(key) else scrub_data(item, depth + 1)
        return value
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = scrub_data(item, depth + 1)
        _mask_pair(value)
        return value
    items = [scrub_data(item, depth + 1) for item in value]
    if isinstance(value, tuple):
        _mask_pair(items)
    return type(value)(items)


def _iter_frames(event: dict[str, Any]):
    stacktraces = []
    for section in ("exception", "threads"):
        container = event.get(section)
        if isinstance(container, dict):
            for item in container.get("values") or ():
                if isinstance(item, dict):
                    stacktraces.append(item.get("stacktrace"))
    stacktraces.append(event.get("stacktrace"))
    for stacktrace in stacktraces:
        if isinstance(stacktrace, dict):
            for frame in stacktrace.get("frames") or ():
                if isinstance(frame, dict):
                    yield frame


def _scrub_event(event: dict[str, Any]) -> None:
    user = event.get("user")
    if isinstance(user, dict):
        user.pop("ip_address", None)
    # Whole sections, whatever their type: scrub_data fails closed.
    for key in (
        "request",
        "extra",
        "contexts",
        "user",
        "breadcrumbs",
        "spans",
        "logentry",
        "message",
        "tags",
        "transaction",
        "culprit",
    ):
        if key in event:
            event[key] = scrub_data(event[key])
    request = event.get("request")
    if isinstance(request, dict) and isinstance(request.get("query_string"), str):
        request["query_string"] = scrub_query(request["query_string"])
    for section in ("exception", "threads"):
        container = event.get(section)
        if isinstance(container, dict):
            for item in container.get("values") or ():
                if isinstance(item, dict):
                    for key in ("value", "type"):
                        if key in item:
                            item[key] = scrub_data(item[key])
    for frame in _iter_frames(event):
        if "vars" in frame:
            frame["vars"] = scrub_data(frame["vars"])


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Scrub an error event in place and return it. Never raises: on an
    unexpected shape it strips the risky sections and frame variables."""
    try:
        _scrub_event(event)
    except Exception:
        for key in _RISKY_SECTIONS:
            event.pop(key, None)
        try:
            for frame in _iter_frames(event):
                frame.pop("vars", None)
        except Exception:
            event.pop("exception", None)
            event.pop("threads", None)
            event.pop("stacktrace", None)
    return event


def _scrub_span(span: dict[str, Any]) -> None:
    for key in ("name", "description", "attributes", "data"):
        if key in span:
            span[key] = scrub_data(span[key])
    if not isinstance(span.get("name"), str):
        span["name"] = FILTERED


def scrub_span(span: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """``before_send_span``: scrub the span name and attributes. A span
    cannot be dropped here (3.0); use ``ignore_spans`` for that. Never
    raises: on failure the name is masked and the attributes emptied, since
    the SDK would otherwise send the unscrubbed original."""
    try:
        _scrub_span(span)
    except Exception:
        span["name"] = FILTERED
        span["attributes"] = {}
    return span

"""Sentry SDK configuration (sentry-sdk 3.x).

``DATA_COLLECTION`` replaced ``send_default_pii=False`` and collects no more
than before: no user (id, e-mail, IP), no cookies, no request or response
bodies, no SQL parameters, no GenAI / MCP content.
Headers, query strings and local variables go through the SDK's sensitive-key
denylist, plus the proxy/IP header terms the SDK itself used when it mapped
``send_default_pii=False``.

sentry-sdk 3.0 removed the built-in ``EventScrubber``: ``settings.sentry_scrub``
re-applies its denylist from ``before_send`` (events) and ``before_send_span``
(span names and attributes).
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import DisallowedHost

from .sentry_scrub import scrub_event, scrub_span

_PII_TERMS = ["forwarded", "-ip", "remote-", "via", "-user"]

DATA_COLLECTION: dict[str, Any] = {
    "user_info": False,
    "cookies": {"mode": "off"},
    # ``response`` is not read by sentry-sdk 3.0.0 (it only collects request
    # headers); pinned off so a later release cannot start collecting them.
    "http_headers": {
        "request": {"mode": "denylist", "terms": _PII_TERMS},
        "response": {"mode": "off"},
    },
    "http_bodies": [],
    "url_query_params": {"mode": "denylist", "terms": _PII_TERMS},
    "graphql": {"document": False, "variables": False},
    "gen_ai": {"inputs": False, "outputs": False},
    "mcp": {"inputs": False, "outputs": False},
    "database_query_data": False,
    "queues": False,
    "stack_frame_variables": {"mode": "denylist"},
    "frame_context_lines": 5,
}


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Scrub, then drop public-endpoint noise: bots/scanners hitting the API
    with a bad Host header raise DisallowedHost (a handled 400, not a bug)."""
    scrub_event(event, hint)
    exc = (hint.get("exc_info") or (None, None, None))[1]
    if isinstance(exc, DisallowedHost):
        return None
    if str(event.get("logger", "")).startswith("django.security.DisallowedHost"):
        return None
    return event


def sentry_options(
    *, dsn: str, environment: str, release: str, traces_sample_rate: float
) -> dict[str, Any]:
    """Keyword arguments for ``sentry_sdk.init`` (shared by the settings and
    the tests, so the tests exercise the production configuration)."""
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    return {
        "dsn": dsn,
        "environment": environment,
        "release": release,
        "integrations": [
            # 3.0 turned cache spans on by default; keep 2.x's span volume.
            DjangoIntegration(cache_spans=False),
            # 3.0 no longer auto-enables logging and defaults ``event_level``
            # to None. Same as the 2.x auto-enabled integration: INFO+ logs
            # become breadcrumbs, ERROR+ become events, and no Sentry Logs
            # (``level=None``; 3.0 dropped ``enable_logs``, so its INFO
            # default would ship every log record).
            LoggingIntegration(
                level=None, breadcrumb_level=logging.INFO, event_level=logging.ERROR
            ),
        ],
        "before_send": before_send,
        "before_send_span": scrub_span,
        # 3.0 default is True; 2.x did not attach stack traces (and their
        # local variables) to plain messages.
        "attach_stacktrace": False,
        "traces_sample_rate": traces_sample_rate,
        "data_collection": DATA_COLLECTION,
    }

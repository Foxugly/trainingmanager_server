"""HttpOnly refresh-token cookie for the web SPA (opt-in, fleet design).

Same contract as the PushIT pilot (PushIT_server#69). The SPA opts in by
sending ``X-Auth-Mode: cookie`` on its auth calls. For those requests the
refresh token travels in an ``HttpOnly; Secure; SameSite=Lax`` cookie scoped
to ``/api/v1/auth/`` on the API host, and is **omitted from the JSON body**:
JavaScript never sees it, so an XSS can no longer exfiltrate the refresh
token. Requests without the header (the KMP app ``trainingmanager_app``, old
SPA bundles) keep the historical body-token contract.

CSRF: once the cookie authenticates ``token/refresh/`` and ``logout/``, a
cross-site page could try to drive them. Two independent guards:

* the custom ``X-Auth-Mode`` header makes every cookie-mode call a non-simple
  CORS request, so the browser preflights it and only the explicitly allowed
  origins (``CORS_ALLOWED_ORIGINS``, credentials on) get through;
* every cookie-mode request must also carry an ``Origin`` that is in
  ``CORS_ALLOWED_ORIGINS`` (checked server-side, 403 otherwise).

``SameSite=Lax`` works because the SPA (tm.foxugly.com) and the API
(tm-api.foxugly.com) share the registrable domain foxugly.com: they are
*same-site* even though they are cross-origin.
"""

from __future__ import annotations

import time

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse
from rest_framework import status
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

AUTH_MODE_HEADER = "X-Auth-Mode"
AUTH_MODE_COOKIE = "cookie"


AUTH_MODE_PARAMETER = OpenApiParameter(
    name="X-Auth-Mode",
    type=str,
    location=OpenApiParameter.HEADER,
    required=False,
    enum=["cookie"],
    description=(
        "Web SPA only. `cookie` moves the refresh token into an HttpOnly, Secure, "
        "SameSite=Lax cookie scoped to `/api/v1/auth/` (and omits `refresh` from the "
        "body); the request's `Origin` must then be in CORS_ALLOWED_ORIGINS (403 "
        "`origin_not_allowed`). Absent: body tokens (mobile app)."
    ),
)

REFRESH_COOKIE_NOTE = (
    " Omitted when the request sent `X-Auth-Mode: cookie`: the token is then "
    "set in the HttpOnly refresh cookie instead."
)

ORIGIN_REJECTED_RESPONSE = OpenApiResponse(
    description="Cookie mode from an origin that is not allowed (code=origin_not_allowed)."
)


def cookie_name() -> str:
    return settings.AUTH_REFRESH_COOKIE_NAME


def wants_cookie(request) -> bool:
    """True when the client opted into cookie transport for the refresh token."""
    return request.headers.get(AUTH_MODE_HEADER, "").strip().lower() == AUTH_MODE_COOKIE


def origin_allowed(request) -> bool:
    origin = request.headers.get("Origin")
    return bool(origin) and origin in settings.CORS_ALLOWED_ORIGINS


def origin_rejected_response() -> Response:
    return Response(
        {
            "code": "origin_not_allowed",
            "detail": _("Cookie authentication is only accepted from an allowed origin."),
        },
        status=status.HTTP_403_FORBIDDEN,
    )


def cookie_origin_guard(request) -> Response | None:
    """403 for a cookie-mode request from an origin that is not allowed (CSRF)."""
    if wants_cookie(request) and not origin_allowed(request):
        return origin_rejected_response()
    return None


def read_refresh_cookie(request) -> str:
    return request.COOKIES.get(cookie_name(), "")


def _max_age(refresh: str) -> int:
    """Seconds until the token expires: the cookie lives exactly as long.

    Follows the token's own ``exp`` so the 7-day default, the 30-day
    ``remember`` login and rotation (which resets to 7 days) need no extra
    bookkeeping. Only ever called on a token this process just minted or
    rotated, so the signature was checked upstream; we read one claim.
    """
    fallback = int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds())
    try:
        exp = RefreshToken(refresh, verify=False).payload.get("exp")
    except TokenError:
        return fallback
    if not isinstance(exp, int | float):
        return fallback
    return max(0, int(exp - time.time()))


def set_refresh_cookie(response, refresh: str) -> None:
    response.set_cookie(
        cookie_name(),
        refresh,
        max_age=_max_age(refresh),
        path=settings.AUTH_REFRESH_COOKIE_PATH,
        domain=None,  # host-only: the API host, never the whole foxugly.com
        secure=settings.AUTH_REFRESH_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",
    )


def clear_refresh_cookie(response) -> None:
    response.delete_cookie(
        cookie_name(),
        path=settings.AUTH_REFRESH_COOKIE_PATH,
        samesite="Lax",
    )


def deliver_tokens(request, response):
    """Move ``refresh`` from the JSON body into the cookie when opted in.

    No-op for body-mode clients (mobile app, old SPA bundles) and for error
    responses (nothing to move).
    """
    if not wants_cookie(request):
        return response
    data = getattr(response, "data", None)
    if isinstance(data, dict) and data.get("refresh"):
        refresh = data.pop("refresh")
        set_refresh_cookie(response, refresh)
    return response

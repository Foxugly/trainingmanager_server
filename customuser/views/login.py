"""Login + session lifecycle: verified JWT obtain, logout (refresh revoke),
and the passwordless magic-link request/exchange pair."""

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import (
    TokenRefreshSerializer as SimpleJWTRefreshSerializer,
)
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from tools.exceptions import CaptchaFailed
from tools.throttling import LoginThrottle, LogoutThrottle, MagicLinkRequestThrottle
from tools.turnstile import get_remote_ip, turnstile_configured, verify_turnstile_token

from .. import refresh_cookie
from ..refresh_cookie import AUTH_MODE_PARAMETER, ORIGIN_REJECTED_RESPONSE, REFRESH_COOKIE_NOTE
from ..serializers import (
    LogoutSerializer,
    MagicLinkExchangeRequestSerializer,
    MagicLinkExchangeResponseSerializer,
    MagicLinkRequestSerializer,
    TokenRefreshSerializer,
    VerifiedTokenObtainPairSerializer,
)
from ._helpers import _jwt_pair


@extend_schema(
    parameters=[AUTH_MODE_PARAMETER],
    responses={
        200: OpenApiResponse(
            response=inline_serializer(
                name="TokenObtainPairResponse",
                fields={
                    "access": drf_serializers.CharField(),
                    "refresh": drf_serializers.CharField(
                        required=False, help_text="Refresh token." + REFRESH_COOKIE_NOTE
                    ),
                },
            ),
            description=(
                "JWT pair. Use `access` as a Bearer token; obtain a fresh "
                "`access` via /auth/token/refresh/ when it expires. The "
                "refresh TTL depends on `remember`: 7 days (default) or "
                "30 days when remember=true."
            ),
        ),
        400: OpenApiResponse(
            description=(
                "Email not verified (code=email_not_verified) — the frontend "
                "should expose a 'Resend confirmation email' affordance — or "
                "any other validation error normalized by the custom "
                "exception handler."
            ),
        ),
        401: OpenApiResponse(description="Invalid username/email or password."),
        403: ORIGIN_REJECTED_RESPONSE,
    },
)
class VerifiedTokenObtainPairView(TokenObtainPairView):
    """Drop-in replacement for SimpleJWT's TokenObtainPairView that refuses
    login when the user's primary email is unverified.

    Rate-limited to 10 requests per minute per IP (anti-bruteforce).

    The @extend_schema(responses=...) above is required because SimpleJWT's
    TokenObtainPairSerializer describes the *input* shape ({username, password})
    and drf-spectacular would otherwise reuse it for the response — leading
    to a wrong codegen on the frontend (Observable<{username, password}>
    instead of Observable<{access, refresh}>).
    """

    serializer_class = VerifiedTokenObtainPairSerializer
    throttle_classes = [LoginThrottle]

    def post(self, request, *args, **kwargs):
        if (rejected := refresh_cookie.cookie_origin_guard(request)) is not None:
            return rejected
        return refresh_cookie.deliver_tokens(request, super().post(request, *args, **kwargs))


class MagicLinkRequestView(APIView):
    """POST /api/v1/auth/magic-link/request/ — public: email a sign-in link.

    Anti-leak: ALWAYS returns 200 with the same body, whether the email
    matches an active, email-confirmed User or not. If it does, an email
    with a 15-minute signed magic-link is dispatched (frontend URL:
    {FRONTEND_URL}/auth/magic-link/{token}, no trailing slash). If it
    doesn't, the call is a silent no-op — the response is byte-for-byte
    identical (no user enumeration, timing-insensitive wording).

    Rate-limited to 5 requests per hour per IP. When Cloudflare Turnstile
    is configured, ``turnstile_token`` is required and verified before any
    lookup: missing, blank or invalid -> 400 ``captcha_failed``
    (fail-closed).
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [MagicLinkRequestThrottle]

    @extend_schema(
        tags=["auth"],
        request=MagicLinkRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="MagicLinkRequestResponse",
                    fields={"detail": drf_serializers.CharField()},
                ),
                description=(
                    'Always 200 with {"detail": "ok"}. If the email matches '
                    "an active, email-confirmed account, a sign-in link has been "
                    "dispatched; otherwise the response is identical (anti-leak)."
                ),
            ),
            400: OpenApiResponse(
                description="Body validation error (malformed email) or Turnstile failure (code=captcha_failed)."
            ),
            429: OpenApiResponse(description="Too Many Requests (rate limit hit)."),
        },
    )
    def post(self, request):
        from customuser.magic_link import request_magic_link

        serializer = MagicLinkRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Server-side Turnstile check before any DB lookup. Fail-closed when
        # Turnstile is configured: a missing/blank token is rejected like an
        # invalid one (verify_turnstile_token returns False for "").
        if turnstile_configured():
            token = serializer.validated_data.get("turnstile_token", "")
            if not verify_turnstile_token(token, remote_ip=get_remote_ip(request)):
                raise CaptchaFailed()
        request_magic_link(serializer.validated_data["email"])
        return Response({"detail": "ok"})


class MagicLinkExchangeView(APIView):
    """POST /api/v1/auth/magic-link/exchange/ — public: trade token for JWT.

    Body: {token}. The token is the signed string the user received in
    their magic-link email. On success returns the JWT pair (access +
    refresh) for sign-in. Error mapping:
      - expired token        -> 410 {"detail": "token_expired"}
      - invalid / tampered   -> 400 {"detail": "token_invalid"}
      - user no longer eligible (inactive / unconfirmed / deleted)
                             -> 400 {"detail": "token_invalid"}
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=["auth"],
        request=MagicLinkExchangeRequestSerializer,
        parameters=[AUTH_MODE_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=MagicLinkExchangeResponseSerializer,
                description="Token accepted. Returns the JWT pair {access, refresh}.",
            ),
            400: OpenApiResponse(
                description=(
                    "Token invalid/tampered, or the user is no longer eligible "
                    "(inactive / email-unconfirmed / deleted). "
                    'Body: {"detail": "token_invalid"}.'
                )
            ),
            410: OpenApiResponse(
                description='Token expired. Body: {"detail": "token_expired"}.',
            ),
            403: ORIGIN_REJECTED_RESPONSE,
        },
    )
    def post(self, request):
        from django.core.signing import (
            BadSignature,
            SignatureExpired,
            TimestampSigner,
        )

        from customuser.magic_link import exchange_magic_link
        from customuser.magic_link_token import SIGNER_SALT, TOKEN_MAX_AGE_SECONDS

        if (rejected := refresh_cookie.cookie_origin_guard(request)) is not None:
            return rejected
        serializer = MagicLinkExchangeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data["token"]

        # Unsign here (rather than via parse_magic_link_token) so we can
        # distinguish an *expired* token (410) from an *invalid* one (400).
        try:
            raw = TimestampSigner(salt=SIGNER_SALT).unsign(token, max_age=TOKEN_MAX_AGE_SECONDS)
        except SignatureExpired:
            return Response({"detail": "token_expired"}, status=status.HTTP_410_GONE)
        except BadSignature:
            return Response({"detail": "token_invalid"}, status=status.HTTP_400_BAD_REQUEST)

        # Payload is "<user_id>:<nonce>" (cf. magic_link_token).
        user_id_str, sep, nonce = raw.partition(":")
        try:
            user_id = int(user_id_str)
        except (ValueError, TypeError):
            return Response({"detail": "token_invalid"}, status=status.HTTP_400_BAD_REQUEST)
        if not sep or not nonce:
            return Response({"detail": "token_invalid"}, status=status.HTTP_400_BAD_REQUEST)

        # exchange consumes the single-use nonce — a replay returns None (400).
        user = exchange_magic_link(user_id, nonce)
        if user is None:
            return Response({"detail": "token_invalid"}, status=status.HTTP_400_BAD_REQUEST)

        return refresh_cookie.deliver_tokens(request, Response(_jwt_pair(user)))


class IsAuthenticatedOrCookieMode(BasePermission):
    """Logout: a Bearer token, or the cookie-mode opt-in (origin checked in the view).

    Lets the SPA sign out even when its in-memory access token is gone (expired,
    tab reloaded): blacklisting the caller's own refresh cookie needs nothing else.
    """

    def has_permission(self, request, view):
        if refresh_cookie.wants_cookie(request):
            return True
        return bool(request.user and request.user.is_authenticated)


class LogoutView(APIView):
    """POST /api/v1/auth/logout/ — revoke a refresh token.

    Body mode (mobile app): the caller authenticates with their access token
    and posts the refresh they want revoked. On success (204), that refresh —
    and any rotation descendants produced by /auth/token/refresh/ — can no
    longer be used. The access token itself is short-lived and not
    invalidated here; it naturally expires within ACCESS_TOKEN_LIFETIME.

    Ownership is verified before blacklisting: a holder of someone else's
    refresh string cannot use this endpoint to revoke it. We respond with
    a generic invalid_token to avoid signalling whether the token exists
    or only doesn't belong to the caller.

    Cookie mode (``X-Auth-Mode: cookie``, web SPA): no Bearer needed. The
    body token, or else the HttpOnly refresh cookie, is blacklisted (with
    the same ownership check when a Bearer is present), the cookie is
    always cleared and the answer is always 204 (idempotent).
    """

    permission_classes = [IsAuthenticatedOrCookieMode]
    throttle_classes = [LogoutThrottle]

    @extend_schema(
        request=LogoutSerializer,
        parameters=[AUTH_MODE_PARAMETER],
        auth=[{"jwtAuth": []}, {}],
        responses={
            204: OpenApiResponse(
                description="Refresh token blacklisted (cookie mode: refresh cookie cleared)."
            ),
            400: OpenApiResponse(
                description=(
                    "Body mode: refresh token missing, malformed, expired, already "
                    "blacklisted, or not owned by the caller. code=invalid_token."
                )
            ),
            401: OpenApiResponse(description="Body mode: access token missing or invalid."),
            403: ORIGIN_REJECTED_RESPONSE,
            429: OpenApiResponse(description="Too Many Requests (rate limit hit)."),
        },
    )
    def post(self, request):
        if (rejected := refresh_cookie.cookie_origin_guard(request)) is not None:
            return rejected
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw = serializer.validated_data.get("refresh", "")

        if refresh_cookie.wants_cookie(request):
            # Idempotent: whatever the token's state, the browser ends up signed out.
            raw = raw or refresh_cookie.read_refresh_cookie(request)
            if raw:
                try:
                    token = RefreshToken(raw)
                    user = request.user
                    if not user.is_authenticated or str(token.get("user_id")) == str(user.pk):
                        token.blacklist()
                except TokenError:
                    pass
            response = Response(status=status.HTTP_204_NO_CONTENT)
            refresh_cookie.clear_refresh_cookie(response)
            return response

        if not raw:
            raise drf_serializers.ValidationError(
                {"refresh": [_("This field is required.")]}, code="required"
            )

        try:
            token = RefreshToken(raw)
        except TokenError:
            raise drf_serializers.ValidationError(
                {"detail": _("Invalid or expired refresh token.")},
                code="invalid_token",
            )

        # Ownership check: the refresh must belong to the authenticated user.
        # SimpleJWT stores user_id under USER_ID_CLAIM (default 'user_id').
        if str(token.get("user_id")) != str(request.user.pk):
            raise drf_serializers.ValidationError(
                {"detail": _("Invalid or expired refresh token.")},
                code="invalid_token",
            )

        try:
            token.blacklist()
        except TokenError:
            # Already blacklisted, or any token-state error.
            raise drf_serializers.ValidationError(
                {"detail": _("Invalid or expired refresh token.")},
                code="invalid_token",
            )

        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    summary="Refresh a token",
    description=(
        "Generates a new access token from a refresh token and rotates the refresh "
        "token (the presented one is blacklisted). Body mode: `{refresh}` in, "
        "`{access, refresh}` out. Cookie mode (`X-Auth-Mode: cookie`): the body "
        "`refresh` is optional; when absent the HttpOnly cookie is used (401 "
        "`refresh_cookie_missing` if there is none); the rotated token is set in "
        "the cookie and omitted from the body. A body token sent in cookie mode "
        "is the one-time migration path for SPAs that still hold one in storage."
    ),
    request=TokenRefreshSerializer,
    parameters=[AUTH_MODE_PARAMETER],
    responses={
        200: TokenRefreshSerializer,
        400: OpenApiResponse(description="Body mode without `refresh`."),
        401: OpenApiResponse(
            description=(
                "Refresh token invalid, expired or blacklisted (code=token_not_valid), "
                "or no refresh cookie in cookie mode (code=refresh_cookie_missing)."
            )
        ),
        403: ORIGIN_REJECTED_RESPONSE,
    },
)
class CookieAwareTokenRefreshView(TokenRefreshView):
    """POST /api/v1/auth/token/refresh/ — simplejwt's refresh + cookie mode."""

    def post(self, request, *args, **kwargs):
        if not refresh_cookie.wants_cookie(request):
            return super().post(request, *args, **kwargs)

        if (rejected := refresh_cookie.cookie_origin_guard(request)) is not None:
            return rejected
        body_token = request.data.get("refresh") if hasattr(request.data, "get") else None
        token = body_token or refresh_cookie.read_refresh_cookie(request)
        if not token:
            return Response(
                {
                    "code": "refresh_cookie_missing",
                    "detail": _("No refresh cookie: sign in again."),
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )
        serializer = SimpleJWTRefreshSerializer(data={"refresh": token})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise InvalidToken(exc.args[0]) from exc
        return refresh_cookie.deliver_tokens(
            request, Response(serializer.validated_data, status=status.HTTP_200_OK)
        )

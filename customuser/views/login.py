"""Login + session lifecycle: verified JWT obtain, logout (refresh revoke),
and the passwordless magic-link request/exchange pair."""

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from tools.throttling import LoginThrottle, LogoutThrottle, MagicLinkRequestThrottle

from ..serializers import (
    LogoutSerializer,
    MagicLinkExchangeRequestSerializer,
    MagicLinkExchangeResponseSerializer,
    MagicLinkRequestSerializer,
    VerifiedTokenObtainPairSerializer,
)
from ._helpers import _jwt_pair


@extend_schema(
    responses={
        200: OpenApiResponse(
            response=inline_serializer(
                name="TokenObtainPairResponse",
                fields={
                    "access": drf_serializers.CharField(),
                    "refresh": drf_serializers.CharField(),
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


class MagicLinkRequestView(APIView):
    """POST /api/v1/auth/magic-link/request/ — public: email a sign-in link.

    Anti-leak: ALWAYS returns 200 with the same body, whether the email
    matches an active, email-confirmed User or not. If it does, an email
    with a 15-minute signed magic-link is dispatched (frontend URL:
    {FRONTEND_URL}/auth/magic-link/{token}, no trailing slash). If it
    doesn't, the call is a silent no-op — the response is byte-for-byte
    identical (no user enumeration, timing-insensitive wording).

    Rate-limited to 5 requests per hour per IP.
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
                    "Always 200 with {\"detail\": \"ok\"}. If the email matches "
                    "an active, email-confirmed account, a sign-in link has been "
                    "dispatched; otherwise the response is identical (anti-leak)."
                ),
            ),
            400: OpenApiResponse(description="Body validation error (malformed email)."),
            429: OpenApiResponse(description="Too Many Requests (rate limit hit)."),
        },
    )
    def post(self, request):
        from customuser.magic_link import request_magic_link

        serializer = MagicLinkRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
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
        responses={
            200: OpenApiResponse(
                response=MagicLinkExchangeResponseSerializer,
                description="Token accepted. Returns the JWT pair {access, refresh}.",
            ),
            400: OpenApiResponse(
                description=(
                    "Token invalid/tampered, or the user is no longer eligible "
                    "(inactive / email-unconfirmed / deleted). "
                    "Body: {\"detail\": \"token_invalid\"}."
                )
            ),
            410: OpenApiResponse(
                description="Token expired. Body: {\"detail\": \"token_expired\"}.",
            ),
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

        serializer = MagicLinkExchangeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data["token"]

        # Unsign here (rather than via parse_magic_link_token) so we can
        # distinguish an *expired* token (410) from an *invalid* one (400).
        try:
            raw = TimestampSigner(salt=SIGNER_SALT).unsign(
                token, max_age=TOKEN_MAX_AGE_SECONDS
            )
        except SignatureExpired:
            return Response(
                {"detail": "token_expired"}, status=status.HTTP_410_GONE
            )
        except BadSignature:
            return Response(
                {"detail": "token_invalid"}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user_id = int(raw)
        except (ValueError, TypeError):
            return Response(
                {"detail": "token_invalid"}, status=status.HTTP_400_BAD_REQUEST
            )

        user = exchange_magic_link(user_id)
        if user is None:
            return Response(
                {"detail": "token_invalid"}, status=status.HTTP_400_BAD_REQUEST
            )

        return Response(_jwt_pair(user))


class LogoutView(APIView):
    """POST /api/v1/auth/logout/ — revoke a refresh token.

    The caller authenticates with their access token and posts the refresh
    they want revoked. On success (204), that refresh — and any rotation
    descendants produced by /auth/token/refresh/ — can no longer be used.
    The access token itself is short-lived and not invalidated here; it
    naturally expires within ACCESS_TOKEN_LIFETIME.

    Ownership is verified before blacklisting: a holder of someone else's
    refresh string cannot use this endpoint to revoke it. We respond with
    a generic invalid_token to avoid signalling whether the token exists
    or only doesn't belong to the caller.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [LogoutThrottle]

    @extend_schema(
        request=LogoutSerializer,
        responses={
            204: OpenApiResponse(description="Refresh token blacklisted."),
            400: OpenApiResponse(
                description=(
                    "Refresh token missing, malformed, expired, already blacklisted, "
                    "or not owned by the caller. code=invalid_token."
                )
            ),
            401: OpenApiResponse(description="Access token missing or invalid."),
            429: OpenApiResponse(description="Too Many Requests (rate limit hit)."),
        },
    )
    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw = serializer.validated_data["refresh"]

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

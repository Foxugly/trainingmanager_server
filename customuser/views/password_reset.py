"""Public password-reset flow: request a reset link + confirm the new password
(Django default_token_generator + custom views, mirroring QuizOnline)."""

import logging

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from tools.exceptions import CaptchaFailed
from tools.throttling import PasswordResetThrottle
from tools.turnstile import get_remote_ip, verify_turnstile_token

from ..models import CustomUser
from ..serializers import (
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
)
from ._helpers import _jwt_pair, _user_payload

logger = logging.getLogger(__name__)


class PasswordResetRequestView(APIView):
    """POST /api/v1/auth/password/reset/ — public: request a password reset.

    Anti-leak: ALWAYS returns 200 with the same body, whether the email
    matches a User or not. If it does, an email with a reset link is
    dispatched (frontend URL: {FRONTEND_URL}/auth/reset-password/{key},
    no trailing slash). If it doesn't, the call is a silent no-op.

    Rate-limited to 3 requests per hour per IP. Turnstile required.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetThrottle]

    @extend_schema(
        request=PasswordResetRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="PasswordResetRequestResponse",
                    fields={
                        "detail": drf_serializers.CharField(),
                        "code": drf_serializers.ChoiceField(choices=["password_reset_processed"]),
                    },
                ),
                description=(
                    "Always 200. Returns {detail, code: 'password_reset_processed'}. "
                    "If the email matches a registered account, a reset link "
                    "has been dispatched; otherwise the response is identical "
                    "(anti-leak)."
                ),
            ),
            400: OpenApiResponse(
                description=(
                    "Body validation error (malformed email, missing token), "
                    "or Turnstile failure (code=captcha_failed)."
                )
            ),
            429: OpenApiResponse(description="Too Many Requests (rate limit hit)."),
        },
    )
    def post(self, request):
        from django.core.mail import send_mail

        from customuser.email_tokens import make_key
        from customuser.frontend_urls import get_password_reset_url

        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Server-side Turnstile check before any DB query — fail-closed.
        if not verify_turnstile_token(data["turnstile_token"], remote_ip=get_remote_ip(request)):
            raise CaptchaFailed()

        # Anti-leak: don't .get() (would raise DoesNotExist for unknown email);
        # silently no-op when no user matches.
        user = CustomUser.objects.filter(email__iexact=data["email"].lower()).first()
        if user is not None:
            key = make_key(user)
            reset_url = get_password_reset_url(key)
            with translation.override(user.language or "en"):
                subject = f"[TrainingManager] {_('Password reset')}"
                body = (
                    f"{_('Hello')} {user.first_name or user.email},\n\n"
                    f"{_('You (or someone) requested a password reset for your account.')}\n\n"
                    f"{_('To set a new password, click the link below:')}\n"
                    f"{reset_url}\n\n"
                    f"{_('If you did not request this, you can safely ignore this email.')}\n"
                )
                try:
                    send_mail(
                        subject=str(subject),
                        message=str(body),
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[user.email],
                        fail_silently=False,
                    )
                except Exception:
                    logger.exception("Failed to send password reset email to %s", user.email)

        return Response(
            {
                "detail": _("If a matching account exists, a password reset email has been sent."),
                "code": "password_reset_processed",
            }
        )


class PasswordResetConfirmView(APIView):
    """POST /api/v1/auth/password/reset/confirm/ — public: finalize a reset.

    Body: {key, new_password}. The key is the {uid}-{token} string the
    user received in their email. Returns the JWT pair on success
    (auto-login after reset)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        request=PasswordResetConfirmSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="PasswordResetConfirmResponse",
                    fields={
                        "access": drf_serializers.CharField(),
                        "refresh": drf_serializers.CharField(),
                        "user": inline_serializer(
                            name="PasswordResetConfirmUser",
                            fields={
                                "id": drf_serializers.IntegerField(),
                                "email": drf_serializers.EmailField(),
                                "first_name": drf_serializers.CharField(),
                                "last_name": drf_serializers.CharField(),
                                "language": drf_serializers.CharField(),
                            },
                        ),
                    },
                ),
                description=(
                    "Password updated. Returns the JWT pair (access + refresh) "
                    "for auto-login plus a minimal user payload."
                ),
            ),
            400: OpenApiResponse(
                description=(
                    "Token invalid / expired (code=invalid_or_expired_token), "
                    "or new_password rejected by validators (fields.new_password)."
                )
            ),
        },
    )
    def post(self, request):
        from django.contrib.auth.password_validation import validate_password

        from customuser.email_tokens import parse_key, token_is_valid

        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = serializer.validated_data["key"]
        new_password = serializer.validated_data["new_password"]

        # Key shape: "{uid}-{token}". Token itself contains dashes, so parse_key
        # splits on the FIRST dash only.
        parsed = parse_key(key)
        if parsed is None:
            raise drf_serializers.ValidationError(
                {"detail": _("Invalid or expired password reset token.")},
                code="invalid_or_expired_token",
            )
        user, token = parsed
        if not token_is_valid(user, token):
            raise drf_serializers.ValidationError(
                {"detail": _("Invalid or expired password reset token.")},
                code="invalid_or_expired_token",
            )

        # Validate the new password against Django's configured validators
        # WITH the user as context (helps reject username/email-similar
        # passwords).
        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as exc:
            raise drf_serializers.ValidationError(
                {"new_password": list(exc.messages)},
                code="weak_password",
            )

        # Successful reset = user proved control of their email by clicking
        # the reset link → mark email_confirmed (replaces allauth's
        # EmailAddress.verified flag). Saved together with the new password.
        user.set_password(new_password)
        user.email_confirmed = True
        user.save(update_fields=["password", "email_confirmed"])

        # Defence in depth: blacklist every outstanding refresh token of
        # this user so a stolen-but-still-valid refresh in the wild is
        # neutralised by the reset. Tokens issued before
        # token_blacklist was enabled are NOT in OutstandingToken and
        # cannot be revoked retroactively — this is acceptable: the
        # reset itself was the trigger to enable the feature.
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken,
        )

        for token in OutstandingToken.objects.filter(user=user):
            BlacklistedToken.objects.get_or_create(token=token)

        # ACCEPTABLE COMPROMISE (intentional, out of scope to fix here): this
        # reset revokes outstanding refresh JWTs (above) but does NOT invalidate
        # stateless HMAC magic-links already emitted for this user. Those links
        # are self-contained signed tokens with a short ~15-minute TTL, so a
        # link minted just before the reset stays usable until it expires.
        # Closing that window would require a server-side nonce / per-user
        # secret-version stamped into the link and checked on use — deliberately
        # not implemented here. The short TTL bounds the exposure and the reset
        # already neutralises the higher-value refresh tokens.
        return Response({**_jwt_pair(user), "user": _user_payload(user)})

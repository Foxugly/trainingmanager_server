"""Public self-signup: account creation + email confirmation + resend."""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from tools.exceptions import CaptchaFailed
from tools.throttling import RegisterThrottle, ResendEmailThrottle
from tools.turnstile import get_remote_ip, verify_turnstile_token

from ..email_tokens import make_key, parse_key, token_is_valid
from ..frontend_urls import get_email_confirmation_url
from ..models import CustomUser
from ..serializers import (
    EmailConfirmSerializer,
    EmailResendSerializer,
    RegisterSerializer,
)
from ._helpers import _jwt_pair, _user_payload

logger = logging.getLogger(__name__)


def send_confirmation_email(user) -> None:
    """Mail the user a confirmation link (Django default_token_generator + uid,
    mirroring QuizOnline). Best-effort: a delivery failure is logged, not raised
    — the account still exists with email_confirmed=False so /auth/email/resend/
    works rather than leaving a half-registered state."""
    key = make_key(user)
    url = get_email_confirmation_url(key)
    with translation.override(user.language or "en"):
        subject = f"[TrainingManager] {_('Confirm your registration')}"
        body = (
            f"{_('Hello')} {user.first_name or user.email},\n\n"
            f"{_('Welcome! Please confirm your email address to activate your account.')}\n\n"
            f"{_('To confirm, click the link below:')}\n"
            f"{url}\n\n"
            f"{_('If you did not create this account, you can safely ignore this email.')}\n"
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
            logger.exception("Failed to send confirmation email to %s", user.email)


class RegisterView(APIView):
    """POST /api/v1/auth/register/ — public self-signup.

    Creates a CustomUser (is_active=True, email_confirmed=False), then sends a
    confirmation email carrying a Django default_token_generator link. No JWT is
    returned — the caller must confirm their email before obtaining tokens.

    Rate-limited to 5 requests per hour per IP (anti-bot signup).
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [RegisterThrottle]

    @extend_schema(
        request=RegisterSerializer,
        responses={
            201: OpenApiResponse(
                response=inline_serializer(
                    name="RegisterResponse",
                    fields={
                        "detail": drf_serializers.CharField(),
                        "code": drf_serializers.ChoiceField(
                            choices=["registration_pending_verification"]
                        ),
                        "email": drf_serializers.EmailField(),
                    },
                ),
                description=(
                    "Account created. Returns "
                    "{detail, code: 'registration_pending_verification', email}. "
                    "JWT is intentionally NOT returned — the user must confirm their "
                    "email first."
                ),
            ),
            400: OpenApiResponse(
                description=(
                    "Validation error. Field-level codes include "
                    "`email_taken`, password validators."
                )
            ),
        },
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Server-side Turnstile check before any DB write. Fail-closed:
        # any network/auth failure raises CaptchaFailed -> 400.
        if not verify_turnstile_token(data["turnstile_token"], remote_ip=get_remote_ip(request)):
            raise CaptchaFailed()

        user = CustomUser.objects.create_user(
            email=data["email"],
            password=data["password"],
            first_name=data["first_name"],
            last_name=data["last_name"],
            language=data.get("language", "en"),
        )
        # email_confirmed defaults to False on the model; be explicit anyway so
        # the intent is obvious and a changed default can't silently auto-confirm.
        if user.email_confirmed:
            user.email_confirmed = False
            user.save(update_fields=["email_confirmed"])
        send_confirmation_email(user)

        return Response(
            {
                "detail": _(
                    "Account created. Please check your email to confirm your registration."
                ),
                "code": "registration_pending_verification",
                "email": user.email,
            },
            status=status.HTTP_201_CREATED,
        )


class ConfirmEmailView(APIView):
    """POST /api/v1/auth/email/confirm/ — finalize signup with the key
    received by email. Returns JWT tokens for auto-login.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        request=EmailConfirmSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="EmailConfirmResponse",
                    fields={
                        "access": drf_serializers.CharField(),
                        "refresh": drf_serializers.CharField(),
                        "user": inline_serializer(
                            name="EmailConfirmUser",
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
                description="Email verified. Returns {access, refresh, user}.",
            ),
            400: OpenApiResponse(
                description="Token invalid or expired. code=invalid_or_expired_token."
            ),
        },
    )
    def post(self, request):
        serializer = EmailConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = serializer.validated_data["key"]

        # key shape: "<uid>-<token>" (Django default_token_generator).
        parsed = parse_key(key)
        if parsed is None:
            raise drf_serializers.ValidationError(
                {"detail": _("Invalid or expired confirmation token.")},
                code="invalid_or_expired_token",
            )
        user, token = parsed
        if not token_is_valid(user, token):
            raise drf_serializers.ValidationError(
                {"detail": _("Invalid or expired confirmation token.")},
                code="invalid_or_expired_token",
            )

        if not user.email_confirmed:
            user.email_confirmed = True
            user.save(update_fields=["email_confirmed"])

        return Response({**_jwt_pair(user), "user": _user_payload(user)})


class ResendEmailView(APIView):
    """POST /api/v1/auth/email/resend/ — re-send confirmation link.

    Anti-leak: always returns 200 regardless of whether the email exists,
    so an attacker cannot enumerate registered emails. The fact that no
    email is sent for unknown addresses must remain invisible to the
    client.

    Rate-limited to 3 requests per hour per IP (anti-enumeration +
    anti-mail-spam).
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ResendEmailThrottle]

    @extend_schema(
        request=EmailResendSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="EmailResendResponse",
                    fields={
                        "detail": drf_serializers.CharField(),
                        "code": drf_serializers.ChoiceField(choices=["resend_processed"]),
                    },
                ),
                description=(
                    "Always 200. Returns {detail, code: 'resend_processed'}. "
                    "If a matching unverified account exists, a new confirmation "
                    "email has been dispatched; otherwise the response is identical."
                ),
            ),
            400: OpenApiResponse(description="Body validation error (e.g. malformed email)."),
        },
    )
    def post(self, request):
        serializer = EmailResendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].lower()

        # Anti-leak: only (re)send to a known, still-unconfirmed account;
        # silently no-op otherwise. ``.filter().first()`` never raises.
        user = CustomUser.objects.filter(
            email__iexact=email, email_confirmed=False
        ).first()
        if user is not None:
            send_confirmation_email(user)

        return Response(
            {
                "detail": _(
                    "If a matching unverified account exists, a confirmation email has been sent."
                ),
                "code": "resend_processed",
            }
        )

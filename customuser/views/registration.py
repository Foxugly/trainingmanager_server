"""Public self-signup: account creation + email confirmation + resend."""

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

from ..models import CustomUser
from ..serializers import (
    EmailConfirmSerializer,
    EmailResendSerializer,
    RegisterSerializer,
)
from ._helpers import _jwt_pair, _user_payload


class RegisterView(APIView):
    """POST /api/v1/auth/register/ — public self-signup.

    Creates a CustomUser (is_active=True) plus an unverified EmailAddress
    via allauth, then sends a confirmation email. No JWT is returned —
    the caller must verify their email before obtaining tokens.

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
                        "username": drf_serializers.CharField(),
                        "email": drf_serializers.EmailField(),
                    },
                ),
                description=(
                    "Account created. Returns "
                    "{detail, code: 'registration_pending_verification', username, email}. "
                    "JWT is intentionally NOT returned — the user must confirm their "
                    "email first."
                ),
            ),
            400: OpenApiResponse(
                description=(
                    "Validation error. Field-level codes include `username_taken`, "
                    "`email_taken`, password validators."
                )
            ),
        },
    )
    def post(self, request):
        from allauth.account.models import EmailAddress

        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Server-side Turnstile check before any DB write. Fail-closed:
        # any network/auth failure raises CaptchaFailed -> 400.
        if not verify_turnstile_token(data["turnstile_token"], remote_ip=get_remote_ip(request)):
            raise CaptchaFailed()

        user = CustomUser.objects.create_user(
            username=data["username"],
            email=data["email"],
            password=data["password"],
            first_name=data["first_name"],
            last_name=data["last_name"],
            language=data.get("language", "en"),
        )
        address = EmailAddress.objects.create(
            user=user, email=user.email, primary=True, verified=False
        )
        address.send_confirmation(request, signup=True)

        return Response(
            {
                "detail": _(
                    "Account created. Please check your email to confirm your registration."
                ),
                "code": "registration_pending_verification",
                "username": user.username,
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
                                "username": drf_serializers.CharField(),
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
        from allauth.account.models import EmailConfirmation, EmailConfirmationHMAC

        serializer = EmailConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = serializer.validated_data["key"]

        # Try HMAC first (no DB row, default for ACCOUNT_EMAIL_CONFIRMATION_HMAC=True)
        confirmation = EmailConfirmationHMAC.from_key(key)
        if confirmation is None:
            # Legacy fallback for db-stored confirmations.
            try:
                confirmation = EmailConfirmation.objects.get(key=key.lower())
            except EmailConfirmation.DoesNotExist:
                confirmation = None

        if confirmation is None or confirmation.key_expired():
            raise drf_serializers.ValidationError(
                {"detail": _("Invalid or expired confirmation token.")},
                code="invalid_or_expired_token",
            )

        email_address = confirmation.confirm(request)
        if email_address is None:
            # confirm() returns None when EmailAddress no longer exists
            # or was already confirmed in a way that cancelled this key.
            raise drf_serializers.ValidationError(
                {"detail": _("Invalid or expired confirmation token.")},
                code="invalid_or_expired_token",
            )

        user = email_address.user
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
        from allauth.account.models import EmailAddress

        serializer = EmailResendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].lower()

        try:
            address = EmailAddress.objects.get(email__iexact=email, verified=False)
            address.send_confirmation(request)
        except EmailAddress.DoesNotExist:
            # Anti-leak: silently no-op.
            pass

        return Response(
            {
                "detail": _(
                    "If a matching unverified account exists, a confirmation email has been sent."
                ),
                "code": "resend_processed",
            }
        )

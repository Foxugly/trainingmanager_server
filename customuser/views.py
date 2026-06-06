import logging

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpResponse
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from customuser.calendar import build_calendar_for_user
from tools.exceptions import CaptchaFailed, OwnsTeams
from tools.throttling import (
    LoginThrottle,
    LogoutThrottle,
    PasswordResetThrottle,
    RegisterThrottle,
    ResendEmailThrottle,
)
from tools.turnstile import get_remote_ip, verify_turnstile_token

from .models import CustomUser
from .serializers import (
    AccountDeleteSerializer,
    CalendarTokenSerializer,
    EmailConfirmSerializer,
    EmailResendSerializer,
    LogoutSerializer,
    MeSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    VerifiedTokenObtainPairSerializer,
)

logger = logging.getLogger(__name__)


class MeView(RetrieveUpdateAPIView):
    """GET/PATCH du profil de l'utilisateur connecté.

    PUT is intentionally disabled to prevent partial bodies from resetting
    unspecified writable fields (first_name, last_name, language) to their
    defaults. Use PATCH for any update.

    `email` is read-only here; changing the email requires admin intervention
    in v1 (a verified change-email flow is deferred to v2).
    """

    serializer_class = MeSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user


class DataExportView(APIView):
    """GET /api/v1/me/export/ — RGPD data portability.

    Returns a single JSON document with all personal data we hold about the
    authenticated caller, as a downloadable attachment. The password hash
    and the live calendar_token secret are NEVER included.

    The export is assembled defensively: each related area is wrapped so a
    missing app/relation or a user without a linked Member yields an empty
    list rather than a 500. Imports are kept local to avoid hard-coupling
    customuser to every other app.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="me_export_retrieve",
        request=None,
        responses={
            200: OpenApiResponse(
                description=(
                    "A downloadable JSON document (Content-Disposition: "
                    "attachment) with all personal data of the caller: "
                    "profile, member_profile, team_memberships, owned/managed "
                    "teams, performances, rsvps, roti_scores, athlete-visible "
                    "notes_about_me, messages_authored, and uploaded_attachment "
                    "metadata. The password hash and calendar_token are never "
                    "included."
                ),
            ),
            401: OpenApiResponse(description="Access token missing or invalid."),
        },
    )
    def get(self, request):
        user = request.user
        data = self._build_export(user)
        response = Response(data)
        response["Content-Disposition"] = (
            f'attachment; filename="trainingmanager-export-{user.username}.json"'
        )
        return response

    @staticmethod
    def _iso(value):
        return value.isoformat() if value is not None else None

    def _build_export(self, user):
        member = getattr(user, "member_profile", None)

        export = {
            "profile": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "language": user.language,
                "date_joined": self._iso(user.date_joined),
                "last_login": self._iso(user.last_login),
                "weekly_recap_opt_in": user.weekly_recap_opt_in,
            },
            "member_profile": None,
            "team_memberships": [],
            "owned_teams": [],
            "managed_teams": [],
            "performances": [],
            "rsvps": [],
            "roti_scores": [],
            "notes_about_me": [],
            "messages_authored": [],
            "uploaded_attachments": [],
        }

        if member is not None:
            export["member_profile"] = {
                "firstname": member.firstname,
                "lastname": member.lastname,
                "email": member.email,
                "phonenumber": member.phonenumber,
            }

        export["team_memberships"] = self._team_memberships(member)
        export["owned_teams"] = self._owned_teams(user)
        export["managed_teams"] = self._managed_teams(user)
        export["performances"] = self._performances(member)
        export["rsvps"] = self._rsvps(member)
        export["roti_scores"] = self._roti_scores(member)
        export["notes_about_me"] = self._notes_about_me(member)
        export["messages_authored"] = self._messages_authored(user)
        export["uploaded_attachments"] = self._uploaded_attachments(user)

        return export

    def _team_memberships(self, member):
        if member is None:
            return []
        try:
            return [
                {
                    "team": m.team.name,
                    "joined_at": self._iso(m.joined_at),
                    "left_at": self._iso(m.left_at),
                }
                for m in member.memberships.select_related("team").all()
            ]
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("export: team_memberships failed")
            return []

    def _owned_teams(self, user):
        try:
            return [{"id": t.id, "name": t.name} for t in user.owned_teams.all()]
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("export: owned_teams failed")
            return []

    def _managed_teams(self, user):
        try:
            return [{"id": t.id, "name": t.name} for t in user.managed_teams.all()]
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("export: managed_teams failed")
            return []

    def _performances(self, member):
        if member is None:
            return []
        try:
            return [
                {
                    "label": p.label,
                    "value": str(p.value),
                    "unit": p.unit,
                    "recorded_on": self._iso(p.recorded_on),
                    "notes": p.notes,
                }
                for p in member.performances.all()
            ]
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("export: performances failed")
            return []

    def _rsvps(self, member):
        if member is None:
            return []
        try:
            return [
                {
                    "event_id": r.event_id,
                    "event_name": r.event.name,
                    "status": r.status,
                }
                for r in member.rsvps.select_related("event").all()
            ]
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("export: rsvps failed")
            return []

    def _roti_scores(self, member):
        if member is None:
            return []
        try:
            return [
                {"event_id": r.event_id, "score": r.score}
                for r in member.rotis.all()
            ]
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("export: roti_scores failed")
            return []

    def _notes_about_me(self, member):
        if member is None:
            return []
        try:
            # Mirror the note app's athlete visibility rule: an athlete may
            # only read a note that is shared with them AND still active.
            return [
                {
                    "team": n.team.name,
                    "content": n.content,
                    "created_at": self._iso(n.created_at),
                }
                for n in member.notes.select_related("team").filter(
                    visible_to_athlete=True, is_active=True
                )
            ]
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("export: notes_about_me failed")
            return []

    def _messages_authored(self, user):
        try:
            return [
                {
                    "topic_title": m.topic.title,
                    "content": m.content,
                    "created_at": self._iso(m.created_at),
                }
                for m in user.topic_messages_authored.select_related("topic").all()
            ]
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("export: messages_authored failed")
            return []

    def _uploaded_attachments(self, user):
        try:
            return [
                {
                    "filename": a.filename,
                    "content_type_mime": a.content_type_mime,
                    "size_bytes": a.size_bytes,
                    "created_at": self._iso(a.created_at),
                }
                for a in user.uploaded_attachments.all()
            ]
        except Exception:  # pragma: no cover - defensive guard
            logger.exception("export: uploaded_attachments failed")
            return []


class CalendarFeedView(APIView):
    """GET /api/v1/calendar/<token>.ics — public iCal subscription feed.

    The opaque ``token`` IS the authentication: the user pastes the full URL
    into their calendar client (Google/Apple/Outlook/Thunderbird/Proton/
    Nextcloud), which polls it periodically. We look the user up by
    ``calendar_token`` (404 for any unknown/rotated token) and return a
    valid ``text/calendar`` VCALENDAR of their teams' sessions.

    Excluded from the OpenAPI schema: it returns a file, not JSON, so it
    has no place in the typed frontend client.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(exclude=True)
    def get(self, request, token):
        user = CustomUser.objects.filter(calendar_token=token).first()
        if user is None:
            raise Http404("No calendar for this token.")

        content = build_calendar_for_user(user)
        response = HttpResponse(content, content_type="text/calendar; charset=utf-8")
        response["Content-Disposition"] = 'inline; filename="trainingmanager.ics"'
        return response


class CalendarTokenRotateView(APIView):
    """POST /api/v1/me/calendar-token/rotate/ — rotate the caller's token.

    Generates a brand-new ``calendar_token`` for the authenticated user,
    which immediately invalidates the previous .ics subscription URL, and
    returns the new token so the SPA can rebuild the URL.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="me_calendar_token_rotate",
        request=None,
        responses={
            200: OpenApiResponse(
                response=CalendarTokenSerializer,
                description=(
                    "Token rotated. Returns {calendar_token}. The previous "
                    ".ics subscription URL is now invalid."
                ),
            ),
            401: OpenApiResponse(description="Access token missing or invalid."),
        },
    )
    def post(self, request):
        new_token = request.user.rotate_calendar_token()
        return Response(CalendarTokenSerializer({"calendar_token": new_token}).data)


def _jwt_pair(user):
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def _user_payload(user):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "language": user.language,
    }


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
                description=(
                    "Account created. Returns "
                    "{detail, code: 'registration_pending_verification', username, email}. "
                    "JWT is intentionally NOT returned — the user must confirm their "
                    "email first."
                )
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
            200: OpenApiResponse(description="Email verified. Returns {access, refresh, user}."),
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
                description=(
                    "Always 200. Returns {detail, code: 'resend_processed'}. "
                    "If a matching unverified account exists, a new confirmation "
                    "email has been dispatched; otherwise the response is identical."
                )
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


# =====================================================================
# Password reset flow (allauth token generator + custom view)
# =====================================================================


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
        from allauth.account.forms import default_token_generator
        from allauth.account.utils import user_pk_to_url_str
        from django.core.mail import send_mail

        from customuser.adapter import FrontendAccountAdapter

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
            uid = user_pk_to_url_str(user)
            token = default_token_generator.make_token(user)
            key = f"{uid}-{token}"
            reset_url = FrontendAccountAdapter.get_password_reset_url(key)
            with translation.override(user.language or "en"):
                subject = f"[TrainingManager] {_('Password reset')}"
                body = (
                    f"{_('Hello')} {user.first_name or user.username},\n\n"
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
                                "username": drf_serializers.CharField(),
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
        from allauth.account.forms import default_token_generator
        from allauth.account.utils import url_str_to_user_pk
        from django.contrib.auth.password_validation import validate_password

        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = serializer.validated_data["key"]
        new_password = serializer.validated_data["new_password"]

        # Key shape: "{uid}-{token}". Token itself contains dashes, so split
        # on the FIRST dash only.
        if "-" not in key:
            raise drf_serializers.ValidationError(
                {"detail": _("Invalid or expired password reset token.")},
                code="invalid_or_expired_token",
            )
        uid, token = key.split("-", 1)
        try:
            user_pk = url_str_to_user_pk(uid)
            user = CustomUser.objects.get(pk=user_pk)
        except (ValueError, CustomUser.DoesNotExist):
            raise drf_serializers.ValidationError(
                {"detail": _("Invalid or expired password reset token.")},
                code="invalid_or_expired_token",
            )

        if not default_token_generator.check_token(user, token):
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

        user.set_password(new_password)
        user.save(update_fields=["password"])

        # Successful reset = user proved control of their email by clicking
        # the reset link → mark the EmailAddress verified. allauth's
        # EmailAwarePasswordResetTokenGenerator side-effects an unverified
        # EmailAddress when computing hashes, so we both upgrade-or-create
        # the row and flag it primary+verified.
        from allauth.account.models import EmailAddress

        addr, _created = EmailAddress.objects.get_or_create(
            user=user,
            email=user.email,
            defaults={"primary": True, "verified": True},
        )
        if not addr.verified or not addr.primary:
            addr.verified = True
            addr.primary = True
            addr.save(update_fields=["verified", "primary"])

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

        return Response({**_jwt_pair(user), "user": _user_payload(user)})


class PasswordChangeView(APIView):
    """POST /api/v1/auth/password/change/ — authenticated: change own password.

    Body: {current_password, new_password}. The caller must prove they
    still know `current_password`; `new_password` is validated against
    Django's configured password validators (with the user as context) and
    must differ from the current one. On success the password is updated
    and a localized {detail} body is returned — NO tokens are issued. The
    short-lived access token (and any outstanding refresh) remain valid
    until they expire; this keeps the flow minimal for a user who is
    already authenticated and merely rotating their password.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=PasswordChangeSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="PasswordChangeResponse",
                    fields={"detail": drf_serializers.CharField()},
                ),
                description="Password updated. Returns {detail}. No tokens are issued.",
            ),
            400: OpenApiResponse(
                description=(
                    "current_password wrong (code=current_password_invalid), "
                    "new_password equal to current (code=password_unchanged), "
                    "or new_password rejected by validators (fields.new_password)."
                )
            ),
            401: OpenApiResponse(description="Access token missing or invalid."),
        },
    )
    def post(self, request):
        serializer = PasswordChangeSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        current_password = serializer.validated_data["current_password"]
        new_password = serializer.validated_data["new_password"]
        user = request.user

        if not user.check_password(current_password):
            raise drf_serializers.ValidationError(
                {"detail": _("Current password is incorrect.")},
                code="current_password_invalid",
            )

        if new_password == current_password:
            raise drf_serializers.ValidationError(
                {"detail": _("The new password must be different from the current one.")},
                code="password_unchanged",
            )

        user.set_password(new_password)
        user.save(update_fields=["password"])

        return Response({"detail": _("Password updated.")})


class AccountDeleteView(APIView):
    """POST /api/v1/auth/account/delete/ — authenticated: delete own account.

    Body: {current_password}. The caller must prove they still know their
    password before the account is irreversibly removed. On success the user
    row is deleted (memberships, athlete links, join requests, etc. cascade
    per their FK on_delete) and 204 No Content is returned.

    Safety guard: a user who still OWNS one or more teams (Team.owner ==
    user) is REFUSED with 409 (code owns_teams). Team.owner is
    on_delete=PROTECT, so the DB would raise ProtectedError on delete —
    we surface a clean, localized error instead and never cascade-delete
    teams. The guard intentionally counts ALL owned teams, not just
    is_active ones: a soft-deleted (is_active=False) team still references
    the user via the protected FK and would block the delete just the same.
    The user must transfer ownership (or hard-delete those teams) first.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="auth_account_delete_create",
        request=AccountDeleteSerializer,
        responses={
            204: OpenApiResponse(description="Account deleted. No content."),
            400: OpenApiResponse(
                description=(
                    "current_password missing (fields.current_password) or "
                    "wrong (code=current_password_invalid)."
                )
            ),
            401: OpenApiResponse(description="Access token missing or invalid."),
            409: OpenApiResponse(
                description=(
                    "The user still owns one or more teams (code=owns_teams). "
                    "Transfer ownership of those teams first; the account is "
                    "NOT deleted."
                )
            ),
        },
    )
    def post(self, request):
        serializer = AccountDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        current_password = serializer.validated_data["current_password"]
        user = request.user

        if not user.check_password(current_password):
            raise drf_serializers.ValidationError(
                {"detail": _("Current password is incorrect.")},
                code="current_password_invalid",
            )

        # Safety guard: refuse while the user still owns ANY team. We count
        # all owned teams (not just is_active=True) because Team.owner is
        # on_delete=PROTECT — even a soft-deleted team would raise
        # ProtectedError and turn the delete into a 500. Refuse cleanly with
        # 409 instead; never cascade-delete teams.
        if user.owned_teams.exists():
            raise OwnsTeams()

        # Audit BEFORE delete: the actor FK SET_NULLs on the cascade but the
        # actor_label snapshot survives. Best-effort; never blocks the delete.
        from audit.models import AuditAction
        from audit.services import record

        record(
            AuditAction.ACCOUNT_DELETED,
            actor=user,
            team=None,
            target_repr=f"User #{user.id} ({user.username})",
            request=request,
        )

        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


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

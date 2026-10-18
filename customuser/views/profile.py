"""Authenticated account self-service: profile read/update, RGPD export,
iCal calendar feed + token rotation, verified email change, password change,
and account deletion."""

from django.db import transaction
from django.http import Http404, HttpResponse
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from customuser.calendar import build_calendar_for_user
from member.models import Member
from tools.exceptions import OwnsTeams
from tools.throttling import (
    AccountDeleteThrottle,
    ChangeEmailRequestThrottle,
    PasswordChangeThrottle,
)

from .. import refresh_cookie
from ..models import CustomUser
from ..refresh_cookie import AUTH_MODE_PARAMETER, ORIGIN_REJECTED_RESPONSE, REFRESH_COOKIE_NOTE
from ..serializers import (
    AccountDeleteSerializer,
    CalendarTokenSerializer,
    EmailChangeConfirmSerializer,
    EmailChangeRequestSerializer,
    MeSerializer,
    PasswordChangeSerializer,
)
from ._helpers import _jwt_pair


class _EmailTaken(Exception):
    """Internal sentinel: the target email was claimed between request and
    confirm. Raised inside the email-change atomic block to roll it back and
    surface a 409 (kept private to this module)."""


class MeView(RetrieveUpdateAPIView):
    """GET/PATCH du profil de l'utilisateur connecté.

    PUT is intentionally disabled to prevent partial bodies from resetting
    unspecified writable fields (first_name, last_name, language) to their
    defaults. Use PATCH for any update.

    `email` is read-only here: it is changed through the dedicated verified
    change-email flow (EmailChangeRequestView → EmailChangeConfirmView), which
    re-verifies the new address before swapping the primary EmailAddress — it is
    never mutated directly via this endpoint.
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
        from ..export import build_export

        user = request.user
        data = build_export(user)
        response = Response(data)
        response["Content-Disposition"] = (
            f'attachment; filename="trainingmanager-export-{user.email}.json"'
        )
        return response


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


class PasswordChangeView(APIView):
    """POST /api/v1/auth/password/change/ — authenticated: change own password.

    Body: {current_password, new_password}. The caller must prove they
    still know `current_password`; `new_password` is validated against
    Django's configured password validators (with the user as context) and
    must differ from the current one. On success the password is updated,
    every outstanding refresh token of the user is blacklisted (other
    sessions must sign in again) and a fresh {access, refresh} pair is
    returned next to the localized {detail} so the calling session carries
    on. Already-issued access tokens stay valid until their short expiry.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [PasswordChangeThrottle]

    @extend_schema(
        request=PasswordChangeSerializer,
        parameters=[AUTH_MODE_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="PasswordChangeResponse",
                    fields={
                        "detail": drf_serializers.CharField(),
                        "access": drf_serializers.CharField(),
                        "refresh": drf_serializers.CharField(
                            required=False,
                            help_text="Fresh refresh token." + REFRESH_COOKIE_NOTE,
                        ),
                    },
                ),
                description=(
                    "Password updated. Returns {detail, access, refresh}: every "
                    "previously issued refresh token is revoked, so the client must "
                    "store this new pair."
                ),
            ),
            400: OpenApiResponse(
                description=(
                    "current_password wrong (code=current_password_invalid), "
                    "new_password equal to current (code=password_unchanged), "
                    "or new_password rejected by validators (fields.new_password)."
                )
            ),
            401: OpenApiResponse(description="Access token missing or invalid."),
            403: ORIGIN_REJECTED_RESPONSE,
        },
    )
    def post(self, request):
        if (rejected := refresh_cookie.cookie_origin_guard(request)) is not None:
            return rejected
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
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

        # Revoke every outstanding refresh token (other devices / a leaked
        # refresh), then hand the calling session a fresh pair.
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken,
        )

        for token in OutstandingToken.objects.filter(user=user):
            BlacklistedToken.objects.get_or_create(token=token)

        return refresh_cookie.deliver_tokens(
            request, Response({"detail": _("Password updated."), **_jwt_pair(user)})
        )


class EmailChangeRequestView(APIView):
    """POST /api/v1/me/email/change/ — authenticated: request an email change.

    Body: {new_email}. Sends a signed confirmation link to the NEW address; the
    change only takes effect once that link is confirmed (deferred-to-v2 flow,
    now implemented). Rate-limited per user."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ChangeEmailRequestThrottle]

    @extend_schema(
        request=EmailChangeRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="EmailChangeRequestResponse",
                    fields={
                        "detail": drf_serializers.CharField(),
                        "code": drf_serializers.ChoiceField(choices=["email_change_requested"]),
                    },
                ),
                description="A confirmation link was sent to the new address.",
            ),
            400: OpenApiResponse(
                description="new_email equals current (email_unchanged) or taken (email_taken)."
            ),
            401: OpenApiResponse(description="Access token missing or invalid."),
            429: OpenApiResponse(description="Too Many Requests (rate limit)."),
        },
    )
    def post(self, request):
        from customuser.email_change import send_email_change_email

        serializer = EmailChangeRequestSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        send_email_change_email(request.user, serializer.validated_data["new_email"])
        return Response(
            {
                "detail": _("A confirmation link has been sent to the new email address."),
                "code": "email_change_requested",
            }
        )


class EmailChangeConfirmView(APIView):
    """POST /api/v1/auth/email/change/confirm/ — public: finalize an email change.

    Body: {token}. Verifies the signed token, re-checks the new email is still
    free, then swaps the account's primary email. Public so the link works even
    if the SPA session has expired."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        request=EmailChangeConfirmSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="EmailChangeConfirmResponse",
                    fields={
                        "detail": drf_serializers.CharField(),
                        "code": drf_serializers.ChoiceField(choices=["email_changed"]),
                    },
                ),
                description="Email changed. The SPA should refetch /me/.",
            ),
            400: OpenApiResponse(description="Token invalid (code=invalid_or_expired_token)."),
            409: OpenApiResponse(description="email_taken (claimed since the request)."),
            410: OpenApiResponse(description="token_expired."),
        },
    )
    def post(self, request):
        from django.core.signing import SignatureExpired

        from customuser.email_change import parse_email_change_token

        serializer = EmailChangeConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data["token"]

        try:
            parsed = parse_email_change_token(token)
        except SignatureExpired:
            return Response(
                {"code": "token_expired", "detail": _("This link has expired.")},
                status=status.HTTP_410_GONE,
            )
        if parsed is None:
            return Response(
                {"code": "invalid_or_expired_token", "detail": _("Invalid or expired link.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from django.db import IntegrityError

        user_id, new_email = parsed
        user = CustomUser.objects.filter(pk=user_id).first()
        if user is None:
            return Response(
                {"code": "invalid_or_expired_token", "detail": _("Invalid or expired link.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The uniqueness check + the write run in ONE atomic block so two
        # confirmations racing toward the same address can't both pass the
        # check and create duplicate accounts; the unique-email constraint on
        # CustomUser.email is the backstop (IntegrityError -> clean 409).
        try:
            with transaction.atomic():
                taken = (
                    CustomUser.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists()
                )
                if taken:
                    raise _EmailTaken()
                # Swap the user's email and (re)assert email_confirmed: clicking
                # the link sent to the NEW address proves ownership of it.
                user.email = new_email
                user.email_confirmed = True
                user.save(update_fields=["email", "email_confirmed"])
        except (_EmailTaken, IntegrityError):
            return Response(
                {"code": "email_taken", "detail": _("This email is already in use.")},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            {"detail": _("Your email address has been updated."), "code": "email_changed"}
        )


class AccountDeleteView(APIView):
    """POST /api/v1/auth/account/delete/ — authenticated: delete own account.

    Body: {current_password}. The caller must prove they still know their
    password before the account is irreversibly removed. On success the user
    row is deleted (memberships, athlete links, join requests, etc. cascade
    per their FK on_delete), the linked Member record is anonymized (RGPD:
    names, email and phone erased, team history kept) and 204 No Content is
    returned.

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
    throttle_classes = [AccountDeleteThrottle]

    @extend_schema(
        operation_id="auth_account_delete_create",
        request=AccountDeleteSerializer,
        parameters=[AUTH_MODE_PARAMETER],
        responses={
            204: OpenApiResponse(
                description=(
                    "Account deleted. No content. In cookie mode the refresh cookie is cleared."
                )
            ),
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
        from audit.services import audit_event

        audit_event(
            request,
            AuditAction.ACCOUNT_DELETED,
            team=None,
            target_repr=f"User #{user.id}",
        )

        with transaction.atomic():
            # RGPD: the athlete record linked to the account would otherwise
            # survive (Member.user is SET_NULL) with name, email and phone.
            # Anonymize it; team history stays tied to the anonymized row.
            member = Member.objects.filter(user=user).first()
            if member is not None:
                member.anonymize()
            user.delete()
        response = Response(status=status.HTTP_204_NO_CONTENT)
        if refresh_cookie.wants_cookie(request):
            refresh_cookie.clear_refresh_cookie(response)
        return response

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from tools.exceptions import EmailNotVerified

from .entitlements import user_quota
from .models import CustomUser


class CustomUserPublicSerializer(serializers.ModelSerializer):
    """Public user payload for nested read contexts.

    Excludes email and other privacy-sensitive fields. Use this whenever
    a user is exposed inside another resource (Team.owner, Member.user,
    TeamInvitation.invited_by, ...).
    """

    class Meta:
        model = CustomUser
        fields = ["id", "first_name", "last_name"]
        read_only_fields = fields


class TeamQuotaStatusSerializer(serializers.Serializer):
    """Computed quota block embedded in /me/. Tells the frontend whether the
    user can create a new team and how many slots they have used / are
    entitled to."""

    used = serializers.IntegerField(help_text="Active teams currently owned by the user.")
    max = serializers.IntegerField(
        help_text="Maximum active teams the user can own (CustomUser.team_quota)."
    )
    can_create = serializers.BooleanField(help_text="True iff used < max.")


class MeSerializer(serializers.ModelSerializer):
    team_quota = serializers.SerializerMethodField()
    member_id = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = [
            "id",
            "email",
            "email_confirmed",
            "first_name",
            "last_name",
            "language",
            "weekly_recap_opt_in",
            "digest_email",
            "is_staff",
            "is_superuser",
            "last_login",
            "date_joined",
            "team_quota",
            "subscription_bypass",
            "calendar_token",
            "member_id",
        ]
        # is_staff and is_superuser are exposed READ-ONLY so the SPA can gate UI
        # affordances (the admin back-office link is superuser-only). They are the
        # user's own flags; server-side permissions still enforce every admin
        # endpoint. read_only prevents privilege escalation via PATCH.
        # subscription_bypass follows the same rule: read-only so the SPA can show
        # an "offered access" badge and hide pricing, never mutable by its owner.
        # email is read-only here: it is changed through the dedicated verified
        # change-email flow, never mutated directly via /me/.
        read_only_fields = [
            "id",
            "email",
            "email_confirmed",
            "is_staff",
            "is_superuser",
            "last_login",
            "date_joined",
            "team_quota",
            "subscription_bypass",
            "calendar_token",
            "member_id",
        ]

    @extend_schema_field(TeamQuotaStatusSerializer)
    def get_team_quota(self, obj):
        used = obj.active_owned_teams_count()
        quota = user_quota(obj)
        return {
            "used": used,
            "max": quota,
            "can_create": used < quota,
        }

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_member_id(self, obj):
        """The id of the Member linked 1:1 to this user, or null if none.

        Lets the SPA know the caller's own athlete member id for self-service
        (e.g. viewing/logging their own performances)."""
        member = getattr(obj, "member_profile", None)
        return member.id if member is not None else None


class CalendarTokenSerializer(serializers.Serializer):
    """Response body of POST /api/v1/me/calendar-token/rotate/.

    Returns the freshly-generated calendar_token so the SPA can rebuild the
    user's .ics subscription URL ({API_BASE}/api/v1/calendar/{token}.ics)."""

    calendar_token = serializers.CharField(
        read_only=True,
        help_text="The new calendar token. The previous .ics URL is now invalid.",
    )


class RegisterSerializer(serializers.Serializer):
    """Public registration payload for POST /api/v1/auth/register/.

    Email-only: there is no username. Uniqueness is enforced against
    CustomUser.email (the unique constraint is the DB backstop).

    `turnstile_token` is required: server-side verification with Cloudflare
    happens in the view (not here, because we need the request to extract
    the remote IP). The serializer just enforces presence."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    # Reuse the (code, label) tuple from settings.LANGUAGES so drf-spectacular
    # unifies this enum with CustomUser.language and avoids a "collision"
    # warning that would force a suffixed name like LanguageFd4Enum.
    language = serializers.ChoiceField(
        choices=settings.LANGUAGES,
        required=False,
        default="en",
    )
    turnstile_token = serializers.CharField(write_only=True)

    def validate_email(self, value):
        normalized = value.lower()
        if CustomUser.objects.filter(email__iexact=normalized).exists():
            raise serializers.ValidationError(
                _("This email is already in use."), code="email_taken"
            )
        return normalized

    def validate_password(self, value):
        validate_password(value)
        return value


class EmailConfirmSerializer(serializers.Serializer):
    """Body for POST /api/v1/auth/email/confirm/."""

    key = serializers.CharField()


class EmailResendSerializer(serializers.Serializer):
    """Body for POST /api/v1/auth/email/resend/."""

    email = serializers.EmailField()


class VerifiedTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Block JWT login when the user's email is not yet confirmed.

    simplejwt authenticates on the USERNAME_FIELD, which is now ``email``
    (no username) — the request field is therefore ``email``. The gate is
    ``user.email_confirmed`` (the boolean that replaced allauth's
    EmailAddress.verified flag).

    Optional `remember` flag (default False) extends the refresh token
    lifetime to settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME_REMEMBER"]
    (30 days) instead of the standard REFRESH_TOKEN_LIFETIME (7 days).
    The access token TTL is unchanged either way.
    """

    remember = serializers.BooleanField(required=False, default=False, write_only=True)

    def validate(self, attrs):
        from rest_framework_simplejwt.tokens import RefreshToken

        # Pop `remember` before super().validate — TokenObtainSerializer
        # only knows about email/password and would otherwise complain.
        remember = attrs.pop("remember", False)
        data = super().validate(attrs)  # raises 401 if creds invalid; sets self.user
        if not getattr(self.user, "email_confirmed", False):
            # Use a dedicated APIException (not ValidationError) so the
            # custom_exception_handler hits the APIException branch and
            # surfaces default_code="email_not_verified" at the top of
            # the response, exactly like ResourceLocked does.
            raise EmailNotVerified()

        if remember:
            # Re-issue the refresh with extended TTL. Access TTL stays at
            # SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"] (untouched).
            refresh = RefreshToken.for_user(self.user)
            extended = settings.SIMPLE_JWT.get("REFRESH_TOKEN_LIFETIME_REMEMBER")
            if extended is not None:
                refresh.set_exp(lifetime=extended)
            data["refresh"] = str(refresh)
            data["access"] = str(refresh.access_token)
        return data


class PasswordResetRequestSerializer(serializers.Serializer):
    """Body of POST /api/v1/auth/password/reset/.

    Anti-leak by design — the view always returns the same 200 payload
    regardless of whether `email` matches a User. Validation here only
    enforces field shapes."""

    email = serializers.EmailField()
    turnstile_token = serializers.CharField(write_only=True)


class PasswordResetConfirmSerializer(serializers.Serializer):
    """Body of POST /api/v1/auth/password/reset/confirm/. The view decodes
    `key` (uid + token) and validates `new_password` against Django's
    password validators with the matched user as context."""

    key = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=8)


class EmailChangeRequestSerializer(serializers.Serializer):
    """Body of POST /api/v1/me/email/change/ (authenticated). Validates that the
    new email is different from the caller's current one and not already taken."""

    new_email = serializers.EmailField()

    def validate_new_email(self, value):
        normalized = value.lower()
        user = getattr(self.context.get("request"), "user", None)
        if user is not None and user.email and user.email.lower() == normalized:
            raise serializers.ValidationError(
                _("This is already your email address."), code="email_unchanged"
            )
        users = CustomUser.objects.filter(email__iexact=normalized)
        if user is not None:
            users = users.exclude(pk=user.pk)
        if users.exists():
            raise serializers.ValidationError(
                _("This email is already in use."), code="email_taken"
            )
        return normalized


class EmailChangeConfirmSerializer(serializers.Serializer):
    """Body of POST /api/v1/auth/email/change/confirm/. The view decodes the
    signed `token` (user pk + new email)."""

    token = serializers.CharField()


class PasswordChangeSerializer(serializers.Serializer):
    """Body of POST /api/v1/auth/password/change/ (authenticated).

    The caller proves they still know `current_password` and supplies a
    `new_password` validated against Django's configured validators with
    the authenticated user as context. The view performs the
    `check_password` comparison and the equality check — the serializer
    only enforces field shapes and the password strength rules."""

    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_new_password(self, value):
        # `user` is injected via the serializer context by the view so that
        # username/email-similarity validators have the right subject.
        validate_password(value, user=self.context.get("request").user)
        return value


class AccountDeleteSerializer(serializers.Serializer):
    """Body of POST /api/v1/auth/account/delete/ (authenticated).

    The caller proves they still know `current_password` before their
    account is irreversibly deleted. The view performs the `check_password`
    comparison and the owned-teams safety guard — the serializer only
    enforces that the confirmation password is present."""

    current_password = serializers.CharField(write_only=True)


class MagicLinkRequestSerializer(serializers.Serializer):
    """Body of POST /api/v1/auth/magic-link/request/.

    Anti-leak by design — the view always returns the same 200 payload
    regardless of whether `email` matches a confirmed user. Validation
    here only enforces the field shape."""

    email = serializers.EmailField()


class MagicLinkExchangeRequestSerializer(serializers.Serializer):
    """Body of POST /api/v1/auth/magic-link/exchange/.

    `token` is the signed string the user received in their magic-link
    email (the SPA POSTs it back from /auth/magic-link/:token)."""

    token = serializers.CharField()


class MagicLinkExchangeResponseSerializer(serializers.Serializer):
    """Success body of POST /api/v1/auth/magic-link/exchange/ — the JWT
    pair for the freshly signed-in user."""

    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)


class LogoutSerializer(serializers.Serializer):
    """Body of POST /api/v1/auth/logout/.

    The caller's access token authenticates the request; the refresh token
    they want to revoke is passed in the body. The view validates ownership
    (refresh.user_id == request.user.id) before blacklisting — without that
    check, a holder of someone else's refresh string could blacklist it
    unilaterally.
    """

    refresh = serializers.CharField(write_only=True)

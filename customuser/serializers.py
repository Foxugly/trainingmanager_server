from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from tools.exceptions import EmailNotVerified

from .models import CustomUser


class CustomUserPublicSerializer(serializers.ModelSerializer):
    """Public user payload for nested read contexts.

    Excludes email and other privacy-sensitive fields. Use this whenever
    a user is exposed inside another resource (Team.owner, Member.user,
    TeamInvitation.invited_by, ...).
    """

    class Meta:
        model = CustomUser
        fields = ["id", "username", "first_name", "last_name"]
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

    class Meta:
        model = CustomUser
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "language",
            "last_login",
            "date_joined",
            "team_quota",
        ]
        read_only_fields = [
            "id",
            "username",
            "email",
            "last_login",
            "date_joined",
            "team_quota",
        ]

    @extend_schema_field(TeamQuotaStatusSerializer)
    def get_team_quota(self, obj):
        used = obj.active_owned_teams_count()
        return {
            "used": used,
            "max": obj.team_quota,
            "can_create": used < obj.team_quota,
        }


class RegisterSerializer(serializers.Serializer):
    """Public registration payload for POST /api/v1/auth/register/.

    Validates uniqueness against both CustomUser AND allauth.EmailAddress
    (allauth allows two unverified entries for the same email otherwise).

    `turnstile_token` is required: server-side verification with Cloudflare
    happens in the view (not here, because we need the request to extract
    the remote IP). The serializer just enforces presence."""

    username = serializers.CharField(max_length=150)
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

    def validate_username(self, value):
        if CustomUser.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError(
                _("This username is already taken."), code="username_taken"
            )
        return value

    def validate_email(self, value):
        from allauth.account.models import EmailAddress

        normalized = value.lower()
        if CustomUser.objects.filter(email__iexact=normalized).exists():
            raise serializers.ValidationError(
                _("This email is already in use."), code="email_taken"
            )
        if EmailAddress.objects.filter(email__iexact=normalized).exists():
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
    """Block JWT login when the user has an unverified primary email.

    Legacy users predating allauth integration have no EmailAddress row;
    they are treated as verified to preserve backwards compatibility (no
    data migration). Once an EmailAddress exists for a user, the
    `verified` flag becomes authoritative.

    Optional `remember` flag (default False) extends the refresh token
    lifetime to settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME_REMEMBER"]
    (30 days) instead of the standard REFRESH_TOKEN_LIFETIME (7 days).
    The access token TTL is unchanged either way.
    """

    remember = serializers.BooleanField(required=False, default=False, write_only=True)

    def validate(self, attrs):
        from allauth.account.models import EmailAddress
        from rest_framework_simplejwt.tokens import RefreshToken

        # Pop `remember` before super().validate — TokenObtainSerializer
        # only knows about username/password and would otherwise complain.
        remember = attrs.pop("remember", False)
        data = super().validate(attrs)  # raises 401 if creds invalid; sets self.user
        addresses = EmailAddress.objects.filter(user=self.user)
        if addresses.exists() and not addresses.filter(verified=True).exists():
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


class LogoutSerializer(serializers.Serializer):
    """Body of POST /api/v1/auth/logout/.

    The caller's access token authenticates the request; the refresh token
    they want to revoke is passed in the body. The view validates ownership
    (refresh.user_id == request.user.id) before blacklisting — without that
    check, a holder of someone else's refresh string could blacklist it
    unilaterally.
    """

    refresh = serializers.CharField(write_only=True)

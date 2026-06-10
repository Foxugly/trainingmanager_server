from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from ..models import (
    Team,
    TeamInvitation,
    TeamJoinRequest,
)


class TeamJoinRequestSerializer(serializers.ModelSerializer):
    """Standard serializer for /join-requests/. Mirrors TeamMembershipSerializer's
    pattern of exposing username/fullname denorms so the manager dashboard can
    render the requester without a separate /users/{id}/ fetch."""

    user_username = serializers.CharField(source="user.username", read_only=True)
    user_fullname = serializers.SerializerMethodField()
    responded_by_username = serializers.CharField(
        source="responded_by.username",
        read_only=True,
        allow_null=True,
        default=None,
    )

    class Meta:
        model = TeamJoinRequest
        fields = [
            "id",
            "user",
            "user_username",
            "user_fullname",
            "team",
            "status",
            "message",
            "response_message",
            "requested_at",
            "responded_at",
            "responded_by",
            "responded_by_username",
        ]
        read_only_fields = [
            "id",
            "user",
            "user_username",
            "user_fullname",
            "requested_at",
            "responded_at",
            "responded_by",
            "responded_by_username",
        ]

    @extend_schema_field(serializers.CharField())
    def get_user_fullname(self, obj) -> str:
        user = obj.user
        parts = [p for p in [user.first_name, user.last_name] if p]
        return " ".join(parts) if parts else user.username


# ---------------------------------------------------------------------
# Magic-action endpoints (/api/v1/join-magic/) — input + response shapes
# ---------------------------------------------------------------------


class TeamJoinRequestMagicActionPostSerializer(serializers.Serializer):
    """Body of POST /api/v1/join-magic/. Token is the HMAC-signed link
    received in the manager email."""

    token = serializers.CharField()


class _MagicActionJoinRequestSerializer(serializers.Serializer):
    """Inline shape of the `join_request` field returned by the magic-action
    endpoints. Mirrors the dict assembled in
    TeamJoinRequestMagicActionView._serialize — keep in sync."""

    id = serializers.IntegerField()
    team_id = serializers.IntegerField()
    team_name = serializers.CharField()
    requester_username = serializers.CharField()
    requester_email = serializers.EmailField()
    message = serializers.CharField(allow_blank=True)
    requested_at = serializers.DateTimeField()
    status = serializers.ChoiceField(choices=["pending", "accepted", "rejected", "cancelled"])
    responded_at = serializers.DateTimeField(allow_null=True)
    responded_by = serializers.CharField(allow_null=True, help_text="username")


class TeamJoinRequestMagicActionResponseSerializer(serializers.Serializer):
    """200 response for both GET (preview) and POST (execute) on
    /api/v1/join-magic/. Identical shape — POST just reflects the
    post-action state in `join_request.status`."""

    join_request = _MagicActionJoinRequestSerializer()
    action_proposed = serializers.ChoiceField(choices=["accept", "reject"])
    would_change_decision = serializers.BooleanField(
        help_text=(
            "True iff the action — if executed — would reverse a previous "
            "manager decision (accepted->rejected or rejected->accepted)."
        )
    )
    can_act = serializers.BooleanField(
        help_text="False when the request was cancelled by the requester."
    )


class JoinMagicErrorSerializer(serializers.Serializer):
    """Common 4xx body for the magic-action endpoints. Shape produced by
    tools.exceptions.custom_exception_handler."""

    code = serializers.ChoiceField(
        choices=[
            "invalid_or_expired_token",
            "token_required",
            "request_cancelled",
        ]
    )
    detail = serializers.CharField()


class JoinMagicCancelledResponseSerializer(TeamJoinRequestMagicActionResponseSerializer):
    """409 body when the requester cancelled the request. Combines the error
    envelope (code + detail) with the regular response payload so the
    frontend can show "the user cancelled" + the snapshot of what was
    being attempted."""

    code = serializers.ChoiceField(choices=["request_cancelled"])
    detail = serializers.CharField()


class CreateJoinRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = TeamJoinRequest
        fields = ["id", "team", "message"]
        read_only_fields = ["id"]

    def validate(self, data):
        user = self.context["request"].user
        team = data["team"]
        member_profile = getattr(user, "member_profile", None)
        if (
            member_profile is not None
            and member_profile.memberships.filter(team=team, left_at__isnull=True).exists()
        ):
            raise serializers.ValidationError(
                {"team": _("You are already a member of this team.")},
                code="already_member",
            )
        if TeamJoinRequest.objects.filter(user=user, team=team, status="pending").exists():
            raise serializers.ValidationError(
                {"team": _("You already have a pending request for this team.")},
                code="pending_request_exists",
            )
        if not team.is_active:
            raise serializers.ValidationError(
                {"team": _("This team is inactive.")},
                code="team_not_active",
            )
        if not team.is_public:
            raise serializers.ValidationError(
                {"team": _("This team is not public.")},
                code="team_not_public",
            )
        return data


class TeamInvitationSerializer(serializers.ModelSerializer):
    """List/detail view for managers. Token is intentionally excluded."""

    class Meta:
        model = TeamInvitation
        fields = [
            "id",
            "team",
            "invited_by",
            "member",
            "email",
            "status",
            "created_at",
            "expires_at",
            "completed_at",
        ]
        read_only_fields = [
            "id",
            "invited_by",
            "member",
            "status",
            "created_at",
            "expires_at",
            "completed_at",
        ]


class CreateInvitationSerializer(serializers.Serializer):
    team = serializers.PrimaryKeyRelatedField(queryset=Team.objects.all())
    email = serializers.EmailField()
    firstname = serializers.CharField(max_length=100)
    lastname = serializers.CharField(max_length=100)
    phonenumber = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")

    def validate_team(self, team):
        user = self.context["request"].user
        if not team.is_managed_by(user):
            raise serializers.ValidationError(
                _("You do not manage this team."),
                code="not_a_manager",
            )
        if not team.is_active:
            raise serializers.ValidationError(
                _("This team is inactive."),
                code="team_not_active",
            )
        return team

    def validate(self, data):
        if TeamInvitation.objects.filter(
            email=data["email"],
            team=data["team"],
            status="pending",
        ).exists():
            raise serializers.ValidationError(
                {"email": _("An invitation is already pending for this email on this team.")},
                code="email_already_invited",
            )
        return data


class ValidateInvitationSerializer(serializers.ModelSerializer):
    team_name = serializers.CharField(source="team.name", read_only=True)

    class Meta:
        model = TeamInvitation
        fields = ["email", "team_name", "status", "expires_at"]
        read_only_fields = fields


class CompleteInvitationSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_username(self, username):
        User = get_user_model()
        # Case-insensitive uniqueness, consistent with RegisterSerializer.
        # A case-sensitive check would let "Bob"/"bob" both register and risks
        # a 500 on the DB-level case-insensitive unique constraint at create.
        if User.objects.filter(username__iexact=username).exists():
            raise serializers.ValidationError(
                _("This username is already taken."),
                code="username_taken",
            )
        return username

    def validate_password(self, password):
        from django.contrib.auth.password_validation import validate_password

        validate_password(password)
        return password

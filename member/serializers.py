from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from customuser.serializers import CustomUserPublicSerializer

from .models import Member

User = get_user_model()


class MemberSerializer(serializers.ModelSerializer):
    fullname = serializers.SerializerMethodField()
    teams = serializers.SerializerMethodField()
    user = CustomUserPublicSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        source="user",
        queryset=User.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Member
        fields = [
            "id",
            "firstname",
            "lastname",
            "fullname",
            "email",
            "phonenumber",
            "teams",
            "user",
            "user_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "fullname", "teams", "created_at", "updated_at"]

    @extend_schema_field(serializers.CharField())
    def get_fullname(self, obj) -> str:
        parts = [p for p in [obj.firstname, obj.lastname] if p]
        return " ".join(parts) if parts else ""

    @extend_schema_field(serializers.ListField(child=serializers.IntegerField()))
    def get_teams(self, obj) -> list[int]:
        """Return active team IDs for this member (left_at IS NULL).

        Filter the prefetched `memberships` in Python (the list view prefetches
        them) rather than `.filter(...)`, which would issue a fresh query per
        member and defeat the prefetch.
        """
        return [m.team_id for m in obj.memberships.all() if m.left_at is None]

    def _requester_may_see_pii(self, instance) -> bool:
        """True if the request user may read this member's email/phonenumber.

        Allowed for a coach (owner/manager) of any team the member belongs to,
        or when the member record IS the requester's own linked member.
        Everyone else (mere athlete teammates) is treated as not entitled to
        the PII even though ``get_queryset`` lets them see the row's non-PII
        identity fields.

        The user's managed-team-id set is resolved once per request (cached on
        the serializer context) instead of a managers query per member when
        serializing a list — mirrors EventSerializer._requester_is_manager.
        """
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        # The member's own linked user always sees their own PII.
        if instance.user_id is not None and instance.user_id == user.pk:
            return True
        managed = self.context.get("_managed_team_ids")
        if managed is None:
            from team.queries import managed_teams

            managed = set(managed_teams(user).values_list("id", flat=True))
            self.context["_managed_team_ids"] = managed
        if not managed:
            return False
        member_team_ids = {m.team_id for m in instance.memberships.all()}
        return bool(managed & member_team_ids)

    def to_representation(self, instance):
        """Serialize, then redact PII for non-manager, non-self requesters.

        Security: an athlete-member of a member's team must never receive that
        teammate's email/phonenumber. Non-PII identity fields
        (id/firstname/lastname/fullname/teams) stay visible so athlete-facing
        features that read member names keep working. Managers/owners and the
        member's own linked user are never redacted.
        """
        data = super().to_representation(instance)
        if not self._requester_may_see_pii(instance):
            if "email" in data:
                data["email"] = None
            if "phonenumber" in data:
                data["phonenumber"] = None
        return data

    def validate_user_id(self, user):
        """Reject upfront if the chosen user already has a Member.

        The DB-level OneToOne unique constraint on Member.user would raise
        IntegrityError -> 500. Catch the conflict here so the client gets a
        clean 400 with the unified error format.
        """
        if user is None:
            return user
        existing = Member.objects.filter(user=user)
        if self.instance is not None:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError(
                _("This user already has a member profile."),
                code="user_already_has_member",
            )
        return user

    def validate(self, data):
        data = super().validate(data)
        user = data.get("user")
        if user is not None and self.instance is not None:
            active_team_ids = set(
                self.instance.memberships.filter(left_at__isnull=True).values_list(
                    "team_id", flat=True
                )
            )
            if active_team_ids:
                user_team_ids = set(user.owned_teams.values_list("pk", flat=True)) | set(
                    user.managed_teams.values_list("pk", flat=True)
                )
                member_profile = getattr(user, "member_profile", None)
                if member_profile is not None:
                    user_team_ids |= set(
                        member_profile.memberships.filter(left_at__isnull=True).values_list(
                            "team_id", flat=True
                        )
                    )
                if user_team_ids.isdisjoint(active_team_ids):
                    raise serializers.ValidationError(
                        {
                            "user_id": _(
                                "The user must already belong to at least one "
                                "of the member's teams."
                            )
                        },
                        code="user_team_mismatch",
                    )
        return data

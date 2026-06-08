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

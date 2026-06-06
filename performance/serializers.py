from rest_framework import serializers

from member.models import Member
from team.models import Team

from .models import Performance


class PerformanceSerializer(serializers.ModelSerializer):
    """Read/write serializer for a single Performance row.

    `team` and `member` are writable PrimaryKeyRelatedFields on create; on
    update they are ignored (a performance never moves to another member or
    team — the view drops them from validated_data in update()). `created_by`
    is set by the view from request.user and is read-only here.
    """

    team = serializers.PrimaryKeyRelatedField(queryset=Team.objects.all())
    member = serializers.PrimaryKeyRelatedField(queryset=Member.objects.all())
    is_lower_better = serializers.BooleanField(read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Performance
        fields = [
            "id",
            "team",
            "member",
            "label",
            "value",
            "unit",
            "is_lower_better",
            "recorded_on",
            "notes",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "is_lower_better",
            "created_by",
            "created_at",
            "updated_at",
        ]

    def update(self, instance, validated_data):
        # A performance never moves to another member/team: ignore any change
        # to those relations on update (PATCH/PUT only edits the data fields).
        validated_data.pop("team", None)
        validated_data.pop("member", None)
        return super().update(instance, validated_data)

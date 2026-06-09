from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Attendance, AttendanceStatus


class AttendanceStatusSerializer(serializers.ModelSerializer):
    """Public flavor: 'label' is auto-localized by modeltranslation."""

    class Meta:
        model = AttendanceStatus
        fields = [
            "id",
            "code",
            "label",
            "is_default",
            "order",
            "color",
            "is_active",
        ]
        read_only_fields = fields


class AttendanceStatusAdminSerializer(serializers.ModelSerializer):
    """Admin flavor: exposes per-language label variants."""

    class Meta:
        model = AttendanceStatus
        fields = [
            "id",
            "code",
            "label_fr",
            "label_nl",
            "label_en",
            "label_it",
            "label_es",
            "is_default",
            "order",
            "color",
            "is_active",
        ]
        read_only_fields = ["id"]


class AttendanceSerializer(serializers.ModelSerializer):
    member_fullname = serializers.SerializerMethodField()
    status_code = serializers.CharField(source="status.code", read_only=True)

    class Meta:
        model = Attendance
        fields = [
            "id",
            "event",
            "member",
            "member_fullname",
            "status",
            "status_code",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "event",
            "member_fullname",
            "status_code",
            "created_at",
            "updated_at",
        ]

    def get_extra_kwargs(self):
        # `member` is writable only at create time. On update/partial_update
        # (self.instance is set), it becomes read-only so a coach cannot
        # repoint an Attendance row to a member of another team — silent
        # ignore, consistent with the pattern used for is_staff/is_superuser
        # on /me/.
        extra_kwargs = super().get_extra_kwargs()
        if self.instance is not None:
            member_kwargs = dict(extra_kwargs.get("member", {}))
            member_kwargs["read_only"] = True
            extra_kwargs["member"] = member_kwargs
        return extra_kwargs

    @extend_schema_field(serializers.CharField())
    def get_member_fullname(self, obj) -> str:
        m = obj.member
        parts = [p for p in [m.firstname, m.lastname] if p]
        return " ".join(parts) if parts else f"Member#{m.pk}"


class AttendanceBulkItemSerializer(serializers.Serializer):
    member_id = serializers.IntegerField()
    status_code = serializers.CharField(max_length=30)


# A single bulk write is one event's roster; cap it so a malformed/abusive
# payload can't open a long transaction doing N per-row upserts.
MAX_BULK_ATTENDANCES = 500


class AttendanceBulkSerializer(serializers.Serializer):
    attendances = AttendanceBulkItemSerializer(many=True)

    def validate_attendances(self, value):
        if not value:
            raise serializers.ValidationError(
                "attendances list cannot be empty.",
                code="empty_attendances",
            )
        if len(value) > MAX_BULK_ATTENDANCES:
            raise serializers.ValidationError(
                f"Too many attendances in one request (max {MAX_BULK_ATTENDANCES}).",
                code="too_many_attendances",
            )
        member_ids = [item["member_id"] for item in value]
        if len(member_ids) != len(set(member_ids)):
            raise serializers.ValidationError(
                "Duplicate member_id in attendances list.",
                code="duplicate_member",
            )
        return value

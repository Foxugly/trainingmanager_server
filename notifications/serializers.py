from rest_framework import serializers

from .models import Notification, NotificationType


class NotificationSerializer(serializers.ModelSerializer):
    """Read serializer for a single in-app notification."""

    class Meta:
        model = Notification
        fields = [
            "id",
            "type",
            "title",
            "body",
            "url",
            "is_read",
            "created_at",
        ]
        read_only_fields = fields


class UnreadCountSerializer(serializers.Serializer):
    """Response shape for GET /notifications/unread_count/."""

    count = serializers.IntegerField(help_text="Number of unread notifications for the caller.")


class ReadAllResponseSerializer(serializers.Serializer):
    """Response shape for POST /notifications/read_all/."""

    updated = serializers.IntegerField(help_text="Number of notifications marked as read.")


class NotificationPreferenceSerializer(serializers.Serializer):
    """Effective per-type channel preference (one row of the matrix).

    Used both as the GET response item and the PUT request item. ``label``
    is the human-readable, localized NotificationType label; it is read-only
    (server-derived) and ignored on input.
    """

    type = serializers.ChoiceField(choices=NotificationType.choices)
    label = serializers.CharField(read_only=True)
    in_app = serializers.BooleanField()
    email = serializers.BooleanField()
    push = serializers.BooleanField(required=False, default=True)


class NotificationPreferenceUpdateSerializer(serializers.Serializer):
    """Request body for PUT /notifications/preferences/.

    A list of {type, in_app, email} entries to upsert. Types are validated
    against NotificationType; duplicates of the same type are rejected.
    """

    preferences = NotificationPreferenceSerializer(many=True)

    def validate_preferences(self, value):
        seen = set()
        for entry in value:
            t = entry["type"]
            if t in seen:
                raise serializers.ValidationError(
                    f"Duplicate preference for type '{t}'.",
                    code="duplicate_type",
                )
            seen.add(t)
        return value

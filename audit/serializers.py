from rest_framework import serializers

from .models import AuditLogEntry


class AuditLogEntrySerializer(serializers.ModelSerializer):
    """Read-only view of an audit log entry for coaches.

    ``action_display`` is the localized human label for the stable ``action``
    code. All fields are read-only — the log is append-only and only written
    via ``audit.services.record``.
    """

    action_display = serializers.CharField(source="get_action_display", read_only=True)

    class Meta:
        model = AuditLogEntry
        fields = [
            "id",
            "actor",
            "actor_label",
            "action",
            "action_display",
            "team",
            "target_repr",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields

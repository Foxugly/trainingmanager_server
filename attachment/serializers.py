from rest_framework import serializers

from .models import Attachment


class AttachmentSerializer(serializers.ModelSerializer):
    """Read-only representation of an Attachment.

    ``target_type`` / ``target_id`` are derived from the generic relation so the
    frontend never sees raw content_type ids.
    """

    target_type = serializers.SerializerMethodField()
    target_id = serializers.IntegerField(source="object_id", read_only=True)
    uploaded_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Attachment
        fields = [
            "id",
            "target_type",
            "target_id",
            "filename",
            "content_type_mime",
            "size_bytes",
            "status",
            "uploaded_by",
            "created_at",
        ]
        read_only_fields = fields

    def get_target_type(self, obj) -> str | None:
        # Map the stored ContentType back to our public label (event/message).
        ct = obj.content_type
        for label, (app_label, model) in _label_lookup().items():
            if ct.app_label == app_label and ct.model == model:
                return label
        return None


def _label_lookup():
    from .permissions import TARGET_MODELS

    return TARGET_MODELS


class PresignUploadRequestSerializer(serializers.Serializer):
    """Body for POST /attachments/presign/."""

    target_type = serializers.ChoiceField(
        choices=["event", "message", "program", "performance"]
    )
    target_id = serializers.IntegerField(min_value=1)
    filename = serializers.CharField(min_length=1, max_length=255)
    content_type = serializers.CharField(max_length=150)
    size = serializers.IntegerField(min_value=1)

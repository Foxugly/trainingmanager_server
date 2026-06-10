from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers

from customuser.serializers import CustomUserPublicSerializer
from tools.html_sanitizer import sanitize_html, strip_html

from .models import Message, Topic


@extend_schema_serializer(component_name="TopicMessage")
class MessageSerializer(serializers.ModelSerializer):
    """A single message. ``author`` is the nested public user; ``content``
    is sanitized via bleach on every write."""

    author = CustomUserPublicSerializer(read_only=True)

    class Meta:
        model = Message
        fields = [
            "id",
            "content",
            "author",
            "edited_at",
            "created_at",
        ]
        read_only_fields = ["id", "author", "edited_at", "created_at"]

    def validate_content(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError(
                _("Message content cannot be empty."),
                code="empty_content",
            )
        cleaned = sanitize_html(value)
        if not cleaned or not cleaned.strip():
            # Sanitization may have stripped everything (only disallowed tags).
            raise serializers.ValidationError(
                _("Message content cannot be empty."),
                code="empty_content",
            )
        return cleaned


class TopicSerializer(serializers.ModelSerializer):
    """A team topic. ``author`` is the nested public user; ``message_count``
    is the number of messages in the thread. ``team`` and ``author`` are
    derived from the URL / request and are read-only."""

    author = CustomUserPublicSerializer(read_only=True)
    message_count = serializers.SerializerMethodField()

    class Meta:
        model = Topic
        fields = [
            "id",
            "title",
            "audience",
            "allow_athlete_replies",
            "author",
            "message_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "author",
            "message_count",
            "created_at",
            "updated_at",
        ]

    def get_message_count(self, obj) -> int:
        # Prefer the annotated value from the list/detail queryset; fall back
        # to a live count for freshly created instances (POST response).
        annotated = getattr(obj, "message_count", None)
        if annotated is not None:
            return annotated
        return obj.messages.count()

    def validate_title(self, value):
        # A title is plain text: strip any HTML (a title is never rendered as
        # markup) so it can't carry a stored-XSS payload — consistent with how
        # message bodies are sanitized.
        cleaned = strip_html(value or "").strip()
        if not cleaned:
            raise serializers.ValidationError(
                _("Title cannot be empty."),
                code="empty_title",
            )
        return cleaned


class UnreadTopicSerializer(serializers.Serializer):
    """One topic with unread messages, for the unread summary."""

    topic_id = serializers.IntegerField()
    team_id = serializers.IntegerField()
    team_name = serializers.CharField()
    title = serializers.CharField()
    unread_count = serializers.IntegerField()
    updated_at = serializers.DateTimeField()


class UnreadSummarySerializer(serializers.Serializer):
    """The current user's unread discussion summary (count + topics)."""

    count = serializers.IntegerField()
    topics = UnreadTopicSerializer(many=True)

from rest_framework import serializers

from tools.html_sanitizer import sanitize_html

from .models import Note


class NoteSerializer(serializers.ModelSerializer):
    """Serializer for Note. team/member/author are derived from the URL
    and the request user; they are read-only here. The 'content' field
    is sanitized via nh3 on every write."""

    # Denorm display label. Email-only: no username column — sourced off email
    # (field NAME kept to avoid churn on the note UI that reads it).
    author_username = serializers.CharField(
        source="author.email",
        read_only=True,
        default=None,
    )

    class Meta:
        model = Note
        fields = [
            "id",
            "team",
            "member",
            "author",
            "author_username",
            "content",
            "visible_to_athlete",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "team",
            "member",
            "author",
            "author_username",
            "is_active",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            # Coach-writable; only coaches reach a write at all (athletes are
            # blocked by IsTeamCoachOrReadOwnNotes). Defaults to False on create.
            "visible_to_athlete": {"required": False, "default": False},
        }

    def validate_content(self, value):
        return sanitize_html(value)

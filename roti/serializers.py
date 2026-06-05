from rest_framework import serializers

from .models import Roti


class RotiSerializer(serializers.ModelSerializer):
    """Read/write serializer for a single Roti row.

    `event` and `member` are resolved by the view (event from the URL,
    member from the authenticated user's active membership) and are
    read-only here — mirrors AttendanceSerializer where the view owns the
    relationship binding. Only `score` (1..5) is writable.
    """

    class Meta:
        model = Roti
        fields = [
            "id",
            "event",
            "member",
            "score",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "event",
            "member",
            "created_at",
            "updated_at",
        ]

    def validate_score(self, value):
        if not (1 <= value <= 5):
            raise serializers.ValidationError(
                "Score must be between 1 and 5.",
                code="score_out_of_range",
            )
        return value


class RotiUpsertSerializer(serializers.Serializer):
    """Request body for PUT .../roti/ — only the caller's own score."""

    score = serializers.IntegerField(min_value=1, max_value=5)


class RotiSummarySerializer(serializers.Serializer):
    """Aggregate ROTI for an event, plus the caller's own score.

    `average`/`distribution` are only populated for team managers; an
    athlete sees their own `my_score` (and the aggregate counts are still
    returned, scoped to what the view chooses to expose).
    """

    average = serializers.FloatField(allow_null=True)
    count = serializers.IntegerField()
    distribution = serializers.DictField(child=serializers.IntegerField())
    my_score = serializers.IntegerField(allow_null=True)

from rest_framework import serializers

from .models import AIUsage


class AIUsageDetailSerializer(serializers.ModelSerializer):
    """Single AI call row, with the user's email for display."""

    # Email-only: no username column — sourced off email (field NAME kept to
    # avoid churn on the admin AI-usage UI that reads it).
    username = serializers.CharField(source="user.email", read_only=True, default=None)

    class Meta:
        model = AIUsage
        fields = [
            "id",
            "endpoint",
            "model_used",
            "input_tokens",
            "output_tokens",
            "cache_creation_tokens",
            "cache_read_tokens",
            "total_tokens",
            "username",
            "created_at",
        ]
        read_only_fields = fields


class AIUsageAggregateRowSerializer(serializers.Serializer):
    """One row of the aggregated usage response (per period bucket)."""

    period = serializers.CharField()
    total_calls = serializers.IntegerField()
    total_tokens = serializers.IntegerField()
    input_tokens = serializers.IntegerField()
    output_tokens = serializers.IntegerField()


class AIUsageAggregateResponseSerializer(serializers.Serializer):
    """Top-level aggregate response."""

    team_id = serializers.IntegerField()
    period = serializers.CharField()
    exclude_ping = serializers.BooleanField()
    data = AIUsageAggregateRowSerializer(many=True)


class AIUsageByTeamQuerySerializer(serializers.Serializer):
    """Validates ?period/?start/?end/?exclude_ping on /teams/{id}/ai-usage/.

    Replaces the previous hand-rolled parsing that let malformed dates
    bubble up as 500 from the ORM (mirror of I5 fix on chat)."""

    period = serializers.ChoiceField(
        choices=["day", "week", "month", "year"],
        required=False,
        default="month",
    )
    start = serializers.DateField(required=False)
    end = serializers.DateField(required=False)
    exclude_ping = serializers.BooleanField(required=False, default=True)


class AIUsageByTeamDetailsQuerySerializer(serializers.Serializer):
    """Validates ?since/?endpoint on /teams/{id}/ai-usage/details/."""

    since = serializers.DateTimeField(required=False)
    endpoint = serializers.ChoiceField(
        choices=[("ping", "ping"), ("plan", "plan"), ("training", "training")],
        required=False,
    )

from rest_framework import serializers

from .stats import StatsPeriodSerializer

# ---------------------------------------------------------------------
# AI review of a training block — POST /api/v1/teams/{id}/review-block/
# ---------------------------------------------------------------------


class ReviewBlockRequestSerializer(serializers.Serializer):
    """Optional free-text guidance appended to the AI review prompt. The
    [from, to] window is taken from the same query params as /stats/."""

    additional_prompt = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=2000
    )


class ReviewFindingSerializer(serializers.Serializer):
    area = serializers.CharField()
    severity = serializers.CharField()
    observation = serializers.CharField()


class ReviewAdjustmentSerializer(serializers.Serializer):
    recommendation = serializers.CharField()
    rationale = serializers.CharField(allow_blank=True)


class ReviewTokensUsedSerializer(serializers.Serializer):
    input = serializers.IntegerField()
    output = serializers.IntegerField()


class ReviewBlockResponseSerializer(serializers.Serializer):
    """Structured AI critique of a team's training block over the window."""

    period = StatsPeriodSerializer()
    summary = serializers.CharField()
    load_assessment = serializers.CharField(
        help_text="One of: too_light, balanced, too_heavy, uncertain."
    )
    findings = ReviewFindingSerializer(many=True)
    adjustments = ReviewAdjustmentSerializer(many=True)
    confidence = serializers.CharField(help_text="One of: low, medium, high.")
    model = serializers.CharField()
    tokens_used = ReviewTokensUsedSerializer()

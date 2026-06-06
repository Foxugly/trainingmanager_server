from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from team.models import Team
from team.serializers import TeamMinimalSerializer

from .choices import OVERLAP_STRATEGY_CHOICES
from .models import Program


class ProgramMinimalSerializer(serializers.ModelSerializer):
    """Compact program payload for nested read contexts."""

    class Meta:
        model = Program
        fields = ["id", "name"]
        read_only_fields = fields


class ProgramSerializer(serializers.ModelSerializer):
    team = TeamMinimalSerializer(read_only=True)
    team_id = serializers.PrimaryKeyRelatedField(
        source="team",
        queryset=Team.objects.all(),
        write_only=True,
    )
    # `events` is the FK reverse manager since Program.events M2M was dropped
    # in migration 0008. Exposed read-only — to attach an Event to a Program,
    # write `refer_program` on the Event itself.
    events = serializers.PrimaryKeyRelatedField(many=True, read_only=True)

    class Meta:
        model = Program
        fields = [
            "id",
            "name",
            "date_start",
            "date_end",
            "team",
            "team_id",
            "events",
            "frequency_per_week",
            "description",
            "generated_by_ai",
            "ai_response",
            "ai_generated_at",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "events",
            "generated_by_ai",
            "ai_response",
            "ai_generated_at",
            "created_at",
            "updated_at",
        ]


ADDITIONAL_PROMPT_MAX_LENGTH = 2000


class GeneratePlanRequestSerializer(serializers.Serializer):
    date_start = serializers.DateField()
    date_end = serializers.DateField()
    frequency_per_week = serializers.IntegerField(
        min_value=1,
        max_value=14,
        required=False,
        help_text=(
            "Weekly session count. Optional: when the team has a weekly "
            "training template, the frequency is derived from the number of "
            "slots and any supplied value is ignored. Required (else 400 "
            "frequency_required) only when the team has no template."
        ),
    )
    description = serializers.CharField(required=False, allow_blank=True, default="")
    additional_prompt = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        trim_whitespace=True,
    )
    overlap_strategy = serializers.ChoiceField(
        choices=OVERLAP_STRATEGY_CHOICES,
        default="add_only",
    )

    def validate_additional_prompt(self, value):
        if len(value) > ADDITIONAL_PROMPT_MAX_LENGTH:
            raise serializers.ValidationError(
                _("Additional prompt cannot exceed %(n)d characters.")
                % {"n": ADDITIONAL_PROMPT_MAX_LENGTH},
                code="additional_prompt_too_long",
            )
        return value

    def validate(self, data):
        if data["date_end"] < data["date_start"]:
            raise serializers.ValidationError(
                {"date_end": _("date_end must be after date_start.")},
                code="date_range_invalid",
            )
        if (data["date_end"] - data["date_start"]).days > 365:
            raise serializers.ValidationError(
                {"date_end": _("Range cannot exceed 365 days.")},
                code="date_range_too_long",
            )
        return data

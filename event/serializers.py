from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from program.models import Program
from program.serializers import ProgramMinimalSerializer
from round.models import Round

from .models import Event

ADDITIONAL_PROMPT_MAX_LENGTH = 2000


class ReorderRoundsRequestSerializer(serializers.Serializer):
    """Body for POST /events/{id}/rounds/reorder/.

    `round_ids` must contain exactly the IDs of the Rounds attached to the
    Event in the desired final order. The view enforces scope/completeness
    in addition to this serializer's syntactic validation.
    """

    round_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=True,
    )


class DuplicateEventRequestSerializer(serializers.Serializer):
    """Body for POST /events/{id}/duplicate/.

    `date` is the date of the FIRST copy. When `repeat_weekly` is True, the
    view materializes `occurrences` independent Event rows, each 7 days apart
    starting at `date`. When False, exactly one copy is created (the view
    forces occurrences to 1 regardless of the supplied value).
    """

    date = serializers.DateField(required=True)
    repeat_weekly = serializers.BooleanField(required=False, default=False)
    occurrences = serializers.IntegerField(
        required=False,
        default=1,
        min_value=1,
        max_value=52,
    )


class EventShareRequestSerializer(serializers.Serializer):
    """Body for POST /events/{id}/share/.

    `is_public=True` shares the session (minting a public token if absent);
    `is_public=False` un-shares it but keeps the token so re-enabling reuses
    the same public URL.
    """

    is_public = serializers.BooleanField(required=True)


class GenerateTrainingRequestSerializer(serializers.Serializer):
    """Optional payload for POST /events/{id}/generate-training/.

    `additional_prompt` is appended to the LLM user prompt after the
    structured context (sport, level, duration, catalogs). Empty/missing
    is a no-op so older clients without a body remain compatible.
    """

    additional_prompt = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        trim_whitespace=True,
    )

    def validate_additional_prompt(self, value):
        # Custom validation (vs. CharField max_length) so we can stamp a
        # specific error code that the frontend can match on, instead of
        # the generic DRF "max_length".
        if len(value) > ADDITIONAL_PROMPT_MAX_LENGTH:
            raise serializers.ValidationError(
                _("Additional prompt cannot exceed %(n)d characters.")
                % {"n": ADDITIONAL_PROMPT_MAX_LENGTH},
                code="additional_prompt_too_long",
            )
        return value


class EventSerializer(serializers.ModelSerializer):
    refer_program = ProgramMinimalSerializer(read_only=True)
    refer_program_id = serializers.PrimaryKeyRelatedField(
        source="refer_program",
        queryset=Program.objects.all(),
        write_only=True,
        required=True,
        allow_null=False,
    )
    rounds = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Round.objects.all(), required=False
    )
    members = serializers.PrimaryKeyRelatedField(many=True, read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "name",
            "goal",
            "location",
            "equipment",
            "color",
            "date",
            "hour_start",
            "hour_end",
            "total",
            "refer_program",
            "refer_program_id",
            "rounds",
            "members",
            "vis_distance",
            "vis_goal",
            "vis_rounds",
            "is_public",
            "public_token",
            "generated_by_ai",
            "ai_response",
            "ai_generated_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            # is_public / public_token are managed ONLY via /events/{id}/share/;
            # they must never be writable through the normal create/update path.
            "is_public",
            "public_token",
            "generated_by_ai",
            "ai_response",
            "ai_generated_at",
            "created_at",
            "updated_at",
        ]

    def _requester_is_manager(self, instance):
        """True if the request user owns/manages the event's team.

        Managers/owners always see every aspect. Anonymous and non-team
        requesters never reach here (the queryset already scopes them out),
        so any non-manager who can see the event is treated as an athlete.
        """
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        team = instance.team
        if team is None:
            return False
        return team.is_managed_by(user)

    def to_representation(self, instance):
        """Serialize, then null out aspects hidden from a non-manager athlete.

        Security: an athlete-member of the event's team must never receive the
        real value of an aspect whose resolved visibility is not satisfied.
        - vis_distance not visible -> ``total`` is nulled.
        - vis_goal not visible     -> ``goal`` is nulled.
        - vis_rounds not visible   -> ``rounds`` is emptied.
        The vis_* mode fields themselves stay readable so the frontend knows
        WHY a value is hidden. Managers/owners are never redacted.
        """
        data = super().to_representation(instance)
        if self._requester_is_manager(instance):
            return data
        if not instance.aspect_visible_to_athlete("distance"):
            data["total"] = None
        if not instance.aspect_visible_to_athlete("goal"):
            data["goal"] = None
        if not instance.aspect_visible_to_athlete("rounds"):
            data["rounds"] = []
        return data

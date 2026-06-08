from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from event.models import Event
from exercise.models import Exercise
from sport.models import Sport
from sport.serializers import SportSerializer
from tools.exceptions import ResourceLocked
from tools.validators import MMSS_VALIDATOR

from .models import Round
from .utils import check_exercise_round_consistency


class ReorderExercisesRequestSerializer(serializers.Serializer):
    """Body for POST /rounds/{id}/exercises/reorder/.

    `exercise_ids` must contain exactly the IDs of the Exercises currently
    attached to the Round in the desired final order. The view enforces
    scope/completeness in addition to this serializer's syntactic check.
    """

    exercise_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=True,
    )


class NotAuthorizedEvent(PermissionDenied):
    """User has no manager rights on the team owning the target event.

    Raised when a Round is created with `event_id=<id>` but the request user
    does not manage the team behind the referenced event. The dedicated
    default_code lets the frontend distinguish this from a generic
    'permission_denied' — useful since the rest of the POST payload is
    legitimate (the round itself was creatable).
    """

    default_detail = _("You are not authorized to attach a round to this event.")
    default_code = "not_authorized_event"


class RoundSerializer(serializers.ModelSerializer):
    sport = SportSerializer(read_only=True)
    sport_id = serializers.PrimaryKeyRelatedField(
        source="sport",
        queryset=Sport.objects.all(),
        write_only=True,
    )
    exercises = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Exercise.objects.all(), required=False
    )
    # Explicit declaration so drf-spectacular emits `pattern` in the
    # OpenAPI schema (auto-generated ModelSerializer fields don't surface
    # model-level RegexValidator patterns).
    t_start = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        validators=[MMSS_VALIDATOR],
        help_text=_("MM:SS format, e.g. 1:30."),
    )
    t_break = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        validators=[MMSS_VALIDATOR],
        help_text=_("MM:SS format, e.g. 1:30."),
    )
    event_id = serializers.PrimaryKeyRelatedField(
        source="_target_event",
        queryset=Event.objects.all(),
        write_only=True,
        required=False,
        help_text=_(
            "Optional. If provided on POST, the newly created Round is atomically "
            "attached to this Event. The request user must manage the Event's team. "
            "Ignored on PATCH/PUT."
        ),
    )
    usage_count = serializers.SerializerMethodField()

    @extend_schema_field(serializers.IntegerField())
    def get_usage_count(self, obj) -> int:
        # Prefer the queryset annotation (one grouped COUNT for the whole list);
        # fall back to the per-object property for freshly built/cloned rounds.
        annotated = getattr(obj, "_usage_count", None)
        return annotated if annotated is not None else obj.usage_count

    class Meta:
        model = Round
        fields = [
            "id",
            "sport",
            "sport_id",
            "language",
            "order",
            "count",
            "t_start",
            "t_break",
            "exercises",
            "event_id",
            "usage_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "usage_count", "created_at", "updated_at"]

    def validate(self, data):
        data = super().validate(data)
        exercises = data.get("exercises", [])
        if not exercises:
            return data

        sport = data.get("sport") or (self.instance.sport if self.instance else None)
        language = data.get("language") or (self.instance.language if self.instance else None)
        target = self.instance or Round(sport=sport, language=language)
        if sport is not None:
            target.sport = sport
        if language is not None:
            target.language = language

        for ex in exercises:
            check_exercise_round_consistency(ex, target)
        return data

    def create(self, validated_data):
        target_event = validated_data.pop("_target_event", None)
        if target_event is not None:
            request = self.context.get("request")
            user = getattr(request, "user", None)
            program = target_event.refer_program
            team = program.team if program is not None else None
            if user is None or team is None or not team.is_managed_by(user):
                raise NotAuthorizedEvent()
        round_obj = super().create(validated_data)
        if target_event is not None:
            target_event.rounds.add(round_obj)
        return round_obj

    def update(self, instance, validated_data):
        if instance.usage_count > 1:
            raise ResourceLocked()
        validated_data.pop("_target_event", None)
        return super().update(instance, validated_data)

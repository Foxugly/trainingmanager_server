from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from round.models import Round
from sport.serializers import SportSerializer
from tools.exceptions import ResourceLocked
from tools.validators import MMSS_VALIDATOR

from .models import EnergySegment, EnergySystem, Exercise, Modality


class NotAuthorizedRound(PermissionDenied):
    """User has no manager rights on any team owning an event tied to the target round.

    Raised when an Exercise is created with `round_id=<id>` but the round is
    already attached to events whose teams are not managed by the request user.
    Library rounds (no events) skip this check — IsTrainer + (sport, language)
    scoping already protect catalog mutations there.
    """

    default_detail = _("You are not authorized to attach an exercise to this round.")
    default_code = "not_authorized_round"


class ModalitySerializer(serializers.ModelSerializer):
    sport = SportSerializer(read_only=True)

    class Meta:
        model = Modality
        fields = ["id", "name", "sport", "is_active"]
        read_only_fields = fields


class ModalityAdminSerializer(serializers.ModelSerializer):
    """Admin flavor: per-language name variants + writable sport FK."""

    class Meta:
        model = Modality
        fields = [
            "id",
            "name_fr",
            "name_nl",
            "name_en",
            "name_it",
            "name_es",
            "sport",
            "is_active",
        ]
        read_only_fields = ["id"]


class EnergySystemSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnergySystem
        fields = ["id", "name", "is_active"]
        read_only_fields = ["id", "name", "is_active"]


class EnergySystemAdminSerializer(serializers.ModelSerializer):
    """Admin flavor: per-language name variants."""

    class Meta:
        model = EnergySystem
        fields = [
            "id",
            "name_fr",
            "name_nl",
            "name_en",
            "name_it",
            "name_es",
            "is_active",
        ]
        read_only_fields = ["id"]


class EnergySegmentSerializer(serializers.ModelSerializer):
    energy_system = EnergySystemSerializer(source="energysystem", read_only=True)
    energy_system_id = serializers.PrimaryKeyRelatedField(
        source="energysystem",
        queryset=EnergySystem.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = EnergySegment
        fields = [
            "id",
            "abv",
            "description",
            "energy_system",
            "energy_system_id",
            "is_active",
        ]
        read_only_fields = ["id", "is_active"]


class EnergySegmentAdminSerializer(serializers.ModelSerializer):
    """Admin flavor: per-language description variants + writable energy_system_id."""

    energy_system_id = serializers.PrimaryKeyRelatedField(
        source="energysystem",
        queryset=EnergySystem.objects.all(),
    )

    class Meta:
        model = EnergySegment
        fields = [
            "id",
            "abv",
            "description_fr",
            "description_nl",
            "description_en",
            "description_it",
            "description_es",
            "energy_system_id",
            "is_active",
        ]
        read_only_fields = ["id"]


class ExerciseSerializer(serializers.ModelSerializer):
    modality = ModalitySerializer(read_only=True)
    modality_id = serializers.PrimaryKeyRelatedField(
        source="modality",
        queryset=Modality.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )
    # Explicit declaration so drf-spectacular emits `pattern` in the schema.
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
    energysegment = EnergySegmentSerializer(read_only=True)
    energysegment_id = serializers.PrimaryKeyRelatedField(
        source="energysegment",
        queryset=EnergySegment.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )
    round_id = serializers.PrimaryKeyRelatedField(
        source="_target_round",
        queryset=Round.objects.all(),
        write_only=True,
        required=False,
        help_text=_(
            "Optional. On POST, the newly created Exercise is atomically attached "
            "to this Round. On PATCH/PUT, if the Exercise is shared by several "
            "Rounds (usage_count > 1), it is forked: a clone with the changes is "
            "swapped into THIS Round and the shared original is left untouched. "
            "The request user must manage at least one team of an Event linked to "
            "this Round (library rounds with no events are accepted as-is)."
        ),
    )
    usage_count = serializers.SerializerMethodField()

    class Meta:
        model = Exercise
        fields = [
            "id",
            "order",
            "repetition",
            "distance",
            "notes",
            "t_start",
            "t_break",
            "modality",
            "modality_id",
            "energysegment",
            "energysegment_id",
            "round_id",
            "language",
            "usage_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "usage_count", "created_at", "updated_at"]

    @extend_schema_field(serializers.IntegerField())
    def get_usage_count(self, obj) -> int:
        return obj.usage_count

    # Scalar fields copied when forking a shared exercise on edit.
    _FORK_FIELDS = (
        "order",
        "repetition",
        "distance",
        "notes",
        "t_start",
        "t_break",
        "modality",
        "energysegment",
        "language",
    )

    def _assert_can_write_round(self, target_round):
        """Raise unless the request user manages a team of an event tied to the
        round. Library rounds with no events are accepted as-is."""
        request = self.context.get("request")
        user = getattr(request, "user", None)
        linked_events = list(target_round.event_set.select_related("refer_program__team").all())
        if linked_events:
            authorized = any(
                e.refer_program is not None and e.refer_program.team.is_managed_by(user)
                for e in linked_events
            )
            if user is None or not authorized:
                raise NotAuthorizedRound()

    def create(self, validated_data):
        target_round = validated_data.pop("_target_round", None)
        if target_round is not None:
            self._assert_can_write_round(target_round)
        exercise = super().create(validated_data)
        if target_round is not None:
            target_round.exercises.add(exercise)
        return exercise

    def update(self, instance, validated_data):
        target_round = validated_data.pop("_target_round", None)
        # An Exercise is a shared/deduplicated library row. If it is used by more
        # than one Round, editing it in place would silently mutate every other
        # round that references it. Instead, FORK-ON-EDIT: clone the row with the
        # requested changes and swap it into the caller's round, leaving the
        # shared original untouched for everyone else. The caller must say which
        # round to relink (round_id); without it we cannot fork safely.
        if instance.usage_count > 1:
            if target_round is None:
                raise ResourceLocked()
            self._assert_can_write_round(target_round)
            if not target_round.exercises.filter(pk=instance.pk).exists():
                # The exercise isn't part of the round the caller named.
                raise ResourceLocked()
            clone_kwargs = {
                field: validated_data.get(field, getattr(instance, field))
                for field in self._FORK_FIELDS
            }
            with transaction.atomic():
                clone = Exercise.objects.create(**clone_kwargs)
                target_round.exercises.remove(instance)
                target_round.exercises.add(clone)
            return clone
        # Not shared (0/1 round): a plain in-place update is safe.
        return super().update(instance, validated_data)

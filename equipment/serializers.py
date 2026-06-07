from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from .models import Equipment


class EquipmentSerializer(serializers.ModelSerializer):
    """Read/write serializer for managed team equipment (Matériel).

    `team` is a writable PK on create; the view enforces that the requester
    manages the team. The (team, name) unique constraint is surfaced as a 400
    with code `equipment_already_exists` instead of a DB IntegrityError.
    """

    class Meta:
        model = Equipment
        fields = [
            "id",
            "team",
            "name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        # Suppress DRF's auto UniqueTogetherValidator (code "unique") so our
        # custom check in validate() owns the duplicate case with a clear,
        # frontend-matchable code "equipment_already_exists".
        validators = []

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError(
                _("Equipment name cannot be empty."),
                code="equipment_name_required",
            )
        return value

    def validate(self, data):
        team = data.get("team") or getattr(self.instance, "team", None)
        name = data.get("name", getattr(self.instance, "name", None))
        if team is not None and name is not None:
            qs = Equipment.objects.filter(team=team, name=name)
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {"name": _("Equipment with this name already exists for this team.")},
                    code="equipment_already_exists",
                )
        return data


class EquipmentMinimalSerializer(serializers.ModelSerializer):
    """Compact nested read shape for Event.equipment_items."""

    class Meta:
        model = Equipment
        fields = ["id", "name"]
        read_only_fields = fields

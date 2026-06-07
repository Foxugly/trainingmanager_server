from rest_framework import serializers

from .models import Equipment


class EquipmentSerializer(serializers.ModelSerializer):
    """Read serializer for the global equipment catalog.

    ``name`` is auto-localized to the active language by modeltranslation. The
    catalog is curated centrally (admin + seed), so the API is read-only.
    """

    class Meta:
        model = Equipment
        fields = ["id", "name"]
        read_only_fields = fields


class EquipmentMinimalSerializer(serializers.ModelSerializer):
    """Compact nested read shape for Team.equipment / Event.equipment_items."""

    class Meta:
        model = Equipment
        fields = ["id", "name"]
        read_only_fields = fields

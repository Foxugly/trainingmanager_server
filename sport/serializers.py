from rest_framework import serializers

from .models import Sport


class SportSerializer(serializers.ModelSerializer):
    """Public read serializer: 'name' is auto-localised by modeltranslation.
    energy_systems exposed as PK list (frontend joins via /energy-systems/)."""

    class Meta:
        model = Sport
        fields = [
            "id",
            "name",
            "slug",
            "is_active",
            "energy_systems",
            "default_training_type",
            "created_at",
        ]
        read_only_fields = fields


class SportAdminSerializer(serializers.ModelSerializer):
    """Admin flavor: exposes per-language name variants and write-capable
    M2M energy_systems (PK list)."""

    class Meta:
        model = Sport
        fields = [
            "id",
            "name_fr",
            "name_nl",
            "name_en",
            "name_it",
            "name_es",
            "slug",
            "is_active",
            "energy_systems",
            "default_training_type",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

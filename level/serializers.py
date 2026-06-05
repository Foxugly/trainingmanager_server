from rest_framework import serializers

from .models import Level


class LevelSerializer(serializers.ModelSerializer):
    """Public flavor: 'name' and 'description' are auto-localized by
    modeltranslation."""

    class Meta:
        model = Level
        fields = [
            "id",
            "code",
            "name",
            "description",
            "order",
            "is_active",
        ]
        read_only_fields = fields


class LevelAdminSerializer(serializers.ModelSerializer):
    """Admin flavor: exposes per-language name/description variants."""

    class Meta:
        model = Level
        fields = [
            "id",
            "code",
            "name_fr",
            "name_nl",
            "name_en",
            "name_it",
            "name_es",
            "description_fr",
            "description_nl",
            "description_en",
            "description_it",
            "description_es",
            "order",
            "is_active",
        ]
        read_only_fields = ["id"]

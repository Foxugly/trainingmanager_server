from rest_framework import serializers

from team.models import Team

from .models import Place


class PlaceSerializer(serializers.ModelSerializer):
    """Read/write serializer for a venue (Lieu) in the global sport-scoped pool.

    A place is shared (M2M ``Team.places``). On create the requester passes the
    ``team`` it is creating the place for (write-only): the view links it and
    derives the place's ``sport`` from that team. ``sport`` is read-only output.
    """

    team = serializers.PrimaryKeyRelatedField(
        queryset=Team.objects.all(), write_only=True, required=False
    )

    class Meta:
        model = Place
        fields = [
            "id",
            "sport",
            "name",
            "address",
            "team",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "sport", "created_at", "updated_at"]

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError(
                "Place name cannot be empty.", code="place_name_required"
            )
        return value


class PlaceMinimalSerializer(serializers.ModelSerializer):
    """Compact nested read shape for Event.place / Team.default_place / places."""

    class Meta:
        model = Place
        fields = ["id", "name", "address"]
        read_only_fields = fields

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from team.models import Team

from .models import Place


class PlaceSerializer(serializers.ModelSerializer):
    """Read/write serializer for the managed team venue (Lieu).

    `team` is a writable PK on create; the view enforces that the requester
    manages the team. The (team, name) unique constraint is surfaced as a
    400 with code `place_already_exists` instead of a DB IntegrityError.
    """

    class Meta:
        model = Place
        fields = [
            "id",
            "team",
            "name",
            "address",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        # Suppress DRF's auto UniqueTogetherValidator (code "unique") so our
        # custom check in validate() owns the duplicate case with a clear,
        # frontend-matchable code "place_already_exists".
        validators = []

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError(
                _("Place name cannot be empty."),
                code="place_name_required",
            )
        return value

    def validate(self, data):
        # Resolve the effective (team, name) for both create and partial update
        # so the uniqueness check works on PATCH too.
        team = data.get("team") or getattr(self.instance, "team", None)
        name = data.get("name", getattr(self.instance, "name", None))
        if team is not None and name is not None:
            qs = Place.objects.filter(team=team, name=name)
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {"name": _("A place with this name already exists for this team.")},
                    code="place_already_exists",
                )
        return data


class PlaceMinimalSerializer(serializers.ModelSerializer):
    """Compact nested read shape for Event.place / Team.default_place."""

    class Meta:
        model = Place
        fields = ["id", "name", "address"]
        read_only_fields = fields


class PlaceTeamRelatedField(serializers.PrimaryKeyRelatedField):
    """A Place PK field whose queryset is unscoped here; same-team validity is
    enforced by the consuming serializer (Event / Team) against the target's
    team. Centralised so both write paths share one error code."""

    def __init__(self, **kwargs):
        kwargs.setdefault("queryset", Place.objects.all())
        super().__init__(**kwargs)

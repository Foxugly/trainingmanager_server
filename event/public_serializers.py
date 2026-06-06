"""Read-only serializers for the UNAUTHENTICATED public session share view.

These are deliberately minimal and output-only. They never expose anything
beyond what the team's public_show_* config allows, and they are kept separate
from the authenticated EventSerializer so that no writable / sensitive field
can ever leak through the anonymous endpoint by accident.
"""

from rest_framework import serializers


class PublicExerciseSerializer(serializers.Serializer):
    """Read-only exercise as shown on a public share link."""

    id = serializers.IntegerField(read_only=True)
    order = serializers.IntegerField(read_only=True)
    repetition = serializers.IntegerField(read_only=True)
    distance = serializers.IntegerField(read_only=True)
    t_start = serializers.CharField(read_only=True, allow_null=True)
    t_break = serializers.CharField(read_only=True, allow_null=True)
    notes = serializers.CharField(read_only=True, allow_blank=True)
    energysegment = serializers.SerializerMethodField()
    modality = serializers.SerializerMethodField()

    def get_modality(self, obj) -> str | None:
        # Modality.name is a modeltranslation field; rendered in the active
        # language by LocaleMiddleware (Accept-Language).
        return obj.modality.name if obj.modality_id else None

    def get_energysegment(self, obj) -> str | None:
        return obj.energysegment.abv if obj.energysegment_id else None


class PublicRoundSerializer(serializers.Serializer):
    """Read-only round (with its exercises) as shown on a public share link."""

    id = serializers.IntegerField(read_only=True)
    order = serializers.IntegerField(read_only=True)
    count = serializers.IntegerField(read_only=True)
    t_start = serializers.CharField(read_only=True, allow_null=True)
    t_break = serializers.CharField(read_only=True, allow_null=True)
    exercises = serializers.SerializerMethodField()

    def get_exercises(self, obj) -> list:
        exercises = obj.exercises.all().order_by("order", "id")
        return PublicExerciseSerializer(exercises, many=True, context=self.context).data


class EventPublicSerializer(serializers.Serializer):
    """The full read-only public payload for a shared session.

    Always-public fields: name, date, hour_start, hour_end, location,
    equipment, program_name, team_name.

    Sensitive fields are gated by the event's team config:
      - total  -> null unless team.public_show_distance
      - goal   -> null unless team.public_show_goal
      - rounds -> [] unless team.public_show_rounds
    """

    name = serializers.CharField(read_only=True)
    date = serializers.DateField(read_only=True, allow_null=True)
    hour_start = serializers.TimeField(read_only=True, allow_null=True)
    hour_end = serializers.TimeField(read_only=True, allow_null=True)
    location = serializers.CharField(read_only=True, allow_blank=True)
    equipment = serializers.CharField(read_only=True, allow_blank=True)
    program_name = serializers.SerializerMethodField()
    team_name = serializers.SerializerMethodField()
    total = serializers.SerializerMethodField()
    goal = serializers.SerializerMethodField()
    rounds = serializers.SerializerMethodField()

    def _team(self, obj):
        return obj.team

    def get_program_name(self, obj) -> str | None:
        program = obj.refer_program
        return program.name if program is not None else None

    def get_team_name(self, obj) -> str | None:
        team = obj.team
        return team.name if team is not None else None

    def get_total(self, obj) -> int | None:
        team = obj.team
        if team is not None and team.public_show_distance:
            return obj.total
        return None

    def get_goal(self, obj) -> str | None:
        team = obj.team
        if team is not None and team.public_show_goal:
            return obj.goal
        return None

    def get_rounds(self, obj) -> list:
        team = obj.team
        if team is None or not team.public_show_rounds:
            return []
        rounds = obj.rounds.all().order_by("order", "id")
        return PublicRoundSerializer(rounds, many=True, context=self.context).data

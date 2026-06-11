from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from equipment.models import Equipment
from equipment.serializers import EquipmentMinimalSerializer
from exercise.serializers import ExerciseSerializer
from place.models import Place
from place.serializers import PlaceMinimalSerializer
from program.models import Program
from program.serializers import ProgramMinimalSerializer
from round.models import Round
from sport.models import Sport
from sport.serializers import SportSerializer
from tools.html_sanitizer import sanitize_html

from .models import Event


class EventRoundDetailSerializer(serializers.ModelSerializer):
    """A round with its exercises fully nested — embedded under an event's
    ``rounds_detail`` so the client loads a whole session (rounds + exercises)
    in a single request instead of one fetch per round and per exercise."""

    sport = SportSerializer(read_only=True)
    exercises = serializers.SerializerMethodField()

    class Meta:
        model = Round
        fields = ["id", "order", "count", "t_start", "t_break", "sport", "exercises"]
        read_only_fields = fields

    @extend_schema_field(ExerciseSerializer(many=True))
    def get_exercises(self, round_obj):
        # Prefetched in EventViewSet.get_queryset on retrieve; sort in Python so
        # the prefetch cache is reused (no extra query).
        ordered = sorted(round_obj.exercises.all(), key=lambda e: e.order or 0)
        return ExerciseSerializer(ordered, many=True, context=self.context).data

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
    # Read-only: rounds are attached server-side (generate-training, the
    # round reorder/clone endpoints), never bulk-assigned through the event
    # write path — a writable queryset=Round.objects.all() would let a manager
    # link another team's rounds to their event.
    rounds = serializers.PrimaryKeyRelatedField(
        many=True, read_only=True, required=False
    )
    # Rounds + their exercises fully nested, populated on retrieve only (null on
    # list to keep that payload light). Lets the client load a session in one
    # request. Redacted alongside `rounds` for a restricted athlete.
    rounds_detail = serializers.SerializerMethodField()
    members = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    place = PlaceMinimalSerializer(read_only=True, allow_null=True)
    place_id = serializers.PrimaryKeyRelatedField(
        source="place",
        queryset=Place.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )
    equipment_items = EquipmentMinimalSerializer(many=True, read_only=True)
    equipment_item_ids = serializers.PrimaryKeyRelatedField(
        source="equipment_items",
        queryset=Equipment.objects.all(),
        many=True,
        write_only=True,
        required=False,
    )
    # Multi-sport: the session's sport (one of the team's sports). Optional on
    # write — defaults to the team's default sport on create. Scopes the AI
    # generator and (frontend) the modality/round pickers.
    sport = SportSerializer(read_only=True)
    sport_id = serializers.PrimaryKeyRelatedField(
        source="sport",
        queryset=Sport.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )
    # The owning team's id (resolved via refer_program, already select_related —
    # no extra query). refer_program is only a ProgramMinimal {id,name}, so the
    # detail view would otherwise fetch the program just to learn the team id and
    # then fetch the team — exposing team_id here lets it fetch the team directly
    # (one fewer round-trip on the most-opened screen).
    team_id = serializers.IntegerField(source="refer_program.team_id", read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "name",
            "goal",
            "training_type",
            "training_richtext",
            "location",
            "place",
            "place_id",
            "equipment",
            "equipment_items",
            "equipment_item_ids",
            "color",
            "date",
            "hour_start",
            "hour_end",
            "total",
            "refer_program",
            "refer_program_id",
            "team_id",
            "sport",
            "sport_id",
            "rounds",
            "rounds_detail",
            "members",
            "vis_distance",
            "vis_goal",
            "vis_rounds",
            "is_public",
            "public_token",
            "generated_by_ai",
            "ai_response",
            "ai_generated_at",
            "debrief",
            "ai_athlete_brief",
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
            # ai_athlete_brief is written ONLY via /events/{id}/explain/.
            "ai_athlete_brief",
            "created_at",
            "updated_at",
        ]

    def validate_goal(self, value):
        """Sanitize the rich-text (Quill) HTML goal on write.

        The frontend edits ``goal`` with a rich-text editor, so it holds
        HTML. We strip every tag/attribute not on the sanitizer whitelist
        so only safe markup is ever persisted. Empty/None stays empty.
        """
        if not value:
            return value
        return sanitize_html(value)

    def validate_equipment(self, value):
        """Sanitize the rich-text (Quill) HTML equipment on write.

        Same rationale as ``validate_goal`` — equipment is now rich text.
        Empty/None stays empty.
        """
        if not value:
            return value
        return sanitize_html(value)

    def validate_training_richtext(self, value):
        """Sanitize the free-text (Quill) HTML training content on write."""
        if not value:
            return value
        return sanitize_html(value)

    def _event_team(self, data):
        """The event's team for cross-field validation (create or update)."""
        program = data.get("refer_program") or getattr(self.instance, "refer_program", None)
        return program.team if program is not None else None

    def validate(self, data):
        """Validate that a chosen Place and equipment items belong to the team.

        `place` is resolved from `place_id` (sourced to `place`), the equipment
        items from `equipment_item_ids` (sourced to `equipment_items`). The team
        is taken from the create/update `refer_program` if present, else the
        existing instance's program. A foreign Place / item is a 400.
        """
        # `place`/`equipment_items` are present in `data` only when their write
        # field was sent. Absence means "leave untouched".
        if "place" in data and data["place"] is not None:
            team = self._event_team(data)
            if team is None or not team.places.filter(pk=data["place"].pk).exists():
                raise serializers.ValidationError(
                    {"place_id": _("The selected place is not one of this team's venues.")},
                    code="place_not_in_team",
                )
        if data.get("equipment_items"):
            team = self._event_team(data)
            enabled_ids = (
                set(team.equipment.values_list("id", flat=True)) if team else set()
            )
            if any(it.id not in enabled_ids for it in data["equipment_items"]):
                raise serializers.ValidationError(
                    {"equipment_item_ids": _(
                        "An equipment item is not enabled for this event's team."
                    )},
                    code="equipment_not_enabled",
                )
        if "sport" in data and data["sport"] is not None:
            team = self._event_team(data)
            if team is None or not team.sports.filter(pk=data["sport"].pk).exists():
                raise serializers.ValidationError(
                    {"sport_id": _("The selected sport is not one of this team's sports.")},
                    code="sport_not_in_team",
                )
        return data

    def _sync_location(self, validated_data):
        """Apply the place→location sync rule on the validated payload.

        - place_id non-null  -> set location = place.name (place wins).
        - place_id null       -> clear place (location follows whatever the
          `location` field carries, if any).
        - place_id absent     -> leave both place and location as supplied.
        """
        if "place" not in validated_data:
            return validated_data
        place = validated_data["place"]
        if place is not None:
            validated_data["location"] = place.name
        return validated_data

    def _sync_equipment(self, validated_data):
        """Sync the free-text 'equipment' to the joined item names when items
        are supplied (the managed list wins), so iCal / public / AI readers see
        the same equipment. Absence of `equipment_item_ids` leaves text as-is.
        """
        if "equipment_items" not in validated_data:
            return validated_data
        items = validated_data["equipment_items"]
        validated_data["equipment"] = ", ".join(it.name for it in items)
        return validated_data

    def create(self, validated_data):
        validated_data = self._sync_location(validated_data)
        validated_data = self._sync_equipment(validated_data)
        # Default the session sport to the team's default when not specified.
        if validated_data.get("sport") is None:
            program = validated_data.get("refer_program")
            team = program.team if program is not None else None
            if team is not None:
                validated_data["sport"] = team.default_sport
        if "training_type" not in validated_data:
            seed = Event(
                refer_program=validated_data.get("refer_program"),
                sport=validated_data.get("sport"),
            ).resolve_default_training_type()
            validated_data["training_type"] = seed
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data = self._sync_location(validated_data)
        validated_data = self._sync_equipment(validated_data)
        new_type = validated_data.get("training_type", instance.training_type)
        type_changed = new_type != instance.training_type
        instance = super().update(instance, validated_data)
        if type_changed:
            from tools.choices import TrainingType

            if new_type != TrainingType.STRUCTURED:
                instance.rounds.clear()
            if new_type != TrainingType.FREEFORM and instance.training_richtext:
                instance.training_richtext = ""
                instance.save(update_fields=["training_richtext"])
        return instance

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
        # Resolve the user's managed-team-id set once per request (cached on the
        # serializer context) instead of a managers query per event when
        # serializing a list.
        managed = self.context.get("_managed_team_ids")
        if managed is None:
            from team.queries import managed_teams

            managed = set(managed_teams(user).values_list("id", flat=True))
            self.context["_managed_team_ids"] = managed
        return team.id in managed

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
        # debrief is the coach's private post-session notes (manager-only, like
        # the dedicated /debrief/ endpoint). Never expose it to athletes via the
        # ordinary event read.
        if "debrief" in data:
            data["debrief"] = ""
        if not instance.aspect_visible_to_athlete("distance"):
            data["total"] = None
        if not instance.aspect_visible_to_athlete("goal"):
            data["goal"] = None
            # The athlete brief summarises the session/goal — gate it with goal.
            data["ai_athlete_brief"] = ""
        if not instance.aspect_visible_to_athlete("rounds"):
            data["rounds"] = []
            if "rounds_detail" in data:
                data["rounds_detail"] = []
            # The freeform training content is the unstructured counterpart of
            # rounds; the same vis_rounds gate must hide it too.
            data["training_richtext"] = ""
        return data

    @extend_schema_field(EventRoundDetailSerializer(many=True))
    def get_rounds_detail(self, event):
        """Rounds + nested exercises, but only on retrieve (a single event):
        list payloads stay light, so we return null there. Ordered by round
        order using the prefetched cache."""
        view = self.context.get("view")
        if getattr(view, "action", None) != "retrieve":
            return None
        ordered = sorted(event.rounds.all(), key=lambda r: r.order or 0)
        return EventRoundDetailSerializer(ordered, many=True, context=self.context).data


class _DebriefAttendanceSerializer(serializers.Serializer):
    present = serializers.IntegerField()
    absent = serializers.IntegerField()
    total = serializers.IntegerField(help_text="Active athlete members of the team.")


class _DebriefRotiSerializer(serializers.Serializer):
    average = serializers.FloatField(allow_null=True)
    count = serializers.IntegerField()


class _DebriefRsvpSerializer(serializers.Serializer):
    going = serializers.IntegerField()
    maybe = serializers.IntegerField()
    not_going = serializers.IntegerField()
    no_response = serializers.IntegerField()


class EventDebriefSerializer(serializers.Serializer):
    """Consolidated post-session debrief for one event (managers only):
    attendance + ROTI + RSVP summaries, attachment count, and the coach's
    free-text debrief — all in a single read."""

    debrief = serializers.CharField(allow_blank=True)
    attendance = _DebriefAttendanceSerializer()
    roti = _DebriefRotiSerializer()
    rsvp = _DebriefRsvpSerializer()
    attachments_count = serializers.IntegerField()


class EventTemplateSerializer(serializers.ModelSerializer):
    """A reusable session template (read)."""

    rounds_count = serializers.SerializerMethodField()

    class Meta:
        from .models import EventTemplate

        model = EventTemplate
        fields = ["id", "name", "goal", "total", "sport", "team", "rounds_count", "created_at"]
        read_only_fields = fields

    @extend_schema_field(serializers.IntegerField())
    def get_rounds_count(self, obj) -> int:
        # Prefer the queryset annotation (one grouped COUNT for the whole list);
        # fall back to the property for a freshly-created single template.
        annotated = getattr(obj, "_rounds_count", None)
        return annotated if annotated is not None else obj.rounds.count()


class SaveAsTemplateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)


class InstantiateTemplateRequestSerializer(serializers.Serializer):
    refer_program = serializers.IntegerField(help_text="Target program id for the new event.")
    date = serializers.DateField()

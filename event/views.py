from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from round.models import Round
from team.queries import managed_teams, user_member_teams
from tools.exceptions import NotAManagerDenied, NotAuthorizedEventDenied
from tools.throttling import AITrainingGenerationThrottle
from tools.validators import validate_reorder_ids

from .ai import generate_training as ai_generate_training
from .models import Event
from .serializers import (
    EventSerializer,
    GenerateTrainingRequestSerializer,
    ReorderRoundsRequestSerializer,
)


class EventViewSet(viewsets.ModelViewSet):
    """CRUD complet pour Event, scopé par team du program."""

    serializer_class = EventSerializer
    filterset_fields = {
        "refer_program": ["exact"],
        "date": ["exact", "gte", "lte"],
        "color": ["exact"],
    }
    search_fields = ["name", "goal"]
    ordering_fields = ["date", "hour_start", "name", "id"]
    ordering = ["-date", "hour_start"]

    def get_queryset(self):
        return (
            Event.objects.filter(refer_program__team__in=user_member_teams(self.request.user))
            .select_related("refer_program", "refer_program__team", "refer_program__team__sport")
            .prefetch_related("rounds", "members")
        )

    def _check_program_write(self, program):
        if program is None:
            raise PermissionDenied(_("refer_program is required."))
        if not managed_teams(self.request.user).filter(pk=program.team_id).exists():
            raise PermissionDenied(_("You do not manage the team of this program."))

    def perform_create(self, serializer):
        program = serializer.validated_data.get("refer_program")
        self._check_program_write(program)
        # Inherit each per-aspect visibility default from the event's team
        # unless the create payload explicitly set it. We key off the raw
        # request body because the serializer fields carry model defaults,
        # so validated_data alone cannot distinguish "omitted" from
        # "explicitly equal to the default".
        team = program.team if program is not None else None
        overrides = {}
        if team is not None:
            for aspect in ("vis_distance", "vis_goal", "vis_rounds"):
                if aspect not in self.request.data:
                    overrides[aspect] = getattr(team, aspect)
        serializer.save(**overrides)

    def perform_update(self, serializer):
        self._check_program_write(
            serializer.validated_data.get("refer_program", serializer.instance.refer_program)
        )
        serializer.save()

    @extend_schema(
        request=GenerateTrainingRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="GenerateTrainingResponse",
                    fields={
                        "rounds_created": serializers.IntegerField(),
                        "exercises_created": serializers.IntegerField(),
                        "exercises_reused": serializers.IntegerField(),
                        "rationale": serializers.CharField(),
                        "model": serializers.CharField(),
                        "tokens_used": inline_serializer(
                            name="GenerateTrainingTokensUsed",
                            fields={
                                "input": serializers.IntegerField(),
                                "output": serializers.IntegerField(),
                            },
                        ),
                    },
                ),
                description="Training session generated successfully",
            ),
            400: OpenApiResponse(description="Invalid request body"),
            403: OpenApiResponse(description="Not a manager of this event team"),
            409: OpenApiResponse(description="Event already has rounds"),
            500: OpenApiResponse(description="AI configuration error"),
            502: OpenApiResponse(description="AI service error"),
        },
        description=(
            "Generate detailed Rounds and Exercises with AI for an Event. "
            "Optionally accepts an `additional_prompt` (max 2000 chars) "
            "appended to the LLM user prompt after the structured context."
        ),
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="generate-training",
        throttle_classes=[AITrainingGenerationThrottle],
    )
    def generate_training(self, request, pk=None):
        """POST /api/v1/events/{id}/generate-training/ — Claude-generated rounds."""
        from exercise.models import EnergySegment, Exercise, Modality
        from round.models import Round

        event = self.get_object()

        if not event.refer_program or not event.refer_program.team.is_managed_by(request.user):
            raise NotAManagerDenied(_("You must be owner or manager of this event's team."))

        if event.rounds.exists():
            return Response(
                {
                    "code": "event_has_rounds",
                    "detail": _("Event already has rounds. Remove them before regenerating."),
                },
                status=status.HTTP_409_CONFLICT,
            )

        body_serializer = GenerateTrainingRequestSerializer(data=request.data)
        body_serializer.is_valid(raise_exception=True)
        additional_prompt = body_serializer.validated_data.get("additional_prompt", "")

        ai_result = ai_generate_training(
            event=event,
            user=request.user if request.user.is_authenticated else None,
            additional_prompt=additional_prompt,
        )

        created_rounds = 0
        created_exercises = 0
        reused_exercises = 0

        team_sport = event.refer_program.team.sport
        team_language = event.refer_program.team.language
        with transaction.atomic():
            for r_idx, r_data in enumerate(ai_result["rounds"], start=1):
                round_obj = Round.objects.create(
                    sport=team_sport,
                    language=team_language,
                    count=r_data.get("count", 1),
                    t_start=r_data.get("t_start", "00:00"),
                    t_break=r_data.get("t_break", "00:00"),
                    order=r_idx,
                )
                created_rounds += 1

                for ex_idx, ex_data in enumerate(r_data.get("exercises", []), start=1):
                    modality = Modality.objects.get(pk=ex_data["modality_id"])
                    segment = EnergySegment.objects.get(pk=ex_data["energysegment_id"])

                    exercise, created = Exercise.objects.get_or_create(
                        modality=modality,
                        energysegment=segment,
                        distance=ex_data["distance"],
                        repetition=ex_data["repetition"],
                        t_start=ex_data.get("t_start", "00:00"),
                        t_break=ex_data.get("t_break", "00:00"),
                        notes=ex_data.get("notes", ""),
                        language=team_language,
                        defaults={"order": ex_idx},
                    )
                    if created:
                        created_exercises += 1
                    else:
                        reused_exercises += 1

                    round_obj.exercises.add(exercise)

                event.rounds.add(round_obj)

            event.generated_by_ai = True
            event.ai_prompt = ai_result["prompt_sent"]
            event.ai_response = ai_result["rationale"]
            event.ai_generated_at = timezone.now()
            event.save()

        return Response(
            {
                "rounds_created": created_rounds,
                "exercises_created": created_exercises,
                "exercises_reused": reused_exercises,
                "rationale": ai_result["rationale"],
                "model": ai_result["model"],
                "tokens_used": {
                    "input": ai_result["input_tokens"],
                    "output": ai_result["output_tokens"],
                },
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=ReorderRoundsRequestSerializer,
        responses={
            204: OpenApiResponse(description="Rounds reordered"),
            400: OpenApiResponse(
                description=(
                    "Validation error. `body.code` is one of: `empty_list`, "
                    "`duplicate_id`, `scope_mismatch`, `incomplete_reorder`."
                )
            ),
            403: OpenApiResponse(description="Not a manager of this event team"),
        },
        description=(
            "Atomically reorder the Rounds attached to this Event. "
            "`round_ids` must contain exactly the IDs of the Rounds currently "
            "attached, in the desired final order. Round.order is set to "
            "1..N matching list position, in a single transaction."
        ),
    )
    @action(detail=True, methods=["post"], url_path="rounds/reorder")
    def rounds_reorder(self, request, pk=None):
        event = self.get_object()
        if not event.refer_program or not event.refer_program.team.is_managed_by(request.user):
            raise NotAuthorizedEventDenied(
                _("You must manage this event's team to reorder its rounds.")
            )

        body_serializer = ReorderRoundsRequestSerializer(data=request.data)
        body_serializer.is_valid(raise_exception=True)
        round_ids = body_serializer.validated_data["round_ids"]

        validate_reorder_ids(
            round_ids,
            event.rounds.values_list("id", flat=True),
            field_name="round_ids",
            container_label="event",
        )

        with transaction.atomic():
            for index, round_id in enumerate(round_ids, start=1):
                Round.objects.filter(pk=round_id).update(order=index)

        return Response(status=status.HTTP_204_NO_CONTENT)

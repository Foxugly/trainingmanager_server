from django.db import transaction
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from exercise.models import Exercise
from exercise.serializers import ExerciseSerializer
from team.permissions import IsTrainer
from team.queries import user_member_teams
from team.utils import scope_by_sport_language
from tools.exceptions import NotAuthorizedRoundDenied
from tools.validators import validate_reorder_ids

from .models import Round
from .serializers import ReorderExercisesRequestSerializer, RoundSerializer


class RoundViewSet(viewsets.ModelViewSet):
    """CRUD complet pour Round."""

    serializer_class = RoundSerializer
    permission_classes = [IsAuthenticated, IsTrainer]
    filterset_fields = ["sport", "language"]
    search_fields = []
    ordering_fields = ["order", "id"]
    ordering = ["order"]

    def get_queryset(self):
        qs = (
            Round.objects.select_related("sport")
            .prefetch_related(
                "exercises__modality__sport",
                "exercises__energysegment__energysystem",
            )
            # Annotate the event-usage so the serializer avoids one COUNT per row.
            .annotate(_usage_count=Count("event", distinct=True))
        )
        qs = scope_by_sport_language(qs, self.request.user, sport_field="sport_id")
        return _gate_rounds_by_event_visibility(qs, self.request.user)

    def _require_may_mutate(self, round_obj):
        # A round linked to an event must belong to a team the caller manages;
        # library rounds (no events) fall back to the class IsTrainer check.
        if not _user_may_mutate_round(round_obj, self.request.user):
            raise NotAuthorizedRoundDenied(
                _("You must manage a team owning an event linked to this round.")
            )

    def perform_update(self, serializer):
        self._require_may_mutate(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._require_may_mutate(instance)
        instance.delete()

    @extend_schema(
        request=None,
        responses={201: RoundSerializer},
        description="Clone this Round (scalar fields + M2M exercises). Returns the new Round.",
    )
    @action(detail=True, methods=["post"])
    def clone(self, request, pk=None):
        """Standalone clone : new Round with the same scalar fields and the
        same exercise list (M2M copied)."""
        original = self.get_object()
        clone = Round.objects.create(
            sport=original.sport,
            language=original.language,
            count=original.count,
            t_start=original.t_start,
            t_break=original.t_break,
            order=original.order,
        )
        clone.exercises.set(original.exercises.all())
        serializer = self.get_serializer(clone)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=inline_serializer(
            name="CloneExerciseRequest",
            fields={"exercise_id": serializers.IntegerField()},
        ),
        responses={
            201: ExerciseSerializer,
            400: None,
            404: None,
        },
        description="Clone an Exercise and attach the copy to this Round.",
    )
    @action(detail=True, methods=["post"], url_path="clone-exercise")
    def clone_exercise(self, request, pk=None):
        """Clone an Exercise and attach it to this Round.
        Body: {"exercise_id": <id>}."""
        round_obj = self.get_object()
        exercise_id = request.data.get("exercise_id")
        if not exercise_id:
            return Response(
                {"code": "exercise_id_required", "detail": _("exercise_id is required.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        scoped_qs = Exercise.objects.filter(
            modality__sport_id=round_obj.sport_id,
            language=round_obj.language,
        )
        try:
            original = scoped_qs.get(pk=exercise_id)
        except Exercise.DoesNotExist:
            return Response(
                {
                    "code": "exercise_not_found",
                    "detail": _("Exercise not found or not accessible."),
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        cloned_exercise = Exercise.objects.create(
            t_start=original.t_start,
            t_break=original.t_break,
            repetition=original.repetition,
            distance=original.distance,
            notes=original.notes,
            modality=original.modality,
            energysegment=original.energysegment,
            language=round_obj.language,
            order=original.order,
        )
        round_obj.exercises.add(cloned_exercise)
        serializer = ExerciseSerializer(cloned_exercise, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=ReorderExercisesRequestSerializer,
        responses={
            204: OpenApiResponse(description="Exercises reordered"),
            400: OpenApiResponse(
                description=(
                    "Validation error. `body.code` is one of: `empty_list`, "
                    "`duplicate_id`, `scope_mismatch`, `incomplete_reorder`."
                )
            ),
            403: OpenApiResponse(description="Not authorized to mutate this round"),
        },
        description=(
            "Atomically reorder the Exercises attached to this Round. "
            "`exercise_ids` must contain exactly the IDs of the Exercises "
            "currently attached, in the desired final order. Exercise.order "
            "is set to 1..N matching list position, in a single transaction."
        ),
    )
    @action(detail=True, methods=["post"], url_path="exercises/reorder")
    def exercises_reorder(self, request, pk=None):
        round_obj = self.get_object()
        if not _user_may_mutate_round(round_obj, request.user):
            raise NotAuthorizedRoundDenied(
                _(
                    "You must manage at least one team owning an event "
                    "linked to this round to reorder its exercises."
                )
            )

        body_serializer = ReorderExercisesRequestSerializer(data=request.data)
        body_serializer.is_valid(raise_exception=True)
        exercise_ids = body_serializer.validated_data["exercise_ids"]

        validate_reorder_ids(
            exercise_ids,
            round_obj.exercises.values_list("id", flat=True),
            field_name="exercise_ids",
            container_label="round",
        )

        with transaction.atomic():
            for index, exercise_id in enumerate(exercise_ids, start=1):
                Exercise.objects.filter(pk=exercise_id).update(order=index)

        return Response(status=status.HTTP_204_NO_CONTENT)


def _gate_rounds_by_event_visibility(qs, user):
    """Hide rounds an athlete may not see because of per-event vis_rounds.

    Security gate for the athlete session-detail flow: rounds are reached via
    the RoundViewSet (the EventSerializer exposes round PKs, the client then
    fetches them here). A round attached to an event whose ``vis_rounds`` is
    not currently visible to a NON-manager athlete of that event's team must
    not be returned.

    A round can be linked to several events. We exclude a round only when it
    is linked to at least one event the athlete should NOT see rounds for AND
    it is NOT also linked to some event whose rounds the athlete (or a team
    they manage) MAY see. Library rounds (no events) and rounds the user
    manages via any linking team are unaffected.
    """
    if not getattr(user, "is_authenticated", False):
        return qs

    from event.models import Event

    # Events linking the candidate rounds that belong to a team the user is
    # connected to (member/manager/owner). select_related the team so the
    # visibility/role resolution does not issue per-row queries.
    candidate_round_ids = set(qs.values_list("id", flat=True))
    if not candidate_round_ids:
        return qs

    events = (
        Event.objects.filter(rounds__in=candidate_round_ids)
        .filter(refer_program__team__in=user_member_teams(user))
        .select_related("refer_program__team")
        .prefetch_related("rounds")
        .distinct()
    )

    # rounds the athlete is allowed to see via at least one event, and rounds
    # explicitly hidden via at least one event. "Allowed" wins over "hidden".
    allowed = set()
    hidden = set()
    for event in events:
        team = event.team
        is_manager = team is not None and team.is_managed_by(user)
        visible = is_manager or event.aspect_visible_to_athlete("rounds")
        linked_ids = [r.id for r in event.rounds.all() if r.id in candidate_round_ids]
        if visible:
            allowed.update(linked_ids)
        else:
            hidden.update(linked_ids)

    to_exclude = hidden - allowed
    if to_exclude:
        qs = qs.exclude(id__in=to_exclude)
    return qs


def _user_may_mutate_round(round_obj, user):
    """A user may reorder a round's exercises if they manage at least one
    team owning an event that contains this round. Library rounds (no
    events) fall back to the IsTrainer class permission check (already
    enforced by RoundViewSet.permission_classes)."""
    linked_events = list(round_obj.event_set.select_related("refer_program__team").all())
    if not linked_events:
        return True  # library round; class-level IsTrainer already passed
    return any(
        e.refer_program is not None and e.refer_program.team.is_managed_by(user)
        for e in linked_events
    )

from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from team.permissions import IsTrainer
from team.utils import scope_by_sport_language
from tools.exceptions import NotAuthorizedRoundDenied
from tools.mixins import SoftDeleteIncludeInactiveModelViewSet
from tools.openapi import INCLUDE_INACTIVE_PARAM
from tools.permissions import AdminWriteAuthRead

from .models import EnergySegment, EnergySystem, Exercise, Modality
from .serializers import (
    EnergySegmentAdminSerializer,
    EnergySegmentSerializer,
    EnergySystemAdminSerializer,
    EnergySystemSerializer,
    ExerciseSerializer,
    ModalityAdminSerializer,
    ModalitySerializer,
)


@extend_schema_view(
    list=extend_schema(
        summary="List modalities (public flavor)",
        responses=ModalitySerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    retrieve=extend_schema(
        summary="Retrieve modality (admin flavor for staff)",
        responses=ModalityAdminSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    create=extend_schema(
        summary="Create modality (staff only)",
        request=ModalityAdminSerializer,
        responses=ModalityAdminSerializer,
    ),
    update=extend_schema(
        request=ModalityAdminSerializer,
        responses=ModalityAdminSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    partial_update=extend_schema(
        request=ModalityAdminSerializer,
        responses=ModalityAdminSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    destroy=extend_schema(summary="Soft delete modality (staff only)"),
)
class ModalityViewSet(SoftDeleteIncludeInactiveModelViewSet):
    """CRUD on Modality referential, scoped by sport when nested."""

    permission_classes = [AdminWriteAuthRead]
    filterset_fields = ["is_active", "sport", "name"]
    search_fields = ["name"]
    ordering_fields = ["name", "id"]
    ordering = ["name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Modality.objects.none()
        qs = Modality.objects.all()
        sport_pk = self.kwargs.get("sport_pk")
        if sport_pk:
            qs = qs.filter(sport_id=sport_pk)
        return self._apply_include_inactive_filter(qs)

    def get_serializer_class(self):
        if (
            self.request.user.is_authenticated
            and self.request.user.is_staff
            and self.action in ("create", "update", "partial_update", "retrieve")
        ):
            return ModalityAdminSerializer
        return ModalitySerializer


@extend_schema_view(
    list=extend_schema(
        summary="List energy systems (public flavor)",
        responses=EnergySystemSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    retrieve=extend_schema(
        summary="Retrieve energy system (admin flavor for staff)",
        responses=EnergySystemAdminSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    create=extend_schema(
        summary="Create energy system (staff only)",
        request=EnergySystemAdminSerializer,
        responses=EnergySystemAdminSerializer,
    ),
    update=extend_schema(
        request=EnergySystemAdminSerializer,
        responses=EnergySystemAdminSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    partial_update=extend_schema(
        request=EnergySystemAdminSerializer,
        responses=EnergySystemAdminSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    destroy=extend_schema(summary="Soft delete energy system (staff only)"),
)
class EnergySystemViewSet(SoftDeleteIncludeInactiveModelViewSet):
    """CRUD on EnergySystem referential."""

    permission_classes = [AdminWriteAuthRead]
    filterset_fields = ["is_active", "name"]
    search_fields = ["name"]
    ordering_fields = ["name", "id"]
    ordering = ["name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return EnergySystem.objects.none()
        return self._apply_include_inactive_filter(EnergySystem.objects.all())

    def get_serializer_class(self):
        if (
            self.request.user.is_authenticated
            and self.request.user.is_staff
            and self.action in ("create", "update", "partial_update", "retrieve")
        ):
            return EnergySystemAdminSerializer
        return EnergySystemSerializer


@extend_schema_view(
    list=extend_schema(
        summary="List energy segments (public flavor)",
        responses=EnergySegmentSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    retrieve=extend_schema(
        summary="Retrieve energy segment (admin flavor for staff)",
        responses=EnergySegmentAdminSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    create=extend_schema(
        summary="Create energy segment (staff only)",
        request=EnergySegmentAdminSerializer,
        responses=EnergySegmentAdminSerializer,
    ),
    update=extend_schema(
        request=EnergySegmentAdminSerializer,
        responses=EnergySegmentAdminSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    partial_update=extend_schema(
        request=EnergySegmentAdminSerializer,
        responses=EnergySegmentAdminSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    destroy=extend_schema(summary="Soft delete energy segment (staff only)"),
)
class EnergySegmentViewSet(SoftDeleteIncludeInactiveModelViewSet):
    """CRUD on EnergySegment referential."""

    permission_classes = [AdminWriteAuthRead]
    filterset_fields = ["is_active", "energysystem"]
    search_fields = ["abv", "description"]
    ordering_fields = ["abv", "id"]
    ordering = ["abv"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return EnergySegment.objects.none()
        return self._apply_include_inactive_filter(
            EnergySegment.objects.select_related("energysystem")
        )

    def get_serializer_class(self):
        if (
            self.request.user.is_authenticated
            and self.request.user.is_staff
            and self.action in ("create", "update", "partial_update", "retrieve")
        ):
            return EnergySegmentAdminSerializer
        return EnergySegmentSerializer


class ExerciseViewSet(viewsets.ModelViewSet):
    """CRUD complet pour Exercise."""

    serializer_class = ExerciseSerializer
    permission_classes = [IsAuthenticated, IsTrainer]
    filterset_fields = ["modality", "energysegment", "language"]
    search_fields = ["notes"]
    ordering_fields = ["order", "id", "distance"]
    ordering = ["order"]

    def get_queryset(self):
        qs = Exercise.objects.select_related(
            "modality__sport",
            "energysegment__energysystem",
            # Annotate the round-usage so the serializer avoids one COUNT per row.
        ).annotate(_usage_count=Count("round", distinct=True))
        return scope_by_sport_language(qs, self.request.user, sport_field="modality__sport_id")

    def _require_may_mutate(self, exercise):
        # An exercise attached to a round linked to an event must belong to a team
        # the caller manages; library exercises (no events) fall back to the
        # author check. IsTrainer alone is NOT enough (it only proves the caller
        # manages *some* team), which would let any trainer mutate another team's
        # session exercise — mirrors RoundViewSet._require_may_mutate.
        if not _user_may_mutate_exercise(exercise, self.request.user):
            raise NotAuthorizedRoundDenied(
                _("You must manage a team owning an event linked to this exercise.")
            )

    def perform_create(self, serializer):
        # Stamp authorship so a library exercise (attached to no event) can later
        # be restricted to its author (see _user_may_mutate_exercise).
        serializer.save(author=self.request.user)

    def perform_update(self, serializer):
        self._require_may_mutate(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._require_may_mutate(instance)
        instance.delete()

    @extend_schema(
        request=None,
        responses={201: ExerciseSerializer},
        description="Clone this Exercise. Returns the new Exercise.",
    )
    @action(detail=True, methods=["post"])
    def clone(self, request, pk=None):
        """Standalone clone : new Exercise with the same scalar fields."""
        original = self.get_object()
        # Cloning reads the source exercise's content. A library exercise is a
        # shared catalog (author-gated); an event-linked exercise belongs to a
        # team, so only a manager of one of those teams may clone it — otherwise a
        # trainer who merely shares the sport/language could read another team's
        # session content.
        self._require_may_mutate(original)
        clone = Exercise.objects.create(
            t_start=original.t_start,
            t_break=original.t_break,
            repetition=original.repetition,
            distance=original.distance,
            notes=original.notes,
            modality=original.modality,
            energysegment=original.energysegment,
            language=original.language,
            order=original.order,
            author=request.user,
        )
        serializer = self.get_serializer(clone)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


def _user_may_mutate_exercise(exercise, user):
    """A user may mutate an exercise (PATCH/DELETE/clone) if they manage at least
    one team owning an event that (via a round) contains this exercise.

    A *library* exercise (attached to no event) belongs to no team. When it
    carries an author (stamped on create/clone) it is restricted to that author
    or staff — otherwise any same-(sport, language) trainer could edit another
    trainer's catalog entry. Unattributed library exercises (author NULL — legacy
    rows, seeded or AI-deduped data) stay open to any trainer for back-compat.

    Mirrors round.views._user_may_mutate_round.
    """
    from event.models import Event

    linked_events = list(
        Event.objects.filter(rounds__exercises=exercise)
        .select_related("refer_program__team")
        .distinct()
    )
    if not linked_events:
        if exercise.author_id is None:
            return True
        return exercise.author_id == user.pk or bool(user.is_staff)
    return any(
        e.refer_program is not None and e.refer_program.team.is_managed_by(user)
        for e in linked_events
    )

from django.db.models import Count
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from team.permissions import IsTrainer
from team.utils import scope_by_sport_language
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

    @extend_schema(
        request=None,
        responses={201: ExerciseSerializer},
        description="Clone this Exercise. Returns the new Exercise.",
    )
    @action(detail=True, methods=["post"])
    def clone(self, request, pk=None):
        """Standalone clone : new Exercise with the same scalar fields."""
        original = self.get_object()
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
        )
        serializer = self.get_serializer(clone)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

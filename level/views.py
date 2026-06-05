from drf_spectacular.utils import extend_schema, extend_schema_view

from tools.mixins import SoftDeleteIncludeInactiveModelViewSet
from tools.openapi import INCLUDE_INACTIVE_PARAM
from tools.permissions import AdminWriteAuthRead

from .models import Level
from .serializers import LevelAdminSerializer, LevelSerializer


@extend_schema_view(
    list=extend_schema(
        summary="List levels (public flavor)",
        description=(
            "Returns the public Level serializer with localized 'name' and "
            "'description'. Available to all authenticated users. Pass "
            "?include_inactive=true (staff only) to include soft-deleted rows."
        ),
        responses=LevelSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    retrieve=extend_schema(
        summary="Retrieve level (admin flavor for staff)",
        description=(
            "Returns the admin flavor with name_*/description_* per-language "
            "variants when called by a staff user, otherwise the public flavor."
        ),
        responses=LevelAdminSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    create=extend_schema(
        summary="Create level (staff only)",
        description="Accepts the admin flavor with name_*/description_* variants.",
        request=LevelAdminSerializer,
        responses=LevelAdminSerializer,
    ),
    update=extend_schema(
        request=LevelAdminSerializer,
        responses=LevelAdminSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    partial_update=extend_schema(
        request=LevelAdminSerializer,
        responses=LevelAdminSerializer,
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    destroy=extend_schema(
        summary="Soft delete level (staff only)",
        description="Sets is_active=False; does not hard delete.",
    ),
)
class LevelViewSet(SoftDeleteIncludeInactiveModelViewSet):
    """CRUD on the Level referential.

    Read: any authenticated user (default queryset filters is_active=True).
    Write: staff only. Soft delete bumps updated_at.
    """

    permission_classes = [AdminWriteAuthRead]
    filterset_fields = ["is_active"]
    search_fields = ["code", "name"]
    ordering_fields = ["order", "code"]
    ordering = ["order", "code"]
    soft_delete_fields = ("is_active", "updated_at")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Level.objects.none()
        return self._apply_include_inactive_filter(Level.objects.all())

    def get_serializer_class(self):
        user = self.request.user
        if (
            user.is_authenticated
            and user.is_staff
            and self.action in ("create", "update", "partial_update", "retrieve")
        ):
            return LevelAdminSerializer
        return LevelSerializer

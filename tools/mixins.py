"""Reusable DRF viewset mixins for the soft-delete + include_inactive
convention shared across the project.

Six viewsets follow this exact pattern:
  - Sport, Modality, EnergySystem, EnergySegment (referentials, staff-only)
  - AttendanceStatus (referential, staff-only)
  - Note (team-scoped, manager-only)

ProgramViewSet has a custom variant (staff OR managed_teams + a
permission check before destroy) and is not migrated to these mixins.
"""

from rest_framework import viewsets


class SoftDeleteMixin:
    """Override perform_destroy to flip is_active=False instead of hard-deleting.

    Override `soft_delete_fields` if the model also has an auto_now
    `updated_at` you want to bump in the same UPDATE.
    """

    soft_delete_fields: tuple = ("is_active",)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=list(self.soft_delete_fields))


class IncludeInactiveMixin:
    """Add `?include_inactive=true` support to a viewset's get_queryset.

    Default policy: only staff users can see soft-deleted rows. Override
    `_include_inactive_allowed(request)` for a different policy (e.g.
    Note grants this to team managers, not staff). Use
    `_apply_include_inactive_filter(qs)` from get_queryset:

        qs = Model.objects.all()
        return self._apply_include_inactive_filter(qs)
    """

    def _include_inactive_allowed(self, request):
        return (
            request.query_params.get("include_inactive") == "true"
            and request.user.is_authenticated
            and request.user.is_staff
        )

    def _apply_include_inactive_filter(self, qs, is_active_field="is_active"):
        if self._include_inactive_allowed(self.request):
            return qs
        return qs.filter(**{is_active_field: True})


class SoftDeleteIncludeInactiveModelViewSet(
    SoftDeleteMixin, IncludeInactiveMixin, viewsets.ModelViewSet
):
    """Convenience composition for the common case (both mixins + ModelViewSet).

    Used by referentials. ProgramViewSet keeps its custom destroy logic
    and is NOT a candidate for this base."""

    pass


class AdminFlavorSerializerMixin:
    """`get_serializer_class` that returns the admin-flavored serializer to staff
    on write/retrieve actions, and the public flavor otherwise.

    Set ``public_serializer_class`` + ``admin_serializer_class`` on the viewset.
    The six staff-managed referential viewsets (Sport, Level, Modality,
    EnergySystem, EnergySegment, AttendanceStatus) all shared this exact switch
    verbatim.
    """

    public_serializer_class = None
    admin_serializer_class = None
    admin_flavor_actions = ("create", "update", "partial_update", "retrieve")

    def get_serializer_class(self):
        user = self.request.user
        if (
            user.is_authenticated
            and user.is_staff
            and self.action in self.admin_flavor_actions
        ):
            return self.admin_serializer_class
        return self.public_serializer_class


class AdminFlavoredReferentialViewSet(
    SoftDeleteMixin, IncludeInactiveMixin, AdminFlavorSerializerMixin, viewsets.ModelViewSet
):
    """Soft-delete + include_inactive + staff/public serializer flavor — the
    shape every staff-managed referential CRUD viewset follows."""

    pass

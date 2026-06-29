from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Notification, NotificationPreference
from .serializers import (
    NotificationPreferenceSerializer,
    NotificationPreferenceUpdateSerializer,
    NotificationSerializer,
    ReadAllResponseSerializer,
    UnreadCountSerializer,
)
from .services import get_effective_preferences


@extend_schema_view(
    list=extend_schema(
        summary="List the caller's notifications (newest first)",
        parameters=[
            OpenApiParameter(
                name="is_read",
                type=bool,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by read state (e.g. ?is_read=false for unread only).",
            ),
        ],
    ),
)
class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """The caller's own notifications + read-state mutations + channel prefs.

    A user only ever sees/affects their OWN notifications: the queryset is
    filtered by ``recipient=request.user`` and every action operates on that
    scope (so marking someone else's notification read yields 404).
    """

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["is_read"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()
        return Notification.objects.filter(recipient=self.request.user)

    @extend_schema(
        summary="Count the caller's unread notifications",
        responses=UnreadCountSerializer,
    )
    @action(detail=False, methods=["get"], url_path="unread_count")
    def unread_count(self, request):
        count = self.get_queryset().filter(is_read=False).count()
        return Response(UnreadCountSerializer({"count": count}).data)

    @extend_schema(
        summary="Mark one notification as read",
        request=None,
        responses=NotificationSerializer,
    )
    @action(detail=True, methods=["post"], url_path="read")
    def read(self, request, pk=None):
        notification = get_object_or_404(self.get_queryset(), pk=pk)
        if not notification.is_read:
            notification.is_read = True
            notification.save(update_fields=["is_read"])
        return Response(NotificationSerializer(notification).data)

    @extend_schema(
        summary="Mark all the caller's notifications as read",
        request=None,
        responses=ReadAllResponseSerializer,
    )
    @action(detail=False, methods=["post"], url_path="read_all")
    def read_all(self, request):
        updated = self.get_queryset().filter(is_read=False).update(is_read=True)
        return Response(ReadAllResponseSerializer({"updated": updated}).data)

    @extend_schema(
        methods=["get"],
        operation_id="notifications_preferences_retrieve",
        summary="Get the caller's effective notification preferences",
        description=(
            "Returns every NotificationType with the caller's effective "
            "channel settings {type, label, in_app, email}, defaulting to "
            "in_app=True / email=True where no stored row exists."
        ),
        filters=False,
        responses=NotificationPreferenceSerializer(many=True),
    )
    @extend_schema(
        methods=["put"],
        operation_id="notifications_preferences_update",
        summary="Upsert the caller's notification preferences",
        description=(
            "Accepts a list of {type, in_app, email} entries and upserts the "
            "caller's rows. Returns the full updated effective list."
        ),
        filters=False,
        request=NotificationPreferenceUpdateSerializer,
        responses=NotificationPreferenceSerializer(many=True),
    )
    @action(
        detail=False,
        methods=["get", "put"],
        url_path="preferences",
        pagination_class=None,
        filter_backends=[],
    )
    def preferences(self, request):
        if request.method == "PUT":
            serializer = NotificationPreferenceUpdateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            for entry in serializer.validated_data["preferences"]:
                NotificationPreference.objects.update_or_create(
                    user=request.user,
                    type=entry["type"],
                    defaults={
                        "in_app": entry["in_app"],
                        "email": entry["email"],
                        "push": entry["push"],
                    },
                )
        effective = get_effective_preferences(request.user)
        return Response(
            NotificationPreferenceSerializer(effective, many=True).data,
            status=status.HTTP_200_OK,
        )

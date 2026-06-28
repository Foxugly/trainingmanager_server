from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Device, DeviceTokenStatus
from .serializers import (
    DeviceReadSerializer,
    DeviceRegisterSerializer,
    DeviceUnregisterSerializer,
)


class DeviceRegisterView(APIView):
    """Upsert the caller's device by FCM token (idempotent; call on launch + token rotation)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=DeviceRegisterSerializer, responses=DeviceReadSerializer)
    def post(self, request):
        serializer = DeviceRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        device, created = Device.objects.update_or_create(
            push_token=data["push_token"],
            defaults={
                "user": request.user,
                "platform": data["platform"],
                "device_name": data.get("device_name", ""),
                "status": DeviceTokenStatus.ACTIVE,
                "last_seen_at": timezone.now(),
                "failure_count": 0,
            },
        )
        return Response(
            DeviceReadSerializer(device).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class DeviceUnregisterView(APIView):
    """Revoke the caller's device with the given token (call on logout)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=DeviceUnregisterSerializer, responses={204: None})
    def post(self, request):
        serializer = DeviceUnregisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        Device.objects.filter(
            user=request.user, push_token=serializer.validated_data["push_token"]
        ).update(status=DeviceTokenStatus.REVOKED)
        return Response(status=status.HTTP_204_NO_CONTENT)


class DeviceListView(APIView):
    """List the caller's active devices."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=DeviceReadSerializer(many=True))
    def get(self, request):
        qs = Device.objects.filter(user=request.user, status=DeviceTokenStatus.ACTIVE)
        return Response(DeviceReadSerializer(qs, many=True).data)

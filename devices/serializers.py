from rest_framework import serializers

from .models import Device, DevicePlatform


class DeviceRegisterSerializer(serializers.Serializer):
    push_token = serializers.CharField(min_length=20, max_length=512)
    platform = serializers.ChoiceField(choices=DevicePlatform.choices)
    device_name = serializers.CharField(
        max_length=120, required=False, allow_blank=True, default=""
    )


class DeviceUnregisterSerializer(serializers.Serializer):
    push_token = serializers.CharField(min_length=20, max_length=512)


class DeviceReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = ["id", "platform", "status", "device_name", "last_seen_at", "created_at"]
        read_only_fields = fields

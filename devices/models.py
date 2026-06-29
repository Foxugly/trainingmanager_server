from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class DevicePlatform(models.TextChoices):
    ANDROID = "android", _("Android")
    IOS = "ios", _("iOS")


class DeviceTokenStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    INVALID = "invalid", _("Invalid")
    REVOKED = "revoked", _("Revoked")


class Device(models.Model):
    """An FCM-capable mobile device belonging to one authenticated user.

    Identity is the FCM ``push_token`` (unique). Registration upserts by token
    and (re)binds it to the current user, so a re-used device row follows its
    latest owner. ``status`` tracks token health: a send that hits an
    Unregistered/invalid token flips the row to ``invalid`` and future sends
    skip it; logout sets ``revoked``.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="devices",
    )
    push_token = models.CharField(max_length=512, unique=True)
    platform = models.CharField(max_length=20, choices=DevicePlatform.choices)
    status = models.CharField(
        max_length=20,
        choices=DeviceTokenStatus.choices,
        default=DeviceTokenStatus.ACTIVE,
    )
    device_name = models.CharField(max_length=120, blank=True, default="")
    last_seen_at = models.DateTimeField(null=True, blank=True)
    failure_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self):
        return f"{self.platform} device of {self.user} ({self.status})"

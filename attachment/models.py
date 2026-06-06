from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class Attachment(models.Model):
    """A file uploaded to private S3 and attached to any model via contenttypes.

    The file itself never transits the backend: the browser PUTs it to S3 with
    a presigned URL, then calls ``complete`` so we record the real size and flip
    ``status`` to ``ready``. Downloads are short-lived presigned GETs minted on
    demand and gated by the same app permissions used at presign time.

    The generic target (``content_type`` + ``object_id``) lets an Attachment
    point at an Event OR a messaging Message today, and any future model later,
    without a schema change.
    """

    PENDING = "pending"
    READY = "ready"
    STATUS_CHOICES = [
        (PENDING, "pending"),
        (READY, "ready"),
    ]

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    s3_key = models.CharField(max_length=512, unique=True)
    filename = models.CharField(max_length=255)
    content_type_mime = models.CharField(max_length=150)
    size_bytes = models.BigIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_attachments",
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return self.filename

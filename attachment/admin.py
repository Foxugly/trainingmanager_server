from django.contrib import admin

from .models import Attachment


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ("id", "filename", "content_type", "object_id", "status", "size_bytes", "created_at")
    list_filter = ("status", "content_type")
    search_fields = ("filename", "s3_key")
    readonly_fields = ("created_at",)

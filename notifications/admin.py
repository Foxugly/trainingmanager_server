from django.contrib import admin

from .models import Notification, NotificationPreference


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["id", "recipient", "type", "title", "is_read", "created_at"]
    list_filter = ["type", "is_read", "created_at"]
    search_fields = ["title", "body", "recipient__username", "recipient__email"]
    raw_id_fields = ["recipient"]
    readonly_fields = ["created_at"]


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "type", "in_app", "email"]
    list_filter = ["type", "in_app", "email"]
    search_fields = ["user__username", "user__email"]
    raw_id_fields = ["user"]

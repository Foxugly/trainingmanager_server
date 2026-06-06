from django.contrib import admin

from .models import AuditLogEntry


@admin.register(AuditLogEntry)
class AuditLogEntryAdmin(admin.ModelAdmin):
    list_display = ["created_at", "action", "actor_label", "team", "target_repr"]
    list_filter = ["action", "team", "created_at"]
    search_fields = ["actor_label", "target_repr"]
    readonly_fields = [
        "actor",
        "actor_label",
        "action",
        "team",
        "target_repr",
        "metadata",
        "created_at",
    ]
    raw_id_fields = ["actor", "team"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

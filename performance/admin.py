from django.contrib import admin

from .models import Performance


@admin.register(Performance)
class PerformanceAdmin(admin.ModelAdmin):
    list_display = [
        "member",
        "team",
        "label",
        "value",
        "unit",
        "recorded_on",
        "created_by",
    ]
    list_filter = ["unit", "recorded_on", "team"]
    search_fields = ["label", "member__firstname", "member__lastname", "team__name"]
    readonly_fields = ["created_at", "updated_at"]
    raw_id_fields = ["team", "member", "created_by"]
    date_hierarchy = "recorded_on"
